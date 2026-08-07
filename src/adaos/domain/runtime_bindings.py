"""Small provider-neutral references shared by future runtime capabilities."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Mapping
from urllib.parse import urlsplit

from .ownership import validate_owner_ref


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SECRET_REF_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[a-z0-9_./-]+$")


class RuntimeBindingContractError(ValueError):
    """Raised when a content or service binding violates its ABI."""


def _token(value: Any, field_name: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise RuntimeBindingContractError(f"{field_name} must be non-empty")
    return token


def _id_token(value: Any, field_name: str) -> str:
    token = _token(value, field_name).lower()
    if not _TOKEN_RE.fullmatch(token):
        raise RuntimeBindingContractError(f"{field_name} has an invalid identifier")
    return token


def _secret_ref(value: Any) -> str | None:
    secret_ref = str(value or "").strip() or None
    if secret_ref is None:
        return None
    lowered = secret_ref.lower()
    if (
        any(token in lowered for token in ("://", "@", "password=", "token="))
        or not _SECRET_REF_RE.fullmatch(secret_ref)
    ):
        raise RuntimeBindingContractError(
            "secret_ref must be an opaque reference, not inline credentials"
        )
    return secret_ref


@dataclass(frozen=True, slots=True)
class ContentRef:
    """Portable content identity; it is not a release ``ArtifactKind``."""

    SCHEMA: ClassVar[str] = "adaos.content.ref.v1"

    uri: str
    digest: str
    size_bytes: int
    media_type: str
    owner_ref: str
    kind: str = "artifact"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        uri = _token(self.uri, "uri")
        digest = str(self.digest or "").strip().lower()
        if not _DIGEST_RE.fullmatch(digest):
            raise RuntimeBindingContractError("digest must be sha256:<64 lowercase hex>")
        try:
            size_bytes = int(self.size_bytes)
        except (TypeError, ValueError) as exc:
            raise RuntimeBindingContractError("size_bytes must be an integer") from exc
        if size_bytes < 0:
            raise RuntimeBindingContractError("size_bytes must be >= 0")
        media_type = _token(self.media_type, "media_type").lower()
        owner_ref = validate_owner_ref(self.owner_ref)
        kind = _id_token(self.kind, "kind")
        object.__setattr__(self, "uri", uri)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "size_bytes", size_bytes)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "owner_ref", owner_ref)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.SCHEMA, **asdict(self)}


@dataclass(frozen=True, slots=True)
class ServiceBinding:
    """Redacted discovery result for a supervised or external service."""

    SCHEMA: ClassVar[str] = "adaos.service.binding.v1"

    binding_id: str
    capability: str
    provider_ref: str
    consumer_ref: str
    endpoint: str
    protocol: str
    protocol_version: str
    health_endpoint: str | None = None
    ui_endpoint: str | None = None
    secret_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        binding_id = _token(self.binding_id, "binding_id")
        capability = _id_token(self.capability, "capability")
        provider_ref = validate_owner_ref(self.provider_ref)
        consumer_ref = validate_owner_ref(self.consumer_ref)
        endpoint = self._endpoint(self.endpoint, "endpoint")
        protocol = _id_token(self.protocol, "protocol")
        protocol_version = _token(self.protocol_version, "protocol_version")
        health = self._endpoint(self.health_endpoint, "health_endpoint") if self.health_endpoint else None
        ui = self._endpoint(self.ui_endpoint, "ui_endpoint") if self.ui_endpoint else None
        secret_ref = _secret_ref(self.secret_ref)
        object.__setattr__(self, "binding_id", binding_id)
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "provider_ref", provider_ref)
        object.__setattr__(self, "consumer_ref", consumer_ref)
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "protocol", protocol)
        object.__setattr__(self, "protocol_version", protocol_version)
        object.__setattr__(self, "health_endpoint", health)
        object.__setattr__(self, "ui_endpoint", ui)
        object.__setattr__(self, "secret_ref", secret_ref)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @staticmethod
    def _endpoint(value: Any, field_name: str) -> str:
        endpoint = _token(value, field_name)
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https", "unix", "npipe"}:
            raise RuntimeBindingContractError(
                f"{field_name} must use http, https, unix, or npipe"
            )
        if parsed.username or parsed.password:
            raise RuntimeBindingContractError(f"{field_name} must not contain credentials")
        if parsed.query or parsed.fragment:
            raise RuntimeBindingContractError(f"{field_name} must not contain query or fragment data")
        return endpoint

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.SCHEMA, **asdict(self)}


__all__ = ["ContentRef", "RuntimeBindingContractError", "ServiceBinding"]
