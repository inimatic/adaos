from __future__ import annotations

import asyncio
import contextvars
import threading

from adaos.services import runtime_executor


def test_runtime_default_executor_is_fully_started_before_use(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_DEFAULT_EXECUTOR_WORKERS", "4")
    monkeypatch.setenv("ADAOS_RUNTIME_INTERACTIVE_EXECUTOR_WORKERS", "2")
    runtime_executor._reset_runtime_default_executor_for_tests()

    async def _run() -> None:
        snapshot = await runtime_executor.install_runtime_default_executor()

        assert snapshot["state"] == "ready"
        assert snapshot["configured_workers"] == 4
        assert snapshot["live_threads"] == 4
        assert snapshot["interactive_configured_workers"] == 2
        assert snapshot["interactive_live_threads"] == 2

        def _unexpected_thread_start(_self) -> None:
            raise AssertionError("to_thread must use a prestarted runtime worker")

        with monkeypatch.context() as thread_patch:
            thread_patch.setattr(threading.Thread, "start", _unexpected_thread_start)
            results = await asyncio.gather(
                *(asyncio.to_thread(lambda value=value: value) for value in range(8))
            )
            marker = contextvars.ContextVar("interactive_marker", default="missing")
            marker.set("preserved")
            interactive = await runtime_executor.run_runtime_interactive(marker.get)

        assert results == list(range(8))
        assert interactive == "preserved"

    asyncio.run(_run())


def test_runtime_interactive_executor_reports_queue_pressure(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_INTERACTIVE_EXECUTOR_WORKERS", "2")
    runtime_executor._reset_runtime_default_executor_for_tests()

    async def _run() -> None:
        await runtime_executor.install_runtime_interactive_executor()
        entered = threading.Barrier(3)
        release = threading.Event()

        def _blocked(value: int) -> int:
            entered.wait(timeout=5.0)
            release.wait(timeout=5.0)
            return value

        first = asyncio.create_task(runtime_executor.run_runtime_interactive(_blocked, 1))
        second = asyncio.create_task(runtime_executor.run_runtime_interactive(_blocked, 2))
        await asyncio.to_thread(entered.wait, 5.0)
        third = asyncio.create_task(runtime_executor.run_runtime_interactive(lambda: 3))
        await asyncio.sleep(0.05)
        release.set()

        assert await asyncio.gather(first, second, third) == [1, 2, 3]
        snapshot = runtime_executor.runtime_default_executor_snapshot()
        assert snapshot["interactive_submitted_total"] == 3
        assert snapshot["interactive_completed_total"] == 3
        assert snapshot["interactive_failed_total"] == 0
        assert snapshot["interactive_inflight"] == 0
        assert snapshot["interactive_max_queue_wait_ms"] >= 40.0
        assert snapshot["interactive_max_execution_ms"] >= 40.0

    try:
        asyncio.run(_run())
    finally:
        runtime_executor._reset_runtime_default_executor_for_tests()
