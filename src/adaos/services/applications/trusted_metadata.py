from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.artifact_release import canonical_json_bytes, canonical_payload_digest, sha256_digest
from adaos.services.artifact_pipeline.storage import atomic_write_bytes, atomic_write_json, mutation_lock


METADATA_SCHEMA = "adaos.application.trusted_metadata.v1"
_ROLES = ("root", "targets", "snapshot", "freshness")


class TrustedMetadataError(RuntimeError):
    pass


def _time(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrustedMetadataError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise TrustedMetadataError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class MetadataSigner:
    role: str
    private_key: Ed25519PrivateKey

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise TrustedMetadataError(f"unsupported metadata role: {self.role}")

    @classmethod
    def generate(cls, role: str) -> "MetadataSigner":
        return cls(role=role, private_key=Ed25519PrivateKey.generate())

    @classmethod
    def load_or_create(cls, root: Path, role: str) -> "MetadataSigner":
        path = Path(root).expanduser().resolve() / f"{role}.ed25519"
        with mutation_lock(path.with_suffix(".lock"), timeout_s=30.0):
            if path.is_file():
                raw = path.read_bytes()
                if len(raw) != 32:
                    raise TrustedMetadataError(f"{role} metadata private key is corrupt")
                return cls(role, Ed25519PrivateKey.from_private_bytes(raw))
            signer = cls.generate(role)
            raw = signer.private_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            atomic_write_bytes(path, raw)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return signer

    @property
    def public_bytes(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    @property
    def key_id(self) -> str:
        return sha256_digest(self.public_bytes)

    def public_record(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "algorithm": "ed25519",
            "purpose": f"application_metadata_{self.role}",
            "public_key_b64": base64.b64encode(self.public_bytes).decode("ascii"),
        }

    def signature(self, signed: Mapping[str, Any]) -> dict[str, str]:
        signature = self.private_key.sign(canonical_json_bytes(dict(signed)))
        return {"key_id": self.key_id, "signature_b64": base64.b64encode(signature).decode("ascii")}


def _envelope(role: str, signed: Mapping[str, Any], signers: Iterable[MetadataSigner]) -> dict[str, Any]:
    selected = tuple(signers)
    if not selected or any(signer.role != role for signer in selected):
        raise TrustedMetadataError(f"{role} metadata requires role-scoped signers")
    payload = dict(signed)
    return {
        "schema": METADATA_SCHEMA,
        "role": role,
        "signed": payload,
        "signatures": [signer.signature(payload) for signer in selected],
    }


class TrustedMetadataAuthority:
    """Produces one internally consistent TUF-like Application metadata generation."""

    def __init__(self, root: Path, *, signers: Mapping[str, MetadataSigner] | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.root / ".mutation.lock"
        self.signers = dict(signers or {
            role: MetadataSigner.load_or_create(self.root / "private", role) for role in _ROLES
        })
        if set(self.signers) != set(_ROLES) or any(self.signers[role].role != role for role in _ROLES):
            raise TrustedMetadataError("metadata authority requires one signer for every role")

    def _index(self) -> dict[str, Any]:
        path = self.root / "index.json"
        if not path.is_file():
            return {"schema": "adaos.application.metadata_index.v1", "generation": 0, "versions": {role: 0 for role in _ROLES}}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != "adaos.application.metadata_index.v1":
            raise TrustedMetadataError("metadata index is corrupt")
        return payload

    def rotate_keys(
        self,
        signers: Mapping[str, MetadataSigner],
        *,
        now: datetime | None = None,
        root_days: int = 365,
    ) -> dict[str, Any]:
        replacement = dict(signers)
        if set(replacement) != set(_ROLES) or any(replacement[role].role != role for role in _ROLES):
            raise TrustedMetadataError("metadata key rotation requires one signer for every role")
        observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            current = self.current()["root"]
            current_version = int((current.get("signed") or {}).get("version") or 0)
            root_signed = {
                "version": current_version + 1,
                "expires_at": _iso(observed + timedelta(days=root_days)),
                "consistent_snapshot": True,
                "keys": {replacement[role].key_id: replacement[role].public_record() for role in _ROLES},
                "roles": {role: {"key_ids": [replacement[role].key_id], "threshold": 1} for role in _ROLES},
            }
            root_signers = [self.signers["root"]]
            if replacement["root"].key_id != self.signers["root"].key_id:
                root_signers.append(replacement["root"])
            pending = _envelope("root", root_signed, root_signers)
            atomic_write_json(self.root / "pending-root.json", pending)
            self.signers = replacement
            return pending

    @staticmethod
    def release_target(
        application: Application,
        release: ApplicationRelease,
        *,
        channels: Iterable[str],
        package_sizes: Mapping[str, int],
        attestation_set_digest: str,
        status: str = "active",
        addressed_report_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        if status not in {"active", "yanked", "revoked"}:
            raise TrustedMetadataError("release target status is invalid")
        packages = []
        for package in sorted(release.project_release.components, key=lambda item: item.key):
            size = int(package_sizes.get(package.digest, -1))
            if size < 0:
                raise TrustedMetadataError(f"package size is missing for {package.digest}")
            packages.append({"component_ref": package.key, "digest": package.digest, "size": size})
        return {
            "application_id": application.application_id,
            "legacy_project_id": application.legacy_project_id,
            "publisher_ref": application.publisher_ref,
            "publisher_key_id": application.publisher["release_key_fingerprint"],
            "release_digest": release.release_digest,
            "version": release.project_release.version,
            "channels": sorted(set(channels)),
            "visibility": application.visibility,
            "packages": packages,
            "attestation_set_digest": attestation_set_digest,
            "provenance_refs": list(release.provenance_refs),
            "addressed_report_ids": sorted(set(addressed_report_ids)),
            "status": status,
        }

    def publish(
        self,
        targets: Iterable[Mapping[str, Any]],
        *,
        now: datetime | None = None,
        revoked_publisher_keys: Iterable[str] = (),
        emergency_disabled_applications: Iterable[str] = (),
        root_days: int = 365,
        targets_days: int = 30,
        freshness_hours: int = 24,
    ) -> dict[str, Any]:
        observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            index = self._index()
            previous_versions = index.get("versions") or {}
            pending_root_path = self.root / "pending-root.json"
            pending_root = (
                json.loads(pending_root_path.read_text(encoding="utf-8"))
                if pending_root_path.is_file()
                else None
            )
            versions = {
                "root": (
                    int((pending_root.get("signed") or {}).get("version") or 0)
                    if isinstance(pending_root, Mapping)
                    else max(1, int(previous_versions.get("root") or 0))
                ),
                "targets": int(previous_versions.get("targets") or 0) + 1,
                "snapshot": int(previous_versions.get("snapshot") or 0) + 1,
                "freshness": int(previous_versions.get("freshness") or 0) + 1,
            }
            if isinstance(pending_root, Mapping):
                root_envelope = dict(pending_root)
            elif int(index.get("generation") or 0) > 0:
                root_envelope = self.current()["root"]
            else:
                root_signed = {
                    "version": versions["root"],
                    "expires_at": _iso(observed + timedelta(days=root_days)),
                    "consistent_snapshot": True,
                    "keys": {self.signers[role].key_id: self.signers[role].public_record() for role in _ROLES},
                    "roles": {role: {"key_ids": [self.signers[role].key_id], "threshold": 1} for role in _ROLES},
                }
                root_envelope = _envelope("root", root_signed, (self.signers["root"],))
            ordered_targets = sorted((dict(item) for item in targets), key=lambda item: (str(item.get("application_id")), str(item.get("release_digest"))))
            targets_signed = {
                "version": versions["targets"],
                "expires_at": _iso(observed + timedelta(days=targets_days)),
                "targets": ordered_targets,
                "revoked_publisher_keys": sorted(set(revoked_publisher_keys)),
                "emergency_disabled_applications": sorted(set(emergency_disabled_applications)),
            }
            targets_envelope = _envelope("targets", targets_signed, (self.signers["targets"],))
            snapshot_signed = {
                "version": versions["snapshot"],
                "expires_at": _iso(observed + timedelta(hours=freshness_hours * 2)),
                "meta": {
                    "root": {"version": versions["root"], "digest": canonical_payload_digest(root_envelope)},
                    "targets": {"version": versions["targets"], "digest": canonical_payload_digest(targets_envelope)},
                },
            }
            snapshot_envelope = _envelope("snapshot", snapshot_signed, (self.signers["snapshot"],))
            freshness_signed = {
                "version": versions["freshness"],
                "expires_at": _iso(observed + timedelta(hours=freshness_hours)),
                "snapshot": {"version": versions["snapshot"], "digest": canonical_payload_digest(snapshot_envelope)},
            }
            freshness_envelope = _envelope("freshness", freshness_signed, (self.signers["freshness"],))
            bundle = {
                "schema": "adaos.application.metadata_bundle.v1",
                "generation": int(index.get("generation") or 0) + 1,
                "root": root_envelope,
                "targets": targets_envelope,
                "snapshot": snapshot_envelope,
                "freshness": freshness_envelope,
            }
            generation_root = self.root / "generations" / str(bundle["generation"])
            for role in _ROLES:
                atomic_write_json(generation_root / f"{role}.json", bundle[role])
            atomic_write_json(generation_root / "bundle.json", bundle)
            atomic_write_json(
                self.root / "index.json",
                {
                    "schema": "adaos.application.metadata_index.v1",
                    "generation": bundle["generation"],
                    "versions": versions,
                    "bundle_digest": canonical_payload_digest(bundle),
                    "updated_at": _iso(observed),
                },
            )
            if pending_root_path.is_file():
                pending_root_path.unlink()
            return bundle

    def current(self) -> dict[str, Any]:
        index = self._index()
        generation = int(index.get("generation") or 0)
        if generation < 1:
            raise FileNotFoundError("trusted Application metadata is not published")
        payload = json.loads((self.root / "generations" / str(generation) / "bundle.json").read_text(encoding="utf-8"))
        if canonical_payload_digest(payload) != index.get("bundle_digest"):
            raise TrustedMetadataError("current metadata bundle digest mismatch")
        return payload


class TrustedMetadataClient:
    """Verifies metadata generations and persists monotonic trust state."""

    def __init__(self, state_path: Path, *, pinned_root_key_id: str) -> None:
        self.state_path = Path(state_path).expanduser().resolve()
        self.pinned_root_key_id = str(pinned_root_key_id or "")
        self.lock_path = self.state_path.with_suffix(".lock")

    def _state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {
                "schema": "adaos.application.metadata_client_state.v1",
                "versions": {role: 0 for role in _ROLES},
                "trusted_root": None,
            }
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != "adaos.application.metadata_client_state.v1":
            raise TrustedMetadataError("metadata client state is corrupt")
        return payload

    @staticmethod
    def _role_keys(root_envelope: Mapping[str, Any], role: str) -> tuple[dict[str, Any], int]:
        signed = root_envelope.get("signed")
        if not isinstance(signed, Mapping):
            raise TrustedMetadataError("root metadata signed payload is invalid")
        keys = signed.get("keys")
        roles = signed.get("roles")
        role_record = roles.get(role) if isinstance(roles, Mapping) else None
        if not isinstance(keys, Mapping) or not isinstance(role_record, Mapping):
            raise TrustedMetadataError("root metadata key roles are invalid")
        ids = role_record.get("key_ids")
        threshold = int(role_record.get("threshold") or 0)
        if not isinstance(ids, list) or threshold < 1:
            raise TrustedMetadataError("root metadata role threshold is invalid")
        selected = {key_id: keys[key_id] for key_id in ids if key_id in keys}
        if len(selected) < threshold:
            raise TrustedMetadataError("root metadata role has insufficient keys")
        return selected, threshold

    @staticmethod
    def _verify_envelope(
        envelope: Mapping[str, Any],
        *,
        role: str,
        keys: Mapping[str, Any],
        threshold: int,
    ) -> Mapping[str, Any]:
        if envelope.get("schema") != METADATA_SCHEMA or envelope.get("role") != role:
            raise TrustedMetadataError(f"unsupported {role} metadata envelope")
        signed = envelope.get("signed")
        signatures = envelope.get("signatures")
        if not isinstance(signed, Mapping) or not isinstance(signatures, list):
            raise TrustedMetadataError(f"invalid {role} metadata envelope")
        valid: set[str] = set()
        for signature in signatures:
            if not isinstance(signature, Mapping):
                continue
            key_id = str(signature.get("key_id") or "")
            key = keys.get(key_id)
            if not isinstance(key, Mapping) or key_id in valid:
                continue
            try:
                public = base64.b64decode(str(key.get("public_key_b64") or ""), validate=True)
                signature_bytes = base64.b64decode(str(signature.get("signature_b64") or ""), validate=True)
                if sha256_digest(public) != key_id or key.get("algorithm") != "ed25519":
                    continue
                Ed25519PublicKey.from_public_bytes(public).verify(signature_bytes, canonical_json_bytes(dict(signed)))
            except (ValueError, InvalidSignature):
                continue
            valid.add(key_id)
        if len(valid) < threshold:
            raise TrustedMetadataError(f"{role} metadata signature threshold is not met")
        return signed

    def verify_release(
        self,
        bundle: Mapping[str, Any],
        *,
        application_id: str,
        publisher_ref: str,
        release_digest: str,
        observed_packages: Mapping[str, int],
        now: datetime | None = None,
        allow_stale_installed: bool = False,
    ) -> dict[str, Any]:
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._state()
            root = bundle.get("root")
            if not isinstance(root, Mapping):
                raise TrustedMetadataError("metadata bundle has no root")
            trusted_root = state.get("trusted_root")
            bootstrap_keys, bootstrap_threshold = self._role_keys(root, "root")
            if trusted_root is None:
                if self.pinned_root_key_id not in bootstrap_keys:
                    raise TrustedMetadataError("root metadata does not contain the pinned root key")
                bootstrap_keys = {self.pinned_root_key_id: bootstrap_keys[self.pinned_root_key_id]}
                bootstrap_threshold = 1
            else:
                old_keys, bootstrap_threshold = self._role_keys(trusted_root, "root")
                bootstrap_keys = old_keys
            root_signed = self._verify_envelope(root, role="root", keys=bootstrap_keys, threshold=bootstrap_threshold)
            if trusted_root is not None and int(root_signed.get("version") or 0) > int((trusted_root.get("signed") or {}).get("version") or 0):
                new_keys, new_threshold = self._role_keys(root, "root")
                self._verify_envelope(root, role="root", keys=new_keys, threshold=new_threshold)
            signed: dict[str, Mapping[str, Any]] = {"root": root_signed}
            for role in ("targets", "snapshot", "freshness"):
                envelope = bundle.get(role)
                if not isinstance(envelope, Mapping):
                    raise TrustedMetadataError(f"metadata bundle has no {role}")
                keys, threshold = self._role_keys(root, role)
                signed[role] = self._verify_envelope(envelope, role=role, keys=keys, threshold=threshold)
            versions = {role: int(signed[role].get("version") or 0) for role in _ROLES}
            high_water = state.get("versions") or {}
            for role, version in versions.items():
                if version < int(high_water.get(role) or 0):
                    raise TrustedMetadataError(f"{role} metadata rollback detected")
            snapshot_meta = signed["snapshot"].get("meta")
            freshness_snapshot = signed["freshness"].get("snapshot")
            if not isinstance(snapshot_meta, Mapping) or not isinstance(freshness_snapshot, Mapping):
                raise TrustedMetadataError("metadata snapshot bindings are invalid")
            for role in ("root", "targets"):
                binding = snapshot_meta.get(role)
                if (
                    not isinstance(binding, Mapping)
                    or int(binding.get("version") or 0) != versions[role]
                    or binding.get("digest") != canonical_payload_digest(bundle[role])
                ):
                    raise TrustedMetadataError(f"metadata mix-and-match detected for {role}")
            if (
                int(freshness_snapshot.get("version") or 0) != versions["snapshot"]
                or freshness_snapshot.get("digest") != canonical_payload_digest(bundle["snapshot"])
            ):
                raise TrustedMetadataError("metadata freshness snapshot mismatch")
            expired = [role for role in _ROLES if observed_at >= _time(str(signed[role].get("expires_at") or ""), field=f"{role}.expires_at")]
            raw_targets = signed["targets"].get("targets")
            if not isinstance(raw_targets, list):
                raise TrustedMetadataError("targets metadata is invalid")
            matches = [
                item for item in raw_targets
                if isinstance(item, Mapping)
                and item.get("application_id") == application_id
                and item.get("release_digest") == release_digest
            ]
            if len(matches) != 1:
                raise TrustedMetadataError("exact Application release target is unavailable")
            target = dict(matches[0])
            if target.get("publisher_ref") != publisher_ref:
                raise TrustedMetadataError("Application release publisher is unknown")
            if application_id in set(signed["targets"].get("emergency_disabled_applications") or []):
                raise TrustedMetadataError("Application is emergency disabled")
            if target.get("publisher_key_id") in set(signed["targets"].get("revoked_publisher_keys") or []):
                raise TrustedMetadataError("Application publisher release key is revoked")
            if target.get("status") in {"yanked", "revoked"}:
                raise TrustedMetadataError(f"Application release is {target.get('status')}")
            expected_packages = {
                str(item.get("digest")): int(item.get("size") or 0)
                for item in target.get("packages") or []
                if isinstance(item, Mapping)
            }
            if expected_packages != {str(key): int(value) for key, value in observed_packages.items()}:
                raise TrustedMetadataError("Application package size/digest set is inconsistent")
            if expired:
                if not allow_stale_installed:
                    raise TrustedMetadataError("Application metadata is stale: " + ", ".join(expired))
                return {
                    "status": "stale_metadata_installed_only",
                    "application_id": application_id,
                    "release_digest": release_digest,
                    "expired_roles": expired,
                    "target": target,
                }
            atomic_write_json(
                self.state_path,
                {
                    "schema": "adaos.application.metadata_client_state.v1",
                    "versions": versions,
                    "trusted_root": dict(root),
                    "last_bundle_digest": canonical_payload_digest(dict(bundle)),
                    "updated_at": _iso(observed_at),
                },
            )
            return {
                "status": "verified",
                "application_id": application_id,
                "release_digest": release_digest,
                "versions": versions,
                "target": target,
            }


__all__ = [
    "METADATA_SCHEMA",
    "MetadataSigner",
    "TrustedMetadataAuthority",
    "TrustedMetadataClient",
    "TrustedMetadataError",
]
