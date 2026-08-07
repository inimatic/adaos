"""Detached worker used by the restart-reconcilable local executor."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
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
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            return
        except OSError as exc:
            transient = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33}
            if not transient or attempt == 7:
                raise
            time.sleep(min(0.005 * (2**attempt), 0.1))


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


def _drain_stream(stream: Any, path: Path, limit: int, state: dict[str, Any], key: str) -> None:
    written = 0
    seen = 0
    with path.open("wb") as target:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            seen += len(chunk)
            if written < limit:
                accepted = chunk[: max(0, limit - written)]
                target.write(accepted)
                written += len(accepted)
    state[key] = {"bytes_seen": seen, "bytes_written": written, "truncated": seen > written}


def _resource_snapshot(process: psutil.Process) -> dict[str, Any]:
    processes = [process]
    try:
        processes.extend(process.children(recursive=True))
    except psutil.Error:
        pass
    rss = 0
    cpu_seconds = 0.0
    for item in processes:
        try:
            rss += int(item.memory_info().rss)
            cpu = item.cpu_times()
            cpu_seconds += float(cpu.user) + float(cpu.system)
        except psutil.Error:
            continue
    return {
        "at": _now(),
        "rss_bytes": rss,
        "cpu_seconds": round(cpu_seconds, 6),
        "process_count": len(processes),
    }


def _declared_outputs(cwd: Path, values: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    records: list[dict[str, Any]] = []
    for raw in values:
        candidate = (cwd / raw).resolve()
        try:
            candidate.relative_to(cwd)
        except ValueError:
            return [], f"declared output escaped working directory: {raw}"
        if not candidate.is_file():
            return [], f"declared output is missing: {raw}"
        data = candidate.read_bytes()
        records.append(
            {
                "path": raw.replace("\\", "/"),
                "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
                "size_bytes": len(data),
            }
        )
    return records, None


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
    budget = dict(spec.get("budget") or {})
    timeout = resources.get("wall_time_s")
    timeout_s = float(timeout) if timeout is not None else None
    memory_limit = (
        int(resources["memory_mb"]) * 1024 * 1024
        if resources.get("memory_mb") is not None
        else None
    )
    compute_limit = (
        float(budget["max_compute_seconds"])
        if budget.get("max_compute_seconds") is not None
        else None
    )
    storage_limit = (
        int(budget["max_storage_bytes"])
        if budget.get("max_storage_bytes") is not None
        else None
    )
    log_limit = int(resources.get("max_log_bytes") or 4 * 1024 * 1024)
    environment = dict(os.environ)
    environment.update({str(key): str(value) for key, value in dict(spec.get("environment") or {}).items()})
    started_at = _now()

    receipt: dict[str, Any]
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            "resource_observations": [],
            "outputs": [],
        }
        _atomic_json(receipt_path, receipt)
        return 2

    tracked = psutil.Process(process.pid)
    cpu_cores = resources.get("cpu_cores")
    if cpu_cores is not None:
        try:
            available = tracked.cpu_affinity()
            tracked.cpu_affinity(available[: max(1, min(len(available), math.ceil(float(cpu_cores))))])
        except (psutil.Error, AttributeError):
            _kill_tree(process)
            process.wait(timeout=5.0)
            receipt = {
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "exit_code": process.returncode,
                "failure": {"reason": "cpu_affinity_unavailable"},
                "resource_observations": [],
                "outputs": [],
            }
            _atomic_json(receipt_path, receipt)
            return 2

    stream_state: dict[str, Any] = {}
    stdout_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stdout, stdout_path, log_limit, stream_state, "stdout"),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stderr, stderr_path, log_limit, stream_state, "stderr"),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    observations: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    monotonic_start = time.monotonic()
    while process.poll() is None:
        snapshot = _resource_snapshot(tracked)
        observations.append(snapshot)
        observations = observations[-128:]
        _atomic_json(
            attempt_dir / "heartbeat.json",
            {"at": snapshot["at"], "resource_observations": observations},
        )
        if timeout_s is not None and time.monotonic() - monotonic_start > timeout_s:
            failure = {"reason": "wall_time_exceeded", "timeout_s": timeout_s}
        elif memory_limit is not None and int(snapshot["rss_bytes"]) > memory_limit:
            failure = {
                "reason": "memory_limit_exceeded",
                "limit_bytes": memory_limit,
                "observed_bytes": snapshot["rss_bytes"],
            }
        elif compute_limit is not None and float(snapshot["cpu_seconds"]) > compute_limit:
            failure = {
                "reason": "compute_budget_exceeded",
                "limit_seconds": compute_limit,
                "observed_seconds": snapshot["cpu_seconds"],
            }
        if failure is not None:
            _kill_tree(process)
            break
        time.sleep(0.05)
    try:
        exit_code = process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        exit_code = process.wait(timeout=5.0)
    stdout_thread.join(timeout=5.0)
    stderr_thread.join(timeout=5.0)
    outputs, output_error = _declared_outputs(
        Path(cwd).resolve(),
        [str(item) for item in spec.get("expected_outputs") or []],
    )
    if failure is None and exit_code != 0:
        failure = {"reason": "nonzero_exit", "exit_code": int(exit_code)}
    if failure is None and output_error is not None:
        failure = {"reason": "declared_output_missing", "message": output_error}
    total_storage = sum(int(item.get("bytes_written") or 0) for item in stream_state.values())
    total_storage += sum(int(item["size_bytes"]) for item in outputs)
    if failure is None and storage_limit is not None and total_storage > storage_limit:
        failure = {
            "reason": "storage_budget_exceeded",
            "limit_bytes": storage_limit,
            "observed_bytes": total_storage,
        }
    receipt = {
        "status": "succeeded" if failure is None else "failed",
        "started_at": started_at,
        "finished_at": _now(),
        "last_heartbeat_at": observations[-1]["at"] if observations else started_at,
        "exit_code": int(exit_code),
        "failure": failure,
        "resource_observations": observations,
        "log_observations": stream_state,
        "outputs": outputs if failure is None else [],
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
