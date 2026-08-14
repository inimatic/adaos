from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from typing import Any, Callable


_LOG = logging.getLogger("adaos.skill.subscription_execution")
_LOCK = RLock()
_EXECUTOR: ThreadPoolExecutor | None = None
_ACTIVE: dict[str, dict[str, Any]] = {}
_PENDING_BY_HANDLER: dict[str, int] = defaultdict(int)
_STATS: dict[str, dict[str, Any]] = {}
_LAST_OVERLOAD_LOG_AT: dict[str, float] = {}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    with _LOCK:
        if _EXECUTOR is None:
            workers = _env_int("ADAOS_SKILL_SUBSCRIPTION_WORKERS", 4, minimum=1, maximum=32)
            _EXECUTOR = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="adaos-skill-sub")
        return _EXECUTOR


def _handler_key(skill: str, topic: str, handler: str) -> str:
    return f"{skill or '<unknown>'}\0{topic or '<unknown>'}\0{handler or '<unknown>'}"


def _stats_row(key: str, *, skill: str, topic: str, handler: str) -> dict[str, Any]:
    row = _STATS.get(key)
    if row is None:
        row = {
            "skill": skill or "<unknown>",
            "topic": topic or "<unknown>",
            "handler": handler or "<unknown>",
            "submitted_total": 0,
            "completed_total": 0,
            "failed_total": 0,
            "overload_total": 0,
            "blocking_total": 0,
            "max_duration_s": 0.0,
            "last_duration_s": 0.0,
            "last_started_at": None,
            "last_finished_at": None,
            "last_error": None,
        }
        _STATS[key] = row
    return row


def _record_pressure(
    *,
    skill: str,
    topic: str,
    handler: str,
    signal: str,
    duration_s: float | None = None,
    pending: int | None = None,
    threshold_s: float | None = None,
) -> None:
    try:
        from adaos.services.incident_registry import record_skill_handler_pressure

        record_skill_handler_pressure(
            skill=skill,
            topic=topic,
            handler=handler,
            signal=signal,
            duration_s=duration_s,
            pending=pending,
            threshold_s=threshold_s,
        )
    except Exception:
        _LOG.debug("failed to record skill subscription pressure", exc_info=True)


async def _watch_blocking_handler(token: str, threshold_s: float) -> None:
    await asyncio.sleep(threshold_s)
    with _LOCK:
        row = _ACTIVE.get(token)
        if row is None or row.get("watchdog_reported"):
            return
        row["watchdog_reported"] = True
        key = str(row.get("handler_key") or "")
        stats = _STATS.get(key)
        if stats is not None:
            stats["blocking_total"] = int(stats.get("blocking_total") or 0) + 1
        payload = dict(row)
    started = float(payload.get("running_at") or payload.get("queued_at") or time.time())
    duration_s = max(0.0, time.time() - started)
    _LOG.warning(
        "skill subscription exceeded execution budget skill=%s topic=%s handler=%s duration=%.3fs threshold=%.3fs thread=%s",
        payload.get("skill"),
        payload.get("topic"),
        payload.get("handler"),
        duration_s,
        threshold_s,
        payload.get("thread_name") or "pending",
    )
    await asyncio.to_thread(
        _record_pressure,
        skill=str(payload.get("skill") or "<unknown>"),
        topic=str(payload.get("topic") or "<unknown>"),
        handler=str(payload.get("handler") or "<unknown>"),
        signal="execution_budget_exceeded",
        duration_s=duration_s,
        pending=int(payload.get("pending_at_submit") or 0),
        threshold_s=threshold_s,
    )


