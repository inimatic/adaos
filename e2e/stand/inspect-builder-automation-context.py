"""Inspect a retained Automation handoff without submitting, applying or approving it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _write_json
from adaos.services.agent_context import get_ctx
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker


def inspect_admitted_input(input_dir: Path) -> dict:
    """Report retained inputs, never reconstructed context or task credentials."""
    receipts = {}
    documents = {}
    for name in ("task.md", "packet.json", "prototype-resource-handoff.json", "implementation-bindings.json"):
        path = input_dir / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        receipts[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        text = raw.decode("utf-8")
        documents[name] = text if name.endswith(".md") else json.loads(text)
    model_attempts = []
    for path in sorted((input_dir / "model-attempts").glob("*.prompt.md")):
        raw = path.read_bytes()
        model_attempts.append({"path": path.relative_to(input_dir).as_posix(),
                               "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    packet = documents["packet.json"]
    prompt = documents["task.md"]
    brief = packet.get("brief") or ""
    acceptance = (packet.get("context_packet", {}).get("artifacts", {})
                  .get("prototype", {}).get("acceptance", {}))
    handoff = documents.get("prototype-resource-handoff.json", {})
    bindings = documents.get("implementation-bindings.json", {})
    rules = bindings.get("binding_rules", {})
    normalize = lambda text: text.replace("\r\n", "\n").strip()
    checks = {
        "full_brief_in_task": bool(brief) and normalize(brief) in normalize(prompt),
        "accepted_revision_present": bool(acceptance.get("revision")),
        "handoff_acceptance_matches": bool(acceptance.get("acceptance_id"))
        and handoff.get("acceptance_id") == acceptance.get("acceptance_id"),
        "production_seeds_empty": bool(handoff.get("resources"))
        and all(not item.get("bundle", {}).get("seed") for item in handoff.get("resources", [])),
        "create_initialization_contract": bool(rules.get("creation")),
        "verification_ownership_explicit": "independent acceptance owns browser journeys" in prompt
        and "Explicitly mark checks not executed" in prompt,
    }
    return {
        "scope": "retained model input audit; structural checks are not semantic or Automation acceptance",
        "task_id": packet.get("task_id"),
        "inputs": receipts,
        "model_attempts": model_attempts,
        "model_attempt_capture": "present" if model_attempts else "not_recorded_or_no_model_call",
        "brief": {"characters": len(brief), "sha256": hashlib.sha256(brief.encode("utf-8")).hexdigest()},
        "prototype": {key: acceptance.get(key) for key in ("acceptance_id", "revision", "webui_digest")},
        "handoff_mode": handoff.get("mode"),
        "binding_rule_keys": sorted(rules),
        "checks": checks,
        "manual_review_required": [
            "Goal and authorized additions versus preserved behavior",
            "Requested capabilities versus admitted contracts and explicit limitations",
            "Conflicting instructions and verification ownership",
            "Discovery relevance, duplication and unresolved ambiguity",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assignment", type=Path)
    parser.add_argument("--admitted", action="store_true", help="Audit actual retained input files, without reconstruction")
    parser.add_argument("--output", type=Path, help="New retained-input report inside e2e/artifacts/builder")
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("This diagnostic requires ENV_TYPE=dev")
    assignment_path = args.assignment.resolve()
    if args.output and not args.admitted:
        parser.error("--output requires --admitted")
    if args.admitted:
        report = inspect_admitted_input(assignment_path.parent)
        if args.output:
            output = args.output.resolve()
            root = (Path.cwd() / "e2e/artifacts/builder").resolve()
            if not output.is_relative_to(root) or output.exists():
                parser.error("Output must be a new file inside e2e/artifacts/builder")
            _write_json(output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    raw = assignment_path.read_bytes()
    assignment = json.loads(raw)
    workspace = assignment_path.parent.parent / "workspace"
    if not workspace.is_dir():
        parser.error("Retained sibling workspace is absent")
    init_ctx(Settings.from_sources())
    ctx = get_ctx()
    worker = LocalSkillFactoryWorker(
        state_dir=Path(ctx.paths.state_dir()), repo_root=Path.cwd(),
        dev_skills_root=Path(ctx.paths.dev_skills_dir()),
        dev_scenarios_root=Path(ctx.paths.dev_scenarios_dir()),
    )
    handoff = worker._prototype_resource_handoff_from_assignment(assignment, workspace)
    if handoff is None:
        parser.error("This assignment has no resource handoff")
    old_path = assignment_path.parent / "prototype-resource-handoff.json"
    old = json.loads(old_path.read_bytes()) if old_path.is_file() else {}
    print(json.dumps({
        "scope": "read-only context reconstruction; not an Automation pass",
        "task_id": assignment.get("task_id"),
        "assignment_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "acceptance_id": handoff["acceptance_id"],
        "mode": handoff["mode"],
        "previous_obligations": len(old.get("automation_requirements") or []),
        "current_obligations": len(handoff.get("automation_requirements") or []),
        "obligation_refs": [item["requirement_ref"] for item in handoff.get("automation_requirements") or []],
        "completion": handoff["completion"],
        "production_seed_counts": [len(item["bundle"].get("seed") or []) for item in handoff["resources"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
