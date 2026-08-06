"""Constrained conversational workflow authoring for executable prototypes."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, ValidationError

from adaos.services.conversational_artifacts import run_conversation_story
from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    WorkflowDefinitionError,
    compile_definition,
    workflow_definition_digest,
)

from .workflow import BuilderWorkflowError


PROTOTYPE_WORKFLOW_SLICE_SCHEMA = "adaos.builder.prototype_workflow_slice.v1"


def _abi(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(name: str, value: Mapping[str, Any], *, label: str) -> None:
    try:
        Draft202012Validator(_abi(name)).validate(dict(value))
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        raise BuilderWorkflowError(f"invalid {label}{suffix}: {exc.message}") from exc


def _assert_acyclic_without_retry(
    compiled: CompiledWorkflowDefinition,
    retry_transition_ids: set[str],
) -> None:
    adjacency: dict[str, list[str]] = {state: [] for state in compiled.states}
    for transition in compiled.transitions:
        if transition.transition_id in retry_transition_ids:
            continue
        for source in transition.sources:
            adjacency[source].append(transition.target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(state: str) -> None:
        if state in visiting:
            raise BuilderWorkflowError("conversational prototype is cyclic outside its declared retry")
        if state in visited:
            return
        visiting.add(state)
        for target in adjacency[state]:
            visit(target)
        visiting.remove(state)
        visited.add(state)

    visit(compiled.initial_state)


def _validate_profile(value: Mapping[str, Any], compiled: CompiledWorkflowDefinition) -> None:
    if len(compiled.states) > 12 or len(compiled.transitions) > 20:
        raise BuilderWorkflowError("conversational prototype exceeds the bounded 12-state/20-transition profile")
    if compiled.source.get("subworkflows"):
        raise BuilderWorkflowError("conversational prototype does not admit subworkflows")
    outgoing = [
        transition
        for transition in compiled.transitions
        if compiled.initial_state in transition.sources
    ]
    if len(outgoing) != 1 or outgoing[0].command != value["entry_command"]:
        raise BuilderWorkflowError("conversational prototype requires exactly one declared entry command")
    retries = {str(item) for item in value["retry_transition_ids"]}
    transition_ids = {item.transition_id for item in compiled.transitions}
    unknown_retries = sorted(retries - transition_ids)
    if unknown_retries:
        raise BuilderWorkflowError(f"unknown prototype retry transition: {unknown_retries[0]}")
    _assert_acyclic_without_retry(compiled, retries)

    requirements = {str(item["activity_id"]): dict(item) for item in value["activity_requirements"]}
    if len(requirements) != len(value["activity_requirements"]):
        raise BuilderWorkflowError("prototype activity requirement ids must be unique")
    activities = {
        str(transition.descriptor["effect"].get("activity") or "").strip()
        for transition in compiled.transitions
        if str(transition.descriptor["effect"].get("activity") or "").strip()
    }
    missing = sorted(activities - set(requirements))
    if missing:
        raise BuilderWorkflowError(f"prototype workflow activity requirement is missing: {missing[0]}")


def validate_conversational_workflow_slice(
    value: Mapping[str, Any],
    *,
    source_definition: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a candidate slice without mutating the active workflow file."""

    candidate = copy.deepcopy(dict(value))
    _validate(
        "builder.prototype_workflow_slice.v1.schema.json",
        candidate,
        label="prototype workflow slice",
    )
    actual_source_digest = workflow_definition_digest(source_definition)
    if candidate["source_definition_digest"] != actual_source_digest:
        raise BuilderWorkflowError("prototype workflow slice is stale against its source definition")
    try:
        compiled = compile_definition(candidate["definition"])
    except WorkflowDefinitionError as exc:
        raise BuilderWorkflowError(f"invalid prototype workflow definition: {exc}") from exc
    _validate_profile(candidate, compiled)

    story_ids: list[str] = []
    reports: list[dict[str, Any]] = []
    for index, story in enumerate(candidate["stories"]):
        _validate("conversational.story.v1.schema.json", story, label=f"prototype story[{index}]")
        story_id = str(story["id"])
        if story["workflow_type"] != compiled.workflow_type:
            raise BuilderWorkflowError(f"prototype story workflow type differs: {story_id}")
        story_ids.append(story_id)
        reports.append(run_conversation_story(story, compiled))
    if len(story_ids) != len(set(story_ids)):
        raise BuilderWorkflowError("prototype story ids must be unique")
    expected_outcomes = set(candidate["story_outcomes"].values())
    if expected_outcomes != set(story_ids):
        raise BuilderWorkflowError("success, failure, and input_required must map to the three prototype stories")
    invalid = [report for report in reports if not report.get("valid")]
    if invalid:
        raise BuilderWorkflowError(f"prototype story failed: {invalid[0]['story_id']}")
    reports_by_id = {str(report["story_id"]): report for report in reports}
    success_timeline = list(reports_by_id[candidate["story_outcomes"]["success"]].get("timeline") or [])
    failure_timeline = list(reports_by_id[candidate["story_outcomes"]["failure"]].get("timeline") or [])
    input_timeline = list(reports_by_id[candidate["story_outcomes"]["input_required"]].get("timeline") or [])
    if not any(step.get("accepted") is True for step in success_timeline):
        raise BuilderWorkflowError("prototype success story must contain an accepted command")
    if not any(step.get("accepted") is False for step in failure_timeline):
        raise BuilderWorkflowError("prototype failure story must contain a rejected command")
    input_reasons = {
        str(step.get("reason_code") or "")
        for step in input_timeline
        if step.get("accepted") is False
    }
    if not any(
        reason in {"command_not_allowed", "confirmation_required", "evidence_required"}
        or reason.startswith("invalid_input:")
        for reason in input_reasons
    ):
        raise BuilderWorkflowError("prototype input_required story must exercise a recoverable input boundary")

    return {
        "valid": True,
        "schema": PROTOTYPE_WORKFLOW_SLICE_SCHEMA,
        "slice_id": candidate["slice_id"],
        "source_definition_digest": actual_source_digest,
        "candidate_definition_digest": workflow_definition_digest(compiled),
        "source_generation": candidate["source_generation"],
        "profile": candidate["profile"],
        "story_reports": reports,
        "candidate_patch": {
            "operation": "replace_workflow_definition_candidate",
            "base_definition_ref": candidate["source_definition_ref"],
            "base_definition_digest": actual_source_digest,
            "expected_generation": candidate["source_generation"],
            "candidate": copy.deepcopy(compiled.source),
            "activation": "automation_only",
        },
    }


__all__ = ["PROTOTYPE_WORKFLOW_SLICE_SCHEMA", "validate_conversational_workflow_slice"]
