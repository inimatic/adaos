from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adaos.sdk.data import device_access
from adaos.services import redevice_lan_admission as lan


def _install_memory_state(monkeypatch):
    state: dict[str, Any] = {}

    def get(namespace: str, key: str) -> dict[str, Any] | None:
        assert namespace == "redevice_lan_admission"
        assert key == "state"
        return dict(state) if state else None

    def put(namespace: str, key: str, payload: Mapping[str, Any]) -> None:
        assert namespace == "redevice_lan_admission"
        assert key == "state"
        state.clear()
        state.update(dict(payload))

    monkeypatch.setattr(lan.sqlite_db, "durable_state_get", get)
    monkeypatch.setattr(lan.sqlite_db, "durable_state_put", put)
    return state


def test_lan_admission_approves_endpoint_with_local_control_root(monkeypatch) -> None:
    _install_memory_state(monkeypatch)
    monkeypatch.setattr(
        lan,
        "_local_config",
        lambda: {
            "hub_id": "sn_local",
            "owner_id": "owner_local",
            "node_id": "node_local",
            "node_names": ["Homepoint"],
            "subnet_names": ["Home"],
            "zone_id": "lo",
            "local_api_url": "http://127.0.0.1:8777",
        },
    )
    monkeypatch.setattr(lan, "_publish_pending_action", lambda request: None)

    touched: list[dict[str, Any]] = []
    upserts: list[dict[str, Any]] = []
    monkeypatch.setattr(lan.access_links, "touch_redevice_link", lambda endpoint_id, **kwargs: touched.append({"endpoint_id": endpoint_id, **kwargs}) or touched[-1])
    monkeypatch.setattr(lan.access_links, "upsert_link", lambda kind, entry_id, patch: upserts.append({"kind": kind, "entry_id": entry_id, **dict(patch)}) or upserts[-1])

    enabled = lan.enable_discovery(ttl_s=60, hub_base_url="http://192.168.0.10:8777")
    request = lan.submit_request(
        {
            "endpoint_id": "redevice-phone",
            "device_label": "Phone",
            "endpoint_manifest": {"endpoint_id": "redevice-phone"},
            "diagnostic_report": {"network_online": True},
        },
        client_host="192.168.0.44",
    )
    approved = lan.approve_request(request["request_id"], display_name="Kitchen phone")

    assert enabled["discovery"]["hub_base_url"] == "http://192.168.0.10:8777"
    assert request["state"] == "not_confirmed"
    assert approved["ok"] is True
    assert approved["credentials"]["root_url"] == "http://192.168.0.10:8777"
    assert approved["credentials"]["pair_code"]
    assert touched[0]["hub_id"] == "sn_local"
    assert touched[0]["owner_id"] == "owner_local"
    assert upserts[0]["root_url"] == "http://127.0.0.1:8777"
    assert upserts[0]["endpoint_root_url"] == "http://192.168.0.10:8777"
    assert upserts[0]["endpoint_token"] == approved["credentials"]["endpoint_token"]


def test_endpoint_command_uses_endpoint_specific_root_url(monkeypatch) -> None:
    endpoint = {
        "endpoint_id": "redevice-phone",
        "pair_code": "LAN12345",
        "root_url": "http://127.0.0.1:8777",
        "endpoint_policy": {"hub_id": "sn_local", "owner_id": "owner_local"},
    }
    monkeypatch.setattr(device_access, "_resolve_redevice_endpoint", lambda device_ref=None, code=None: (endpoint, "LAN12345"))

    from adaos.services import endpoint_router
    from adaos.sdk import redevice

    monkeypatch.setattr(endpoint_router, "build_endpoint_command", lambda payload, **kwargs: {"payload": payload, "kwargs": kwargs})
    monkeypatch.setattr(endpoint_router, "legacy_payload_from_envelope", lambda envelope: {"type": envelope["payload"].get("type", "display.test")})
    captured: dict[str, Any] = {}

    def fake_send(self: redevice.ReDeviceBridge, code: str, command: Mapping[str, Any]) -> dict[str, Any]:
        captured["base_url"] = self.base_url
        captured["code"] = code
        captured["command"] = dict(command)
        return {"ok": True}

    monkeypatch.setattr(redevice.ReDeviceBridge, "send_command", fake_send)

    result = device_access.send_endpoint_command("redevice:redevice-phone", {"type": "display.test"})

    assert result["ok"] is True
    assert captured["base_url"] == "http://127.0.0.1:8777"
    assert captured["code"] == "LAN12345"


def test_lan_command_queue_redelivers_until_ack(monkeypatch) -> None:
    _install_memory_state(monkeypatch)
    now = {"value": 1000.0}
    monkeypatch.setattr(lan, "_now_ts", lambda: now["value"])
    monkeypatch.setattr(
        lan,
        "_endpoint_by_code",
        lambda code: {
            "id": "redevice-phone",
            "pair_code": "LAN12345",
            "endpoint_token": "endpoint-token",
            "hub_id": "sn_local",
            "owner_id": "owner_local",
        }
        if code == "LAN12345"
        else None,
    )
    monkeypatch.setattr(lan.access_links, "touch_redevice_link", lambda *args, **kwargs: {})

    queued = lan.enqueue_command("LAN12345", {"command_id": "cmd:test", "type": "display.clear_surface"})
    first = lan.next_command("LAN12345", endpoint_token="endpoint-token")
    second = lan.next_command("LAN12345", endpoint_token="endpoint-token")
    now["value"] += 13.0
    third = lan.next_command("LAN12345", endpoint_token="endpoint-token")
    ack = lan.ack_command("LAN12345", "cmd:test", {"state": "completed"}, endpoint_token="endpoint-token")
    now["value"] += 13.0
    after_ack = lan.next_command("LAN12345", endpoint_token="endpoint-token")

    assert queued["state"] == "queued"
    assert first["command"]["command_id"] == "cmd:test"
    assert first["command"]["delivery_attempts"] == 1
    assert second["command"] is None
    assert third["command"]["command_id"] == "cmd:test"
    assert third["command"]["delivery_attempts"] == 2
    assert ack["state"] == "acknowledged"
    assert after_ack["command"] is None
