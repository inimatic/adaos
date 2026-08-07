from __future__ import annotations

import sys
from types import SimpleNamespace

import yaml
from typer.testing import CliRunner

from adaos.apps.cli.commands import scenario as scenario_cli


def test_cli_validate_resolves_declared_companion_skill(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    skill = workspace / "skills" / "dashboard_skill"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "dashboard_skill",
                "version": "0.1.0",
                "entry": "handlers/main.py",
                "exports": {"tools": ["load"]},
                "tools": [{"name": "load", "entry": "handlers.main:load"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    scenario = workspace / "scenarios" / "dashboard"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "dashboard",
                "version": "0.1.0",
                "depends": ["dashboard_skill"],
                "steps": [{"name": "load", "call": "dashboard_skill.load"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        scenario_cli,
        "get_ctx",
        lambda: SimpleNamespace(
            paths=SimpleNamespace(scenarios_workspace_dir=lambda: workspace / "scenarios")
        ),
    )

    result = CliRunner().invoke(scenario_cli.app, ["validate", "dashboard", "--json"])

    assert result.exit_code == 0, result.output
    assert '"ok": true' in result.output
    assert '"errors": []' in result.output


def test_cli_test_runs_packaged_scenario_tests(monkeypatch, tmp_path) -> None:
    tests_dir = tmp_path / "workspace" / "scenarios" / "dashboard" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_dashboard.py").write_text("def test_dashboard():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(
        scenario_cli,
        "get_ctx",
        lambda: SimpleNamespace(
            paths=SimpleNamespace(scenarios_workspace_dir=lambda: tmp_path / "workspace" / "scenarios")
        ),
    )
    calls = []

    def _run(args, text):
        calls.append((args, text))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(scenario_cli.subprocess, "run", _run)

    result = CliRunner().invoke(scenario_cli.app, ["test", "dashboard"])

    assert result.exit_code == 0, result.output
    assert calls == [([sys.executable, "-m", "pytest", "-q", str(tests_dir)], True)]
