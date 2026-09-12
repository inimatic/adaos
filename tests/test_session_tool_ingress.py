import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from adaos.apps.api import auth, personalization, tool_bridge
from adaos.domain.personalization_access import Grant, ScopeRef, SessionKey, SubjectRef
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.policy.caller import current_caller, current_caller_scope, verified_caller


@pytest.fixture
def ingress(tmp_path, monkeypatch):
    access = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / "access.json"))
    reader = SubjectRef("user", "reader")
    access.put_session(SessionKey(session_id="reader-session", key_id="reader-key", subject=reader,
                                  expires_at=time.time() + 600))
    access.put_grant(Grant(grant_id="read", subject=reader, scope=ScopeRef("skill", "sample"),
                           capabilities=("workspace.read",), issued_by=access.owner))
    monkeypatch.setenv("ADAOS_TOKEN", "owner-secret")
    monkeypatch.setattr("adaos.services.personalization_runtime.current_user_id", lambda *_args: access.owner.id)
    monkeypatch.setattr("adaos.services.personalization_runtime.personalization_access_service", lambda *_args: access)
    async def manager(*_args):
        return SimpleNamespace()
    monkeypatch.setattr(tool_bridge, "_skill_manager_for_context", manager)
    monkeypatch.setattr(tool_bridge, "_declared_tool_side_effects", lambda _manager, **kw:
                        {"read": "read", "write": "local_write"}.get(kw["public_tool"], ""))
    monkeypatch.setattr(tool_bridge, "_webspace_uses_dev_runtime", lambda args: args.get("webspace_id") == "dev")
    calls = []
    async def execute(*_args):
        actor = await asyncio.to_thread(current_caller)
        scope = await asyncio.to_thread(current_caller_scope)
        calls.append((actor.ref(), scope.ref() if scope else None))
        return {"ok": True, "actor": actor.ref(), "scope": scope.ref() if scope else None}
    monkeypatch.setattr(tool_bridge, "_call_tool_impl", execute)
    tool_bridge._TOOL_CALL_IDEMPOTENCY_CACHE.clear()
    app = FastAPI()
    app.include_router(tool_bridge.router)
    app.include_router(personalization.router)
    app.dependency_overrides[tool_bridge.get_ctx] = lambda: SimpleNamespace()
    @app.get("/owner-only", dependencies=[Depends(auth.require_token)])
    def owner_only():
        return {"ok": True}
    with TestClient(app) as client:
        response = client.post("/personalization/admin/sessions/reader-session/tool-credential",
                               headers={"X-AdaOS-Token": "owner-secret"}, json={"skill_name": "sample"})
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        token = response.json()["access_token"]
        yield client, access, {"Authorization": f"Bearer {token}"}, calls
    tool_bridge._TOOL_CALL_IDEMPOTENCY_CACHE.clear()


