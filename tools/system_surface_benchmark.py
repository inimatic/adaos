"""Benchmark the public read-only system SDK surfaces.

The benchmark intentionally calls only ``adaos.sdk.system``. It is suitable
for local release qualification and catches regressions where a compact
Desktop section starts rebuilding an unrelated operational snapshot.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import platform
import queue
import statistics
import time
from pathlib import Path
from typing import Any, Callable

import psutil

from adaos.sdk.system import get_operational_snapshot


def _ensure_runtime_context() -> None:
    from adaos.services.agent_context import get_ctx

    try:
        get_ctx()
    except RuntimeError:
        from adaos.apps.bootstrap import init_ctx

        init_ctx()


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[rank]


def _measure(
    name: str,
    operation: Callable[[], dict[str, Any]],
    *,
    warmup: int,
    iterations: int,
) -> dict[str, Any]:
    process = psutil.Process()
    cold_start_ms: float | None = None
    for index in range(warmup):
        started = time.perf_counter()
        operation()
        if index == 0:
            cold_start_ms = (time.perf_counter() - started) * 1000.0
    rss_before = int(process.memory_info().rss)
    timings: list[float] = []
    payload_sizes: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter()
        payload = operation()
        timings.append((time.perf_counter() - started) * 1000.0)
        payload_sizes.append(
            len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        )
    rss_after = int(process.memory_info().rss)
    return {
        "name": name,
        "iterations": iterations,
        "cold_start_ms": round(cold_start_ms, 3) if cold_start_ms is not None else None,
        "latency_ms": {
            "min": round(min(timings), 3),
            "mean": round(statistics.fmean(timings), 3),
            "p50": round(_percentile(timings, 0.50), 3),
            "p95": round(_percentile(timings, 0.95), 3),
            "max": round(max(timings), 3),
        },
        "payload_bytes": {
            "min": min(payload_sizes),
            "max": max(payload_sizes),
        },
        "rss": {
            "before_bytes": rss_before,
            "after_bytes": rss_after,
            "delta_bytes": rss_after - rss_before,
        },
    }


def _measure_section_worker(
    result_queue: Any,
    section: str,
    webspace_id: str,
    warmup: int,
    iterations: int,
) -> None:
    try:
        _ensure_runtime_context()
        selected: str | set[str] = section
        if section != "all":
            selected = {"summary", section} if section != "summary" else {"summary"}
        result_queue.put(
            {
                "ok": True,
                "result": _measure(
                    section,
                    lambda: get_operational_snapshot(
                        sections=selected,
                        webspace_id=webspace_id,
                        limit=40,
                    ),
                    warmup=warmup,
                    iterations=iterations,
                ),
            }
        )
    except Exception as exc:
        result_queue.put(
            {
                "ok": False,
                "result": {
                    "name": section,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            }
        )


def run_benchmark(
    *,
    sections: list[str],
    webspace_id: str,
    warmup: int,
    iterations: int,
    section_timeout_s: float,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    context = multiprocessing.get_context("spawn")
    for section in sections:
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_measure_section_worker,
            args=(result_queue, section, webspace_id, warmup, iterations),
            daemon=True,
        )
        process.start()
        process.join(timeout=section_timeout_s)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2.0)
            results.append(
                {
                    "name": section,
                    "status": "timeout",
                    "timeout_s": section_timeout_s,
                }
            )
        else:
            try:
                message = result_queue.get(timeout=1.0)
            except queue.Empty:
                results.append(
                    {
                        "name": section,
                        "status": "error",
                        "error": f"worker exited with code {process.exitcode}",
                    }
                )
            else:
                results.append(dict(message["result"]))
        result_queue.close()
    return {
        "schema": "adaos.benchmark.system_surface.v1",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "webspace_id": webspace_id,
        "warmup": warmup,
        "results": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sections",
        nargs="+",
        default=["summary", "services", "connections", "quotas", "incidents", "update", "all"],
        choices=["summary", "services", "connections", "quotas", "incidents", "update", "all"],
    )
    parser.add_argument("--webspace-id", default="desktop")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--section-timeout-s", type=float, default=15.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-p95-ms", type=float)
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = run_benchmark(
        sections=list(dict.fromkeys(args.sections)),
        webspace_id=str(args.webspace_id),
        warmup=max(0, int(args.warmup)),
        iterations=max(1, int(args.iterations)),
        section_timeout_s=max(1.0, float(args.section_timeout_s)),
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    failed = any(item.get("status") in {"timeout", "error"} for item in report["results"])
    if args.max_p95_ms is None:
        return int(failed)
    return int(
        failed
        or any(
            float(item["latency_ms"]["p95"]) > float(args.max_p95_ms)
            for item in report["results"]
            if "latency_ms" in item
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
