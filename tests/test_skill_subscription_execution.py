from __future__ import annotations

import asyncio
import threading
import time

from adaos.services import incident_registry
from adaos.services.skill import subscription_execution


def test_sync_subscription_runs_on_dedicated_worker() -> None:
    subscription_execution.reset_subscription_execution_runtime()
    owner_thread = threading.get_ident()

    async def run() -> int:
        return await subscription_execution.run_sync_subscription(
            threading.get_ident,
            skill="blocking_skill",
            topic="operations.changed",
            handler="handlers.main.on_operations_changed",
        )

    worker_thread = asyncio.run(run())
    snapshot = subscription_execution.subscription_execution_snapshot()

    assert worker_thread != owner_thread
    assert snapshot["active_total"] == 0
    assert snapshot["top_handlers"][0]["completed_total"] == 1


def test_async_subscription_long_await_is_not_attributed_as_blocking(monkeypatch) -> None:
    subscription_execution.reset_subscription_execution_runtime()
    incident_registry.reset_incident_registry()
    monkeypatch.setenv("ADAOS_SKILL_SUBSCRIPTION_BLOCKING_WARN_S", "0.05")

    async def run() -> dict:
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler() -> None:
            started.set()
            await release.wait()

        task = asyncio.create_task(
            subscription_execution.run_async_subscription(
                handler,
                skill="async_skill",
                topic="custom.changed",
                handler="handlers.main.on_changed",
            )
        )
        await started.wait()
        await asyncio.sleep(0.08)
        active = subscription_execution.subscription_execution_snapshot()
        release.set()
        await task
        return active

    try:
        active = asyncio.run(run())
        incidents = incident_registry.incident_registry_snapshot()

        assert active["active_total"] == 1
        assert active["active"][0]["skill"] == "async_skill"
        assert active["active"][0]["execution_mode"] == "async_owner_loop"
        assert incidents["items"] == []
        completed = subscription_execution.subscription_execution_snapshot()["top_handlers"][0]
        assert completed["wall_elapsed_total"] == 1
        assert completed["event_loop_stall_total"] == 0
    finally:
        incident_registry.reset_incident_registry()


def test_async_subscription_attributes_handler_that_blocks_event_loop(monkeypatch) -> None:
    subscription_execution.reset_subscription_execution_runtime()
    incident_registry.reset_incident_registry()
    monkeypatch.setenv("ADAOS_SKILL_SUBSCRIPTION_BLOCKING_WARN_S", "0.05")
    monkeypatch.setenv("ADAOS_SKILL_SUBSCRIPTION_CIRCUIT_BREAKER_S", "0.05")
    handler_active = threading.Event()

    async def blocking_handler() -> None:
        handler_active.set()
        time.sleep(0.08)

    def correlate_stall() -> None:
        assert handler_active.wait(1.0)
        subscription_execution.correlate_runtime_event_loop_stall(
            stack_frames=[
                {
                    "filename": (
                        "/workspace/skills/.runtime/evolved_skill/v1/slots/A/"
                        "src/skills/evolved_skill/handlers.py"
                    ),
                    "lineno": 10,
                    "function": "on_operations_changed",
                }
            ],
            stall_ms=80.0,
            threshold_ms=20.0,
        )

    try:
        correlator = threading.Thread(target=correlate_stall)
        correlator.start()
        asyncio.run(
            subscription_execution.run_async_subscription(
                blocking_handler,
                skill="evolved_skill",
                topic="operations.changed",
                handler="handlers.main.on_operations_changed",
            )
        )
        correlator.join(timeout=1.0)
        execution = subscription_execution.subscription_execution_snapshot()
        incidents = incident_registry.incident_registry_snapshot()

        assert execution["active_total"] == 0
        assert execution["top_handlers"][0]["execution_mode"] == "async_owner_loop"
        assert execution["top_handlers"][0]["blocking_total"] == 1
        assert execution["top_handlers"][0]["event_loop_stall_total"] == 1
        assert "event_loop_stall_circuit_opened" in {item["signal"] for item in incidents["items"]}
        assert {item["domain"] for item in incidents["items"]} == {"skill:evolved_skill"}
        stall_incident = next(item for item in incidents["items"] if item["signal"] == "event_loop_stall_circuit_opened")
        assert stall_incident["severity"] == "degraded"
    finally:
        incident_registry.reset_incident_registry()


