from copy import deepcopy

import pytest

from adaos.domain.notifications import NotificationConflict
from adaos.services import conversation_delivery
from adaos.services.notifications import delivery
from adaos.services.notifications.probes import _MANIFEST
from test_notification_runtime import notification_runtime, emit
from test_notification_delivery import configure, Adapter
from test_notification_api import notification_clients


def test_one_shot_probe_uses_verified_user_and_shared_queue_without_policy_mutation(notification_clients):
    (_, _, _, _), (one, two), _ = notification_clients
    endpoint = one.post("/api/notifications/devices", json={"kind": "browser", "permission": "granted"}).json()
    path = "/api/notifications/endpoints/" + endpoint["endpoint_id"]
    before = one.get("/api/notifications/preferences").json()
    body = {"expected_revision": endpoint["revision"], "operation_key": "test-once", "allow_quiet": True}
    assert two.post(path + "/test", json=body).status_code == 403
    assert one.post(path + "/test", json={**body, "principal_ref": "user:two"}).status_code == 422
    tested = one.post(path + "/test", json=body)
    assert tested.status_code == 200, tested.text
    from adaos.domain.notifications import validate
    validate("endpoint_test", tested.json())
    assert tested.json()["decision"]["endpoint_ids"] == [endpoint["endpoint_id"]]
    assert one.post(path + "/test", json=body).json() == tested.json()
    assert one.get("/api/notifications/preferences").json() == before
    assert _dispatch_device()
    pending = one.get(path + "/pending").json()["items"]
    assert len(pending) == 1
    assert two.get(path + "/deliveries").status_code == 403
    snapshot = one.get(path + "/deliveries").json()
    validate("endpoint_inspection", snapshot)
    assert snapshot["items"][0]["evidence"]["recipient_read"] is False
    assert snapshot["items"][0]["evidence"]["provider_created"] is None
    assert snapshot["items"][0]["evidence"]["device_presented"] is None
    assert "Notifications are working" not in str(snapshot)
    ack = {"intent_id": pending[0]["intent_id"], "presentation_token": pending[0]["presentation_token"], "evidence": "presented"}
    assert one.post(path + "/ack", json=ack).status_code == 200
    outcome = one.get(path + "/deliveries").json()["items"][0]
    assert outcome["evidence"]["device_presented"] and not outcome["evidence"]["recipient_read"]


def _dispatch_device():
    from adaos.services.notifications.device_delivery import DeviceAdapter
    return conversation_delivery.dispatch_once(DeviceAdapter())


def test_policy_change_cancels_probe_instead_of_enabling_fallback(notification_clients):
    _, (one, _), _ = notification_clients
    endpoint = one.post("/api/notifications/devices", json={"kind": "browser", "permission": "granted"}).json()
    path = "/api/notifications/endpoints/" + endpoint["endpoint_id"]
    assert one.post(path + "/test", json={"expected_revision": 1, "operation_key": "cancelled", "allow_quiet": True}).status_code == 200
    prefs = one.get("/api/notifications/preferences").json()
    prefs["defaults"]["mode"] = "off"
    assert one.put("/api/notifications/preferences", json={"settings": prefs, "expected_revision": 0}).status_code == 200
    assert _dispatch_device()
    outcome = one.get(path + "/deliveries").json()["items"][0]
    assert outcome["status"] == "suppressed"
    assert outcome["reason"] == "probe_policy_changed"
    assert one.get(path + "/pending").json()["items"] == []


def test_endpoint_epoch_stops_deferred_notices_after_pause_and_resume(notification_runtime, monkeypatch):
    service, binding, _, clock = notification_runtime
    binding, endpoints, items = configure(service, binding, transports=("max",))
    monkeypatch.setattr(delivery, "live_application_authority", lambda *a: True)
    prefs = service.preferences.read(binding.principal_ref, binding.subnet_ref)
    prefs["quiet_hours"].update(enabled=True, timezone="Europe/Moscow")
    service.preferences.write(binding.principal_ref, binding.subnet_ref, prefs, expected_revision=1)
    receipt = emit(service, binding)
    ep = items[0]
    endpoints.set_status(binding.principal_ref, ep["endpoint_id"], status="paused", expected_revision=1)
    endpoints.set_status(binding.principal_ref, ep["endpoint_id"], status="active", expected_revision=2)
    clock[0] = 1794200400
    service.schedule_due()
    adapter = Adapter()
    assert not conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert adapter.sent == []
    assert service.get(binding.principal_ref, receipt["notification_id"])["decision"]["reason"] == "endpoint_unavailable"
    # A new source fact can use the newly granted endpoint epoch.
    emit(service, binding, event_id="new-after-reconnect", source_revision=2)
    assert conversation_delivery.dispatch_once(adapter, now=clock[0])
    assert len(adapter.sent) == 1


