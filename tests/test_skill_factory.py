from __future__ import annotations

from pathlib import Path

from adaos.services.builder import BuilderWorkspaceService
from adaos.services.skill_factory import REALIZE_REQUEST_SCHEMA, SkillFactoryService


def _builder_service(tmp_path: Path) -> BuilderWorkspaceService:
    workspace = tmp_path / "workspace"
    return BuilderWorkspaceService(
        state_dir=tmp_path / "state",
        repo_root=tmp_path,
        workspace_root=workspace,
        skills_root=workspace / "skills",
        scenarios_root=workspace / "scenarios",
        dev_skills_root=tmp_path / "dev" / "test-subnet" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "test-subnet" / "scenarios",
    )


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

    registered = service.register_dev_node({"node_id": "devnode.test"})
    assert registered["registration"]["status"] == "registered_waiting"

    assignment_result = service.poll_assignment("devnode.test")
    assignment = assignment_result["assignment"]
    assert assignment_result["assigned"] is True
    assert assignment["schema"] == "adaos.skill_factory.dev_task_assignment.v1"
    assert assignment["forge"]["branch"] == task["forge"]["branch"]
    assert assignment["forge"]["branch_creator"] == "dev_node"

    internal_path = next(path for path in assignment["forge"]["sparse_paths"] if path.startswith(".adaos/tasks/"))
    completed = service.complete_task(
        {
            "task_id": task["task_id"],
            "node_id": "devnode.test",
            "status": "completed",
            "commit_hash": "abc123",
            "branch": assignment["forge"]["branch"],
            "changed_paths": [
                "skills/shopping_list/skill.yaml",
                f"{internal_path}result.json",
            ],
            "tests": {"status": "passed", "command": "pytest"},
        }
    )
    assert completed["task"]["status"] == "completed"
    assert completed["ready_event"]["schema"] == "adaos.skill_factory.dev_ready_event.v1"
    assert completed["ready_event"]["next_action"] == ["pull_revision", "validate_locally", "show_to_user"]


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
                "changed_paths": ["skills/other/skill.yaml"],
            }
        )
    except ValueError as exc:
        assert "outside the task sparse checkout" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected result path validation failure")


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

    response = invoke_tool(
        "skill_factory.get_status",
        arguments={"include_tasks": False},
        actor="test",
        auth_method="owner_token",
        auth_context={"capabilities": ["*"]},
    )
    assert response.ok is True
    assert response.result["skill_factory"]["queue"]["queued"] == 0
