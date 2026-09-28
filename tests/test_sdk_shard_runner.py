from __future__ import annotations

from pathlib import Path

from tools.run_sdk_shard import partition_tests, pytest_args_with_basetemp


def test_partition_tests_is_complete_deterministic_and_balanced(tmp_path: Path) -> None:
    paths: list[Path] = []
    for index, size in enumerate((100, 90, 80, 70, 60, 50, 40, 30)):
        path = tmp_path / f"test_{index}.py"
        path.write_bytes(b"x" * size)
        paths.append(path)

    first = partition_tests(paths, 3)
    second = partition_tests(list(reversed(paths)), 3)

    assert first == second
    assert sorted(path for bucket in first for path in bucket) == sorted(paths)
    weights = [sum(path.stat().st_size for path in bucket) for bucket in first]
    assert max(weights) - min(weights) <= max(path.stat().st_size for path in paths)


def test_partition_tests_rejects_empty_shard_count() -> None:
    try:
        partition_tests([], 0)
    except ValueError as exc:
        assert str(exc) == "total must be at least one"
    else:
        raise AssertionError("partition_tests accepted an empty shard count")


def test_pytest_args_use_shard_owned_basetemp(tmp_path: Path) -> None:
    assert pytest_args_with_basetemp(["-q"], tmp_path) == [
        "-q",
        f"--basetemp={tmp_path / 'pytest'}",
    ]
    explicit = ["--basetemp=custom"]
    assert pytest_args_with_basetemp(explicit, tmp_path) == explicit
