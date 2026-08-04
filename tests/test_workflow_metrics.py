from __future__ import annotations

from adaos.services.governed_workflow import (
    compile_definition,
    validate_workflow_record,
    workflow_contract_snapshot,
)
from adaos.services.workflow_metrics import (
    WORKFLOW_METRICS_EVIDENCE_SCHEMA,
    WORKFLOW_METRICS_REPORT_SCHEMA,
    workflow_metrics_evidence,
    workflow_metrics_report,
)


def _transition(
    transition_id: str,
    source: str,
    target: str,
    command: str,
    *,
    activity: str = "",
    confirmation: str = "none",
    optional: list[str] | None = None,
) -> dict[str, object]:
    input_schema = {
        "type": "object",
        "properties": {"confirmed": {"type": "boolean"}},
        "additionalProperties": False,
    }
    return {
        "schema": "adaos.workflow.transition.v1",
        "transition_id": transition_id,
        "source": source,
        "target": target,
        "trigger": {"kind": "command", "command": command, "input_schema": input_schema},
        "context": {"target_resolution": "instance", "command_context_required": False},
        "authority": {"actors": ["user"], "permissions": ["metrics.change"]},
        "guards": [{"id": "always", "params": {}, "reason_code": "blocked"}],
        "concurrency": {
            "conflict_scope": "change",
            "requires_generation": True,
            "idempotency": "required",
        },
        "risk": {
            "class": "isolated_write",
            "side_effect": "reversible",
            "confirmation": confirmation,
        },
        "effect": {
            "activity": activity,
            "transaction": "outbox" if activity else "none",
            "retry": "bounded" if activity else "never",
            "compensation": "metrics.undo" if activity else None,
        },
        "recovery": {
            "timeout_seconds": 900,
            "heartbeat_seconds": 30,
            "cancellation": "cooperative",
            "reconciliation": "required_on_unknown",
        },
        "outcomes": {
            "success": "target",
            "failure": "source",
            "input_required": "source",
            "cancelled": "source",
            "unknown": "source",
        },
        "evidence": {"required": False, "minimum": 0},
        "approval": {"required": False, "policy_refs": []},
        "async_reply": {"mode": "progress_and_terminal", "reply_route": "origin"},
        "capability_requirements": {
            "required": ["buttons"],
            "optional": optional or [],
            "fallback": "numbered_text",
        },
        "explanations": {
            "allowed": f"{command} is available",
            "rejected": f"{command} is blocked",
            "completed": f"{command} completed",
        },
        "events": {"emitted": [f"metrics.{command}.accepted"], "outbox": bool(activity)},
        "observability": {
            "audit_event": f"metrics.{command}.audit",
            "redaction": "policy",
            "metrics": ["workflow_transition_total"],
            "trace": True,
        },
        "migration": {"introduced_in": "1.0.0", "aliases": []},
    }


def _definition() -> dict[str, object]:
    approve = _transition(
        "approve_draft",
        "draft",
        "review",
        "approve",
        activity="metrics.approve",
        confirmation="required",
        optional=["progress"],
    )
    cancel = _transition("cancel_draft", "draft", "cancelled", "cancel")
    revise = _transition("revise_review", "review", "draft", "revise")
    return {
        "schema": "adaos.workflow.definition.v1",
        "workflow_type": "metrics.change",
        "definition_version": "1.0.0",
        "aggregate_type": "metrics.change",
        "initial_state": "draft",
        "states": [
            {
                "id": "draft",
                "label": "Draft",
                "terminal": False,
                "waiting": True,
                "wait_explanation": "waiting for the next authoring decision",
            },
            {"id": "review", "label": "Review", "terminal": False},
            {"id": "cancelled", "label": "Cancelled", "terminal": True},
        ],
        "commands": [
            {"id": "approve", "input_schema": approve["trigger"]["input_schema"]},
            {"id": "cancel", "input_schema": cancel["trigger"]["input_schema"]},
            {"id": "revise", "input_schema": revise["trigger"]["input_schema"]},
        ],
        "transitions": [approve, cancel, revise],
        "subworkflows": [],
        "metadata": {"test": "workflow_metrics"},
    }


