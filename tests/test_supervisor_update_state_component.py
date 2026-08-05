from __future__ import annotations

import asyncio

import pytest

from adaos.apps.supervisor_runtime import UpdateStateMachine


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
