"""Separate post-hoc compiler/probe evidence from the frozen paid experiment."""
import argparse
from collections import defaultdict
import hashlib
from pathlib import Path

from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import read

parser = argparse.ArgumentParser()
parser.add_argument("--run", type=Path, required=True)
args = parser.parse_args()
run = args.run.resolve()
if not run.is_relative_to(Path.cwd() / "e2e/artifacts/builder"):
    parser.error("Owned E2E artifacts only")
original = read(run / "report.json")
before = read(run / "compiler-replay-before-validated/report.json")
after = read(run / "compiler-replay-after/report.json")
key = lambda item: (item["cohort"], item["sample"])
prior = {key(item): item for item in before["samples"]}
current = {key(item): item for item in after["samples"]}
if len(prior) != 16 or set(prior) != set(current):
    raise ValueError("Incomplete retained compiler replay")
if any(prior[pair]["effective_candidate_sha256"] != item["effective_candidate_sha256"] for pair, item in current.items()):
    raise ValueError("Post-hoc comparison changed a model candidate")
if any(prior[pair]["generation_ok"] and not item["generation_ok"] for pair, item in current.items()):
    raise ValueError("Compiler change regressed a previously compilable retained candidate")
rows, rounds, groups = [], [], defaultdict(list)
for item in original["samples"]:
    pair = key(item)
    compiled = current[pair]
    cohort = run / item["cohort"]
    probes = dict(item["browser_probes"] or {})
    origins = {probe: "browser-requalification-mcp" for probe in probes}
    history = []
    for folder in sorted(cohort.glob("browser-posthoc-*")):
        summary_path = folder / "results.json"
        if not summary_path.exists():
            continue
        summary = read(summary_path)
        if not summary.get("complete"):
            raise ValueError("Post-hoc qualification still in progress")
        if not summary["preview_restoration"].get("protected_existing_source_unchanged"):
            raise ValueError("Protected Preview source changed")
        round_key = str(folder.relative_to(run))
        if not any(row["round"] == round_key for row in rounds):
            rounds.append({"round": round_key, "configuration": summary["configuration"],
                "source_fingerprints": read(folder / "source-fingerprints.json"),
                "preview_restoration": summary["preview_restoration"], "new_llm_calls": summary["new_llm_calls"]})
        checked = next((row for row in summary["samples"] if row["sample"] == item["sample"]), None)
        if not checked:
            continue
        for probe, status in (checked.get("probes") or {}).items():
            probes[probe], origins[probe] = status, folder.name
        history.append({"round": folder.name, "probes": checked.get("probes"), "ok": checked.get("ok"),
            "seconds": checked["qualification_seconds"]})
    expected = {"prototype-review", "prototype-capabilities", "prototype-procurement", "prototype-procurement-composition"}
    compiled_path = run / "compiler-replay-after" / item["cohort"] / item["sample"] / "generation-result.json"
    if compiled["generation_ok"] and any(command["kind"] == "update" for command in read(compiled_path)["semantic_document"]["commands"]):
        expected.add("prototype-interactions")
    complete_gates = compiled["generation_ok"] and expected.issubset(probes)
    passed = bool(complete_gates and compiled["structural_ok"] and all(probes[probe] == 0 for probe in expected))
    row = {"cohort": item["cohort"], "sample": item["sample"], "model": item["model"],
        "context": item["context"], "mcp": item["mcp"], "effective_candidate_sha256": compiled["effective_candidate_sha256"],
        "original_generation_ok": item["generation_ok"], "original_functional_e2e_ok": item["functional_e2e_ok"],
        "compiler_replay_ok": compiled["generation_ok"], "compiler_replay_structural_ok": compiled["structural_ok"],
        "posthoc_functional_e2e_ok": passed, "latest_gate_statuses": probes, "latest_gate_origins": origins,
        "qualification_history": history, "mcp_calls_in_original_generation": item["mcp_calls"],
        "failure": compiled.get("error") if not compiled["generation_ok"] else None}
    rows.append(row)
    groups[(item["model"], item["mcp"], item["context"])].append(row)
report = {"schema": "adaos.e2e.prototype_mcp_posthoc_comparison.v1",
    "complete": original["complete"] and len(rows) == 16,
    "new_llm_calls": 0, "original_evidence_overwritten": False, "unchanged_model_candidates": 16,
    "compiler_before": {"generation_ok": before["generation_ok"], "contract_compiler_inconsistencies": before["contract_compiler_inconsistencies"], "source_sha256": before["compiler_sha256"]},
    "compiler_after": {"generation_ok": after["generation_ok"], "contract_compiler_inconsistencies": after["contract_compiler_inconsistencies"], "source_sha256": after["compiler_sha256"]},
    "samples": rows,
    "groups": [{"model": model, "mcp": mcp, "context": context, "n": len(samples),
        "original_generation_ok": sum(row["original_generation_ok"] for row in samples),
        "compiler_replay_ok": sum(row["compiler_replay_ok"] for row in samples),
        "original_functional_e2e_ok": sum(row["original_functional_e2e_ok"] for row in samples),
        "posthoc_functional_e2e_ok": sum(row["posthoc_functional_e2e_ok"] for row in samples),
        "mcp_calls_in_original_generation": sum(row["mcp_calls_in_original_generation"] for row in samples)}
        for (model, mcp, context), samples in sorted(groups.items())],
    "rounds": rounds,
    "limitations": ["Post-hoc deterministic replay and corrected probes, not a new paid paired generation series",
        "Latest per-gate evidence may come from different explicitly recorded probe versions; failed gates were rechecked, not all gates rerun each round",
        "All intermediate failures are retained, including a wide-layout response timeout; later success does not erase that failure",
        "The compiler fix can change repair feedback in future runs; no claim is made about those unexecuted repairs",
        "One task, two repetitions per cell; functional checks are not visual or production approval"],
    "visual_review": "visual-review.json", "security_review": "security-review.json",
    "analysis_source_sha256": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
_write_json(run / "posthoc-report.json", report)
print({"complete": report["complete"], "compiler_before": before["generation_ok"], "compiler_after": after["generation_ok"],
    "functional_original": sum(row["original_functional_e2e_ok"] for row in rows),
    "functional_posthoc": sum(row["posthoc_functional_e2e_ok"] for row in rows), "groups": report["groups"]})
