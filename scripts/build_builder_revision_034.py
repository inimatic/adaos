"""Build Builder UI revision 034 with the direct project catalog read model."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos" / "dev" / "sn_6acf0c01" / "scenarios" / "builder"
REVISION_033 = SCENARIO / "ui_revisions" / "033.json"
REVISION_034 = SCENARIO / "ui_revisions" / "034.json"
WEBUI = SCENARIO / "webui.json"
SCENARIO_JSON = SCENARIO / "scenario.json"
CURRENT = SCENARIO / "ui_revisions" / "current.txt"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build() -> None:
    base = _read(REVISION_033)
    before = copy.deepcopy(base["after_webui"])
    webui = copy.deepcopy(before)
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    page.setdefault("meta", {})["builder"] = {
        "scenario_id": "builder",
        "ui_revision": "034",
        "prototype_base_revision": "029",
        "previous_revision": "033",
        "functional": True,
    }

    picker_widgets = webui["ui"]["application"]["modals"]["project-picker"]["schema"]["widgets"]
    picker = next(widget for widget in picker_widgets if widget.get("id") == "project-picker-list")
    previous_source = dict(picker.get("dataSource") or {})
    picker["dataSource"] = {
        "kind": "api",
        "url": "/api/builder/workbench/projects",
        "method": "GET",
        "params": copy.deepcopy(previous_source.get("params") or {}),
        "prefetch": True,
        "scope": "workspace",
        "includeWebspaceContext": True,
        "cacheTtlMs": 0,
        "invalidationTags": ["builder.project.catalog"],
        "preserveLastValue": True,
    }

    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(webui["ui"])
    _write(WEBUI, webui)
    _write(SCENARIO_JSON, scenario)

    preview_state = copy.deepcopy(base["preview_state"])
    preview_state["version"] = "034"
    preview_state["page_schema"] = copy.deepcopy(page)
    preview_state["user_summary"] = {
        "assumptions": ["The project catalog is a local read model, not a Builder command."],
        "preview": ["Select Project opens without waiting for the skill tool bridge."],
        "risks": ["Catalog mutations must keep invalidating builder.project.catalog."],
        "expected_behavior": ["The project list is fetched directly and then cached until invalidation."],
    }
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": "034",
        "created_at": time.time(),
        "session_id": base["session_id"],
        "scenario_id": "builder",
        "draft_id": base["draft_id"],
        "inference": {
            "source": "codex",
            "prototype_base_revision": "029",
            "previous_revision": "033",
            "sdk_only": True,
        },
        "request": "Load Select Project through a fast, lock-free project catalog read model.",
        "patch": {"operation": "project_catalog_read_model", "base_revision": "033"},
        "llm": {"used": False},
        "before_webui": before,
        "after_webui": webui,
        "preview_state": preview_state,
        "prompt_files": copy.deepcopy(base.get("prompt_files") or []),
    }
    _write(REVISION_034, revision)
    CURRENT.write_text("034\n", encoding="utf-8")


if __name__ == "__main__":
    build()
