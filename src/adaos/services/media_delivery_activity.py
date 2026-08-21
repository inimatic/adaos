from __future__ import annotations

import threading
import time
import uuid
from collections import Counter
from typing import Any

MEDIA_DELIVERY_ACTIVITY_SCHEMA = "adaos.media.delivery_activity.v1"
_LEASE_TTL_SECONDS = 120.0
_MAX_ACTIVE_LEASES = 2048
_LOCK = threading.RLock()
_ACTIVE: dict[str, dict[str, Any]] = {}
_SATURATED_UNTIL = 0.0
_LAST_ACTIVITY_AT = 0.0


def _media_kind(media_type: str | None) -> str:
    token = str(media_type or "").strip().lower()
    if token.startswith("audio/"):
        return "audio"
    if token.startswith("video/"):
        return "video"
    return "other"


def _prune(now: float) -> None:
    expired = [
        lease_id
        for lease_id, lease in _ACTIVE.items()
        if now - float(lease.get("last_activity_at") or 0.0) > _LEASE_TTL_SECONDS
    ]
    for lease_id in expired:
        _ACTIVE.pop(lease_id, None)


def begin_media_delivery(*, media_type: str | None, now: float | None = None) -> str:
    global _LAST_ACTIVITY_AT, _SATURATED_UNTIL
    current = time.time() if now is None else float(now)
    with _LOCK:
        _prune(current)
        _LAST_ACTIVITY_AT = current
        if len(_ACTIVE) >= _MAX_ACTIVE_LEASES:
            _SATURATED_UNTIL = max(_SATURATED_UNTIL, current + _LEASE_TTL_SECONDS)
            return ""
        lease_id = uuid.uuid4().hex
        _ACTIVE[lease_id] = {
            "kind": _media_kind(media_type),
            "started_at": current,
            "last_activity_at": current,
        }
        return lease_id


def touch_media_delivery(lease_id: str, *, now: float | None = None) -> None:
    global _LAST_ACTIVITY_AT, _SATURATED_UNTIL
    current = time.time() if now is None else float(now)
    token = str(lease_id or "").strip()
    with _LOCK:
        _LAST_ACTIVITY_AT = current
        if not token:
            _SATURATED_UNTIL = max(_SATURATED_UNTIL, current + _LEASE_TTL_SECONDS)
            return
        lease = _ACTIVE.get(token)
        if lease is not None:
            lease["last_activity_at"] = current


def end_media_delivery(lease_id: str, *, now: float | None = None) -> None:
    global _LAST_ACTIVITY_AT
    current = time.time() if now is None else float(now)
    token = str(lease_id or "").strip()
    with _LOCK:
        if token:
            _ACTIVE.pop(token, None)
        _LAST_ACTIVITY_AT = current


def media_delivery_activity_snapshot(*, now: float | None = None) -> dict[str, Any]:
    current = time.time() if now is None else float(now)
    with _LOCK:
        _prune(current)
        saturated = _SATURATED_UNTIL > current
        kinds = Counter(str(lease.get("kind") or "other") for lease in _ACTIVE.values())
        active_streams = len(_ACTIVE) + (1 if saturated else 0)
        return {
            "schema": MEDIA_DELIVERY_ACTIVITY_SCHEMA,
            "active": active_streams > 0,
            "active_streams": active_streams,
            "tracked_streams": len(_ACTIVE),
            "kind_counts": {
                "audio": int(kinds.get("audio") or 0),
                "video": int(kinds.get("video") or 0),
                "other": int(kinds.get("other") or 0),
            },
            "saturated": saturated,
            "lease_ttl_seconds": _LEASE_TTL_SECONDS,
            "last_activity_at": _LAST_ACTIVITY_AT or None,
        }


def reset_media_delivery_activity_for_tests() -> None:
    global _LAST_ACTIVITY_AT, _SATURATED_UNTIL
    with _LOCK:
        _ACTIVE.clear()
        _LAST_ACTIVITY_AT = 0.0
        _SATURATED_UNTIL = 0.0


__all__ = [
    "MEDIA_DELIVERY_ACTIVITY_SCHEMA",
    "begin_media_delivery",
    "end_media_delivery",
    "media_delivery_activity_snapshot",
    "reset_media_delivery_activity_for_tests",
    "touch_media_delivery",
]
