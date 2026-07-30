from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService


ABI_ROOT = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi"


@pytest.fixture
def service(tmp_path: Path) -> BuilderWorkflowService:
    skills = tmp_path / "skills"
    root = tmp_path / "scenarios" / "recipes"
    skills.mkdir()
    root.mkdir(parents=True)
    (root / "scenario.yaml").write_text("id: recipes\nversion: 0.1.0\n", encoding="utf-8")
    revisions = root / "ui_revisions"
    revisions.mkdir()
    for revision in ("001", "EXP-1"):
        (revisions / f"{revision}.json").write_text("{}", encoding="utf-8")
    (revisions / "current.txt").write_text("001\n", encoding="utf-8")
    instance = BuilderWorkflowService(skills, tmp_path / "scenarios", tmp_path / "state")
    instance.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-recipes",
            "request": "Improve recipes.",
            "affected_refs": ["widget:recipes"],
            "issues": [
                {
                    "issue_id": "recipes",
                    "title": "Improve recipes",
                    "lane": "prototype",
                    "semantic_refs": ["widget:recipes"],
                    "acceptance_criteria": ["Recipes improve."],
                }
            ],
        },
    )
    return instance


def test_experiment_does_not_advance_prototype_until_explicit_adoption(
    service: BuilderWorkflowService,
) -> None:
    before = service.describe("scenario", "recipes")
    experimented = service.transition(
        "scenario",
        "recipes",
        "prototype_experiment_recorded",
        metadata={
            "experiment_id": "EXP-layout",
            "revision": "EXP-1",
            "base_revision": "001",
            "purpose": "experiment",
        },
    )["workflow"]

    assert experimented["prototype"]["head_revision"] == before["prototype"]["head_revision"]
    assert experimented["prototype"]["experiments"][0]["status"] == "pending"
    run = experimented["change"]["runs"][-1]
    assert run["purpose"] == "experiment"
    assert run["adoption_status"] == "pending"

    with pytest.raises(BuilderWorkflowError, match="confirmation"):
        service.transition(
            "scenario",
            "recipes",
            "adopt_experiment",
            metadata={"experiment_id": "EXP-layout"},
        )
    adopted = service.transition(
        "scenario",
        "recipes",
        "adopt_experiment",
        metadata={"experiment_id": "EXP-layout", "confirmed": True},
    )["workflow"]
    assert adopted["prototype"]["head_revision"] == "EXP-1"
    assert adopted["prototype"]["experiments"][0]["status"] == "adopted"
    assert adopted["change"]["runs"][-1]["adoption_status"] == "adopted"

    run_schema = json.loads((ABI_ROOT / "builder.run.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(run_schema).validate(adopted["change"]["runs"][-1])


def test_discarded_experiment_never_changes_active_or_public_line(
    service: BuilderWorkflowService,
) -> None:
    service.transition(
        "scenario",
        "recipes",
        "prototype_experiment_recorded",
        metadata={"experiment_id": "EXP-copy", "revision": "EXP-1"},
    )
    discarded = service.transition(
        "scenario",
        "recipes",
        "discard_experiment",
        metadata={"experiment_id": "EXP-copy", "reason": "The copy was worse."},
    )["workflow"]
    assert discarded["prototype"]["head_revision"] == "001"
    assert discarded["prototype"]["experiments"][0]["status"] == "discarded"
    assert discarded["publication"]["status"] == "not_started"


def test_binding_profile_switch_is_explicit_and_does_not_rewrite_revision(
    service: BuilderWorkflowService,
) -> None:
    before = service.describe("scenario", "recipes")
    configured = service.configure_binding_profile(
        "scenario",
        "recipes",
        {
            "profile_id": "sandbox-store",
            "mode": "sandbox",
            "logical_schema_ref": "schema:recipes:items",
            "source_ref": "sandbox:store:test",
            "sensitivity": "internal",
            "capabilities": ["read", "write"],
            "read_policy": "sandbox",
            "write_policy": "sandbox",
            "owner": "user:local",
            "expires_at": None,
            "redaction": "policy",
            "implementation_mappings": [
                {
                    "logical_ref": "schema:recipes:items",
                    "implementation_ref": "skill:store_api:list_items",
                    "status": "mapped",
                }
            ],
        },
        expected_binding_generation=before["data_binding"]["generation"],
    )["workflow"]
    with pytest.raises(BuilderWorkflowError, match="explicit confirmation"):
        service.select_binding_profile(
            "scenario",
            "recipes",
            "sandbox-store",
            expected_binding_generation=configured["data_binding"]["generation"],
        )
    selected = service.select_binding_profile(
        "scenario",
        "recipes",
        "sandbox-store",
        expected_binding_generation=configured["data_binding"]["generation"],
        confirmed=True,
    )["workflow"]
    assert selected["data_binding"]["selected_mode"] == "sandbox"
    assert selected["prototype"]["head_revision"] == before["prototype"]["head_revision"]
    assert selected["process"]["data_mode"] == "sandbox"


def test_live_binding_is_forbidden_in_prototype_and_missing_mapping_blocks_handoff(
    service: BuilderWorkflowService,
) -> None:
    before = service.describe("scenario", "recipes")
    configured = service.configure_binding_profile(
        "scenario",
        "recipes",
        {
            "profile_id": "live-store",
            "mode": "live",
            "logical_schema_ref": "schema:recipes:items",
            "source_ref": "connector:store:production",
            "sensitivity": "personal",
            "capabilities": ["read", "write"],
            "read_policy": "scoped_live",
            "write_policy": "scoped_live",
            "owner": "user:local",
            "expires_at": None,
            "redaction": "required",
            "implementation_mappings": [],
        },
        expected_binding_generation=before["data_binding"]["generation"],
    )["workflow"]
    with pytest.raises(BuilderWorkflowError, match="forbidden in Prototype"):
        service.select_binding_profile(
            "scenario",
            "recipes",
            "live-store",
            expected_binding_generation=configured["data_binding"]["generation"],
            confirmed=True,
        )

    missing = service.configure_binding_profile(
        "scenario",
        "recipes",
        {
            "profile_id": "fixture-unmapped",
            "mode": "fixture",
            "logical_schema_ref": "schema:recipes:items",
            "source_ref": "fixture:recipes:items",
            "sensitivity": "internal",
            "capabilities": ["read"],
            "read_policy": "fixture",
            "write_policy": "none",
            "owner": "builder",
            "expires_at": None,
            "redaction": "none",
            "implementation_mappings": [
                {
                    "logical_ref": "schema:recipes:items",
                    "implementation_ref": None,
                    "status": "missing",
                }
            ],
        },
        expected_binding_generation=configured["data_binding"]["generation"],
    )["workflow"]
    selected = service.select_binding_profile(
        "scenario",
        "recipes",
        "fixture-unmapped",
        expected_binding_generation=missing["data_binding"]["generation"],
    )["workflow"]
    service.transition("scenario", "recipes", "stabilize_prototype")
    with pytest.raises(BuilderWorkflowError, match="implementation mappings"):
        service.transition("scenario", "recipes", "automation_started", metadata={"task_id": "RUN-1"})
