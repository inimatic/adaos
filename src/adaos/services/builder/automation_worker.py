"""Durable process entry point for one persisted Builder Automation session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from adaos.apps.bootstrap import init_ctx
from adaos.services.agent_context import get_ctx
from adaos.services.builder.automation import BuilderAutomationService, _now_iso
from adaos.services.settings import Settings


def run(session_id: str) -> int:
    init_ctx(Settings.from_sources())
    ctx = get_ctx()
    service = BuilderAutomationService.from_context(background=False)
    token = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in str(session_id or "")
    ).strip("._") or "automation"
    worker_root = Path(ctx.paths.state_dir()) / "builder" / "automation_workers" / token
    result_path = worker_root / "result.json"
    try:
        service._run_worker(str(session_id))
        session = service._find_session_by_id(str(session_id))
        payload = {
            "schema": "adaos.builder.automation_worker_result.v1",
            "session_id": str(session_id),
            "status": str((session or {}).get("status") or "finished"),
            "task_id": (session or {}).get("current_task_id"),
            "finished_at": _now_iso(),
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0 if payload["status"] == "completed" else 1
    except Exception as exc:
        payload = {
            "schema": "adaos.builder.automation_worker_result.v1",
            "session_id": str(session_id),
            "status": "worker_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "finished_at": _now_iso(),
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one persisted Builder Automation worker.")
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run(str(args.session_id))


if __name__ == "__main__":
    raise SystemExit(main())
