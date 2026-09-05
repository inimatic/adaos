from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


PURPOSE_ALGORITHMS = {
    "transport_auth": {"rsa-3072"},
    "release_signing": {"ed25519"},
    "message_signing": {"ed25519"},
    "message_encryption": {"x25519-hkdf-sha256-aes256gcm"},
}


class SubnetKeyError(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _public_bytes(key: Ed25519PublicKey | X25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _private_bytes(key: Ed25519PrivateKey | X25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def _key_id(public: bytes) -> str:
    return f"sha256:{hashlib.sha256(public).hexdigest()}"


def _subnet_ref(value: str) -> str:
    token = str(value or "").strip().lower()
    if not token.startswith("subnet:") or len(token) <= 7:
        raise SubnetKeyError("subnet_ref must use subnet:<id>")
    return token


@dataclass(frozen=True, slots=True)
class SubnetPurposeKey:
    subnet_ref: str
    key_id: str
    purpose: str
    algorithm: str
    public_key_b64: str
    valid_from: str
    valid_to: str | None
    status: str
    issuer: str
    replacement_key_id: str | None = None
    revocation_evidence: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subnet_ref", _subnet_ref(self.subnet_ref))
        if self.purpose not in PURPOSE_ALGORITHMS:
            raise SubnetKeyError("unsupported subnet key purpose")
        if self.algorithm not in PURPOSE_ALGORITHMS[self.purpose]:
            raise SubnetKeyError("algorithm does not match key purpose")
        try:
            public = base64.b64decode(self.public_key_b64, validate=True)
        except Exception as exc:
            raise SubnetKeyError("public_key_b64 is invalid") from exc
        if (self.algorithm != "rsa-3072" and len(public) != 32) or self.key_id != _key_id(public):
            raise SubnetKeyError("key_id does not match public key")
        _parse(self.valid_from)
        if self.valid_to is not None:
            _parse(self.valid_to)
        if self.status not in {"active", "retiring", "revoked"}:
            raise SubnetKeyError("subnet key status is invalid")
        if self.replacement_key_id is not None and not self.replacement_key_id.startswith("sha256:"):
            raise SubnetKeyError("replacement_key_id is invalid")
        if self.revocation_evidence is not None and not isinstance(self.revocation_evidence, Mapping):
            raise SubnetKeyError("revocation_evidence must be an object")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "adaos.subnet.purpose_key.v1",
            "subnet_ref": self.subnet_ref,
            "key_id": self.key_id,
            "purpose": self.purpose,
            "algorithm": self.algorithm,
            "public_key_b64": self.public_key_b64,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "status": self.status,
            "issuer": self.issuer,
            "replacement_key_id": self.replacement_key_id,
            "revocation_evidence": dict(self.revocation_evidence) if self.revocation_evidence is not None else None,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SubnetPurposeKey":
        payload = dict(value)
        if payload.pop("schema", None) != "adaos.subnet.purpose_key.v1":
            raise SubnetKeyError("unsupported subnet purpose key schema")
        allowed = {
            "subnet_ref", "key_id", "purpose", "algorithm", "public_key_b64",
            "valid_from", "valid_to", "status", "issuer", "replacement_key_id",
            "revocation_evidence",
        }
        if set(payload) != allowed:
            raise SubnetKeyError("subnet purpose key fields are invalid")
        return cls(**payload)


class SubnetPurposeKeyStore:
    """Local private key ring for one or more existing subnet identities."""

    def __init__(self, state_dir: Path, *, now: Callable[[], datetime] = _utc_now) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.now = now

    @property
    def root(self) -> Path:
        path = self.state_dir / "subnet_identity" / "purpose_keys"
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
            return {"schema": "adaos.subnet.purpose_key_store.v1", "keys": {}, "audit": []}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SubnetKeyError("subnet purpose key store is unreadable") from exc
        if not isinstance(state, dict) or state.get("schema") != "adaos.subnet.purpose_key_store.v1" or not isinstance(state.get("keys"), dict):
            raise SubnetKeyError("subnet purpose key store is invalid")
        return state

    def _write(self, state: Mapping[str, Any]) -> None:
        atomic_write_json(self.state_path, dict(state))

    def _generate(self, subnet_ref: str, purpose: str, issuer: str) -> tuple[SubnetPurposeKey, str]:
        if purpose == "message_signing":
            private: Ed25519PrivateKey | X25519PrivateKey = Ed25519PrivateKey.generate()
        elif purpose == "message_encryption":
            private = X25519PrivateKey.generate()
        else:
            raise SubnetKeyError("unsupported subnet key purpose")
        public = _public_bytes(private.public_key())
        record = SubnetPurposeKey(
            subnet_ref=_subnet_ref(subnet_ref), key_id=_key_id(public), purpose=purpose,
            algorithm=next(iter(PURPOSE_ALGORITHMS[purpose])), public_key_b64=base64.b64encode(public).decode("ascii"),
            valid_from=_iso(self.now()), valid_to=None, status="active", issuer=str(issuer or "local-owner"),
        )
        return record, base64.b64encode(_private_bytes(private)).decode("ascii")

    def ensure_key(self, subnet_ref: str, purpose: str, *, issuer: str = "local-owner") -> SubnetPurposeKey:
        subnet = _subnet_ref(subnet_ref)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            for stored in state["keys"].values():
                record = SubnetPurposeKey.from_mapping(stored["record"])
                if record.subnet_ref == subnet and record.purpose == purpose and record.status == "active":
                    return record
            record, private = self._generate(subnet, purpose, issuer)
            state["keys"][record.key_id] = {"record": record.to_dict(), "private_key_b64": private}
            state.setdefault("audit", []).append({"action": "created", "key_id": record.key_id, "subnet_ref": subnet, "purpose": purpose, "at": _iso(self.now()), "issuer": issuer})
            self._write(state)
            return record

    def list_public(self, subnet_ref: str | None = None) -> tuple[SubnetPurposeKey, ...]:
        subnet = _subnet_ref(subnet_ref) if subnet_ref is not None else None
        values = [SubnetPurposeKey.from_mapping(item["record"]) for item in self._read()["keys"].values()]
        return tuple(sorted((item for item in values if subnet is None or item.subnet_ref == subnet), key=lambda item: (item.subnet_ref, item.purpose, item.valid_from, item.key_id)))

    def register_public_key(
        self,
        subnet_ref: str,
        purpose: str,
        *,
        algorithm: str,
        public_key: bytes,
        issuer: str,
    ) -> SubnetPurposeKey:
        """Project an existing transport or release key without copying its private material."""
        subnet = _subnet_ref(subnet_ref)
        if algorithm not in PURPOSE_ALGORITHMS.get(purpose, set()):
            raise SubnetKeyError("algorithm does not match key purpose")
        record = SubnetPurposeKey(
            subnet_ref=subnet, key_id=_key_id(public_key), purpose=purpose, algorithm=algorithm,
            public_key_b64=base64.b64encode(public_key).decode("ascii"), valid_from=_iso(self.now()),
            valid_to=None, status="active", issuer=str(issuer or "existing-key-contract"),
        )
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            existing = state["keys"].get(record.key_id)
            if existing is not None:
                if existing.get("record") != record.to_dict():
                    raise SubnetKeyError("existing purpose key projection conflicts")
                return record
            for stored in state["keys"].values():
                current = SubnetPurposeKey.from_mapping(stored["record"])
                if current.subnet_ref == subnet and current.purpose == purpose and current.status == "active":
                    raise SubnetKeyError("purpose already has an active key; rotate it explicitly")
            state["keys"][record.key_id] = {"record": record.to_dict(), "private_key_b64": None}
            state.setdefault("audit", []).append({"action": "projected", "key_id": record.key_id, "subnet_ref": subnet, "purpose": purpose, "at": _iso(self.now()), "issuer": issuer})
            self._write(state)
        return record

    def get_public(self, key_id: str, *, allow_retiring: bool = True) -> SubnetPurposeKey:
        stored = self._read()["keys"].get(str(key_id))
        if not isinstance(stored, dict):
            raise SubnetKeyError("subnet purpose key is unknown")
        record = SubnetPurposeKey.from_mapping(stored["record"])
        allowed = {"active", "retiring"} if allow_retiring else {"active"}
        if record.status not in allowed:
            raise SubnetKeyError("subnet purpose key is not usable")
        if record.valid_to is not None and self.now() > _parse(record.valid_to):
            raise SubnetKeyError("subnet purpose key has expired")
        return record

    def active_key(self, subnet_ref: str, purpose: str) -> SubnetPurposeKey:
        matches = [item for item in self.list_public(subnet_ref) if item.purpose == purpose and item.status == "active"]
        if len(matches) != 1:
            raise SubnetKeyError(f"expected exactly one active {purpose} key")
        return matches[0]

    def private_key(self, key_id: str) -> Ed25519PrivateKey | X25519PrivateKey:
        record = self.get_public(key_id, allow_retiring=True)
        stored = self._read()["keys"][record.key_id]
        try:
            if not stored.get("private_key_b64"):
                raise SubnetKeyError("private key remains with its purpose-specific authority")
            raw = base64.b64decode(stored["private_key_b64"], validate=True)
        except Exception as exc:
            raise SubnetKeyError("private key material is unreadable") from exc
        if record.purpose == "message_signing":
            return Ed25519PrivateKey.from_private_bytes(raw)
        return X25519PrivateKey.from_private_bytes(raw)

    def rotate(self, subnet_ref: str, purpose: str, *, actor: str, overlap: timedelta = timedelta(days=14)) -> SubnetPurposeKey:
        subnet = _subnet_ref(subnet_ref)
        if overlap.total_seconds() <= 0:
            raise SubnetKeyError("rotation overlap must be positive")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            active_ids = []
            for key_id, stored in state["keys"].items():
                record = SubnetPurposeKey.from_mapping(stored["record"])
                if record.subnet_ref == subnet and record.purpose == purpose and record.status == "active":
                    active_ids.append(key_id)
            if len(active_ids) != 1:
                raise SubnetKeyError("rotation requires exactly one active key")
            replacement, private = self._generate(subnet, purpose, actor)
            old_stored = state["keys"][active_ids[0]]
            old = SubnetPurposeKey.from_mapping(old_stored["record"])
            retiring = replace(old, status="retiring", valid_to=_iso(self.now() + overlap), replacement_key_id=replacement.key_id)
            old_stored["record"] = retiring.to_dict()
            state["keys"][replacement.key_id] = {"record": replacement.to_dict(), "private_key_b64": private}
            state.setdefault("audit", []).append({"action": "rotated", "key_id": old.key_id, "replacement_key_id": replacement.key_id, "subnet_ref": subnet, "purpose": purpose, "at": _iso(self.now()), "actor": actor})
            self._write(state)
            return replacement

    def revoke(self, key_id: str, *, actor: str, reason: str, evidence_ref: str) -> SubnetPurposeKey:
        if not str(reason).strip() or not str(evidence_ref).strip():
            raise SubnetKeyError("revocation reason and evidence_ref are required")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            stored = state["keys"].get(str(key_id))
            if not isinstance(stored, dict):
                raise SubnetKeyError("subnet purpose key is unknown")
            record = SubnetPurposeKey.from_mapping(stored["record"])
            revoked = replace(record, status="revoked", valid_to=_iso(self.now()), revocation_evidence={"reason": reason, "evidence_ref": evidence_ref, "actor": actor, "revoked_at": _iso(self.now())})
            stored["record"] = revoked.to_dict()
            state.setdefault("audit", []).append({"action": "revoked", "key_id": key_id, "at": _iso(self.now()), "actor": actor, "reason": reason, "evidence_ref": evidence_ref})
            self._write(state)
            return revoked

    def recover(self, subnet_ref: str, purpose: str, *, owner_factor_ref: str, actor: str) -> SubnetPurposeKey:
        if not str(owner_factor_ref).strip():
            raise SubnetKeyError("recovery requires an existing owner recovery factor")
        subnet = _subnet_ref(subnet_ref)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            for stored in state["keys"].values():
                record = SubnetPurposeKey.from_mapping(stored["record"])
                if record.subnet_ref != subnet or record.purpose != purpose or record.status == "revoked":
                    continue
                stored["record"] = replace(record, status="revoked", valid_to=_iso(self.now()), revocation_evidence={"reason": "key_recovery", "evidence_ref": owner_factor_ref, "actor": actor, "revoked_at": _iso(self.now())}).to_dict()
            replacement, private = self._generate(subnet, purpose, actor)
            state["keys"][replacement.key_id] = {"record": replacement.to_dict(), "private_key_b64": private}
            state.setdefault("audit", []).append({"action": "recovered", "replacement_key_id": replacement.key_id, "subnet_ref": subnet, "purpose": purpose, "owner_factor_ref": owner_factor_ref, "at": _iso(self.now()), "actor": actor})
            self._write(state)
            return replacement
