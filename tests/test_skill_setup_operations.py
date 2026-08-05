from __future__ import annotations

from pathlib import Path

from adaos.services.skill.setup_operations import SetupOperationService


class Manager:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def runtime_status(self, _name: str) -> dict:
        return {"ready": True, "active": True, "deactivated": False}

    def setup_skill(self, name: str) -> dict:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable after first setup step")
        return {"ok": True, "skill": name}


def test_setup_operation_is_approval_gated_durable_and_idempotent(tmp_path: Path) -> None:
    published: list[dict] = []
    service = SetupOperationService(state_dir=tmp_path)
    manager = Manager()
    created = service.create(
        skill_id="example_skill",
        release_digest="sha256:" + "1" * 64,
        plan_digest="sha256:" + "2" * 64,
        webspace_id="desktop",
        manager=manager,
        pending_action_publisher=lambda **payload: published.append(payload) or payload,
    )
    assert created["operation"]["status"] == "approval_pending"
    assert published[0]["kind"] == "skill.setup.approval"
    assert manager.calls == 0

    result = service.approve_and_execute(
        created["operation"]["operation_id"],
        approval_id="pa.setup.1",
        approved_by="user:owner",
        manager=manager,
    )
    assert result["operation"]["status"] == "completed"
    assert manager.calls == 1
    duplicate = service.approve_and_execute(
        created["operation"]["operation_id"],
        approval_id="pa.setup.1",
        approved_by="user:owner",
        manager=manager,
    )
    assert duplicate["duplicate"] is True
    assert manager.calls == 1


def test_setup_failure_requires_explicit_retry_and_restart_never_reexecutes(tmp_path: Path) -> None:
    service = SetupOperationService(state_dir=tmp_path)
    manager = Manager(fail=True)
    operation = service.create(
        skill_id="example_skill",
        release_digest="sha256:" + "3" * 64,
        plan_digest="sha256:" + "4" * 64,
        webspace_id="desktop",
        manager=manager,
        pending_action_publisher=lambda **payload: payload,
    )["operation"]
    failed = service.approve_and_execute(
        operation["operation_id"],
        approval_id="pa.setup.2",
        approved_by="user:owner",
        manager=manager,
    )["operation"]
    assert failed["status"] == "failed"
    assert failed["error"]["partial_failure"] is True
    assert failed["recovery"]["automatic_retry"] is False

    failed["status"] = "running"
    service._write_locked(failed)
    recovered = SetupOperationService(state_dir=tmp_path).recover_interrupted()
    assert recovered[0]["status"] == "input_required"
    assert recovered[0]["recovery"]["automatic_retry"] is False
    assert manager.calls == 1

