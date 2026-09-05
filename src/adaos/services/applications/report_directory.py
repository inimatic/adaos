from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from adaos.domain.artifact_release import canonical_json_bytes
from adaos.services.applications.report_keys import SubnetPurposeKey
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


class SubnetDirectoryError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _key_id(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


class SubnetKeyDirectoryAuthority:
    """Signed Root projection from existing subnet identities to zones and keys."""

    def __init__(self, state_dir: Path, *, zone_id: str, now: Callable[[], datetime] = _now) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.zone_id = str(zone_id or "").strip().lower()
        if not self.zone_id:
            raise SubnetDirectoryError("zone_id is required")
        self.now = now

    @property
    def root(self) -> Path:
        path = self.state_dir / "root" / "subnet_key_directory"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def _read(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            private = Ed25519PrivateKey.generate()
            raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
            return {"schema": "adaos.subnet.key_directory_authority.v1", "generation": 0, "entries": {}, "private_key_b64": base64.b64encode(raw).decode("ascii")}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SubnetDirectoryError("subnet key directory is unreadable") from exc
        if not isinstance(state, dict) or state.get("schema") != "adaos.subnet.key_directory_authority.v1" or not isinstance(state.get("entries"), dict):
            raise SubnetDirectoryError("subnet key directory is invalid")
        return state

    @staticmethod
    def _private(state: Mapping[str, Any]) -> Ed25519PrivateKey:
        try:
            raw = base64.b64decode(str(state["private_key_b64"]), validate=True)
            return Ed25519PrivateKey.from_private_bytes(raw)
        except Exception as exc:
            raise SubnetDirectoryError("directory signing key is invalid") from exc

    def publish_subnet(
        self,
        subnet_ref: str,
        *,
        home_zone: str,
        keys: Sequence[SubnetPurposeKey],
        display_name: str | None = None,
    ) -> dict[str, Any]:
        subnet = str(subnet_ref or "").strip().lower()
        zone = str(home_zone or "").strip().lower()
        if not subnet.startswith("subnet:") or not zone:
            raise SubnetDirectoryError("subnet_ref and home_zone are required")
        public_keys = [item.to_dict() for item in keys]
        if any(item.subnet_ref != subnet for item in keys):
            raise SubnetDirectoryError("directory key belongs to another subnet")
        public_keys.sort(key=lambda item: (item["purpose"], item["key_id"]))
        entry = {
            "subnet_ref": subnet,
            "home_zone": zone,
            "display_name": str(display_name or "").strip() or None,
            "keys": public_keys,
        }
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            if state["entries"].get(subnet) != entry:
                state["entries"][subnet] = entry
                state["generation"] = int(state.get("generation") or 0) + 1
            atomic_write_json(self.state_path, state)
        return self.projection()

    def projection(self) -> dict[str, Any]:
        state = self._read()
        private = self._private(state)
        public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        signed = {
            "schema": "adaos.subnet.key_directory.v1",
            "authority_zone": self.zone_id,
            "generation": max(1, int(state.get("generation") or 0)),
            "generated_at": _iso(self.now()),
            "root_key_id": _key_id(public),
            "root_public_key_b64": base64.b64encode(public).decode("ascii"),
            "entries": [state["entries"][key] for key in sorted(state["entries"])],
        }
        signature = private.sign(canonical_json_bytes(signed))
        return {**signed, "signature_b64": base64.b64encode(signature).decode("ascii")}

    def sign_forward_receipt(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        state = self._read()
        private = self._private(state)
        body = {**dict(payload), "root_zone": self.zone_id}
        return {**body, "signature_b64": base64.b64encode(private.sign(canonical_json_bytes(body))).decode("ascii")}


class SubnetKeyDirectoryClient:
    def __init__(self, *, pinned_root_key_id: str | None = None) -> None:
        self.pinned_root_key_id = str(pinned_root_key_id or "").strip() or None
        self.high_water_generation = 0
        self._projection: dict[str, Any] | None = None

    def update(self, projection: Mapping[str, Any]) -> None:
        payload = dict(projection)
        signature_b64 = payload.pop("signature_b64", None)
        if payload.get("schema") != "adaos.subnet.key_directory.v1":
            raise SubnetDirectoryError("unsupported signed subnet directory")
        try:
            public_raw = base64.b64decode(str(payload["root_public_key_b64"]), validate=True)
            signature = base64.b64decode(str(signature_b64), validate=True)
            public = Ed25519PublicKey.from_public_bytes(public_raw)
            public.verify(signature, canonical_json_bytes(payload))
        except (KeyError, ValueError, InvalidSignature) as exc:
            raise SubnetDirectoryError("subnet directory signature is invalid") from exc
        observed_key_id = _key_id(public_raw)
        if payload.get("root_key_id") != observed_key_id:
            raise SubnetDirectoryError("subnet directory root key identity mismatch")
        if self.pinned_root_key_id is None:
            self.pinned_root_key_id = observed_key_id
        elif self.pinned_root_key_id != observed_key_id:
            raise SubnetDirectoryError("subnet directory root key changed without recovery")
        generation = int(payload.get("generation") or 0)
        if generation < self.high_water_generation:
            raise SubnetDirectoryError("subnet directory rollback detected")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise SubnetDirectoryError("subnet directory entries are invalid")
        for entry in entries:
            if not isinstance(entry, dict) or not str(entry.get("subnet_ref") or "").startswith("subnet:") or not str(entry.get("home_zone") or ""):
                raise SubnetDirectoryError("subnet directory entry is invalid")
            keys = entry.get("keys")
            if not isinstance(keys, list):
                raise SubnetDirectoryError("subnet directory keys are invalid")
            for key in keys:
                SubnetPurposeKey.from_mapping(key)
        self.high_water_generation = generation
        self._projection = {**payload, "signature_b64": signature_b64}

    def entry(self, subnet_ref: str) -> dict[str, Any]:
        if self._projection is None:
            raise SubnetDirectoryError("subnet directory is not initialized")
        matches = [dict(item) for item in self._projection["entries"] if item["subnet_ref"] == subnet_ref]
        if len(matches) != 1:
            raise SubnetDirectoryError("subnet is not present in signed directory")
        return matches[0]

    def key(self, subnet_ref: str, key_id: str, purpose: str, *, allow_retiring: bool = True) -> SubnetPurposeKey:
        entry = self.entry(subnet_ref)
        matches = [SubnetPurposeKey.from_mapping(item) for item in entry["keys"] if item.get("key_id") == key_id and item.get("purpose") == purpose]
        if len(matches) != 1:
            raise SubnetDirectoryError("purpose key is not present in signed directory")
        record = matches[0]
        if record.status == "revoked" or (record.status == "retiring" and not allow_retiring):
            raise SubnetDirectoryError("purpose key is not usable")
        return record

    def active_key(self, subnet_ref: str, purpose: str) -> SubnetPurposeKey:
        entry = self.entry(subnet_ref)
        matches = [SubnetPurposeKey.from_mapping(item) for item in entry["keys"] if item.get("purpose") == purpose and item.get("status") == "active"]
        if len(matches) != 1:
            raise SubnetDirectoryError(f"expected exactly one active {purpose} key")
        return matches[0]

    def home_zone(self, subnet_ref: str) -> str:
        return str(self.entry(subnet_ref)["home_zone"])

