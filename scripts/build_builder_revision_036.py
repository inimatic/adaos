"""Build Builder UI revision 036 with reliable archived-project state."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos" / "dev" / "sn_6acf0c01" / "scenarios" / "builder"
REVISION_035 = SCENARIO / "ui_revisions" / "035.json"
REVISION_036 = SCENARIO / "ui_revisions" / "036.json"
WEBUI = SCENARIO / "webui.json"
SCENARIO_JSON = SCENARIO / "scenario.json"
CURRENT = SCENARIO / "ui_revisions" / "current.txt"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def build() -> None:
    base = _read(REVISION_035)
    before = copy.deepcopy(base["after_webui"])
    webui = copy.deepcopy(before)
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page["initialState"]["projectArchived"] = False
    page.setdefault("meta", {})["builder"] = {
        "scenario_id": "builder",
        "ui_revision": "036",
        "prototype_base_revision": "029",
        "previous_revision": "035",
        "functional": True,
    }

    for node in _walk(webui):
        for expression_key in ("visibleIf", "enabledIf"):
            expression = node.get(expression_key)
            if isinstance(expression, str):
                node[expression_key] = expression.replace(
                    "$state.project.archived",
                    "$state.projectArchived",
                )
        params = node.get("params")
        if isinstance(params, dict) and "project.archived" in params:
            params["projectArchived"] = params.pop("project.archived")

    widgets = {item["id"]: item for item in page["widgets"]}
    widgets["overview-project-state"].setdefault("inputs", {})["stateBindings"] = {
        "projectArchived": "archived"
    }

    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(webui["ui"])
    _write(WEBUI, webui)
    _write(SCENARIO_JSON, scenario)

    preview_state = copy.deepcopy(base["preview_state"])
    preview_state["version"] = "036"
    preview_state["page_schema"] = copy.deepcopy(page)
    preview_state["user_summary"] = {
        "assumptions": ["Project metadata remains the source of truth for archive state."],
        "preview": ["Overview switches between Archive and Restore after metadata loads."],
        "risks": ["None beyond the existing project metadata availability."],
        "expected_behavior": ["Archived current projects always expose Restore in Overview."],
    }
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": "036",
        "created_at": time.time(),
        "session_id": base["session_id"],
        "scenario_id": "builder",
        "draft_id": base["draft_id"],
        "inference": {
            "source": "codex",
            "prototype_base_revision": "029",
            "previous_revision": "035",
            "sdk_only": True,
        },
        "request": "Show Restore whenever the current Builder project is archived.",
        "patch": {"operation": "bind_project_archive_state", "base_revision": "035"},
        "llm": {"used": False},
        "before_webui": before,
        "after_webui": webui,
        "preview_state": preview_state,
        "prompt_files": copy.deepcopy(base.get("prompt_files") or []),
    }
    _write(REVISION_036, revision)
    CURRENT.write_text("036\n", encoding="utf-8")


if __name__ == "__main__":
    build()
