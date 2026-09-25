from __future__ import annotations

import asyncio
from types import SimpleNamespace

from adaos.services.applications import auto_update_runtime as runtime


class _Bus:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


def test_runtime_ready_schedules_registry_sync_on_running_loop(monkeypatch) -> None:
    bus = _Bus()
    ctx = SimpleNamespace(bus=bus)
    calls = []
    monkeypatch.setattr(runtime, "get_ctx", lambda: ctx)
    monkeypatch.setattr(runtime, "_startup_delay_s", lambda: 0.0)
    monkeypatch.setattr(
        runtime,
        "sync_workspace_sparse_to_registry",
        lambda observed: calls.append(observed)
        or {
            "ok": True,
            "application_auto_update": {
                "status": "completed",
                "applied_count": 1,
            },
        },
    )
    runtime._TASK = None
    runtime._PENDING_TRIGGER = None

    async def exercise() -> None:
        await runtime.on_runtime_ready(None)
        assert runtime._TASK is not None
        await runtime._TASK

    asyncio.run(exercise())

    assert calls == [ctx]
    assert len(bus.events) == 1
    assert bus.events[0].type == "applications.auto_update.completed"
    assert bus.events[0].payload["trigger"] == "sys.ready"
    assert bus.events[0].payload["result"]["applied_count"] == 1


def test_runtime_registry_event_coalesces_while_sync_is_running(monkeypatch) -> None:
    bus = _Bus()
    ctx = SimpleNamespace(bus=bus)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []
    monkeypatch.setattr(runtime, "get_ctx", lambda: ctx)
    runtime._TASK = None
    runtime._PENDING_TRIGGER = None

    def sync(observed):
        calls.append(observed)
        if len(calls) == 1:
            started_loop.call_soon_threadsafe(started.set)
            asyncio.run_coroutine_threadsafe(release.wait(), started_loop).result()
        return {"ok": True, "application_auto_update": {"status": "completed"}}

    monkeypatch.setattr(runtime, "sync_workspace_sparse_to_registry", sync)

    async def exercise() -> None:
        nonlocal started_loop
        started_loop = asyncio.get_running_loop()
        await runtime.on_application_registry_updated(None)
        await started.wait()
        await runtime.on_application_registry_updated(None)
        release.set()
        assert runtime._TASK is not None
        await runtime._TASK

    started_loop = None
    asyncio.run(exercise())

    assert len(calls) == 2
    assert [event.payload["trigger"] for event in bus.events] == [
        "applications.registry.updated",
        "applications.registry.updated",
    ]
