"""Archive and remove exact local test drafts recorded by older Builder E2E runs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-run", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project", action="append", default=[], help="Explicitly reviewed older manual test project; replaces run discovery")
    parser.add_argument("--receipt-tag", default="")
    args = parser.parse_args()
    if args.receipt_tag and not args.receipt_tag.isalnum():
        raise SystemExit("Receipt tag must be alphanumeric")
    load_dotenv()
    if os.getenv("ENV_TYPE", "").lower() != "dev":
        raise SystemExit("Test cleanup requires ENV_TYPE=dev")
    from adaos.sdk.developer import compositions
    from adaos.apps.bootstrap import init_ctx
    from adaos.services.agent_context import get_ctx
    from adaos.services.builder import BuilderWorkbenchService

    root = Path("e2e/artifacts/builder").resolve()
    boundary = (root / args.before_run).resolve()
    if boundary.parent != root:
        raise SystemExit("Invalid run path")
    cutoff = datetime.fromisoformat(read(boundary / "run.json")["started_at"])
    init_ctx()
    projects_root = Path(get_ctx().paths.dev_projects_dir()).resolve()
    scenarios_root = Path(get_ctx().paths.dev_scenarios_dir()).resolve()
    service = BuilderWorkbenchService.from_context()
    plan, seen = [], set()
    for run_path in sorted(root.glob("*/run.json")):
        if args.project:
            break
        run = read(run_path)
        if not run.get("started_at") or datetime.fromisoformat(run["started_at"]) >= cutoff:
            continue
        for checkpoint_path in sorted(run_path.parent.glob("checkpoints/*/attempt-*.json")):
            checkpoint = read(checkpoint_path)
            if checkpoint.get("run_id") != run.get("run_id"):
                continue
            context = checkpoint.get("context") or {}
            for owned in context.get("owned_artifacts") or []:
                project_id = str(owned.get("project_id") or "")
                if not project_id or project_id in seen:
                    continue
                project_path = (projects_root / project_id).resolve()
                if project_path.parent != projects_root or not (project_path / "project.yaml").is_file():
                    continue
                project = compositions.get(project_id)
                components = project["components"]["owned"]
                if len(components) != 1 or components[0]["ref"] != owned.get("primary_ref"):
                    continue
                kind, _, scenario_id = components[0]["ref"].partition(":")
                scenario_path = (scenarios_root / scenario_id).resolve()
                if kind != "scenario" or scenario_path.parent != scenarios_root:
                    continue
                draft_path = scenario_path / "builder.draft.json"
                if not draft_path.is_file():
                    continue
                draft = read(draft_path)
                if draft.get("draft_id") != owned.get("draft_id"):
                    continue
                source = str((draft.get("metadata") or {}).get("webspace_id") or "")
                if source != context.get("webspace_id") or not source.startswith("e2e-"):
                    continue
                seen.add(project_id)
                plan.append({
                    "run_id": run["run_id"], "project_id": project_id,
                    "project_path": str(project_path), "scenario_path": str(scenario_path),
                    "draft_id": owned["draft_id"], "primary_ref": owned["primary_ref"],
                    "manifest_digest": project["manifest_digest"], "webspace_id": source,
                })
    for project_id in args.project:
        project_path = (projects_root / project_id).resolve()
        if project_path.parent != projects_root or not (project_path / "project.yaml").is_file():
            raise RuntimeError(f"Invalid selected project: {project_id}")
        project = compositions.get(project_id)
        import yaml
        manifest = yaml.safe_load((project_path / "project.yaml").read_text(encoding="utf-8"))
        if datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00")) >= cutoff:
            raise RuntimeError(f"Selected project is newer than the cutoff: {project_id}")
        components = project["components"]["owned"]
        if len(components) != 1 or not components[0]["ref"].startswith("scenario:"):
            raise RuntimeError(f"Selected project is not an isolated scenario: {project_id}")
        scenario_path = (scenarios_root / components[0]["ref"].partition(":")[2]).resolve()
        if scenario_path.parent != scenarios_root:
            raise RuntimeError("Unsafe scenario path")
        draft = read(scenario_path / "builder.draft.json") if scenario_path.exists() else {}
        plan.append({
            "run_id": "manual-test-explicit-selection", "project_id": project_id,
            "project_path": str(project_path), "scenario_path": str(scenario_path),
            "draft_id": draft.get("draft_id"), "primary_ref": components[0]["ref"],
            "manifest_digest": project["manifest_digest"],
            "webspace_id": str((draft.get("metadata") or {}).get("webspace_id") or "desktop-dev"),
            "template_only": draft.get("draft_id") == "template.scenario_default" and draft.get("source", {}).get("type") == "template",
        })
    receipt = boundary.parent / f"cleanup-before-{args.before_run}{'-selected' if args.project else ''}{'-' + args.receipt_tag if args.receipt_tag else ''}{'-applied' if args.apply else '-plan'}.json"
    output = {"before_run": args.before_run, "apply": args.apply, "count": len(plan), "items": plan}
    if args.apply and receipt.exists():
        raise SystemExit("Cleanup receipt already exists; inspect it before another operation")
    if args.apply:
        for item in plan:
            current = compositions.get(item["project_id"])
            if current["manifest_digest"] != item["manifest_digest"]:
                raise RuntimeError("Project changed after cleanup planning")
            if item.get("template_only"):
                from uuid import uuid4
                scenario = Path(item["scenario_path"]).resolve()
                if scenario.parent != scenarios_root or read(scenario / "builder.draft.json").get("draft_id") != "template.scenario_default":
                    raise RuntimeError("Template source changed after planning")
                archive = Path(service.state_dir) / "builder/archive" / f"template-test-{uuid4().hex}"
                archive.mkdir()
                shutil.copytree(scenario, archive / "artifact")
                shutil.rmtree(scenario)
                deleted = {"ok": True, "archive_root": str(archive), "unregistered_template_draft": True}
            elif item["draft_id"]:
                deleted = service.delete_development_skill(item["draft_id"], item["webspace_id"])
            else:
                if Path(item["scenario_path"]).exists():
                    raise RuntimeError("Orphan source appeared after planning")
                from uuid import uuid4
                archive = Path(service.state_dir) / "builder/archive" / f"orphan-project-{uuid4().hex}"
                archive.mkdir()
                deleted = {"ok": True, "archive_root": str(archive), "source_already_absent": True}
            item["draft_result"] = deleted
            if deleted.get("ok"):
                archive = Path(deleted["archive_root"]).resolve()
                if archive.parent != (Path(service.state_dir) / "builder/archive").resolve():
                    raise RuntimeError("Unexpected archive destination")
                shutil.copytree(item["project_path"], archive / "project")
                item["project_result"] = compositions.delete(
                    item["project_id"], expected_manifest_digest=item["manifest_digest"],
                    expected_primary_ref=item["primary_ref"],
                )
            receipt.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"project_id": item["project_id"], "ok": bool(item.get("project_result", {}).get("ok"))}, ensure_ascii=False), flush=True)
    receipt.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(plan), "receipt": str(receipt), "apply": args.apply}))


if __name__ == "__main__":
    main()
