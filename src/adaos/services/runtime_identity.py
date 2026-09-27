from __future__ import annotations

import os
import socket
import time
import uuid
from typing import Any

_RUNTIME_INSTANCE_ID: str | None = None
_RUNTIME_STARTED_AT = time.time()


def _normalize_transition_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    if role in {"active", "candidate"}:
        return role
    return "active"


def runtime_transition_role() -> str:
    return _normalize_transition_role(os.getenv("ADAOS_RUNTIME_TRANSITION_ROLE"))


def runtime_shared_state_write_authorized() -> bool:
    """Return whether this process may mutate shared runtime state.

    A candidate briefly adopts the ``active`` transition role while proving
    its upstream authority. Shared-state writers must remain fenced during
    that interval: the role alone is not proof that promotion committed.
    Ordinary active boots do not need an explicit marker and remain backward
    compatible.
    """

    if runtime_transition_role() != "active":
        return False
    marker = os.getenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED")
    if marker is None:
        return True
    return str(marker).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def runtime_instance_id() -> str:
    global _RUNTIME_INSTANCE_ID
    env_value = str(os.getenv("ADAOS_RUNTIME_INSTANCE_ID") or "").strip()
    if env_value:
        _RUNTIME_INSTANCE_ID = env_value
        return env_value
    if _RUNTIME_INSTANCE_ID:
        return _RUNTIME_INSTANCE_ID
    role = runtime_transition_role()
    _RUNTIME_INSTANCE_ID = f"rt-{role[:1]}-{uuid.uuid4().hex[:10]}"
    os.environ.setdefault("ADAOS_RUNTIME_INSTANCE_ID", _RUNTIME_INSTANCE_ID)
    return _RUNTIME_INSTANCE_ID


def runtime_instance_short_id(length: int = 10) -> str:
    token = runtime_instance_id()
    size = max(4, int(length))
    return token[-size:]


def runtime_connect_name(*, prefix: str) -> str:
    name_prefix = str(prefix or "runtime").strip() or "runtime"
    return f"{name_prefix}-{runtime_transition_role()}-{runtime_instance_short_id(8)}"


def runtime_identity_snapshot() -> dict[str, Any]:
    return {
        "runtime_instance_id": runtime_instance_id(),
        "transition_role": runtime_transition_role(),
        "shared_state_write_authorized": runtime_shared_state_write_authorized(),
        "hostname": socket.gethostname(),
        "started_at": _RUNTIME_STARTED_AT,
    }


__all__ = [
    "runtime_connect_name",
    "runtime_identity_snapshot",
    "runtime_instance_id",
    "runtime_instance_short_id",
    "runtime_shared_state_write_authorized",
    "runtime_transition_role",
]
