"""Build Builder UI revision 040 without central-draft synchronization controls."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / ".adaos" / "dev" / "sn_6acf0c01" / "scenarios" / "builder"
WEBUI = SCENARIO / "webui.json"
SCENARIO_JSON = SCENARIO / "scenario.json"
SCENARIO_YAML = SCENARIO / "scenario.yaml"
CURRENT = SCENARIO / "ui_revisions" / "current.txt"
REVISION = "040"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _widget(schema: dict[str, Any], widget_id: str) -> dict[str, Any]:
    return next(item for item in schema["widgets"] if item.get("id") == widget_id)


def _remove_update(widget: dict[str, Any]) -> None:
    inputs = widget.setdefault("inputs", {})
    buttons = inputs.get("buttons")
    if isinstance(buttons, list):
        inputs["buttons"] = [item for item in buttons if item.get("id") != "update"]
    actions = widget.get("actions")
    if isinstance(actions, list):
        widget["actions"] = [
            item
            for item in actions
            if item.get("on") != "click:update"
            and item.get("target") != "builder_sdk_control_skill.update_project"
        ]
    widget["title"] = "Версионирование и публикация"


def build() -> None:
    before = _read(WEBUI)
    after = copy.deepcopy(before)
    application = after["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    _remove_update(_widget(page, "publication-workspace-actions"))
    publication_modal = application["modals"]["publication"]["schema"]
    _remove_update(_widget(publication_modal, "publication-actions"))

    page.setdefault("meta", {}).setdefault("builder", {}).update(
        {
            "ui_revision": REVISION,
            "previous_revision": "039",
        }
    )
    after["ui"]["version"] = REVISION
    summary = after["ui"].setdefault("user_summary", {})
    summary.setdefault("assumptions", []).append(
        "Central-draft synchronization is intentionally unavailable until divergence signaling is implemented."
    )
    summary.setdefault("expected_behavior", []).append(
        "Publication exposes checkpoint, release validation, publish, and delete; it never overwrites DEV from Root."
    )

    manifest = yaml.safe_load(SCENARIO_YAML.read_text(encoding="utf-8")) or {}
    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(after["ui"])
    scenario["version"] = str(manifest.get("version") or scenario.get("version") or "")
    scenario["updated_at"] = str(manifest.get("updated_at") or scenario.get("updated_at") or "")

    _write(WEBUI, after)
    _write(SCENARIO_JSON, scenario)
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": REVISION,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "scenario_id": "builder",
        "request": {
            "text": "Remove unsafe central-draft update until repository divergence signaling is designed."
        },
        "patch": {
            "id": "builder-remove-update-040",
            "target": "ui",
            "operation": "remove_central_draft_update",
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