def test_http_scope_and_grants_are_checked_before_execution_and_cache(ingress):
    client, access, headers, calls = ingress
    body = {"tool": "sample:read", "idempotency_key": "same-request", "arguments": {"actor": {"id": "owner", "role": "owner"}}}
    assert client.post("/tools/call", headers=headers, json=body).json()["actor"] == "session:reader-session"
    replay = client.post("/tools/call", headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.headers["X-AdaOS-Idempotency-Replay"] == "1"
    assert len(calls) == 1
    access.revoke_grant("read", actor=access.owner)
    assert client.post("/tools/call", headers=headers, json=body).status_code == 403
    assert len(calls) == 1


def test_http_reader_denies_write_but_current_writer_grant_allows_it(ingress):
    client, access, headers, calls = ingress
    body = {"tool": "sample:write", "idempotency_key": "write", "intent": "read",
            "arguments": {"role": "owner", "side_effect_class": "safe"}}
    assert client.post("/tools/call", headers=headers, json=body).status_code == 403
    assert calls == []
    access.store.update_grant("read", {"capabilities": ["workspace.read", "workspace.write"]})
    body.pop("intent")
    assert client.post("/tools/call", headers=headers, json=body).status_code == 200
    access.store.update_grant("read", {"capabilities": ["workspace.read"]})
    assert client.post("/tools/call", headers=headers, json=body).status_code == 403
    assert len(calls) == 1


@pytest.mark.parametrize("body", [
    {"tool": "other:read"}, {"tool": "sample:read", "dev": True},
    {"tool": "sample:read", "context": {"webspace_id": "dev"}},
    {"tool": "sample:read", "arguments": {"webspace_id": ""}, "context": {"webspace_id": "dev"}},
    {"tool": "sample:get_undeclared", "intent": "read"}, {"tool": ":read"},
])
def test_http_tool_credential_cannot_expand_scope(ingress, body):
    client, _, headers, calls = ingress
    assert client.post("/tools/call", headers=headers, json=body).status_code == 403
    assert not calls


def test_http_credential_does_not_authorize_node_admin_or_reissue_itself(ingress):
    client, _, headers, _ = ingress
    assert client.get("/owner-only", headers=headers).status_code == 401
    assert client.post("/personalization/admin/sessions/reader-session/tool-credential", headers=headers,
                       json={"skill_name": "other"}).status_code == 401
    raw = headers["Authorization"].removeprefix("Bearer ")
    assert client.post("/tools/call", params={"token": raw}, json={"tool": "sample:read"}).status_code == 401
    assert client.post("/tools/call", headers={"X-AdaOS-Token": raw}, json={"tool": "sample:read"}).status_code == 401


def test_http_session_revocation_precedes_idempotent_replay(ingress):
    client, access, headers, calls = ingress
    body = {"tool": "sample:read", "idempotency_key": "revoke"}
    assert client.post("/tools/call", headers=headers, json=body).status_code == 200
    access.revoke_session("reader-session", actor=access.owner)
    assert client.post("/tools/call", headers=headers, json=body).status_code == 401
    assert len(calls) == 1


@pytest.mark.parametrize("transport", ["header", "bearer", "query"])
def test_http_owner_keeps_existing_transports_and_separate_replay(ingress, transport):
    client, access, reader_headers, calls = ingress
    body = {"tool": "sample:read", "idempotency_key": "owner-reader"}
    options = ({"headers": {"X-AdaOS-Token": "owner-secret"}} if transport == "header" else
               {"headers": {"Authorization": "Bearer owner-secret"}} if transport == "bearer" else
               {"params": {"token": "owner-secret"}})
    owner = client.post("/tools/call", json=body, **options)
    assert owner.status_code == 200, owner.text
    assert owner.json()["actor"] == access.owner.ref()
    assert owner.json()["scope"] is None
    assert client.post("/tools/call", json=body, **options).headers["X-AdaOS-Idempotency-Replay"] == "1"
    reader = client.post("/tools/call", json=body, headers=reader_headers)
    assert reader.status_code == 200
    assert reader.json()["actor"] == "session:reader-session"
    assert not reader.headers.get("X-AdaOS-Idempotency-Replay")
    assert len(calls) == 2


def test_scoped_tool_proxy_cannot_replace_session_identity_with_node_owner(monkeypatch):
    def unexpected():
        pytest.fail("routing must not begin before the scoped forwarding guard")
    monkeypatch.setattr(tool_bridge, "get_directory", unexpected)
    with verified_caller(SubjectRef("session", "reader"), ScopeRef("skill", "sample")):
        with pytest.raises(HTTPException) as error:
            asyncio.run(tool_bridge._proxy_tool_call_to_node(
                conf=SimpleNamespace(token="owner-secret"), request=SimpleNamespace(headers={}),
                body=tool_bridge.ToolCall(tool="sample:read"), payload={}, target_node_id="remote"))
        assert error.value.status_code == 403
    assert current_caller() is None
    assert current_caller_scope() is None
