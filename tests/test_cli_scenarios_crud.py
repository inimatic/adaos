import json
from pathlib import Path

from typer.testing import CliRunner

from adaos.adapters.db import SqliteScenarioRegistry
from adaos.services.agent_context import get_ctx


def _scenario_names(cli_app) -> set[str]:
    result = CliRunner().invoke(cli_app, ["scenario", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout or "{}")
    return {item["name"] for item in payload.get("scenarios", [])}


def test_scenario_create_command(cli_app, tmp_base_dir, tmp_path):
    runner = CliRunner()
    scenario_id = "demo_scenario_cli"

    result = runner.invoke(cli_app, ["scenario", "create", scenario_id, "--template", "template"])
    assert result.exit_code == 0

    scenario_dir = Path(tmp_base_dir) / "scenarios" / scenario_id
    assert scenario_dir.exists()

    manifest = scenario_dir / "scenario.yaml"
    assert manifest.exists()
    assert f"id: {scenario_id}" in manifest.read_text(encoding="utf-8")

    registry = SqliteScenarioRegistry(get_ctx().sql)
    assert registry.get(scenario_id) is not None

    names = _scenario_names(cli_app)
    assert scenario_id in names
