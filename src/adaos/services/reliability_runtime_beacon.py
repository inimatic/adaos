from __future__ import annotations

import asyncio
import functools
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar


T = TypeVar("T")


class ReliabilityRuntimeBeaconExecutor:
    """Keep the browser channel beacon independent from general blocking work."""

    def __init__(self, *, max_workers: int = 2) -> None:
        self._max_workers = max(1, int(max_workers))
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.RLock()
        self._stats: dict[str, Any] = {
            "submitted_total": 0,
            "completed_total": 0,
            "failed_total": 0,
            "in_flight": 0,
            "max_in_flight": 0,
            "last_queue_wait_ms": 0.0,
            "max_queue_wait_ms": 0.0,
            "last_execution_ms": 0.0,
            "max_execution_ms": 0.0,
            "last_error": None,
        }

    def _get_executor(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix="adaos-reliability-beacon",
                )
            return self._executor

    def _execute(
        self,
        callback: Callable[..., T],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        queued_at: float,
    ) -> tuple[T, float]:
        started = time.perf_counter()
        queue_wait_ms = (started - queued_at) * 1000.0
        with self._lock:
            in_flight = int(self._stats.get("in_flight") or 0) + 1
            self._stats["in_flight"] = in_flight
            self._stats["max_in_flight"] = max(
                int(self._stats.get("max_in_flight") or 0),
                in_flight,
            )
            self._stats["last_queue_wait_ms"] = round(queue_wait_ms, 3)
            self._stats["max_queue_wait_ms"] = round(
                max(float(self._stats.get("max_queue_wait_ms") or 0.0), queue_wait_ms),
                3,
            )
        error: Exception | None = None
        try:
            return callback(*args, **kwargs), queue_wait_ms
        except Exception as exc:
            error = exc
            raise
        finally:
            execution_ms = (time.perf_counter() - started) * 1000.0
            with self._lock:
                self._stats["in_flight"] = max(0, int(self._stats.get("in_flight") or 0) - 1)
                self._stats["last_execution_ms"] = round(execution_ms, 3)
                self._stats["max_execution_ms"] = round(
                    max(float(self._stats.get("max_execution_ms") or 0.0), execution_ms),
                    3,
                )
                if error is None:
                    self._stats["completed_total"] = int(self._stats.get("completed_total") or 0) + 1
                    self._stats["last_error"] = None
                else:
                    self._stats["failed_total"] = int(self._stats.get("failed_total") or 0) + 1
                    self._stats["last_error"] = f"{type(error).__name__}: {error}"

    async def run(self, callback: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        queued_at = time.perf_counter()
        with self._lock:
            self._stats["submitted_total"] = int(self._stats.get("submitted_total") or 0) + 1
        result, queue_wait_ms = await asyncio.get_running_loop().run_in_executor(
            self._get_executor(),
            functools.partial(self._execute, callback, args, kwargs, queued_at),
        )
        headers = getattr(result, "headers", None)
        if headers is not None:
            headers["X-AdaOS-Runtime-Executor"] = "dedicated"
            headers["X-AdaOS-Runtime-Queue-Ms"] = str(round(queue_wait_ms, 3))
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
        pending_total = max(
            0,
            int(stats.get("submitted_total") or 0)
            - int(stats.get("completed_total") or 0)
            - int(stats.get("failed_total") or 0),
        )
        return {
            "schema": "adaos.reliability_runtime_beacon_executor.v1",
            "status": "degraded" if stats.get("last_error") else "ready",
            "executor": "dedicated_bounded",
            "max_workers": self._max_workers,
            **stats,
            "pending_total": pending_total,
            "queued_total": max(0, pending_total - int(stats.get("in_flight") or 0)),
        }


_RUNTIME = ReliabilityRuntimeBeaconExecutor()


async def run_reliability_runtime_beacon(
    callback: Callable[..., T],
    /,
    *args: Any,
    **kwargs: Any,
) -> T:
    return await _RUNTIME.run(callback, *args, **kwargs)


def reliability_runtime_beacon_snapshot() -> dict[str, Any]:
    return _RUNTIME.snapshot()