def test_async_subscription_circuit_blocks_repeated_slow_handler(monkeypatch) -> None:
    subscription_execution.reset_subscription_execution_runtime()
    incident_registry.reset_incident_registry()
    monkeypatch.setenv("ADAOS_SKILL_SUBSCRIPTION_BLOCKING_WARN_S", "0.05")
    monkeypatch.setenv("ADAOS_SKILL_SUBSCRIPTION_CIRCUIT_BREAKER_S", "0.05")
    monkeypatch.setenv("ADAOS_SKILL_SUBSCRIPTION_CIRCUIT_TTL_S", "30")
    calls = 0
    handler_active = threading.Event()

    async def blocking_handler() -> None:
        nonlocal calls
        calls += 1
        handler_active.set()
        time.sleep(0.08)

    def correlate_stall() -> None:
        assert handler_active.wait(1.0)
        subscription_execution.correlate_runtime_event_loop_stall(
            stack_frames=[
                {
                    "filename": (
                        "/workspace/skills/.runtime/evolved_skill/v1/slots/A/"
                        "src/skills/evolved_skill/handlers.py"
                    ),
                    "lineno": 20,
                    "function": "on_runtime_event",
                }
            ],
            stall_ms=80.0,
            threshold_ms=20.0,
        )

    async def run() -> tuple[object, object]:
        first = await subscription_execution.run_async_subscription(
            blocking_handler,
            skill="evolved_skill",
            topic="subnet.member.link.up",
            handler="handlers.main.on_runtime_event",
        )
        second = await subscription_execution.run_async_subscription(
            blocking_handler,
            skill="evolved_skill",
            topic="subnet.member.link.up",
            handler="handlers.main.on_runtime_event",
        )
        return first, second

    try:
        correlator = threading.Thread(target=correlate_stall)
        correlator.start()
        asyncio.run(run())
        correlator.join(timeout=1.0)
        execution = subscription_execution.subscription_execution_snapshot()

        assert calls == 1
        assert execution["open_circuit_total"] == 1
        assert execution["open_circuits"][0]["skill"] == "evolved_skill"
        assert execution["top_handlers"][0]["circuit_open_total"] == 1
        assert execution["top_handlers"][0]["circuit_rejected_total"] == 1
    finally:
        subscription_execution.reset_subscription_execution_runtime()
        incident_registry.reset_incident_registry()


def test_event_loop_stall_does_not_open_circuit_for_core_handler() -> None:
    subscription_execution.reset_subscription_execution_runtime()

    async def run() -> list[dict]:
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler() -> None:
            started.set()
            await release.wait()

        task = asyncio.create_task(
            subscription_execution.run_async_subscription(
                handler,
                skill="<unknown>",
                topic="sys.ready",
                handler="adaos.services.nlu.teacher_store_runtime._on_sys_ready",
            )
        )
        await started.wait()
        attributed = subscription_execution.correlate_runtime_event_loop_stall(
            stack_frames=[
                {
                    "filename": "/repo/src/adaos/services/nlu/teacher_store_runtime.py",
                    "lineno": 300,
                    "function": "_on_sys_ready",
                }
            ],
            stall_ms=5000.0,
            threshold_ms=250.0,
        )
        release.set()
        await task
        return attributed

    attributed = asyncio.run(run())
    snapshot = subscription_execution.subscription_execution_snapshot()

    assert attributed == []
    assert snapshot["open_circuit_total"] == 0


def test_sync_subscription_reports_active_blocker(monkeypatch) -> None:
    subscription_execution.reset_subscription_execution_runtime()
    incident_registry.reset_incident_registry()
    monkeypatch.setenv("ADAOS_SKILL_SUBSCRIPTION_BLOCKING_WARN_S", "0.05")
    monkeypatch.setattr(incident_registry, "process_activity_history_snapshot", lambda limit=8: {"sample_total": 1})

    async def run() -> dict:
        task = asyncio.create_task(
            subscription_execution.run_sync_subscription(
                lambda: time.sleep(0.15),
                skill="blocking_skill",
                topic="custom.changed",
                handler="handlers.main.on_changed",
            )
        )
        await asyncio.sleep(0.08)
        active = subscription_execution.subscription_execution_snapshot()
        await task
        return active

    try:
        active = asyncio.run(run())
        incidents = incident_registry.incident_registry_snapshot()

        assert active["active_total"] == 1
        assert active["active"][0]["skill"] == "blocking_skill"
        assert incidents["items"][0]["class"] == "skill_handler_pressure"
        assert incidents["items"][0]["domain"] == "skill:blocking_skill"
    finally:
        incident_registry.reset_incident_registry()


def test_cancelled_waiter_keeps_running_worker_admitted(monkeypatch) -> None:
    subscription_execution.reset_subscription_execution_runtime()
    monkeypatch.setenv("ADAOS_SKILL_SUBSCRIPTION_BLOCKING_WARN_S", "5")
    worker_started = threading.Event()
    worker_release = threading.Event()

    def blocking_handler() -> None:
        worker_started.set()
        worker_release.wait(timeout=2.0)

    async def run() -> tuple[dict, dict]:
        task = asyncio.create_task(
            subscription_execution.run_sync_subscription(
                blocking_handler,
                skill="blocking_skill",
                topic="operations.changed",
                handler="handlers.main.on_operations_changed",
            )
        )
        await asyncio.to_thread(worker_started.wait, 1.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        while not task.done():
            await asyncio.sleep(0)
        active_after_cancel = subscription_execution.subscription_execution_snapshot()
        worker_release.set()
        for _ in range(100):
            completed = subscription_execution.subscription_execution_snapshot()
            if completed["active_total"] == 0:
                break
            await asyncio.sleep(0.01)
        return active_after_cancel, completed

    active, completed = asyncio.run(run())

    assert active["active_total"] == 1
    assert active["pending_total"] == 1
    assert completed["active_total"] == 0
    assert completed["pending_total"] == 0
