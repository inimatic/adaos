from __future__ import annotations

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any


_LOCK = threading.Lock()
_EXECUTOR: ThreadPoolExecutor | None = None
_STATE: dict[str, Any] = {
    "schema": "adaos.runtime.default_executor.v1",
    "state": "not_installed",
    "configured_workers": 0,
    "live_threads": 0,
    "install_total": 0,
    "installed_at": 0.0,
    "install_duration_ms": None,
    "last_error": None,
}


def runtime_default_executor_workers() -> int:
    try:
        value = int(str(os.getenv("ADAOS_RUNTIME_DEFAULT_EXECUTOR_WORKERS") or "8").strip())
    except Exception:
        value = 8
    return min(32, max(2, value))


def runtime_default_executor_snapshot() -> dict[str, Any]:
    with _LOCK:
        snapshot = dict(_STATE)
        executor = _EXECUTOR
        snapshot["live_threads"] = len(getattr(executor, "_threads", ())) if executor is not None else 0
        installed_at = float(snapshot.get("installed_at") or 0.0)
    snapshot["installed_ago_s"] = round(max(0.0, time.time() - installed_at), 3) if installed_at else None
    return snapshot


async def install_runtime_default_executor() -> dict[str, Any]:
    """Install a fully started executor before runtime readiness is advertised."""

    global _EXECUTOR
    loop = asyncio.get_running_loop()
    workers = runtime_default_executor_workers()
    with _LOCK:
        existing = _EXECUTOR
        reusable = existing is not None and not bool(getattr(existing, "_shutdown", False))
        if reusable:
            executor = existing
        else:
            executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="adaos-runtime-io")
            _EXECUTOR = executor
            _STATE.update(
                {
                    "state": "installing",
                    "configured_workers": workers,
                    "install_total": int(_STATE.get("install_total") or 0) + 1,
                    "last_error": None,
                }
            )
    if reusable:
        loop.set_default_executor(executor)
        return runtime_default_executor_snapshot()

    started = time.monotonic()
    release = threading.Event()

    def _hold_worker() -> None:
        release.wait(timeout=30.0)

    futures: list[asyncio.Future[Any]] = []
    try:
        for _ in range(workers):
            futures.append(loop.run_in_executor(executor, _hold_worker))
        loop.set_default_executor(executor)
    except Exception as exc:
        release.set()
        executor.shutdown(wait=False, cancel_futures=True)
        with _LOCK:
            _STATE.update(
                {
                    "state": "error",
                    "install_duration_ms": round((time.monotonic() - started) * 1000.0, 3),
                    "last_error": f"{type(exc).__name__}: {exc}",
                }
            )
        raise
    finally:
        release.set()

    await asyncio.gather(*futures)
    duration_ms = round((time.monotonic() - started) * 1000.0, 3)
    with _LOCK:
        _STATE.update(
            {
                "state": "ready",
                "installed_at": time.time(),
                "install_duration_ms": duration_ms,
                "last_error": None,
            }
        )
    return runtime_default_executor_snapshot()


def _reset_runtime_default_executor_for_tests() -> None:
    global _EXECUTOR
    with _LOCK:
        executor = _EXECUTOR
        _EXECUTOR = None
        _STATE.update(
            {
                "state": "not_installed",
                "configured_workers": 0,
                "live_threads": 0,
                "install_total": 0,
                "installed_at": 0.0,
                "install_duration_ms": None,
                "last_error": None,
            }
        )
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)
