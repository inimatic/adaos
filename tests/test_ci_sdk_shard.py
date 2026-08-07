from __future__ import annotations

from pathlib import Path

from tools.ci_sdk_shard import ShardFile, discover_test_files, plan_test_shards


def test_discover_test_files_supports_pytest_module_patterns(tmp_path: Path) -> None:
    (tmp_path / "test_alpha.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
    (tmp_path / "beta_test.py").write_text(
        "async def test_two():\n    pass\n\ndef helper():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "helper.py").write_text("def test_not_discovered():\n    pass\n", encoding="utf-8")

    discovered = discover_test_files(tmp_path)

    assert [item.path.name for item in discovered] == ["beta_test.py", "test_alpha.py"]
    assert [item.weight for item in discovered] == [1, 1]


def test_plan_test_shards_is_complete_unique_and_deterministic(tmp_path: Path) -> None:
    test_files = [
        ShardFile(tmp_path / f"test_{index}.py", weight=weight)
        for index, weight in enumerate([11, 9, 7, 5, 3, 2, 1])
    ]

    first = plan_test_shards(test_files, 3)
    second = plan_test_shards(list(reversed(test_files)), 3)
    flattened = [item.path for shard in first for item in shard]

    assert first == second
    assert sorted(flattened) == sorted(item.path for item in test_files)
    assert len(flattened) == len(set(flattened))


def test_plan_test_shards_balances_estimated_test_count(tmp_path: Path) -> None:
    test_files = [
        ShardFile(tmp_path / f"test_{index}.py", weight=weight)
        for index, weight in enumerate([13, 8, 8, 7, 6, 5, 4, 3, 2, 1])
    ]

    shards = plan_test_shards(test_files, 4)
    weights = [sum(item.weight for item in shard) for shard in shards]

    assert max(weights) - min(weights) <= max(item.weight for item in test_files)
