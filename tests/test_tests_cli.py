from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from adaos.apps.cli.commands import tests as tests_cli


def test_prune_duplicate_skill_tests_preserves_sdk_suite(tmp_path: Path) -> None:
    sdk_tests = tmp_path / "tests"
    sdk_tests.mkdir()

    assert tests_cli._prune_duplicate_skill_tests([str(sdk_tests)]) == [str(sdk_tests)]


def test_run_one_group_uses_ctx_repo_root(monkeypatch, tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    suite = checkout / "tests"
    suite.mkdir(parents=True)
    captured = {}

    def _run(command, *, cwd, extra_env, use_sandbox):
        captured.update(command=command, cwd=cwd, extra_env=extra_env, use_sandbox=use_sandbox)
        return 0, "passed", ""

    monkeypatch.setattr(tests_cli, "_sandbox_run", _run)
    ctx = SimpleNamespace(
        paths=SimpleNamespace(repo_root=lambda: checkout),
        settings=SimpleNamespace(lang="en", profile="test"),
    )

    result = tests_cli._run_one_group(
        ctx=ctx,
        base_dir=tmp_path / "state",
        venv_python=sys.executable,
        paths=[str(suite)],
        py_exec=sys.executable,
        py_prefix=[],
        use_sandbox=False,
    )

    assert result == (0, "passed", "")
    assert captured["cwd"] == checkout
    assert "markers=asyncio: mark asyncio tests" not in captured["command"]


def test_pytest_addopts_preserves_k_expression() -> None:
    expression = "scenario_runtime or runtime_bindings"

    joined = tests_cli.subprocess.list2cmdline(["-q", "-k", expression])

    assert joined == '-q -k "scenario_runtime or runtime_bindings"'
