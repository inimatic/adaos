import json
from types import SimpleNamespace

import pytest
import typer

from adaos.apps.cli.commands import setup as setup_cmd
from adaos.services.setup.presets import get_preset


def test_default_preset_installs_default_projects() -> None:
    preset = get_preset("default")
    assert preset.projects == ("web_desktop", "default_app_bundle")
    assert "web_desktop" in preset.scenarios


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


def test_live_runtime_activation_notification_reports_reload_failure(monkeypatch) -> None:
    monkeypatch.setattr(setup_cmd, "resolve_control_base_url", lambda **_kwargs: "http://127.0.0.1:8777")
    monkeypatch.setattr(setup_cmd, "resolve_control_token", lambda **_kwargs: "token")
    monkeypatch.setattr(setup_cmd, "probe_control_api", lambda **_kwargs: (200, {"ok": True}))

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"ok": True, "handler_reload": {"ok": False, "reason": "reload_failed"}}

    monkeypatch.setattr(setup_cmd.requests, "post", lambda *_args, **_kwargs: _Response())

    result = setup_cmd._notify_live_skill_runtime_activated("weather_skill", webspace_id="desktop")

    assert result["ok"] is False
    assert result["restart_required"] is True
    assert result["response"]["handler_reload"]["reason"] == "reload_failed"


def test_live_runtime_activation_notification_requires_restart_on_owner_rejection(monkeypatch) -> None:
    monkeypatch.setattr(setup_cmd, "resolve_control_base_url", lambda **_kwargs: "http://127.0.0.1:8777")
    monkeypatch.setattr(setup_cmd, "resolve_control_token", lambda **_kwargs: "token")
    monkeypatch.setattr(setup_cmd, "probe_control_api", lambda **_kwargs: (200, {"ok": True}))

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"ok": False, "reason": "bus-unavailable"}

    monkeypatch.setattr(setup_cmd.requests, "post", lambda *_args, **_kwargs: _Response())

    result = setup_cmd._notify_live_skill_runtime_activated("weather_skill", webspace_id="desktop")

    assert result["ok"] is False
    assert result["restart_required"] is True
    assert result["response"]["reason"] == "bus-unavailable"
