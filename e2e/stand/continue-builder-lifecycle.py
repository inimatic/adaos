"""Continue a failed lifecycle tail without regenerating or rewriting its run.

This is diagnostic continuation evidence, never a fresh-cohort pass. The original
case, owned targets and review gates remain authoritative.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import time
import traceback

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import (_digest, _expectation_findings, _load_document,
                              _resolve_value, _write_json)
from adaos.e2e.builder_lifecycle import STEP_TYPES, execute
from adaos.e2e.stand import redact_value


def lifecycle_tail(checkpoint, case):
    if checkpoint.get("active_step") or checkpoint.get("case_digest") != _digest(case):
        raise ValueError("Continuation requires the unchanged case and a terminal checkpoint")
    prior = checkpoint.get("steps") or []
    declarations = case.get("steps") or []
    if not prior or prior[-1].get("status") != "failed":
        raise ValueError("Continuation requires a failed terminal step")
    for index, step in enumerate(prior):
        if index >= len(declarations) or (step["id"], step["type"]) != (declarations[index]["id"], declarations[index]["type"]):
            raise ValueError("Checkpoint steps do not match the case")
        if index < len(prior) - 1 and step.get("status") != "passed":
            raise ValueError("Continuation cannot skip an earlier failed step")
    tail = declarations[len(prior) - 1:]
    if any(step["type"] not in STEP_TYPES for step in tail):
        raise ValueError("Only the declared lifecycle tail can be continued")
    return copy.deepcopy(tail)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("case", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--retry-failed-automation", action="store_true",
                        help="Retry the unchanged, owned Automation session after a terminal executor failure")
    args = parser.parse_args()
    raw = args.checkpoint.read_bytes()
    checkpoint = json.loads(raw)
    context = copy.deepcopy(checkpoint["context"])
    root = Path(context["bundle_dir"]).resolve()
    output = args.output.resolve()
    if not args.checkpoint.resolve().is_relative_to(root) or not output.is_relative_to(root / "continuations"):
        parser.error("Checkpoint and new continuation evidence must remain inside the original bundle")
    tail = lifecycle_tail(checkpoint, _load_document(args.case))
    load_dotenv()
    init_ctx(Settings.from_sources())
    output.mkdir(parents=True, exist_ok=False)
    report = {"scope": "diagnostic lifecycle continuation; original run verdict unchanged",
              "parent_checkpoint": str(args.checkpoint.resolve()), "parent_sha256": hashlib.sha256(raw).hexdigest(),
              "core_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "core_status": subprocess.check_output(["git", "status", "--short"], text=True).strip(),
              "retry_failed_automation": args.retry_failed_automation, "steps": [], "ok": False}
    for declaration in tail:
        step_id = declaration["id"]
        inputs = _resolve_value(declaration.get("input") or {}, context)
        report["active_step"] = step_id
        _write_json(output / "report.json", report)
        started = time.perf_counter()
        print(f"{step_id}: started", flush=True)
        try:
            result = execute(declaration["type"], inputs, {**context, "step_id": step_id})
            if (args.retry_failed_automation and declaration["type"] == "automation.start"
                    and result.get("duplicate") and (result.get("session") or {}).get("status") == "failed"):
                from adaos.sdk.builder import automation

                previous = result["session"]
                _write_json(output / f"{step_id}-previous.json", redact_value(result))
                result = automation.retry_failed(
                    object_type=str(inputs.get("object_type") or "scenario"), object_id=inputs["object_id"],
                    webspace_id=context["webspace_id"], conversation_id=previous["conversation_id"],
                    execution_budget=inputs.get("execution_budget"))
                result["retried_session_id"] = previous.get("session_id")
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
        _write_json(output / "report.json", report)
        print(f"{step_id}: {receipt['status']} ({receipt['duration_ms']}ms)", flush=True)
        if not passed:
            raise SystemExit(1)
        context["outputs"][step_id] = result
    report["ok"] = True
    _write_json(output / "report.json", report)


if __name__ == "__main__":
    main()
