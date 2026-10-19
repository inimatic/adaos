from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.services import access_links, node_departure
from adaos.services.agent_context import get_ctx
from adaos.services.runtime_lifecycle import reset_runtime_lifecycle, is_draining


@pytest.fixture
def departure_context(monkeypatch):
    ctx = get_ctx()
    conf = SimpleNamespace(role="member", node_id="node-a", subnet_id="subnet-a", hub_url="https://example.invalid", token="test-token")
    monkeypatch.setattr(node_departure, "get_ctx", lambda: SimpleNamespace(config=conf, bus=ctx.bus))
    monkeypatch.setattr(node_departure, "_LOCK", asyncio.Lock())
    monkeypatch.setattr(node_departure, "_HOOKS", {})
    monkeypatch.setattr(node_departure, "report_deleted_node_to_root", lambda *_, **__: {"status": "confirmed"})
    reset_runtime_lifecycle()
    yield conf
    reset_runtime_lifecycle()


@pytest.mark.asyncio
async def test_preparation_awaits_hooks_before_hub_and_retries_lost_reply(departure_context, monkeypatch):
    steps, calls = [], []

    async def backup(operation):
        assert is_draining()
        steps.append("backup")

    async def notify(operation):
        assert steps == ["backup"]
        calls.append(operation["operation_id"])
        if len(calls) == 1:
            raise ConnectionError("reply lost")
        return {"node_id": "node-a", "subnet_id": "subnet-a", "lifecycle_state": "deleted", "departure_operation_id": calls[-1]}

    node_departure.register_departure_hook("backup", backup)
    monkeypatch.setattr(node_departure, "_notify_hub", notify)
    with pytest.raises(ConnectionError):
        await node_departure.prepare_deletion(expected_node_id="node-a")
    result = await node_departure.prepare_deletion(expected_node_id="node-a")
    assert result["phase"] == "ready_to_remove"
    assert calls[0] == calls[1]
    assert steps == ["backup"]
    assert await node_departure.prepare_deletion(expected_node_id="node-a") == result


@pytest.mark.asyncio
async def test_failed_hook_and_changed_identity_never_detach(departure_context, monkeypatch):
    monkeypatch.setattr(node_departure, "_notify_hub", lambda _: pytest.fail("detached before backup"))
    node_departure.register_departure_hook("backup", lambda _: {"ok": False})
    with pytest.raises(ValueError):
        await node_departure.prepare_deletion(expected_node_id="another-node")
    with pytest.raises(RuntimeError):
        await node_departure.prepare_deletion(expected_node_id="node-a")
    assert node_departure._read_journal()["phase"] == "failed"


@pytest.mark.asyncio
async def test_standalone_deletion_has_separate_scope(departure_context, monkeypatch):
    departure_context.role = "hub"
    monkeypatch.setattr(node_departure, "_notify_hub", lambda _: pytest.fail("HUB cannot detach itself as a member"))
    with pytest.raises(ValueError):
        await node_departure.prepare_deletion(expected_node_id="node-a")
    result = await node_departure.prepare_deletion(expected_node_id="node-a", allow_standalone=True)
    assert result["scope"] == "local_standalone_hub"


def test_deleted_member_cannot_be_reactivated_by_stale_registry_or_adoption():
    access_links.upsert_link("member", "node-a", {"hostname": "same-pc"})
    stale = access_links._load_registry()
    receipt = access_links.delete_member_link("node-a", operation_id="operation-a", subnet_id="subnet-a")
    assert receipt["lifecycle_state"] == "deleted"
    access_links._save_registry(stale)
    access_links.touch_member_link("node-a", online=True, connection_state="connected")
    assert access_links.authorize_link("member", "node-a") == (False, "deleted")
    assert access_links.get_link("member", "node-a")["lifecycle_state"] == "deleted"
    with pytest.raises(ValueError):
        access_links.set_link_lifetime("member", "node-a", "permanent")
    assert access_links.delete_member_link("node-a", operation_id="operation-a", subnet_id="subnet-a")["deleted_at"] == receipt["deleted_at"]
    with pytest.raises(ValueError):
        access_links.delete_member_link("node-a", operation_id="different", subnet_id="subnet-a")
    assert access_links.authorize_link("member", "node-b") == (True, None)


def test_departure_api_requires_auth_and_protects_hub(departure_context, monkeypatch):
    from adaos.apps.api import node_departure as api
    from adaos.apps.api.auth import require_token

    departure_context.role = "hub"
    app = FastAPI()
    app.include_router(api.router, prefix="/api/node/departure")
    body = {"expected_node_id": "node-a"}
    client = TestClient(app)
    assert client.post("/api/node/departure/prepare", json=body).status_code == 401
    app.dependency_overrides[require_token] = lambda: None
    assert client.post("/api/node/departure/prepare", json=body).status_code == 409
    body["allow_standalone"] = True
    assert client.post("/api/node/departure/prepare", json=body).status_code == 200
    body["expected_node_id"] = "another-node"
    assert client.post("/api/node/departure/prepare", json=body).status_code == 409


def test_departure_uses_link_identity_and_recovers_lost_ack(monkeypatch):
    from adaos.services.subnet import link_ws
    ctx = get_ctx()
    conf = SimpleNamespace(role="hub", node_id="hub-a", subnet_id="subnet-a", token="test-token")
    context = SimpleNamespace(config=conf, bus=ctx.bus)
    monkeypatch.setattr(link_ws, "get_ctx", lambda: context)
    monkeypatch.setattr(node_departure, "get_ctx", lambda: context)
    monkeypatch.setattr(node_departure, "report_deleted_node_to_root", lambda *_, **__: {"status": "confirmed"})

    class Manager:
        async def register(self, node_id, websocket, **kwargs):
            return SimpleNamespace(send_json=websocket.send_json)
        async def unregister(self, *args, **kwargs):
            pass

    monkeypatch.setattr(link_ws, "get_hub_link_manager", Manager)
    app = FastAPI()
    app.include_router(link_ws.router)
    client = TestClient(app)
    hello = {"t": "hello", "node_id": "node-a", "subnet_id": "subnet-a"}
    with client.websocket_connect("/ws/subnet?token=test-token") as ws:
        ws.send_json(hello)
        assert ws.receive_json()["ok"] is True
        ws.send_json({"t": "node.departure", "reason": "delete", "operation_id": "operation-a", "node_id": "victim"})
        response = ws.receive_json()
        assert response["receipt"]["node_id"] == "node-a"
        assert access_links.authorize_link("member", "victim") == (True, None)
    with client.websocket_connect("/ws/subnet?token=test-token") as ws:
        ws.send_json({**hello, "departure_operation_id": "operation-a"})
        response = ws.receive_json()
        assert response["ok"] is False
        assert response["departure_receipt"]["departure_operation_id"] == "operation-a"
    with client.websocket_connect("/ws/subnet?token=test-token") as ws:
        ws.send_json({**hello, "departure_operation_id": "another-operation"})
        assert "departure_receipt" not in ws.receive_json()
