from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    compile_definition,
    definition_review_report,
    validate_workflow_record,
    workflow_definition_digest,
)


WORKFLOW_METRICS_REPORT_SCHEMA = "adaos.workflow.metrics_report.v1"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _compiled(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
) -> CompiledWorkflowDefinition:
    return definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)


def _rate(value: int, total: int) -> float:
    return float(value) / float(total) if total else 0.0


def _text_list(values: Sequence[Any]) -> list[str]:
    return [str(item).strip() for item in values if str(item).strip()]


def _timings(value: Mapping[str, Any] | None) -> dict[str, float | None]:
    record = dict(value or {})
    return {
        "time_to_understand_ms": _number_or_none(record.get("time_to_understand_ms")),
        "action_availability_ms": _number_or_none(record.get("action_availability_ms")),
        "recovery_explanation_ms": _number_or_none(record.get("recovery_explanation_ms")),
        "diagnosis_effort_steps": _number_or_none(record.get("diagnosis_effort_steps")),
    }


def _number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _timing_delta(
    current: Mapping[str, float | None],
    legacy: Mapping[str, float | None],
) -> dict[str, float | None]:
    delta: dict[str, float | None] = {}
    for key in (
        "time_to_understand_ms",
        "action_availability_ms",
        "recovery_explanation_ms",
        "diagnosis_effort_steps",
    ):
        current_value = current.get(key)
        legacy_value = legacy.get(key)
        delta[key] = (
            float(current_value) - float(legacy_value)
            if current_value is not None and legacy_value is not None
            else None
        )
    return delta


def _definition_complexity(compiled: CompiledWorkflowDefinition) -> dict[str, Any]:
    review = definition_review_report(compiled)
    branching: dict[str, int] = {state_id: 0 for state_id in compiled.states}
    guard_count = 0
    external_activity_count = 0
    confirmation_required_count = 0
    required_capabilities: set[str] = set()
    optional_capabilities: set[str] = set()
    for transition in compiled.transitions:
        for source in transition.sources:
            branching[source] = branching.get(source, 0) + 1
        descriptor = transition.descriptor
        guard_count += len(list(descriptor.get("guards") or []))
        if str(descriptor.get("effect", {}).get("activity") or "").strip():
            external_activity_count += 1
        risk = dict(descriptor.get("risk") or {})
        if str(risk.get("confirmation") or "none") != "none":
            confirmation_required_count += 1
        capabilities = dict(descriptor.get("capability_requirements") or {})
        required_capabilities.update(_text_list(list(capabilities.get("required") or [])))
        optional_capabilities.update(_text_list(list(capabilities.get("optional") or [])))
    branch_values = list(branching.values())
    return {
        "state_count": len(compiled.states),
        "transition_count": len(compiled.transitions),
        "command_count": len(compiled.commands),
        "terminal_state_count": sum(1 for item in compiled.states.values() if item.get("terminal")),
        "waiting_state_count": sum(1 for item in compiled.states.values() if item.get("waiting")),
        "max_branching_factor": max(branch_values) if branch_values else 0,
        "average_branching_factor": (
            round(float(sum(branch_values)) / float(len(branch_values)), 4)
            if branch_values
            else 0.0
        ),
        "cycle_edge_count": int(review.get("cycle_edge_count") or 0),
        "guard_count": guard_count,
        "external_activity_count": external_activity_count,
        "confirmation_required_count": confirmation_required_count,
        "required_capability_count": len(required_capabilities),
        "optional_capability_count": len(optional_capabilities),
    }


def _context_sufficiency(context_packet: Mapping[str, Any] | None) -> dict[str, Any]:
    packet = dict(context_packet or {})
    coverage = dict(packet.get("coverage") or {})
    required = _text_list(list(coverage.get("required") or []))
    present = _text_list(list(coverage.get("present") or []))
    missing = _text_list(list(coverage.get("missing") or []))
    ambiguous = _text_list(list(coverage.get("ambiguous") or []))
    score = round(_rate(len(present), len(required)), 4) if required else None
    digest = str(packet.get("digest") or "").strip() or None
    return {
        "ready": bool(coverage.get("ready")) if "ready" in coverage else None,
        "score": score,
        "required": required,
        "present": present,
        "missing": missing,
        "ambiguous": ambiguous,
        "context_packet_digest": digest,
    }


