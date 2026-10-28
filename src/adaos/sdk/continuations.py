"""Bounded durable reconciliation of the current admitted tool invocation."""
from __future__ import annotations
from typing import Any

__all__ = ["defer_current", "complete", "get", "is_replay"]


def is_replay() -> bool:
    """Whether Core is executing this invocation as its admitted durable replay.

    The value comes from Core execution context, never request arguments. A
    long-running tool can persist its snapshot, register defer_current and
    return a queued acknowledgement immediately when this is false. Expensive
    reconciliation then runs only on replay, with the same admission checks,
    per-tool timeout and heartbeat lease. This observation grants no authority.
    """
    from adaos.services.tool_continuations import is_replay as current
    return current()


def defer_current(*, key: str, retry_after_s: float = 3, ttl_s: float = 3600) -> dict[str, Any]:
    """Durably repeat this exact admitted invocation until complete/blocked/expiry.

    Requires a mutating HTTP invocation with verified caller, stable request and
    idempotency identities, and tools.<name>.durable_replay=true in the resolved
    skill manifest. Core retains the exact tool, arguments, caller, webspace and
    runtime source. It rechecks caller rights and durable approvals on every step;
    no credential or one-time action approval is replayed. No other tool/arguments
    can be selected. Register AFTER atomic run/input snapshot persistence and
    BEFORE paid submission. The same replay must load that snapshot/receipt,
    reconcile existing provider jobs, and never recapture current mutable inputs.
    Delivery is at least once, including after process restart; paid effects need
    durable idempotency keys and outcome-unknown reconciliation. Exceptions block
    automatic delivery. Call complete for terminal runs (including QA rejection).
    This service drives work without an open page. A read-only UI state probe may
    observe progress; it must never submit/advance paid work. TTL is 10..7200
    seconds by default. A durable tool may explicitly declare
    continuation_max_ttl_seconds, at most 86400, in its pinned skill manifest;
    requesting a longer lifetime never grants additional caller permissions.
    TTL cannot extend on
    repeated registration. Reusing key for another invocation/input is rejected.
    """
    from adaos.services.tool_continuations import defer_current as schedule
    return schedule(key=key, retry_after_s=retry_after_s, ttl_s=ttl_s)


def complete(key: str, *, outcome: str = "completed") -> dict[str, Any] | None:
    """Set current caller/skill's continuation terminal: completed/blocked/cancelled."""
    from adaos.services.tool_continuations import current_record
    return current_record(key, outcome=outcome)


def get(key: str) -> dict[str, Any] | None:
    """Read current caller/skill's durable continuation receipt without advancing it."""
    from adaos.services.tool_continuations import current_record
    return current_record(key)
