from __future__ import annotations

import json
from pathlib import Path

import pytest
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
    selected = service.task_context(
        "recipes",
        repair_ids=[context["tasks"][0]["repair_id"]],
    )
    assert selected["active_count"] == 1
    assert selected["tasks"][0]["repair_id"] == context["tasks"][0]["repair_id"]


def test_builder_work_item_lifecycle_is_revisioned_and_not_a_user_ticket(tmp_path: Path) -> None:
    service = BuilderRepairService(state_dir=tmp_path)
    created = service.report(
        project_id="demo_metrics",
        signal_type="other",
        summary="Rename the selected metric action",
        source_refs=[
            {"type": "dev_ticket", "id": "dticket.1"},
            {"type": "dev_ticket", "id": "dticket.2"},
        ],
        context={"package_id": "bpackage.1"},
    )["task"]

    assert created["work_item_id"] == created["repair_id"]
    assert created["work_status"] == "planned"
    assert created["ticket_ids"] == ["dticket.1", "dticket.2"]
    assert created["package_id"] == "bpackage.1"

    claimed = service.claim(
        created["repair_id"],
        actor="builder:worker",
        expected_revision=created["revision"],
    )
    running = service.transition_work_item(
        created["repair_id"],
        status="in_progress",
        actor="builder:worker",
        expected_revision=claimed["revision"],
    )
    validating = service.transition_work_item(
        created["repair_id"],
        status="validating",
        actor="builder:validator",
        expected_revision=running["revision"],
    )
    published = service.transition_work_item(
        created["repair_id"],
        status="published",
        actor="builder:publisher",
        evidence_refs=[{"type": "runtime_overlay", "id": "trial.1"}],
        expected_revision=validating["revision"],
    )

    completed = service.record_acceptance(
        created["repair_id"],
        capability_works=True,
        regression_free=True,
        evidence_refs=[{"type": "runtime_guard", "id": "guard.1", "status": "passed"}],
        actor="builder:acceptance",
    )

    assert published["work_status"] == "published"
    assert completed["work_status"] == "completed"
    assert completed["status"] == "resolved"
    assert completed["revision"] > created["revision"]
    assert [entry["event"] for entry in completed["timeline"]].count("status_changed") == 5
    assert service.package_rollup("bpackage.1")["ticket_ids"] == ["dticket.1", "dticket.2"]

    with pytest.raises(ValueError, match="changed since"):
        service.transition_work_item(
            created["repair_id"],
            status="in_progress",
            actor="builder:worker",
            expected_revision=created["revision"],
        )


def test_builder_work_item_can_fail_during_launch_preflight(tmp_path: Path) -> None:
    service = BuilderRepairService(state_dir=tmp_path)
    task = service.report(
        project_id="demo_metrics",
        signal_type="other",
        summary="Rename the selected metric action",
    )["task"]

    failed = service.transition_work_item(
        task["repair_id"],
        status="failed",
        actor="builder:automation",
        reason="automation_start:ValueError",
    )

    assert failed["status"] == "open"
    assert failed["work_status"] == "failed"
    assert failed["timeline"][-1]["details"] == {
        "from": "planned",
        "to": "failed",
        "reason": "automation_start:ValueError",
    }
def test_builder_work_item_keeps_user_visible_trial_receipt(tmp_path: Path) -> None:
    service = BuilderRepairService(state_dir=tmp_path)
    task = service.report(
        project_id="demo_metrics",
        signal_type="other",
        summary="Review the generated trial",
    )["task"]

    linked = service.link_automation(
        task["repair_id"],
        actor="builder:automation",
        automation={
            "automation": {
                "session_id": "automation.trial",
                "task_id": "task.trial",
                "status": "completed",
                "terminal": True,
            },
            "session": {
                "session_id": "automation.trial",
                "current_task_id": "task.trial",
                "completion_readiness": {
                    "aprobation": {
                        "ok": True,
                        "trial": {
                            "candidate_id": "candidate.trial",
                            "candidate_digest": "sha256:" + "1" * 64,
                            "status": "trial",
                        },
                    }
                },
            },
        },
    )

    assert linked["context"]["trial"]["trial"]["candidate_id"] == "candidate.trial"


def test_builder_work_item_aggregates_usage_across_continuation_tasks(tmp_path: Path) -> None:
    service = BuilderRepairService(state_dir=tmp_path)
    task = service.report(
        project_id="demo_metrics",
        signal_type="other",
        summary="Keep the complete Builder cost",
    )["task"]

    service.link_automation(
        task["repair_id"],
        actor="builder:automation",
        automation={
            "automation": {
                "session_id": "automation.usage",
                "task_id": "task.initial",
                "status": "failed",
            },
            "session": {
                "session_id": "automation.usage",
                "current_task_id": "task.initial",
                "codex_usage_accounting": {
                    "task_id": "task.initial",
                    "status": "reported",
                    "accuracy": "exact",
                    "root_event_id": "codex_usage.initial",
                    "input_tokens": 153_933,
                    "cached_input_tokens": 133_888,
                    "output_tokens": 1_835,
                    "total_tokens": 155_768,
                },
            },
        },
    )

    linked = service.link_automation(
        task["repair_id"],
        actor="builder:automation",
        automation={
            "automation": {
                "session_id": "automation.usage",
                "task_id": "task.continuation",
                "status": "completed",
            },
            "session": {
                "session_id": "automation.usage",
                "current_task_id": "task.continuation",
                "codex_usage_history": [
                    {
                        "task_id": "task.initial",
                        "status": "reported",
                        "accuracy": "exact",
                        "root_event_id": "codex_usage.initial",
                        "input_tokens": 153_933,
                        "cached_input_tokens": 133_888,
                        "output_tokens": 1_835,
                        "total_tokens": 155_768,
                    }
                ],
                "codex_usage_accounting": {
                    "task_id": "task.continuation",
                    "status": "reported",
                    "accuracy": "exact",
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
            },
        },
    )

    usage = linked["context"]["usage"]
    assert usage["total_tokens"] == 155_768
    assert usage["output_tokens"] == 1_835
    assert usage["attempts"] == 1
    assert usage["billable_tokens"] == 155_768
    assert usage["fresh_plus_output_tokens"] == 21_880
    assert usage["budget_metric"] == "model_tokens"
    assert usage["budget_tokens"] == 155_768
    assert usage["root_event_ids"] == ["codex_usage.initial"]


def test_completed_builder_repair_is_not_reopened_by_automation_poll(tmp_path: Path) -> None:
    service = BuilderRepairService(state_dir=tmp_path)
    task = service.report(
        project_id="demo_metrics",
        signal_type="other",
        summary="Keep accepted repair terminal",
    )["task"]
    automation = {
        "automation": {
            "session_id": "automation.accepted",
            "task_id": "task.accepted",
            "status": "completed",
        },
        "session": {
            "session_id": "automation.accepted",
            "current_task_id": "task.accepted",
        },
    }
    service.link_automation(
        task["repair_id"],
        actor="builder:automation",
        automation=automation,
    )
    accepted = service.record_acceptance(
        task["repair_id"],
        capability_works=True,
        regression_free=True,
        evidence_refs=[{"type": "test", "id": "tests/passed.json"}],
        actor="builder:automation",
    )

    polled = service.link_automation(
        task["repair_id"],
        actor="builder:automation",
        automation=automation,
    )

    assert accepted["work_status"] == "completed"
    assert polled["status"] == "resolved"
    assert polled["work_status"] == "completed"
    assert polled["revision"] == accepted["revision"]
