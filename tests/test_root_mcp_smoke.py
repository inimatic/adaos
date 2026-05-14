from __future__ import annotations

from adaos.services.root_mcp import smoke as smoke_mod


def test_root_mcp_smoke_classifies_proxy_502(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http_request(*, method, url, headers, timeout, json_payload=None):
        calls.append((method, url))
        assert headers["Authorization"] == "Bearer bearer-123"
        return smoke_mod.RootMcpSmokeHttpResult(status_code=502, body="Bad Gateway")

    monkeypatch.setattr(smoke_mod, "_http_request", fake_http_request)

    result = smoke_mod.run_root_mcp_smoke(
        mcp_http_url="https://ru.api.inimatic.com/v1/root/mcp",
        bearer_token="bearer-123",
    )

    assert result["ok"] is False
    assert result["classification"] == "upstream_unavailable"
    assert [step["classification"] for step in result["steps"]] == [
        "upstream_unavailable",
        "upstream_unavailable",
        "upstream_unavailable",
        "upstream_unavailable",
    ]
    assert calls[0] == ("GET", "https://ru.api.inimatic.com/v1/root/mcp/foundation")
    assert calls[1] == ("POST", "https://ru.api.inimatic.com/v1/root/mcp")


def test_root_mcp_smoke_classifies_auth_and_jsonrpc_errors() -> None:
    auth_ok, auth_classification, auth_error = smoke_mod.classify_mcp_smoke_response(
        smoke_mod.RootMcpSmokeHttpResult(status_code=401, payload={"detail": "unauthorized"})
    )
    rpc_ok, rpc_classification, rpc_error = smoke_mod.classify_mcp_smoke_response(
        smoke_mod.RootMcpSmokeHttpResult(status_code=200, payload={"jsonrpc": "2.0", "id": "1", "error": {"code": -32601}})
    )

    assert auth_ok is False
    assert auth_classification == "auth_failed"
    assert auth_error is None
    assert rpc_ok is False
    assert rpc_classification == "jsonrpc_error"
    assert rpc_error == {"code": -32601}