def test_workflow_metrics_report_records_complexity_context_stories_and_cycle_time() -> None:
    story_reports = [
        {
            "valid": False,
            "steps": 2,
            "diagnostics": [
                {
                    "code": "conversational.story.presentation_commands_mismatch",
                    "severity": "error",
                    "path": "story.steps[0]",
                    "message": "presentation commands do not match",
                },
                {
                    "code": "conversational.story.repeated_correction",
                    "severity": "warning",
                    "path": "story.steps[1]",
                    "message": "same correction repeated",
                },
            ],
            "timeline": [
                {
                    "command": "approve",
                    "retry_of_step": 0,
                    "action_failure": True,
                    "output": {"kind": "clarification"},
                    "presentation": {
                        "mode": "numbered_text",
                        "supported": True,
                        "plan": {
                            "fallback_used": "numbered_text",
                            "semantic_equivalent": True,
                        },
                    },
                },
                {
                    "command": None,
                    "output": {"kind": "repair"},
                    "presentation": {
                        "mode": "unsupported",
                        "supported": False,
                        "plan": {"semantic_equivalent": False},
                    },
                },
            ],
        }
    ]
    report = workflow_metrics_report(
        compile_definition(_definition()),
        story_reports=story_reports,
        context_packet={
            "digest": "sha256:" + ("0" * 64),
            "coverage": {
                "required": ["target_structure", "abi", "workflow_definition"],
                "present": ["target_structure", "abi"],
                "missing": ["workflow_definition"],
                "ambiguous": [],
                "ready": False,
            },
        },
        measurement={
            "source": "manual_builder_probe",
            "current": {
                "time_to_understand_ms": 1200,
                "action_availability_ms": 500,
                "recovery_explanation_ms": 300,
                "diagnosis_effort_steps": 2,
            },
            "legacy": {
                "time_to_understand_ms": 1800,
                "action_availability_ms": 900,
                "recovery_explanation_ms": 700,
                "diagnosis_effort_steps": 5,
            },
            "recovery_explanation_available": True,
        },
        report_id="workflow-metrics:test",
        generated_at="2026-08-04T00:00:00+00:00",
    )

    validate_workflow_record(WORKFLOW_METRICS_REPORT_SCHEMA, report)
    assert workflow_contract_snapshot()["records"]["WorkflowMetricsReport"] == WORKFLOW_METRICS_REPORT_SCHEMA
    assert report["definition_complexity"] == {
        "state_count": 3,
        "transition_count": 3,
        "command_count": 3,
        "terminal_state_count": 1,
        "waiting_state_count": 1,
        "max_branching_factor": 2,
        "average_branching_factor": 1.0,
        "cycle_edge_count": 1,
        "guard_count": 3,
        "external_activity_count": 1,
        "confirmation_required_count": 1,
        "required_capability_count": 1,
        "optional_capability_count": 1,
    }
    assert report["context_sufficiency"]["ready"] is False
    assert report["context_sufficiency"]["score"] == 0.6667
    assert report["story_outcomes"]["story_count"] == 1
    assert report["story_outcomes"]["valid_story_count"] == 0
    assert report["story_outcomes"]["clarification_rate"] == 0.5
    assert report["story_outcomes"]["repair_rate"] == 0.5
    assert report["story_outcomes"]["retry_rate"] == 1.0
    assert report["story_outcomes"]["action_failure_rate"] == 1.0
    assert report["story_outcomes"]["action_mismatch_defect_count"] == 1
    assert report["story_outcomes"]["repeated_correction_count"] == 1
    assert report["story_outcomes"]["presentation_fallback_count"] == 1
    assert report["story_outcomes"]["unsupported_presentation_count"] == 1
    assert report["story_outcomes"]["semantic_equivalence_failure_count"] == 1
    assert report["cycle_time"]["delta"] == {
        "time_to_understand_ms": -600.0,
        "action_availability_ms": -400.0,
        "recovery_explanation_ms": -400.0,
        "diagnosis_effort_steps": -3.0,
    }
    assert report["diagnostics"] == []

    evidence = workflow_metrics_evidence(report)
    validate_workflow_record(WORKFLOW_METRICS_EVIDENCE_SCHEMA, evidence)
    assert evidence["rates"] == {
        "clarification": 0.5,
        "repair": 0.5,
        "retry": 1.0,
        "action_failure": 1.0,
    }
    assert evidence["definition_complexity"] == report["definition_complexity"]
    assert evidence["context_sufficiency"] == report["context_sufficiency"]


def test_workflow_metrics_report_warns_when_cycle_measurements_are_missing() -> None:
    report = workflow_metrics_report(
        _definition(),
        report_id="workflow-metrics:missing",
        generated_at="2026-08-04T00:00:00+00:00",
    )

    assert report["cycle_time"]["current"]["time_to_understand_ms"] is None
    assert report["cycle_time"]["delta"]["diagnosis_effort_steps"] is None
    assert [item["code"] for item in report["diagnostics"]] == [
        "workflow.metrics.current_measurement_missing",
        "workflow.metrics.legacy_measurement_missing",
    ]
