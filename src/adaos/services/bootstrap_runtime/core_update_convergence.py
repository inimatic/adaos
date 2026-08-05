from __future__ import annotations

import asyncio
import hashlib
import json as _json
import time
from typing import Any, Callable


def _core_update_status_fingerprint(status: Any) -> str:
    payload = status if isinstance(status, dict) else {}
    try:
        encoded = _json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        encoded = repr(payload)
    return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()


def _core_update_waits_for_supervisor_convergence(status: Any) -> bool:
    payload = status if isinstance(status, dict) else {}
    state = str(payload.get("state") or "").strip().lower()
    phase = str(payload.get("phase") or "").strip().lower()
    # A warm candidate boots while the shared transition can still be in
    # prepare/countdown.  The same process is promoted without another
    # bootstrap pass, so arming this bridge only at ``root_promoted`` races
    # with fast cutover and can lose the terminal validate event.  Follow the
    # whole bounded transition from candidate boot until a terminal state; the
    # supervisor remains the sole writer/authority for the status file.
    if state in {
        "preparing",
        "countdown",
        "draining",
        "stopping",
        "restarting",
        "applying",
        "validated",
    }:
        return True
    return state == "succeeded" and phase in {
        "apply",
        "launch",
        "shutdown",
        "root_promoted",
        "root_promotion_pending",
    }


async def _watch_supervisor_core_update_convergence(
    bus: Any,
    *,
    read_status: Callable[[], dict[str, Any]],
    initial_status: dict[str, Any],
    poll_interval_s: float = 0.5,
    timeout_s: float = 300.0,
) -> dict[str, Any]:
    last_status = dict(initial_status or {})
    last_fingerprint = _core_update_status_fingerprint(last_status)
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    emitted_total = 0
    while _core_update_waits_for_supervisor_convergence(last_status) and time.monotonic() < deadline:
        await asyncio.sleep(max(0.05, float(poll_interval_s)))
        try:
            current = read_status()
        except Exception:
            continue
        current = dict(current) if isinstance(current, dict) else {}
        fingerprint = _core_update_status_fingerprint(current)
        if fingerprint == last_fingerprint:
            continue
        last_status = current
        last_fingerprint = fingerprint
        await bus.emit(
            "core.update.status",
            current,
            source="supervisor.convergence",
            actor="system",
        )
        emitted_total += 1
    return {
        "ok": not _core_update_waits_for_supervisor_convergence(last_status),
        "emitted_total": emitted_total,
        "timed_out": _core_update_waits_for_supervisor_convergence(last_status),
        "status": last_status,
    }
