import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api.notifications import router
from adaos.domain.personalization_access import SessionKey, SubjectRef
from adaos.services.agent_context import get_ctx
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
from adaos.services.personalization_runtime import personalization_access_service
from test_notification_runtime import notification_runtime, emit


@pytest.fixture
def notification_clients(notification_runtime):
    ctx = get_ctx()
    ctx.config = SimpleNamespace(subnet_id="A")
    policy = DesktopAccessService()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    clients = []
    for user in ("one", "two"):
        session = "notify-api-" + user
        policy.access.put_session(SessionKey(session_id=session, key_id="test",
            subject=SubjectRef("user", user), expires_at=time.time() + 3600))
        credential = policy.issue_credential(DesktopPrincipal(SubjectRef("session", session), session))["access_token"]
        clients.append(TestClient(app, headers={"X-AdaOS-Desktop-Credential": credential}))
    return notification_runtime, clients, policy


def test_http_private_state_cas_and_spoofing(notification_clients):
    (service, binding, _, clock), (one, two), policy = notification_clients
    from dataclasses import replace
    receipt = emit(service, replace(binding, principal_ref="user:one"))
    path = "/api/notifications"
    first = one.get(path)
    assert first.status_code == 200, first.text
    assert first.headers["Cache-Control"] == "private, no-store"
    assert first.json()["items"][0]["notification_id"] == receipt["notification_id"]
    assert two.get(path).json()["items"] == []
    assert two.get(path + "/records/" + receipt["notification_id"]).status_code == 404
    prefs = one.get(path + "/preferences").json()
    prefs["defaults"]["mode"] = "off"
    data = {"settings": prefs, "expected_revision": 0}
    assert one.put(path + "/preferences", json={**data, "principal_ref": "user:two"}).status_code == 422
    assert one.put(path + "/preferences", json=data).status_code == 200
    assert one.put(path + "/preferences", json=data).status_code == 409
    assert two.get(path + "/preferences").json()["revision"] == 0
    session = policy.access.store.get_session("notify-api-one")
    policy.access.store.update_session("notify-api-one", {"expires_at": time.time() - 1})
    assert one.get(path).status_code == 401


def test_devices_bind_opaque_session_without_exposing_route(notification_clients):
    _, (one, two), _ = notification_clients
    path = "/api/notifications"
    response = one.post(path + "/devices", json={"kind": "browser", "permission": "granted"})
    assert response.status_code == 200, response.text
    endpoint = response.json()
    assert "route_id" not in endpoint and "session_ref" not in endpoint
    assert one.get(path + "/endpoints/" + endpoint["endpoint_id"] + "/pending").json() == {"items": []}
    assert two.get(path + "/endpoints").json()["items"] == []
    assert two.patch(path + "/endpoints/" + endpoint["endpoint_id"],
                     json={"status": "paused", "expected_revision": 1}).status_code == 403
    assert one.post(path + "/devices", json={"kind": "browser", "principal_ref": "user:two"}).status_code == 422


def test_bad_credential_never_falls_back_to_owner(notification_clients, monkeypatch):
    _, (one, _), _ = notification_clients
    monkeypatch.setenv("ADAOS_TOKEN", "notify-test-owner")
    result = one.get("/api/notifications", headers={"X-AdaOS-Token": "notify-test-owner",
        "X-AdaOS-Desktop-Credential": "adaos-desktop-v1.invalid.secret"})
    assert result.status_code == 401


def test_export_and_erase_are_private_and_preserve_other_user(notification_clients):
    from dataclasses import replace
    (service, binding, _, _), (one, two), _ = notification_clients
    first = emit(service, replace(binding, principal_ref="user:one"))
    second = emit(service, replace(binding, principal_ref="user:two"))
    exported = one.get("/api/notifications/export").json()
    assert {row["notification_id"] for row in exported["items"]} == {first["notification_id"]}
    erased = one.delete("/api/notifications/history")
    assert erased.status_code == 200, erased.text
    assert erased.json()["deleted_records"] == 1
    assert "remote_pending_cleanup" in erased.json()
    assert one.get("/api/notifications/export").json()["items"] == []
    assert two.get("/api/notifications").json()["items"][0]["notification_id"] == second["notification_id"]
