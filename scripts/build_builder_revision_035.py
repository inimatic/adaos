"""Build Builder UI revision 035 with phase-aware lifecycle and project controls."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos" / "dev" / "sn_6acf0c01" / "scenarios" / "builder"
REVISION_034 = SCENARIO / "ui_revisions" / "034.json"
REVISION_035 = SCENARIO / "ui_revisions" / "035.json"
WEBUI = SCENARIO / "webui.json"
SCENARIO_JSON = SCENARIO / "scenario.json"
CURRENT = SCENARIO / "ui_revisions" / "current.txt"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _widget_map(widgets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id")): item for item in widgets}


def _configure_conversations(page: dict[str, Any], application: dict[str, Any]) -> None:
    widgets = page["widgets"]
    by_id = _widget_map(widgets)
    views = by_id["node-views"]["inputs"]["buttons"]
    conversation_index = next(index for index, item in enumerate(views) if item.get("id") == "chat")
    views[conversation_index : conversation_index + 1] = [
        {
            "id": "prototype-chat",
            "label": "Разговор — Prototype",
            "icon": "chatbubbles-outline",
            "label_i18n": {"key": "builder.text.prototype.conversation"},
        },
        {
            "id": "automation-chat",
            "label": "Разговор — Automation",
            "icon": "construct-outline",
            "label_i18n": {"key": "builder.text.automation.conversation"},
        },
    ]

    prototype_chat = by_id["builder-chat"]
    prototype_chat["title"] = "Разговор — Prototype"
    prototype_chat["title_i18n"] = {"key": "builder.text.prototype.conversation"}
    prototype_chat["visibleIf"] = "$state.activeView === 'prototype-chat'"

    automation_modal = application["modals"]["automation"]["schema"]["widgets"]
    page_automation: list[dict[str, Any]] = []
    for source in automation_modal:
        item = copy.deepcopy(source)
        item["id"] = f"automation-conversation-{str(source['id']).removeprefix('automation-')}"
        item["area"] = "center"
        item["visibleIf"] = "$state.activeView === 'automation-chat'"
        page_automation.append(item)
    start = next(item for item in page_automation if item["id"] == "automation-conversation-start")
    call = next(action for action in start["actions"] if action.get("type") == "callSkill")
    call["params"]["conversation_id"] = "$state.builderConversationId"
    insert_at = widgets.index(prototype_chat) + 1
    widgets[insert_at:insert_at] = page_automation


def _configure_overview(page: dict[str, Any]) -> None:
    widgets = page["widgets"]
    by_id = _widget_map(widgets)
    overview = by_id["node-overview"]
    overview["inputs"]["fields"] = [
        field
        for field in overview["inputs"]["fields"]
        if field.get("id") in {"project_type", "description", "title"}
    ]

    lifecycle_details = {
        "area": "center",
        "id": "overview-lifecycle-node",
        "type": "item.details",
        "title": "Выбранный элемент Lifecycle",
        "visibleIf": "$state.activeView === 'overview'",
        "dataSource": {
            "kind": "skill",
            "name": "builder_sdk_control_skill.get_lifecycle",
            "params": {
                "object_type": "$state.selectedProjectKind",
                "object_id": "$state.selectedProjectId",
            },
            "cacheTtlMs": 0,
            "invalidationTags": ["builder.project.lifecycle"],
            "preserveLastValue": True,
        },
        "inputs": {
            "selectedStateKey": "selectedNodeId",
            "fields": [
                {
                    "key": "version",
                    "label": "Версия",
                    "label_i18n": {"key": "builder.text.version"},
                },
                {
                    "key": "updated_at",
                    "label": "Изменено",
                    "label_i18n": {"key": "builder.text.modified"},
                },
                {
                    "key": "source_prototype_version",
                    "label": "Версия исходного Prototype",
                    "label_i18n": {"key": "builder.text.source.prototype.version"},
                },
            ],
        },
        "title_i18n": {"key": "builder.text.selected.lifecycle.item"},
    }

    reorder_ids = {
        "overview-lifecycle-node",
        "overview-project-state",
        "overview-archive",
        "overview-restore",
    }
    remainder = [item for item in widgets if item.get("id") not in reorder_ids]
    overview_index = next(index for index, item in enumerate(remainder) if item.get("id") == "node-overview")
    remainder[overview_index + 1 : overview_index + 1] = [
        lifecycle_details,
        by_id["overview-project-state"],
        by_id["overview-archive"],
        by_id["overview-restore"],
    ]
    page["widgets"] = remainder


def _configure_project_picker(application: dict[str, Any], page: dict[str, Any]) -> None:
    page["initialState"]["projectPickerArchived"] = False
    page_widgets = _widget_map(page["widgets"])
    page_widgets["project-header"]["inputs"]["stretch"] = True

    tree = page_widgets["project-tree"]
    tree["inputs"]["compactIndent"] = True
    tree["inputs"]["wrapTitles"] = True

    modal = application["modals"]["project-picker"]
    schema = modal["schema"]
    schema["initialState"] = {"projectPickerArchived": False}
    schema["interaction"] = {"initialFocus": "widget:project-picker-list"}
    picker = next(item for item in schema["widgets"] if item.get("id") == "project-picker-list")
    picker["dataSource"]["params"]["include_archived"] = "$state.projectPickerArchived"
    picker["inputs"]["subtitleKey"] = "description"
    picker["inputs"]["addButton"] = True
    picker["inputs"]["addButtonFirst"] = True
    picker["inputs"]["addButtonLabel"] = "Создать проект"
    picker["inputs"]["toolbarToggles"] = [
        {
            "id": "archived",
            "label": "Архивные",
            "stateKey": "projectPickerArchived",
        }
    ]
    picker["actions"].append(
        {"on": "add", "type": "openModal", "params": {"modalId": "new-project"}}
    )
    schema["widgets"] = [item for item in schema["widgets"] if item.get("id") != "project-picker-actions"]


def _update_i18n() -> None:
    additions = {
        "ru": {
            "builder.text.prototype.conversation": "Разговор — Prototype",
            "builder.text.automation.conversation": "Разговор — Automation",
            "builder.text.modified": "Изменено",
            "builder.text.source.prototype.version": "Версия исходного Prototype",
            "builder.text.selected.lifecycle.item": "Выбранный элемент Lifecycle",
        },
        "en": {
            "builder.text.prototype.conversation": "Prototype conversation",
            "builder.text.automation.conversation": "Automation conversation",
            "builder.text.modified": "Modified",
            "builder.text.source.prototype.version": "Source Prototype version",
            "builder.text.selected.lifecycle.item": "Selected Lifecycle item",
        },
    }
    for locale, values in additions.items():
        path = SCENARIO / "assets" / "i18n" / f"{locale}.json"
        catalog = _read(path)
        catalog.update(values)
        _write(path, catalog)


def build() -> None:
    base = _read(REVISION_034)
    before = copy.deepcopy(base["after_webui"])
    webui = copy.deepcopy(before)
    application = webui["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    page.setdefault("meta", {})["builder"] = {
        "scenario_id": "builder",
        "ui_revision": "035",
        "prototype_base_revision": "029",
        "previous_revision": "034",
        "functional": True,
    }

    _configure_conversations(page, application)
    _configure_overview(page)
    _configure_project_picker(application, page)
    _update_i18n()

    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(webui["ui"])
    _write(WEBUI, webui)
    _write(SCENARIO_JSON, scenario)

    preview_state = copy.deepcopy(base["preview_state"])
    preview_state["version"] = "035"
    preview_state["page_schema"] = copy.deepcopy(page)
    preview_state["user_summary"] = {
        "assumptions": ["Prototype conversation remains the canonical Builder transcript."],
        "preview": ["Lifecycle, project selection, Overview, and phase conversations are responsive."],
        "risks": ["Automation remains isolated until its result is explicitly promoted."],
        "expected_behavior": ["Archived projects are hidden by default and Lifecycle metadata follows selection."],
    }
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": "035",
        "created_at": time.time(),
        "session_id": base["session_id"],
        "scenario_id": "builder",
        "draft_id": base["draft_id"],
        "inference": {
            "source": "codex",
            "prototype_base_revision": "029",
            "previous_revision": "034",
            "sdk_only": True,
        },
        "request": "Refine responsive Lifecycle, project selection, Overview, and phase conversations.",
        "patch": {"operation": "builder_phase_ux", "base_revision": "034"},
        "llm": {"used": False},
        "before_webui": before,
        "after_webui": webui,
        "preview_state": preview_state,
        "prompt_files": copy.deepcopy(base.get("prompt_files") or []),
    }
    _write(REVISION_035, revision)
    CURRENT.write_text("035\n", encoding="utf-8")


if __name__ == "__main__":
    build()
