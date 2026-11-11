import asyncio
import base64
import json
import logging
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from adaos.apps.api.auth import require_token, require_tool_caller
from adaos.apps.api.desktop_access import require_desktop_caller
from adaos.domain.personalization_access import SessionKey, SubjectRef
from adaos.services.bootstrap_runtime import HubRouteProxyPolicy
from adaos.services.bootstrap_runtime import route_tunnel_runtime as route_runtime
from adaos.services.bootstrap_runtime.route_http_auth import can_reply_inline, local_http_headers
from adaos.services.bootstrap_runtime.route_tunnel_runtime import NatsRouteTunnelRuntime
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore


@pytest.fixture
def authority(tmp_path, monkeypatch):
    from adaos.services import personalization_runtime as personalization
    from adaos.services.workspaces import index

    access = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / "access.json"),
                                         owner=SubjectRef("user", "owner"))
    monkeypatch.setattr(personalization, "personalization_access_service", lambda: access)
    monkeypatch.setattr(personalization, "current_user_id", lambda: "owner")
    monkeypatch.setattr(personalization, "current_subnet_id", lambda: "test-subnet")
    monkeypatch.setattr(index, "get_workspace", lambda target: SimpleNamespace(workspace_id=target))
    monkeypatch.setenv("ADAOS_TOKEN", "test-owner-token")
    policy = DesktopAccessService(access)
    visitor = SubjectRef("user", "visitor")
    policy.share(DesktopPrincipal(access.owner), "allowed", visitor)
    access.put_session(SessionKey(session_id="visitor-session", key_id="test", subject=visitor,
                                  expires_at=time.time() + 600))
    token = policy.issue_credential(DesktopPrincipal(SubjectRef("session", "visitor-session"), "visitor-session"))["access_token"]
    return policy, token


@pytest.mark.asyncio
@pytest.mark.parametrize("case,method,path,transport,expected", [
    ("invalid", "GET", "/api/node/status", "http", 401),
    ("valid", "GET", "/api/node/status", "http", 200),
    ("valid", "GET", "/api/node/yjs/webspaces/allowed", "http", 200),
    ("valid", "GET", "/api/node/yjs/webspaces/other", "http", 403),
    ("valid", "POST", "/api/node/yjs/webspaces/allowed/access", "http", 403),
    ("valid", "POST", "/api/tools/call", "http", 200),
    ("valid", "POST", "/api/tools/call", "chunked", 200),
    ("invalid", "POST", "/api/tools/call", "chunked", 403),
    ("unverified", "GET", "/api/node/status", "http", 401),
    ("unverified", "POST", "/api/tools/call", "chunked", 401),
    ("revoked", "GET", "/api/node/status", "http", 401),
    ("expired", "GET", "/api/node/yjs/webspaces/allowed", "http", 403),
])
async def test_nats_http_route_preserves_caller_and_denies_invalid_authority(
    authority, monkeypatch, tmp_path, case, method, path, transport, expected,
):
    policy, token = authority
    if case == "revoked":
        policy.access.revoke_session("visitor-session", actor=policy.access.owner)
    elif case == "expired":
        policy.access.store.update_session("visitor-session", {"expires_at": time.time() - 1})
    app = FastAPI()

    async def identity(request: Request):
        actor = request.state.adaos_verified_caller
        scope = getattr(request.state, "adaos_verified_caller_scope", None)
        return {"actor": actor.ref(), "scope": scope.ref() if scope else None}

    app.add_api_route("/api/node/status", identity, dependencies=[Depends(require_token)])
    app.add_api_route("/api/node/yjs/webspaces/{webspace_id}", identity,
                      dependencies=[Depends(require_desktop_caller)])
    app.add_api_route("/api/node/yjs/webspaces/{webspace_id}/access", identity, methods=["POST"],
                      dependencies=[Depends(require_desktop_caller)])
    app.add_api_route("/api/tools/call", identity, methods=["POST"], dependencies=[Depends(require_tool_caller)])
    api = TestClient(app)
    forwarded = []

    class LocalSession:
        def request(self, method, url, *, data=None, headers=None, **kwargs):
            forwarded.append(dict(headers))
            if hasattr(data, "read"):
                data = data.read()
            return api.request(method, urlsplit(url).path, headers=headers, content=data)

        def close(self):
            pass

    monkeypatch.setattr("requests.Session", LocalSession)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(HubRouteProxyPolicy, "build_http_bases", lambda self, **kwargs: ["http://127.0.0.1:18777"])
    monkeypatch.setattr(route_runtime, "_hub_route_node_status_supervisor_runtime", lambda _: {"available": False})
    published = []
    replied = asyncio.Event()
    subscriptions = {}

    class Nats:
        _pending_data_size = 0

        async def publish(self, subject, payload):
            message = json.loads(payload)
            if message.get("t") == "http_resp":
                published.append(message)
                replied.set()

        async def flush(self, *args, **kwargs):
            pass

    async def subscribe(subject, *, cb):
        subscriptions[subject] = cb
        return SimpleNamespace(subject=subject, cb=cb)

    service = SimpleNamespace(_log=logging.getLogger("test.route-auth"), _route_policy=HubRouteProxyPolicy(),
                              ctx=SimpleNamespace(config=SimpleNamespace(token="test-owner-token", zone_id="ru")),
                              _mark_hub_root_authority_ready=lambda: None)
    runtime = NatsRouteTunnelRuntime(service, rate_limited_log=lambda *args, **kwargs: None, is_ready=lambda: True)
    workers = []
    await runtime.install(nc=Nats(), subscribe=subscribe, sub_workers=workers, hub_id="test-hub",
                          candidate_passive_mode=False, runtime_instance="active",
                          hub_nats_verbose=False, hub_nats_quiet=True)
    callback = subscriptions["route.v2.to_hub.test-hub.*"]

    async def send(payload):
        await callback(SimpleNamespace(subject="route.v2.to_hub.test-hub.test-hub--http--auth",
                                       data=json.dumps(payload).encode()))

    headers = {"x-adaos-route-authenticated": "1", "x-adaos-route-principal": "verified",
               "content-type": "application/json"}
    if case == "unverified":
        headers["x-adaos-route-principal"] = "unverified"
    else:
        headers["x-adaos-desktop-credential"] = "adaos-desktop-v1.invalid" if case == "invalid" else token
    body = json.dumps({"context": {"webspace_id": "allowed"}}).encode()
    try:
        if transport == "chunked":
            await send({"t": "http_req_open", "method": method, "path": path, "headers": headers,
                        "content_length": len(body)})
            await send({"t": "http_req_chunk", "data_b64": base64.b64encode(body).decode()})
            await send({"t": "http_req_end"})
        else:
            await send({"t": "http", "method": method, "path": path, "headers": headers,
                        "body_b64": base64.b64encode(body).decode() if method == "POST" else None})
        await asyncio.wait_for(replied.wait(), 3)
        response = published[0]
        assert response["status"] == expected, response
        assert forwarded, "Protected requests must reach production API admission, not inline status"
        assert all("X-AdaOS-Token" not in entry for entry in forwarded)
        if expected == 200:
            identity_payload = json.loads(base64.b64decode(response["body_b64"]))
            assert identity_payload["actor"] == "session:visitor-session"
            if path == "/api/tools/call":
                assert identity_payload["scope"] == "webspace:allowed"
    finally:
        await runtime.close()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        api.close()


