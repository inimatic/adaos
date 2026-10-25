"""Audit installed or DEV consumer sources without importing their handlers.

Example: python tools/audit_application_compatibility.py --source <artifact-dir>
The report is static evidence, not an execution or release-acceptance receipt.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from adaos.services.applications.sdk_compatibility import audit_sdk_usage
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.scenario.validation import validate_scenario_path
from adaos.services.skill.validation import validate_webui_file_contract


def audit_source(root: Path) -> dict:
    root = Path(root)
    manifest_path = root / "skill.yaml"
    kind = "skill"
    if not manifest_path.is_file():
        kind, manifest_path = "scenario", root / "scenario.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8-sig")) or {}
    issues = validate_webui_file_contract(root, skill_name=manifest.get("name") if kind == "skill" else None)
    if kind == "scenario":
        issues.extend(validate_scenario_path(root, dependency_roots=(root.parent.parent / "skills",)).issues)
    sdk = audit_sdk_usage(root)
    ui = [{"level": i.level, "code": i.code, "message": i.message, "where": i.where} for i in issues]
    return {"source": str(root), "kind": kind,
            "id": manifest.get("id") or manifest.get("name"), "version": manifest.get("version"),
            "sdk": sdk, "ui": {"status": "incompatible" if any(i["level"] == "error" for i in ui)
                                else "static_checks_passed", "findings": ui},
            "scope": "static_sdk_and_current_core_ui_contracts", "runtime_smoke_required": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = [audit_source(root) for root in args.source]
    report = {"schema": "adaos.application.consumer_compatibility_audit.v1", "results": results}
    if args.output:
        atomic_write_json(args.output, report)
    for result in results:
        print(json.dumps({k: result[k] for k in ("source", "id", "version")} | {
            "sdk_status": result["sdk"]["status"], "sdk_findings": len(result["sdk"]["findings"]),
            "ui_status": result["ui"]["status"], "ui_errors": [i for i in result["ui"]["findings"] if i["level"] == "error"]}, ensure_ascii=True))
    return int(any(r["sdk"]["status"] == "incompatible" or r["ui"]["status"] == "incompatible" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
