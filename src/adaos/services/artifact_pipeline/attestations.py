from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from adaos.domain.artifact_release import (
    canonical_json_bytes,
    canonical_payload_digest,
    sha256_digest,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import (
    atomic_write_bytes,
    atomic_write_json,
    mutation_lock,
)


ARTIFACT_ATTESTATION_SCHEMA = "adaos.artifact.attestation.v1"
ARTIFACT_TRUST_STORE_SCHEMA = "adaos.artifact.trust_store.v1"
ARTIFACT_TRUST_KEY_SCHEMA = "adaos.artifact.trust_key.v1"
ARTIFACT_ATTESTATION_MEDIA_TYPE = "application/vnd.adaos.artifact-attestation+json"
PACKAGE_PROVENANCE_PREDICATE = "adaos.artifact.package_provenance.v1"
RELEASE_PROVENANCE_PREDICATE = "adaos.artifact.release_provenance.v1"

ArtifactSubjectKind = Literal["package", "release"]

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_SUPPORTED_SUBJECTS = {"package", "release"}
_MAX_ATTESTATION_BYTES = 64 * 1024


class ArtifactAttestationError(ValueError):
    pass


class ArtifactAttestationVerificationError(ArtifactAttestationError):
    pass


def _require_text(value: Any, *, field: str, maximum: int = 512) -> str:
    token = str(value or "").strip()
    if not token:
        raise ArtifactAttestationError(f"{field} must not be empty")
    if len(token) > maximum:
        raise ArtifactAttestationError(f"{field} exceeds {maximum} characters")
    return token


def _require_digest(value: Any, *, field: str) -> str:
    token = _require_text(value, field=field).lower()
    if not _DIGEST_RE.fullmatch(token):
        raise ArtifactAttestationError(
            f"{field} must be sha256:<64 lowercase hex characters>"
        )
    return token


def _require_project_id(value: Any) -> str:
    token = _require_text(value, field="project_id", maximum=255)
    if not _PROJECT_ID_RE.fullmatch(token):
        raise ArtifactAttestationError(f"project_id is invalid: {token!r}")
    return token


def _canonical_timestamp(value: Any, *, field: str) -> str:
    token = _require_text(value, field=field, maximum=64)
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactAttestationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ArtifactAttestationError(f"{field} must include a timezone")
    normalized = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _decode_b64(value: Any, *, field: str, expected_bytes: int) -> tuple[str, bytes]:
    token = _require_text(value, field=field, maximum=4096)
    try:
        raw = base64.b64decode(token, validate=True)
    except Exception as exc:
        raise ArtifactAttestationError(f"{field} must be canonical base64") from exc
    if len(raw) != expected_bytes:
        raise ArtifactAttestationError(
            f"{field} must decode to exactly {expected_bytes} bytes"
        )
    canonical = base64.b64encode(raw).decode("ascii")
    if canonical != token:
        raise ArtifactAttestationError(f"{field} must use canonical padded base64")
    return canonical, raw


def _require_mapping(
    value: Mapping[str, Any],
    *,
    schema: str,
    allowed: set[str],
    required: set[str],
    field: str,
) -> None:
    if value.get("schema") != schema:
        raise ArtifactAttestationError(
            f"unsupported {field} schema: {value.get('schema')!r}; expected {schema!r}"
        )
    unknown = sorted(str(key) for key in set(value) - allowed)
    if unknown:
        raise ArtifactAttestationError(
            f"{field} contains unsupported fields: {', '.join(unknown)}"
        )
    missing = sorted(required - set(value))
    if missing:
        raise ArtifactAttestationError(
            f"{field} is missing required fields: {', '.join(missing)}"
        )


def _unsigned_attestation_payload(
    *,
    subject_kind: ArtifactSubjectKind,
    subject_digest: str,
    project_id: str,
    issuer: str,
    key_id: str,
    issued_at: str,
    predicate_type: str,
    predicate_digest: str,
) -> dict[str, Any]:
    return {
        "schema": ARTIFACT_ATTESTATION_SCHEMA,
        "subject_kind": subject_kind,
        "subject_digest": subject_digest,
        "project_id": project_id,
        "issuer": issuer,
        "key_id": key_id,
        "issued_at": issued_at,
        "predicate_type": predicate_type,
        "predicate_digest": predicate_digest,
        "algorithm": "ed25519",
    }


@dataclass(frozen=True, slots=True)
class ArtifactAttestation:
    subject_kind: ArtifactSubjectKind
    subject_digest: str
    project_id: str
    issuer: str
    key_id: str
    issued_at: str
    predicate_type: str
    predicate_digest: str
    signature_b64: str
    algorithm: str = "ed25519"
    attestation_digest: str | None = None

    def __post_init__(self) -> None:
        if self.subject_kind not in _SUPPORTED_SUBJECTS:
            raise ArtifactAttestationError("subject_kind must be package or release")
        object.__setattr__(
            self,
            "subject_digest",
            _require_digest(self.subject_digest, field="subject_digest"),
        )
        object.__setattr__(self, "project_id", _require_project_id(self.project_id))
        object.__setattr__(self, "issuer", _require_text(self.issuer, field="issuer"))
        object.__setattr__(self, "key_id", _require_digest(self.key_id, field="key_id"))
        object.__setattr__(
            self,
            "issued_at",
            _canonical_timestamp(self.issued_at, field="issued_at"),
        )
        object.__setattr__(
            self,
            "predicate_type",
            _require_text(self.predicate_type, field="predicate_type"),
        )
        object.__setattr__(
            self,
            "predicate_digest",
            _require_digest(self.predicate_digest, field="predicate_digest"),
        )
        if self.algorithm != "ed25519":
            raise ArtifactAttestationError("algorithm must be ed25519")
        signature, _ = _decode_b64(
            self.signature_b64,
            field="signature_b64",
            expected_bytes=64,
        )
        object.__setattr__(self, "signature_b64", signature)
        if self.attestation_digest is not None:
            object.__setattr__(
                self,
                "attestation_digest",
                _require_digest(self.attestation_digest, field="attestation_digest"),
            )

    def unsigned_dict(self) -> dict[str, Any]:
        return _unsigned_attestation_payload(
            subject_kind=self.subject_kind,
            subject_digest=self.subject_digest,
            project_id=self.project_id,
            issuer=self.issuer,
            key_id=self.key_id,
            issued_at=self.issued_at,
            predicate_type=self.predicate_type,
            predicate_digest=self.predicate_digest,
        )

    def signed_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "signature_b64": self.signature_b64}

    def computed_digest(self) -> str:
        return canonical_payload_digest(self.signed_dict())

    def seal(self) -> "ArtifactAttestation":
        digest = self.computed_digest()
        if self.attestation_digest is not None and self.attestation_digest != digest:
            raise ArtifactAttestationError(
                "attestation_digest does not match ArtifactAttestation content"
            )
        return replace(self, attestation_digest=digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.signed_dict(),
            "attestation_digest": self.attestation_digest or self.computed_digest(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ArtifactAttestation":
        fields = {
            "schema",
            "subject_kind",
            "subject_digest",
            "project_id",
            "issuer",
            "key_id",
            "issued_at",
            "predicate_type",
            "predicate_digest",
            "algorithm",
            "signature_b64",
            "attestation_digest",
        }
        _require_mapping(
            value,
            schema=ARTIFACT_ATTESTATION_SCHEMA,
            allowed=fields,
            required=fields,
            field="ArtifactAttestation",
        )
        return cls(
            subject_kind=value.get("subject_kind"),
            subject_digest=value.get("subject_digest"),
            project_id=value.get("project_id"),
            issuer=value.get("issuer"),
            key_id=value.get("key_id"),
            issued_at=value.get("issued_at"),
            predicate_type=value.get("predicate_type"),
            predicate_digest=value.get("predicate_digest"),
            algorithm=value.get("algorithm"),
            signature_b64=value.get("signature_b64"),
            attestation_digest=value.get("attestation_digest"),
        ).seal()


@dataclass(frozen=True, slots=True)
class TrustedArtifactKey:
    issuer: str
    public_key_b64: str
    purposes: tuple[ArtifactSubjectKind, ...] = ("package", "release")
    not_before: str | None = None
    not_after: str | None = None
    revoked_at: str | None = None
    revocation_reason: str | None = None
    key_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer", _require_text(self.issuer, field="issuer"))
        encoded, raw = _decode_b64(
            self.public_key_b64,
            field="public_key_b64",
            expected_bytes=32,
        )
        object.__setattr__(self, "public_key_b64", encoded)
        derived = sha256_digest(raw)
        if self.key_id is not None and _require_digest(self.key_id, field="key_id") != derived:
            raise ArtifactAttestationError("key_id does not match Ed25519 public key")
        object.__setattr__(self, "key_id", derived)
        normalized_purposes = tuple(sorted(set(self.purposes)))
        if not normalized_purposes or any(
            purpose not in _SUPPORTED_SUBJECTS for purpose in normalized_purposes
        ):
            raise ArtifactAttestationError("purposes must contain package and/or release")
        object.__setattr__(self, "purposes", normalized_purposes)
        for field in ("not_before", "not_after", "revoked_at"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(
                    self,
                    field,
                    _canonical_timestamp(value, field=field),
                )
        if self.not_before and self.not_after and _timestamp(self.not_before) >= _timestamp(
            self.not_after
        ):
            raise ArtifactAttestationError("not_before must be earlier than not_after")
        if self.revoked_at is not None:
            object.__setattr__(
                self,
                "revocation_reason",
                _require_text(self.revocation_reason, field="revocation_reason"),
            )
        elif self.revocation_reason is not None:
            raise ArtifactAttestationError("revocation_reason requires revoked_at")

    @property
    def public_key(self) -> Ed25519PublicKey:
        _, raw = _decode_b64(
            self.public_key_b64,
            field="public_key_b64",
            expected_bytes=32,
        )
        return Ed25519PublicKey.from_public_bytes(raw)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": ARTIFACT_TRUST_KEY_SCHEMA,
            "key_id": self.key_id,
            "issuer": self.issuer,
            "algorithm": "ed25519",
            "public_key_b64": self.public_key_b64,
            "purposes": list(self.purposes),
        }
        for field in ("not_before", "not_after", "revoked_at", "revocation_reason"):
            value = getattr(self, field)
            if value is not None:
                payload[field] = value
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TrustedArtifactKey":
        allowed = {
            "schema",
            "key_id",
            "issuer",
            "algorithm",
            "public_key_b64",
            "purposes",
            "not_before",
            "not_after",
            "revoked_at",
            "revocation_reason",
        }
        _require_mapping(
            value,
            schema=ARTIFACT_TRUST_KEY_SCHEMA,
            allowed=allowed,
            required={
                "schema",
                "key_id",
                "issuer",
                "algorithm",
                "public_key_b64",
                "purposes",
            },
            field="TrustedArtifactKey",
        )
        if value.get("algorithm") != "ed25519":
            raise ArtifactAttestationError("trusted key algorithm must be ed25519")
        purposes = value.get("purposes")
        if not isinstance(purposes, list):
            raise ArtifactAttestationError("trusted key purposes must be a list")
        return cls(
            issuer=value.get("issuer"),
            public_key_b64=value.get("public_key_b64"),
            purposes=tuple(purposes),
            not_before=value.get("not_before"),
            not_after=value.get("not_after"),
            revoked_at=value.get("revoked_at"),
            revocation_reason=value.get("revocation_reason"),
            key_id=value.get("key_id"),
        )


class Ed25519ArtifactSigner:
    def __init__(self, *, issuer: str, private_key: Ed25519PrivateKey) -> None:
        self.issuer = _require_text(issuer, field="issuer")
        self._private_key = private_key

    @classmethod
    def generate(cls, *, issuer: str) -> "Ed25519ArtifactSigner":
        return cls(issuer=issuer, private_key=Ed25519PrivateKey.generate())

    @classmethod
    def from_private_key_bytes(
        cls,
        *,
        issuer: str,
        private_key: bytes,
    ) -> "Ed25519ArtifactSigner":
        if len(private_key) != 32:
            raise ArtifactAttestationError("Ed25519 private key must contain 32 raw bytes")
        return cls(
            issuer=issuer,
            private_key=Ed25519PrivateKey.from_private_bytes(private_key),
        )

    def private_key_bytes(self) -> bytes:
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def trusted_key(
        self,
        *,
        purposes: Iterable[ArtifactSubjectKind] = ("package", "release"),
        not_before: str | None = None,
        not_after: str | None = None,
    ) -> TrustedArtifactKey:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return TrustedArtifactKey(
            issuer=self.issuer,
            public_key_b64=base64.b64encode(raw).decode("ascii"),
            purposes=tuple(purposes),
            not_before=not_before,
            not_after=not_after,
        )

    def sign(
        self,
        *,
        subject_kind: ArtifactSubjectKind,
        subject_digest: str,
        project_id: str,
        predicate_type: str,
        predicate_digest: str,
        issued_at: str | None = None,
    ) -> ArtifactAttestation:
        key = self.trusted_key()
        timestamp = _canonical_timestamp(
            issued_at or datetime.now(timezone.utc).isoformat(),
            field="issued_at",
        )
        payload = _unsigned_attestation_payload(
            subject_kind=subject_kind,
            subject_digest=_require_digest(subject_digest, field="subject_digest"),
            project_id=_require_project_id(project_id),
            issuer=self.issuer,
            key_id=str(key.key_id),
            issued_at=timestamp,
            predicate_type=_require_text(predicate_type, field="predicate_type"),
            predicate_digest=_require_digest(
                predicate_digest,
                field="predicate_digest",
            ),
        )
        signature = self._private_key.sign(canonical_json_bytes(payload))
        return ArtifactAttestation(
            subject_kind=subject_kind,
            subject_digest=subject_digest,
            project_id=project_id,
            issuer=self.issuer,
            key_id=str(key.key_id),
            issued_at=timestamp,
            predicate_type=predicate_type,
            predicate_digest=predicate_digest,
            signature_b64=base64.b64encode(signature).decode("ascii"),
        ).seal()


class ArtifactTrustStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")

    def _load_unlocked(self) -> tuple[int, dict[str, TrustedArtifactKey]]:
        if not self.path.exists():
            return 0, {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ArtifactAttestationError(f"cannot read artifact trust store: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ArtifactAttestationError("artifact trust store must contain an object")
        _require_mapping(
            payload,
            schema=ARTIFACT_TRUST_STORE_SCHEMA,
            allowed={"schema", "revision", "keys"},
            required={"schema", "revision", "keys"},
            field="ArtifactTrustStore",
        )
        revision = payload.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ArtifactAttestationError("artifact trust store revision must be positive")
        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list) or any(
            not isinstance(item, Mapping) for item in raw_keys
        ):
            raise ArtifactAttestationError("artifact trust store keys must be a list of objects")
        keys: dict[str, TrustedArtifactKey] = {}
        for raw in raw_keys:
            key = TrustedArtifactKey.from_mapping(raw)
            if key.key_id in keys:
                raise ArtifactAttestationError(
                    f"artifact trust store contains duplicate key {key.key_id}"
                )
            keys[str(key.key_id)] = key
        return revision, keys

    def load(self) -> tuple[TrustedArtifactKey, ...]:
        _, keys = self._load_unlocked()
        return tuple(keys[key] for key in sorted(keys))

    def get(self, key_id: str) -> TrustedArtifactKey | None:
        token = _require_digest(key_id, field="key_id")
        _, keys = self._load_unlocked()
        return keys.get(token)

    def _write(self, revision: int, keys: Mapping[str, TrustedArtifactKey]) -> None:
        atomic_write_json(
            self.path,
            {
                "schema": ARTIFACT_TRUST_STORE_SCHEMA,
                "revision": revision,
                "keys": [keys[key].to_dict() for key in sorted(keys)],
            },
        )

    def add(self, key: TrustedArtifactKey) -> TrustedArtifactKey:
        with mutation_lock(self.lock_path):
            revision, keys = self._load_unlocked()
            existing = keys.get(str(key.key_id))
            if existing is not None:
                if existing != key:
                    raise ArtifactAttestationError(
                        "trusted key identity already exists with different policy metadata"
                    )
                return existing
            keys[str(key.key_id)] = key
            self._write(revision + 1, keys)
            return key

    def revoke(
        self,
        key_id: str,
        *,
        reason: str,
        revoked_at: str | None = None,
    ) -> TrustedArtifactKey:
        token = _require_digest(key_id, field="key_id")
        timestamp = _canonical_timestamp(
            revoked_at or datetime.now(timezone.utc).isoformat(),
            field="revoked_at",
        )
        with mutation_lock(self.lock_path):
            revision, keys = self._load_unlocked()
            current = keys.get(token)
            if current is None:
                raise ArtifactAttestationError(f"trusted key is not registered: {token}")
            if current.revoked_at is not None:
                if current.revoked_at != timestamp or current.revocation_reason != reason:
                    raise ArtifactAttestationError("trusted key is already revoked differently")
                return current
            revoked = replace(
                current,
                revoked_at=timestamp,
                revocation_reason=_require_text(reason, field="revocation_reason"),
            )
            keys[token] = revoked
            self._write(revision + 1, keys)
            return revoked


class ArtifactAttestationStore(Protocol):
    def put(self, attestation: ArtifactAttestation) -> str: ...

    def list_for_subject(
        self,
        subject_kind: ArtifactSubjectKind,
        subject_digest: str,
    ) -> tuple[ArtifactAttestation, ...]: ...


class ContentAddressedAttestationStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()

    @staticmethod
    def _digest_token(value: str, *, field: str) -> str:
        return _require_digest(value, field=field).split(":", 1)[1]

    def subject_root(
        self,
        subject_kind: ArtifactSubjectKind,
        subject_digest: str,
    ) -> Path:
        if subject_kind not in _SUPPORTED_SUBJECTS:
            raise ArtifactAttestationError("subject_kind must be package or release")
        token = self._digest_token(subject_digest, field="subject_digest")
        return self.root / subject_kind / "sha256" / token[:2] / token

    def attestation_path(self, attestation: ArtifactAttestation) -> Path:
        sealed = attestation.seal()
        token = self._digest_token(
            str(sealed.attestation_digest),
            field="attestation_digest",
        )
        return self.subject_root(sealed.subject_kind, sealed.subject_digest) / f"{token}.json"

    @staticmethod
    def _parse(data: bytes) -> ArtifactAttestation:
        if len(data) > _MAX_ATTESTATION_BYTES:
            raise ArtifactAttestationError("artifact attestation exceeds size limit")
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception as exc:
            raise ArtifactAttestationError(f"invalid artifact attestation JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ArtifactAttestationError("artifact attestation must contain an object")
        return ArtifactAttestation.from_mapping(payload)

    def put(self, attestation: ArtifactAttestation) -> str:
        sealed = attestation.seal()
        data = canonical_json_bytes(sealed.to_dict())
        target = self.attestation_path(sealed)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target.read_bytes()
            loaded = self._parse(existing)
            if loaded != sealed or existing != data:
                raise ArtifactAttestationError(
                    "immutable attestation asset already exists with different content"
                )
            return str(sealed.attestation_digest)
        atomic_write_bytes(target, data)
        return str(sealed.attestation_digest)

    def list_for_subject(
        self,
        subject_kind: ArtifactSubjectKind,
        subject_digest: str,
    ) -> tuple[ArtifactAttestation, ...]:
        root = self.subject_root(subject_kind, subject_digest)
        if not root.exists():
            return ()
        result: list[ArtifactAttestation] = []
        for path in sorted(root.glob("*.json")):
            loaded = self._parse(path.read_bytes())
            expected_name = f"{str(loaded.attestation_digest).split(':', 1)[1]}.json"
            if path.name != expected_name:
                raise ArtifactAttestationError(
                    f"attestation asset name does not match content digest: {path.name}"
                )
            if loaded.subject_kind != subject_kind or loaded.subject_digest != subject_digest:
                raise ArtifactAttestationError(
                    "attestation asset is stored under a different subject identity"
                )
            result.append(loaded)
        return tuple(result)


class ImmutableArtifactAssetClient(Protocol):
    def put_immutable_asset(
        self,
        *,
        name: str,
        data: bytes,
        media_type: str,
        digest: str,
    ) -> Mapping[str, Any]: ...

    def get_immutable_asset(self, *, name: str) -> bytes: ...

    def list_immutable_assets(self, *, prefix: str) -> Iterable[str]: ...


class ExternalImmutableAttestationStore:
    """Adapter for detached attestations stored as external immutable assets."""

    def __init__(self, client: ImmutableArtifactAssetClient, *, prefix: str = "adaos/attestations/v1") -> None:
        self.client = client
        self.prefix = _require_text(prefix, field="prefix").strip("/")

    def _subject_prefix(
        self,
        subject_kind: ArtifactSubjectKind,
        subject_digest: str,
    ) -> str:
        if subject_kind not in _SUPPORTED_SUBJECTS:
            raise ArtifactAttestationError("subject_kind must be package or release")
        token = _require_digest(subject_digest, field="subject_digest").split(":", 1)[1]
        return f"{self.prefix}/{subject_kind}/{token}/"

    def _name(self, attestation: ArtifactAttestation) -> str:
        sealed = attestation.seal()
        token = str(sealed.attestation_digest).split(":", 1)[1]
        return f"{self._subject_prefix(sealed.subject_kind, sealed.subject_digest)}{token}.json"

    def put(self, attestation: ArtifactAttestation) -> str:
        sealed = attestation.seal()
        data = canonical_json_bytes(sealed.to_dict())
        digest = sha256_digest(data)
        response = self.client.put_immutable_asset(
            name=self._name(sealed),
            data=data,
            media_type=ARTIFACT_ATTESTATION_MEDIA_TYPE,
            digest=digest,
        )
        observed = str(response.get("digest") or digest).strip().lower()
        if observed != digest:
            raise ArtifactAttestationError("external immutable asset digest mismatch")
        return str(sealed.attestation_digest)

    def list_for_subject(
        self,
        subject_kind: ArtifactSubjectKind,
        subject_digest: str,
    ) -> tuple[ArtifactAttestation, ...]:
        prefix = self._subject_prefix(subject_kind, subject_digest)
        names = tuple(sorted(set(self.client.list_immutable_assets(prefix=prefix))))
        result: list[ArtifactAttestation] = []
        for name in names:
            if not name.startswith(prefix) or not name.endswith(".json"):
                raise ArtifactAttestationError("external asset listing escaped subject prefix")
            data = self.client.get_immutable_asset(name=name)
            loaded = ContentAddressedAttestationStore._parse(data)
            if self._name(loaded) != name:
                raise ArtifactAttestationError(
                    "external attestation asset name does not match signed content"
                )
            result.append(loaded)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class ArtifactAttestationPolicy:
    required_subjects: tuple[ArtifactSubjectKind, ...] = ("package", "release")
    allowed_issuers: tuple[str, ...] = ()
    minimum_signatures: int = 1

    def __post_init__(self) -> None:
        subjects = tuple(sorted(set(self.required_subjects)))
        if any(subject not in _SUPPORTED_SUBJECTS for subject in subjects):
            raise ArtifactAttestationError("required_subjects contains an unsupported kind")
        object.__setattr__(self, "required_subjects", subjects)
        issuers = tuple(sorted({_require_text(value, field="allowed_issuer") for value in self.allowed_issuers}))
        object.__setattr__(self, "allowed_issuers", issuers)
        if self.minimum_signatures < 1 or self.minimum_signatures > 16:
            raise ArtifactAttestationError("minimum_signatures must be between 1 and 16")


def verify_artifact_attestation(
    attestation: ArtifactAttestation,
    *,
    trust_store: ArtifactTrustStore,
    expected_subject_kind: ArtifactSubjectKind,
    expected_subject_digest: str,
    expected_project_id: str,
    expected_predicate_type: str,
    allowed_issuers: Iterable[str] = (),
) -> dict[str, Any]:
    sealed = attestation.seal()
    expected_digest = _require_digest(expected_subject_digest, field="expected_subject_digest")
    expected_project = _require_project_id(expected_project_id)
    if sealed.subject_kind != expected_subject_kind or sealed.subject_digest != expected_digest:
        raise ArtifactAttestationVerificationError("attestation subject does not match requested artifact")
    if sealed.project_id != expected_project:
        raise ArtifactAttestationVerificationError("attestation project does not match requested release")
    if sealed.predicate_type != expected_predicate_type:
        raise ArtifactAttestationVerificationError("attestation predicate type is not admitted")
    key = trust_store.get(sealed.key_id)
    if key is None:
        raise ArtifactAttestationVerificationError("attestation signing key is not trusted")
    if key.issuer != sealed.issuer:
        raise ArtifactAttestationVerificationError("attestation issuer does not match trusted key")
    issuer_allowlist = {str(value).strip() for value in allowed_issuers if str(value).strip()}
    if issuer_allowlist and sealed.issuer not in issuer_allowlist:
        raise ArtifactAttestationVerificationError("attestation issuer is not allowed by policy")
    if sealed.subject_kind not in key.purposes:
        raise ArtifactAttestationVerificationError("trusted key is not admitted for this subject kind")
    if key.revoked_at is not None:
        raise ArtifactAttestationVerificationError("attestation signing key is revoked")
    issued = _timestamp(sealed.issued_at)
    if key.not_before and issued < _timestamp(key.not_before):
        raise ArtifactAttestationVerificationError("attestation predates signing-key validity")
    if key.not_after and issued > _timestamp(key.not_after):
        raise ArtifactAttestationVerificationError("attestation postdates signing-key validity")
    _, signature = _decode_b64(
        sealed.signature_b64,
        field="signature_b64",
        expected_bytes=64,
    )
    try:
        key.public_key.verify(signature, canonical_json_bytes(sealed.unsigned_dict()))
    except InvalidSignature as exc:
        raise ArtifactAttestationVerificationError("artifact attestation signature is invalid") from exc
    return {
        "attestation_digest": sealed.attestation_digest,
        "subject_kind": sealed.subject_kind,
        "subject_digest": sealed.subject_digest,
        "project_id": sealed.project_id,
        "issuer": sealed.issuer,
        "key_id": sealed.key_id,
        "issued_at": sealed.issued_at,
        "predicate_type": sealed.predicate_type,
        "predicate_digest": sealed.predicate_digest,
    }


class ArtifactAttestationAdmission:
    def __init__(
        self,
        *,
        store: ArtifactAttestationStore,
        trust_store: ArtifactTrustStore,
        policy: ArtifactAttestationPolicy | None = None,
    ) -> None:
        self.store = store
        self.trust_store = trust_store
        self.policy = policy or ArtifactAttestationPolicy()

    def policy_summary(self) -> dict[str, Any]:
        return {
            "schema": "adaos.artifact.attestation_policy.v1",
            "required_subjects": list(self.policy.required_subjects),
            "allowed_issuers": list(self.policy.allowed_issuers),
            "minimum_signatures": self.policy.minimum_signatures,
        }

    def verify_subject(
        self,
        *,
        subject_kind: ArtifactSubjectKind,
        subject_digest: str,
        project_id: str,
        predicate_type: str,
    ) -> dict[str, Any]:
        if subject_kind not in self.policy.required_subjects:
            return {
                "status": "skipped",
                "subject_kind": subject_kind,
                "subject_digest": subject_digest,
                "reason": "subject_not_required_by_policy",
            }
        attestations = self.store.list_for_subject(subject_kind, subject_digest)
        valid: list[dict[str, Any]] = []
        rejected: list[str] = []
        seen_keys: set[str] = set()
        for attestation in attestations:
            try:
                receipt = verify_artifact_attestation(
                    attestation,
                    trust_store=self.trust_store,
                    expected_subject_kind=subject_kind,
                    expected_subject_digest=subject_digest,
                    expected_project_id=project_id,
                    expected_predicate_type=predicate_type,
                    allowed_issuers=self.policy.allowed_issuers,
                )
            except ArtifactAttestationVerificationError as exc:
                rejected.append(str(exc))
                continue
            if str(receipt["key_id"]) in seen_keys:
                continue
            seen_keys.add(str(receipt["key_id"]))
            valid.append(receipt)
        if len(valid) < self.policy.minimum_signatures:
            details = "; ".join(sorted(set(rejected))) or "no attestations found"
            raise ArtifactAttestationVerificationError(
                f"artifact attestation policy requires {self.policy.minimum_signatures} "
                f"valid signature(s) for {subject_kind}:{subject_digest}; {details}"
            )
        return {
            "status": "verified",
            "subject_kind": subject_kind,
            "subject_digest": subject_digest,
            "valid_signatures": valid,
            "rejected_total": len(rejected),
        }

    def verify_release_plan(self, plan: ReleasePlan) -> dict[str, Any]:
        release_digest = plan.release.release_digest or plan.release.computed_digest()
        receipts = [
            self.verify_subject(
                subject_kind="release",
                subject_digest=release_digest,
                project_id=plan.release.project_id,
                predicate_type=RELEASE_PROVENANCE_PREDICATE,
            )
        ]
        receipts.extend(
            self.verify_subject(
                subject_kind="package",
                subject_digest=package.digest,
                project_id=plan.release.project_id,
                predicate_type=PACKAGE_PROVENANCE_PREDICATE,
            )
            for package in sorted(plan.packages, key=lambda value: value.key)
        )
        return {
            "schema": "adaos.artifact.attestation_admission.v1",
            "status": "verified",
            "policy": self.policy_summary(),
            "subjects": receipts,
        }


__all__ = [
    "ARTIFACT_ATTESTATION_MEDIA_TYPE",
    "ARTIFACT_ATTESTATION_SCHEMA",
    "ARTIFACT_TRUST_KEY_SCHEMA",
    "ARTIFACT_TRUST_STORE_SCHEMA",
    "PACKAGE_PROVENANCE_PREDICATE",
    "RELEASE_PROVENANCE_PREDICATE",
    "ArtifactAttestation",
    "ArtifactAttestationAdmission",
    "ArtifactAttestationError",
    "ArtifactAttestationPolicy",
    "ArtifactAttestationStore",
    "ArtifactAttestationVerificationError",
    "ArtifactTrustStore",
    "ContentAddressedAttestationStore",
    "Ed25519ArtifactSigner",
    "ExternalImmutableAttestationStore",
    "ImmutableArtifactAssetClient",
    "TrustedArtifactKey",
    "verify_artifact_attestation",
]
