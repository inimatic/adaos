"""Versioned health and protocol negotiation for capability providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Mapping


_HEALTH = frozenset({"healthy", "degraded", "unavailable"})


class ProviderProtocolError(RuntimeError):
    """Raised when a provider cannot satisfy a required protocol version."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _version(value: Any) -> tuple[int, int]:
    token = str(value or "").strip()
    parts = token.split(".")
    if len(parts) != 2 or any(not item.isdigit() for item in parts):
        raise ValueError("protocol_version must use <major>.<minor>")
    return int(parts[0]), int(parts[1])


def protocol_compatible(supported: str, required: str) -> bool:
    supported_major, supported_minor = _version(supported)
    required_major, required_minor = _version(required)
    return supported_major == required_major and supported_minor >= required_minor


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """Redacted provider status suitable for projections and diagnostics."""

    SCHEMA: ClassVar[str] = "adaos.provider.status.v1"

    capability: str
    provider_id: str
    protocol_version: str
    health: str
    features: tuple[str, ...] = ()
    checked_at: str = field(default_factory=_now)
    reason_code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        capability = str(self.capability or "").strip().lower()
        provider_id = str(self.provider_id or "").strip().lower()
        health = str(self.health or "").strip().lower()
        if not capability or not provider_id:
            raise ValueError("capability and provider_id are required")
        _version(self.protocol_version)
        if health not in _HEALTH:
            raise ValueError(f"unsupported provider health: {health}")
        features = tuple(
            dict.fromkeys(str(item).strip().lower() for item in self.features if str(item).strip())
        )
        details = dict(self.details)
        encoded = repr(details).lower()
        if any(token in encoded for token in ("password", "token=", "://", "dsn")):
            raise ValueError("provider status details must not contain credentials or DSNs")
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "health", health)
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "details", details)

    def require_protocol(self, required: str) -> None:
        if not protocol_compatible(self.protocol_version, required):
            raise ProviderProtocolError(
                f"{self.capability} provider {self.provider_id!r} protocol "
                f"{self.protocol_version} cannot satisfy {required}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.SCHEMA, **asdict(self), "features": list(self.features)}


__all__ = [
    "ProviderProtocolError",
    "ProviderStatus",
    "protocol_compatible",
]
