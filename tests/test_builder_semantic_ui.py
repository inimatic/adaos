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
                        "layout": {"type": "stack", "areas": [{"id": "main", "role": "main"}]},
                        "widgets": [
                            {
                                "id": "recipe-list",
                                "type": "ui.form",
                                "area": "main",
                                "title": "Recipes",
                                "fields": [
                                    {"id": "recipe-name", "type": "text", "label": "Name"}
                                ],
                            }
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
    assert scenario["ui"] == {**webui["ui"], "manifest": "webui.json"}
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
