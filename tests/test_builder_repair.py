from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from adaos.services.builder.repair import BuilderRepairService


def test_repair_tasks_deduplicate_supersede_and_require_acceptance_evidence(tmp_path: Path) -> None:
    service = BuilderRepairService(state_dir=tmp_path)
    first = service.report(
        project_id="recipes",
        signal_type="test_failure",
        summary="Recipe add test failed",
        source_refs=[{"type": "test", "id": "test_add_recipe", "run_id": "run-1"}],
        context={"test": "test_add_recipe", "artifact_id": "recipes"},
    )
    duplicate = service.report(
        project_id="recipes",
        signal_type="test_failure",
        summary="Recipe add test failed",
        source_refs=[{"type": "trace", "id": "trace-2"}],
        context={"test": "test_add_recipe", "artifact_id": "recipes"},
    )
    assert duplicate["duplicate"] is True
    assert duplicate["task"]["repair_id"] == first["task"]["repair_id"]
    assert duplicate["task"]["occurrence_count"] == 2
    assert len(duplicate["task"]["source_refs"]) == 2

    replacement = service.report(
        project_id="recipes",
        signal_type="test_failure",
        summary="Recipe persistence contract failed",
        source_refs=[{"type": "test", "id": "test_recipe_persistence"}],
        context={"test": "test_recipe_persistence", "artifact_id": "recipes"},
        supersedes=[first["task"]["repair_id"]],
    )
    old = next(item for item in service.list() if item["repair_id"] == first["task"]["repair_id"])
    assert old["status"] == "superseded"
    assert old["superseded_by"] == replacement["task"]["repair_id"]

    unresolved = service.record_acceptance(
        replacement["task"]["repair_id"],
        capability_works=True,
        regression_free=False,
        evidence_refs=[{"type": "test_run", "id": "run-3"}],
        actor="builder:test",
    )
    assert unresolved["status"] == "in_progress"
    resolved = service.record_acceptance(
        replacement["task"]["repair_id"],
        capability_works=True,
        regression_free=True,
        evidence_refs=[
            {"type": "test_run", "id": "run-4", "status": "passed"},
            {"type": "regression_suite", "id": "suite-4", "status": "passed"},
        ],
        actor="builder:test",
    )
    assert resolved["status"] == "resolved"

    schema = json.loads(
        (Path(__file__).parents[1] / "src" / "adaos" / "abi" / "builder.repair_task.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(resolved)


def test_non_design_time_signal_is_recorded_without_autonomous_repair(tmp_path: Path) -> None:
    task = BuilderRepairService(state_dir=tmp_path).report(
        project_id="payments",
        signal_type="quarantine",
        summary="Provider account is administratively quarantined",
        source_refs=[{"type": "quarantine_report", "id": "q-1"}],
        design_time_fixable=False,
    )["task"]
    assert task["status"] == "not_design_time_fixable"


def test_runtime_evidence_bundle_becomes_bounded_builder_task_context(tmp_path: Path) -> None:
    service = BuilderRepairService(state_dir=tmp_path)
    ingested = service.ingest_task_evidence(
        project_id="recipes",
        evidence={
            "failed_tests": [{"summary": "recipe add failed", "test": "test_add"}],
            "import_errors": [{"message": "cannot import recipe handler", "component": "handler"}],
            "route_pressure": [{"summary": "route queue exceeded budget", "route": "recipes.add"}],
            "memory_growth": [{"summary": "worker heap grew", "component": "recipe_worker"}],
            "nlu_misses": [{"summary": "add recipe phrase was not matched", "intent": "recipe.add"}],
        },
    )

    context = service.task_context("recipes")
    assert ingested["reported_count"] == 5
    assert context["status"] == "present"
    assert context["active_count"] == 5
    assert {item["signal_type"] for item in context["tasks"]} == {
        "test_failure",
        "import_error",
        "route_pressure",
        "memory_growth",
        "nlu_miss",
    }
