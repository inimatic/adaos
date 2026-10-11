"""Preregistered 16-candidate, 32-request maximum native Prototype comparison.

Generation cohorts run in separate processes. Browser qualification is serial
because it shares one existing pinned Preview. No model judges or paid smokes.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import source_fingerprints

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--fixture-run", type=Path, required=True)
parser.add_argument("--host", required=True)
parser.add_argument("--plan-only", action="store_true")
parser.add_argument("--prior-run", type=Path, help="Count every attempted request from an interrupted series against the same 32-request budget")
args = parser.parse_args()
repo = Path.cwd().resolve()
output = args.output.resolve()
if not output.is_relative_to(repo / "e2e/artifacts/builder") or output.exists():
    parser.error("New owned E2E output required")
output.mkdir()
cohorts = [("gpt5-inventory", "gpt-5", None), ("astra-support", "gpt-6-astra", "support-desk-ru"),
           ("astra-inventory", "gpt-6-astra", None), ("gpt5-support", "gpt-5", "support-desk-ru")]
sources = source_fingerprints(repo)
prior_requests = len(list(args.prior_run.rglob("call-*-request.json"))) if args.prior_run else 0
support_repetitions = 1 if prior_requests else 2
planned_candidates = 8 + 4 * support_repetitions
if prior_requests + planned_candidates * 2 > 32:
    parser.error("Preregistered candidates would exceed the combined 32-request budget")
_write_json(output / "preregistration.json", {"schema": "adaos.e2e.prototype_pipeline_series.v1",
    "max_paid_requests": 32, "prior_attempted_requests": prior_requests, "remaining_request_budget": 32 - prior_requests,
    "candidates": planned_candidates, "per_candidate_request_cap": 2,
    "models": ["gpt-5", "gpt-6-astra"], "reasoning_effort": "high",
    "repetitions_per_cell": {"inventory-procurement-ru": 2, "support-desk-ru": support_repetitions},
    "arms": {"full": "byte-preserving current full context", "adaptive": "SDK hybrid-focused: exact kernel, optional retrieval, closed-scope repair projection"},
    "cohorts": [{"name": name, "model": model, "task": task or "inventory-procurement-ru"} for name, model, task in cohorts],
    "controls": ["same model/effort/output budget, schema, facts, compiler, Client and probes within each pair",
        "same batch-capable MCP tools for both arms; batching alone is NOT causally isolated",
        "full-adaptive-adaptive-full order; task/model order crossed; generation-only processes, serial browser",
        "all failed requests included in usage, no post-hoc repairs or model judges",
        "first-pass compile, final compile, fixed task acceptance, desktop/mobile functional probes, screenshot review",
        "provider usage only; cached input and reasoning are subsets, not additional tokens",
        "small n, descriptive paired outcomes, no claim of statistical significance"],
    "runtime_source_fingerprints": sources,
    "task_sha256": hashlib.sha256((repo / "e2e/builder/development/context-ablation/support-desk-ru.json").read_bytes()).hexdigest()})


def generate(cohort):
    name, model, task = cohort
    if source_fingerprints(repo) != sources:
        raise RuntimeError("Frozen runtime source drift")
    command = [sys.executable, "e2e/stand/run-prototype-context-ablation.py", "--fixture-run", str(args.fixture_run),
        "--output", str(output / name), "--host", args.host, "--model", model, "--reasoning-effort", "high",
        "--mcp", "on", "--context-mode", "pipeline", "--repetitions", str(support_repetitions if task else 2), "--defer-browser"]
    if task:
        command.extend(["--task-file", "e2e/builder/development/context-ablation/support-desk-ru.json"])
    if args.plan_only:
        command.append("--plan-only")
    with (output / f"{name}-generation.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    print(json.dumps({"cohort": name, "generation_exit": result.returncode}), flush=True)
    return result.returncode


with ThreadPoolExecutor(max_workers=2) as executor:
    generation = list(executor.map(generate, cohorts[:2]))
    if not any(generation):
        generation.extend(executor.map(generate, cohorts[2:]))
if any(generation) or args.plan_only:
    raise SystemExit(1 if any(generation) else 0)
for name, model, task in cohorts:
    # Match the real outer Builder dispatch: the primary helper alone does not
    # invoke semantic repair. Keep original outputs and append one planned repair.
    with (output / f"{name}-repair.log").open("w", encoding="utf-8") as log:
        repaired = subprocess.run([sys.executable, "e2e/stand/repair-prototype-pipeline-cohort.py", "--run", str(output / name)],
                                  stdout=log, stderr=subprocess.STDOUT)
    if repaired.returncode:
        raise SystemExit(repaired.returncode)
    command = [sys.executable, "e2e/stand/qualify-prototype-context-ablation.py", "--run", str(output / name), "--host", args.host]
    with (output / f"{name}-browser.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    print(json.dumps({"cohort": name, "qualification_exit": result.returncode}), flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
    with (output / f"{name}-repair-browser.log").open("w", encoding="utf-8") as log:
        checked = subprocess.run([sys.executable, "e2e/stand/qualify-planned-prototype-repairs.py", "--run", str(output / name), "--host", args.host],
                                 stdout=log, stderr=subprocess.STDOUT)
    if checked.returncode:
        raise SystemExit(checked.returncode)
