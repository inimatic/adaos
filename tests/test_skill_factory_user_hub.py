from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from adaos.services.skill_factory import SkillFactoryService
from adaos.services.skill_factory_user_hub import UserHubResultService, UserHubSubmissionError


def _assigned(tmp_path: Path) -> tuple[SkillFactoryService, dict, dict]:
    factory = SkillFactoryService(state_dir=tmp_path)
    task = factory.submit_realize_request(
        {
            "request_id": "realize.user-hub.example",
            "target": {"type": "skill", "id": "example_skill"},
            "mcp": {
                "requested_scope": ["requirements", "staging_validation"],
                "credential_refs": ["secret:provider-test"],
            },
        }
    )["task"]
    factory.register_dev_node({"node_id": "devnode.user-hub"})
    assignment = factory.poll_assignment("devnode.user-hub")["assignment"]
    return factory, task, assignment


def _result(task: dict, assignment: dict) -> dict:
    return {
        "schema": "adaos.skill_factory.dev_result.v1",
        "task_id": task["task_id"],
        "node_id": "devnode.user-hub",
        "status": "completed",
        "commit_hash": "abc1234",
        "branch": assignment["forge"]["branch"],
        "changed_paths": [
            "skills/example_skill/skill.yaml",
            assignment["evidence"]["expected_paths"]["provenance"],
        ],
        "tests": {"status": "passed"},
        "validation": {"status": "passed"},
        "provenance": {
            "schema": "adaos.skill_factory.task_provenance.v1",
            "task_id": task["task_id"],
            "dev_node_id": "devnode.user-hub",
            "runner_version": "user-hub/1",
            "image_digest": None,
            "instruction_packet_hash": None,
            "dependency_changes": [],
            "snapshot_refs": [],
            "reported_at": "2026-08-05T00:00:00+00:00",
        },
    }


def test_task_access_lease_is_node_scope_credential_bound_and_revoked(tmp_path: Path) -> None:
    factory, task, assignment = _assigned(tmp_path)
    mcp = assignment["mcp"]
    assert mcp["access_token"].startswith("sf_task_")
    assert mcp["credential_refs"] == ["secret:provider-test"]
    admitted = factory.validate_task_access_lease(
        mcp["access_token"],
        task_id=task["task_id"],
        node_id="devnode.user-hub",
        scope="read_requirements",
        credential_ref="secret:provider-test",
    )
    assert admitted["ok"] is True
    with pytest.raises(ValueError, match="another dev node"):
        factory.validate_task_access_lease(
            mcp["access_token"],
            task_id=task["task_id"],
            node_id="devnode.other",
            scope="read_requirements",
        )
    with pytest.raises(ValueError, match="outside the task lease"):
        factory.validate_task_access_lease(
            mcp["access_token"],
            task_id=task["task_id"],
            node_id="devnode.user-hub",
            scope="read_requirements",
            credential_ref="secret:production",
        )
    factory.cancel_task(task["task_id"], actor="user:owner", reason="test cancellation")
    with pytest.raises(ValueError, match="revoked"):
        factory.validate_task_access_lease(
            mcp["access_token"],
            task_id=task["task_id"],
            node_id="devnode.user-hub",
            scope="read_requirements",
        )


def test_user_hub_result_is_validated_staged_and_requires_approval(tmp_path: Path) -> None:
    factory, task, assignment = _assigned(tmp_path)
    result = _result(task, assignment)
    body = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
    pending: list[dict] = []
    service = UserHubResultService(factory=factory, state_dir=tmp_path)
    submission = service.fetch_validate_stage(
        task_id=task["task_id"],
        source_url="https://user-hub.example/results/result.json",
        source_digest=digest,
        webspace_id="desktop",
        fetcher=lambda _url: body,
        pending_action_publisher=lambda **payload: pending.append(payload) or payload,
    )
    assert submission["status"] == "approval_pending"
    assert pending[0]["kind"] == "skill_factory.user_hub_result.review"
    current = next(item for item in factory.snapshot()["tasks"] if item["task_id"] == task["task_id"])
    assert current["status"] == "assigned"

    accepted = service.decide(
        submission["submission_id"],
        accepted=True,
        approval_id="pa.user-hub.1",
        actor_id="user:owner",
    )
    assert accepted["status"] == "accepted"
    completed = next(item for item in factory.snapshot()["tasks"] if item["task_id"] == task["task_id"])
    assert completed["status"] == "completed"


def test_user_hub_result_fails_closed_on_digest_or_path_violation(tmp_path: Path) -> None:
    factory, task, assignment = _assigned(tmp_path)
    result = _result(task, assignment)
    body = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    service = UserHubResultService(factory=factory, state_dir=tmp_path)
    with pytest.raises(UserHubSubmissionError, match="digest mismatch"):
        service.fetch_validate_stage(
            task_id=task["task_id"],
            source_url="https://user-hub.example/result.json",
            source_digest="sha256:" + "0" * 64,
            webspace_id="desktop",
            fetcher=lambda _url: body,
            pending_action_publisher=lambda **payload: payload,
        )

    result["changed_paths"].append("src/adaos/core.py")
    bad = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ValueError, match="outside the task sparse checkout"):
        service.fetch_validate_stage(
            task_id=task["task_id"],
            source_url="https://user-hub.example/result.json",
            source_digest=f"sha256:{hashlib.sha256(bad).hexdigest()}",
            webspace_id="desktop",
            fetcher=lambda _url: bad,
            pending_action_publisher=lambda **payload: payload,
        )

