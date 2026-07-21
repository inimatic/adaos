"""Build Builder UI revision 033 with durable project context hydration."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos" / "dev" / "sn_6acf0c01" / "scenarios" / "builder"
REVISION_032 = SCENARIO / "ui_revisions" / "032.json"
REVISION_033 = SCENARIO / "ui_revisions" / "033.json"
WEBUI = SCENARIO / "webui.json"
SCENARIO_JSON = SCENARIO / "scenario.json"
CURRENT = SCENARIO / "ui_revisions" / "current.txt"
SELECT_PREVIEW = "builder_sdk_control_skill.select_preview"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _remove_selection_preview_invalidation(value: Any) -> None:
    if isinstance(value, dict):
        if value.get("type") == "callSkill" and value.get("target") == SELECT_PREVIEW:
            value.pop("invalidates", None)
            value["background"] = True
        for nested in value.values():
            _remove_selection_preview_invalidation(nested)
    elif isinstance(value, list):
        for nested in value:
            _remove_selection_preview_invalidation(nested)


def build() -> None:
    base = _read(REVISION_032)
    before = copy.deepcopy(base["after_webui"])
    webui = copy.deepcopy(before)
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page.setdefault("meta", {})["builder"] = {
        "scenario_id": "builder",
        "ui_revision": "033",
        "prototype_base_revision": "029",
        "previous_revision": "032",
        "functional": True,
    }
    page["initialStateSource"] = {
        "kind": "y",
        "path": "data/builder/selection",
        "map": {
            "selectedProjectKind": "object_type",
            "selectedProjectId": "object_id",
            "selectedProjectRef": "ref",
            "selectedProjectTitle": "title",
            "selectedObjectKind": "object_type",
            "selectedObjectId": "object_id",
            "project.title": "title",
            "project.description": "description",
            "project.type": "object_type",
            "builderTopicId": "topic_id",
            "builderThreadId": "thread_id",
        },
    }
    widgets = {item["id"]: item for item in page["widgets"]}
    project_button = widgets["project-header"]["inputs"]["buttons"][0]
    project_button["label"] = "$state.selectedProjectTitle"
    project_button.pop("label_i18n", None)
    picker = webui["ui"]["application"]["modals"]["project-picker"]["schema"]["widgets"][0]
    picker.setdefault("inputs", {})["disableImplicitScenarioSelect"] = True
    selection_order = {"callSkill": 0, "updateState": 1, "closeModal": 2}
    picker["actions"] = sorted(
        picker.get("actions") or [],
        key=lambda action: selection_order.get(str(action.get("type") or ""), 10),
    )
    _remove_selection_preview_invalidation(webui)

    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(webui["ui"])
    _write(WEBUI, webui)
    _write(SCENARIO_JSON, scenario)

    preview_state = copy.deepcopy(base["preview_state"])
    preview_state["version"] = "033"
    preview_state["page_schema"] = copy.deepcopy(page)
    preview_state["user_summary"] = {
        "assumptions": ["Builder project identity is persisted by the workbench service."],
        "preview": ["Project selection updates Builder data without reloading its host scenario."],
        "risks": ["Scenario projects still reconcile their explicitly related preview asynchronously."],
        "expected_behavior": ["The Builder host remains mounted while project data changes."],
    }
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": "033",
        "created_at": time.time(),
        "session_id": base["session_id"],
        "scenario_id": "builder",
        "draft_id": base["draft_id"],
        "inference": {
            "source": "codex",
            "prototype_base_revision": "029",
            "previous_revision": "032",
            "sdk_only": True,
        },
        "request": "Keep Builder mounted while switching its durable project data context.",
        "patch": {"operation": "durable_project_context", "base_revision": "032"},
        "llm": {"used": False},
        "before_webui": before,
        "after_webui": webui,
        "preview_state": preview_state,
        "prompt_files": copy.deepcopy(base.get("prompt_files") or []),
    }
    _write(REVISION_033, revision)
    CURRENT.write_text("033\n", encoding="utf-8")


if __name__ == "__main__":
    build()
