from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.governed import (
    builder_change_definition,
    compiled_builder_change_definition,
)
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService
from adaos.services.governed_workflow import definition_review_report, export_statechart


ABI_ROOT = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi"


def _service(tmp_path: Path) -> BuilderWorkflowService:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    root = scenarios / "recipes"
    skills.mkdir(exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    (root / "scenario.yaml").write_text("id: recipes\nversion: 0.1.0\n", encoding="utf-8")
    revisions = root / "ui_revisions"
    revisions.mkdir(exist_ok=True)
    (revisions / "001.json").write_text("{}", encoding="utf-8")
    (revisions / "current.txt").write_text("001\n", encoding="utf-8")
    return BuilderWorkflowService(skills, scenarios, tmp_path / "state")


def _plan(service: BuilderWorkflowService, *, lane: str = "prototype") -> dict[str, object]:
    return service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-recipes",
            "request": "Add favorites to recipes.",
            "issues": [
                {
                    "issue_id": "favorites",
                    "title": "Add favorites",
                    "lane": lane,
                    "acceptance_criteria": ["Favorites can be selected."],
                }
            ],
        },
    )["workflow"]


def test_normative_builder_definition_is_compiled_and_explainable() -> None:
    compiled = compiled_builder_change_definition()
    report = definition_review_report(compiled)
    graph = export_statechart(compiled)

    assert builder_change_definition()["metadata"]["planes"] == [
        "change",
        "artifact_lineage",
        "run",
        "view",
    ]
    assert report["unreachable_states"] == []
    assert set(report["terminal_states"]) == {"cancelled", "published", "superseded"}
    assert {"prototype_editing", "automation_waiting", "trial_review", "publication_ready"} <= {
        item["id"] for item in graph["states"]
    }


def test_dev_builder_skill_workflow_is_runtime_authority(tmp_path: Path) -> None:
    service = _service(tmp_path)
    builder_skill = service.dev_skills_root / "builder_skill"
    builder_skill.mkdir()
    (builder_skill / "skill.yaml").write_text(
        "name: builder_skill\nversion: 0.1.0\nworkflow:\n  manifest: workflow.json\n",
        encoding="utf-8",
    )
    definition = builder_change_definition()
    definition["definition_version"] = "1.0.1"
    (builder_skill / "workflow.json").write_text(
        json.dumps(definition, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    described = service.describe("scenario", "recipes")

    assert described["governed"]["definition_version"] == "1.0.1"


def test_present_but_invalid_dev_builder_workflow_fails_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    builder_skill = service.dev_skills_root / "builder_skill"
    builder_skill.mkdir()
    (builder_skill / "skill.yaml").write_text(
        "name: builder_skill\nversion: 0.1.0\nworkflow:\n  manifest: workflow.json\n",
        encoding="utf-8",
    )
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "adaos"
        / "services"
        / "builder"
        / "builder_change.workflow.json"
    )
    shutil.copyfile(source, builder_skill / "workflow.json")
    definition = json.loads((builder_skill / "workflow.json").read_text(encoding="utf-8"))
    definition["transitions"][0]["effect"]["activity"] = "builder.unregistered"
    (builder_skill / "workflow.json").write_text(json.dumps(definition), encoding="utf-8")

    with pytest.raises(BuilderWorkflowError, match="unregistered activity"):
        service.describe("scenario", "recipes")


def test_legacy_transition_is_admitted_and_persisted_by_canonical_statechart(tmp_path: Path) -> None:
    service = _service(tmp_path)

    planned = _plan(service)
    assert planned["governed"]["state"] == "prototype_editing"
    assert planned["workflow_description"]["state"] == "prototype_editing"

    approved = service.transition("scenario", "recipes", "stabilize_prototype")["workflow"]
    assert approved["governed"]["state"] == "automation_ready"

    running = service.transition(
        "scenario", "recipes", "automation_started", metadata={"task_id": "RUN-1"}
    )["workflow"]
    assert running["governed"]["state"] == "automation_waiting"
    assert running["history"][-1]["canonical"]["command"] == "start_automation"

    restarted = _service(tmp_path).describe("scenario", "recipes")
    assert restarted["governed"] == running["governed"]
    assert restarted["workflow_description"]["state"] == "automation_waiting"


def test_process_projection_is_dependent_lineage_with_exact_preview_labels(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _plan(service, lane="automation")
    service.transition("scenario", "recipes", "automation_started", metadata={"task_id": "RUN-1"})
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "RUN-1", "version": "0.2.0", "snapshot_path": "snapshot"},
    )
    workflow = service.describe("scenario", "recipes")
    process = workflow["process"]

    schema = json.loads(
        (ABI_ROOT / "builder.process_projection.v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(process)
    by_kind = {item["kind"]: item for item in process["nodes"]}
    assert by_kind["prototype"]["parent_ref"] == by_kind["change"]["ref"]
    assert by_kind["automation"]["parent_ref"] == by_kind["prototype"]["ref"]
    assert by_kind["automation"]["source_ref"] == by_kind["prototype"]["ref"]
    assert {item["label"].split(":", 1)[0] for item in process["preview_options"]} == {
        "proto",
        "active",
    }


def test_view_selection_does_not_advance_canonical_change(tmp_path: Path) -> None:
    service = _service(tmp_path)
    planned = _plan(service)
    canonical_generation = planned["governed"]["generation"]

    updated = service.update_interaction_context(
        "scenario",
        "recipes",
        {"inspected_ref": "prototype:recipes:001", "preview_target": "proto:recipes:001"},
        expected_generation=planned["generation"],
    )["workflow"]

    assert updated["generation"] == planned["generation"] + 1
    assert updated["governed"]["generation"] == canonical_generation
    assert updated["governed"]["state"] == "prototype_editing"
    assert updated["process"]["preview_target"] == "proto:recipes:001"


def test_builder_actions_and_shared_interaction_are_bound_to_canonical_generation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workflow = _plan(service)

    frame = service.interaction_frame("scenario", "recipes")
    approve = next(item for item in frame["actions"] if item["command"] == "builder.prototype.approve")
    assert approve["workflow_command"] == "accept_prototype"
    assert approve["workflow_generation"] == workflow["governed"]["generation"]

    interaction = service.conversation_interaction(
        "scenario",
        "recipes",
        conversation_id="conversation.builder.recipes",
        principal_id="user:local",
        command_context_id="webspace:dev1-dev",
    )
    by_command = {item["command"]: item for item in interaction["actions"]}
    assert "accept_prototype" in by_command
    assert by_command["accept_prototype"]["expected_generation"] == workflow["governed"]["generation"]
    assert by_command["accept_prototype"]["command_context_ref"]["id"] == "webspace:dev1-dev"


def test_compact_explanation_answers_state_reason_and_next_from_canonical_snapshot(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    workflow = _plan(service)

    explanation = service.compact_explanation("scenario", "recipes")
    frame = service.interaction_frame("scenario", "recipes")

    assert explanation["state"] == "prototype_editing"
    assert explanation["generation"] == workflow["governed"]["generation"]
    assert explanation["change_ref"] == "change:CH-recipes"
    assert "accept_prototype" in explanation["next_commands"]
    assert "Why:" in explanation["text"]
    assert "Next:" in explanation["text"]
    assert frame["message"] == explanation["text"]
    assert frame["status"]["workflow_state"] == explanation["state"]
    assert frame["status"]["next_commands"] == explanation["next_commands"]
