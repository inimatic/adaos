"""Build Builder UI 056 from UI 055 with a compact project-only header.

The selected project keeps the full center width. Change and Preview context
remain visible in the left navigation, where they do not truncate long project
titles on compact layouts.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
OWNER = "sn_6acf0c01"
SCENARIO = ROOT / ".adaos" / "dev" / OWNER / "scenarios" / "builder"
WEBUI = SCENARIO / "webui.json"
SCENARIO_JSON = SCENARIO / "scenario.json"
SCENARIO_YAML = SCENARIO / "scenario.yaml"
REVISIONS = SCENARIO / "ui_revisions"
CURRENT = REVISIONS / "current.txt"
BASE_REVISION = "055"
REVISION = "056"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _widget(page: dict[str, Any], widget_id: str) -> dict[str, Any]:
    matches = [item for item in page["widgets"] if item.get("id") == widget_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one widget {widget_id!r}; found {len(matches)}")
    return matches[0]


def transform(base: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    page = result["ui"]["application"]["desktop"]["pageSchema"]
    builder_meta = page.setdefault("meta", {}).setdefault("builder", {})
    builder_meta["ui_revision"] = REVISION
    builder_meta["previous_revision"] = BASE_REVISION
    builder_meta["proto"] = f"proto:{REVISION}"

    header = _widget(page, "project-header")
    header["title"] = "Project"
    header["inputs"]["buttons"] = [
        {
            "id": "project-label",
            "label": "$state.selectedProjectTitle",
            "icon": "folder-outline",
        }
    ]

    left = _widget(page, "left-actions")
    buttons = [
        item
        for item in left["inputs"]["buttons"]
        if item.get("id") not in {"change-context", "preview-context"}
    ]
    buttons.extend(
        [
            {
                "id": "change-context",
                "label": "$state.changeLabel",
                "icon": "git-branch-outline",
                "disabled": True,
            },
            {
                "id": "preview-context",
                "label": "$state.previewViewingLabel",
                "icon": "eye-outline",
                "disabled": True,
            },
        ]
    )
    left["inputs"]["buttons"] = buttons
    return result


def build() -> None:
    current = CURRENT.read_text(encoding="utf-8-sig").strip()
    if current not in {BASE_REVISION, REVISION}:
        raise ValueError(
            f"Builder UI moved from the reviewed {BASE_REVISION} base: current={current}"
        )
    base_record = _read(REVISIONS / f"{BASE_REVISION}.json")
    before = base_record.get("after_webui")
    if not isinstance(before, dict):
        raise ValueError(f"UI revision {BASE_REVISION} has no after_webui")
    before = copy.deepcopy(before)
    after = transform(before)

    manifest = yaml.safe_load(SCENARIO_YAML.read_text(encoding="utf-8-sig")) or {}
    source_version = str(manifest.get("version") or "")
    after["ui"]["version"] = source_version
    scenario = _read(SCENARIO_JSON)
    scenario["version"] = source_version
    scenario["updated_at"] = str(
        manifest.get("updated_at") or scenario.get("updated_at") or ""
    )
    scenario["ui"] = copy.deepcopy(after["ui"])
    scenario["ui"]["manifest"] = "webui.json"

    _write(WEBUI, after)
    _write(SCENARIO_JSON, scenario)
    _write(
        REVISIONS / f"{REVISION}.json",
        {
            "schema": "adaos.builder.ui_revision.v1",
            "revision": REVISION,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "scenario_id": "builder",
            "request": {
                "text": "Keep the full header width for long project titles and move Change/Preview context left."
            },
            "patch": {
                "id": "builder-compact-context-header-056",
                "target": "ui",
                "operation": "move_context_labels",
                "base_revision": BASE_REVISION,
                "status": "applied",
            },
            "before_webui": before,
            "after_webui": after,
            "preview_state": {},
        },
    )
    CURRENT.write_text(REVISION + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
