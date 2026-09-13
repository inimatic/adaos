"""Evaluate Automation against an existing owned Prototype, without generation or delivery."""

import argparse
import copy
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _expectation_findings, _load_document, _resolve_value, _write_json
from adaos.e2e.builder_lifecycle import _target, execute
from adaos.e2e.stand import redact_value


ALLOWED = {"prototype.accept", "automation.start", "automation.submit", "automation.wait", "builder.workflow"}


def validate_plan(plan):
    steps = plan.get("steps") or []
    if plan.get("schema") != "adaos.e2e.frozen_automation.v1" or not steps:
        raise ValueError("A nonempty frozen Automation plan is required")
    if any(step.get("type") not in ALLOWED for step in steps):
        raise ValueError("Frozen Automation cannot generate Prototypes, prepare Trial or publish")
    ids = [step["id"] for step in steps]
    if len(ids) != len(set(ids)):
        raise ValueError("Step IDs must be unique")
    types = [step["type"] for step in steps]
    if types.count("automation.start") + types.count("automation.submit") != 1:
        raise ValueError("Exactly one Automation start or correction is required")
    if "automation.submit" in types and "prototype.accept" in types:
        raise ValueError("A correction must not reapprove its functional UI as Prototype")
    if "prototype.accept" in types and types.index("prototype.accept") > types.index("automation.start"):
        raise ValueError("Prototype acceptance must precede Automation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--host", required=True)
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev" or not args.host.startswith("e2e-"):
        parser.error("Requires a DEV node and an isolated E2E Builder host")
    plan = _load_document(args.plan)
    validate_plan(plan)
    original = _load_document(args.checkpoint)
    context = copy.deepcopy(original["context"])
    targets = context.get("owned_artifacts") or []
    if len(targets) != 1 or not original.get("cleanup", {}).get("test"):
        parser.error("Requires an existing checkpoint owning exactly one test application")
    kind, identifier = targets[0]["primary_ref"].split(":", 1)
    _target({"object_type": kind, "object_id": identifier}, context)
    output = args.output.resolve()
    root = (Path.cwd() / "e2e/artifacts/builder").resolve()
    if not output.is_relative_to(root) or output == root:
        parser.error("Evidence must remain inside e2e/artifacts/builder")
    output.mkdir(parents=True, exist_ok=False)
    context.update(bundle_dir=str(output), run_id=output.name, case_id=plan["case_id"],
                   repetition=1, webspace_id=args.host, outputs={}, scenario_id=identifier,
                   object_type=kind, step_id="")
    init_ctx(Settings.from_sources())
    _write_json(output / "parent-checkpoint.json", original)
    report = {"scope": "frozen Prototype Automation diagnostic; original generation verdict unchanged",
              "parent_sha256": hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
              "plan": plan, "context": context, "steps": [], "ok": False,
              "core_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    for declaration in plan["steps"]:
        step_id = declaration["id"]
        inputs = _resolve_value(declaration.get("input") or {}, context)
        inputs.setdefault("object_id", identifier)
        # Reviews are copied as evidence; their bytes are not rewritten on resume.
        if declaration["type"] == "prototype.accept":
            review = args.plan.parent / str(inputs["review_file"])
            value = _load_document(review)
            refs = {str(ref) for check in value.get("behavior_checks", []) for ref in check.get("evidence_refs", [])}
            refs.update(str(check["evidence_ref"]) for check in value.get("visual_checks", []))
            refs.add(str(inputs["review_file"]))
            for ref in refs:
                source, destination = (args.plan.parent / ref).resolve(), (output / ref).resolve()
                if not source.is_relative_to(args.plan.parent.resolve()) or not destination.is_relative_to(output):
                    raise ValueError("Review references must stay inside their evidence bundle")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        started = time.perf_counter()
        report["active_step"] = step_id
        _write_json(output / "report.json", redact_value(report))
        print(f"{step_id}: started", flush=True)
        try:
            result = execute(declaration["type"], inputs, {**context, "step_id": step_id})
            findings = _expectation_findings(result, declaration.get("expect") or {})
            passed = result.get("ok") is not False and not findings
        except Exception as exc:
            result = {"ok": False, "error": type(exc).__name__, "detail": str(exc), "traceback": traceback.format_exc()}
            findings, passed = [], False
        receipt = {"id": step_id, "type": declaration["type"], "input": inputs, "output": result,
                   "status": "passed" if passed else "failed", "findings": findings,
                   "duration_ms": round((time.perf_counter() - started) * 1000, 3)}
        _write_json(output / f"{step_id}.json", redact_value(receipt))
        report["steps"].append({key: receipt[key] for key in ("id", "type", "status", "duration_ms")})
        report["active_step"] = None
        _write_json(output / "report.json", redact_value(report))
        print(f"{step_id}: {receipt['status']}", flush=True)
        if not passed:
            raise SystemExit(1)
        context["outputs"][step_id] = result
    report["ok"] = True
    _write_json(output / "report.json", redact_value(report))


if __name__ == "__main__":
    main()
