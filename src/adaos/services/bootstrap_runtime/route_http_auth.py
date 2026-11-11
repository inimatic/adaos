"""Preserve Root caller admission across the trusted NATS-to-loopback hop."""

from __future__ import annotations

from typing import Any


def _headers(headers: Any) -> dict[str, Any]:
    if not isinstance(headers, dict):
        return {}
    return {str(key).strip().lower(): value for key, value in headers.items()}


def _desktop_credential(headers: dict[str, Any]) -> str | None:
    if "x-adaos-desktop-credential" in headers:
        value = headers["x-adaos-desktop-credential"]
    else:
        authorization = headers.get("authorization")
        if not isinstance(authorization, str) or not authorization.lower().startswith("bearer adaos-desktop-v1."):
            return None
        value = authorization[7:]
    # Explicit malformed authority must reach Core as denied authority, never
    # disappear and select the bridge's node-owner token instead.
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        return "adaos-desktop-v1.invalid"
    return value.strip()


def _owner_route(headers: dict[str, Any]) -> bool:
    authenticated = headers.get("x-adaos-route-authenticated")
    principal = headers.get("x-adaos-route-principal")
    # Unmarked traffic is trusted Root control-plane traffic on the NATS
    # subject. Browser HTTP carries explicit admission written by Root.
    return (authenticated is None and principal is None) or (
        authenticated == "1" and principal == "verified"
    )


def local_http_headers(headers: Any, *, token_local: str | None, zone_id: str = "") -> dict[str, str]:
    incoming = _headers(headers)
    credential = _desktop_credential(incoming)
    result = {"X-AdaOS-Route": "root-routed"}
    if credential is not None:
        result["X-AdaOS-Desktop-Credential"] = credential
    elif token_local and _owner_route(incoming):
        result["X-AdaOS-Token"] = str(token_local)
    if zone_id:
        result["X-AdaOS-Zone"] = zone_id
    content_type = incoming.get("content-type")
    if isinstance(content_type, str) and content_type:
        result["Content-Type"] = content_type
    for name in ("x-adaos-route-authenticated", "x-adaos-development-access"):
        if incoming.get(name) in ("0", "1"):
            result[name] = incoming[name]
    if "x-adaos-route-principal" in incoming or incoming.get("x-adaos-route-authenticated") == "1":
        result["x-adaos-route-principal"] = (
            "verified" if incoming.get("x-adaos-route-principal") == "verified" else "unverified"
        )
    return result


def can_reply_inline(method: str, path: str, headers: Any) -> bool:
    if method.upper() not in {"GET", "HEAD"}:
        return False
    normalized = path.split("?", 1)[0].rstrip("/") or "/"
    if normalized in {"/api/ping", "/healthz"}:
        return True
    if normalized != "/api/node/status":
        return False
    incoming = _headers(headers)
    return _desktop_credential(incoming) is None and _owner_route(incoming)
