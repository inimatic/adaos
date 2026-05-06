from __future__ import annotations
import asyncio
import logging
import os
import time
from collections import defaultdict
from threading import RLock
from typing import Callable, Awaitable, Any, DefaultDict, List

from adaos.domain import Event
from adaos.ports import EventBus


Handler = Callable[[Event], Any] | Callable[[Event], Awaitable[Any]]

_log = logging.getLogger("adaos.eventbus")


def _trace_subscribe_enabled() -> bool:
    raw = str(os.getenv("ADAOS_EVENTBUS_TRACE_SUBSCRIBE", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _handler_label(handler: Handler) -> str:
    """
    Build a human-readable label for a handler, including optional skill/topic
    hints injected by the SDK decorators.
    """
    mod = getattr(handler, "__module__", None) or "<?>"
    name = getattr(handler, "__name__", None) or repr(handler)
    skill = getattr(handler, "_adaos_skill", None)
    topic = getattr(handler, "_adaos_topic", None)
    adapted = getattr(handler, "_adaos_handler", None)
    parts = [f"{mod}.{name}"]
    if adapted:
        parts.append(f"adapted={adapted}")
    if skill:
        parts.append(f"skill={skill}")
    if topic:
        parts.append(f"topic={topic}")
    return " ".join(parts)


def _slow_handler_threshold_s(kind: str, default: float) -> float:
    env_name = f"ADAOS_EVENTBUS_SLOW_{kind.upper()}_WARN_S"
    try:
        value = float(os.getenv(env_name, str(default)) or str(default))
    except Exception:
        value = default
    return max(0.0, value)


def _pending_backlog_warn_threshold() -> int:
    try:
        value = int(str(os.getenv("ADAOS_EVENTBUS_PENDING_WARN_THRESHOLD", "64") or "64").strip())
    except Exception:
        value = 64
    return max(1, min(value, 100000))


def _pending_backlog_warn_interval_s() -> float:
    try:
        value = float(str(os.getenv("ADAOS_EVENTBUS_PENDING_WARN_INTERVAL_S", "5.0") or "5.0").strip())
    except Exception:
        value = 5.0
    return max(0.0, min(value, 300.0))


async def _run_coro_with_timing(coro: Awaitable[Any], handler: Handler, event: Event) -> None:
    """
    Wrapper for async handlers that records execution time and logs slow/crashing
    handlers for debugging high CPU usage in the hub.
    """
    started = time.perf_counter()
    try:
        await coro
    except Exception:  # pragma: no cover - defensive logging
        _log.warning(
            "event handler crashed handler=%s type=%s",
            _handler_label(handler),
            getattr(event, "type", "<unknown>"),
            exc_info=True,
        )
    else:
        duration = time.perf_counter() - started
        if duration >= _slow_handler_threshold_s("async", 0.25):
            _log.warning(
                "slow async event handler handler=%s type=%s duration=%.3fs",
                _handler_label(handler),
                getattr(event, "type", "<unknown>"),
                duration,
            )


class LocalEventBus(EventBus):
    """
    Локальная неблокирующая шина событий для одного процесса.
      - subscribe(prefix, handler)
      - publish(event)

    Особенности:
      * prefix = "" или "*" — подписка на все события.
      * вызовы обработчиков делаются в текущем или уже запущенном event loop.

    Дополнительно эта реализация логирует медленные/падающие обработчики,
    чтобы упростить отладку случаев, когда какой‑то skill «крутит» CPU.
    """

    def __init__(self) -> None:
        self._subs: DefaultDict[str, List[Handler]] = defaultdict(list)
        self._lock = RLock()
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._pending_task_meta: dict[asyncio.Task[Any], tuple[str, str, float]] = {}
        self._pending_by_type: DefaultDict[str, int] = defaultdict(int)
        self._pending_by_handler: DefaultDict[str, int] = defaultdict(int)
        self._pending_peak = 0
        self._last_pending_warn_at = 0.0

    def _pending_backlog_snapshot_locked(self) -> dict[str, Any]:
        now = time.monotonic()
        oldest_age_s = 0.0
        for pending_task, (_event_type, _handler_name, started) in self._pending_task_meta.items():
            if pending_task.done():
                continue
            oldest_age_s = max(oldest_age_s, max(0.0, now - float(started or now)))
        top_types = sorted(
            ((event_type, count) for event_type, count in self._pending_by_type.items() if count > 0),
            key=lambda item: (-item[1], item[0]),
        )[:5]
        top_handlers = sorted(
            ((handler_name, count) for handler_name, count in self._pending_by_handler.items() if count > 0),
            key=lambda item: (-item[1], item[0]),
        )[:5]
        return {
            "pending_tasks": len(self._pending_tasks),
            "pending_peak": int(self._pending_peak),
            "oldest_age_s": float(oldest_age_s),
            "top_types": top_types,
            "top_handlers": top_handlers,
        }

    def backlog_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._pending_backlog_snapshot_locked()

    def _maybe_log_pending_backlog_locked(self) -> None:
        pending_total = len(self._pending_tasks)
        if pending_total < _pending_backlog_warn_threshold():
            return
        now = time.monotonic()
        if now - float(self._last_pending_warn_at or 0.0) < _pending_backlog_warn_interval_s():
            return
        snapshot = self._pending_backlog_snapshot_locked()
        self._last_pending_warn_at = now
        _log.warning(
            "eventbus backlog pending_tasks=%s peak_pending_tasks=%s oldest_age_s=%.3fs top_types=%s top_handlers=%s",
            int(snapshot["pending_tasks"]),
            int(snapshot["pending_peak"]),
            float(snapshot["oldest_age_s"]),
            snapshot["top_types"],
            snapshot["top_handlers"],
        )

    def _track_task(self, task: asyncio.Task[Any], handler: Handler, event: Event) -> None:
        event_type = str(getattr(event, "type", "<unknown>") or "<unknown>")
        handler_name = _handler_label(handler)
        started = time.monotonic()
        with self._lock:
            self._pending_tasks.add(task)
            self._pending_task_meta[task] = (event_type, handler_name, started)
            self._pending_by_type[event_type] += 1
            self._pending_by_handler[handler_name] += 1
            if len(self._pending_tasks) > self._pending_peak:
                self._pending_peak = len(self._pending_tasks)
            self._maybe_log_pending_backlog_locked()

        def _cleanup(done: asyncio.Task[Any]) -> None:
            with self._lock:
                self._pending_tasks.discard(done)
                meta = self._pending_task_meta.pop(done, None)
                if meta is None:
                    return
                done_event_type, done_handler_name, _done_started = meta
                if done_event_type in self._pending_by_type:
                    self._pending_by_type[done_event_type] = max(0, int(self._pending_by_type[done_event_type]) - 1)
                    if self._pending_by_type[done_event_type] <= 0:
                        self._pending_by_type.pop(done_event_type, None)
                if done_handler_name in self._pending_by_handler:
                    self._pending_by_handler[done_handler_name] = max(0, int(self._pending_by_handler[done_handler_name]) - 1)
                    if self._pending_by_handler[done_handler_name] <= 0:
                        self._pending_by_handler.pop(done_handler_name, None)

        task.add_done_callback(_cleanup)

    async def wait_for_idle(self, timeout: float = 5.0) -> bool:
        """
        Wait until all async handlers spawned by ``publish()`` finish.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout))
        while True:
            with self._lock:
                pending = [task for task in self._pending_tasks if not task.done()]
            if not pending:
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.wait(pending, timeout=min(0.1, remaining), return_when=asyncio.FIRST_COMPLETED)

    def subscribe(self, type_prefix: str, handler: Handler) -> None:
        with self._lock:
            self._subs[type_prefix].append(handler)
        if _trace_subscribe_enabled():
            _log.debug("bus.subscribe prefix=%r handler=%s", type_prefix, _handler_label(handler))

    def publish(self, event: Event) -> None:
        with self._lock:
            pairs = [(p, hs[:]) for p, hs in self._subs.items()]

        if _log.isEnabledFor(logging.DEBUG):
            total_handlers = sum(
                len(hs) for p, hs in pairs if p == "" or p == "*" or event.type.startswith(p)
            )
            _log.debug(
                "bus.publish type=%s source=%s handlers=%d",
                getattr(event, "type", "<unknown>"),
                getattr(event, "source", "<unknown>"),
                total_handlers,
            )

        for prefix, handlers in pairs:
            if prefix != "*" and prefix != "" and not event.type.startswith(prefix):
                continue
            for h in handlers:
                started = time.perf_counter()
                try:
                    res = h(event)
                except Exception:  # pragma: no cover - defensive logging
                    _log.warning(
                        "event handler crashed handler=%s type=%s",
                        _handler_label(h),
                        getattr(event, "type", "<unknown>"),
                        exc_info=True,
                    )
                    continue

                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        # Если нет текущего цикла, fallback на asyncio.run (CLI/скрипты).
                        asyncio.run(res)
                    else:
                        task = loop.create_task(_run_coro_with_timing(res, h, event))
                        self._track_task(task, h, event)
                else:
                    duration = time.perf_counter() - started
                    if duration >= _slow_handler_threshold_s("sync", 0.1):
                        _log.warning(
                            "slow sync event handler handler=%s type=%s duration=%.3fs",
                            _handler_label(h),
                            getattr(event, "type", "<unknown>"),
                            duration,
                        )


def emit(bus: EventBus, type_: str, payload: dict, source: str) -> None:
    bus.publish(Event(type=type_, payload=payload, source=source, ts=time.time()))

