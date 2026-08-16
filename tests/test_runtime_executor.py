from __future__ import annotations

import asyncio
import threading

from adaos.services import runtime_executor


def test_runtime_default_executor_is_fully_started_before_use(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RUNTIME_DEFAULT_EXECUTOR_WORKERS", "4")
    runtime_executor._reset_runtime_default_executor_for_tests()

    async def _run() -> None:
        snapshot = await runtime_executor.install_runtime_default_executor()

        assert snapshot["state"] == "ready"
        assert snapshot["configured_workers"] == 4
        assert snapshot["live_threads"] == 4

        def _unexpected_thread_start(_self) -> None:
            raise AssertionError("to_thread must use a prestarted runtime worker")

        with monkeypatch.context() as thread_patch:
            thread_patch.setattr(threading.Thread, "start", _unexpected_thread_start)
            results = await asyncio.gather(*(asyncio.to_thread(lambda value=value: value) for value in range(8)))

        assert results == list(range(8))

    asyncio.run(_run())
