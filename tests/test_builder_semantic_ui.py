from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.builder.semantic_ui import BuilderSemanticUIService
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService


@pytest.fixture
def semantic_project(tmp_path: Path) -> tuple[BuilderSemanticUIService, BuilderWorkflowService, Path]:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    root = scenarios / "recipes"
    skills.mkdir()
    root.mkdir(parents=True)
    (root / "scenario.yaml").write_text("id: recipes\nversion: 0.1.0\n", encoding="utf-8")
    webui = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "recipes-page",
                        "layout": {
                            "version": 2,
                            "pattern": "document",
                            "density": "comfortable",
                            "contentWidth": "fluid",
                            "scroll": "page",
                            "regions": [
                                {
                                    "id": "main",
                                    "role": "main",
                                    "presentation": {"wide": "pane", "compact": "stack"},
                                }
                            ],
                        },
                        "widgets": [
                            {
                                "id": "recipe-list",
                                "type": "ui.form",
                                "area": "main",
                                "title": "Recipes",
                                "fields": [
                                    {"id": "recipe-name", "type": "text", "label": "Name"}
                                ],
                            },
                            {
                                "id": "recipe-summary",
                                "type": "item.details",
                                "area": "main",
                                "title": "Summary",
                                "dataSource": {
                                    "kind": "static",
                                    "value": {"title": "Summary"},
                                },
                            },
                        ],
                    }
                }
            }
        },
    }
    (root / "webui.json").write_text(
        json.dumps(webui, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "scenario.json").write_text(
        json.dumps({"id": "recipes", "version": "0.1.0", "ui": webui["ui"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    revisions = root / "ui_revisions"
    revisions.mkdir()
    (revisions / "001.json").write_text("{}\n", encoding="utf-8")
    (revisions / "current.txt").write_text("001\n", encoding="utf-8")
    workflow = BuilderWorkflowService(
        dev_skills_root=skills,
        dev_scenarios_root=scenarios,
        state_dir=tmp_path / "state",
    )
    workflow.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-recipes-label",
            "request": "Rename the recipe field.",
            "issues": [
                {
                    "issue_id": "recipe-label",
                    "title": "Rename the recipe field",
                    "lane": "prototype",
                    "acceptance_criteria": ["The name field uses the new label."],
                }
            ],
        },
    )
    return BuilderSemanticUIService(workflow=workflow), workflow, root


def test_semantic_rename_creates_valid_revision_and_undo(
    semantic_project: tuple[BuilderSemanticUIService, BuilderWorkflowService, Path],
) -> None:
    service, workflow, root = semantic_project

    result = service.apply(
        {
            "schema": "adaos.builder.semantic_ui_change.v1",
            "operation_id": "RUN-rename-recipe-name",
            "change_id": "CH-recipes-label",
            "project_ref": "scenario:recipes",
            "operation": "rename",
            "target_ref": "field:recipe-list:recipe-name",
            "source_revision": "001",
            "value": "Recipe name",
            "risk": "local_reversible",
            "acceptance": {"property": "label", "equals": "Recipe name"},
        }
    )

    webui = json.loads((root / "webui.json").read_text(encoding="utf-8"))
    scenario = json.loads((root / "scenario.json").read_text(encoding="utf-8"))
    field = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["fields"][0]
    assert field["label"] == "Recipe name"
    assert scenario["ui"] == {"manifest": "webui.json"}
    assert result["revision"] == "002"
    assert result["undo"] == {
        "operation": "set_property",
        "property": "label",
        "value": "Name",
        "remove_when_null": False,
    }
    revision = json.loads((root / "ui_revisions" / "002.json").read_text(encoding="utf-8"))
    assert revision["patch"]["source_revision"] == "001"
    assert revision["patch"]["undo"] == result["undo"]
    assert (root / "ui_revisions" / "current.txt").read_text(encoding="utf-8").strip() == "002"
    state = workflow.describe("scenario", "recipes")
    assert state["prototype"]["head_revision"] == "002"
    assert state["change"]["runs"][-1]["run_id"] == "RUN-rename-recipe-name"
    assert state["change"]["runs"][-1]["context_packet_digest"] == result["context_packet_digest"]


def test_semantic_rename_rejects_stale_revision_without_filesystem_mutation(
    semantic_project: tuple[BuilderSemanticUIService, BuilderWorkflowService, Path],
) -> None:
    service, _workflow, root = semantic_project
    before = (root / "webui.json").read_bytes()

    with pytest.raises(BuilderWorkflowError, match="stale semantic UI source revision"):
        service.apply(
            {
                "schema": "adaos.builder.semantic_ui_change.v1",
                "operation_id": "RUN-stale-rename",
                "change_id": "CH-recipes-label",
                "project_ref": "scenario:recipes",
                "operation": "rename",
                "target_ref": "widget:recipe-list",
                "source_revision": "000",
                "value": "My recipes",
                "risk": "local_reversible",
            }
        )

    assert (root / "webui.json").read_bytes() == before
    assert not (root / "ui_revisions" / "002.json").exists()


def test_semantic_field_add_and_remove_are_revisioned_and_reversible(
    semantic_project: tuple[BuilderSemanticUIService, BuilderWorkflowService, Path],
) -> None:
    service, _workflow, root = semantic_project
    added = service.apply(
        {
            "schema": "adaos.builder.semantic_ui_change.v1",
            "operation_id": "RUN-add-recipe-notes",
            "change_id": "CH-recipes-label",
            "project_ref": "scenario:recipes",
            "operation": "add",
            "target_ref": "widget:recipe-list",
            "source_revision": "001",
            "value": {
                "collection": "fields",
                "index": 1,
                "item": {"id": "recipe-notes", "type": "text", "label": "Notes"},
            },
            "risk": "local_reversible",
        }
    )
    assert added["revision"] == "002"
    assert added["undo"]["target_ref"] == "field:recipe-list:recipe-notes"
    webui = json.loads((root / "webui.json").read_text(encoding="utf-8"))
    fields = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["fields"]
    assert [item["id"] for item in fields] == ["recipe-name", "recipe-notes"]

    removed = service.apply(
        {
            "schema": "adaos.builder.semantic_ui_change.v1",
            "operation_id": "RUN-remove-recipe-notes",
            "change_id": "CH-recipes-label",
            "project_ref": "scenario:recipes",
            "operation": "remove",
            "target_ref": "field:recipe-list:recipe-notes",
            "source_revision": "002",
            "value": None,
            "risk": "local_reversible",
        }
    )
    assert removed["revision"] == "003"
    assert removed["undo"] == {
        "operation": "add",
        "parent_ref": "widget:recipe-list",
        "collection": "fields",
        "index": 1,
        "value": {"id": "recipe-notes", "type": "text", "label": "Notes"},
    }


def test_semantic_data_mode_switch_does_not_create_ui_revision(
    semantic_project: tuple[BuilderSemanticUIService, BuilderWorkflowService, Path],
) -> None:
    service, workflow, root = semantic_project
    before = workflow.describe("scenario", "recipes")
    configured = workflow.configure_binding_profile(
        "scenario",
        "recipes",
        {
            "profile_id": "fixture-recipes",
            "mode": "fixture",
            "logical_schema_ref": "schema:recipes:prototype",
            "source_ref": "fixture:recipes:sample",
            "owner": "builder",
        },
        expected_binding_generation=before["data_binding"]["generation"],
    )["workflow"]

    result = service.apply(
        {
            "schema": "adaos.builder.semantic_ui_change.v1",
            "operation_id": "RUN-select-fixture",
            "change_id": "CH-recipes-label",
            "project_ref": "scenario:recipes",
            "operation": "set_data_mode",
            "target_ref": "widget:recipe-list",
            "source_revision": "001",
            "value": {"profile_id": "fixture-recipes"},
            "risk": "local_reversible",
        }
    )

    assert result["ui_revision_changed"] is False
    assert result["revision"] == "001"
    assert result["binding"]["selected_mode"] == "fixture"
    assert not (root / "ui_revisions" / "002.json").exists()
    assert configured["prototype"]["head_revision"] == "001"


def test_semantic_move_uses_stable_anchor_and_records_reversible_order(
    semantic_project: tuple[BuilderSemanticUIService, BuilderWorkflowService, Path],
) -> None:
    service, _workflow, root = semantic_project
    result = service.apply(
        {
            "schema": "adaos.builder.semantic_ui_change.v1",
            "operation_id": "RUN-move-summary",
            "change_id": "CH-recipes-label",
            "project_ref": "scenario:recipes",
            "operation": "move",
            "target_ref": "widget:recipe-summary",
            "source_revision": "001",
            "value": {"before_ref": "widget:recipe-list", "breakpoints": ["wide", "compact"]},
            "risk": "local_reversible",
        }
    )
    webui = json.loads((root / "webui.json").read_text(encoding="utf-8"))
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    assert [item["id"] for item in widgets] == ["recipe-summary", "recipe-list"]
    assert result["undo"]["value"]["index"] == 1

    restored = service.apply({**result["operation"], "operation_id": "RUN-undo-move",
                              "source_revision": result["revision"], "value": result["undo"]["value"]})
    widgets = json.loads((root / "webui.json").read_text(encoding="utf-8"))["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    assert restored["ok"]
    assert [item["id"] for item in widgets] == ["recipe-list", "recipe-summary"]


def _label_operation(**changes):
    return {"schema": "adaos.builder.semantic_ui_change.v1", "operation_id": "label-op",
            "change_id": "CH-recipes-label", "project_ref": "scenario:recipes", "operation": "rename",
            "target_ref": "field:recipe-list:recipe-name", "source_revision": "001",
            "value": "Recipe name", "risk": "local_reversible", **changes}


@pytest.mark.parametrize("condition", [
    {"kind": "unknown", "value": False},
    {"kind": "property_equals", "property": "label", "value": "Wrong"},
    {"kind": "workflow_generation", "value": -1},
    {"kind": "source_digest", "value": "sha256:wrong"},
])
def test_semantic_preconditions_fail_closed_without_mutation(semantic_project, condition):
    service, workflow, root = semantic_project
    before = (root / "webui.json").read_bytes()
    generation = workflow.describe("scenario", "recipes")["generation"]
    with pytest.raises(BuilderWorkflowError, match="precondition"):
        service.apply(_label_operation(preconditions=[condition]))
    assert (root / "webui.json").read_bytes() == before
    assert workflow.describe("scenario", "recipes")["generation"] == generation
    assert not (root / "ui_revisions" / "002.json").exists()


def test_semantic_exact_retry_is_durable_and_conflict_is_not_a_new_revision(semantic_project):
    service, workflow, root = semantic_project
    operation = _label_operation(preconditions=[{"kind": "property_equals", "property": "label", "value": "Name"}])
    first = service.apply(operation)
    recovered = BuilderSemanticUIService(workflow=workflow).apply(operation)
    assert recovered["revision"] == first["revision"]
    assert recovered["replayed"] is True
    with pytest.raises(BuilderWorkflowError, match="identity conflicts"):
        service.apply({**operation, "value": "Another label", "source_revision": first["revision"]})
    assert not (root / "ui_revisions" / "003.json").exists()


def test_semantic_rename_undo_is_executable(semantic_project):
    service, _, root = semantic_project
    result = service.apply(_label_operation())
    service.apply(_label_operation(operation="set_property", operation_id="undo-label",
                                  source_revision=result["revision"], value=result["undo"]))
    field = json.loads((root / "webui.json").read_text(encoding="utf-8"))["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["fields"][0]
    assert field["label"] == "Name"


def test_renderer_edits_cannot_leave_a_managed_semantic_source_stale(semantic_project):
    service, _, root = semantic_project
    (root / "semantic.webui.json").write_text('{}', encoding="utf-8")
    before = (root / "webui.json").read_bytes()
    with pytest.raises(BuilderWorkflowError, match="requires graph operations"):
        service.apply(_label_operation())
    assert (root / "webui.json").read_bytes() == before


def test_failed_operation_receipt_rolls_back_ui_and_workflow(semantic_project, monkeypatch):
    from adaos.services.builder import semantic_ui
    service, workflow, root = semantic_project
    before_ui = (root / "webui.json").read_bytes()
    before_workflow = workflow.describe("scenario", "recipes")
    write = semantic_ui._write_bytes_atomic

    def fail_receipt(path, raw):
        if "semantic_operations" in path.parts:
            raise OSError("injected receipt persistence failure")
        write(path, raw)

    monkeypatch.setattr(semantic_ui, "_write_bytes_atomic", fail_receipt)
    with pytest.raises(OSError, match="receipt persistence"):
        service.apply(_label_operation())
    assert (root / "webui.json").read_bytes() == before_ui
    after = workflow.describe("scenario", "recipes")
    assert after["generation"] == before_workflow["generation"]
    assert after["prototype"]["head_revision"] == "001"
    assert not (root / "ui_revisions" / "002.json").exists()


def test_failed_binding_receipt_does_not_leave_a_changed_profile(semantic_project, monkeypatch):
    from adaos.services.builder import semantic_ui
    service, workflow, root = semantic_project
    state = workflow.describe("scenario", "recipes")
    workflow.configure_binding_profile("scenario", "recipes", {
        "profile_id": "binding-fixture", "mode": "fixture", "logical_schema_ref": "schema:recipes:prototype",
        "source_ref": "fixture:recipes:sample", "owner": "builder",
    }, expected_binding_generation=state["data_binding"]["generation"])
    before = workflow.describe("scenario", "recipes")["data_binding"]
    write = semantic_ui._write_bytes_atomic
    def fail(path, raw):
        if "semantic_operations" in path.parts:
            raise OSError("binding receipt failure")
        write(path, raw)
    monkeypatch.setattr(semantic_ui, "_write_bytes_atomic", fail)
    with pytest.raises(OSError, match="binding receipt"):
        service.apply(_label_operation(operation="set_data_mode", value={"profile_id": "binding-fixture"}))
    assert workflow.describe("scenario", "recipes")["data_binding"] == before
