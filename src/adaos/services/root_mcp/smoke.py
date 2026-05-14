from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import httpx


@dataclass(slots=True)
class RootMcpSmokeHttpResult:
    status_code: int | None
    payload: Any | None = None
    body: str | None = None
    error: str | None = None


@dataclass(slots=True)
class RootMcpSmokeStep:
    name: str
    ok: bool
    classification: str
    method: str
    url: str
    status_code: int | None = None
    error: str | None = None
    jsonrpc_error: Any | None = None


def normalize_mcp_http_url(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError("MCP HTTP URL is required")
    return token.rstrip("/")


def classify_mcp_smoke_response(result: RootMcpSmokeHttpResult) -> tuple[bool, str, Any | None]:
    if result.status_code is None:
        return False, "network_error", None
    status = int(result.status_code)
    if status in {401, 403}:
        return False, "auth_failed", None
    if status == 404:
        return False, "endpoint_not_found", None
    if 500 <= status <= 599:
        return False, "upstream_unavailable", None
    if status >= 400:
        return False, "http_error", None
    payload = result.payload
    if isinstance(payload, Mapping) and payload.get("error") is not None:
        return False, "jsonrpc_error", payload.get("error")
    return True, "ok", None


def _http_request(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    timeout: float,
    json_payload: Mapping[str, Any] | None = None,
) -> RootMcpSmokeHttpResult:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.request(method, url, headers=dict(headers), json=json_payload)
    except httpx.RequestError as exc:
        return RootMcpSmokeHttpResult(status_code=None, error=f"{type(exc).__name__}: {exc}")
    payload: Any | None = None
    body: str | None = None
    if response.content:
        try:
            payload = response.json()
        except ValueError:
            body = response.text[:500]
    return RootMcpSmokeHttpResult(status_code=response.status_code, payload=payload, body=body)


def _make_step(
    *,
    name: str,
    method: str,
    url: str,
    result: RootMcpSmokeHttpResult,
) -> RootMcpSmokeStep:
    ok, classification, jsonrpc_error = classify_mcp_smoke_response(result)
    return RootMcpSmokeStep(
        name=name,
        ok=ok,
        classification=classification,
        method=method,
        url=url,
        status_code=result.status_code,
        error=result.error or (result.body if not ok and result.body else None),
        jsonrpc_error=jsonrpc_error,
    )


def run_root_mcp_smoke(
    *,
    mcp_http_url: str,
    bearer_token: str,
    timeout: float = 10.0,
    tool_name: str | None = "get_status",
) -> dict[str, Any]:
    url = normalize_mcp_http_url(mcp_http_url)
    bearer = str(bearer_token or "").strip()
    if not bearer:
        raise ValueError("Bearer token is required")
    headers = {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}
    steps: list[RootMcpSmokeStep] = []

    foundation_url = f"{url}/foundation"
    steps.append(
        _make_step(
            name="foundation",
            method="GET",
            url=foundation_url,
            result=_http_request(method="GET", url=foundation_url, headers=headers, timeout=timeout),
        )
    )

    initialize_payload = {
        "jsonrpc": "2.0",
        "id": "initialize",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "adaos-mcp-smoke", "version": "1.0"},
        },
    }
    steps.append(
        _make_step(
            name="initialize",
            method="POST",
            url=url,
            result=_http_request(method="POST", url=url, headers=headers, timeout=timeout, json_payload=initialize_payload),
        )
    )

    tools_payload = {"jsonrpc": "2.0", "id": "tools-list", "method": "tools/list", "params": {}}
    steps.append(
        _make_step(
            name="tools/list",
            method="POST",
            url=url,
            result=_http_request(method="POST", url=url, headers=headers, timeout=timeout, json_payload=tools_payload),
        )
    )

    normalized_tool_name = str(tool_name or "").strip()
    if normalized_tool_name:
        tool_payload = {
            "jsonrpc": "2.0",
            "id": f"tools-call-{normalized_tool_name}",
            "method": "tools/call",
            "params": {"name": normalized_tool_name, "arguments": {}},
        }
        steps.append(
            _make_step(
                name=f"tools/call:{normalized_tool_name}",
                method="POST",
                url=url,
                result=_http_request(method="POST", url=url, headers=headers, timeout=timeout, json_payload=tool_payload),
            )
        )

    ok = all(step.ok for step in steps)
    classifications = sorted({step.classification for step in steps if not step.ok})
    return {
        "ok": ok,
        "mcp_http_url": url,
        "classification": "ok" if ok else (classifications[0] if len(classifications) == 1 else "mixed_failure"),
        "steps": [asdict(step) for step in steps],
    }


__all__ = [
    "RootMcpSmokeHttpResult",
    "RootMcpSmokeStep",
    "classify_mcp_smoke_response",
    "normalize_mcp_http_url",
    "run_root_mcp_smoke",
]
