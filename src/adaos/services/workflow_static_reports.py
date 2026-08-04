from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from adaos.services.conversational_artifacts import (
    ConversationalPackage,
    ConversationalValidationResult,
    run_conversation_story,
)
from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    WORKFLOW_STATIC_REPORT_SCHEMA,
    compile_definition,
    definition_review_report,
    export_statechart,
    generate_conformance_cases,
    validate_workflow_record,
    workflow_definition_digest,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _compiled(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
) -> CompiledWorkflowDefinition:
    return definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)


def _sorted_ids(values: Sequence[Any]) -> list[str]:
    return sorted({str(item) for item in values if str(item or "").strip()})


def _ids_from_mapping(values: Mapping[str, Any]) -> list[str]:
    return sorted(str(key) for key in values)


def _output_id_list(outputs: Sequence[Mapping[str, Any]] | Sequence[str]) -> list[str]:
    ids: list[str] = []
    for item in outputs:
        if isinstance(item, Mapping):
            value = str(item.get("id") or "").strip()
        else:
            value = str(item or "").strip()
        if value:
            ids.append(value)
    return _sorted_ids(ids)


def _story_output_refs(stories: Sequence[Mapping[str, Any]]) -> list[str]:
    output_refs: list[str] = []
    for story in stories:
        for step in list(story.get("steps") or []):
            if not isinstance(step, Mapping):
                continue
            expect = step.get("expect")
            if not isinstance(expect, Mapping):
                continue
            output = expect.get("output")
            if not isinstance(output, Mapping):
                continue
            output_ref = str(output.get("output_ref") or "").strip()
            if output_ref:
                output_refs.append(output_ref)
    return _sorted_ids(output_refs)


def _timeline_output_summary(output: Mapping[str, Any]) -> dict[str, Any]:
    correlation = dict(output.get("correlation") or {})
    next_expected = dict(output.get("next_expected_input") or {})
    return {
        "output_id": str(output.get("output_id") or "missing"),
        "kind": str(output.get("kind") or "result"),
        "next_expected_input": str(next_expected.get("kind") or "none"),
        "turn_trace_id": correlation.get("turn_trace_id"),
        "workflow_event_id": correlation.get("workflow_event_id"),
        "command_id": correlation.get("command_id"),
    }


def _story_report_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    timeline: list[dict[str, Any]] = []
    for item in list(report.get("timeline") or []):
        if not isinstance(item, Mapping):
            continue
        output = item.get("output")
        output = output if isinstance(output, Mapping) else {}
        timeline.append(
            {
                "step": int(item.get("step") or 0),
                "command": item.get("command"),
                "before_state": item.get("before_state"),
                "after_state": item.get("after_state"),
                "accepted": item.get("accepted"),
                "reason_code": item.get("reason_code"),
                "transition_id": item.get("transition_id"),
                "output": _timeline_output_summary(output),
            }
        )
    return {
        "story_id": str(report.get("story_id") or "story"),
        "valid": bool(report.get("valid")),
        "steps": int(report.get("steps") or len(timeline)),
        "final_state": report.get("final_state"),
        "diagnostics": [copy.deepcopy(dict(item)) for item in list(report.get("diagnostics") or [])],
        "timeline": timeline,
    }


