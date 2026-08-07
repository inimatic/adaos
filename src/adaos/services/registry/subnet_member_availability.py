from __future__ import annotations

import os
import time
from typing import Any


_DEFAULT_DORMANT_AFTER_S = 7.0 * 24.0 * 60.0 * 60.0


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    return value if value > 0.0 else default


def subnet_member_availability_policy() -> dict[str, float]:
    return {
        "dormant_after_s": _env_float(
            "ADAOS_MEMBER_AVAILABILITY_DORMANT_AFTER_S",
            _DEFAULT_DORMANT_AFTER_S,
        ),
    }


def subnet_member_availability_scope(
    *,
    connected: bool,
    online: bool,
    last_seen_at: Any,
    now: float | None = None,
) -> dict[str, Any]:
    now_value = time.time() if now is None else float(now)
    try:
        last_seen_value = float(last_seen_at) if last_seen_at is not None else 0.0
    except Exception:
        last_seen_value = 0.0
    age_s = round(max(0.0, now_value - last_seen_value), 3) if last_seen_value > 0.0 else None
    dormant_after_s = float(subnet_member_availability_policy()["dormant_after_s"])

    if connected:
        scope = "active"
        reason = "member_link_connected"
    elif online:
        scope = "active"
        reason = "directory_online"
    elif age_s is None:
        scope = "active"
        reason = "last_seen_unknown"
    elif age_s < dormant_after_s:
        scope = "active"
        reason = "recently_offline"
    else:
        scope = "dormant"
        reason = "offline_retention"

    return {
        "scope": scope,
        "reason": reason,
        "last_seen_ago_s": age_s,
        "dormant_after_s": dormant_after_s,
    }


__all__ = [
    "subnet_member_availability_policy",
    "subnet_member_availability_scope",
]
