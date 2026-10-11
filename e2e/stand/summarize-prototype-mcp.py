"""Aggregate frozen native Prototype/MCP cohorts without generating new outputs."""
import argparse
from collections import Counter, defaultdict
import copy
import json
import hashlib
from pathlib import Path
from statistics import mean

from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import read
from adaos.e2e.prototype_mcp_eval import mcp_trace

parser = argparse.ArgumentParser()
parser.add_argument("--run", type=Path, required=True)
args = parser.parse_args()
run = args.run.resolve()
if not run.is_relative_to(Path.cwd() / "e2e/artifacts/builder"):
    parser.error("Owned E2E artifacts only")
cohorts = sorted(folder for folder in run.glob("*high-mcp-*") if (folder / "plan.json").exists())
rows, checks, groups, job_traces = [], {}, defaultdict(list), []
for cohort in cohorts:
    plan = read(cohort / "plan.json")
    if not (cohort / "results.json").exists():
        continue
    results = read(cohort / "results.json")
    qualification_path = cohort / "browser-requalification-mcp/results.json"
    qualified = read(qualification_path) if qualification_path.exists() else {}
    qualification = {item["sample"]: item for item in qualified.get("samples", [])}
    checks[cohort.name] = {"generation_complete": results["complete"],
        "qualification_complete": qualified.get("complete", False),
        "protected_source_unchanged": results["protected_source_unchanged"],
        "preview_restored": qualified.get("preview_restoration", {}).get("protected_existing_source_unchanged")}
    for item in results["samples"]:
        sample_name = f"{item['order']:02d}-{item['arm']}"
        sample = cohort / sample_name
        terminals = [read(file) for file in sorted(sample.glob("call-*-terminal.json"))]
        traces = [mcp_trace(envelope) for envelope in terminals]
        job_traces.extend(traces)
        providers = Counter(envelope.get("response", {}).get("model") for envelope in terminals)
        efforts = Counter(envelope.get("response", {}).get("reasoning", {}).get("effort") for envelope in terminals)
        if len(terminals) != item["calls"]:
            raise ValueError("Missing retained provider terminal evidence")
        calls = [call for trace in traces for call in trace["calls"]]
        usage = {key: sum((trace.get("usage") or {}).get(key, 0) for trace in traces)
            for key in ("input_tokens", "output_tokens", "total_tokens")}
        usage["cached_input_tokens"] = sum((trace.get("usage") or {}).get("input_tokens_details", {}).get("cached_tokens", 0) for trace in traces)
        usage["reasoning_tokens"] = sum((trace.get("usage") or {}).get("output_tokens_details", {}).get("reasoning_tokens", 0) for trace in traces)
        gates = qualification.get(sample_name, {})
        row = {"cohort": cohort.name, "sample": sample_name, "model": plan["model"],
            "provider_models": dict(providers), "provider_efforts": dict(efforts), "mcp": plan["mcp"],
            "context": "full" if item["arm"] == "full" else "adaptive-indexed", "first_pass": item["first_pass"],
            "generation_ok": item["generation_ok"], "structural_ok": item["structural"]["ok"],
            "browser_ok": gates.get("ok", False), "browser_probes": gates.get("probes"),
            "functional_e2e_ok": item["generation_ok"] and item["structural"]["ok"] and gates.get("ok", False),
            "provider_jobs": len(terminals), "generation_seconds": item["generation_seconds"], "usage": usage,
            "mcp_calls": len(calls), "primary_mcp_calls": traces[0]["call_count"] if traces else 0,
            "repair_mcp_calls": sum(trace["call_count"] for trace in traces[1:]),
            "mcp_errors": sum(trace["errors"] for trace in traces), "mcp_discoveries": sum(trace["discovery_count"] for trace in traces),
            "mcp_digest_mismatches": sum(call["library_digest"] != plan["library_digest"]
                or not isinstance(call["arguments"], dict) or call["arguments"].get("expected_digest") != plan["library_digest"] for call in calls),
            "unique_fragments_read": sorted({call["fragment_id"] for call in calls if call["fragment_id"]}),
            "tool_names": dict(Counter(call["name"] for call in calls)),
            "repeated_exact_calls": sum(trace["repeated_exact_calls"] for trace in traces),
            "serialized_tool_output_bytes": sum(call["output_bytes"] for call in calls),
            "mcp_trace_files": [str(sample / "native-mcp-traces.json")], "failure": item["failure"]}
        if plan["mcp"] == "off" and row["mcp_calls"]:
            raise ValueError("Off control contains native MCP calls")
        rows.append(row)
        groups[(plan["model"], plan["mcp"], row["context"])].append(row)
