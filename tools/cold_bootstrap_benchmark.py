"""Measure process-cold AdaOS API bootstrap stages across repeated runs.

Each iteration starts an isolated foreground ``adaos dev serve`` process on a
dedicated port, waits for Uvicorn readiness, records the structured startup
stage timings, and terminates the complete child process tree.  The report is
intended for release evidence rather than as a micro-benchmark.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import psutil


_STAGE_DONE = re.compile(
    r"startup stage (?:done|slow) stage=(?P<stage>[A-Za-z0-9_.:-]+) "
    r"duration_s=(?P<duration>[0-9.]+)"
)
_READY_MARKER = "Application startup complete"
_FIRST_STAGE_MARKER = "startup stage start stage="


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 3)


def _terminate_tree(process: subprocess.Popen[Any]) -> None:
    try:
        parent = psutil.Process(process.pid)
    except psutil.Error:
        return
    children = parent.children(recursive=True)
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        parent.terminate()
    except psutil.Error:
        pass
    _gone, alive = psutil.wait_procs([*children, parent], timeout=5.0)
    for item in alive:
        try:
            item.kill()
        except psutil.Error:
            pass


def _run_once(
    *,
    repo_root: Path,
    python: Path,
    port: int,
    timeout_s: float,
    log_path: Path,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["ADAOS_STARTUP_STAGE_LOGS"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    command = [
        str(python),
        "-m",
        "adaos",
        "dev",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    started = time.perf_counter()
    ready = False
    ready_elapsed_s: float | None = None
    first_stage_elapsed_s: float | None = None
    timed_out = False
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
        )
        try:
            while time.perf_counter() - started < timeout_s:
                if process.poll() is not None:
                    break
                log_handle.flush()
                try:
                    current = log_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    current = ""
                if first_stage_elapsed_s is None and _FIRST_STAGE_MARKER in current:
                    first_stage_elapsed_s = time.perf_counter() - started
                if _READY_MARKER in current:
                    ready = True
                    ready_elapsed_s = time.perf_counter() - started
                    break
                time.sleep(0.2)
            else:
                timed_out = True
        finally:
            _terminate_tree(process)
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
    elapsed_s = ready_elapsed_s or (time.perf_counter() - started)
    content = log_path.read_text(encoding="utf-8", errors="replace")
    stages: dict[str, float] = {}
    stage_occurrences: dict[str, int] = defaultdict(int)
    for match in _STAGE_DONE.finditer(content):
        stage = match.group("stage")
        duration = float(match.group("duration"))
        stages[stage] = max(duration, stages.get(stage, 0.0))
        stage_occurrences[stage] += 1
    return {
        "port": port,
        "ready": ready,
        "timed_out": timed_out,
        "exit_code": process.returncode,
        "ready_seconds": round(elapsed_s, 3),
        "process_to_first_stage_seconds": (
            round(first_stage_elapsed_s, 3)
            if first_stage_elapsed_s is not None
            else None
        ),
        "stages_seconds": stages,
        "stage_occurrences": dict(stage_occurrences),
        "log": str(log_path),
    }


def run_benchmark(
    *,
    repo_root: Path,
    python: Path,
    runs: int,
    base_port: int,
    timeout_s: float,
    logs_dir: Path,
) -> dict[str, Any]:
    iterations = [
        _run_once(
            repo_root=repo_root,
            python=python,
            port=base_port + index,
            timeout_s=timeout_s,
            log_path=logs_dir / f"run-{index + 1}.log",
        )
        for index in range(runs)
    ]
    ready_values = [
        float(item["ready_seconds"]) for item in iterations if item["ready"]
    ]
    pre_stage_values = [
        float(item["process_to_first_stage_seconds"])
        for item in iterations
        if item["ready"] and item.get("process_to_first_stage_seconds") is not None
    ]
    stage_values: dict[str, list[float]] = defaultdict(list)
    for item in iterations:
        if not item["ready"]:
            continue
        for stage, duration in item["stages_seconds"].items():
            stage_values[stage].append(float(duration))
    stages = {
        stage: {
            "samples": len(values),
            "p50_seconds": _percentile(values, 0.50),
            "p95_seconds": _percentile(values, 0.95),
            "min_seconds": round(min(values), 3),
            "max_seconds": round(max(values), 3),
        }
        for stage, values in sorted(stage_values.items())
    }
    return {
        "schema": "adaos.benchmark.cold_bootstrap.v1",
        "runs_requested": runs,
        "runs_ready": len(ready_values),
        "process_ready": {
            "p50_seconds": _percentile(ready_values, 0.50),
            "p95_seconds": _percentile(ready_values, 0.95),
            "min_seconds": round(min(ready_values), 3) if ready_values else None,
            "max_seconds": round(max(ready_values), 3) if ready_values else None,
        },
        "process_to_first_stage": {
            "p50_seconds": _percentile(pre_stage_values, 0.50),
            "p95_seconds": _percentile(pre_stage_values, 0.95),
            "min_seconds": round(min(pre_stage_values), 3) if pre_stage_values else None,
            "max_seconds": round(max(pre_stage_values), 3) if pre_stage_values else None,
        },
        "stages": stages,
        "iterations": iterations,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--base-port", type=int, default=8788)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--logs-dir", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    python = (args.python or Path(sys.executable)).resolve()
    logs_dir = (
        args.logs_dir
        or repo_root / "e2e" / "artifacts" / "cold-bootstrap" / "logs"
    ).resolve()
    report = run_benchmark(
        repo_root=repo_root,
        python=python,
        runs=max(1, int(args.runs)),
        base_port=int(args.base_port),
        timeout_s=max(5.0, float(args.timeout_s)),
        logs_dir=logs_dir,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    output = args.output
    if output:
        output = output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["runs_ready"] == report["runs_requested"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
