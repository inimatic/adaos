"""Inspect a retained Automation handoff without submitting, applying or approving it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.services.agent_context import get_ctx
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assignment", type=Path)
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("This diagnostic requires ENV_TYPE=dev")
    assignment_path = args.assignment.resolve()
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
