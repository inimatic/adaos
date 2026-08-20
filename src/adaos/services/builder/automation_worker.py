"""Durable process entry point for one persisted Builder Automation session."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Callable, Sequence

from adaos.apps.bootstrap import init_ctx
from adaos.services.agent_context import get_ctx
from adaos.services.builder.automation import BuilderAutomationService, _now_iso
from adaos.services.settings import Settings


_QUEUE_STATUSES = {"starting", "queued"}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "expired"}


def _run_until_settled(
    service: BuilderAutomationService,
    session_id: str,
    *,
    poll_interval_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Keep a per-session worker alive while its submitted task is queued.

    A local dev node admits one task at a time.  A second durable worker used
    to call ``run_once`` and exit immediately when that node was occupied,
    leaving its own task queued without an executor.  Retrying only the exact
    session task preserves FIFO-independent targeting while avoiding a custom
    queue or a second execution path.
    """

    while True:
        session = service._find_session_by_id(str(session_id))
        status = str((session or {}).get("status") or "").strip().lower()
        if status in _TERMINAL_STATUSES:
            return dict(session or {})
        if status in _QUEUE_STATUSES:
            service._run_worker(str(session_id))
            session = service._find_session_by_id(str(session_id))
            status = str((session or {}).get("status") or "").strip().lower()
            if status in _TERMINAL_STATUSES:
                return dict(session or {})
        if not session:
            raise RuntimeError(f"automation session not found: {session_id}")
        sleep(max(0.05, float(poll_interval_seconds)))


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
    ready_path = worker_root / "ready.json"
    worker_root.mkdir(parents=True, exist_ok=True)
    ready_path.write_text(
        json.dumps(
            {
                "schema": "adaos.builder.automation_worker_ready.v1",
                "session_id": str(session_id),
                "status": "ready",
                "pid": os.getpid(),
                "ready_at": _now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        session = _run_until_settled(service, str(session_id))
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
