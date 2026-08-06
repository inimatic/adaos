from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

from adaos.sdk.builder.prototype import (
    automation_handoff,
    composition_slice,
    start_data_runtime,
    validate_workflow_slice,
)
from adaos.services.builder.workflow import BuilderWorkflowService
from adaos.services.scenario.validation import validate_scenario_path


ROOT = Path(__file__).resolve().parents[1] / "examples" / "executable_prototype_lab"


def _load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_executable_prototype_lab_runs_full_fail_closed_handoff() -> None:
    validation = validate_scenario_path(ROOT)
    assert validation.ok, validation.errors

    data_definition = _load("prototype/data.json")
    runtime = start_data_runtime(data_definition)
    runtime.execute(
        "prototype.request.create",
        {
            "record": {
                "id": "req-002",
                "title": "Check automation handoff",
                "details": "Use only disposable prototype state.",
                "status": "draft",
            }
        },
        expected_generation=0,
    )
    generated = runtime.execute("prototype.request.suggest", {"topic": "demo"})
    provider = runtime.execute("prototype.request.policy", {"request_id": "req-002"})
    assert generated["result"]["fixture"] is True
    assert provider["trace_entry"]["provenance"]["kind"] == "recorded_fixture"
    assert len(runtime.execute("prototype.request.list", {})["result"]) == 2

    webui = _load("webui.json")
    request_list = composition_slice(
        webui,
        "widget:request-list",
        source_revision="001",
        acceptance=[{"relation": "after", "reference_ref": "widget:request-form"}],
        renderer_snapshots=[
            {"breakpoint": "compact", "visible_order": ["widget:request-form", "widget:request-list", "widget:simulation-trace"], "rects": {}},
            {"breakpoint": "wide", "visible_order": ["widget:request-form", "widget:request-list", "widget:simulation-trace"], "rects": {}},
        ],
    )
    request_form = composition_slice(
        webui,
        "field:request-form:request-title",
        source_revision="001",
        renderer_snapshots=[
            {"breakpoint": "compact", "visible_order": ["field:request-form:request-title", "field:request-form:request-details", "field:request-form:request-priority"], "rects": {}},
            {"breakpoint": "wide", "visible_order": ["field:request-form:request-title", "field:request-form:request-details", "field:request-form:request-priority"], "rects": {}},
        ],
    )
    assert request_list["composition"]["responsive"] == {"compact": "stack", "wide": "split"}
    assert request_form["bindings"]["stateKey"] == "draft.title"

    workflow = _load("workflow.json")
    workflow_slice = _load("prototype/workflow_slice.json")
    workflow_report = validate_workflow_slice(workflow_slice, source_definition=workflow)
    assert workflow_report["valid"] is True
    assert {item["story_id"] for item in workflow_report["story_reports"]} == {
        "request.success",
        "request.failure",
        "request.input",
    }
    assert workflow_slice["locales"]["ru"]["submit"] == "Отправить заявку"

    states = _load("prototype/representative_states.json")["states"]
    binding = _load("prototype/binding_profile.json")
    requirements = [*runtime.activity_requirements(), *workflow_slice["activity_requirements"]]
    blocked = automation_handoff(
        handoff_id="executable-prototype-lab-001",
        project_ref="scenario:executable_prototype_lab",
        ui_revision_ref="ui_revision:001",
        workflow_report=workflow_report,
        data_definition=data_definition,
        binding_profile=binding,
        composition_slices=[request_list, request_form],
        activity_requirements=requirements,
        representative_states=states,
        strict=False,
    )
    assert blocked["ready"] is False
    assert "missing_activity_mapping:prototype.request.submit" in blocked["blockers"]
    assert "missing_binding_mapping:schema:prototype.requests" in blocked["blockers"]

    mapped_binding = copy.deepcopy(binding)
    mapped_binding["implementation_mappings"][0] = {
        "logical_ref": "schema:prototype.requests",
        "implementation_ref": "skill:request_service.storage",
        "status": "mapped",
    }
    mapped_requirements = copy.deepcopy(requirements)
    for item in mapped_requirements:
        item["implementation_status"] = "mapped"
        item["implementation_ref"] = f"skill:request_service.{item['activity_id'].rsplit('.', 1)[-1]}"
    ready = automation_handoff(
        handoff_id="executable-prototype-lab-001",
        project_ref="scenario:executable_prototype_lab",
        ui_revision_ref="ui_revision:001",
        workflow_report=workflow_report,
        data_definition=data_definition,
        binding_profile=mapped_binding,
        composition_slices=[request_list, request_form],
        activity_requirements=mapped_requirements,
        representative_states=states,
    )
    assert ready["ready"] is True
    assert ready["blockers"] == []
    assert ready["digest"].startswith("sha256:")


def test_executable_prototype_enters_bounded_builder_context(tmp_path: Path) -> None:
    scenarios = tmp_path / "scenarios"
    skills = tmp_path / "skills"
    skills.mkdir()
    project = scenarios / "executable_prototype_lab"
    shutil.copytree(ROOT, project)
    service = BuilderWorkflowService(skills, scenarios, tmp_path / "state")
    service.transition(
        "scenario",
        "executable_prototype_lab",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-executable-context",
            "request": "Move the request list after the form.",
            "issues": [
                {
                    "issue_id": "layout",
                    "title": "Keep request composition explicit",
                    "lane": "prototype",
                    "semantic_refs": ["widget:request-list"],
                    "acceptance_criteria": ["The request list follows the form."],
                }
            ],
        },
    )

    packet = service.build_context_packet(
        "scenario",
        "executable_prototype_lab",
        required_facets=["target_structure", "executable_prototype"],
        enforce_context_coverage=True,
    )

    prototype = packet["facets"]["executable_prototype"]
    assert prototype["status"] == "present"
    assert prototype["data_mode"] == "local_crud"
    assert prototype["simulation_trace"]["implementation_evidence"] is False
    assert prototype["implementation_mapping"] == {
        "schema": "adaos.builder.implementation_mapping_report.v1",
        "profile_id": "request-fixture",
        "mode": "fixture",
        "mapping_count": 1,
        "missing": ["schema:prototype.requests"],
        "ready": False,
    }
    assert prototype["workflow_validation"]["valid"] is True
    assert prototype["composition_slices"][0]["target"]["ref"] == "widget:request-list"
    assert prototype["artifacts"]["workflow_slice"]["ref"] == "prototype/workflow_slice.json"
