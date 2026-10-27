"""Idempotent Builder-owned upgrade of the application address/read contracts.

This operates on a source checkpoint, never an installed immutable Trial.
Release preparation and activation remain ordinary governed lifecycle actions.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
import json
from pathlib import Path
import re

from adaos.services.artifact_pipeline.storage import atomic_write_bytes


def upgrade_webui(document: dict) -> dict:
    result = deepcopy(document)
    app = result["ui"]["application"]
    page = app["desktop"]["pageSchema"]
    page["initialStateSource"].update(required=True,
        identityKeys=["selectedProjectKind", "selectedProjectId"])
    bindings = page.setdefault("navigationState", {}).setdefault("bindings", [])
    for key, query in (("selectedProjectKind", "builder_object_type"),
                       ("selectedProjectId", "builder_object_id")):
        existing = next((binding for binding in bindings if binding.get("stateKey") == key), None)
        if existing and existing.get("queryKey") != query:
            raise ValueError(f"Conflicting Builder entity address: {key}")
        if not existing:
            bindings.append({"stateKey": key, "queryKey": query, "history": "push", "hydrate": True})
    picker = next(widget for widget in app["modals"]["project-picker"]["schema"]["widgets"]
        if widget["id"] == "project-picker-table")
    picker["dataSource"]["readPriority"] = "interactive"
    return result


def upgrade_catalog_source(source: str) -> str:
    old = '''    candidates = [item.get("created_at"), state.get("updated_at")]
    for ref in item.get("component_refs") or []:
        identity = _split_component_ref(str(ref))
        if identity:
            candidates.append(_catalog_state(*identity).get("updated_at"))'''
    new = '''    # The governed catalog already projects metadata. Resolving each member
    # here re-enters authoring/access admission per component and can outlive
    # the read timeout, accumulating duplicate workers on retries.
    candidates = [item.get("updated"), item.get("created_at"), state.get("updated_at")]'''
    if new in source:
        return source
    if source.count(old) != 1:
        raise ValueError("Unknown Builder catalog source; refusing a broad rewrite")
    return source.replace(old, new, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-workspace", type=Path, required=True)
    parser.add_argument("--scenario-version", required=True)
    parser.add_argument("--control-version", required=True)
    parser.add_argument("--application-version", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.source_workspace.resolve()
    if any(part in {"trials", ".runtime"} for part in root.parts):
        parser.error("Only source workspaces may be migrated, not immutable runtime editions")
    scenario = root / "scenarios/builder"
    control = root / "skills/builder_sdk_control_skill"
    ui = upgrade_webui(json.loads((scenario / "webui.json").read_text(encoding="utf-8-sig")))
    ui["ui"]["version"] = args.scenario_version
    manifest = json.loads((scenario / "scenario.json").read_text(encoding="utf-8-sig"))
    # Preserve an external UI reference instead of expanding a second copy.
    if "desktop" in (manifest.get("ui", {}).get("application") or {}):
        manifest["ui"] = deepcopy(ui["ui"])
    manifest["version"] = args.scenario_version
    handler = control / "handlers/main.py"
    source = upgrade_catalog_source(handler.read_text(encoding="utf-8-sig"))
    version_files = [(scenario / "scenario.yaml", args.scenario_version),
        (control / "skill.yaml", args.control_version),
        (root / "projects/adaos_builder/project.yaml", args.application_version)]
    versions = []
    for path, version in version_files:
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            parser.error("Explicit semantic versions required")
        value, count = re.subn(r"(?m)^version:[^\r\n]+", "version: " + version, path.read_text(encoding="utf-8-sig"), count=1)
        if count != 1:
            raise ValueError(f"Expected one version field: {path}")
        versions.append((path, value))
    if args.apply:
        for path, value in ((scenario / "webui.json", ui), (scenario / "scenario.json", manifest)):
            atomic_write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=4) + "\n").encode("utf-8"))
        atomic_write_bytes(handler, source.encode("utf-8"))
        for path, value in versions:
            atomic_write_bytes(path, value.encode("utf-8"))
    print(json.dumps({"applied": args.apply, "source_workspace": str(root), "application_version": args.application_version}))


if __name__ == "__main__":
    main()