def test_story_endpoint_test_cannot_reach_provider_or_foreign_fixture():
    from adaos.sdk import notifications, testing
    declaration = deepcopy(_MANIFEST)
    with testing.notification_story(declaration, management=True) as story:
        endpoint = story.endpoint()
        result = notifications.test_endpoint(endpoint["endpoint_id"], expected_revision=1, operation_key="fixture-test")
        assert result["decision"]["reason"] == "preview"
        snapshot = notifications.inspect_endpoint(endpoint["endpoint_id"])
        assert snapshot["items"] == []
        with pytest.raises(PermissionError):
            notifications.test_endpoint("endpoint:production", expected_revision=1, operation_key="escape")
        with pytest.raises(NotificationConflict):
            notifications.test_endpoint(endpoint["endpoint_id"], expected_revision=2, operation_key="stale")


def test_device_reconnect_requires_original_session_and_explicit_new_epoch(notification_clients):
    _, (one, two), _ = notification_clients
    endpoint = one.post("/api/notifications/devices", json={"kind": "browser", "permission": "granted"}).json()
    path = "/api/notifications/endpoints/" + endpoint["endpoint_id"]
    repeated = one.post("/api/notifications/devices", json={"kind": "browser", "permission": "granted"})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["endpoint_id"] == endpoint["endpoint_id"]
    assert repeated.json()["revision"] == endpoint["revision"]
    assert one.patch(path, json={"status": "revoked", "expected_revision": 1}).status_code == 200
    # Ordinary foreground auto-enrollment cannot override a user's revocation.
    assert one.post("/api/notifications/devices", json={"kind": "browser", "permission": "granted"}).json()["status"] == "revoked"
    assert two.post(path + "/reconnect", json={"expected_revision": 2}).status_code == 403
    resumed = one.post(path + "/reconnect", json={"expected_revision": 2})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active" and resumed.json()["revision"] == 3
    assert one.post(path + "/reconnect", json={"expected_revision": 2}).status_code == 409


def test_max_discovery_refreshes_cached_audience_evidence(notification_runtime, monkeypatch):
    import json
    from adaos.services.agent_context import get_ctx
    from adaos.services import conversation_routes, max_delivery
    from adaos.services.notifications.endpoints import EndpointService
    service, binding, _, _ = notification_runtime
    binding, endpoints, _ = configure(service, binding, transports=("max",))
    ctx = get_ctx()
    (ctx.paths.state_dir() / "max-channel.json").write_text(json.dumps({"enabled": True}), encoding="utf8")
    observed = {"hub_id": "A", "bot_id": "test", "chat_id": "202", "user_id": "alice", "binding_id": "verified", "chat_type": "unknown"}
    class Root:
        def request(self, *args, **kwargs):
            return {"ok": True, "bindings": [dict(observed)]}
    monkeypatch.setattr(max_delivery, "root_client", lambda: Root())
    route = conversation_routes.refresh_max_routes("discovery-test", binding.principal_ref)[0]
    first = endpoints.admit_route(binding.principal_ref, binding.subnet_ref, route["route_id"], label="MAX")
    assert first["capabilities"]["source_audience"] == "unverified"
    observed["chat_type"] = "dialog"
    fresh = conversation_routes.refresh_max_routes("discovery-test", binding.principal_ref)[0]
    second = endpoints.admit_route(binding.principal_ref, binding.subnet_ref, fresh["route_id"], label="MAX")
    assert second["endpoint_id"] == first["endpoint_id"]
    assert second["revision"] == first["revision"] + 1
    assert second["capabilities"]["source_audience"] == "personal"
