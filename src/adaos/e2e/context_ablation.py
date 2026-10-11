"""Matched live coding E2E for Builder context delivery (no publish/activation)."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from adaos.services.builder_context import STRATEGIES, digest
from adaos.services.skill_factory_worker import (
    LocalSkillFactoryWorker, SubprocessCodexExecutor, _codex_jsonl_usage,
    _deterministic_repair_prompt, _implementation_sdk_contract_bundle,
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tree_identity(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file() and not any(
                part in {".git", "__pycache__", ".pytest_cache"} for part in path.parts)}


def fixed_contract_index() -> dict[str, Any]:
    bundle = _implementation_sdk_contract_bundle()
    contracts = bundle.get("contracts") or []
    return {
        "schema": "adaos.builder.descriptor_working_set.v1", "query_digest": digest(contracts),
        "headers": [{"descriptor_id": "public-sdk", "item_id": item.get("name"),
                     "kind": "method", "title": item.get("name"),
                     "summary": str(item.get("signature") or item.get("summary") or "Public SDK method"),
                     "fingerprint": digest(item)} for item in contracts],
        "details": [{"descriptor_id": "public-sdk", "item_id": item.get("name"), "level": "exact",
                     "item": {"title": item.get("name"), "summary": json.dumps(item, ensure_ascii=False)}}
                    for item in contracts],
        "evidence": {"status": "retained", "result_digest": digest(bundle)},
    }


def assignment_for(task: dict[str, Any], strategy: str) -> dict[str, Any]:
    return {
        "task_id": f"ablation.{task['id']}.{strategy}",
        "target": {"type": "skill", "id": "context_probe"},
        "forge": {"sparse_paths": ["skills/context_probe/"]},
        "constraints": {},
        "realize_request": {"artifacts": {
            "implementation_brief": task["brief"], "context_strategy": strategy,
            "execution_budget": {"max_context_tokens": 64_000, "target_context_tokens": 24_000,
                                 "max_model_tokens": 300_000, "max_billable_tokens": 600_000},
            "repair_hints": {"prompt_facts": task["facts"]},
            "context_packet": {
                "project": {"project_ref": "skill:context_probe", "phase": "Automation"},
                "change": {"change_id": task["id"], "intent": task["brief"], "status": "accepted"},
                "facets": {
                    "execution_authority": {"status": "present", "allowed_paths": ["skills/context_probe/"]},
                    "constraints": {"status": "present", "acceptance_constraints": [task["brief"]]},
                },
            },
        }},
    }


def prepare_source(workspace: Path, task: dict[str, Any]) -> None:
    skill = workspace / "skills" / "context_probe"
    (skill / "handlers").mkdir(parents=True)
    (skill / "handlers" / "main.py").write_text(task["source"], encoding="utf-8")
    (skill / "skill.yaml").write_text(
        "name: context_probe\nversion: 0.1.0\ndescription: Fixed isolated context evaluation\n"
        "entry: handlers/main.py\nsupported_locales: [en, ru]\ndependencies: []\n",
        encoding="utf-8",
    )
    if task["id"] == "locale_identity":
        write_json(skill / "i18n" / "en.json", {"save": "Save", "cancel": "Cancel"})
        write_json(skill / "i18n" / "ru.json", {"save": "Сохранить", "cancel": "Отмена"})


def validate_candidate(worker: LocalSkillFactoryWorker, assignment: dict[str, Any],
                       sample: Path, task: dict[str, Any], acceptance: str,
                       before: dict[str, str], input_before: dict[str, str]) -> dict[str, Any]:
    workspace = sample / "workspace"
    after = tree_identity(workspace)
    changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    authority_errors: list[str] = []
    input_after = tree_identity(sample / "input")
    authority_errors.extend(f"immutable task input changed: {key}" for key, value in input_before.items()
                            if input_after.get(key) != value)
    try:
        worker._validate_changed_paths(assignment, changed, workspace=workspace)
    except ValueError as exc:
        authority_errors.append(str(exc))
    manifest = yaml.safe_load((workspace / "skills/context_probe/skill.yaml").read_text(encoding="utf-8"))
    if manifest.get("version") != "0.1.0":
        authority_errors.append("model changed checkpoint-owned release metadata")
    for path in (workspace / "skills/context_probe").rglob("*.py"):
        try:
            syntax = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(syntax):
                names = ([node.module or ""] if isinstance(node, ast.ImportFrom) else
                         [item.name for item in node.names] if isinstance(node, ast.Import) else [])
                if any(name.startswith(("adaos.services", "adaos.runtime", "adaos.integrations")) for name in names):
                    authority_errors.append(f"non-public platform import: {path.relative_to(workspace)}")
        except (SyntaxError, UnicodeError) as exc:
            errors.append(str(exc))
    # This trusted test is absent during generation and only materialized now.
    test_path = sample / "acceptance" / "test_acceptance.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(acceptance, encoding="utf-8")
    env = os.environ.copy()
    env.update({"ADAOS_ABLATION_SOURCE": str(workspace / "skills/context_probe/handlers/main.py"),
                "ADAOS_ABLATION_TASK": task["id"], "PYTHONPATH": str(worker.repo_root / "src")})
    checked = subprocess.run([sys.executable, "-m", "pytest", str(test_path), "-q", "--disable-warnings",
                              "--basetemp", str(sample / "acceptance-runtime")],
                             cwd=sample, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    (sample / "acceptance" / "pytest.txt").write_text(checked.stdout + checked.stderr, encoding="utf-8")
    if checked.returncode:
        errors.append((checked.stdout + checked.stderr)[-8_000:])
    # Reuse trusted package-test and manifest checks, not model-reported claims.
    trusted_validation = worker._validate_workspace(assignment, workspace)
    checks.extend(trusted_validation["checks"])
    errors.extend(trusted_validation["errors"])
    result = {"ok": not errors and not authority_errors, "acceptance_returncode": checked.returncode,
              "authority_violations": len(authority_errors), "authority_errors": authority_errors,
              "errors": errors, "checks": checks, "changed_paths": changed,
              "acceptance_sha256": digest(acceptance)}
    write_json(sample / "acceptance" / "result.json", result)
    return result


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    arms = {}
    for strategy in STRATEGIES:
        rows = [row for row in samples if row["strategy"] == strategy]
        arms[strategy] = {
            "samples": len(rows), "passed": sum(row.get("ok") is True for row in rows),
            "first_pass": sum(row.get("first_pass") is True for row in rows),
            "repairs": sum(row.get("repairs", 0) for row in rows),
            "authority_violations": sum(row.get("authority_violations", 0) for row in rows),
            "false_blockers": sum(row.get("false_blocker") is True for row in rows),
            "latency_seconds": round(sum(row.get("latency_seconds", 0) for row in rows), 3),
            "prompt_tokens_estimated": sum(row.get("prompt_tokens_estimated", 0) for row in rows),
            **{key: sum(row.get("usage", {}).get(key, 0) for row in rows)
               for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")},
        }
        arms[strategy]["fresh_input_tokens"] = arms[strategy]["input_tokens"] - arms[strategy]["cached_input_tokens"]
    return {"arms": arms, "claim": "exploratory_matched_coding_e2e_not_statistically_powered",
            "scope": "production packet/executor plus independent hermetic acceptance; no live browser or publication"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--max-repairs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.repetitions < 1 or args.max_repairs < 0:
        parser.error("positive repetitions and nonnegative repairs required")
    repo = Path(__file__).resolve().parents[3]
    fixtures = repo / "e2e/builder/development/context-ablation"
    tasks = json.loads((fixtures / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    acceptance = (fixtures / "acceptance.py").read_text(encoding="utf-8")
    descriptors = fixed_contract_index()
    implementation_files = (
        "src/adaos/services/builder_context.py", "src/adaos/services/platform_accents.py",
        "src/adaos/services/prompt_rules.py", "src/adaos/services/skill_factory_worker.py",
        "src/adaos/e2e/context_ablation.py", "e2e/builder/development/context-ablation/tasks.json",
        "e2e/builder/development/context-ablation/acceptance.py",
    )
    implementation_identity = {name: "sha256:" + hashlib.sha256((repo / name).read_bytes()).hexdigest()
                               for name in implementation_files}
    root = (args.output or repo / "e2e/artifacts" / ("context-ablation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))).resolve()
    if not root.is_relative_to((repo / "e2e/artifacts").resolve()):
        parser.error("output must stay under e2e/artifacts")
    root.mkdir(parents=True, exist_ok=False)
    schedule = [{"task": task["id"], "repetition": repetition + 1, "strategy": strategy}
                for repetition in range(args.repetitions) for index, task in enumerate(tasks)
                for strategy in (*STRATEGIES[(index + repetition) % 4:], *STRATEGIES[:(index + repetition) % 4])]
    configuration = {"schema": "adaos.e2e.context_ablation.v1", "model": args.model, "effort": args.effort,
                     "repetitions": args.repetitions, "max_repairs": args.max_repairs,
                     "timeout_seconds": args.timeout, "tasks": tasks, "schedule": schedule,
                     "dataset_digest": digest({"tasks": tasks, "acceptance": acceptance}),
                     "contract_index_digest": digest(descriptors),
                     "implementation_identity": implementation_identity,
                     "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
                     "rollout": "full remains default; delivery strategy alone varies"}
    write_json(root / "configuration.json", configuration)
    samples: list[dict[str, Any]] = []
    for entry in schedule:
        if any("sha256:" + hashlib.sha256((repo / name).read_bytes()).hexdigest() != value
               for name, value in implementation_identity.items()):
            raise RuntimeError("ablation implementation changed after freezing; retain this run as incomplete")
        task = next(item for item in tasks if item["id"] == entry["task"])
        sample = root / f"{entry['task']}-r{entry['repetition']}-{entry['strategy']}"
        workspace = sample / "workspace"
        prepare_source(workspace, task)
        before = tree_identity(workspace)
        worker = LocalSkillFactoryWorker(
            state_dir=sample / "state", repo_root=repo, dev_skills_root=sample / "dev/skills",
            dev_scenarios_root=sample / "dev/scenarios",
            executor=SubprocessCodexExecutor(model=args.model, reasoning_effort=args.effort,
                                            timeout_seconds=args.timeout, repo_root=repo),
        )
        worker._init_git_workspace(workspace, "eval/context")
        assignment = assignment_for(task, entry["strategy"])
        write_json(sample / "input/descriptor-working-set.json", descriptors)
        worker._build_packet(assignment, workspace, sample / "input", descriptor_working_set=descriptors)
        selection = json.loads((sample / "input/context-selection.json").read_text(encoding="utf-8"))
        input_before = tree_identity(sample / "input")
        row = {**entry, "source_digest": digest(before), "selection": selection,
               "prompt_tokens_estimated": sum(item["estimated_tokens"] for item in selection["selected"])}
        if args.plan_only:
            samples.append(row)
            continue
        print(f"START {entry['task']} r{entry['repetition']} {entry['strategy']}", flush=True)
        started = time.monotonic()
        usage = {key: 0 for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")}
        prompt = (sample / "input/task.md").read_text(encoding="utf-8")
        result = None
        try:
            for attempt in range(args.max_repairs + 1):
                result = worker._execute_codex(task_id=assignment["task_id"], assignment=assignment,
                                              workspace=workspace, prompt=prompt, output_dir=sample / "output")
                (sample / "runtime").mkdir(exist_ok=True)
                worker._record_codex_attempt(sample / "runtime", result, attempt=attempt)
                provider = _codex_jsonl_usage(sample / "output/codex-live.jsonl")
                for key in usage:
                    usage[key] += int(provider.get(key) or 0)
                validation = validate_candidate(worker, assignment, sample, task, acceptance, before, input_before)
                write_json(sample / "acceptance" / f"attempt-{attempt + 1}.json", validation)
                if attempt == 0:
                    row["first_pass"] = validation["ok"] and result.returncode == 0
                row.update(ok=validation["ok"] and result.returncode == 0, repairs=attempt,
                           authority_violations=validation["authority_violations"], validation=validation)
                if row["ok"] or validation["authority_violations"]:
                    break
                prompt = _deterministic_repair_prompt(prompt, validation["errors"])
            row["false_blocker"] = bool(result and not row["ok"] and
                ("blocker" in result.final_message.lower() or "missing_contract" in result.final_message))
            row.update(worker._context_run_evidence(sample / "runtime", {"ok": row["ok"], "independent_validation": "passed" if row["ok"] else "failed"}))
        except Exception as exc:
            row.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        row.update(usage=usage, latency_seconds=round(time.monotonic() - started, 3))
        samples.append(row)
        write_json(sample / "result.json", row)
        report = {**summarize(samples), "configuration_digest": digest(configuration), "samples": samples,
                  "status": "complete" if len(samples) == len(schedule) else "running"}
        write_json(root / "report.json", report)
        print(f"END {entry['task']} {entry['strategy']} ok={row['ok']} tokens={usage['input_tokens']} seconds={row['latency_seconds']}", flush=True)
    if args.plan_only:
        write_json(root / "report.json", {**summarize(samples), "samples": samples, "status": "plan_only"})
    print(str(root), flush=True)
    return 0 if args.plan_only or all(row.get("ok") for row in samples) else 1


if __name__ == "__main__":
    raise SystemExit(main())
