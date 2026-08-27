from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


DEVICE_PRESENCE_CHANGED = "device.presence.changed"
DEFAULT_GRACE_SECONDS = 60


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) if float(value) > 0 else None
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def project_device_presence(
    device: Mapping[str, Any] | None,
    *,
    now: float | None = None,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
) -> dict[str, Any]:
    record = dict(device or {})
    observation = record.get("observation")
    observation = dict(observation) if isinstance(observation, Mapping) else {}
    current_time = float(now) if now is not None else datetime.now(tz=timezone.utc).timestamp()
    last_seen_at = _timestamp(observation.get("last_seen_at"))
    age_seconds = max(0.0, current_time - last_seen_at) if last_seen_at is not None else None
    reported_online = bool(observation.get("online"))
    grace = max(0, int(grace_seconds or 0))

    if reported_online:
        state = "online"
        available = True
    elif age_seconds is not None and age_seconds <= grace:
        state = "grace"
        available = True
    else:
        state = "offline"
        available = False

    return {
        "schema": "adaos.device_presence.v1",
        "device_ref": str(record.get("ref") or "").strip(),
        "state": state,
        "available": available,
        "reported_online": reported_online,
        "connection_state": str(observation.get("connection_state") or "").strip() or None,
        "last_seen_at": observation.get("last_seen_at"),
        "age_seconds": age_seconds,
        "grace_seconds": grace,
    }


__all__ = [
    "DEFAULT_GRACE_SECONDS",
    "DEVICE_PRESENCE_CHANGED",
    "project_device_presence",
]