async def run_sync_subscription(
    callback: Callable[[], Any],
    *,
    skill: str,
    topic: str,
    handler: str,
) -> Any:
    """Run untrusted synchronous skill code away from the runtime event loop.

    The executor is intentionally separate from asyncio's default executor so a
    noisy skill cannot starve core HTTP, NATS, or persistence offloads.
    """

    key = _handler_key(skill, topic, handler)
    max_pending = _env_int("ADAOS_SKILL_SUBSCRIPTION_MAX_PENDING_PER_HANDLER", 2, minimum=1, maximum=64)
    now = time.time()
    with _LOCK:
        pending = int(_PENDING_BY_HANDLER.get(key) or 0)
        stats = _stats_row(key, skill=skill, topic=topic, handler=handler)
        if pending >= max_pending:
            stats["overload_total"] = int(stats.get("overload_total") or 0) + 1
            stats["last_overload_at"] = now
            last_log = float(_LAST_OVERLOAD_LOG_AT.get(key) or 0.0)
            should_log = now - last_log >= 5.0
            if should_log:
                _LAST_OVERLOAD_LOG_AT[key] = now
        else:
            should_log = False
            _PENDING_BY_HANDLER[key] = pending + 1
            stats["submitted_total"] = int(stats.get("submitted_total") or 0) + 1
    if pending >= max_pending:
        if should_log:
            _LOG.warning(
                "skill subscription admission rejected skill=%s topic=%s handler=%s pending=%s limit=%s",
                skill,
                topic,
                handler,
                pending,
                max_pending,
            )
            await asyncio.to_thread(
                _record_pressure,
                skill=skill,
                topic=topic,
                handler=handler,
                signal="pending_limit_exceeded",
                pending=pending,
            )
        return None

    token = uuid.uuid4().hex
    queued_at = time.time()
    with _LOCK:
        _ACTIVE[token] = {
            "token": token,
            "handler_key": key,
            "skill": skill,
            "topic": topic,
            "handler": handler,
            "state": "queued",
            "queued_at": queued_at,
            "running_at": None,
            "thread_id": None,
            "thread_name": None,
            "pending_at_submit": pending + 1,
            "watchdog_reported": False,
        }

    context = contextvars.copy_context()

    def _invoke() -> Any:
        running_at = time.time()
        with _LOCK:
            active = _ACTIVE.get(token)
            if active is not None:
                active.update(
                    {
                        "state": "running",
                        "running_at": running_at,
                        "thread_id": threading.get_ident(),
                        "thread_name": threading.current_thread().name,
                    }
                )
            stats = _STATS.get(key)
            if stats is not None:
                stats["last_started_at"] = running_at
        return context.run(callback)

    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_executor(), _invoke)
    threshold_s = _env_float("ADAOS_SKILL_SUBSCRIPTION_BLOCKING_WARN_S", 1.0, minimum=0.05, maximum=300.0)
    watchdog = asyncio.create_task(
        _watch_blocking_handler(token, threshold_s),
        name=f"skill-subscription-watchdog:{skill}:{topic}",
    )

    def _finalize(done: asyncio.Future[Any]) -> None:
        error: BaseException | None = None
        try:
            done.result()
        except BaseException as exc:
            error = exc
        finished_at = time.time()
        with _LOCK:
            active = _ACTIVE.pop(token, None) or {}
            _PENDING_BY_HANDLER[key] = max(0, int(_PENDING_BY_HANDLER.get(key) or 0) - 1)
            if _PENDING_BY_HANDLER[key] <= 0:
                _PENDING_BY_HANDLER.pop(key, None)
            stats = _STATS.get(key)
            if stats is not None:
                started_at = float(active.get("running_at") or active.get("queued_at") or finished_at)
                duration_s = max(0.0, finished_at - started_at)
                stats["completed_total"] = int(stats.get("completed_total") or 0) + 1
                stats["failed_total"] = int(stats.get("failed_total") or 0) + (1 if error is not None else 0)
                stats["last_duration_s"] = round(duration_s, 6)
                stats["max_duration_s"] = round(max(float(stats.get("max_duration_s") or 0.0), duration_s), 6)
                stats["last_finished_at"] = finished_at
                stats["last_error"] = type(error).__name__ if error is not None else None
        watchdog.cancel()

    future.add_done_callback(_finalize)
    # Eventbus superseding may cancel the awaiting task. Shielding keeps the
    # non-cancellable worker represented in admission and diagnostics until it
    # actually exits.
    return await asyncio.shield(future)


