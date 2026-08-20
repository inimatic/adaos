from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from adaos.services.builder import BuilderWorkspaceService
from adaos.services.skill_factory import REALIZE_REQUEST_SCHEMA, SkillFactoryService


def _builder_service(tmp_path: Path) -> BuilderWorkspaceService:
    workspace = tmp_path / "workspace"
    dev_skills = tmp_path / "dev" / "test-subnet" / "skills"
    dev_scenarios = tmp_path / "dev" / "test-subnet" / "scenarios"

    class _DeveloperService:
        def _create(self, kind: str, name: str, template: str | None):
            package_root = Path(__file__).resolve().parents[1] / "src" / "adaos"
            source = package_root / ("skills_templates" if kind == "skill" else "scenario_templates") / str(template)
            target = (dev_skills if kind == "skill" else dev_scenarios) / name
            shutil.copytree(source, target)
            return SimpleNamespace(path=target, name=name)

        def create_skill(self, name: str, template: str | None = None):
            return self._create("skill", name, template or "skill_default")

        def create_scenario(self, name: str, template: str | None = None):
            return self._create("scenario", name, template or "scenario_default")

    return BuilderWorkspaceService(
        state_dir=tmp_path / "state",
        repo_root=tmp_path,
        workspace_root=workspace,
        skills_root=workspace / "skills",
        scenarios_root=workspace / "scenarios",
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        developer_service=_DeveloperService(),
    )


def _dev_result(
    *,
    task_id: str,
    assignment: dict,
    node_id: str,
    commit_hash: str = "abc123",
    changed_paths: list[str],
    dependency_changes: list[dict] | None = None,
) -> dict:
    evidence_paths = assignment["evidence"]["expected_paths"]
    evidence_artifacts = [
        {
            "kind": kind,
            "logical_path": evidence_paths[kind],
            "digest": "sha256:" + token * 64,
            "size_bytes": 1,
            "media_type": "application/json" if kind != "changed_files" else "text/plain",
        }
        for kind, token in zip(
            ("result", "test_report", "changed_files", "provenance"),
            ("1", "2", "3", "4"),
        )
    ]
    return {
        "task_id": task_id,
        "node_id": node_id,
        "status": "completed",
        "commit_hash": commit_hash,
        "branch": assignment["forge"]["branch"],
        "changed_paths": list(changed_paths),
        "tests": {"status": "passed", "command": "pytest"},
        "provenance": {
            "runner_version": "pytest-runner/1.0",
            "image_digest": "sha256:test-image",
            "instruction_packet_hash": "sha256:test-instructions",
            "dependency_changes": dependency_changes or [],
        },
        "evidence": {
            "schema": "adaos.skill_factory.task_evidence_manifest.v1",
            "storage": "worker_task_envelope",
            "artifacts": evidence_artifacts,
        },
    }


def test_skill_factory_queue_assigns_and_accepts_valid_result(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)
    submitted = service.submit_realize_request(
        {
            "user_subnet_id": "subnet-test",
            "target": {"type": "skill", "id": "shopping_list"},
            "source": {"type": "builder", "text": "Build a local shopping list skill."},
        }
    )
    task = submitted["task"]
    assert task["status"] == "queued"
    assert task["forge"]["branch"].startswith("realize/")
    assert "skills/shopping_list/" in task["forge"]["sparse_paths"]
    assert "docs/requirements/shopping_list/" in task["forge"]["sparse_paths"]
    assert not any(path.startswith(".adaos/tasks/") for path in task["forge"]["sparse_paths"])

    registered = service.register_dev_node({"node_id": "devnode.test"})
    assert registered["registration"]["status"] == "registered_waiting"

    assignment_result = service.poll_assignment("devnode.test")
    assignment = assignment_result["assignment"]
    assert assignment_result["assigned"] is True
    assert assignment["schema"] == "adaos.skill_factory.dev_task_assignment.v1"
    assert assignment["forge"]["branch"] == task["forge"]["branch"]
    assert assignment["forge"]["branch_creator"] == "dev_node"

    completed = service.complete_task(
        _dev_result(
            task_id=task["task_id"],
            assignment=assignment,
            node_id="devnode.test",
            changed_paths=[
                "skills/shopping_list/skill.yaml",
            ],
        )
    )
    assert completed["task"]["status"] == "completed"
    assert completed["ready_event"]["schema"] == "adaos.skill_factory.dev_ready_event.v1"
    assert completed["ready_event"]["next_action"] == ["pull_revision", "validate_locally", "show_to_user"]
    assert completed["task"]["provenance"]["schema"] == "adaos.skill_factory.task_provenance.v1"


