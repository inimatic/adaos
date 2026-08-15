from __future__ import annotations

import asyncio
import functools
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from starlette.responses import Response


T = TypeVar("T")


@dataclass(frozen=True)
class _CachedResponse:
    body: bytes
    status_code: int
    headers: dict[str, str]
    media_type: str | None
    captured_at: float


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _freeze_key(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze_key(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze_key(item) for item in value)
    return repr(value)


def _clone_response(response: Response) -> Response:
    return Response(
        content=bytes(response.body or b""),
        status_code=int(response.status_code),
        headers=dict(response.headers),
        media_type=response.media_type,
    )


class ReliabilityRuntimeBeaconExecutor:
    """Keep the browser channel beacon independent from general blocking work."""

    def __init__(self, *, max_workers: int = 2) -> None:
        self._max_workers = max(1, int(max_workers))
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.RLock()
        self._in_flight: dict[Any, Future[tuple[T, float]]] = {}
        self._timed_out_futures: set[Future[tuple[T, float]]] = set()
        self._cache: dict[Any, _CachedResponse] = {}
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
            "coalesced_total": 0,
            "timeout_total": 0,
            "late_completed_total": 0,
            "stale_served_total": 0,
            "unavailable_total": 0,
            "cache_write_total": 0,
            "cache_expired_total": 0,
            "last_timeout_at": None,
            "last_stale_served_at": None,
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

    @staticmethod
    def _request_key(
        callback: Callable[..., T],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        content_only: bool,
    ) -> tuple[Any, ...]:
        ignored = {"started_at", "endpoint"}
        if content_only:
            ignored.add("if_none_match")
        stable_kwargs = {key: value for key, value in kwargs.items() if key not in ignored}
        return (
            str(getattr(callback, "__module__", "") or ""),
            str(getattr(callback, "__qualname__", "") or repr(callback)),
            _freeze_key(args),
            _freeze_key(stable_kwargs),
        )

    @staticmethod
    def _cached_response(result: Any) -> _CachedResponse | None:
        if not isinstance(result, Response) or int(result.status_code) != 200:
            return None
        return _CachedResponse(
            body=bytes(result.body or b""),
            status_code=int(result.status_code),
            headers=dict(result.headers),
            media_type=result.media_type,
            captured_at=time.time(),
        )

    def _finish_future(
        self,
        future: Future[tuple[T, float]],
        *,
        execution_key: Any,
        content_key: Any,
    ) -> None:
        cached: _CachedResponse | None = None
        try:
            result, _queue_wait_ms = future.result()
            cached = self._cached_response(result)
        except BaseException:
            cached = None
        with self._lock:
            if self._in_flight.get(execution_key) is future:
                self._in_flight.pop(execution_key, None)
            if future in self._timed_out_futures:
                self._timed_out_futures.discard(future)
                self._stats["late_completed_total"] = int(self._stats.get("late_completed_total") or 0) + 1
            if cached is not None:
                self._cache[content_key] = cached
                self._stats["cache_write_total"] = int(self._stats.get("cache_write_total") or 0) + 1
                if len(self._cache) > 32:
                    oldest_key = min(self._cache, key=lambda key: self._cache[key].captured_at)
                    self._cache.pop(oldest_key, None)

    def _fallback_from_cache(self, content_key: Any, *, reason: str, timeout_s: float) -> Response | None:
        now = time.time()
        max_stale_s = _env_float(
            "ADAOS_RELIABILITY_RUNTIME_BEACON_MAX_STALE_S",
            15.0,
            minimum=1.0,
            maximum=300.0,
        )
        with self._lock:
            cached = self._cache.get(content_key)
            cache_age_s = max(0.0, now - cached.captured_at) if cached is not None else None
            if cached is not None and cache_age_s is not None and cache_age_s > max_stale_s:
                self._cache.pop(content_key, None)
                self._stats["cache_expired_total"] = int(self._stats.get("cache_expired_total") or 0) + 1
                cached = None
            if cached is None:
                return None
            self._stats["stale_served_total"] = int(self._stats.get("stale_served_total") or 0) + 1
            self._stats["last_stale_served_at"] = now
        headers = dict(cached.headers)
        headers.update(
            {
                "X-AdaOS-Runtime-Executor": "dedicated",
                "X-AdaOS-Runtime-Stale": "1",
                "X-AdaOS-Runtime-Fallback": reason,
                "X-AdaOS-Runtime-Cache-Age-Ms": str(round(float(cache_age_s or 0.0) * 1000.0, 3)),
                "X-AdaOS-Runtime-Timeout-Ms": str(round(timeout_s * 1000.0, 3)),
            }
        )
        return Response(
            content=cached.body,
            status_code=cached.status_code,
            headers=headers,
            media_type=cached.media_type,
        )

    async def run(
        self,
        callback: Callable[..., T],
        /,
        *args: Any,
        timeout_fallback: Callable[..., T] | None = None,
        **kwargs: Any,
    ) -> T:
        timeout_s = _env_float(
            "ADAOS_RELIABILITY_RUNTIME_BEACON_TIMEOUT_S",
            0.75,
            minimum=0.1,
            maximum=5.0,
        )
        execution_key = self._request_key(callback, args, kwargs, content_only=False)
        content_key = self._request_key(callback, args, kwargs, content_only=True)
        with self._lock:
            future = self._in_flight.get(execution_key)
            if future is not None and not future.done():
                self._stats["coalesced_total"] = int(self._stats.get("coalesced_total") or 0) + 1
            else:
                queued_at = time.perf_counter()
                self._stats["submitted_total"] = int(self._stats.get("submitted_total") or 0) + 1
                future = self._get_executor().submit(self._execute, callback, args, kwargs, queued_at)
                self._in_flight[execution_key] = future
                future.add_done_callback(
                    functools.partial(
                        self._finish_future,
                        execution_key=execution_key,
                        content_key=content_key,
                    )
                )
        try:
            result, queue_wait_ms = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            with self._lock:
                if future.done():
                    self._stats["late_completed_total"] = int(self._stats.get("late_completed_total") or 0) + 1
                else:
                    self._timed_out_futures.add(future)
                self._stats["timeout_total"] = int(self._stats.get("timeout_total") or 0) + 1
                self._stats["last_timeout_at"] = time.time()
                self._stats["last_error"] = "TimeoutError: runtime beacon callback exceeded SLA"
            stale = self._fallback_from_cache(content_key, reason="timeout", timeout_s=timeout_s)
            if stale is not None:
                return stale  # type: ignore[return-value]
            with self._lock:
                self._stats["unavailable_total"] = int(self._stats.get("unavailable_total") or 0) + 1
            if timeout_fallback is not None:
                return timeout_fallback(reason="timeout", timeout_s=timeout_s)
            raise
        except Exception as exc:
            stale = self._fallback_from_cache(content_key, reason="error", timeout_s=timeout_s)
            if stale is not None:
                return stale  # type: ignore[return-value]
            with self._lock:
                self._stats["unavailable_total"] = int(self._stats.get("unavailable_total") or 0) + 1
            if timeout_fallback is not None:
                return timeout_fallback(reason=f"error:{type(exc).__name__}", timeout_s=timeout_s)
            raise
        if isinstance(result, Response):
            result = _clone_response(result)  # type: ignore[assignment]
        headers = getattr(result, "headers", None)
        if headers is not None:
            headers["X-AdaOS-Runtime-Executor"] = "dedicated"
            headers["X-AdaOS-Runtime-Queue-Ms"] = str(round(queue_wait_ms, 3))
            headers["X-AdaOS-Runtime-Stale"] = "0"
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            cache_entries = len(self._cache)
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
            "cache_entries": cache_entries,
            "timeout_s": _env_float(
                "ADAOS_RELIABILITY_RUNTIME_BEACON_TIMEOUT_S", 0.75, minimum=0.1, maximum=5.0
            ),
            "max_stale_s": _env_float(
                "ADAOS_RELIABILITY_RUNTIME_BEACON_MAX_STALE_S", 15.0, minimum=1.0, maximum=300.0
            ),
        }


_RUNTIME = ReliabilityRuntimeBeaconExecutor()


async def run_reliability_runtime_beacon(
    callback: Callable[..., T],
    /,
    *args: Any,
    timeout_fallback: Callable[..., T] | None = None,
    **kwargs: Any,
) -> T:
    return await _RUNTIME.run(callback, *args, timeout_fallback=timeout_fallback, **kwargs)


def reliability_runtime_beacon_snapshot() -> dict[str, Any]:
    return _RUNTIME.snapshot()
