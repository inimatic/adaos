"""Resume browser qualification of frozen, owned candidates; no model calls."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time
import re
from unittest.mock import patch
from dotenv import load_dotenv

from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import browser_review, native_builder, preserved_preview, read, source_fingerprints

parser = argparse.ArgumentParser()
parser.add_argument("--run", type=Path, required=True)
parser.add_argument("--host", required=True)
parser.add_argument("--partial", action="store_true")
parser.add_argument("--resume", action="store_true")
parser.add_argument("--posthoc-label", default="")
parser.add_argument("--compiler-replay-label", default="")
parser.add_argument("--samples", nargs="+", default=[])
parser.add_argument("--probes", nargs="+", default=[], choices=("prototype-review", "prototype-capabilities",
    "prototype-procurement", "prototype-procurement-composition", "prototype-interactions"))
args = parser.parse_args()
if args.posthoc_label and not re.fullmatch(r"[a-z0-9-]+", args.posthoc_label):
    parser.error("Safe post-hoc label required")
if args.compiler_replay_label and not re.fullmatch(r"[a-z0-9-]+", args.compiler_replay_label):
    parser.error("Safe compiler replay label required")
if len(args.probes) != len(set(args.probes)):
    parser.error("Duplicate browser probes")
if (args.compiler_replay_label or args.samples or args.probes) and not args.posthoc_label:
    parser.error("Replay or selected samples require an explicitly separate post-hoc qualification")
load_dotenv()
logging.disable(logging.INFO)
if os.getenv("ENV_TYPE") != "dev":
    parser.error("DEV only")
run = args.run.resolve()
if not run.is_relative_to(Path.cwd() / "e2e/artifacts/builder"):
    parser.error("Owned E2E artifacts only")
plan, results = read(run / "plan.json"), read(run / "results.json")
if plan.get("task") == "support-desk-ru" and not args.probes:
    args.probes = ["prototype-review", "prototype-capabilities", "prototype-interactions"]
if not results["complete"] and not args.partial:
    parser.error("Generation incomplete; --partial explicitly required")
fingerprints = source_fingerprints(Path.cwd())
if fingerprints != plan["runtime_source_fingerprints"] and not args.posthoc_label:
    from adaos.e2e.runtime_amendment import validated_sources
    validated_sources(Path.cwd(), plan, run)
changed_sources = sorted(key for key in set(fingerprints) | set(plan["runtime_source_fingerprints"])
    if fingerprints.get(key) != plan["runtime_source_fingerprints"].get(key))
posthoc_allowed = {
    "src/adaos/services/builder/semantic_prototype.py",
    *[f"e2e/stand/browser/{name}.mjs" for name in ("prototype-navigation", "prototype-interactions",
        "prototype-procurement", "prototype-procurement-lines", "prototype-procurement-fields")],
}
if args.posthoc_label and set(changed_sources) - posthoc_allowed:
    raise ValueError("Unexpected product/source drift in post-hoc qualification")
output = run / ("browser-posthoc-" + args.posthoc_label if args.posthoc_label else "browser-requalification-mcp")
if output.exists() != args.resume:
    parser.error("Requires a new output, or --resume")
output.mkdir(exist_ok=args.resume)
from adaos.apps.cli.app import Settings, init_ctx
from adaos.services.agent_context import get_ctx
init_ctx(Settings.from_sources())
os.environ["ADAOS_CONTROL_URL"] = "http://127.0.0.1:8777"
# This wrapper performs no generation; it must never create an MCP lease.
os.environ["ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED"] = "0"
ctx = get_ctx()
handler = Path(ctx.paths.dev_skills_dir()) / "builder_skill/handlers/main.py"
handler_sha256 = "sha256:" + hashlib.sha256(handler.read_bytes()).hexdigest()
if handler_sha256 != plan["native_builder_sha256"] and not args.posthoc_label:
    raise ValueError("Native Builder changed before frozen qualification")
builder = native_builder(handler)
health = subprocess.run(["node", "e2e/stand/browser/prototype-client-health.mjs"],
    capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
_write_json(output / "client-health.json", json.loads(health.stdout))
_write_json(output / "source-fingerprints.json", {**fingerprints,
    "qualification_wrapper": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "native_builder": handler_sha256})
from adaos.sdk.llm import llm_client
selected = [item for item in results["samples"] if not args.samples or f"{item['order']:02d}-{item['arm']}" in args.samples]
if args.samples and len(selected) != len(set(args.samples)):
    parser.error("Unknown selected sample")
replay = run.parent / ("compiler-replay-" + args.compiler_replay_label) / run.name
configuration = {"posthoc_label": args.posthoc_label, "compiler_replay_label": args.compiler_replay_label,
    "samples": [f"{item['order']:02d}-{item['arm']}" for item in selected], "changed_sources": changed_sources,
    "selected_probes": args.probes,
    "new_llm_calls": 0, "original_evidence_overwritten": False, "task_and_model_outputs_unchanged": True}
if args.resume and read(output / "configuration.json") != configuration:
    raise ValueError("Post-hoc qualification configuration drift")
_write_json(output / "configuration.json", configuration)
rows = []
with preserved_preview(args.host, output), patch.object(llm_client, "submit_response_job", side_effect=AssertionError("Qualification must never submit a model")):
    for item in selected:
        if source_fingerprints(Path.cwd()) != fingerprints:
            raise ValueError("Source drift during browser qualification")
        sample = run / f"{item['order']:02d}-{item['arm']}"
        target = output / sample.name
        if args.resume and (target / "result.json").exists():
            rows.append(read(target / "result.json"))
            continue
        target.mkdir(exist_ok=args.resume)
        started = time.perf_counter()
        replay_result = replay / sample.name / "generation-result.json"
        repaired = sample / "planned-repair-result.json"
        candidate = (read(replay_result) if args.compiler_replay_label and replay_result.exists()
            else read(repaired) if plan.get("context_mode") == "composition" and repaired.exists()
            else read(sample / "generation-result.json"))
        if not candidate.get("ok"):
            checked = {"ok": False, "skipped": "native_generation_failed"}
        else:
            try:
                checked = browser_review(builder, read(sample / "session.json"), candidate,
                    target, read(sample / "creation.json"), Path(ctx.paths.dev_dir()).name, run.name, host=args.host,
                    selected_probes=args.probes or None, observe_delivery=plan.get("context_mode") == "composition")
            except Exception as exc:
                checked = {"ok": False, "error": type(exc).__name__, "detail": str(exc)}
        row = {"sample": sample.name, "arm": item["arm"], **checked,
            "qualification_seconds": round(time.perf_counter() - started, 3)}
        _write_json(target / "result.json", row)
        rows.append(row)
        _write_json(output / "results.json", {"complete": False, "samples": rows, "new_llm_calls": 0})
        print(json.dumps({"stage": "browser_qualified", **row}, ensure_ascii=False), flush=True)
_write_json(output / "results.json", {"complete": results["complete"] and len(rows) == len(selected),
    "samples": rows, "new_llm_calls": 0, "partial": args.partial,
    "posthoc": bool(args.posthoc_label), "configuration": configuration,
    "preview_restoration": read(output / "preview-restored.json")})
print(json.dumps({"stage": "qualification_round_complete", "samples": len(rows), "generation_complete": results["complete"], "restored": True}), flush=True)
