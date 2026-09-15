"""Audit retained Prototype input without applying or regenerating a candidate."""

import argparse
import hashlib
import json
from pathlib import Path

from adaos.services.builder_intent import process_constraint_kind


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((Path.cwd() / "e2e/artifacts/builder").resolve()):
        parser.error("Output must remain in Builder E2E artifacts")
    terminal = json.loads(args.job.read_text(encoding="utf-8"))
    reference = terminal["input_artifact"]
    raw = (args.job.parent / Path(reference["path"]).name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("Retained input digest changed")
    request = json.loads(raw)
    if request["scenario_id"] != terminal["scenario_id"]:
        raise ValueError("Retained input belongs to another scenario")
    dynamic = json.loads(request["messages"][-1]["content"])["builder_request"]
    brief = dynamic["prototype_brief"]
    requirements = brief["required_references"]
    options = request["generation"]["options"]
    diagnostic = terminal.get("diagnostic") or {}
    telemetry = diagnostic.get("telemetry") or {}
    findings = diagnostic.get("result", {}).get("validation", {}).get("findings", [])
    missing = {ref for row in findings if row.get("code") == "requirement.coverage_missing"
               for ref in row.get("requirement_refs", [])}
    misclassified = [{**row, "verification_owner": owner} for row in requirements
                     if (owner := process_constraint_kind(row["statement"]))]
    report = {
        "schema": "adaos.e2e.prototype_input_audit.v1",
        "scope": "retained context audit, not semantic or application acceptance",
        "job_id": terminal["job_id"], "scenario_id": terminal["scenario_id"],
        "input_sha256": reference["sha256"], "model": request["generation"]["model"],
        "reasoning": options.get("reasoning"), "output_mode": options.get("output_mode"),
        "message_bytes": [len(row["content"].encode("utf-8")) for row in request["messages"]],
        "dynamic_section_bytes": {key: len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
                                  for key, value in dynamic.items()},
        "requirement_count": len(requirements),
        "process_constraint_count": len(brief.get("process_constraints", [])),
        "process_constraints_in_ui_inventory": misclassified,
        "missing_ui_refs": sorted(missing),
        "missing_refs_now_owned_by_process_review": sorted(missing & {row["id"] for row in misclassified}),
        "usage": telemetry.get("usage"), "usage_breakdown": telemetry.get("usage_breakdown"),
        "timing": telemetry.get("timing"), "repair_timing": telemetry.get("repair", {}).get("timing"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({key: report[key] for key in ("job_id", "requirement_count", "process_constraint_count",
                     "missing_refs_now_owned_by_process_review")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
