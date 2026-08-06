from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.skill.setup_plan import (
    SetupExecutionRequest,
    SetupPlanError,
    execute_via_skill_manager,
    publication_setup_evidence,
    validate_setup_plan,
)


def _plan(skill_id: str = "example_skill") -> dict:
    return {
        "schema": "adaos.skill.setup_plan.v1",
        "plan_id": f"{skill_id}.setup",
        "version": 1,
        "skill_id": skill_id,
        "inputs": [{"name": "endpoint", "required": True, "schema": {"type": "string"}}],
        "secrets": [{"name": "token", "required": True, "source": "secret_store"}],
        "capabilities": ["network.outbound"],
        "side_effects": [
            {
                "class": "network",
                "target": "configured provider",
                "description": "Verify provider connectivity",
                "reversible": True,
            }
        ],
        "preconditions": [{"check_id": "activated", "activity": "skill.runtime.is_active", "required": True}],
        "steps": [
            {
                "step_id": "configure",
                "activity": "skill.setup",
                "transaction_boundary": "step",
                "timeout_seconds": 60,
                "retry": {"max_attempts": 2, "backoff_seconds": 1},
            }
        ],
        "idempotency": {
            "scope": "skill_release",
            "key_fields": ["skill_id", "release_digest", "workspace_id"],
            "replay": "verify_then_resume",
        },
        "verification": {
            "checks": [{"check_id": "ready", "activity": "skill.setup.verify", "required": True}],
            "success_policy": "all",
        },
        "rollback": {"mode": "automatic", "activity": "skill.setup.rollback", "verification": []},
    }


def _skill(root: Path, *, include_plan: bool = True) -> Path:
    root.mkdir(parents=True)
    (root / "skill.yaml").write_text(
        "id: example_skill\nversion: 0.1.0\ntools:\n  setup:\n    module: setup\n    callable: run\n",
        encoding="utf-8",
    )
    if include_plan:
        (root / "setup_plan.json").write_text(json.dumps(_plan(), ensure_ascii=False), encoding="utf-8")
    return root


def test_setup_plan_schema_and_publication_gate(tmp_path: Path) -> None:
    skill = _skill(tmp_path / "example_skill")
    evidence = publication_setup_evidence(
        skill,
        validation_evidence={"setup_tests": {"status": "passed", "run_id": "test-run-1"}},
    )
    assert evidence["status"] == "passed"
    assert evidence["plan_digest"].startswith("sha256:")
    assert evidence["execution_policy"] == "separate_approved_post_activation_operation"
    schema = json.loads(
        (Path(__file__).parents[1] / "src" / "adaos" / "abi" / "skill.setup_plan.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(validate_setup_plan(_plan()))


def test_setup_publication_gate_fails_closed_without_plan_or_tests(tmp_path: Path) -> None:
    skill = _skill(tmp_path / "missing-plan", include_plan=False)
    with pytest.raises(SetupPlanError, match="setup_plan.json"):
        publication_setup_evidence(skill, validation_evidence={})

    skill = _skill(tmp_path / "missing-tests")
    with pytest.raises(SetupPlanError, match="setup_tests"):
        publication_setup_evidence(skill, validation_evidence={})


def test_setup_executor_reuses_skill_manager_and_requires_approval() -> None:
    class Manager:
        calls: list[str] = []

        def setup_skill(self, name: str) -> dict:
            self.calls.append(name)
            return {"ok": True, "skill": name}

    manager = Manager()
    request = SetupExecutionRequest(
        skill_id="example_skill",
        release_digest="sha256:" + "1" * 64,
        plan_digest="sha256:" + "2" * 64,
        approval_id="pa.setup.1",
        approved_by="user:owner",
        webspace_id="desktop",
    )
    assert execute_via_skill_manager(request, manager=manager) == {"ok": True, "skill": "example_skill"}
    assert manager.calls == ["example_skill"]
    assert request.idempotency_key == request.idempotency_key