def test_targeted_poll_assigns_the_requested_task_not_an_older_queue_item(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)
    older = service.submit_realize_request(
        {
            "request_id": "realize.test.older",
            "target": {"type": "scenario", "id": "older"},
        }
    )["task"]
    requested = service.submit_realize_request(
        {
            "request_id": "realize.test.requested",
            "target": {"type": "scenario", "id": "requested"},
        }
    )["task"]
    service.register_dev_node({"node_id": "devnode.targeted"})

    assignment = service.poll_assignment(
        "devnode.targeted",
        task_id=requested["task_id"],
    )

    assert assignment["assigned"] is True
    assert assignment["assignment"]["task_id"] == requested["task_id"]
    tasks = {
        item["task_id"]: item
        for item in service.snapshot(include_tasks=True)["tasks"]
    }
    assert tasks[older["task_id"]]["status"] == "queued"
    assert tasks[requested["task_id"]]["status"] == "assigned"


def test_skill_factory_fails_closed_on_corrupt_authoritative_state(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)
    service.state_path.parent.mkdir(parents=True, exist_ok=True)
    service.state_path.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="failed to read Skill Factory state"):
        service.snapshot(include_tasks=True)


def test_skill_factory_rejects_result_outside_sparse_paths(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)
    task = service.submit_realize_request({"target": {"type": "scenario", "id": "morning"}})["task"]
    service.register_dev_node({"node_id": "devnode.test"})
    assignment = service.poll_assignment("devnode.test")["assignment"]

    try:
        service.complete_task(
            {
                "task_id": task["task_id"],
                "node_id": "devnode.test",
                "status": "completed",
                "commit_hash": "abc123",
                "branch": assignment["forge"]["branch"],
                "changed_paths": [
                    "skills/other/skill.yaml",
                    assignment["evidence"]["expected_paths"]["provenance"],
                ],
                "provenance": {"runner_version": "pytest-runner/1.0"},
            }
        )
    except ValueError as exc:
        assert "outside the task sparse checkout" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected result path validation failure")


def test_realize_request_adds_policy_and_snapshot_context(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)

    request = service.normalize_realize_request(
        {
            "target": {"type": "connector", "id": "github_sync"},
            "constraints": {"new_dependencies": ["httpx"], "no_external_api": False},
            "artifacts": {"requirement_spec_id": "req.github_sync.v1"},
            "mock_fixture_ids": ["fixture.github_sync.empty"],
        }
    )

    policy = request["realization_policy"]
    assert policy["schema"] == "adaos.skill_factory.realization_policy.v1"
    assert policy["classification"] == "manual_only"
    assert policy["manual_approval_required"] is True
    assert "manual_only:external_io" in policy["reasons"]
    assert "manual_only:new_dependencies" in policy["reasons"]

    context = request["snapshot_context"]
    assert context["schema"] == "adaos.skill_factory.task_context.v1"
    assert context["privacy"]["secrets_absent"] is True
    assert context["privacy"]["raw_user_data_absent"] is True
    assert context["mock_data"]["deterministic"] is True
    assert context["mock_data"]["fixture_ids"] == ["fixture.github_sync.empty"]
    assert context["provenance"] == [{"kind": "requirement_spec_id", "ref": "req.github_sync.v1"}]

    blocked = service.normalize_realize_request(
        {
            "target": {"type": "skill", "id": "secret_reader"},
            "constraints": {"read_secrets": True},
        }
    )
    assert blocked["realization_policy"]["classification"] == "disallowed"
    assert blocked["realization_policy"]["disallowed"] is True


def test_skill_factory_result_records_dependency_delta(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)
    task = service.submit_realize_request({"target": {"type": "skill", "id": "dep_demo"}})["task"]
    service.register_dev_node({"node_id": "devnode.test"})
    assignment = service.poll_assignment("devnode.test")["assignment"]

    completed = service.complete_task(
        _dev_result(
            task_id=task["task_id"],
            assignment=assignment,
            node_id="devnode.test",
            changed_paths=[
                "skills/dep_demo/skill.yaml",
                "skills/dep_demo/pyproject.toml",
            ],
            dependency_changes=[{"name": "httpx", "action": "add"}],
        )
    )

    delta = completed["task"]["dependency_delta"]
    assert delta["changed"] is True
    assert delta["review_required"] is True
    assert "skills/dep_demo/pyproject.toml" in delta["files"]
    assert completed["task"]["provenance"]["dependency_changes"] == [{"name": "httpx", "action": "add"}]


