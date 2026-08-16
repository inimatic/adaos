import json
from types import SimpleNamespace

import pytest
import typer

from adaos.apps.cli.commands import setup as setup_cmd
from adaos.services.setup.presets import get_preset


def test_default_preset_includes_infrastate_skill() -> None:
    preset = get_preset("default")
    assert "infrastate_skill" in preset.skills


def test_workspace_only_update_skips_runtime_refresh_and_yjs_sync(monkeypatch, capsys) -> None:
    monkeypatch.setattr(setup_cmd, "get_ctx", lambda: SimpleNamespace())
    monkeypatch.setattr(setup_cmd, "_scenario_mgr", lambda: SimpleNamespace())
    monkeypatch.setattr(setup_cmd, "_skill_mgr", lambda: SimpleNamespace())
    monkeypatch.setattr(
        setup_cmd,
        "SqliteSkillRegistry",
        lambda *_args, **_kwargs: pytest.fail("workspace-only update read skill runtime registry"),
    )
    monkeypatch.setattr(
        setup_cmd,
        "SqliteScenarioRegistry",
        lambda *_args, **_kwargs: pytest.fail("workspace-only update started Yjs sync"),
    )

    with pytest.raises(typer.Exit) as exc_info:
        setup_cmd.update(
            pull=False,
            sync_yjs=True,
            workspace_only=True,
            migrate_runtime=True,
            webspace_id="desktop",
            json_output=True,
        )

    assert exc_info.value.exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["runtime_refresh_skipped"] is True
    assert payload["runtime_updated"] == []
    assert payload["yjs_sync_skipped"] is True
    assert payload["yjs_synced"] == []
