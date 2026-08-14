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


def test_async_subscription_reports_active_handler_and_owner_loop_budget(monkeypatch) -> None:
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
        assert incidents["items"][0]["domain"] == "skill:async_skill"
    finally:
        incident_registry.reset_incident_registry()


def test_async_subscription_attributes_handler_that_blocks_event_loop(monkeypatch) -> None:
    subscription_execution.reset_subscription_execution_runtime()
    incident_registry.reset_incident_registry()
    monkeypatch.setenv("ADAOS_SKILL_SUBSCRIPTION_BLOCKING_WARN_S", "0.05")

    async def blocking_handler() -> None:
        time.sleep(0.08)

    try:
        asyncio.run(
            subscription_execution.run_async_subscription(
                blocking_handler,
                skill="evolved_skill",
                topic="operations.changed",
                handler="handlers.main.on_operations_changed",
            )
        )
        execution = subscription_execution.subscription_execution_snapshot()
        incidents = incident_registry.incident_registry_snapshot()

        assert execution["active_total"] == 0
        assert execution["top_handlers"][0]["execution_mode"] == "async_owner_loop"
        assert execution["top_handlers"][0]["blocking_total"] == 1
        assert incidents["items"][0]["signal"] == "async_execution_budget_exceeded"
        assert incidents["items"][0]["domain"] == "skill:evolved_skill"
    finally:
        incident_registry.reset_incident_registry()


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
