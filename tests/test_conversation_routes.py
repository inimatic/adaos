from types import SimpleNamespace

from adaos.services import conversation_routes, conversation_store, durable_delivery
from adaos.services.agent_context import get_ctx


def test_pairing_refresh_is_stable_but_rebinding_or_revoke_needs_new_consent(monkeypatch):
    import requests
    monkeypatch.setattr(get_ctx(), "config", SimpleNamespace(subnet_id="test", root_settings=None))
    now = [1000.0]
    monkeypatch.setattr(conversation_routes.time, "time", lambda: now[0])
    conversation_store.upsert_conversation(conversation_id="paired", owner="skill:test", webspace_id="test")
    link = {"ok": True, "link": {"bot_id": "bot", "chat_id": "one"}}
    status = [200]
    monkeypatch.setattr(requests, "get", lambda *a, **kw: SimpleNamespace(status_code=status[0], json=lambda: link))
    first = conversation_routes.refresh_paired_route("paired", "user:tester")
    now[0] += 40
    assert conversation_routes.refresh_paired_route("paired", "user:tester")["route_id"] == first["route_id"]
    link["link"]["chat_id"] = "two"
    now[0] += 40
    second = conversation_routes.refresh_paired_route("paired", "user:tester")
    assert second["route_id"] != first["route_id"]
    assert durable_delivery.get_reply_route(first["route_id"])["status"] == "revoked"
    assert conversation_store.get_conversation("paired")["policy"].get("output_route_ids", []) == []
    now[0] += 40
    status[0] = 404
    link.clear()
    link.update(ok=False, error="pairing_not_found")
    assert conversation_routes.refresh_paired_route("paired", "user:tester") is None
    assert durable_delivery.get_reply_route(second["route_id"])["status"] == "revoked"
    # Re-pairing to the SAME address is still a new grant, not revival of old consent.
    status[0] = 200
    link.update(ok=True, link={"bot_id": "bot", "chat_id": "two"})
    third = conversation_routes.refresh_paired_route("paired", "user:tester")
    assert third["route_id"] != second["route_id"]


def test_pairing_outage_cannot_be_misreported_as_authoritative_revocation(monkeypatch):
    import requests
    monkeypatch.setattr(get_ctx(), "config", SimpleNamespace(subnet_id="test", root_settings=None))
    conversation_store.upsert_conversation(conversation_id="paired", owner="skill:test", webspace_id="test")
    def unavailable(*a, **kw):
        raise requests.Timeout("offline")
    monkeypatch.setattr(requests, "get", unavailable)
    assert conversation_routes.refresh_paired_route("paired", "user:tester") is None
