"""Build Builder UI revision 038 with a deterministic project-picker default."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos" / "dev" / "sn_6acf0c01" / "scenarios" / "builder"
REVISION_037 = SCENARIO / "ui_revisions" / "037.json"
REVISION_038 = SCENARIO / "ui_revisions" / "038.json"
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
    base = _read(REVISION_037)
    before = copy.deepcopy(base["after_webui"])
    webui = copy.deepcopy(before)
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page.setdefault("meta", {})["builder"] = {
        "scenario_id": "builder",
        "ui_revision": "038",
        "prototype_base_revision": "029",
        "previous_revision": "037",
        "functional": True,
    }

    for widget in _walk(page):
        actions = widget.get("actions")
        if not isinstance(actions, list):
            continue
        for index, action in enumerate(list(actions)):
            if (
                action.get("type") == "openModal"
                and action.get("params", {}).get("modalId") == "project-picker"
            ):
                actions.insert(
                    index,
                    {
                        "on": action.get("on", "click"),
                        "type": "updateState",
                        "params": {"projectPickerArchived": False},
                    },
                )
                break

    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(webui["ui"])
    _write(WEBUI, webui)
    _write(SCENARIO_JSON, scenario)

    preview_state = copy.deepcopy(base["preview_state"])
    preview_state["version"] = "038"
    preview_state["page_schema"] = copy.deepcopy(page)
    preview_state["user_summary"] = {
        "assumptions": ["Archived projects are an opt-in view on every picker opening."],
        "preview": ["Project picker opens with archived filtering disabled and search focused."],
        "risks": [],
        "expected_behavior": ["Closing and reopening Select project resets Archived to off."],
    }
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": "038",
        "created_at": time.time(),
        "session_id": base["session_id"],
        "scenario_id": "builder",
        "draft_id": base["draft_id"],
        "inference": {
            "source": "codex",
            "prototype_base_revision": "029",
            "previous_revision": "037",
            "sdk_only": True,
        },
        "request": "Reset archived-project filtering whenever Select project opens.",
        "patch": {"operation": "project_picker_default_filter", "base_revision": "037"},
        "llm": {"used": False},
        "before_webui": before,
        "after_webui": webui,
        "preview_state": preview_state,
        "prompt_files": copy.deepcopy(base.get("prompt_files") or []),
    }
    _write(REVISION_038, revision)
    CURRENT.write_text("038\n", encoding="utf-8")


if __name__ == "__main__":
    build()