summaries = []
for (model, mcp, context), samples in sorted(groups.items()):
    summaries.append({"model": model, "mcp": mcp, "context": context, "n": len(samples),
        **{key: sum(row[key] for row in samples) for key in ("first_pass", "generation_ok", "structural_ok", "browser_ok", "functional_e2e_ok", "provider_jobs", "mcp_calls", "primary_mcp_calls", "repair_mcp_calls", "mcp_errors", "mcp_discoveries", "mcp_digest_mismatches", "repeated_exact_calls")},
        "mean_generation_seconds": round(mean(row["generation_seconds"] for row in samples), 3),
        "mean_tokens": {key: round(mean(row["usage"][key] for row in samples), 1) for key in samples[0]["usage"]},
        "unique_fragments_read": sorted({fragment for row in samples for fragment in row["unique_fragments_read"]})})

# The two models must have identical messages and all non-model generation controls.
parity = []
for mcp in ("on", "off"):
    pair = [folder for folder in cohorts if read(folder / "plan.json")["mcp"] == mcp]
    if len(pair) != 2:
        continue
    for arm in ("full", "adaptive"):
        inputs = [copy.deepcopy(read(folder / f"planned-{arm}-input.json")) for folder in pair]
        for value in inputs:
            value["options"].pop("model", None)
            value["options"].pop("reasoning", None)
        parity.append({"mcp": mcp, "arm": arm, "exact_non_model_input_parity": inputs[0] == inputs[1]})
if parity and not all(item["exact_non_model_input_parity"] for item in parity):
    raise ValueError("Model comparison input parity failed")
all_plans = [read(folder / "plan.json") for folder in cohorts]
fixed = ("instruction_sha256", "brief_sha256", "provider_schema_sha256", "initial_webui_sha256",
    "native_builder_sha256", "acceptance_source_sha256", "runtime_source_fingerprints", "acceptance", "provider_timeout_ms", "library_digest")
source_parity = {key: all(plan[key] == all_plans[0][key] for plan in all_plans) for key in fixed} if all_plans else {}
if not all(source_parity.values()):
    raise ValueError("Cross-cohort implementation/criteria drift")
report = {"schema": "adaos.e2e.prototype_mcp_comparison.v1", "complete": len(rows) == 16 and all(
    value["generation_complete"] and value["qualification_complete"] for value in checks.values()),
    "task": "inventory-procurement-ru", "expected_candidates": 16, "samples": rows, "groups": summaries,
    "cohort_checks": checks, "model_input_parity": parity, "source_and_task_parity": source_parity,
    "design": {"models": ["gpt-5 high", "gpt-6-astra high"], "contexts": ["full", "adaptive-indexed"],
        "retrieval": ["native read-only MCP on", "off"], "repetitions_per_cell": 2, "within_cohort_order": "ABBA",
        "max_tool_calls_per_job": 40, "provider_timeout_ms": 900000, "voluntary_tools": True,
        "no_retrieval_indexed_control": "intentionally missing detailed platform guidance, not a shipping default"},
    "limitations": ["One rich task and two repetitions per cell; descriptive evidence, not statistical proof or a general model ranking",
        "Reasoning=high is identical configuration, not a guarantee of equivalent compute across models",
        "MCP call count measures retrieval behavior, not a model's mental state",
        "MCP-on includes discovery/schema overhead; provider token totals include tool-result context",
        "Provider job wall time and server timestamps are available; per-call tool latency is not exposed in terminal outputs",
        "Functional browser gates and independent screenshot review are distinct"],
    "smokes_excluded_from_cohort_metrics": ["smoke-gpt5-02", "smoke-astra"],
    "implementation": {"default_context": "full", "prototype_mcp_switch": "ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED=1",
        "permission_scope": ["builder.prototype.read_guidance"], "project_or_app_mutation_tools": []}}
report["analysis_source_fingerprints"] = {str(path): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (Path(__file__), Path("src/adaos/e2e/prototype_mcp_eval.py"))}
report["interpretation"] = {
    "main_browser_measurement": "Frozen original probes; follow-up probe/compiler corrections are separate, not substituted",
    "causal_claim": "Retrieval availability and index are controlled interventions; small n and exposed compiler/probe defects limit attribution",
    "missing_channel_hypothesis": "Supported for Astra adaptive on this task, not a sufficient explanation for all GPT-5 or full-context failures",
    "confusion_proxy": "Rejected as a reliable metric: failed GPT-5 repairs made no calls while successful Astra primary attempts actively retrieved",
}
_write_json(run / "native-mcp-audit.json", {"schema": "adaos.e2e.prototype_mcp_provider_audit.v1",
    "jobs": job_traces, "calls": sum(trace["call_count"] for trace in job_traces),
    "tool_errors": sum(trace["errors"] for trace in job_traces), "source_fingerprints": report["analysis_source_fingerprints"]})
_write_json(run / "report.json", report)
print(json.dumps({"complete": report["complete"], "candidates": len(rows), "groups": summaries}, ensure_ascii=False), flush=True)
