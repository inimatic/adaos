from __future__ import annotations

import copy

import pytest

from adaos.sdk.builder.prototype import automation_handoff
from adaos.services.builder.prototype_handoff import REQUIRED_REPRESENTATIVE_STATES
from adaos.services.builder.workflow import BuilderWorkflowError


def _binding(*, mapped: bool = True) -> dict:
    return {
        "schema": "adaos.builder.binding_profile.v1",
        "profile_id": "prototype-fixture",
        "mode": "fixture",
        "logical_schema_ref": "schema:requests",
        "source_ref": "fixture:requests:v1",
        "sensitivity": "internal",
        "capabilities": ["read", "write"],
        "read_policy": "fixture",
        "write_policy": "none",
        "owner": "builder",
        "expires_at": None,
        "redaction": "none",
        "implementation_mappings": [
            {
                "logical_ref": "schema:requests",
                "implementation_ref": "skill:requests" if mapped else None,
                "status": "mapped" if mapped else "missing",
            }
        ],
    }


def _composition() -> dict:
    return {
        "schema": "adaos.builder.ui_composition_slice.v1",
        "slice_id": "composition-request-form",
        "source_revision": "001",
        "target": {"ref": "widget:request-form", "kind": "widget", "id": "request-form", "type": "ui.form", "label": "Request", "area": "main"},
        "parent_ref": "node:request-page",
        "siblings": ["widget:request-form"],
        "order": 0,
        "ancestors": ["node:request-page"],
        "composition": {"collection": "widgets", "layout": "stack", "responsive": {"compact": "stack"}},
        "actions": [],
        "bindings": {},
        "acceptance": [],
        "renderer_evidence": {
            "kind": "bounded_structured", "target_ref": "widget:request-form",
            "visible_neighbor_refs": ["widget:request-form"],
            "breakpoints": [
                {"breakpoint": "compact", "visible_order": ["widget:request-form"], "rects": {}},
                {"breakpoint": "wide", "visible_order": ["widget:request-form"], "rects": {}}
            ],
            "budget": 5, "truncated": False, "digest": "0" * 64
        },
    }


def _workflow_report() -> dict:
    return {
        "source_definition_digest": "sha256:" + "1" * 64,
        "candidate_definition_digest": "sha256:" + "2" * 64,
        "source_generation": 3,
        "candidate_patch": {"base_definition_ref": "workflow.json@1.0.0"},
        "story_reports": [
            {"story_id": "success", "valid": True},
            {"story_id": "failure", "valid": True},
            {"story_id": "input", "valid": True},
        ],
    }


def _states() -> list[dict]:
    return [
        {
            "state_id": state_id,
            "evidence_ref": f"story:{state_id}",
            "fixture": {
                "kind": "locale" if state_id.startswith("locale_") else "layout" if state_id in {"compact", "wide"} else "data",
                "input": {"locale": state_id.removeprefix("locale_")} if state_id.startswith("locale_") else {"breakpoint": state_id} if state_id in {"compact", "wide"} else {"profile": state_id},
                "expected": {"rendered": True},
            },
        }
        for state_id in sorted(REQUIRED_REPRESENTATIVE_STATES)
    ]


def _requirements(*, mapped: bool = True) -> list[dict]:
    return [
        {
            "activity_id": "request.create",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "implementation_status": "mapped" if mapped else "missing",
            "implementation_ref": "skill:requests.create" if mapped else None,
        }
    ]


def test_handoff_pins_exact_prototype_evidence_and_is_ready() -> None:
    report = automation_handoff(
        handoff_id="handoff-request-v1",
        project_ref="scenario:requests",
        ui_revision_ref="ui_revision:001",
        workflow_report=_workflow_report(),
        data_definition={"schema": "adaos.builder.prototype_data.v1", "source_id": "requests"},
        binding_profile=_binding(),
        composition_slices=[_composition()],
        activity_requirements=_requirements(),
        representative_states=_states(),
    )
    assert report["ready"] is True
    assert report["workflow"]["source_generation"] == 3
    assert report["implementation_mappings"][0]["status"] == "mapped"
    assert report["digest"].startswith("sha256:")


def test_handoff_fails_closed_on_missing_activity_mapping() -> None:
    with pytest.raises(BuilderWorkflowError, match="missing_activity_mapping:request.create"):
        automation_handoff(
            handoff_id="handoff-request-v1",
            project_ref="scenario:requests",
            ui_revision_ref="ui_revision:001",
            workflow_report=_workflow_report(),
            data_definition={"schema": "adaos.builder.prototype_data.v1", "source_id": "requests"},
            binding_profile=_binding(),
            composition_slices=[_composition()],
            activity_requirements=_requirements(mapped=False),
            representative_states=_states(),
        )


def test_handoff_reports_all_missing_representative_states_without_mutating_inputs() -> None:
    workflow = _workflow_report()
    before = copy.deepcopy(workflow)
    report = automation_handoff(
        handoff_id="handoff-request-v1",
        project_ref="scenario:requests",
        ui_revision_ref="ui_revision:001",
        workflow_report=workflow,
        data_definition={"schema": "adaos.builder.prototype_data.v1", "source_id": "requests"},
        binding_profile=_binding(),
        composition_slices=[_composition()],
        activity_requirements=_requirements(),
        representative_states=[{"state_id": "normal", "evidence_ref": "story:normal", "fixture": {"kind": "data", "input": {"profile": "normal"}, "expected": {"rendered": True}}}],
        strict=False,
    )
    assert report["ready"] is False
    assert "missing_representative_state:empty" in report["blockers"]
    assert workflow == before
