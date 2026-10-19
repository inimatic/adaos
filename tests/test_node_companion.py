from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from adaos.apps.api import node_companion
from adaos.apps.api.auth import require_token
from adaos.services import access_links, device_inventory


@pytest.mark.parametrize("role,parent", [("hub", "hub:subnet-a"), ("member", "member:node-a")])
def test_companion_binds_one_endpoint_to_the_local_node_device(monkeypatch, role, parent):
    monkeypatch.setattr(node_companion, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="node-a", subnet_id="subnet-a", role=role)))
    app = FastAPI()
    app.include_router(node_companion.router, prefix="/api/node/companion")
    client = TestClient(app)
    body = {"endpoint_id": "desktop-test", "expected_node_id": "node-a", "version": "preview", "os_name": "windows"}
    assert client.post("/api/node/companion", json=body).status_code == 401
    app.dependency_overrides[require_token] = lambda: None
    for _ in range(2):
        response = client.post("/api/node/companion", json=body)
        assert response.status_code == 200
        assert response.json()["parent_device_ref"] == parent
    access_links.touch_browser_session("desktop-test", online=True, webspace_id="desktop")
    monkeypatch.setattr(access_links, "_active_browser_runtime_peers", lambda: [
        {"device_id": "desktop-test", "runtime_client_id": "events", "online": True},
        {"device_id": "desktop-test", "runtime_client_id": "yws", "online": True},
    ])
    rows = access_links.browser_snapshot()
    assert len(rows) == 1
    assert rows[0]["id"] == "desktop-test"
    assert rows[0]["parent_device_ref"] == parent
    assert rows[0]["endpoint_kind"] == "native_client"
    body["expected_node_id"] = "another-node"
    assert client.post("/api/node/companion", json=body).status_code == 409


def test_native_admission_and_transport_are_not_two_inventory_endpoints(monkeypatch):
    monkeypatch.setattr(access_links, "browser_snapshot", lambda: [{"id": "desktop-test", "client_build_id": "adaos-desktop"}])
    monkeypatch.setattr(access_links, "list_links", lambda _: [{"id": "desktop-test", "endpoint_manifest": {"endpoint_id": "desktop-test", "kind": "desktop_client"}}])
    monkeypatch.setattr(device_inventory, "_redevice_entry_matches_local_scope", lambda _: True)
    assert device_inventory.DeviceInventoryService()._list_redevice_devices(now=1000) == []
