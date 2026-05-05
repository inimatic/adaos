from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

from typer.testing import CliRunner


def test_request_local_member_activation_switches_role_for_non_member(monkeypatch) -> None:
    node_cli = importlib.import_module("adaos.apps.cli.commands.node")
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(node_cli, "_resolve_node_control_base_url", lambda explicit=None: "http://127.0.0.1:8777")
    monkeypatch.setattr(node_cli, "_resolved_local_control_token", lambda control, cfg: "dev-token")

    def _fake_post_json(**kwargs):
        calls.append(dict(kwargs))
        return 200, {"ok": True}

    monkeypatch.setattr(node_cli, "_control_post_json", _fake_post_json)

    result = node_cli._request_local_member_activation(
        SimpleNamespace(subnet_id="sn_member01"),
        previous_role="hub",
    )

    assert result["ok"] is True
    assert result["mode"] == "role_switch"
    assert calls[0]["path"] == "/api/node/role"
    assert calls[0]["body"] == {"role": "member", "subnet_id": "sn_member01"}


def test_request_local_member_activation_reconnects_existing_member(monkeypatch) -> None:
    node_cli = importlib.import_module("adaos.apps.cli.commands.node")
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(node_cli, "_resolve_node_control_base_url", lambda explicit=None: "http://127.0.0.1:8777")
    monkeypatch.setattr(node_cli, "_resolved_local_control_token", lambda control, cfg: "dev-token")

    def _fake_post_json(**kwargs):
        calls.append(dict(kwargs))
        return 200, {"ok": True, "accepted": True}

    monkeypatch.setattr(node_cli, "_control_post_json", _fake_post_json)

    result = node_cli._request_local_member_activation(
        SimpleNamespace(subnet_id="sn_member01"),
        previous_role="member",
    )

    assert result["ok"] is True
    assert result["mode"] == "member_hub_reconnect"
    assert calls[0]["path"] == "/api/node/member-hub/reconnect"


def test_node_join_reports_activation_and_persists_member_session(monkeypatch) -> None:
    node_cli = importlib.import_module("adaos.apps.cli.commands.node")
    cfg = SimpleNamespace(
        node_id="node-1",
        subnet_id="",
        role="hub",
        hub_url=None,
        root_settings=SimpleNamespace(base_url=""),
    )
    saved: dict[str, object] = {}
    runtime_state: dict[str, object] = {}

    monkeypatch.setattr(node_cli, "load_config", lambda: cfg)
    monkeypatch.setattr(node_cli, "_ensure_managed_key_paths", lambda conf: None)
    monkeypatch.setattr(node_cli, "save_config", lambda conf: saved.update({"role": conf.role, "subnet_id": conf.subnet_id, "hub_url": conf.hub_url}))
    monkeypatch.setattr(node_cli, "save_node_runtime_state", lambda **kwargs: runtime_state.update(kwargs))
    monkeypatch.setattr(
        node_cli,
        "_request_local_member_activation",
        lambda conf, previous_role: {"attempted": True, "ok": True, "mode": "role_switch"},
    )

    class _Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "token": "join-session-token",
                "subnet_id": "sn_member01",
                "hub_url": "https://ru.api.inimatic.com/hubs/sn_member01",
            }

    class _Session:
        trust_env = False

        def post(self, url, json=None, timeout=None):
            return _Response()

    monkeypatch.setattr(node_cli.requests, "Session", lambda: _Session())

    result = CliRunner().invoke(
        node_cli.app,
        ["join", "--code", "ABCD-EFGH", "--root", "https://ru.api.inimatic.com", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["role"] == "member"
    assert payload["subnet_id"] == "sn_member01"
    assert payload["activation"]["ok"] is True
    assert saved == {
        "role": "member",
        "subnet_id": "sn_member01",
        "hub_url": "https://ru.api.inimatic.com/hubs/sn_member01",
    }
    assert runtime_state["member_hub_token"] == "join-session-token"