def test_skill_factory_idempotent_completion_survives_restart(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)
    task = service.submit_realize_request({"target": {"type": "skill", "id": "restart_demo"}})["task"]
    service.register_dev_node({"node_id": "devnode.test"})
    assignment = service.poll_assignment("devnode.test")["assignment"]
    result = _dev_result(
        task_id=task["task_id"],
        assignment=assignment,
        node_id="devnode.test",
        changed_paths=["skills/restart_demo/skill.yaml"],
    )

    completed = service.complete_task(result)
    duplicate = service.complete_task(result)
    restarted = SkillFactoryService(state_dir=tmp_path).snapshot(include_tasks=True)

    assert completed["task"]["status"] == "completed"
    assert duplicate["duplicate"] is True
    persisted = {item["task_id"]: item for item in restarted["tasks"]}
    assert persisted[task["task_id"]]["status"] == "completed"
    assert restarted["ready_events"][0]["task_id"] == task["task_id"]


def test_skill_factory_cancel_is_terminal_for_late_worker_updates(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)
    task = service.submit_realize_request({"target": {"type": "skill", "id": "cancel_demo"}})["task"]
    service.register_dev_node({"node_id": "devnode.test"})
    assignment = service.poll_assignment("devnode.test")["assignment"]
    service.report_progress(
        task["task_id"],
        {"node_id": "devnode.test", "status": "in_progress", "message": "working"},
    )

    cancelled = service.cancel_task(task["task_id"], reason="user cancelled", actor="test")

    assert cancelled["task"]["status"] == "cancelled"
    with pytest.raises(ValueError, match="is terminal: cancelled"):
        service.report_progress(
            task["task_id"],
            {"node_id": "devnode.test", "status": "tests_running", "message": "late update"},
        )
    with pytest.raises(ValueError, match="is terminal: cancelled"):
        service.complete_task(
            _dev_result(
                task_id=task["task_id"],
                assignment=assignment,
                node_id="devnode.test",
                changed_paths=["skills/cancel_demo/skill.yaml"],
            )
        )
    persisted = {item["task_id"]: item for item in service.snapshot(include_tasks=True)["tasks"]}
    assert persisted[task["task_id"]]["status"] == "cancelled"


def test_skill_factory_cannot_cancel_completed_or_committing_task(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)
    task = service.submit_realize_request({"target": {"type": "skill", "id": "terminal_demo"}})["task"]
    service.register_dev_node({"node_id": "devnode.test"})
    assignment = service.poll_assignment("devnode.test")["assignment"]
    service.report_progress(
        task["task_id"],
        {"node_id": "devnode.test", "status": "commit_ready", "message": "committing"},
    )

    committing = service.cancel_task(task["task_id"])
    assert committing["ok"] is False
    assert committing["terminal"] is False
    assert committing["task"]["status"] == "commit_ready"

    service.complete_task(
        _dev_result(
            task_id=task["task_id"],
            assignment=assignment,
            node_id="devnode.test",
            changed_paths=["skills/terminal_demo/skill.yaml"],
        )
    )
    completed = service.cancel_task(task["task_id"])
    assert completed["ok"] is False
    assert completed["terminal"] is True
    assert completed["task"]["status"] == "completed"


def test_skill_factory_recovers_validated_result_without_requeue(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)
    task = service.submit_realize_request({"target": {"type": "skill", "id": "recover_demo"}})["task"]
    service.register_dev_node({"node_id": "devnode.test"})
    assignment = service.poll_assignment("devnode.test")["assignment"]
    service.fail_task(
        {
            "task_id": task["task_id"],
            "node_id": "devnode.test",
            "message": "post-commit activation failed",
            "retryable": True,
        }
    )
    result = _dev_result(
        task_id=task["task_id"],
        assignment=assignment,
        node_id="devnode.test",
        changed_paths=["skills/recover_demo/skill.yaml"],
    )

    recovered = service.recover_task_result(
        {
            **result,
            "recovery": {
                "reason": "activate preserved validated result",
                "validated_run_dir": str(tmp_path / "runs" / task["task_id"]),
                "actor": "test",
            },
        }
    )

    assert recovered["recovered"] is True
    assert recovered["task"]["status"] == "completed"
    assert recovered["task"]["attempts"] == 1
    assert recovered["task"]["result_recovery_history"][-1]["failure_id"]
    assert len(recovered["task"]["failure_history"]) == 1


