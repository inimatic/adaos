import json
from types import SimpleNamespace

import pytest
import typer

from adaos.apps.cli.commands import setup as setup_cmd
from adaos.services.setup.presets import get_preset


def test_default_preset_installs_default_projects() -> None:
    preset = get_preset("default")
    assert preset.projects == ("web_desktop", "applications", "users_access")
    assert preset.scenarios == ("web_desktop", "applications", "users_access")
    assert preset.applications == (
        "web_desktop",
        "applications",
        "users_access",
        "voice",
    )
    assert "prompt_engineer_scenario" not in preset.scenarios
    assert "web_desktop_runtime_skill" in preset.skills


def test_default_project_order_keeps_management_as_home_candidate() -> None:
    chosen = get_preset("default")
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: "unused"))

    assert setup_cmd._project_ids_for_preset(ctx, chosen) == [
        "web_desktop",
        "applications",
        "users_access",
    ]


def test_default_application_order_includes_voice_provider() -> None:
    assert setup_cmd._application_ids_for_preset(get_preset("default")) == [
        "web_desktop",
        "applications",
        "users_access",
        "voice",
    ]


def test_default_application_runtime_is_authoritative(monkeypatch) -> None:
    calls: list[tuple[object, bool]] = []

    monkeypatch.setattr(
        "adaos.services.project_deployment.default_runtime.configure_default_distributed_runtimes",
        lambda ctx, *, authoritative: calls.append((ctx, authoritative)),
    )
    ctx = object()

    setup_cmd._configure_application_lifecycle_runtime(ctx)

    assert calls == [(ctx, True)]


def test_default_application_lifecycle_uses_reviewed_plan_and_apply(
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class Applications:
        @staticmethod
        def get_application(application_id, *, webspace_id):
            calls.append(("get", (application_id, webspace_id)))
            return {
                "installed": False,
                "effective_release": {"release_digest": "sha256:" + "a" * 64},
            }

        @staticmethod
        def plan_install(application_id, **kwargs):
            calls.append(("plan", (application_id, kwargs)))
            return {
                "operation_id": "appop.bootstrap",
                "plan_digest": "sha256:" + "b" * 64,
                "plan": {"conflicts": []},
            }

        @staticmethod
        def apply_operation(operation_id, **kwargs):
            calls.append(("apply", (operation_id, kwargs)))
            return {
                "status": "succeeded",
                "result": {
                    "installation": {
                        "installed_release_digest": "sha256:" + "a" * 64
                    }
                },
                "home": {"pinned": True},
            }

    import adaos.sdk

    monkeypatch.setattr(adaos.sdk, "applications", Applications)
    ctx = SimpleNamespace(
        config=SimpleNamespace(subnet_id="sn_bootstrap"),
        settings=SimpleNamespace(owner_id="local-owner"),
    )

    result = setup_cmd._install_default_application_lifecycle(
        ctx,
        application_ids=["applications"],
        webspace_id="desktop",
    )

    assert result == [
        {
            "id": "applications",
            "status": "installed",
            "release_digest": "sha256:" + "a" * 64,
            "home": {"pinned": True},
        }
    ]
    assert calls[1][0] == "plan"
    assert calls[2][0] == "apply"
    _, apply_kwargs = calls[2][1]
    assert apply_kwargs["webspace_id"] == "desktop"
    assert apply_kwargs["actor_ref"] == "user:local-owner"
    assert apply_kwargs["idempotency_key"].startswith(
        "bootstrap-default:applications:"
    )


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
