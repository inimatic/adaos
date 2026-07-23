"""Build Builder UI revision 037 with one Lifecycle-aware work area."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos" / "dev" / "sn_6acf0c01" / "scenarios" / "builder"
REVISION_036 = SCENARIO / "ui_revisions" / "036.json"
REVISION_037 = SCENARIO / "ui_revisions" / "037.json"
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


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _replace_conversation_tabs(page: dict[str, Any]) -> None:
    widgets = page["widgets"]
    by_id = _widget_map(widgets)
    views = by_id["node-views"]["inputs"]["buttons"]
    first_index = next(index for index, item in enumerate(views) if item.get("id") == "prototype-chat")
    views[first_index : first_index + 2] = [
        {
            "id": "conversation",
            "label": "$state.lifecycleConversationLabel",
            "icon": "chatbubbles-outline",
        }
    ]

    prototype_visible = (
        "$state.activeView === 'conversation' && "
        "$state.selectedLifecycleStage === 'prototype'"
    )
    automation_visible = (
        "$state.activeView === 'conversation' && "
        "$state.selectedLifecycleStage === 'automation'"
    )
    publication_visible = (
        "$state.activeView === 'conversation' && "
        "$state.selectedLifecycleStage === 'publication'"
    )
    by_id["builder-chat"]["visibleIf"] = prototype_visible
    by_id["chat-side-settings"]["visibleIf"] = prototype_visible
    for widget_id in ("automation-conversation-start", "automation-conversation-followup"):
        by_id[widget_id]["visibleIf"] = automation_visible
    automation_state = by_id["automation-conversation-state"]
    automation_state["visibleIf"] = automation_visible
    automation_state["area"] = "right"
    automation_fields = automation_state["inputs"]["fields"]
    automation_fields[0:0] = [
        {"key": "version", "label": "Version"},
        {"key": "source_prototype_version", "label": "Source Prototype version"},
        {"key": "updated_at", "label": "Modified", "format": "datetime"},
    ]

    publication_widgets = []
    application = page["_application"]
    for source in application["modals"]["publication"]["schema"]["widgets"]:
        item = copy.deepcopy(source)
        item["id"] = f"publication-workspace-{source['id'].removeprefix('publication-')}"
        item["visibleIf"] = publication_visible
        item["area"] = "right" if source["id"] == "publication-status" else "center"
        publication_widgets.append(item)
    insert_at = widgets.index(automation_state) + 1
    widgets[insert_at:insert_at] = publication_widgets


def _configure_lifecycle_selection(page: dict[str, Any]) -> None:
    page["initialState"]["selectedLifecycleStage"] = "prototype"
    page["initialState"]["lifecycleConversationLabel"] = "Prototype conversation"
    tree = _widget_map(page["widgets"])["project-tree"]
    select_action = next(action for action in tree["actions"] if action.get("on") == "select")
    select_action["params"].update(
        {
            "selectedLifecycleStage": "$event.lifecycleStage",
            "lifecycleConversationLabel": "$event.conversationLabel",
        }
    )

    application = page["_application"]
    picker = next(
        item
        for item in application["modals"]["project-picker"]["schema"]["widgets"]
        if item.get("id") == "project-picker-list"
    )
    picker_state = next(action for action in picker["actions"] if action.get("type") == "updateState")
    picker_state["params"].update(
        {
            "selectedNodeId": "stage-proto",
            "selectedLifecycleStage": "prototype",
            "lifecycleConversationLabel": "Prototype conversation",
        }
    )


def _configure_dates_and_width(page: dict[str, Any]) -> None:
    by_id = _widget_map(page["widgets"])
    by_id["project-header"]["inputs"]["stretch"] = True
    by_id["project-tree"]["inputs"].update({"compactIndent": True, "wrapTitles": True})
    for field in by_id["overview-lifecycle-node"]["inputs"]["fields"]:
        if field.get("key") == "updated_at":
            field["format"] = "datetime"


def _configure_preview_invalidation(application: dict[str, Any]) -> None:
    for value in _walk(application):
        if value.get("type") == "callSkill" and value.get("target") == "builder_sdk_control_skill.select_preview":
            value["invalidates"] = [
                "builder.project.preview",
                "builder.project.lifecycle",
            ]


def build() -> None:
    base = _read(REVISION_036)
    before = copy.deepcopy(base["after_webui"])
    webui = copy.deepcopy(before)
    application = webui["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    # Private construction-only reference; removed before serializing.
    page["_application"] = application
    page.setdefault("meta", {})["builder"] = {
        "scenario_id": "builder",
        "ui_revision": "037",
        "prototype_base_revision": "029",
        "previous_revision": "036",
        "functional": True,
    }

    _replace_conversation_tabs(page)
    _configure_lifecycle_selection(page)
    _configure_dates_and_width(page)
    page.pop("_application", None)
    _configure_preview_invalidation(application)

    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(webui["ui"])
    _write(WEBUI, webui)
    _write(SCENARIO_JSON, scenario)

    preview_state = copy.deepcopy(base["preview_state"])
    preview_state["version"] = "037"
    preview_state["page_schema"] = copy.deepcopy(page)
    preview_state["user_summary"] = {
        "assumptions": ["The selected Lifecycle node determines the phase work area."],
        "preview": ["One dynamic phase tab replaces competing Prototype and Automation tabs."],
        "risks": ["Published preview target selection remains a separate runtime enhancement."],
        "expected_behavior": [
            "Prototype keeps Development settings, Automation shows execution metadata, and Publication shows release controls."
        ],
    }
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": "037",
        "created_at": time.time(),
        "session_id": base["session_id"],
        "scenario_id": "builder",
        "draft_id": base["draft_id"],
        "inference": {
            "source": "codex",
            "prototype_base_revision": "029",
            "previous_revision": "036",
            "sdk_only": True,
        },
        "request": "Make the Builder work area follow the selected Lifecycle phase.",
        "patch": {"operation": "builder_lifecycle_work_area", "base_revision": "036"},
        "llm": {"used": False},
        "before_webui": before,
        "after_webui": webui,
        "preview_state": preview_state,
        "prompt_files": copy.deepcopy(base.get("prompt_files") or []),
    }
    _write(REVISION_037, revision)
    CURRENT.write_text("037\n", encoding="utf-8")


if __name__ == "__main__":
    build()
