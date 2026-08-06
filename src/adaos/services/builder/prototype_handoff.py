"""Fail-closed handoff from executable Prototype evidence to Automation."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, ValidationError

from .workflow import BuilderWorkflowError


PROTOTYPE_HANDOFF_SCHEMA = "adaos.builder.prototype_handoff.v1"
REQUIRED_REPRESENTATIVE_STATES = frozenset(
    {
        "empty",
        "normal",
        "validation_failure",
        "unavailable",
        "delayed_input_required",
        "role_denied",
        "long_content",
        "locale_en",
        "locale_ru",
        "compact",
        "wide",
        "offline",
        "conflict",
        "rate_limited",
        "large_dataset",
    }
)


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate(name: str, value: Mapping[str, Any], *, label: str) -> None:
    path = Path(__file__).resolve().parents[2] / "abi" / name
    schema = json.loads(path.read_text(encoding="utf-8"))
    try:
        Draft202012Validator(schema).validate(dict(value))
    except ValidationError as exc:
        location = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {location}" if location else ""
        raise BuilderWorkflowError(f"invalid {label}{suffix}: {exc.message}") from exc


def build_automation_handoff(
    *,
    handoff_id: str,
    project_ref: str,
    ui_revision_ref: str,
    workflow_report: Mapping[str, Any],
    data_definition: Mapping[str, Any],
    binding_profile: Mapping[str, Any],
    composition_slices: Sequence[Mapping[str, Any]],
    activity_requirements: Sequence[Mapping[str, Any]],
    representative_states: Sequence[Mapping[str, Any]],
    strict: bool = True,
) -> dict[str, Any]:
    """Build an exact handoff bundle and reject incomplete implementation maps."""

    _validate("builder.binding_profile.v1.schema.json", binding_profile, label="binding profile")
    for index, item in enumerate(composition_slices):
        _validate(
            "builder.ui_composition_slice.v1.schema.json",
            item,
            label=f"composition slice[{index}]",
        )
    reports = [copy.deepcopy(dict(item)) for item in workflow_report.get("story_reports") or []]
    requirements = [copy.deepcopy(dict(item)) for item in activity_requirements]
    mapping_by_activity: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for item in requirements:
        activity_id = str(item.get("activity_id") or "").strip()
        if not activity_id:
            blockers.append("activity_requirement_missing_id")
            continue
        implementation_ref = str(item.get("implementation_ref") or "").strip() or None
        status = "mapped" if item.get("implementation_status") == "mapped" and implementation_ref else "missing"
        mapping_by_activity[activity_id] = {
            "activity_id": activity_id,
            "implementation_ref": implementation_ref,
            "status": status,
        }
        if status == "missing":
            blockers.append(f"missing_activity_mapping:{activity_id}")

    for item in binding_profile.get("implementation_mappings") or []:
        logical_ref = str(item.get("logical_ref") or "").strip()
        if item.get("status") != "mapped" or not str(item.get("implementation_ref") or "").strip():
            blockers.append(f"missing_binding_mapping:{logical_ref}")
    invalid_stories = sorted(
        str(item.get("story_id") or "unknown") for item in reports if not item.get("valid")
    )
    blockers.extend(f"failed_story:{story_id}" for story_id in invalid_stories)
    supplied_states = {str(item.get("state_id") or "") for item in representative_states}
    missing_states = sorted(REQUIRED_REPRESENTATIVE_STATES - supplied_states)
    blockers.extend(f"missing_representative_state:{state_id}" for state_id in missing_states)
    for item in representative_states:
        state_id = str(item.get("state_id") or "")
        fixture = item.get("fixture") if isinstance(item.get("fixture"), Mapping) else {}
        fixture_input = fixture.get("input") if isinstance(fixture.get("input"), Mapping) else {}
        expected_kind = (
            "locale" if state_id.startswith("locale_")
            else "layout" if state_id in {"compact", "wide"}
            else None
        )
        if expected_kind and fixture.get("kind") != expected_kind:
            blockers.append(f"invalid_representative_state_kind:{state_id}")
        expected_value = state_id.removeprefix("locale_") if state_id.startswith("locale_") else state_id
        expected_field = "locale" if state_id.startswith("locale_") else "breakpoint"
        if expected_kind and fixture_input.get(expected_field) != expected_value:
            blockers.append(f"invalid_representative_state_input:{state_id}")
    if len(composition_slices) == 0:
        blockers.append("missing_composition_slice")
    for item in composition_slices:
        evidence = item.get("renderer_evidence") if isinstance(item.get("renderer_evidence"), Mapping) else {}
        observed = {str(entry.get("breakpoint") or "") for entry in evidence.get("breakpoints") or []}
        for breakpoint in ("compact", "wide"):
            if breakpoint not in observed:
                blockers.append(f"missing_renderer_evidence:{item.get('slice_id')}:{breakpoint}")

    workflow = {
        "source_definition_ref": str(
            dict(workflow_report.get("candidate_patch") or {}).get("base_definition_ref") or ""
        ),
        "source_definition_digest": str(workflow_report.get("source_definition_digest") or ""),
        "candidate_definition_digest": str(workflow_report.get("candidate_definition_digest") or ""),
        "source_generation": int(workflow_report.get("source_generation") or 0),
    }
    payload = {
        "schema": PROTOTYPE_HANDOFF_SCHEMA,
        "handoff_id": str(handoff_id),
        "project_ref": str(project_ref),
        "ui_revision_ref": str(ui_revision_ref),
        "workflow": workflow,
        "data_definition_digest": _digest(dict(data_definition)),
        "binding_profile": copy.deepcopy(dict(binding_profile)),
        "composition_slice_refs": [str(item["slice_id"]) for item in composition_slices],
        "activity_requirements": requirements,
        "story_reports": reports,
        "representative_states": [copy.deepcopy(dict(item)) for item in representative_states],
        "implementation_mappings": sorted(mapping_by_activity.values(), key=lambda item: item["activity_id"]),
        "ready": not blockers,
        "blockers": sorted(set(blockers)),
    }
    payload["digest"] = _digest(payload)
    _validate("builder.prototype_handoff.v1.schema.json", payload, label="prototype handoff")
    if strict and blockers:
        raise BuilderWorkflowError("prototype handoff blocked: " + ", ".join(payload["blockers"]))
    return payload


__all__ = ["PROTOTYPE_HANDOFF_SCHEMA", "REQUIRED_REPRESENTATIVE_STATES", "build_automation_handoff"]