def test_skill_factory_operator_controls_pause_drain_quarantine_and_retry(tmp_path: Path) -> None:
    service = SkillFactoryService(state_dir=tmp_path)
    task = service.submit_realize_request({"target": {"type": "skill", "id": "ops_demo"}})["task"]

    service.register_dev_node({"node_id": "devnode.pause"})
    paused = service.set_queue_paused(paused=True, reason="maintenance", actor="operator")
    assert paused["queue"]["paused"] is True
    assert service.poll_assignment("devnode.pause")["reason"] == "queue_paused"

    service.set_queue_paused(paused=False, actor="operator")
    service.drain_dev_node("devnode.pause", reason="retire", actor="operator")
    assert service.poll_assignment("devnode.pause")["reason"] == "node_draining"

    service.register_dev_node({"node_id": "devnode.quarantine"})
    service.quarantine_dev_node("devnode.quarantine", reason="bad image", actor="operator")
    assert service.poll_assignment("devnode.quarantine")["reason"] == "node_quarantined"

    service.register_dev_node({"node_id": "devnode.revoke"})
    revoked = service.revoke_dev_node_credentials("devnode.revoke", reason="lost token", actor="operator")
    assert revoked["node"]["credentials_revoked"] is True
    assert service.poll_assignment("devnode.revoke")["reason"] == "credentials_revoked"
    try:
        service.report_progress(task["task_id"], {"node_id": "devnode.revoke", "status": "in_progress"})
    except ValueError as exc:
        assert "credentials are revoked" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected revoked node report rejection")

    service.register_dev_node({"node_id": "devnode.worker"})
    assignment = service.poll_assignment("devnode.worker")["assignment"]
    service.fail_task(
        {
            "task_id": task["task_id"],
            "node_id": "devnode.worker",
            "message": "test failure",
            "retryable": True,
        }
    )

    retried = service.retry_task(task["task_id"], reason="operator retry", actor="operator")
    assert retried["task"]["status"] == "queued"
    assert "devnode.worker" in retried["task"]["avoid_node_ids"]
    assert service.poll_assignment("devnode.worker")["reason"] == "queue_empty"

    service.register_dev_node({"node_id": "devnode.worker2"})
    reassigned = service.poll_assignment("devnode.worker2")
    assert reassigned["assigned"] is True
    assert reassigned["assignment"]["task_id"] == assignment["task_id"]


def test_builder_realize_request_preserves_local_fallback(tmp_path: Path) -> None:
    service = _builder_service(tmp_path)
    draft_result = service.create_draft(
        kind="skill",
        artifact_id="demo_realize",
        source_idea="Create a demo skill that can be realized remotely.",
        webspace_id="builder-test",
    )

    result = service.create_realize_request(
        draft_id=draft_result["draft"]["draft_id"],
        user_subnet_id="subnet-test",
        submit_remote=False,
        create_pending_action=False,
    )

    request = result["realize_request"]
    assert result["mode"] == "local_fallback"
    assert result["remote_submitted"] is False
    assert request["schema"] == REALIZE_REQUEST_SCHEMA
    assert request["target"] == {"type": "skill", "id": "demo_realize"}
    assert "skills/demo_realize/" in request["repo"]["sparse_paths"]
    assert Path(result["request_dir"], "realize_request.json").exists()


def test_root_mcp_exposes_skill_factory_plane_and_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))

    from adaos.services.root_mcp.service import invoke_tool, list_tool_contracts

    contracts = list_tool_contracts(plane_id="skill_factory_task")
    contract_ids = {item.id for item in contracts}
    assert "skill_factory.get_status" in contract_ids
    assert "skill_factory.submit_realize_request" in contract_ids
    assert "skill_factory.poll_assignment" in contract_ids
    assert "skill_factory.set_queue_paused" in contract_ids
    assert "skill_factory.drain_dev_node" in contract_ids
    assert "skill_factory.quarantine_dev_node" in contract_ids
    assert "skill_factory.revoke_dev_node_credentials" in contract_ids
    assert "skill_factory.retry_task" in contract_ids

    response = invoke_tool(
        "skill_factory.get_status",
        arguments={"include_tasks": False},
        actor="test",
        auth_method="owner_token",
        auth_context={"capabilities": ["*"]},
    )
    assert response.ok is True
    assert response.result["skill_factory"]["queue"]["queued"] == 0
