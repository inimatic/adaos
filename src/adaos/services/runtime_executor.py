from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any


_LOCK = threading.Lock()
_EXECUTOR: ThreadPoolExecutor | None = None
_INTERACTIVE_EXECUTOR: ThreadPoolExecutor | None = None
_STATE: dict[str, Any] = {
    "schema": "adaos.runtime.default_executor.v1",
    "state": "not_installed",
    "configured_workers": 0,
    "live_threads": 0,
    "install_total": 0,
    "installed_at": 0.0,
    "install_duration_ms": None,
    "last_error": None,
    "interactive_configured_workers": 0,
    "interactive_live_threads": 0,
}


def runtime_default_executor_workers() -> int:
    try:
        value = int(
            str(os.getenv("ADAOS_RUNTIME_DEFAULT_EXECUTOR_WORKERS") or "8").strip()
        )
    except Exception:
        value = 8
    return min(32, max(2, value))


def runtime_interactive_executor_workers() -> int:
    try:
        value = int(
            str(os.getenv("ADAOS_RUNTIME_INTERACTIVE_EXECUTOR_WORKERS") or "4").strip()
        )
    except Exception:
        value = 4
    return min(8, max(2, value))


def runtime_default_executor_snapshot() -> dict[str, Any]:
    with _LOCK:
        snapshot = dict(_STATE)
        executor = _EXECUTOR
        interactive = _INTERACTIVE_EXECUTOR
        snapshot["live_threads"] = (
            len(getattr(executor, "_threads", ())) if executor is not None else 0
        )
        snapshot["interactive_live_threads"] = (
            len(getattr(interactive, "_threads", ())) if interactive is not None else 0
        )
        installed_at = float(snapshot.get("installed_at") or 0.0)
    snapshot["installed_ago_s"] = (
        round(max(0.0, time.time() - installed_at), 3) if installed_at else None
    )
    return snapshot


async def install_runtime_default_executor() -> dict[str, Any]:
    """Install a fully started executor before runtime readiness is advertised."""

    global _EXECUTOR
    loop = asyncio.get_running_loop()
    workers = runtime_default_executor_workers()
    with _LOCK:
        existing = _EXECUTOR
        reusable = existing is not None and not bool(
            getattr(existing, "_shutdown", False)
        )
        if reusable:
            executor = existing
        else:
            executor = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="adaos-runtime-io"
            )
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
        await install_runtime_interactive_executor()
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
                    "install_duration_ms": round(
                        (time.monotonic() - started) * 1000.0, 3
                    ),
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
    await install_runtime_interactive_executor()
    return runtime_default_executor_snapshot()


async def install_runtime_interactive_executor() -> dict[str, Any]:
    """Prestart the small UI/control lane independently of background I/O."""

    global _INTERACTIVE_EXECUTOR
    loop = asyncio.get_running_loop()
    workers = runtime_interactive_executor_workers()
    with _LOCK:
        existing = _INTERACTIVE_EXECUTOR
        if existing is not None and not bool(getattr(existing, "_shutdown", False)):
            executor = existing
            reusable = True
        else:
            executor = ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="adaos-interactive-io",
            )
            _INTERACTIVE_EXECUTOR = executor
            _STATE["interactive_configured_workers"] = workers
            reusable = False
    if reusable:
        return runtime_default_executor_snapshot()

    release = threading.Event()

    def _hold_worker() -> None:
        release.wait(timeout=30.0)

    futures = [loop.run_in_executor(executor, _hold_worker) for _ in range(workers)]
    release.set()
    await asyncio.gather(*futures)
    return runtime_default_executor_snapshot()


async def run_runtime_interactive(func, /, *args, **kwargs):
    """Run latency-sensitive local control work outside the background I/O queue.

    ``asyncio.to_thread`` propagates context variables; preserve that contract
    explicitly because Root MCP handlers depend on the active AgentContext.
    """

    if _INTERACTIVE_EXECUTOR is None or bool(
        getattr(_INTERACTIVE_EXECUTOR, "_shutdown", False)
    ):
        await install_runtime_interactive_executor()
    with _LOCK:
        executor = _INTERACTIVE_EXECUTOR
    if executor is None:  # pragma: no cover - guarded by installer
        raise RuntimeError("interactive runtime executor is unavailable")
    context = contextvars.copy_context()
    call = partial(func, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(
        executor,
        context.run,
        call,
    )


def _reset_runtime_default_executor_for_tests() -> None:
    global _EXECUTOR, _INTERACTIVE_EXECUTOR
    with _LOCK:
        executor = _EXECUTOR
        interactive = _INTERACTIVE_EXECUTOR
        _EXECUTOR = None
        _INTERACTIVE_EXECUTOR = None
        _STATE.update(
            {
                "state": "not_installed",
                "configured_workers": 0,
                "live_threads": 0,
                "install_total": 0,
                "installed_at": 0.0,
                "install_duration_ms": None,
                "last_error": None,
                "interactive_configured_workers": 0,
                "interactive_live_threads": 0,
            }
        )
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)
    if interactive is not None:
        interactive.shutdown(wait=True, cancel_futures=True)
