"""Restore a Builder UI snapshot into a DEV scenario deterministically.

This is a recovery tool, not an LLM migration.  It reads a durable Builder
``ui_revisions/<revision>.json`` checkpoint, copies its ``after_webui`` value
into another DEV scenario, rebinds the initial project identity to that target,
and keeps ``scenario.json`` synchronized with ``scenario.yaml``.

The primary use is an executable reference scenario for A/B recovery work.
The stable Workspace scenario is never touched.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OWNER = "sn_6acf0c01"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _scenario_dir(owner: str, name: str) -> Path:
    return ROOT / ".adaos" / "dev" / owner / "scenarios" / name


def load_webui_snapshot(owner: str, source: str, revision: str) -> dict[str, Any]:
    revision_path = _scenario_dir(owner, source) / "ui_revisions" / f"{revision}.json"
    record = _read_json(revision_path)
    snapshot = record.get("after_webui")
    if not isinstance(snapshot, dict):
        raise ValueError(f"Builder UI revision {revision!r} has no after_webui snapshot")
    if snapshot.get("schema") != "adaos.webui.v1":
        raise ValueError(f"Builder UI revision {revision!r} is not adaos.webui.v1")
    return copy.deepcopy(snapshot)


def rebind_reference(webui: dict[str, Any], target: str, revision: str) -> None:
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    state = page.setdefault("initialState", {})
    title = f"Builder reference — UI {revision}"
    project_ref = f"scenario:{target}"
    topic_ref = f"prompt-project:scenario:{target}"
    state.update(
        {
            "selectedProjectKind": "scenario",
            "selectedProjectId": target,
            "selectedProjectRef": project_ref,
            "selectedProjectTitle": title,
            "selectedObjectKind": "scenario",
            "selectedObjectId": target,
            "builderTopicId": topic_ref,
            "builderThreadId": topic_ref,
        }
    )
    project = state.setdefault("project", {})
    project.update(
        {
            "title": title,
            "description": "Temporary functional Builder reference for A/B recovery.",
            "type": "scenario",
        }
    )
    page.setdefault("meta", {}).setdefault("builder", {}).update(
        {
            "ui_revision": revision,
            "reference_revision": revision,
            "reference_only": True,
        }
    )
    summary = webui["ui"].setdefault("user_summary", {})
    summary.setdefault("expected_behavior", []).append(
        f"This temporary scenario renders the functional Builder UI revision {revision}."
    )


def synchronize_target(
    owner: str,
    target: str,
    revision: str,
    webui: dict[str, Any],
) -> None:
    target_dir = _scenario_dir(owner, target)
    yaml_path = target_dir / "scenario.yaml"
    scenario_json_path = target_dir / "scenario.json"
    webui_path = target_dir / "webui.json"
    if not yaml_path.exists() or not scenario_json_path.exists():
        raise FileNotFoundError(
            f"DEV scenario {target!r} must exist before restoring the snapshot"
        )

    manifest = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    title = f"Builder reference — UI {revision}"
    description = "Temporary functional Builder reference for A/B recovery."
    manifest.update(
        {
            "id": target,
            "name": target,
            "title": title,
            "description": description,
        }
    )
    manifest.setdefault("supported_locales", ["en", "ru"])
    webui["ui"]["version"] = str(manifest.get("version") or "")
    yaml_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    scenario = _read_json(scenario_json_path)
    scenario.update(
        {
            "id": target,
            "name": target,
            "title": title,
            "description": description,
            "version": str(manifest.get("version") or scenario.get("version") or ""),
            "updated_at": str(
                manifest.get("updated_at") or scenario.get("updated_at") or ""
            ),
            "ui": copy.deepcopy(webui["ui"]),
        }
    )
    scenario["ui"]["manifest"] = "webui.json"
    _write_json(webui_path, webui)
    _write_json(scenario_json_path, scenario)


def restore(owner: str, source: str, target: str, revision: str) -> None:
    if source == target:
        raise ValueError("The recovery reference target must differ from the source")
    webui = load_webui_snapshot(owner, source, revision)
    rebind_reference(webui, target, revision)
    synchronize_target(owner, target, revision, webui)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--source", default="builder")
    parser.add_argument("--target", required=True)
    parser.add_argument("--revision", default="042")
    args = parser.parse_args()
    restore(args.owner, args.source, args.target, args.revision)


if __name__ == "__main__":
    main()
