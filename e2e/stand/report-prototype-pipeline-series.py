"""Reconcile native usage, repair behavior and independent E2E gates; no LLM."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean

from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import read
from adaos.e2e.prototype_mcp_eval import mcp_trace

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run", type=Path, required=True)
parser.add_argument("--prior-run", type=Path, action='append', default=[])
parser.add_argument("--partial", action="store_true")
parser.add_argument('--qualification-label', default='')
args = parser.parse_args()
run = args.run.resolve()
if not run.is_relative_to(Path.cwd() / "e2e/artifacts/builder"):
    parser.error("Owned Builder artifacts required")
registration = read(run / "preregistration.json")
rows, jobs, groups = [], set(), defaultdict(list)
for cohort in registration["cohorts"]:
    directory = run / cohort["name"]
    results_path = directory / "results.json"
    if not results_path.exists():
        if args.partial:
            continue
        parser.error("Incomplete generation cohort")
    results = read(results_path)
    qualification_directory = 'browser-posthoc-' + args.qualification_label if args.qualification_label else 'browser-requalification-mcp'
    qualification_path = directory / qualification_directory / 'results.json'
    final_qualification_path = directory / ('final-qualification-' + args.qualification_label) / 'results.json'
    final_qualification = final_qualification_path.exists()
    if final_qualification:
        qualification_path = final_qualification_path
    qualification = read(qualification_path) if qualification_path.exists() else {"samples": [], "complete": False}
    if not args.partial and (not results["complete"] or not qualification["complete"]):
        parser.error("Generation and frozen browser qualification must be complete")
    browser = {item["sample"]: item for item in qualification["samples"]}
    repaired_directory = 'planned-repair-qualification-' + args.qualification_label if args.qualification_label else 'planned-repair-qualification'
    repaired_qualification_path = directory / repaired_directory / 'results.json'
    repaired_qualification = read(repaired_qualification_path) if repaired_qualification_path.exists() else {"samples": []}
    repaired_browser = {item["sample"]: item for item in repaired_qualification["samples"]}
    if final_qualification:
        repaired_browser = browser
    for result in results["samples"]:
        sample_id = f"{result['order']:02d}-{result['arm']}"
        sample = directory / sample_id
        usage = dict.fromkeys(("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
                               "output_excluding_reasoning_tokens", "total_tokens"), 0)
        retained, traces, missing_usage, execution_ms = [], [], [], 0
        for terminal_path in sorted(sample.glob("call-*-terminal.json")):
            envelope = read(terminal_path)
            request = read(terminal_path.with_name(terminal_path.name.replace('-terminal.json', '-request.json')))
            controls = request['options']
            assert controls['model'] == cohort['model']
            assert controls['reasoning'] == {'effort': registration['reasoning_effort']}
            assert envelope.get('telemetry', {}).get('provider_timeout_ms') == (controls.get('provider_timeout_s') or 300) * 1000
            assert controls['max_tool_calls'] == 40
            job_id = envelope["job_id"]
            if job_id in jobs:
                raise ValueError("Duplicate job would double-charge usage")
            jobs.add(job_id)
            provider_usage = (envelope.get("response") or {}).get("usage")
            trace = mcp_trace(envelope)
            traces.append(trace)
            execution_ms += envelope.get('telemetry', {}).get('timing', {}).get('execution_ms') or 0
            if not isinstance(provider_usage, dict):
                missing_usage.append(job_id)
                continue
            record = {key: provider_usage[key] for key in ("input_tokens", "output_tokens", "total_tokens")}
            record["cached_input_tokens"] = provider_usage.get("input_tokens_details", {}).get("cached_tokens", 0)
            record["reasoning_tokens"] = provider_usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
            record["output_excluding_reasoning_tokens"] = record["output_tokens"] - record["reasoning_tokens"]
            assert record["total_tokens"] == record["input_tokens"] + record["output_tokens"]
            assert 0 <= record["cached_input_tokens"] <= record["input_tokens"]
            assert 0 <= record["reasoning_tokens"] <= record["output_tokens"]
            for key in usage:
                usage[key] += record[key]
            retained.append({"job_id": job_id, "status": envelope["status"], "usage": record,
                             "provider_timeout_ms": envelope.get('telemetry', {}).get('provider_timeout_ms'),
                             "actual_provider_model": (envelope.get("response") or {}).get("model")})
        delivery = [read(path) for path in sorted(sample.glob("delivery-*.json"))]
        repair_path = sample / 'planned-repair-result.json'
        repaired = read(repair_path) if repair_path.exists() else None
        if repaired is not None and not args.partial and sample_id not in repaired_browser:
            parser.error('Planned repair requires separate frozen browser qualification')
        browser_result = repaired_browser.get(sample_id, {}) if repaired else browser.get(sample_id, {})
        browser_pass = browser_result.get("ok") is True
        final_document = (repaired or read(sample / 'generation-result.json')).get('semantic_document') or {}
        structural_pass = browser_result.get('structural', {}).get('ok') is True if repaired or final_qualification else result['structural']['ok']
        row = {"cohort": cohort["name"], "sample": sample_id, "task": cohort["task"], "model": cohort["model"],
            "arm": result["arm"], "first_pass_compile": result["first_pass"], "primary_compile": result['generation_ok'],
            "final_compile": repaired.get('ok') is True if repaired else result["generation_ok"],
            "planned_repair_attempted": repaired is not None,
            "structural_pass": structural_pass, "browser_pass": browser_pass,
            "functional_e2e_pass": structural_pass and browser_pass,
            "final_candidate_shape": {key: len(final_document.get(key) or [])
                                      for key in ('resources', 'views', 'commands', 'representative_states')},
            "usage": usage, "usage_complete": not missing_usage, "missing_usage_jobs": missing_usage,
            "provider_jobs": retained, "attempted_requests": len(list(sample.glob("call-*-request.json"))),
            "generation_seconds": execution_ms / 1000, "primary_helper_wall_seconds": result["generation_seconds"],
            "timing_scope": "sum of provider-job execution, including planned repairs; not Client qualification wall time",
            "failure": result.get("failure"),
            "mcp_calls": sum(trace["call_count"] for trace in traces), "mcp_errors": sum(trace["errors"] for trace in traces),
            "batch_reads": sum(bool(call["arguments"].get("fragment_ids")) for trace in traces for call in trace["calls"] if isinstance(call["arguments"], dict)),
            "mcp_fragment_ids": sorted({identity for trace in traces for call in trace["calls"]
                for identity in ([call["fragment_id"]] if call.get("fragment_id") else call.get("fragment_ids") or [])}),
            "focused_repairs": sum(len(item.get("focused_repairs") or []) for item in delivery),
            "delivery_fallbacks": [item for receipt in delivery for item in receipt.get("fallbacks") or []],
            "mcp_traces": traces}
        rows.append(row)
        groups[cohort["model"], cohort["task"], result["arm"]].append(row)
        if row['attempted_requests'] > registration['per_candidate_request_cap']:
            raise ValueError('Candidate exceeded its request cap')
summary = []
for (model, task, arm), items in sorted(groups.items()):
    success = sum(item["functional_e2e_pass"] for item in items)
    total_usage = {key: sum(item["usage"][key] for item in items) for key in items[0]["usage"]}
    record = {"model": model, "task": task, "arm": arm, "candidates": len(items),
        "first_pass_compile": sum(item["first_pass_compile"] for item in items), "final_compile": sum(item["final_compile"] for item in items),
        "functional_e2e_pass": success, "mean_usage_all_attempts": {key: value / len(items) for key, value in total_usage.items()},
        "mean_fresh_input_tokens": (total_usage["input_tokens"] - total_usage["cached_input_tokens"]) / len(items),
        "total_usage": total_usage, "usage_complete": all(item["usage_complete"] for item in items),
        "tokens_per_observed_success_including_failures": total_usage["total_tokens"] / success if success else None,
        "mean_generation_seconds": mean(item["generation_seconds"] for item in items),
        "attempted_requests": sum(item["attempted_requests"] for item in items),
        "mcp_calls": sum(item["mcp_calls"] for item in items), "batch_reads": sum(item["batch_reads"] for item in items),
        "mcp_errors": sum(item["mcp_errors"] for item in items),
        "focused_repairs": sum(item["focused_repairs"] for item in items)}
    summary.append(record)
comparisons = []
for optimized in summary:
    if optimized["arm"] != "adaptive":
        continue
    full = next(item for item in summary if item["model"] == optimized["model"] and item["task"] == optimized["task"] and item["arm"] == "full")
    changes = {key: 100 * (optimized["mean_usage_all_attempts"][key] / value - 1) if value else None
               for key, value in full["mean_usage_all_attempts"].items()}
    comparisons.append({"model": full["model"], "task": full["task"], "change_percent": changes,
        "full_success": full["functional_e2e_pass"], "adaptive_success": optimized["functional_e2e_pass"]})
attempts = len(list(run.rglob("call-*-request.json")))
generation_attempts = attempts
recovery_smokes = []
for request_path in sorted((run / 'mcp-error-recovery').glob('*/request.json')):
    attempts += 1
    directory = request_path.parent
    if not (directory / 'result.json').exists():
        if args.partial:
            continue
        parser.error('Instructed recovery smoke must have terminal evidence')
    smoke = read(directory / 'result.json')
    requested_model = read(request_path)['options']['model']
    recovery_smokes.append({'requested_model': requested_model, 'provider_model': smoke['model'],
        'job_id': smoke['provider_job_id'], 'ok': smoke['ok'], 'native_mcp_calls': smoke['call_count'],
        'expected_tool_errors': smoke.get('expected_tool_errors', 0),
        'unexpected_errors': smoke.get('unexpected_errors', smoke['errors']), 'usage': smoke['usage'],
        'evidence': str((directory / 'result.json').relative_to(Path.cwd())),
        'instructed_not_voluntary': True})
assert len({smoke['job_id'] for smoke in recovery_smokes}) == len(recovery_smokes)
prior_runs = {path.resolve() for path in args.prior_run}
if run in prior_runs or any(not path.is_relative_to(Path.cwd() / 'e2e/artifacts/builder') for path in prior_runs):
    parser.error('Distinct owned prior runs required')
prior_attempts = sum(len(list(path.rglob('call-*-request.json'))) for path in prior_runs)
if prior_attempts != registration.get('prior_attempted_requests', 0):
    parser.error('Provide every prior run from the registered shared request budget')
if prior_attempts + attempts > registration["max_paid_requests"]:
    raise ValueError("Combined request budget exceeded")
report = {"schema": "adaos.e2e.prototype_pipeline_report.v1", "complete": not args.partial,
    "qualification_label": args.qualification_label, "original_model_outputs_preserved": True,
    "attempted_requests": attempts, "prior_attempted_requests": prior_attempts, "combined_attempted_requests": attempts + prior_attempts,
    'generation_attempted_requests': generation_attempts, 'instructed_mcp_error_recovery': recovery_smokes,
    'instructed_smoke_total_tokens': sum(smoke['usage']['total_tokens'] for smoke in recovery_smokes),
    "unique_retained_terminal_jobs": len(jobs), "samples": rows, "summary": summary, "comparisons": comparisons,
    "cost": {"status": "unpriced", "reason": "Root selected-model tariff is not configured; tokens are not USD. Cached input, reasoning and output-excluding-reasoning are breakdowns, never additional charges."},
    "causality": "Native full vs hybrid-focused bundle. Batch-capable MCP catalog is shared; batching-alone effect not isolated. Small n, descriptive results, no significance claim."}
report['delivery_deviations'] = [{'cohort': row['cohort'], 'sample': row['sample'], 'job_id': job['job_id'],
    'expected_provider_timeout_ms': 900000, 'actual_provider_timeout_ms': job['provider_timeout_ms']}
    for row in rows for job in row['provider_jobs'] if job['provider_timeout_ms'] != 900000]
report['input_deviations'] = [{'cohort': row['cohort'], 'sample': row['sample'],
    'detail': 'Support-desk instruction was paired with an inventory session title; constant within both arms, but unwanted input noise'}
    for row in rows if row['task'] == 'support-desk-ru'
    and read(run / row['cohort'] / row['sample'] / 'session.json').get('title') != 'Служба поддержки [TEST]']
_write_json(run / ("report-partial.json" if args.partial else "report.json"), report)
print(json.dumps({key: report[key] for key in ("complete", "combined_attempted_requests", "summary", "comparisons")}, ensure_ascii=False, indent=2))
