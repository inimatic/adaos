from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.skill_factory import SkillFactoryService
from adaos.services.skill_factory_user_hub import UserHubResultService, UserHubSubmissionError


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "skill_factory"


def _result(task: dict, assignment: dict, *, changed_paths: list[str] | None = None) -> dict:
    expected = assignment["evidence"]["expected_paths"]
    paths = list(changed_paths or [f"skills/{task['target']['id']}/skill.yaml"])
    if expected["provenance"] not in paths:
        paths.append(expected["provenance"])
    return {
        "schema": "adaos.skill_factory.dev_result.v1",
        "task_id": task["task_id"],
        "node_id": "devnode.golden",
        "status": "completed",
        "commit_hash": "abc1234",
        "branch": assignment["forge"]["branch"],
        "changed_paths": paths,
        "tests": {"status": "passed"},
        "validation": {"status": "passed"},
        "provenance": {"runner_version": "golden-fixture/1"},
    }


@pytest.mark.parametrize("fixture_path", sorted(FIXTURE_DIR.glob("*.json")), ids=lambda path: path.stem)
def test_skill_factory_golden_task_fixture(tmp_path: Path, fixture_path: Path) -> None:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["schema"] == "adaos.skill_factory.golden_task.v1"
    service = SkillFactoryService(state_dir=tmp_path / fixture["id"])
    task = service.submit_realize_request(fixture["request"])["task"]
    service.register_dev_node({"node_id": "devnode.golden"})
    assignment = service.poll_assignment("devnode.golden")["assignment"]
    operation = fixture["operation"]

    if operation == "complete":
        completed = service.complete_task(_result(task, assignment))
        assert completed["task"]["status"] == fixture["expected"]["status"]
        assert bool(completed.get("ready_event")) is fixture["expected"]["ready_event"]
        return
    if operation == "fail_tests":
        failed = service.fail_task(
            {
                "task_id": task["task_id"],
                "node_id": "devnode.golden",
                "failure_class": "tests_failed",
                "stage": "tests_running",
                "message": "focused tests failed",
                "retryable": False,
            }
        )
        assert failed["task"]["status"] == fixture["expected"]["status"]
        assert failed["failure"]["failure_class"] == fixture["expected"]["failure_class"]
        from adaos.services.builder.repair import BuilderRepairService

        repairs = BuilderRepairService(state_dir=service.state_dir).task_context(
            task["target"]["id"]
        )
        assert repairs["active_count"] == 1
        assert repairs["tasks"][0]["signal_type"] == "test_failure"
        return
    if operation == "complete_forbidden_path":
        with pytest.raises(ValueError, match=fixture["expected"]["error"]):
            service.complete_task(
                _result(task, assignment, changed_paths=[fixture["input"]["changed_path"]])
            )
        return
    if operation == "use_forbidden_mcp_scope":
        with pytest.raises(ValueError, match=fixture["expected"]["error"]):
            service.validate_task_access_lease(
                assignment["mcp"]["access_token"],
                task_id=task["task_id"],
                node_id="devnode.golden",
                scope=fixture["input"]["scope"],
            )
        return
    if operation == "cancel":
        cancelled = service.cancel_task(task["task_id"], actor="user:fixture", reason="golden")
        assert cancelled["task"]["status"] == fixture["expected"]["status"]
        with pytest.raises(ValueError, match=fixture["expected"]["late_progress_error"]):
            service.report_progress(
                task["task_id"],
                {"node_id": "devnode.golden", "status": "in_progress"},
            )
        return
    if operation == "stage_bad_user_hub_digest":
        body = json.dumps(_result(task, assignment), sort_keys=True).encode("utf-8")
        user_hub = UserHubResultService(factory=service, state_dir=tmp_path / fixture["id"])
        with pytest.raises(UserHubSubmissionError, match=fixture["expected"]["error"]):
            user_hub.fetch_validate_stage(
                task_id=task["task_id"],
                source_url="https://user-hub.example/result.json",
                source_digest="sha256:" + "0" * 64,
                webspace_id="desktop",
                fetcher=lambda _url: body,
                pending_action_publisher=lambda **payload: payload,
            )
        current = next(item for item in service.snapshot()["tasks"] if item["task_id"] == task["task_id"])
        assert current["status"] == fixture["expected"]["status"]
        return
    raise AssertionError(f"unsupported golden operation: {operation}")
