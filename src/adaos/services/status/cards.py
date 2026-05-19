from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from adaos.services.system_model.model import CanonicalStatus, normalize_operational_status


_SEVERITY_BY_STATUS = {
    CanonicalStatus.ONLINE: "info",
    CanonicalStatus.WARNING: "warning",
    CanonicalStatus.DEGRADED: "high",
    CanonicalStatus.OFFLINE: "critical",
    CanonicalStatus.UNKNOWN: "unknown",
}


def _text(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _json_clean(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            cleaned = _json_clean(raw_value)
            if cleaned is not None and cleaned != {} and cleaned != []:
                result[key] = cleaned
        return result
    if isinstance(value, (list, tuple)):
        return [item for item in (_json_clean(item) for item in value) if item is not None]
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        try:
            return _json_clean(to_json())
        except Exception:
            return str(value)
    return str(value)


def _mapping(value: Any) -> dict[str, Any]:
    cleaned = _json_clean(value)
    return dict(cleaned) if isinstance(cleaned, dict) else {}


def _status(value: Any) -> CanonicalStatus:
    if isinstance(value, CanonicalStatus):
        return value
    return normalize_operational_status(value)


def _severity(value: Any, *, status: CanonicalStatus) -> str:
    token = str(value or "").strip().lower()
    if token in {"info", "low", "medium", "warning", "warn", "high", "critical", "unknown"}:
        return "warning" if token == "warn" else token
    return _SEVERITY_BY_STATUS.get(status, "unknown")


def status_card_fingerprint(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    for key in (
        "schema",
        "version",
        "fingerprint",
        "updated_at",
        "changed_at",
        "age_s",
        "_age_s",
        "_ago_s",
        "stale",
        "expires_at",
    ):
        body.pop(key, None)
    raw = json.dumps(_json_clean(body), ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class StatusCard:
    id: str
    owner: str
    kind: str
    scope: str
    status: CanonicalStatus = CanonicalStatus.UNKNOWN
    summary: str | None = None
    severity: str | None = None
    webspace_id: str | None = None
    updated_at: float = field(default_factory=time.time)
    ttl_ms: int | None = None
    incident_id: str | None = None
    version: int = 1
    fingerprint: str | None = None
    changed_at: float | None = None
    details_ref: dict[str, Any] = field(default_factory=dict)
    route: dict[str, Any] = field(default_factory=dict)
    guard_ref: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = _text(self.id) or "unknown"
        self.owner = _text(self.owner) or "unknown"
        self.kind = _text(self.kind) or "status"
        self.scope = _text(self.scope) or "runtime"
        self.webspace_id = _text(self.webspace_id)
        self.status = _status(self.status)
        self.severity = _severity(self.severity, status=self.status)
        self.summary = _text(self.summary)
        self.ttl_ms = _positive_int(self.ttl_ms)
        self.incident_id = _text(self.incident_id)
        self.version = max(1, int(self.version or 1))
        self.updated_at = float(self.updated_at or time.time())
        self.changed_at = float(self.changed_at if self.changed_at is not None else self.updated_at)
        self.details_ref = _mapping(self.details_ref)
        self.route = _mapping(self.route)
        self.guard_ref = _mapping(self.guard_ref)
        self.metadata = _mapping(self.metadata)
        self.fingerprint = status_card_fingerprint(self._fingerprint_payload())

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner": self.owner,
            "kind": self.kind,
            "scope": self.scope,
            "webspace_id": self.webspace_id,
            "status": self.status.value,
            "summary": self.summary,
            "severity": self.severity,
            "ttl_ms": self.ttl_ms,
            "incident_id": self.incident_id,
            "details_ref": self.details_ref,
            "route": self.route,
            "guard_ref": self.guard_ref,
            "metadata": self.metadata,
        }

    def is_stale(self, *, now_ts: float | None = None) -> bool:
        if not self.ttl_ms:
            return False
        now = float(now_ts if now_ts is not None else time.time())
        return now > self.updated_at + (self.ttl_ms / 1000.0)

    def to_dict(self, *, now_ts: float | None = None) -> dict[str, Any]:
        now = float(now_ts if now_ts is not None else time.time())
        expires_at = self.updated_at + (self.ttl_ms / 1000.0) if self.ttl_ms else None
        payload: dict[str, Any] = {
            "schema": "adaos.status_card.v1",
            "id": self.id,
            "owner": self.owner,
            "kind": self.kind,
            "scope": self.scope,
            "status": self.status.value,
            "summary": self.summary,
            "severity": self.severity,
            "webspace_id": self.webspace_id,
            "updated_at": self.updated_at,
            "ttl_ms": self.ttl_ms,
            "incident_id": self.incident_id,
            "version": self.version,
            "fingerprint": self.fingerprint,
            "changed_at": self.changed_at,
            "details_ref": self.details_ref,
            "route": self.route,
            "guard_ref": self.guard_ref,
            "metadata": self.metadata,
            "stale": self.is_stale(now_ts=now),
            "expires_at": expires_at,
            "age_s": max(0.0, now - self.updated_at),
        }
        return {key: value for key, value in payload.items() if value is not None and value != {} and value != []}

    def with_registry_state(
        self,
        *,
        version: int,
        fingerprint: str | None = None,
        changed_at: float | None = None,
    ) -> "StatusCard":
        return replace(
            self,
            version=max(1, int(version or 1)),
            fingerprint=fingerprint or self.fingerprint,
            changed_at=float(changed_at if changed_at is not None else self.changed_at or self.updated_at),
        )


def make_status_card(**kwargs: Any) -> StatusCard:
    return StatusCard(**kwargs)


def normalize_status_card(value: StatusCard | Mapping[str, Any], **defaults: Any) -> StatusCard:
    if isinstance(value, StatusCard):
        data = value.to_dict(now_ts=value.updated_at)
    elif isinstance(value, Mapping):
        data = dict(value)
    else:
        raise TypeError("status card must be a StatusCard or mapping")
    for key in ("schema", "stale", "age_s", "expires_at"):
        data.pop(key, None)
    for key, default in defaults.items():
        if data.get(key) in (None, "", {}):
            data[key] = default
    return StatusCard(**data)


__all__ = [
    "StatusCard",
    "make_status_card",
    "normalize_status_card",
    "status_card_fingerprint",
]
