from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


async def _run_bounded_async_cleanup(
    operation: Callable[[], Awaitable[Any]],
    *,
    timeout_s: float = 1.0,
) -> bool:
    """Run best-effort transport cleanup without trapping the reconnect supervisor."""
    try:
        await asyncio.wait_for(operation(), timeout=max(0.01, float(timeout_s)))
        return True
    except asyncio.CancelledError:
        # A transport close coroutine can surface its *own* cancellation after
        # an EOF.  That must not be mistaken for cancellation of the bridge
        # supervisor itself, otherwise one broken session permanently removes
        # the reconnect loop.  Preserve only cancellation requested for the
        # current task by its owner (shutdown, cutover, or explicit rearm).
        current = asyncio.current_task()
        cancelling = getattr(current, "cancelling", None) if current is not None else None
        if callable(cancelling) and int(cancelling() or 0) > 0:
            raise
        return False
    except Exception:
        return False


async def _close_route_tunnels_bounded(
    tunnels: dict[str, dict[str, Any]],
    *,
    timeout_s: float = 1.0,
) -> dict[str, int]:
    """Close all live route tunnels concurrently without blocking reconnect."""
    records = list(tunnels.items())

    async def _close_one(record: dict[str, Any]) -> bool:
        ws = record.get("ws") if isinstance(record, dict) else None
        close = getattr(ws, "close", None)
        if not callable(close):
            return True
        return await _run_bounded_async_cleanup(close, timeout_s=timeout_s)

    results: list[bool] = []
    try:
        if records:
            results = list(
                await asyncio.gather(
                    *(_close_one(record) for _, record in records),
                    return_exceptions=False,
                )
            )
    finally:
        for key, _ in records:
            tunnels.pop(key, None)
    completed = sum(1 for value in results if value is True)
    return {
        "attempted": len(records),
        "completed": completed,
        "failed_or_timed_out": max(0, len(records) - completed),
    }


def _current_async_task_is_cancelling() -> bool:
    current = asyncio.current_task()
    if current is None:
        return False
    cancelling = getattr(current, "cancelling", None)
    if not callable(cancelling):
        return bool(current.cancelled())
    try:
        return int(cancelling() or 0) > 0
    except Exception:
        return bool(current.cancelled())
