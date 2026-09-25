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
