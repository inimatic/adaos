from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from adaos.apps.cli.commands import tests as tests_cli


def test_prune_duplicate_skill_tests_preserves_sdk_suite(tmp_path: Path) -> None:
    sdk_tests = tmp_path / "tests"
    sdk_tests.mkdir()

    assert tests_cli._prune_duplicate_skill_tests([str(sdk_tests)]) == [str(sdk_tests)]


def test_collect_test_dirs_excludes_installed_and_vendored_suites(tmp_path: Path) -> None:
    own_tests = tmp_path / "weather_skill" / "tests"
    nested_tests = tmp_path / "media_skill" / "runtime" / "tests" / "integration"
    vendored_tests = tmp_path / "media_skill" / "vendor" / "numpy" / "tests"
    installed_tests = tmp_path / ".runtime" / "media_skill" / "vendor" / "numpy" / "tests"
    for test_dir in (own_tests, nested_tests, vendored_tests, installed_tests):
        test_dir.mkdir(parents=True)
        (test_dir / "test_example.py").write_text("def test_example(): pass\n", encoding="utf-8")

    assert tests_cli._collect_test_dirs(tmp_path) == [str(own_tests)]


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


def test_run_one_group_isolates_skill_import_paths(monkeypatch, tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    skill_root = checkout / "skills" / "weather_skill"
    suite = skill_root / "tests"
    suite.mkdir(parents=True)
    (skill_root / "skill.yaml").write_text("name: weather_skill\n", encoding="utf-8")
    vendor = checkout / "skills" / ".runtime" / "weather_skill" / "v2" / "vendor"
    vendor.mkdir(parents=True)
    (vendor.parents[1] / "current_runtime.json").write_text(
        '{"runtime_bucket": "v2"}\n', encoding="utf-8"
    )
    captured = {}

    def _run(command, *, cwd, extra_env, use_sandbox):
        captured.update(command=command, extra_env=extra_env)
        return 0, "passed", ""

    monkeypatch.setattr(tests_cli, "_sandbox_run", _run)
    ctx = SimpleNamespace(
        paths=SimpleNamespace(repo_root=lambda: checkout),
        settings=SimpleNamespace(lang="en", profile="test"),
    )

    tests_cli._run_one_group(
        ctx=ctx,
        base_dir=tmp_path / "state",
        venv_python=sys.executable,
        paths=[str(suite)],
        py_exec=sys.executable,
        py_prefix=[],
        use_sandbox=False,
    )

    python_path = captured["extra_env"]["PYTHONPATH"].split(tests_cli.os.pathsep)
    assert python_path == [str(suite), str(skill_root), str(checkout), str(vendor)]
    assert captured["extra_env"]["ADAOS_SKILL_NAME"] == "weather_skill"
    assert captured["extra_env"]["ADAOS_SKILL_PACKAGE"] == "skills.weather_skill"
    assert captured["extra_env"]["ADAOS_DEV_SKILL_DIR"] == str(skill_root)
    assert "--import-mode=importlib" in captured["command"]


def test_pytest_addopts_preserves_k_expression() -> None:
    expression = "scenario_runtime or runtime_bindings"

    joined = tests_cli.subprocess.list2cmdline(["-q", "-k", expression])

    assert joined == '-q -k "scenario_runtime or runtime_bindings"'


def test_run_one_group_uses_argfile_for_large_test_suite(monkeypatch, tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    state = tmp_path / "state"
    paths = [str(checkout / f"test_{index}.py") for index in range(3)]
    captured = {}

    def _run(command, *, cwd, extra_env, use_sandbox):
        args_file = Path(command[-1][1:])
        captured.update(command=command, paths=args_file.read_text(encoding="utf-8").splitlines())
        return 0, "passed", ""

    monkeypatch.setattr(tests_cli, "_PYTEST_COMMAND_LINE_SAFE_CHARS", 1)
    monkeypatch.setattr(tests_cli, "_sandbox_run", _run)
    ctx = SimpleNamespace(
        paths=SimpleNamespace(repo_root=lambda: checkout),
        settings=SimpleNamespace(lang="en", profile="test"),
    )

    result = tests_cli._run_one_group(
        ctx=ctx,
        base_dir=state,
        venv_python=sys.executable,
        paths=paths,
        py_exec=sys.executable,
        py_prefix=[],
        use_sandbox=False,
    )

    assert result == (0, "passed", "")
    assert captured["command"][-1].startswith("@")
    assert captured["paths"] == paths
    assert not Path(captured["command"][-1][1:]).exists()


def test_junit_shards_are_rewritten_and_merged(tmp_path: Path) -> None:
    target = tmp_path / "reports" / "skills.xml"
    first = tmp_path / "reports" / "skills-001.xml"
    second = tmp_path / "reports" / "skills-002.xml"
    first.parent.mkdir()
    first.write_text(
        '<testsuites><testsuite name="first" tests="2" errors="0" failures="1" skipped="0" time="1.25" /></testsuites>',
        encoding="utf-8",
    )
    second.write_text(
        '<testsuites><testsuite name="second" tests="3" errors="1" failures="0" skipped="1" time="2.5" /></testsuites>',
        encoding="utf-8",
    )

    rewritten = tests_cli._replace_junit_target(["-q", "--junitxml=reports/original.xml"], first)
    assert tests_cli._junit_target(rewritten) == first.as_posix()
    assert tests_cli._merge_junit_reports([first, second], target) == 2

    root = tests_cli.ET.parse(target).getroot()
    assert root.attrib == {
        "name": "pytest tests",
        "tests": "5",
        "errors": "1",
        "failures": "1",
        "skipped": "1",
        "time": "3.750",
    }
    assert [suite.get("name") for suite in root.findall("testsuite")] == ["first", "second"]
    assert not first.exists()
    assert not second.exists()
