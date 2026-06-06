from __future__ import annotations

from typing import Any, Mapping

from adaos.sdk import redevice
from adaos.services import device_inventory


def _endpoint(code: str, hub_id: str | None) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "schema_version": "endpoint-policy.v1",
        "endpoint_id": f"endpoint-{code}",
        "trust_level": "limited",
    }
    if hub_id:
        policy["hub_id"] = hub_id
    return {
        "code": code,
        "endpoint_id": f"endpoint-{code}",
        "state": "consumed",
        "hub_id": hub_id,
        "owner_id": hub_id,
        "endpoint_policy": policy,
        "last_seen_at": 1,
    }


def test_redevice_root_list_is_filtered_by_local_subnet(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(redevice, "_local_scope", lambda: ("sn_local", "sn_local"))

    def fake_request(self: redevice.ReDeviceBridge, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        captured["method"] = method
        captured["path"] = path
        return {
            "ok": True,
            "devices": [
                _endpoint("LOCAL", "sn_local"),
                _endpoint("FOREIGN", "sn_foreign"),
                _endpoint("UNSCOPED", None),
            ],
        }

    synced: list[dict[str, Any]] = []
    monkeypatch.setattr(redevice.ReDeviceBridge, "request_json", fake_request)
    monkeypatch.setattr(redevice.ReDeviceBridge, "sync_local_registry", lambda self, endpoints: synced.extend(dict(item) for item in endpoints))

    endpoints = redevice.ReDeviceBridge(root_base="https://root.example").list_endpoints(sync_registry=True)

    assert captured["method"] == "GET"
    assert "hub_id=sn_local" in captured["path"]
    assert "subnet_id=sn_local" in captured["path"]
    assert [item["code"] for item in endpoints] == ["LOCAL"]
    assert [item["code"] for item in synced] == ["LOCAL"]


def test_redevice_sync_local_registry_skips_foreign_subnet(monkeypatch) -> None:
    monkeypatch.setattr(redevice, "_local_scope", lambda: ("sn_local", "sn_local"))

    touched: list[dict[str, Any]] = []

    def fake_touch(endpoint_id: str, **kwargs: Any) -> dict[str, Any]:
        touched.append({"endpoint_id": endpoint_id, **kwargs})
        return touched[-1]

    from adaos.services import access_links

    monkeypatch.setattr(access_links, "touch_redevice_link", fake_touch)

    redevice.ReDeviceBridge(root_base="https://root.example").sync_local_registry(
        [
            _endpoint("LOCAL", "sn_local"),
            _endpoint("FOREIGN", "sn_foreign"),
            _endpoint("UNSCOPED", None),
        ]
    )

    assert [item["pair_code"] for item in touched] == ["LOCAL"]
    assert touched[0]["hub_id"] == "sn_local"


def test_redevice_command_request_is_scoped(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(redevice, "_local_scope", lambda: ("sn_local", "sn_local"))

    def fake_request(self: redevice.ReDeviceBridge, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = dict(payload or {})
        return {"ok": True}

    monkeypatch.setattr(redevice.ReDeviceBridge, "request_json", fake_request)

    result = redevice.ReDeviceBridge(root_base="https://root.example").send_command("LOCAL", {"type": "display.next"})

    assert result["ok"] is True
    assert captured["method"] == "POST"
    assert captured["path"].startswith("/v1/redevice/devices/LOCAL/commands?")
    assert "hub_id=sn_local" in captured["path"]
    assert "owner_id=sn_local" in captured["path"]


def test_device_inventory_hides_polluted_redevice_links(monkeypatch) -> None:
    monkeypatch.setattr(device_inventory, "_local_redevice_scope", lambda: ("sn_local", "sn_local"))
    monkeypatch.setattr(
        device_inventory._access_links,
        "list_links",
        lambda kind: [
            {
                "id": "endpoint-local",
                "display_name": "Local tablet",
                "online": True,
                "hub_id": "sn_local",
                "owner_id": "sn_local",
                "endpoint_policy": {"hub_id": "sn_local", "trust_level": "limited"},
            },
            {
                "id": "endpoint-foreign",
                "display_name": "Foreign tablet",
                "online": True,
                "hub_id": "sn_foreign",
                "owner_id": "sn_foreign",
                "endpoint_policy": {"hub_id": "sn_foreign", "trust_level": "limited"},
            },
            {
                "id": "endpoint-unscoped",
                "display_name": "Unscoped tablet",
                "online": True,
            },
        ],
    )

    items = device_inventory.DeviceInventoryService().list_devices(kind="redevice")

    assert [item["identity"]["endpoint_id"] for item in items] == ["endpoint-local"]
