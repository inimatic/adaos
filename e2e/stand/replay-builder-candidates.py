"""Replay archived model outputs against current Core, without rewriting run evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess

from adaos.sdk.builder.prototype import apply_state_repair, compile_semantic_candidate
from adaos.sdk.developer.prototypes import validate_resource_spec
from adaos.sdk.developer.ui import evaluate


def replay(run: Path, builder_skill: Path | None = None) -> list[dict]:
    builder = None
    if builder_skill:
        spec = importlib.util.spec_from_file_location("builder_replay_validation", builder_skill / "handlers/main.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
    rows = []
    for checkpoint in sorted(run.glob("checkpoints/*/attempt-*.json")):
        value = json.loads(checkpoint.read_text(encoding="utf-8"))
        brief = value.get("context", {}).get("outputs", {}).get("design", {}).get("patch", {}).get("prototype_brief")
        if not brief:
            continue
        case = checkpoint.parent.name
        attempt = int(checkpoint.stem.split("-")[-1])
        generation_path = run / "evidence" / "generation" / f"{case}-attempt-{attempt:02}.json"
        if not generation_path.is_file():
            rows.append({"case": case, "attempt": attempt, "status": "not_replayed", "reason": "No retained terminal generation evidence"})
            continue
        generation = json.loads(generation_path.read_text(encoding="utf-8"))
        instruction = None
        for request_path in sorted((run / "evidence/model-io" / f"{case}-attempt-{attempt:02}").rglob("*.request.json")):
            request = json.loads(request_path.read_text(encoding="utf-8"))
            for message in request.get("messages") or []:
                try:
                    dynamic = json.loads(message.get("content") or "{}")
                except (ValueError, TypeError):
                    continue
                if isinstance(dynamic, dict) and isinstance(dynamic.get("builder_request"), dict):
                    instruction = dynamic["builder_request"].get("instruction")
            if instruction:
                break
        base = None
        findings = []
        for artifact in generation.get("candidate_artifacts", []):
            if artifact.get("kind") != "raw_model_output":
                continue
            row = {"case": case, "attempt": attempt, "stage": artifact["stage"], "source": artifact["evidence_ref"]}
            try:
                candidate = json.loads((run / artifact["evidence_ref"]).read_text(encoding="utf-8"))["structured_candidate"]
                if candidate.get("schema") in {"adaos.builder.state_repair.v1", "adaos.builder.state_repair.v2"}:
                    original = next((attempt.get("validation", {}).get("findings") for attempt in generation.get("attempts", [])
                                     if attempt.get("validation", {}).get("findings")), findings)
                    candidate = apply_state_repair(base, candidate, original)
                    row["repair_authority"] = "original_preflight_findings"
                else:
                    base = candidate
                compiled = compile_semantic_candidate(candidate, brief=brief)
                for resource in compiled.get("prototype_resources", []):
                    validate_resource_spec(compiled["webui"], resource["records"], resource_type=resource["resource_type"])
                if builder:
                    validation = builder._validate_builder_webui_payload(compiled["webui"], compiled.get("preview_state") or {})
                    row["builder_validation"] = validation
                    if not validation.get("ok"):
                        raise ValueError(f"Builder payload validation: {validation}")
                if not instruction:
                    raise ValueError("No exact retained user instruction for postcondition replay")
                evaluation = evaluate(instruction, compiled["webui"],
                                      prototype_records=compiled.get("prototype_records"),
                                      prototype_resources=compiled.get("prototype_resources"),
                                      locale_dictionaries=compiled.get("locale_dictionaries"), domain_packs=[])
                row["postconditions"] = [item for item in evaluation.get("postconditions") or [] if not item.get("ok")]
                if not evaluation.get("ok"):
                    raise ValueError(f"Postconditions failed: {row['postconditions']}")
                row.update(status="passed", normalizations=compiled["normalizations"])
            except Exception as exc:
                findings = getattr(exc, "findings", [])
                row.update(status="failed", error=str(exc), findings=getattr(exc, "findings", []))
            rows.append(row)
            print(f"{case} {attempt} {artifact['stage']}: {row['status']} {row.get('error', '')[:260]}", flush=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--builder-skill", type=Path, help="Validate the complete current DEV Builder payload boundary, without applying it")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Replay output already exists; retain immutable evidence")
    result = {"kind": "compiler_replay_not_fresh_generation", "source_run": str(args.run),
              "core_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "working_diff": subprocess.check_output(["git", "diff", "--stat"], text=True),
              "builder_skill": str(args.builder_skill) if args.builder_skill else None,
              "rows": replay(args.run, args.builder_skill)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
