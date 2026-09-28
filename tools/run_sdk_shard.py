#!/usr/bin/env python3
"""Run one deterministic, balanced shard of the complete SDK test suite."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def discover_sdk_tests(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "tests").rglob("test_*.py"))


def partition_tests(paths: list[Path], total: int) -> list[list[Path]]:
    if total < 1:
        raise ValueError("total must be at least one")
    buckets: list[list[Path]] = [[] for _ in range(total)]
    weights = [0] * total
    weighted_paths = sorted(paths, key=lambda path: (-path.stat().st_size, path.as_posix()))
    for path in weighted_paths:
        bucket = min(range(total), key=lambda candidate: (weights[candidate], candidate))
        buckets[bucket].append(path)
        weights[bucket] += path.stat().st_size
    return [sorted(bucket) for bucket in buckets]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, required=True, help="Zero-based shard index.")
    parser.add_argument("--total", type=int, required=True, help="Total number of shards.")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def pytest_args_with_basetemp(args: list[str], base_dir: Path) -> list[str]:
    if any(arg == "--basetemp" or arg.startswith("--basetemp=") for arg in args):
        return list(args)
    return [*args, f"--basetemp={base_dir / 'pytest'}"]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.total < 1 or not 0 <= args.index < args.total:
        raise SystemExit("--index must be in the range [0, --total)")

    repo_root = Path(__file__).resolve().parents[1]
    buckets = partition_tests(discover_sdk_tests(repo_root), args.total)
    selected = buckets[args.index]
    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]

    env = os.environ.copy()
    test_root = str(repo_root / "tests")
    existing_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = (
        f"{test_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else test_root
    )
    env.setdefault("PYTHONNOUSERSITE", "1")
    env.setdefault("ADAOS_TESTING", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")

    weight = sum(path.stat().st_size for path in selected)
    print(
        f"[sdk-shard {args.index + 1}/{args.total}] files={len(selected)} weight={weight}",
        flush=True,
    )
    def run(base_dir: Path) -> int:
        command = [
            sys.executable,
            "-m",
            "pytest",
            *(str(path) for path in selected),
            *pytest_args_with_basetemp(pytest_args, base_dir),
        ]
        return subprocess.call(command, cwd=repo_root, env=env)

    configured_base_dir = str(env.get("ADAOS_BASE_DIR") or "").strip()
    if configured_base_dir:
        return run(Path(configured_base_dir))

    with tempfile.TemporaryDirectory(prefix=f"adaos_sdk_shard_{args.index:02d}_") as base_dir:
        env["ADAOS_BASE_DIR"] = base_dir
        return run(Path(base_dir))


if __name__ == "__main__":
    raise SystemExit(main())