def _coverage(
    compiled: CompiledWorkflowDefinition,
    review: Mapping[str, Any],
    *,
    story_reports: Sequence[Mapping[str, Any]],
    stories: Sequence[Mapping[str, Any]],
    outputs: Sequence[Mapping[str, Any]] | Sequence[str],
) -> dict[str, Any]:
    declared_states = _ids_from_mapping(compiled.states)
    declared_transitions = _sorted_ids(transition.transition_id for transition in compiled.transitions)
    declared_commands = _ids_from_mapping(compiled.commands)
    declared_outputs = _output_id_list(outputs)

    covered_states: list[str] = []
    covered_transitions: list[str] = []
    covered_commands: list[str] = []
    story_step_count = 0
    for story in stories:
        start = story.get("start")
        if isinstance(start, Mapping):
            state = str(start.get("state") or "").strip()
            if state:
                covered_states.append(state)
    for report in story_reports:
        story_step_count += int(report.get("steps") or 0)
        final_state = str(report.get("final_state") or "").strip()
        if final_state:
            covered_states.append(final_state)
        for item in list(report.get("timeline") or []):
            if not isinstance(item, Mapping):
                continue
            for field in ("before_state", "after_state"):
                state = str(item.get(field) or "").strip()
                if state:
                    covered_states.append(state)
            command = str(item.get("command") or "").strip()
            if command:
                covered_commands.append(command)
            transition_id = str(item.get("transition_id") or "").strip()
            if transition_id:
                covered_transitions.append(transition_id)

    covered_states_sorted = _sorted_ids(covered_states)
    covered_transitions_sorted = _sorted_ids(covered_transitions)
    covered_commands_sorted = _sorted_ids(covered_commands)
    covered_outputs = _story_output_refs(stories)
    return {
        "state_total": len(declared_states),
        "transition_total": len(declared_transitions),
        "command_total": len(declared_commands),
        "story_count": len(story_reports),
        "story_step_count": story_step_count,
        "valid_story_count": sum(1 for item in story_reports if item.get("valid") is True),
        "states_declared": declared_states,
        "states_reachable": _sorted_ids(list(review.get("reachable_states") or [])),
        "states_covered_by_stories": covered_states_sorted,
        "states_missing_story_coverage": sorted(set(declared_states) - set(covered_states_sorted)),
        "transitions_declared": declared_transitions,
        "transitions_covered_by_stories": covered_transitions_sorted,
        "transitions_missing_story_coverage": sorted(set(declared_transitions) - set(covered_transitions_sorted)),
        "commands_declared": declared_commands,
        "commands_covered_by_stories": covered_commands_sorted,
        "commands_missing_story_coverage": sorted(set(declared_commands) - set(covered_commands_sorted)),
        "outputs_declared": declared_outputs,
        "outputs_covered_by_stories": covered_outputs,
        "outputs_missing_story_coverage": sorted(set(declared_outputs) - set(covered_outputs)),
    }


def workflow_static_report(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
    *,
    stories: Sequence[Mapping[str, Any]] = (),
    story_reports: Sequence[Mapping[str, Any]] = (),
    outputs: Sequence[Mapping[str, Any]] | Sequence[str] = (),
    package_id: str | None = None,
    package_digest: str | None = None,
    report_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic static review artifact from declarative workflow sources."""

    compiled = _compiled(definition)
    definition_digest = workflow_definition_digest(compiled)
    review = definition_review_report(compiled)
    cases = generate_conformance_cases(compiled)
    story_summaries = [_story_report_summary(item) for item in story_reports]
    state_case_count = sum(1 for item in cases if item.get("kind") == "state_explanation")
    transition_case_count = sum(1 for item in cases if item.get("kind") == "transition_admission")
    report = {
        "schema": WORKFLOW_STATIC_REPORT_SCHEMA,
        "report_id": report_id or f"workflow-static:{definition_digest.removeprefix('sha256:')[:24]}",
        "generated_at": generated_at or _now(),
        "workflow_type": compiled.workflow_type,
        "definition_version": compiled.definition_version,
        "definition_digest": definition_digest,
        "package_id": package_id,
        "package_digest": package_digest,
        "statechart": export_statechart(compiled),
        "definition_review": review,
        "conformance": {
            "case_count": len(cases),
            "state_case_count": state_case_count,
            "transition_case_count": transition_case_count,
            "cases": [copy.deepcopy(dict(item)) for item in cases],
        },
        "coverage": _coverage(
            compiled,
            review,
            story_reports=story_summaries,
            stories=stories,
            outputs=outputs,
        ),
        "story_reports": story_summaries,
    }
    validate_workflow_record(WORKFLOW_STATIC_REPORT_SCHEMA, report)
    return report


def conversational_package_static_report(
    package: ConversationalPackage,
    *,
    validation_result: ConversationalValidationResult | Mapping[str, Any] | None = None,
    report_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the static workflow review report bound to a conversational package."""

    if isinstance(validation_result, ConversationalValidationResult):
        validation_report = validation_result.report
    elif validation_result is not None:
        validation_report = dict(validation_result)
    else:
        validation_report = None

    story_reports: Sequence[Mapping[str, Any]]
    if validation_report is not None:
        story_reports = tuple(
            dict(item)
            for item in list(validation_report.get("story_reports") or [])
            if isinstance(item, Mapping)
        )
    else:
        story_reports = tuple(
            run_conversation_story(story, package.workflow_artifact.compiled)
            for story in package.stories
        )

    outputs = tuple(
        dict(item)
        for item in list(package.output_source.get("outputs") or [])
        if isinstance(item, Mapping)
    )
    return workflow_static_report(
        package.workflow_artifact.compiled,
        stories=package.stories,
        story_reports=story_reports,
        outputs=outputs,
        package_id=str(package.manifest.get("package_id") or "") or None,
        package_digest=package.package_digest,
        report_id=report_id,
        generated_at=generated_at,
    )


__all__ = [
    "WORKFLOW_STATIC_REPORT_SCHEMA",
    "conversational_package_static_report",
    "workflow_static_report",
]
