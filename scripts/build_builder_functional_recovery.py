"""Build the functional Builder recovery revision from UI 042.

The operation is intentionally deterministic:

* UI revision 042 supplies the complete working control plane;
* the current UI supplies only the Yjs project-selection projection;
* the project picker is upgraded to the bounded ``list_projects`` skill;
* project kind drives real scenario/skill template discovery;
* no LLM output participates in the merge.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

try:
    from scripts.restore_builder_functional_baseline import load_webui_snapshot
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from restore_builder_functional_baseline import load_webui_snapshot


ROOT = Path(__file__).resolve().parents[1]
OWNER = "sn_6acf0c01"
SCENARIO = ROOT / ".adaos" / "dev" / OWNER / "scenarios" / "builder"
WEBUI = SCENARIO / "webui.json"
SCENARIO_JSON = SCENARIO / "scenario.json"
SCENARIO_YAML = SCENARIO / "scenario.yaml"
CURRENT = SCENARIO / "ui_revisions" / "current.txt"
PARITY_CONTRACT = ROOT / "docs" / "architecture" / "builder-functional-parity.json"
REVISION = "054"
BASE_REVISION = "042"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _widget(schema: dict[str, Any], widget_id: str) -> dict[str, Any]:
    return next(item for item in schema["widgets"] if item.get("id") == widget_id)


def _merge_project_selection(
    recovered: dict[str, Any], current: dict[str, Any]
) -> None:
    recovered_page = recovered["ui"]["application"]["desktop"]["pageSchema"]
    current_page = current["ui"]["application"]["desktop"]["pageSchema"]
    source = current_page.get("initialStateSource")
    if not isinstance(source, dict):
        raise ValueError("Current Builder has no project selection projection")
    recovered_page["initialStateSource"] = copy.deepcopy(source)
    state = recovered_page.setdefault("initialState", {})
    state.setdefault("selectedProjectKind", "scenario")
    state.setdefault("projectPickerArchived", False)
    state["newProjectKind"] = "scenario"
    state["selectedTemplate"] = None


def _upgrade_project_picker(recovered: dict[str, Any]) -> None:
    modal = recovered["ui"]["application"]["modals"]["project-picker"]
    picker = _widget(modal["schema"], "project-picker-list")
    picker["dataSource"] = {
        "kind": "skill",
        "name": "builder_sdk_control_skill.list_projects",
        "params": {
            "limit": 200,
            "selected_object_type": "$state.selectedProjectKind",
            "selected_object_id": "$state.selectedProjectId",
            "include_archived": "$state.projectPickerArchived",
        },
        "cacheTtlMs": 0,
        "invalidationTags": ["builder.project.catalog"],
        "preserveLastValue": True,
    }
    inputs = picker.setdefault("inputs", {})
    inputs.update(
        {
            "search": True,
            "searchPlaceholder": "Поиск проектов...",
            "titleKey": "title",
            "subtitleKey": "description",
            "groupBy": "type",
            "groupDisplay": "sections",
            "groupTitleKey": "type",
            "addButton": True,
            "addButtonFirst": True,
            "addButtonLabel": "Создать проект",
            "toolbarToggles": [
                {
                    "id": "archived",
                    "label": "Архивные",
                    "stateKey": "projectPickerArchived",
                }
            ],
            "disableImplicitScenarioSelect": True,
        }
    )
    modal["schema"]["interaction"] = {"initialFocus": "widget:project-picker-list"}


def _upgrade_project_creation(recovered: dict[str, Any]) -> None:
    modal = recovered["ui"]["application"]["modals"]["new-project"]
    form = _widget(modal["schema"], "new-project-form")
    form_inputs = form.setdefault("inputs", {})
    form_inputs["autoCommit"] = True
    kind_field = next(
        item for item in form_inputs["fields"] if item.get("id") == "object_type"
    )
    kind_field["stateKey"] = "newProjectKind"
    kind_field["default"] = "scenario"
    change_action = {
        "on": "change:object_type",
        "type": "updateState",
        "params": {"selectedTemplate": None},
    }
    actions = form.setdefault("actions", [])
    if not any(item.get("on") == "change:object_type" for item in actions):
        actions.insert(0, change_action)
    templates = _widget(modal["schema"], "new-project-templates")
    templates["dataSource"]["params"] = {"object_type": "$state.newProjectKind"}


def _annotate_recovery(
    recovered: dict[str, Any], semantic_version: str | None = None
) -> None:
    ui = recovered["ui"]
    page = ui["application"]["desktop"]["pageSchema"]
    page.setdefault("meta", {}).setdefault("builder", {}).update(
        {
            "ui_revision": REVISION,
            "previous_revision": "053",
            "functional_base_revision": BASE_REVISION,
            "functional_parity_contract": "adaos.builder.functional_parity.v1",
            "binding_mode": "skill",
            "functional": True,
            "typed_contracts": {
                "project": "builder_sdk_control_skill.get_project",
                "lifecycle": "builder_sdk_control_skill.get_lifecycle",
                "file": "builder_sdk_control_skill.read_project_file",
                "templates": "builder_sdk_control_skill.list_templates",
                "projects": "builder_sdk_control_skill.list_projects",
                "changes": "builder_sdk_control_skill.list_changes",
                "preview": "builder_sdk_control_skill.get_preview",
                "automation": "builder_sdk_control_skill.get_automation",
                "publication": "builder_sdk_control_skill.publish_project",
            },
            "proto": f"proto:{REVISION}",
            "active": "active:current",
            "public": "public:current",
            "lifecycle": ["prototype", "automation", "trial", "publication"],
        }
    )
    if semantic_version:
        ui["version"] = semantic_version
    summary = ui.setdefault("user_summary", {})
    summary.setdefault("expected_behavior", []).extend(
        [
            "Builder preserves the complete UI 042 control plane.",
            "Project selection is projected atomically from Yjs state.",
            "Choose Project uses bounded live project data with search and archived filtering.",
            "New projects can be based on scenario or skill templates.",
        ]
    )


def build_recovered_webui(
    baseline: dict[str, Any],
    current: dict[str, Any],
    semantic_version: str | None = None,
) -> dict[str, Any]:
    recovered = copy.deepcopy(baseline)
    _merge_project_selection(recovered, current)
    _upgrade_project_picker(recovered)
    _upgrade_project_creation(recovered)
    _annotate_recovery(recovered, semantic_version)
    return recovered


def build() -> None:
    before = _read(WEBUI)
    baseline = load_webui_snapshot(OWNER, "builder", BASE_REVISION)
    manifest = yaml.safe_load(SCENARIO_YAML.read_text(encoding="utf-8")) or {}
    semantic_version = str(manifest.get("version") or "")
    after = build_recovered_webui(baseline, before, semantic_version)
    chat = _widget(after["ui"]["application"]["desktop"]["pageSchema"], "builder-chat")
    chat.setdefault("actions", [])
    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(after["ui"])
    scenario["ui"]["manifest"] = "webui.json"
    scenario["version"] = str(manifest.get("version") or scenario.get("version") or "")
    scenario["updated_at"] = str(
        manifest.get("updated_at") or scenario.get("updated_at") or ""
    )
    _write(WEBUI, after)
    _write(SCENARIO_JSON, scenario)
    _write(SCENARIO / "assets" / "builder_functional_parity.json", _read(PARITY_CONTRACT))
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": REVISION,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "scenario_id": "builder",
        "request": {
            "text": "Recover the complete Builder UI 042 control plane and forward-port bounded project selection."
        },
        "patch": {
            "id": "builder-functional-recovery-054",
            "target": "ui",
            "operation": "functional_rebase",
            "base_revision": BASE_REVISION,
            "status": "applied",
        },
        "before_webui": before,
        "after_webui": after,
        "preview_state": {},
    }
    _write(SCENARIO / "ui_revisions" / f"{REVISION}.json", revision)
    CURRENT.write_text(REVISION + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
