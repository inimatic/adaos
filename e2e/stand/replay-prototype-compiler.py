"""Offline compiler replay on retained model outputs; never resubmit a model."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from unittest.mock import patch
from dotenv import load_dotenv

from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import acceptance, native_builder, read
from adaos.services.builder_context import canonical, digest

parser = argparse.ArgumentParser()
parser.add_argument("--run", type=Path, required=True)
parser.add_argument("--label", required=True)
parser.add_argument("--include-repairs", action="store_true", help="Replay the final retained candidate; retain primary outcomes separately")
args = parser.parse_args()
run = args.run.resolve()
if not run.is_relative_to(Path.cwd() / "e2e/artifacts/builder") or not re.fullmatch(r"[a-z0-9-]+", args.label):
    parser.error("Owned E2E run and a safe new replay label required")
output = run / ("compiler-replay-" + args.label)
if output.exists():
    parser.error("Replay evidence cannot be overwritten")
output.mkdir()
load_dotenv()
logging.disable(logging.INFO)
os.environ["ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED"] = "0"
from adaos.apps.cli.app import Settings, init_ctx
from adaos.services.agent_context import get_ctx
from adaos.sdk.builder import prototype
from adaos.sdk.developer import projects
from adaos.sdk.llm import llm_client
init_ctx(Settings.from_sources())
builder = native_builder(Path(get_ctx().paths.dev_skills_dir()) / "builder_skill/handlers/main.py")
rows = []
with patch.object(llm_client, "submit_response_job", side_effect=AssertionError("Offline replay must never submit a model")):
    registration_path=run/'preregistration.json'
    cohorts=([run/item['id'] for item in read(registration_path)['cohorts']]
        if registration_path.exists() else sorted(run.glob("*high-mcp-*")))
    for cohort in cohorts:
        results = read(cohort / "results.json")
        assert results["complete"]
        brief = read(cohort / "source-brief.json")
        for row in results["samples"]:
            sample = cohort / f"{row['order']:02d}-{row['arm']}"
            repaired=sample/'planned-repair-result.json'
            original = read(repaired if args.include_repairs and repaired.exists() else sample / "generation-result.json")
            session = read(sample / "session.json")
            assert row["scenario_id"].startswith("test_protoctx_")
            root = projects.resolve_root("scenario", row["scenario_id"]).resolve()
            text = original.get("raw_response") or original.get("last_response") or ""
            for artifact in reversed(original.get("candidate_artifacts") or []):
                if artifact.get("stage") == "semantic-repair-merged":
                    path = (root / artifact["path"]).resolve()
                    assert path.is_relative_to(root)
                    merged = read(path)["structured_candidate"]
                    if merged:
                        text = canonical(merged)
                        break
            record = {"cohort": cohort.name, "sample": sample.name, "original_generation_ok": row["generation_ok"],
                "includes_repair":args.include_repairs and repaired.exists(),
                "effective_candidate_sha256": digest(text), "new_llm_calls": 0}
            destination=output/cohort.name/sample.name
            destination.mkdir(parents=True,exist_ok=True)
            (destination/'effective-candidate.txt').write_text(text,encoding='utf-8')
            try:
                candidate = builder._extract_json_object(text)
                # Match native Builder's mandatory compact-ref expansion.
                candidate = prototype.expand_requirement_aliases(candidate, brief=brief)
                prototype.normalize_semantic_candidate(candidate, brief=brief)
                record["semantic_input_accepted"] = True
            except Exception as exc:
                record.update(semantic_input_accepted=False, input_error=str(exc))
            try:
                result = builder._parse_llm_webui_transform_output(output_text=text,
                    previous_preview=session["initial_webui"].get("preview_state") or {},
                    before_webui=session["initial_webui"], prototype_brief=brief,
                    project_ref=session["project_ref"], output_mode="semantic_v2")
                checked = acceptance(result, brief, task=read(cohort/'plan.json')['task'])
                record.update(generation_ok=result["ok"], structural_ok=checked["ok"], semantic_digest=result.get("semantic_digest"))
                _write_json(output / cohort.name / sample.name / "generation-result.json", result)
            except Exception as exc:
                record.update(generation_ok=False, structural_ok=False, error=str(exc), findings=getattr(exc, "findings", []))
                _write_json(output / cohort.name / sample.name / 'generation-result.json',
                    {'ok':False,'detail':str(exc),'error':'offline_replay_failed','findings':getattr(exc,'findings',[])})
            rows.append(record)
source = Path('src/adaos/services/builder/semantic_prototype.py')
report = {"schema": "adaos.e2e.prototype_compiler_replay.v1", "label": args.label, "samples": rows,
    "includes_repairs":args.include_repairs,
    "compiler_sha256": 'sha256:' + hashlib.sha256(source.read_bytes()).hexdigest(),
    "new_llm_calls": 0, "new_application_writes": 0, "generation_ok": sum(row["generation_ok"] for row in rows),
    "contract_compiler_inconsistencies": sum(row["semantic_input_accepted"] and not row["generation_ok"] for row in rows),
    "scope": "Post-hoc deterministic compiler diagnostic, separate from original paired generation and browser metrics"}
_write_json(output / "report.json", report)
print(json.dumps({key: value for key, value in report.items() if key != "samples"}), flush=True)