def _story_outcomes(story_reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    story_count = len(story_reports)
    valid_story_count = sum(1 for item in story_reports if item.get("valid") is True)
    step_count = 0
    workflow_command_step_count = 0
    clarification_output_count = 0
    repair_output_count = 0
    presentation_fallback_count = 0
    unsupported_presentation_count = 0
    semantic_equivalence_failure_count = 0
    diagnostics: list[dict[str, Any]] = []
    repeated_correction_count = 0
    for report in story_reports:
        diagnostics.extend(
            copy.deepcopy(dict(item))
            for item in list(report.get("diagnostics") or [])
            if isinstance(item, Mapping)
        )
        for item in list(report.get("timeline") or []):
            if not isinstance(item, Mapping):
                continue
            step_count += 1
            if str(item.get("command") or "").strip():
                workflow_command_step_count += 1
            output = dict(item.get("output") or {})
            output_kind = str(output.get("kind") or "")
            if output_kind == "clarification":
                clarification_output_count += 1
            if output_kind == "repair":
                repair_output_count += 1
            presentation = dict(item.get("presentation") or {})
            plan = dict(presentation.get("plan") or {})
            if plan.get("fallback_used") or (
                not plan
                and presentation.get("mode") in {"numbered_text", "plain_text", "deep_link"}
            ):
                presentation_fallback_count += 1
            if presentation.get("supported") is False:
                unsupported_presentation_count += 1
            if plan.get("semantic_equivalent") is False:
                semantic_equivalence_failure_count += 1
    codes = sorted({str(item.get("code") or "") for item in diagnostics if item.get("code")})
    action_mismatch_defect_count = sum(
        1
        for item in diagnostics
        for code in [str(item.get("code") or "")]
        if "mismatch" in code and any(token in code for token in ("action", "command", "presentation"))
    )
    repeated_correction_count += sum(
        1
        for item in diagnostics
        for code in [str(item.get("code") or "")]
        if "repeated_correction" in code or "correction" in code
    )
    error_count = sum(1 for item in diagnostics if item.get("severity") == "error")
    warning_count = sum(1 for item in diagnostics if item.get("severity") == "warning")
    return {
        "story_count": story_count,
        "valid_story_count": valid_story_count,
        "step_count": step_count,
        "workflow_command_step_count": workflow_command_step_count,
        "clarification_output_count": clarification_output_count,
        "repair_output_count": repair_output_count,
        "action_mismatch_defect_count": action_mismatch_defect_count,
        "repeated_correction_count": repeated_correction_count,
        "presentation_fallback_count": presentation_fallback_count,
        "unsupported_presentation_count": unsupported_presentation_count,
        "semantic_equivalence_failure_count": semantic_equivalence_failure_count,
        "diagnostic_count": len(diagnostics),
        "error_count": error_count,
        "warning_count": warning_count,
        "distinct_diagnostic_codes": codes,
        "clarification_rate": round(_rate(clarification_output_count, step_count), 4),
        "repair_rate": round(_rate(repair_output_count, step_count), 4),
        "action_mismatch_rate": round(_rate(action_mismatch_defect_count, step_count), 4),
        "presentation_fallback_rate": round(_rate(presentation_fallback_count, step_count), 4),
    }


def _cycle_time(
    measurement: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    record = dict(measurement or {})
    current = _timings(record.get("current") if isinstance(record.get("current"), Mapping) else None)
    legacy = _timings(record.get("legacy") if isinstance(record.get("legacy"), Mapping) else None)
    diagnostics: list[dict[str, str]] = []
    if not any(value is not None for value in current.values()):
        diagnostics.append(
            {
                "code": "workflow.metrics.current_measurement_missing",
                "severity": "warning",
                "path": "cycle_time.current",
                "message": "current workflow timing measurements were not supplied",
            }
        )
    if not any(value is not None for value in legacy.values()):
        diagnostics.append(
            {
                "code": "workflow.metrics.legacy_measurement_missing",
                "severity": "warning",
                "path": "cycle_time.legacy",
                "message": "legacy workflow timing measurements were not supplied",
            }
        )
    return (
        {
            "source": str(record.get("source") or "").strip() or None,
            "current": current,
            "legacy": legacy,
            "delta": _timing_delta(current, legacy),
            "recovery_explanation_available": (
                bool(record.get("recovery_explanation_available"))
                if "recovery_explanation_available" in record
                else None
            ),
        },
        diagnostics,
    )


def workflow_metrics_report(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
    *,
    story_reports: Sequence[Mapping[str, Any]] = (),
    context_packet: Mapping[str, Any] | None = None,
    measurement: Mapping[str, Any] | None = None,
    report_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    compiled = _compiled(definition)
    definition_digest = workflow_definition_digest(compiled)
    cycle_time, diagnostics = _cycle_time(measurement)
    report = {
        "schema": WORKFLOW_METRICS_REPORT_SCHEMA,
        "report_id": report_id
        or f"workflow-metrics:{definition_digest.removeprefix('sha256:')[:24]}",
        "generated_at": generated_at or _now(),
        "workflow_type": compiled.workflow_type,
        "definition_version": compiled.definition_version,
        "definition_digest": definition_digest,
        "definition_complexity": _definition_complexity(compiled),
        "context_sufficiency": _context_sufficiency(context_packet),
        "story_outcomes": _story_outcomes(story_reports),
        "cycle_time": cycle_time,
        "diagnostics": diagnostics,
    }
    return validate_workflow_record(WORKFLOW_METRICS_REPORT_SCHEMA, report)


__all__ = [
    "WORKFLOW_METRICS_REPORT_SCHEMA",
    "workflow_metrics_report",
]
