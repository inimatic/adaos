"""Detached worker used by the restart-reconcilable local executor."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _kill_tree(process: subprocess.Popen[Any]) -> None:
    try:
        root = psutil.Process(process.pid)
        children = root.children(recursive=True)
    except Exception:
        children = []
        root = None
    for child in reversed(children):
        try:
            child.kill()
        except Exception:
            pass
    if root is not None:
        try:
            root.kill()
        except Exception:
            pass
    else:
        try:
            process.kill()
        except Exception:
            pass


def run(attempt_dir: Path) -> int:
    spec_path = attempt_dir / "spec.json"
    receipt_path = attempt_dir / "receipt.json"
    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"
    spec_record = json.loads(spec_path.read_text(encoding="utf-8"))
    spec = dict(spec_record.get("spec") or {})
    command = [str(item) for item in spec.get("command") or []]
    cwd = str(spec.get("working_directory") or "")
    resources = dict(spec.get("resources") or {})
    timeout = resources.get("wall_time_s")
    timeout_s = float(timeout) if timeout is not None else None
    environment = dict(os.environ)
    environment.update({str(key): str(value) for key, value in dict(spec.get("environment") or {}).items()})
    started_at = _now()

    receipt: dict[str, Any]
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
                start_new_session=(os.name != "nt"),
            )
        except Exception as exc:
            receipt = {
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "exit_code": None,
                "failure": {"reason": "spawn_failed", "type": type(exc).__name__, "message": str(exc)},
            }
            _atomic_json(receipt_path, receipt)
            return 2

        try:
            exit_code = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            try:
                process.wait(timeout=5.0)
            except Exception:
                pass
            receipt = {
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "exit_code": process.returncode,
                "failure": {"reason": "wall_time_exceeded", "timeout_s": timeout_s},
            }
        else:
            receipt = {
                "status": "succeeded" if exit_code == 0 else "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "exit_code": int(exit_code),
                "failure": None if exit_code == 0 else {"reason": "nonzero_exit", "exit_code": int(exit_code)},
            }
    _atomic_json(receipt_path, receipt)
    return 0 if receipt["status"] == "succeeded" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-dir", required=True)
    args = parser.parse_args()
    return run(Path(args.attempt_dir).expanduser().resolve())


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    raise SystemExit(main())
