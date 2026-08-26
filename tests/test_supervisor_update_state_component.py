from __future__ import annotations

import asyncio

import pytest

from adaos.apps.supervisor_runtime import UpdateStateMachine
from adaos.apps.supervisor_runtime.config import SupervisorRuntimeConfig


def test_update_state_machine_keeps_root_promotion_non_terminal() -> None:
    machine = UpdateStateMachine()

    assert machine.is_terminal({"state": "validated", "phase": "root_promotion_pending"}) is False
    assert machine.is_terminal({"state": "validated", "phase": "validate"}) is True


def test_update_state_machine_requires_new_generation_for_root_restart_finalize() -> None:
    machine = UpdateStateMachine()
    status = {
        "state": "succeeded",
        "phase": "root_promoted",
        "target_slot": "B",
        "root_promotion_supervisor_instance_id": "old",
    }
    runtime = {"runtime_state": "ready", "active_slot": "B"}

    assert machine.runtime_ready_for_boot_finalize(status, runtime, current_instance_id="new") is True
    assert machine.runtime_ready_for_boot_finalize(status, runtime, current_instance_id="old") is False


def test_update_state_machine_recognizes_resolved_target() -> None:
    machine = UpdateStateMachine()

    assert machine.transition_request_has_resolved_target({"action": "update", "target_version": "abc1234"})
    assert not machine.transition_request_has_resolved_target({"action": "update", "target_version": "latest"})
    assert machine.transition_request_has_resolved_target({"action": "rollback"})


def test_windows_self_restart_requires_managed_restartable_wrapper(monkeypatch) -> None:
    monkeypatch.setattr("adaos.apps.supervisor_runtime.config.os.name", "nt")
    monkeypatch.setenv("ADAOS_AUTOSTART_MANAGED", "1")
    monkeypatch.delenv("ADAOS_AUTOSTART_SELF_RESTART", raising=False)

    assert SupervisorRuntimeConfig.autostart_self_restart_supported() is False

    monkeypatch.setenv("ADAOS_AUTOSTART_SELF_RESTART", "1")
    assert SupervisorRuntimeConfig.autostart_self_restart_supported() is True


def test_supervisor_update_timeouts_allow_slow_deployment_environments(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ADAOS_SUPERVISOR_UPDATE_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("ADAOS_SUPERVISOR_PREPARE_TIMEOUT_SEC", raising=False)
    config = SupervisorRuntimeConfig()

    assert config.update_attempt_timeout_sec() == 900.0
    assert config.update_prepare_timeout_sec() == 3600.0

    monkeypatch.setenv("ADAOS_SUPERVISOR_UPDATE_TIMEOUT_SEC", "1200")
    monkeypatch.setenv("ADAOS_SUPERVISOR_PREPARE_TIMEOUT_SEC", "600")
    assert config.update_attempt_timeout_sec() == 1200.0
    assert config.update_prepare_timeout_sec() == 1200.0


@pytest.mark.anyio
async def test_update_state_machine_owns_worker_lifecycle() -> None:
    machine = UpdateStateMachine()
    release = asyncio.Event()

    async def _worker() -> None:
        await release.wait()

    task = machine.start_task("supervisor-update", _worker)

    assert machine.task is task
    assert machine.task_running() is True
    assert await machine.cancel_task(mode="rescheduled") is True
    assert task.cancelled()
    assert machine.task is None
    assert machine.cancel_mode is None


def test_update_state_machine_persists_linked_status_and_attempt() -> None:
    statuses: list[dict] = []
    attempts: list[dict] = []
    machine = UpdateStateMachine()
    machine.bind_persistence(
        write_status=lambda payload: statuses.append({**payload, "revision": 2}) or statuses[-1],
        write_attempt=lambda payload: attempts.append(payload),
    )

    status = machine.persist_transition(
        status_payload={"state": "countdown"},
        attempt_payload=lambda persisted: {"state": "active", "last_status": persisted},
    )

    assert status["revision"] == 2
    assert attempts == [{"state": "active", "last_status": status}]
