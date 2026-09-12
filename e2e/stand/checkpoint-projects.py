"""Checkpoint explicitly selected local projects through the public AdaOS CLI."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.services.agent_context import get_ctx


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--space", choices=("dev", "workspace"), required=True)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--publish", action="store_true", help="Upload releases; Workspace also pushes scoped Git checkpoints")
    parser.add_argument("--source-checkpoint", action="store_true", help="DEV only: checkpoint owned component sources in Forge, without a ProjectRelease")
    parser.add_argument("--change-id", help="Required for source checkpoints")
    parser.add_argument("--resume", action="store_true", help="Skip successful entries in this same immutable batch")
    args = parser.parse_args()
    if args.source_checkpoint and (args.space != "dev" or not args.publish or not args.change_id):
        parser.error("Source checkpoint requires --space dev --publish --change-id")
    if args.change_id and not args.source_checkpoint:
        parser.error("--change-id is only meaningful with --source-checkpoint")
    load_dotenv(ROOT / ".env")
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("This technological stand requires ENV_TYPE=dev")
    init_ctx(Settings.from_sources())
    if get_ctx().config.subnet_id != args.subnet:
        parser.error("The requested subnet must match the public CLI's active configuration")
    source = (ROOT / ".adaos" / (f"dev/{args.subnet}" if args.space == "dev" else "workspace")).resolve()
    if ROOT not in source.parents or not source.is_dir():
        parser.error("Source must be an existing local development/workspace directory")
    projects = sorted(path.parent.name for path in (source / "projects").glob("*/project.yaml"))
    if args.space == "workspace":
        published = subprocess.check_output(
            ["git", "-C", str(source), "ls-tree", "-d", "--name-only", "origin/main:projects"],
            text=True, encoding="utf-8",
        ).splitlines()
        projects = [project for project in projects if project in published]
    if args.project:
        missing = set(args.project) - set(projects)
        if missing:
            parser.error(f"Projects absent from the eligible source set: {sorted(missing)}")
        projects = [project for project in projects if project in args.project]
    projects = [project for project in projects if project not in args.exclude]
    output = args.output.resolve()
    if ROOT / "e2e" / "artifacts" not in output.parents:
        parser.error("Receipts must stay below e2e/artifacts")
    manifest = {"space": args.space, "subnet": args.subnet, "source": str(source),
                "publish": args.publish, "projects": projects}
    if args.source_checkpoint:
        manifest.update(mode="source_checkpoint", change_id=args.change_id)
    if args.resume:
        retained = json.loads((output / "batch.json").read_text(encoding="utf-8"))
        if retained != manifest:
            parser.error("Resume must use the same scope, projects and publication mode")
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "batch.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "ADAOS_CONTROL_URL": "http://127.0.0.1:8778"}
    failed = 0
    for index, project in enumerate(projects, 1):
        receipt_path = output / f"{project}.json"
        if receipt_path.exists() and json.loads(receipt_path.read_text(encoding="utf-8")).get("passed"):
            print(f"[{index}/{len(projects)}] {project}: already checkpointed", flush=True)
            continue
        attempt = len(list(output.glob(f"{project}.attempt-*.stdout.txt"))) + 1
        prefix = output / f"{project}.attempt-{attempt:02}"
        command = [sys.executable, "-c", "from adaos.apps.cli.app import app; app()"]
        command += (["dev"] if args.space == "dev" else []) + ["project", "checkpoint" if args.source_checkpoint else "push", project, "--json"]
        if args.source_checkpoint:
            command += ["--change-id", args.change_id]
        if not args.publish:
            command.append("--local-only")
        started = time.monotonic()
        with Path(f"{prefix}.stdout.txt").open("x", encoding="utf-8") as stdout, Path(f"{prefix}.stderr.txt").open("x", encoding="utf-8") as stderr:
            process = subprocess.run(command, cwd=ROOT, env=env, stdout=stdout, stderr=stderr, check=False)
        try:
            payload = json.loads(Path(f"{prefix}.stdout.txt").read_text(encoding="utf-8"))
        except ValueError:
            payload = {}
        passed = process.returncode == 0 and payload.get("project_id") == project
        if args.source_checkpoint:
            components = payload.get("components") or []
            passed = passed and payload.get("ok") is True and bool(components) and all(item.get("ok") and item.get("commit") for item in components)
        else:
            passed = passed and bool(payload.get("release_digest"))
            if args.publish:
                passed = passed and payload.get("publication", {}).get("published") is True
        receipt = {"project_id": project, "passed": passed, "exit_code": process.returncode,
                   "attempt": attempt, "duration_s": round(time.monotonic() - started, 3),
                   "completed_at": datetime.now(timezone.utc).isoformat(), "command": command,
                   "payload": payload, "logs": [f"{prefix.name}.stdout.txt", f"{prefix.name}.stderr.txt"]}
        Path(f"{prefix}.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[{index}/{len(projects)}] {project}: {'passed' if passed else 'FAILED'} ({receipt['duration_s']}s)", flush=True)
        failed += not passed
    print(json.dumps({"projects": len(projects), "failed": failed, "output": str(output)}, ensure_ascii=False))
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
