"""Replay archived model outputs against current Core, without rewriting run evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from adaos.sdk.builder.prototype import apply_state_repair, compile_semantic_candidate
from adaos.sdk.developer.prototypes import validate_resource_spec


def replay(run: Path) -> list[dict]:
    rows = []
    for checkpoint in sorted(run.glob("checkpoints/*/attempt-*.json")):
        value = json.loads(checkpoint.read_text(encoding="utf-8"))
        brief = value.get("context", {}).get("outputs", {}).get("design", {}).get("patch", {}).get("prototype_brief")
        if not brief:
            continue
        case = checkpoint.parent.name
        attempt = int(checkpoint.stem.split("-")[-1])
        generation_path = run / "evidence" / "generation" / f"{case}-attempt-{attempt:02}.json"
        generation = json.loads(generation_path.read_text(encoding="utf-8"))
        base = None
        findings = []
        for artifact in generation.get("candidate_artifacts", []):
            if artifact.get("kind") != "raw_model_output":
                continue
            row = {"case": case, "attempt": attempt, "stage": artifact["stage"], "source": artifact["evidence_ref"]}
            try:
                candidate = json.loads((run / artifact["evidence_ref"]).read_text(encoding="utf-8"))["structured_candidate"]
                if candidate.get("schema") == "adaos.builder.state_repair.v1":
                    candidate = apply_state_repair(base, candidate, findings)
                else:
                    base = candidate
                compiled = compile_semantic_candidate(candidate, brief=brief)
                for resource in compiled.get("prototype_resources", []):
                    validate_resource_spec(compiled["webui"], resource["records"], resource_type=resource["resource_type"])
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
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Replay output already exists; retain immutable evidence")
    result = {"kind": "compiler_replay_not_fresh_generation", "source_run": str(args.run),
              "core_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "working_diff": subprocess.check_output(["git", "diff", "--stat"], text=True),
              "rows": replay(args.run)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
