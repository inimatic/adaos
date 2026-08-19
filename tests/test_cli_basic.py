# tests/test_cli_basic.py
import json

from typer.testing import CliRunner


def test_cli_help(cli_app):
    r = CliRunner().invoke(cli_app, ["--help"])
    assert r.exit_code == 0
    assert "Usage" in r.stdout or "использование" in r.stdout.lower()


def test_repo_registry_list_json(cli_app, tmp_base_dir):
    workspace = tmp_base_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-03-06T00:00:00+00:00",
                "skills": [{"kind": "skill", "name": "weather_skill", "version": "1.0.0"}],
                "scenarios": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli_app, ["repo", "registry", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["items"][0]["name"] == "weather_skill"


def test_reliability_summary_exposes_media_relay_operations(monkeypatch):
    from adaos.apps.cli.commands import node as node_cmd

    lines: list[str] = []
    monkeypatch.setattr(node_cmd.typer, "echo", lambda value="": lines.append(str(value)))

    node_cmd._print_reliability_summary(
        {
            "node": {"node_id": "node-1", "role": "hub", "ready": True, "node_state": "ready"},
            "runtime": {
                "hub_root_protocol": {
                    "route_runtime": {
                        "media_io_workers": 4,
                        "media_io_active": 1,
                        "media_io_active_by_operation": {"read": 1},
                        "media_io_oldest_active_operation": "read",
                        "media_io_oldest_active_age_s": 12.5,
                        "media_io_slow_total": 2,
                        "media_io_slow_by_operation": {"resolve_reference": 1, "read": 1},
                        "media_io_max_ms_by_operation": {"resolve_reference": 10791.0, "read": 79000.0},
                        "last_media_io_operation": "read",
                        "last_media_io_ms": 79000.0,
                        "last_media_source_kind": "unc",
                        "last_media_path_digest": "digest",
                        "last_media_key_tag": "request",
                    }
                }
            },
        }
    )

    media_line = next(line for line in lines if line.startswith("protocol.media_relay:"))
    assert "workers=1/4" in media_line
    assert "active=read=1" in media_line
    assert "oldest=read:12.5s" in media_line
    assert "slow=2[read=1,resolve_reference=1]" in media_line
    assert "read=79000.0" in media_line