@pytest.mark.parametrize("header,value", [
    ("X-AdaOS-Desktop-Credential", ""),
    ("X-ADAOS-DESKTOP-CREDENTIAL", ["unexpected"]),
    ("x-adaos-desktop-credential", "x" * 1025),
    ("Authorization", "Bearer adaos-desktop-v1.invalid"),
])
def test_malformed_explicit_desktop_authority_never_selects_owner(header, value):
    incoming = {header: value, "x-adaos-route-authenticated": "1", "x-adaos-route-principal": "verified"}
    forwarded = local_http_headers(incoming, token_local="test-owner-token")
    assert "X-AdaOS-Token" not in forwarded
    assert forwarded["X-AdaOS-Desktop-Credential"]
    assert not can_reply_inline("GET", "/api/node/status", incoming)


@pytest.mark.parametrize("incoming", [{}, {"x-adaos-route-authenticated": "1", "x-adaos-route-principal": "verified"}])
def test_trusted_root_control_keeps_inline_liveness_and_owner_transport(incoming):
    forwarded = local_http_headers(incoming, token_local="test-owner-token", zone_id="ru")
    assert forwarded["X-AdaOS-Token"] == "test-owner-token"
    assert forwarded["X-AdaOS-Route"] == "root-routed"
    assert forwarded["X-AdaOS-Zone"] == "ru"
    assert can_reply_inline("GET", "/api/node/status/", incoming)
    assert not can_reply_inline("POST", "/api/node/status", incoming)
    assert can_reply_inline("GET", "/api/ping", incoming)


@pytest.mark.parametrize("incoming", [
    {"x-adaos-route-authenticated": "1"},
    {"x-adaos-route-authenticated": "0", "x-adaos-route-principal": "verified"},
    {"x-adaos-route-authenticated": "1", "x-adaos-route-principal": "unknown"},
    {"x-adaos-route-principal": "verified"},
])
def test_incomplete_or_unverified_root_admission_cannot_select_owner(incoming):
    assert "X-AdaOS-Token" not in local_http_headers(incoming, token_local="test-owner-token")
    assert not can_reply_inline("GET", "/api/node/status", incoming)
