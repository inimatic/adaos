from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class ShardFile:
    path: Path
    weight: int


def _test_count(path: Path) -> int:
    """Estimate module cost without importing test code."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return 1
    count = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    )
    return max(1, count)


def discover_test_files(tests_root: Path) -> list[ShardFile]:
    root = tests_root.resolve()
    candidates = {
        path.resolve()
        for pattern in ("test_*.py", "*_test.py")
        for path in root.rglob(pattern)
        if path.is_file() and "__pycache__" not in path.parts
    }
    return [ShardFile(path=path, weight=_test_count(path)) for path in sorted(candidates)]


def plan_test_shards(test_files: Sequence[ShardFile], shard_count: int) -> list[list[ShardFile]]:
    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    shards: list[list[ShardFile]] = [[] for _ in range(shard_count)]
    weights = [0] * shard_count
    for test_file in sorted(test_files, key=lambda item: (-item.weight, item.path.as_posix())):
        shard_index = min(range(shard_count), key=lambda index: (weights[index], index))
        shards[shard_index].append(test_file)
        weights[shard_index] += test_file.weight
    for shard in shards:
        shard.sort(key=lambda item: item.path.as_posix())
    return shards


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one deterministic, test-count-balanced SDK test shard.")
    parser.add_argument("--tests-root", type=Path, default=Path("tests"))
    parser.add_argument("--shard-index", type=int, required=True, help="Zero-based shard index.")
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--junitxml", type=Path)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.shard_count < 1:
        raise SystemExit("--shard-count must be positive")
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("--shard-index must be between 0 and shard-count - 1")

    tests_root = args.tests_root.resolve()
    test_files = discover_test_files(tests_root)
    shard = plan_test_shards(test_files, args.shard_count)[args.shard_index]
    if not shard:
        print(f"SDK shard {args.shard_index + 1}/{args.shard_count}: no test files")
        return 0

    selected_weight = sum(item.weight for item in shard)
    total_weight = sum(item.weight for item in test_files)
    print(
        f"SDK shard {args.shard_index + 1}/{args.shard_count}: "
        f"files={len(shard)}/{len(test_files)} estimated_tests={selected_weight}/{total_weight}",
        flush=True,
    )

    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    command = [sys.executable, "-m", "pytest", *[str(item.path) for item in shard]]
    if args.junitxml is not None:
        junit_path = args.junitxml.resolve()
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        command.append(f"--junitxml={junit_path}")
    command.extend(pytest_args)

    environment = dict(os.environ)
    environment.setdefault("ADAOS_SANDBOX_DISABLED", "1")
    with tempfile.TemporaryDirectory(prefix=f"adaos-ci-sdk-{args.shard_index}-") as base_dir:
        environment.setdefault("ADAOS_BASE_DIR", base_dir)
        return subprocess.call(command, cwd=str(tests_root.parent), env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
