from __future__ import annotations

from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from adaos.apps.api import auth


def test_expected_token_prefers_runtime_env_over_ctx_config(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_TOKEN", "runtime-env-token")
    monkeypatch.setattr(auth, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(token="stale-config-token")))

    assert auth._expected_token() == "runtime-env-token"


def test_ensure_token_accepts_runtime_env_token_when_ctx_config_is_stale(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_TOKEN", "runtime-env-token")
    monkeypatch.setattr(auth, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(token="stale-config-token")))

    auth.ensure_token("runtime-env-token")


def test_ensure_token_rejects_wrong_token(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_TOKEN", "runtime-env-token")
    monkeypatch.setattr(auth, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(token="stale-config-token")))

    try:
        auth.ensure_token("wrong-token")
    except HTTPException as exc:
        assert exc.status_code == 401
        assert exc.detail == "Invalid or missing X-AdaOS-Token"
    else:
        raise AssertionError("ensure_token() must reject unexpected tokens")


def test_require_token_accepts_x_adaos_token_header_via_request_headers(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_TOKEN", "dev-local-token")
    monkeypatch.setattr(auth, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(token="stale-config-token")))

    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(auth.require_token)])
    async def _protected() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/protected", headers={"X-AdaOS-Token": "dev-local-token"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_tool_ingress_binds_verified_owner_not_argument_identity(monkeypatch):
    import asyncio
    from adaos.apps.api import tool_bridge
    from adaos.services.policy.caller import current_caller

    monkeypatch.setenv("ADAOS_TOKEN", "owner-secret")
    monkeypatch.setattr("adaos.services.personalization_runtime.current_user_id", lambda: "owner")
    app = FastAPI()
    app.include_router(tool_bridge.router)
    app.dependency_overrides[tool_bridge.get_ctx] = lambda: SimpleNamespace()

    async def inspect_call(_body, _request, _response, _ctx):
        actor = await asyncio.to_thread(current_caller)
        return {"caller": actor.ref() if actor else None}

    monkeypatch.setattr(tool_bridge, "_call_tool_with_identity", inspect_call)
    client = TestClient(app)
    payload = {"tool": "sample:save", "arguments": {"actor": "user:forged", "role": "owner"},
               "context": {"principal": {"kind": "user", "id": "forged"}}}
    assert client.post("/tools/call", json=payload).status_code == 401
    response = client.post("/tools/call", json=payload, headers={"Authorization": "Bearer owner-secret"})
    assert response.status_code == 200
    assert response.json() == {"caller": "user:owner"}
    assert current_caller() is None