async def run_async_subscription(
    callback: Callable[[], Any],
    *,
    skill: str,
    topic: str,
    handler: str,
) -> Any:
    """Observe and bound an async skill handler running on the owner loop.

    Async handlers may legitimately await runtime-owned asyncio objects, so
    moving them to a worker thread would violate object affinity. Admission and
    timing still make an evolved blocking handler attributable after the loop
    recovers.
    """

    key = _handler_key(skill, topic, handler)
    max_pending = _env_int("ADAOS_SKILL_SUBSCRIPTION_MAX_PENDING_PER_HANDLER", 2, minimum=1, maximum=64)
    now = time.time()
    with _LOCK:
        pending = int(_PENDING_BY_HANDLER.get(key) or 0)
        stats = _stats_row(key, skill=skill, topic=topic, handler=handler)
        stats["execution_mode"] = "async_owner_loop"
        if pending >= max_pending:
            stats["overload_total"] = int(stats.get("overload_total") or 0) + 1
            stats["last_overload_at"] = now
            last_log = float(_LAST_OVERLOAD_LOG_AT.get(key) or 0.0)
            should_log = now - last_log >= 5.0
            if should_log:
                _LAST_OVERLOAD_LOG_AT[key] = now
        else:
            should_log = False
            _PENDING_BY_HANDLER[key] = pending + 1
            stats["submitted_total"] = int(stats.get("submitted_total") or 0) + 1
    if pending >= max_pending:
        if should_log:
            _LOG.warning(
                "async skill subscription admission rejected skill=%s topic=%s handler=%s pending=%s limit=%s",
                skill,
                topic,
                handler,
                pending,
                max_pending,
            )
            await asyncio.to_thread(
                _record_pressure,
                skill=skill,
                topic=topic,
                handler=handler,
                signal="pending_limit_exceeded",
                pending=pending,
            )
        return None

    token = uuid.uuid4().hex
    started_at = time.time()
    with _LOCK:
        _ACTIVE[token] = {
            "token": token,
            "handler_key": key,
            "skill": skill,
            "topic": topic,
            "handler": handler,
            "execution_mode": "async_owner_loop",
            "state": "running",
            "queued_at": started_at,
            "running_at": started_at,
            "thread_id": threading.get_ident(),
            "thread_name": threading.current_thread().name,
            "pending_at_submit": pending + 1,
            "watchdog_reported": False,
        }
        stats = _STATS.get(key)
        if stats is not None:
            stats["last_started_at"] = started_at

    threshold_s = _env_float("ADAOS_SKILL_SUBSCRIPTION_BLOCKING_WARN_S", 1.0, minimum=0.05, maximum=300.0)
    watchdog = asyncio.create_task(
        _watch_blocking_handler(token, threshold_s),
        name=f"skill-async-subscription-watchdog:{skill}:{topic}",
    )
    error: BaseException | None = None
    try:
        result = callback()
        if inspect.isawaitable(result):
            return await result
        return result
    except BaseException as exc:
        error = exc
        raise
    finally:
        finished_at = time.time()
        duration_s = max(0.0, finished_at - started_at)
        with _LOCK:
            active = _ACTIVE.pop(token, None) or {}
            watchdog_reported = bool(active.get("watchdog_reported"))
            _PENDING_BY_HANDLER[key] = max(0, int(_PENDING_BY_HANDLER.get(key) or 0) - 1)
            if _PENDING_BY_HANDLER[key] <= 0:
                _PENDING_BY_HANDLER.pop(key, None)
            stats = _STATS.get(key)
            if stats is not None:
                stats["completed_total"] = int(stats.get("completed_total") or 0) + 1
                stats["failed_total"] = int(stats.get("failed_total") or 0) + (1 if error is not None else 0)
                stats["last_duration_s"] = round(duration_s, 6)
                stats["max_duration_s"] = round(max(float(stats.get("max_duration_s") or 0.0), duration_s), 6)
                stats["last_finished_at"] = finished_at
                stats["last_error"] = type(error).__name__ if error is not None else None
                if duration_s >= threshold_s and not watchdog_reported:
                    stats["blocking_total"] = int(stats.get("blocking_total") or 0) + 1
        watchdog.cancel()
        if duration_s >= threshold_s and not watchdog_reported:
            _LOG.warning(
                "async skill subscription exceeded execution budget skill=%s topic=%s handler=%s duration=%.3fs threshold=%.3fs",
                skill,
                topic,
                handler,
                duration_s,
                threshold_s,
            )
            await asyncio.to_thread(
                _record_pressure,
                skill=skill,
                topic=topic,
                handler=handler,
                signal="async_execution_budget_exceeded",
                duration_s=duration_s,
                pending=pending + 1,
                threshold_s=threshold_s,
            )


def subscription_execution_snapshot(*, limit: int = 25) -> dict[str, Any]:
    now = time.time()
    bounded_limit = max(1, min(int(limit or 25), 100))
    with _LOCK:
        active = [dict(item) for item in _ACTIVE.values()]
        stats = [dict(item) for item in _STATS.values()]
        pending_total = sum(int(value or 0) for value in _PENDING_BY_HANDLER.values())
    for item in active:
        started = float(item.get("running_at") or item.get("queued_at") or now)
        item["age_s"] = round(max(0.0, now - started), 3)
        item.pop("handler_key", None)
        item.pop("token", None)
    active.sort(key=lambda item: (-float(item.get("age_s") or 0.0), str(item.get("handler") or "")))
    stats.sort(
        key=lambda item: (
            -int(item.get("blocking_total") or 0),
            -int(item.get("overload_total") or 0),
            -float(item.get("max_duration_s") or 0.0),
        )
    )
    return {
        "schema": "adaos.skill_subscription_execution.v1",
        "executor_workers": _env_int("ADAOS_SKILL_SUBSCRIPTION_WORKERS", 4, minimum=1, maximum=32),
        "max_pending_per_handler": _env_int(
            "ADAOS_SKILL_SUBSCRIPTION_MAX_PENDING_PER_HANDLER", 2, minimum=1, maximum=64
        ),
        "blocking_warn_s": _env_float(
            "ADAOS_SKILL_SUBSCRIPTION_BLOCKING_WARN_S", 1.0, minimum=0.05, maximum=300.0
        ),
        "active_total": len(active),
        "pending_total": pending_total,
        "active": active[:bounded_limit],
        "top_handlers": stats[:bounded_limit],
        "updated_at": now,
    }


def reset_subscription_execution_runtime() -> None:
    with _LOCK:
        _ACTIVE.clear()
        _PENDING_BY_HANDLER.clear()
        _STATS.clear()
        _LAST_OVERLOAD_LOG_AT.clear()


__all__ = [
    "reset_subscription_execution_runtime",
    "run_async_subscription",
    "run_sync_subscription",
    "subscription_execution_snapshot",
]
