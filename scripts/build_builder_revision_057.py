"""Declare the Builder surface identity on every skill-backed UI operation."""

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
REVISION = "057"
VERSION = "0.2.50"
SURFACE_META = {"scenario_id": "builder", "current_scenario": "builder"}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _declare_surface_identity(value: Any) -> int:
    changed = 0
    if isinstance(value, dict):
        data_source = value.get("dataSource")
        if isinstance(data_source, dict) and data_source.get("kind") == "skill":
            params = data_source.setdefault("params", {})
            if isinstance(params, dict):
                metadata = params.setdefault("_meta", {})
                if isinstance(metadata, dict):
                    before = dict(metadata)
                    metadata.update(SURFACE_META)
                    changed += int(metadata != before)
        if value.get("type") == "callSkill":
            params = value.setdefault("params", {})
            if isinstance(params, dict):
                metadata = params.setdefault("_meta", {})
                if isinstance(metadata, dict):
                    before = dict(metadata)
                    metadata.update(SURFACE_META)
                    changed += int(metadata != before)
        for child in value.values():
            changed += _declare_surface_identity(child)
    elif isinstance(value, list):
        for child in value:
            changed += _declare_surface_identity(child)
    return changed


def build() -> None:
    before = _read(WEBUI)
    after = copy.deepcopy(before)
    changes = _declare_surface_identity(after["ui"]["application"])
    if changes < 1:
        raise RuntimeError("Builder revision 057 did not find skill-backed UI operations")

    page = after["ui"]["application"]["desktop"]["pageSchema"]
    page.setdefault("meta", {}).setdefault("builder", {}).update(
        {
            "ui_revision": REVISION,
            "previous_revision": "056",
            "surface_identity": "builder",
        }
    )
    after["ui"]["version"] = REVISION
    summary = after["ui"].setdefault("user_summary", {})
    summary.setdefault("expected_behavior", []).append(
        "Every skill-backed Builder operation declares the active Builder surface so nested preview actions remain isolated."
    )

    manifest = yaml.safe_load(SCENARIO_YAML.read_text(encoding="utf-8")) or {}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    manifest["version"] = VERSION
    manifest["updated_at"] = now
    SCENARIO_YAML.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    scenario = _read(SCENARIO_JSON)
    scenario["ui"] = copy.deepcopy(after["ui"])
    scenario["version"] = VERSION
    scenario["updated_at"] = now

    _write(WEBUI, after)
    _write(SCENARIO_JSON, scenario)
    revision = {
        "schema": "adaos.builder.ui_revision.v1",
        "revision": REVISION,
        "created_at": now,
        "scenario_id": "builder",
        "request": {
            "text": "Keep nested Builder preview operations bound to their active Builder surface."
        },
        "patch": {
            "id": "builder-surface-identity-057",
            "target": "ui.skill_operations",
            "operation": "declare_active_surface_identity",
            "changed_operations": changes,
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
