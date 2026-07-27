from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from adaos.services.bounded_io import env_int, rotate_file_if_needed
from adaos.services.nats_config import (
    normalize_nats_ws_url,
    nats_url_uses_websocket,
    order_nats_ws_candidates,
    public_nats_ws_api,
)
from adaos.services.node_runtime_state import load_nats_runtime_config, migrate_legacy_nats_runtime_config
from adaos.services.nats_ws_transport import (
    _set_tcp_keepalive,
    _ws_heartbeat_s_from_env,
    _ws_max_queue_from_env,
    _ws_proxy_from_env,
)
from adaos.services.runtime_dotenv import merged_runtime_dotenv_env
from adaos.services.runtime_paths import current_base_dir, current_repo_root

NATS_PING = b"PING\r\n"
NATS_PONG = b"PONG\r\n"
_realtime_remote_quarantine_until: dict[str, float] = {}
_ROUTE_TUNNEL_RUNTIME_STATE: dict[str, dict[str, Any]] = {
    "ws": {
        "listener_ready": False,
        "listener_host": None,
        "listener_port": None,
        "listener_url": None,
        "upstream_host": None,
        "upstream_port": None,
        "upstream_url": None,
    },
    "yws": {
        "listener_ready": False,
        "listener_host": None,
        "listener_port": None,
        "listener_url": None,
        "upstream_host": None,
        "upstream_port": None,
        "upstream_url": None,
    },
}
_MEDIA_PROXY_RUNTIME_STATE: dict[str, Any] = {
    "listener_ready": False,
    "listener_host": None,
    "listener_port": None,
    "listener_url": None,
    "public_bases": [],
    "last_error": None,
}


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    try:
        text = str(value).strip().lower()
    except Exception:
        return default
    if not text:
        return default
    return text in {"1", "true", "on", "yes"}


def _realtime_remote_quarantine_s() -> float:
    raw = os.getenv("ADAOS_REALTIME_REMOTE_QUARANTINE_S")
    try:
        value = float(str(raw or "60").strip() or "60")
    except Exception:
        value = 60.0
    if value < 5.0:
        value = 5.0
    return value


def _realtime_remote_connect_retry_initial_s() -> float:
    raw = os.getenv("ADAOS_REALTIME_REMOTE_CONNECT_RETRY_INITIAL_S")
    try:
        value = float(str(raw or "1.0").strip() or "1.0")
    except Exception:
        value = 1.0
    if value < 0.05:
        value = 0.05
    return value


def _realtime_remote_connect_retry_max_s() -> float:
    raw = os.getenv("ADAOS_REALTIME_REMOTE_CONNECT_RETRY_MAX_S")
    try:
        value = float(str(raw or "15.0").strip() or "15.0")
    except Exception:
        value = 15.0
    return max(_realtime_remote_connect_retry_initial_s(), value)


def _realtime_remote_connect_retry_factor() -> float:
    raw = os.getenv("ADAOS_REALTIME_REMOTE_CONNECT_RETRY_FACTOR")
    try:
        value = float(str(raw or "1.6").strip() or "1.6")
    except Exception:
        value = 1.6
    if value < 1.0:
        value = 1.0
    return value


def _realtime_remote_quarantine_key(url: str) -> str:
    try:
        parsed = urlparse(str(url))
        base = urlunparse(parsed._replace(query="", fragment=""))
    except Exception:
        base = str(url or "").strip()
    normalized = normalize_nats_ws_url(base, fallback=None)
    return str(normalized or base or "").strip()


def _should_quarantine_realtime_remote(details: str) -> bool:
    text = str(details or "").strip().lower()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "unexpected eof",
            "connectionclosederror",
            "connection closed",
            "no close frame received or sent",
            "close code=1006",
            "code=1006",
            "connection reset",
            "winerror 10054",
        )
    )


def _quarantine_realtime_remote(url: str, *, details: str | None = None) -> None:
    key = _realtime_remote_quarantine_key(url)
    if not key:
        return
    _realtime_remote_quarantine_until[key] = time.monotonic() + _realtime_remote_quarantine_s()


def _available_realtime_remote_candidates() -> list[str]:
    candidates = resolve_realtime_remote_candidates()
    if not candidates:
        return []
    now_m = time.monotonic()
    available: list[str] = []
    quarantined: list[tuple[float, int, str]] = []
    for index, candidate in enumerate(candidates):
        until = float(_realtime_remote_quarantine_until.get(_realtime_remote_quarantine_key(candidate), 0.0))
        if now_m >= until:
            available.append(candidate)
            continue
        quarantined.append((until, index, candidate))
    if available:
        return available
    quarantined.sort(key=lambda item: (item[0], item[1]))
    return [candidate for _until, _index, candidate in quarantined] or candidates


def _default_realtime_sidecar_role(role: str | None = None) -> str | None:
    role_norm = str(role or "").strip().lower() or None
    if role_norm:
        return role_norm
    try:
        from adaos.services.agent_context import get_ctx

        ctx = get_ctx()
        cfg = getattr(ctx, "config", None)
        role_norm = str(getattr(cfg, "role", "") or "").strip().lower() or None
        if role_norm:
            return role_norm
    except Exception:
        pass
    return None


def _realtime_sidecar_repo_root() -> Path | None:
    try:
        from adaos.services.agent_context import get_ctx

        ctx = get_ctx()
        repo_root = ctx.paths.repo_root()
        raw = repo_root() if callable(repo_root) else repo_root
        if raw:
            return Path(raw).expanduser().resolve()
    except Exception:
        pass
    return current_repo_root()


def _safe_realtime_relative_base() -> Path:
    try:
        return Path.cwd()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    root = current_repo_root()
    try:
        if root is not None and root.exists():
            return root
    except Exception:
        pass
    return current_base_dir()


def realtime_sidecar_enablement_policy(*, role: str | None = None) -> dict[str, Any]:
    role_norm = _default_realtime_sidecar_role(role)
    default_enabled = role_norm == "hub"
    raw = os.getenv("ADAOS_REALTIME_ENABLE")
    env_var = "ADAOS_REALTIME_ENABLE"
    if raw is None:
        raw = os.getenv("HUB_REALTIME_ENABLE")
        env_var = "HUB_REALTIME_ENABLE"
    if raw is not None:
        enabled = _truthy(raw, default=False)
        value = str(raw).strip()
        return {
            "role": role_norm,
            "enabled": enabled,
            "default_enabled": default_enabled,
            "explicit": True,
            "source": "env_override",
            "env_var": env_var,
            "env_value": value,
            "reason": f"{env_var}={value or '0'}",
        }
    if role_norm == "hub":
        return {
            "role": role_norm,
            "enabled": True,
            "default_enabled": True,
            "explicit": False,
            "source": "role_default",
            "env_var": None,
            "env_value": None,
            "reason": "hub runtimes use sidecar as the default realtime transport",
        }
    if role_norm:
        return {
            "role": role_norm,
            "enabled": False,
            "default_enabled": False,
            "explicit": False,
            "source": "role_default",
            "env_var": None,
            "env_value": None,
            "reason": "non-hub runtimes keep sidecar disabled by default",
        }
    return {
        "role": None,
        "enabled": False,
        "default_enabled": False,
        "explicit": False,
        "source": "role_unresolved",
        "env_var": None,
        "env_value": None,
        "reason": "runtime role is unresolved, so sidecar stays disabled by default",
    }


def realtime_sidecar_enabled(*, role: str | None = None, os_name: str | None = None) -> bool:
    policy = realtime_sidecar_enablement_policy(role=role)
    return bool(policy.get("enabled"))


def realtime_sidecar_host() -> str:
    return str(os.getenv("ADAOS_REALTIME_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"


def realtime_sidecar_port() -> int:
    raw = os.getenv("ADAOS_REALTIME_PORT")
    try:
        port = int(str(raw or "7422").strip() or "7422")
    except Exception:
        port = 7422
    if port <= 0:
        port = 7422
    return port


def realtime_sidecar_local_url() -> str:
    return f"nats://{realtime_sidecar_host()}:{realtime_sidecar_port()}"


def _realtime_sidecar_lifecycle_manager() -> str:
    return "supervisor" if _truthy(os.getenv("ADAOS_SUPERVISOR_ENABLED"), default=False) else "runtime"


def _route_tunnel_runtime_paths() -> dict[str, str]:
    return {
        "ws": "/ws",
        "yws": "/yws",
    }


def _route_tunnel_listener_host() -> str:
    raw = str(os.getenv("ADAOS_REALTIME_ROUTE_PROXY_HOST") or "").strip()
    return raw or realtime_sidecar_host()


def _route_tunnel_listener_port(kind: str) -> int:
    key = str(kind or "").strip().lower()
    env_map = {
        "ws": "ADAOS_REALTIME_ROUTE_WS_PORT",
        "yws": "ADAOS_REALTIME_ROUTE_YWS_PORT",
    }
    default_offsets = {
        "ws": 1,
        "yws": 2,
    }
    raw = os.getenv(env_map.get(key, ""))
    default_port = int(realtime_sidecar_port()) + int(default_offsets.get(key, 0) or 0)
    try:
        port = int(str(raw or default_port).strip() or str(default_port))
    except Exception:
        port = default_port
    if port <= 0:
        port = default_port
    return port


def _route_tunnel_proxy_enabled(*, role: str | None = None) -> bool:
    raw = os.getenv("ADAOS_REALTIME_ROUTE_PROXY_ENABLE")
    if raw is not None:
        return _truthy(raw, default=False)
    return bool(realtime_sidecar_enabled(role=role))


def _media_proxy_enabled(*, role: str | None = None) -> bool:
    raw = os.getenv("ADAOS_REALTIME_MEDIA_PROXY_ENABLE")
    if raw is not None:
        return _truthy(raw, default=False)
    return _truthy(os.getenv("ADAOS_REALTIME_MEDIA_PROXY_DEFAULT_ENABLE"), default=False) and bool(
        realtime_sidecar_enabled(role=role)
    )


def _media_proxy_listener_host() -> str:
    raw = str(os.getenv("ADAOS_REALTIME_MEDIA_PROXY_HOST") or "").strip()
    return raw or realtime_sidecar_host()


def _media_proxy_listener_port() -> int:
    default_port = int(realtime_sidecar_port()) + 3
    raw = os.getenv("ADAOS_REALTIME_MEDIA_PROXY_PORT")
    try:
        port = int(str(raw or default_port).strip() or str(default_port))
    except Exception:
        port = default_port
    if port <= 0:
        port = default_port
    return port


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


def _media_proxy_normalized_base_url(value: str | None) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _media_proxy_public_bases(*, host: str | None = None, port: int | None = None) -> list[str]:
    bases: list[str] = []
    for raw in (
        os.getenv("ADAOS_REALTIME_MEDIA_PUBLIC_BASES"),
        os.getenv("ADAOS_REDEVICE_MEDIA_BASES"),
        os.getenv("ADAOS_MEDIA_DIRECT_BASES"),
    ):
        for value in _split_csv(raw):
            base = _media_proxy_normalized_base_url(value)
            if base and base not in bases:
                bases.append(base)
    if bases:
        return bases
    host_token = str(host or "").strip()
    if not host_token or host_token in {"0.0.0.0", "::", "[::]", "127.0.0.1", "localhost", "::1", "[::1]"}:
        return []
    listener_port = int(port or 0)
    if listener_port <= 0:
        return []
    return [f"http://{host_token}:{listener_port}"]


def _media_proxy_listener_url(*, host: str, port: int) -> str:
    return f"http://{str(host or '').strip() or '127.0.0.1'}:{int(port)}"


def _route_tunnel_upstream_host() -> str:
    dynamic = _route_tunnel_supervisor_runtime_endpoint()
    if dynamic is not None:
        return dynamic[0]
    raw = str(os.getenv("ADAOS_RUNTIME_HOST") or "").strip()
    if raw:
        return raw
    return "127.0.0.1"


def _route_tunnel_upstream_port() -> int:
    dynamic = _route_tunnel_supervisor_runtime_endpoint()
    if dynamic is not None:
        return dynamic[1]
    raw = os.getenv("ADAOS_RUNTIME_PORT")
    try:
        port = int(str(raw or "8777").strip() or "8777")
    except Exception:
        port = 8777
    if port <= 0:
        port = 8777
    return port


def _route_tunnel_supervisor_base_url() -> str | None:
    if str(os.getenv("ADAOS_SUPERVISOR_ENABLED") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    raw = str(os.getenv("ADAOS_SUPERVISOR_URL") or os.getenv("ADAOS_SUPERVISOR_BASE") or "").strip().rstrip("/")
    if raw:
        return raw
    host = str(os.getenv("ADAOS_SUPERVISOR_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = str(os.getenv("ADAOS_SUPERVISOR_PORT") or "8776").strip() or "8776"
    return f"http://{host}:{port}"


def _route_tunnel_runtime_endpoint_from_payload(payload: dict[str, Any] | None) -> tuple[str, int] | None:
    if not isinstance(payload, dict):
        return None
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else payload
    runtime_url = str(runtime.get("runtime_url") or "").strip().rstrip("/")
    if not runtime_url:
        return None
    parsed = urlparse(runtime_url)
    host = str(parsed.hostname or "").strip()
    port = parsed.port
    if not host or not isinstance(port, int) or port <= 0:
        return None
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return host, int(port)


def _route_tunnel_supervisor_state_endpoint() -> tuple[str, int] | None:
    try:
        path = current_base_dir() / "state" / "supervisor" / "runtime.json"
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return None
    endpoint = _route_tunnel_runtime_endpoint_from_payload(payload if isinstance(payload, dict) else {})
    if endpoint is None:
        return None
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else payload
    if isinstance(runtime, dict):
        if runtime.get("desired_running") is False or runtime.get("managed_alive") is False:
            return None
    return endpoint


def _route_tunnel_supervisor_http_endpoint() -> tuple[str, int] | None:
    base = _route_tunnel_supervisor_base_url()
    if not base:
        return None
    timeout_s = 0.25
    try:
        timeout_s = max(0.05, float(os.getenv("ADAOS_REALTIME_ROUTE_SUPERVISOR_TIMEOUT_S", "0.25") or "0.25"))
    except Exception:
        timeout_s = 0.25
    try:
        request = UrlRequest(base + "/api/supervisor/public/update-status", headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, URLError, json.JSONDecodeError, TimeoutError):
        return None
    except Exception:
        return None
    return _route_tunnel_runtime_endpoint_from_payload(payload if isinstance(payload, dict) else {})


def _route_tunnel_supervisor_runtime_endpoint() -> tuple[str, int] | None:
    endpoint = _route_tunnel_supervisor_state_endpoint()
    if endpoint is not None:
        return endpoint
    # Process-local status snapshots must never synchronously call back into
    # the single-threaded supervisor that is currently building the snapshot.
    if str(os.getenv("ADAOS_REALTIME_CHILD") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    return _route_tunnel_supervisor_http_endpoint()


def _route_tunnel_listener_url(*, host: str, port: int, path: str) -> str:
    return f"ws://{str(host or '').strip() or '127.0.0.1'}:{int(port)}{path}"


def _route_tunnel_upstream_url(*, host: str, port: int, path: str) -> str:
    return f"ws://{str(host or '').strip() or '127.0.0.1'}:{int(port)}{path}"


def _route_tunnel_listener_base_url(*, host: str, port: int) -> str:
    return f"ws://{str(host or '').strip() or '127.0.0.1'}:{int(port)}"


def _reset_route_tunnel_runtime_state() -> None:
    paths = _route_tunnel_runtime_paths()
    upstream_host = _route_tunnel_upstream_host()
    upstream_port = _route_tunnel_upstream_port()
    listener_host = _route_tunnel_listener_host()
    for kind, path in paths.items():
        listener_port = _route_tunnel_listener_port(kind)
        entry = _ROUTE_TUNNEL_RUNTIME_STATE.setdefault(kind, {})
        entry.clear()
        entry.update(
            {
                "listener_ready": False,
                "listener_host": listener_host,
                "listener_port": listener_port,
                "listener_url": _route_tunnel_listener_url(host=listener_host, port=listener_port, path=path),
                "upstream_host": upstream_host,
                "upstream_port": upstream_port,
                "upstream_url": _route_tunnel_upstream_url(host=upstream_host, port=upstream_port, path=path),
            }
        )


def _set_route_tunnel_runtime_state(kind: str, **values: Any) -> None:
    key = str(kind or "").strip().lower()
    if key not in _ROUTE_TUNNEL_RUNTIME_STATE:
        return
    entry = _ROUTE_TUNNEL_RUNTIME_STATE.setdefault(key, {})
    entry.update(values)


def _route_tunnel_runtime_state(kind: str) -> dict[str, Any]:
    key = str(kind or "").strip().lower()
    payload = _ROUTE_TUNNEL_RUNTIME_STATE.get(key)
    return dict(payload) if isinstance(payload, dict) else {}


def _reset_media_proxy_runtime_state() -> None:
    listener_host = _media_proxy_listener_host()
    listener_port = _media_proxy_listener_port()
    _MEDIA_PROXY_RUNTIME_STATE.clear()
    _MEDIA_PROXY_RUNTIME_STATE.update(
        {
            "listener_ready": False,
            "listener_host": listener_host,
            "listener_port": listener_port,
            "listener_url": _media_proxy_listener_url(host=listener_host, port=listener_port),
            "public_bases": _media_proxy_public_bases(host=listener_host, port=listener_port),
            "last_error": None,
        }
    )


def _set_media_proxy_runtime_state(**values: Any) -> None:
    _MEDIA_PROXY_RUNTIME_STATE.update(values)


def _media_proxy_runtime_state() -> dict[str, Any]:
    return dict(_MEDIA_PROXY_RUNTIME_STATE)


def realtime_sidecar_route_tunnel_listeners(*, role: str | None = None) -> dict[str, Any]:
    enabled = bool(_route_tunnel_proxy_enabled(role=role))
    lifecycle_manager = _realtime_sidecar_lifecycle_manager()
    upstream_host = _route_tunnel_upstream_host()
    upstream_port = _route_tunnel_upstream_port()
    listener_host = _route_tunnel_listener_host()
    paths = _route_tunnel_runtime_paths()
    listeners: dict[str, Any] = {}
    for kind, path in paths.items():
        port = _route_tunnel_listener_port(kind)
        runtime_state = _route_tunnel_runtime_state(kind)
        listener_ready = bool(runtime_state.get("listener_ready"))
        listener_url = str(runtime_state.get("listener_url") or "").strip() or _route_tunnel_listener_url(
            host=listener_host,
            port=port,
            path=path,
        )
        listeners[kind] = {
            "enabled": enabled,
            "listener_host": str(runtime_state.get("listener_host") or listener_host).strip() or listener_host,
            "listener_port": int(runtime_state.get("listener_port") or port),
            "listener_url": listener_url,
            "listener_ready": listener_ready,
            "upstream_host": upstream_host,
            "upstream_port": int(upstream_port),
            "upstream_url": _route_tunnel_upstream_url(host=upstream_host, port=upstream_port, path=path),
            "upstream_path": path,
            "upstream_configured": int(upstream_port) > 0,
            "lifecycle_manager": lifecycle_manager,
        }
    return listeners


def realtime_sidecar_route_tunnel_ws_bases(*, path: str | None = None, role: str | None = None) -> list[str]:
    path_norm = str(path or "").strip().lower()
    if path_norm.startswith("/yws"):
        ordered_kinds = ["yws"]
    elif path_norm.startswith("/ws"):
        ordered_kinds = ["ws"]
    else:
        return []
    listeners = realtime_sidecar_route_tunnel_listeners(role=role)
    bases: list[str] = []
    seen: set[str] = set()
    for kind in ordered_kinds:
        listener = listeners.get(kind)
        if not isinstance(listener, dict):
            continue
        if not bool(listener.get("enabled")) or not bool(listener.get("upstream_configured")):
            continue
        if not bool(listener.get("listener_ready")):
            continue
        base = _route_tunnel_listener_base_url(
            host=str(listener.get("listener_host") or "127.0.0.1").strip() or "127.0.0.1",
            port=int(listener.get("listener_port") or 0),
        )
        if not base or base in seen:
            continue
        seen.add(base)
        bases.append(base)
    return bases


def realtime_sidecar_route_tunnel_contract(*, role: str | None = None) -> dict[str, Any]:
    enabled = bool(realtime_sidecar_enabled(role=role))
    lifecycle_manager = _realtime_sidecar_lifecycle_manager()
    listeners = realtime_sidecar_route_tunnel_listeners(role=role)

    def _entry(kind: str, *, logical_channels: list[str], ownership_blocker: str) -> dict[str, Any]:
        listener = listeners.get(kind) if isinstance(listeners.get(kind), dict) else {}
        listener_ready = bool(listener.get("listener_ready"))
        listener_enabled = bool(listener.get("enabled"))
        upstream_configured = bool(listener.get("upstream_configured"))
        handoff_ready = enabled and listener_enabled and upstream_configured and listener_ready
        blockers: list[str] = []
        if handoff_ready:
            blockers = []
        elif not enabled:
            blockers.append(ownership_blocker)
            blockers.append("realtime sidecar is disabled")
        elif not listener_enabled:
            blockers.append(ownership_blocker)
            blockers.append("sidecar local route proxy listeners are disabled")
        elif not upstream_configured:
            blockers.append(ownership_blocker)
            blockers.append("local runtime websocket proxy target is not configured")
        else:
            blockers.append(ownership_blocker)
            blockers.append("sidecar local websocket proxy listener is not running yet")
        current_owner = "sidecar" if handoff_ready else "runtime"
        current_support = "disabled" if not enabled else ("ready" if handoff_ready else "planned")
        return {
            "current_owner": current_owner,
            "planned_owner": "sidecar",
            "migration_phase": "phase_2_route_tunnel_ownership",
            "logical_channels": logical_channels,
            "current_support": current_support,
            "delegation_mode": "local_ws_proxy",
            "listener_ready": listener_ready,
            "handoff_ready": handoff_ready,
            "listener": {
                "host": listener.get("listener_host"),
                "port": listener.get("listener_port"),
                "url": listener.get("listener_url"),
            },
            "upstream": {
                "host": listener.get("upstream_host"),
                "port": listener.get("upstream_port"),
                "url": listener.get("upstream_url"),
            },
            "blockers": blockers,
        }

    ws_entry = _entry(
        "ws",
        logical_channels=[
            "hub_member.command",
            "hub_member.event",
            "hub_member.presence",
        ],
        ownership_blocker="browser route websocket still terminates in the runtime FastAPI app",
    )
    yws_entry = _entry(
        "yws",
        logical_channels=[
            "hub_member.sync",
        ],
        ownership_blocker="Yjs websocket/session ownership still lives in the runtime gateway",
    )
    ready_total = sum(
        1
        for item in (ws_entry, yws_entry)
        if isinstance(item, dict) and bool(item.get("handoff_ready"))
    )
    current_support = "disabled" if not enabled else ("ready" if ready_total >= 2 else "planned")
    return {
        "current_support": current_support,
        "lifecycle_manager": lifecycle_manager,
        "ownership_boundary": "transport_only",
        "ws": ws_entry,
        "yws": yws_entry,
    }


def realtime_sidecar_media_proxy_contract(*, role: str | None = None) -> dict[str, Any]:
    enabled = bool(_media_proxy_enabled(role=role))
    lifecycle_manager = _realtime_sidecar_lifecycle_manager()
    listener_host = _media_proxy_listener_host()
    listener_port = _media_proxy_listener_port()
    runtime_state = _media_proxy_runtime_state()
    listener_ready = bool(runtime_state.get("listener_ready"))
    host = str(runtime_state.get("listener_host") or listener_host).strip() or listener_host
    port = int(runtime_state.get("listener_port") or listener_port)
    listener_url = str(runtime_state.get("listener_url") or "").strip() or _media_proxy_listener_url(host=host, port=port)
    raw_public_bases = runtime_state.get("public_bases")
    if not isinstance(raw_public_bases, list) or not raw_public_bases:
        raw_public_bases = _media_proxy_public_bases(host=host, port=port)
    public_bases = [str(item).strip().rstrip("/") for item in raw_public_bases if str(item or "").strip()]
    handoff_ready = enabled and listener_ready
    blockers: list[str] = []
    if not enabled:
        blockers.append("sidecar media proxy is disabled")
    elif not listener_ready:
        blockers.append("sidecar media proxy listener is not running yet")
    if enabled and listener_ready and not public_bases:
        blockers.append("no endpoint-reachable media base is published")
    current_support = "disabled" if not enabled else ("ready" if handoff_ready else "planned")
    return {
        "current_support": current_support,
        "lifecycle_manager": lifecycle_manager,
        "current_owner": "sidecar" if handoff_ready else "runtime",
        "planned_owner": "sidecar",
        "migration_phase": "phase_3_endpoint_media_http_proxy",
        "ownership_boundary": "media_content_read_only",
        "delegation_mode": "local_http_media_proxy",
        "listener_ready": listener_ready,
        "handoff_ready": handoff_ready,
        "listener": {
            "host": host,
            "port": port,
            "url": listener_url,
        },
        "public_bases": public_bases,
        "route_paths": [
            "/api/node/media/files/content/{filename}",
            "/media/files/content/{filename}",
            "/api/node/media-indexer/content/{playback_id}",
            "/media/media-indexer/content/{playback_id}",
        ],
        "auth": {
            "query_token": True,
            "x_adaos_token": True,
            "authorization_bearer": True,
        },
        "cache_policy": "private_max_age_3600",
        "range_requests": True,
        "blockers": blockers,
        "last_error": runtime_state.get("last_error"),
    }


def _media_proxy_expected_token() -> str:
    raw = str(os.getenv("ADAOS_TOKEN") or "").strip()
    if raw:
        return raw
    try:
        from adaos.services.agent_context import get_ctx

        return str(get_ctx().config.token or "dev-local-token").strip() or "dev-local-token"
    except Exception:
        return "dev-local-token"


def _media_proxy_presented_token(*, headers: dict[str, str], query: dict[str, str]) -> str | None:
    authorization = str(headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    header_token = str(headers.get("x-adaos-token") or "").strip()
    if header_token:
        return header_token
    query_token = str(query.get("token") or "").strip()
    return query_token or None


def _media_proxy_token_ok(*, headers: dict[str, str], query: dict[str, str]) -> bool:
    return _media_proxy_presented_token(headers=headers, query=query) == _media_proxy_expected_token()


def _media_proxy_file_path(filename: str) -> Path:
    from adaos.services.media_library import MEDIA_SKILL_NAME, media_file_path, sanitize_media_filename
    from adaos.services.skill.runtime_env import SkillRuntimeEnvironment

    try:
        return media_file_path(filename)
    except RuntimeError as exc:
        if "AgentContext is not initialized" not in str(exc):
            raise
    name = sanitize_media_filename(filename)
    env = SkillRuntimeEnvironment(
        skills_root=current_base_dir() / "workspace" / "skills",
        skill_name=MEDIA_SKILL_NAME,
    )
    active_version = env.resolve_active_version()
    candidates: list[Path] = []
    if active_version:
        candidates.append(env.files_dir(active_version) / name)
    candidates.append(env.runtime_root / "data" / "files" / name)
    for version in env.list_versions():
        candidate = env.files_dir(version) / name
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else env.runtime_root / "data" / "files" / name


def _media_proxy_guess_media_type(filename: str) -> str:
    from adaos.services.media_library import guess_media_type

    return guess_media_type(filename)


def realtime_sidecar_log_path() -> Path:
    raw = str(os.getenv("ADAOS_REALTIME_LOG", ".adaos/diagnostics/realtime_sidecar.log") or "").strip()
    path = Path(raw)
    if not path.is_absolute():
        if path.parts and path.parts[0] == ".adaos":
            path = current_base_dir().joinpath(*path.parts[1:])
        else:
            path = _safe_realtime_relative_base() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def realtime_sidecar_diag_path() -> Path:
    raw = str(os.getenv("ADAOS_REALTIME_DIAG_FILE", ".adaos/diagnostics/realtime_sidecar.jsonl") or "").strip()
    path = Path(raw)
    if not path.is_absolute():
        if path.parts and path.parts[0] == ".adaos":
            path = current_base_dir().joinpath(*path.parts[1:])
        else:
            path = _safe_realtime_relative_base() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _realtime_sidecar_log_max_bytes() -> int:
    return env_int("ADAOS_REALTIME_LOG_MAX_BYTES", 32 * 1024 * 1024, minimum=0)


def _realtime_sidecar_diag_max_bytes() -> int:
    return env_int("ADAOS_REALTIME_DIAG_MAX_BYTES", 32 * 1024 * 1024, minimum=0)


def _realtime_sidecar_log_backups() -> int:
    return env_int("ADAOS_REALTIME_LOG_BACKUPS", 5, minimum=0)


def _realtime_sidecar_diag_backups() -> int:
    return env_int("ADAOS_REALTIME_DIAG_BACKUPS", 5, minimum=0)


def _rotate_realtime_sidecar_log_if_needed(path: Path) -> bool:
    return rotate_file_if_needed(
        path,
        max_bytes=_realtime_sidecar_log_max_bytes(),
        backup_count=_realtime_sidecar_log_backups(),
    )


def _rotate_realtime_sidecar_diag_if_needed(path: Path) -> bool:
    return rotate_file_if_needed(
        path,
        max_bytes=_realtime_sidecar_diag_max_bytes(),
        backup_count=_realtime_sidecar_diag_backups(),
    )


def _host_matches_listener(host: str, other: str | None) -> bool:
    target = str(host or "").strip().lower()
    current = str(other or "").strip().lower()
    if not target:
        return not current
    if not current:
        return False
    if target == current:
        return True
    local_any = {"0.0.0.0", "::", "[::]"}
    loopbacks = {"127.0.0.1", "::1", "localhost"}
    if target in loopbacks and (current in loopbacks or current in local_any):
        return True
    return False


def _cmdline_option_value(cmdline: list[str], option: str) -> str | None:
    opt = str(option or "").strip().lower()
    if not opt:
        return None
    for idx, part in enumerate(cmdline):
        item = str(part or "").strip()
        lower = item.lower()
        if lower == opt:
            if idx + 1 < len(cmdline):
                value = str(cmdline[idx + 1] or "").strip()
                return value or None
            return None
        prefix = f"{opt}="
        if lower.startswith(prefix):
            value = item[len(prefix) :].strip()
            return value or None
    return None


def _process_looks_like_adaos_realtime(proc: Any) -> bool:
    try:
        cmdline = [str(part).lower() for part in proc.cmdline()]
    except Exception:
        return False
    joined = " ".join(cmdline)
    if "adaos.services.realtime_sidecar" in joined:
        return True
    return "adaos" in joined and "realtime" in joined and "serve" in joined


def _process_matches_realtime_bind(proc: Any, host: str, port: int) -> bool:
    try:
        cmdline = [str(part) for part in proc.cmdline()]
    except Exception:
        return False
    if not _process_looks_like_adaos_realtime(proc):
        return False
    raw_port = _cmdline_option_value(cmdline, "--port")
    try:
        cmd_port = int(str(raw_port or "").strip() or "7422")
    except Exception:
        return False
    if cmd_port != int(port):
        return False
    cmd_host = _cmdline_option_value(cmdline, "--host") or "127.0.0.1"
    return _host_matches_listener(host, cmd_host)


def _find_realtime_listener_pid(host: str, port: int) -> int | None:
    try:
        import psutil
    except Exception:
        return None
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN:
                continue
            laddr = getattr(conn, "laddr", None)
            if not laddr or int(getattr(laddr, "port", 0) or 0) != int(port):
                continue
            listener_host = getattr(laddr, "ip", None) or getattr(laddr, "host", None)
            if not _host_matches_listener(host, listener_host):
                continue
            pid = int(conn.pid or 0)
            if pid > 0:
                return pid
    except Exception:
        return None
    return None


def _terminate_process_tree(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        import psutil
    except Exception:
        return False
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return False
    try:
        children = proc.children(recursive=True)
    except psutil.Error:
        children = []
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.Error:
            pass
    psutil.wait_procs(children, timeout=3.0)
    for child in children:
        try:
            if child.is_running():
                child.kill()
        except psutil.Error:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=5.0)
    except psutil.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=3.0)
        except psutil.Error:
            pass
    except psutil.Error:
        pass
    return True


def _replace_existing_realtime_listener(host: str, port: int) -> bool:
    try:
        import psutil
    except Exception:
        return False
    pid = _find_realtime_listener_pid(host, port)
    if not pid or pid == os.getpid():
        return False
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return False
    if not _process_matches_realtime_bind(proc, host, port):
        return False
    if not _terminate_process_tree(pid):
        return False
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        owner_pid = _find_realtime_listener_pid(host, port)
        if not owner_pid or owner_pid == os.getpid():
            return True
        time.sleep(0.1)
    return False


def _realtime_ws_heartbeat_s() -> float | None:
    raw = os.getenv("ADAOS_REALTIME_WS_HEARTBEAT_S")
    if raw is None:
        return None
    try:
        value = float(str(raw).strip() or "0")
    except Exception:
        value = 0.0
    if value <= 0.0:
        return None
    if value < 5.0:
        value = 5.0
    return value


def _realtime_ws_max_queue() -> int | None:
    raw = os.getenv("ADAOS_REALTIME_WS_MAX_QUEUE")
    if raw is None:
        return None
    try:
        value = int(str(raw).strip() or "0")
    except Exception:
        return None
    if value <= 0:
        return None
    return value


def _realtime_ws_proxy() -> str | bool | None:
    raw = os.getenv("ADAOS_REALTIME_WS_PROXY")
    if raw is None:
        return _ws_proxy_from_env()
    try:
        value = str(raw).strip()
    except Exception:
        return _ws_proxy_from_env()
    if not value:
        return None
    lowered = value.lower()
    if lowered in {"auto", "system", "default", "1", "true", "yes"}:
        return True
    if lowered in {"none", "off", "0", "false", "no"}:
        return None
    return value


def _realtime_nats_ping_interval_s() -> float | None:
    raw = os.getenv("ADAOS_REALTIME_NATS_PING_S")
    if raw is None:
        raw = os.getenv("ADAOS_REALTIME_UPSTREAM_NATS_PING_S")
    if raw is None:
        return 15.0
    try:
        value = float(str(raw).strip() or "0")
    except Exception:
        return None
    if value <= 0.0:
        return None
    if value < 5.0:
        value = 5.0
    return value


def _realtime_probe_grace_s() -> float:
    raw = os.getenv("ADAOS_REALTIME_PROBE_GRACE_S")
    try:
        value = float(str(raw if raw is not None else "0.15").strip() or "0.15")
    except Exception:
        value = 0.15
    return max(0.0, min(value, 2.0))


def _realtime_max_local_sessions() -> int:
    raw = os.getenv("ADAOS_REALTIME_MAX_LOCAL_SESSIONS")
    try:
        value = int(str(raw if raw is not None else "2").strip() or "2")
    except Exception:
        value = 2
    return max(1, min(value, 8))


def _ws_socket(ws: Any) -> Any | None:
    try:
        transport = getattr(ws, "transport", None)
        if transport is None:
            protocol = getattr(ws, "protocol", None)
            transport = getattr(protocol, "transport", None)
        if transport is None:
            return None
        return transport.get_extra_info("socket")
    except Exception:
        return None


def _is_normal_ws_close(exc: BaseException) -> bool:
    if type(exc).__name__ == "ConnectionClosedOK":
        return True
    code = getattr(exc, "code", None)
    if code in {1000, 1001}:
        return True
    sent = getattr(exc, "sent", None)
    rcvd = getattr(exc, "rcvd", None)
    sent_code = getattr(sent, "code", None)
    rcvd_code = getattr(rcvd, "code", None)
    return bool(
        sent_code in {1000, 1001}
        and (rcvd_code is None or rcvd_code in {1000, 1001})
    )


def _sidecar_loop_mode() -> str:
    raw = os.getenv("ADAOS_REALTIME_WIN_LOOP")
    if raw is None:
        return "proactor"
    value = str(raw).strip().lower()
    if value in {"selector", "proactor", "auto"}:
        return value
    return "proactor"


def apply_realtime_loop_policy() -> None:
    if os.name != "nt":
        return
    mode = _sidecar_loop_mode()
    if mode == "auto":
        return
    try:
        if mode == "selector":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        elif mode == "proactor":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass


def _load_node_yaml() -> dict[str, Any]:
    try:
        from adaos.services.capacity import _load_node_yaml as load_yaml
    except Exception:
        return {}
    try:
        payload = load_yaml()
    except TypeError:
        try:
            payload = load_yaml(None)
        except Exception:
            payload = {}
    except Exception:
        payload = {}
    return dict(payload) if isinstance(payload, dict) else {}


def resolve_realtime_remote_candidates() -> list[str]:
    explicit_url = str(os.getenv("ADAOS_REALTIME_REMOTE_WS_URL") or "").strip() or None
    nats_cfg = load_nats_runtime_config()
    if not nats_cfg:
        legacy_payload = _load_node_yaml()
        legacy_nats = legacy_payload.get("nats") if isinstance(legacy_payload.get("nats"), dict) else {}
        nats_cfg = dict(legacy_nats) if isinstance(legacy_nats, dict) else {}
    if not nats_cfg:
        nats_cfg = migrate_legacy_nats_runtime_config()
    node_url_raw = str((nats_cfg or {}).get("ws_url") or "").strip() or None
    if explicit_url and nats_url_uses_websocket(explicit_url):
        base = normalize_nats_ws_url(explicit_url, fallback=None)
        candidates: list[str] = []
        for item in [base]:
            if isinstance(item, str) and item.startswith("ws") and item not in candidates:
                candidates.append(item)
        extra = str(os.getenv("ADAOS_REALTIME_REMOTE_WS_ALT", "") or "").strip()
        if extra:
            for item in [part.strip() for part in extra.split(",") if part.strip()]:
                normalized = normalize_nats_ws_url(item, fallback=None)
                if isinstance(normalized, str) and normalized.startswith("ws") and normalized not in candidates:
                    candidates.append(normalized)
        allow_api_fallback = _truthy(os.getenv("ADAOS_REALTIME_ALLOW_API_FALLBACK"), default=False)
        if allow_api_fallback:
            for item in [public_nats_ws_api()]:
                if item not in candidates:
                    candidates.append(item)
        return candidates
    target_url = explicit_url or node_url_raw
    if target_url and not nats_url_uses_websocket(target_url):
        allow_api_fallback = _truthy(os.getenv("ADAOS_REALTIME_ALLOW_API_FALLBACK"), default=True)
        allow_tcp_fallback = _truthy(os.getenv("ADAOS_REALTIME_ALLOW_TCP_FALLBACK"), default=False)
        ordered = [public_nats_ws_api()] if allow_api_fallback else []
        base_tcp = str(target_url).strip()
        if allow_tcp_fallback and base_tcp.startswith("nats://") and base_tcp not in ordered:
            ordered.append(base_tcp)
        return ordered
    node_url = normalize_nats_ws_url(node_url_raw, fallback=None)
    base = normalize_nats_ws_url(explicit_url or node_url, fallback=None)
    candidates: list[str] = []
    for item in [base, public_nats_ws_api()]:
        if isinstance(item, str) and item.startswith("ws") and item not in candidates:
            candidates.append(item)
    extra = str(os.getenv("ADAOS_REALTIME_REMOTE_WS_ALT", "") or "").strip()
    if extra:
        for item in [part.strip() for part in extra.split(",") if part.strip()]:
            normalized = normalize_nats_ws_url(item, fallback=None)
            if isinstance(normalized, str) and normalized.startswith("ws") and normalized not in candidates:
                candidates.append(normalized)
    # The api-domain ingress is the canonical public websocket endpoint for sidecar sessions. The
    # dedicated hostname is a legacy alias and is only used when explicitly supplied through ALT.
    prefer_dedicated = os.getenv("ADAOS_REALTIME_PREFER_DEDICATED", "0")
    ordered = order_nats_ws_candidates(candidates, explicit_url=base, prefer_dedicated=prefer_dedicated)
    api_ingress = public_nats_ws_api()
    allow_api_fallback = _truthy(os.getenv("ADAOS_REALTIME_ALLOW_API_FALLBACK"), default=True)
    if base and api_ingress in ordered and api_ingress != base and not allow_api_fallback:
        ordered = [item for item in ordered if item != api_ingress]
    return ordered


async def _is_port_open(host: str, port: int) -> bool:
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except Exception:
        return False
    try:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    except Exception:
        pass
    return True


async def probe_realtime_sidecar_ready(*, host: str, port: int, timeout_s: float = 2.0) -> bool:
    try:
        return bool(await asyncio.wait_for(_is_port_open(host, port), timeout=max(0.1, float(timeout_s))))
    except Exception:
        return False


async def wait_realtime_sidecar_ready(*, host: str, port: int, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + max(0.5, float(timeout_s))
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if await probe_realtime_sidecar_ready(host=host, port=port, timeout_s=min(remaining, 2.5)):
            return True
        await asyncio.sleep(0.1)
    return False


async def wait_realtime_sidecar_bound(*, host: str, port: int, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + max(0.5, float(timeout_s))
    while time.monotonic() < deadline:
        if _find_realtime_listener_pid(host, port):
            return True
        await asyncio.sleep(0.1)
    return False


async def start_realtime_sidecar_subprocess(
    *,
    role: str | None = None,
    repo_root: str | Path | None = None,
) -> subprocess.Popen[Any] | None:
    if not realtime_sidecar_enabled(role=role):
        return None
    if not resolve_realtime_remote_candidates():
        return None
    host = realtime_sidecar_host()
    port = realtime_sidecar_port()
    if await _is_port_open(host, port):
        try:
            await asyncio.to_thread(_replace_existing_realtime_listener, host, port)
        except Exception:
            pass
    if await _is_port_open(host, port):
        return None
    env = merged_runtime_dotenv_env(os.environ.copy())
    env["ADAOS_REALTIME_ENABLE"] = "1"
    env["ADAOS_REALTIME_CHILD"] = "1"
    env["ADAOS_BASE_DIR"] = str(current_base_dir())
    env.setdefault("ADAOS_REALTIME_PREFER_DEDICATED", "0")
    env.setdefault("ADAOS_REALTIME_ALLOW_API_FALLBACK", "0")
    env.setdefault("ADAOS_REALTIME_WIN_LOOP", "proactor")
    resolved_repo_root = (
        Path(repo_root).expanduser().resolve()
        if str(repo_root or "").strip()
        else _realtime_sidecar_repo_root()
    )
    launch_cwd = (
        resolved_repo_root
        if isinstance(resolved_repo_root, Path) and resolved_repo_root.exists()
        else Path(os.getcwd()).resolve()
    )
    if resolved_repo_root is not None:
        env["ADAOS_ROOT_REPO_ROOT"] = str(resolved_repo_root)
    log_path = realtime_sidecar_log_path()
    _rotate_realtime_sidecar_log_if_needed(log_path)
    stdout_handle = log_path.open("ab")
    args = [
        sys.executable,
        "-m",
        "adaos.services.realtime_sidecar",
        "--host",
        host,
        "--port",
        str(port),
    ]
    proc = subprocess.Popen(
        args,
        cwd=str(launch_cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stdout_handle,
        stderr=subprocess.STDOUT,
        start_new_session=(os.name != "nt"),
        creationflags=(
            int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if os.name == "nt"
            else 0
        ),
    )
    with contextlib.suppress(Exception):
        stdout_handle.close()
    if not await wait_realtime_sidecar_bound(host=host, port=port, timeout_s=10.0):
        with contextlib.suppress(Exception):
            proc.terminate()
        raise RuntimeError(f"adaos-realtime sidecar did not bind {host}:{port}")
    return proc


async def stop_realtime_sidecar_subprocess(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    with contextlib.suppress(Exception):
        proc.terminate()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        await asyncio.sleep(0.1)
    with contextlib.suppress(Exception):
        proc.kill()


def realtime_sidecar_listener_snapshot(
    proc: subprocess.Popen[Any] | None = None,
    *,
    role: str | None = None,
) -> dict[str, Any]:
    host = realtime_sidecar_host()
    port = realtime_sidecar_port()
    listener_pid = _find_realtime_listener_pid(host, port)
    managed_pid: int | None = None
    managed_alive = False
    managed_exit_code: int | None = None
    try:
        if proc is not None:
            pid = int(getattr(proc, "pid", 0) or 0)
            managed_pid = pid or None
            exit_code = proc.poll()
            if exit_code is None:
                managed_alive = True
            elif isinstance(exit_code, int):
                managed_exit_code = exit_code
    except Exception:
        managed_pid = managed_pid if isinstance(managed_pid, int) and managed_pid > 0 else None
        managed_alive = False
        managed_exit_code = None
    listener_running = bool(isinstance(listener_pid, int) and listener_pid > 0)
    listener_matches_managed = bool(
        listener_running
        and isinstance(managed_pid, int)
        and managed_pid > 0
        and int(listener_pid) == int(managed_pid)
    )
    adopted_listener = bool(listener_running and not listener_matches_managed)
    enablement_policy = realtime_sidecar_enablement_policy(role=role)
    return {
        "host": host,
        "port": int(port),
        "local_url": realtime_sidecar_local_url(),
        "log_path": str(realtime_sidecar_log_path()),
        "diag_path": str(realtime_sidecar_diag_path()),
        "managed_pid": managed_pid,
        "managed_alive": managed_alive,
        "managed_exit_code": managed_exit_code,
        "listener_pid": int(listener_pid) if listener_running else None,
        "listener_running": listener_running,
        "listener_matches_managed": listener_matches_managed,
        "adopted_listener": adopted_listener,
        "enablement_policy": enablement_policy,
        "route_tunnel_contract": realtime_sidecar_route_tunnel_contract(role=role),
        "media_proxy_contract": realtime_sidecar_media_proxy_contract(role=role),
    }


async def restart_realtime_sidecar_subprocess(
    *,
    proc: subprocess.Popen[Any] | None,
    role: str | None = None,
    repo_root: str | Path | None = None,
) -> tuple[subprocess.Popen[Any] | None, dict[str, Any]]:
    before = realtime_sidecar_listener_snapshot(proc, role=role)
    if not realtime_sidecar_enabled(role=role):
        return proc, {
            "ok": True,
            "accepted": False,
            "enabled": False,
            "reason": "disabled",
            "before": before,
            "after": before,
        }
    await stop_realtime_sidecar_subprocess(proc)
    new_proc = await start_realtime_sidecar_subprocess(role=role, repo_root=repo_root)
    after = realtime_sidecar_listener_snapshot(new_proc, role=role)
    return new_proc, {
        "ok": True,
        "accepted": True,
        "enabled": True,
        "reason": "restarted",
        "before": before,
        "after": after,
    }


@dataclass
class _RelayStats:
    session_id: str | None = None
    remote_url: str | None = None
    ws_ping_interval_s: float | None = None
    sidecar_nats_ping_interval_s: float | None = None
    local_connected_at: float | None = None
    remote_connected_at: float | None = None
    local_rx_bytes: int = 0
    local_tx_bytes: int = 0
    remote_rx_bytes: int = 0
    remote_tx_bytes: int = 0
    last_local_rx_at: float | None = None
    last_local_tx_at: float | None = None
    last_remote_rx_at: float | None = None
    last_remote_tx_at: float | None = None
    local_nats_pings_tx: int = 0
    local_nats_pongs_tx: int = 0
    remote_nats_pings_rx: int = 0
    remote_nats_pongs_rx: int = 0
    sidecar_nats_pings_tx: int = 0
    sidecar_nats_pongs_rx: int = 0
    sidecar_nats_pings_outstanding: int = 0
    client_nats_pings_outstanding: int = 0
    last_error: str | None = None
    active_session: bool = False
    local_client_total: int = 0
    session_open_total: int = 0
    session_close_total: int = 0
    remote_connect_total: int = 0
    remote_connect_fail_total: int = 0
    remote_connect_retry_total: int = 0
    remote_connect_retrying: bool = False
    remote_connect_retry_delay_s: float | None = None
    remote_quarantine_total: int = 0
    superseded_total: int = 0
    overlap_admitted_total: int = 0
    last_session_open_at: float | None = None
    last_session_close_at: float | None = None
    last_remote_connect_error: str | None = None
    last_remote_connect_error_at: float | None = None
    last_remote_disconnect_at: float | None = None


class RealtimeSidecarServer:
    def __init__(self, *, host: str, port: int) -> None:
        self._host = str(host or "127.0.0.1")
        self._port = int(port)
        self._server: asyncio.AbstractServer | None = None
        self._route_servers: dict[str, Any] = {}
        self._media_server: asyncio.AbstractServer | None = None
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._diag_task: asyncio.Task[Any] | None = None
        self._stopped = asyncio.Event()
        self._stats = _RelayStats()
        self._pending_ping_sources: deque[str] = deque()
        _reset_route_tunnel_runtime_state()
        _reset_media_proxy_runtime_state()

    def _begin_session_stats(self, *, session_id: str) -> None:
        previous = self._stats
        self._stats = _RelayStats(
            session_id=session_id,
            local_connected_at=time.monotonic(),
            active_session=True,
            local_client_total=int(previous.local_client_total or 0),
            session_open_total=int(previous.session_open_total or 0),
            session_close_total=int(previous.session_close_total or 0),
            remote_connect_total=int(previous.remote_connect_total or 0),
            remote_connect_fail_total=int(previous.remote_connect_fail_total or 0),
            remote_connect_retry_total=int(previous.remote_connect_retry_total or 0),
            remote_quarantine_total=int(previous.remote_quarantine_total or 0),
            superseded_total=int(previous.superseded_total or 0),
            overlap_admitted_total=int(previous.overlap_admitted_total or 0),
            last_session_open_at=time.monotonic(),
            last_session_close_at=previous.last_session_close_at,
            last_remote_connect_error=previous.last_remote_connect_error,
            last_remote_connect_error_at=previous.last_remote_connect_error_at,
            last_remote_disconnect_at=previous.last_remote_disconnect_at,
        )

    def _log(self, msg: str) -> None:
        try:
            print(f"[adaos-realtime] {msg}", flush=True)
        except Exception:
            pass

    @property
    def listen_host(self) -> str:
        return self._host

    @property
    def listen_port(self) -> int:
        try:
            if self._server is not None and getattr(self._server, "sockets", None):
                sock = self._server.sockets[0]
                return int(sock.getsockname()[1])
        except Exception:
            pass
        return int(self._port)

    def _diag_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()

        def _ago(value: float | None) -> float | None:
            if not isinstance(value, (int, float)):
                return None
            return round(now - float(value), 3)

        return {
            "ts": round(time.time(), 3),
            "listen": f"{self._host}:{self._port}",
            "session_id": self._stats.session_id,
            "active_session": self._stats.active_session,
            "active_session_total": len(self._live_session_tasks()),
            "ownership_boundary": "transport_only",
            "enablement_policy": realtime_sidecar_enablement_policy(),
            "route_tunnel_contract": realtime_sidecar_route_tunnel_contract(),
            "media_proxy_contract": realtime_sidecar_media_proxy_contract(),
            "remote_url": self._stats.remote_url,
            "ws_ping_interval_s": self._stats.ws_ping_interval_s,
            "sidecar_nats_ping_interval_s": self._stats.sidecar_nats_ping_interval_s,
            "local_connected_ago_s": _ago(self._stats.local_connected_at),
            "remote_connected_ago_s": _ago(self._stats.remote_connected_at),
            "local_rx_bytes": self._stats.local_rx_bytes,
            "local_tx_bytes": self._stats.local_tx_bytes,
            "remote_rx_bytes": self._stats.remote_rx_bytes,
            "remote_tx_bytes": self._stats.remote_tx_bytes,
            "last_local_rx_ago_s": _ago(self._stats.last_local_rx_at),
            "last_local_tx_ago_s": _ago(self._stats.last_local_tx_at),
            "last_remote_rx_ago_s": _ago(self._stats.last_remote_rx_at),
            "last_remote_tx_ago_s": _ago(self._stats.last_remote_tx_at),
            "local_nats_pings_tx": self._stats.local_nats_pings_tx,
            "local_nats_pongs_tx": self._stats.local_nats_pongs_tx,
            "remote_nats_pings_rx": self._stats.remote_nats_pings_rx,
            "remote_nats_pongs_rx": self._stats.remote_nats_pongs_rx,
            "sidecar_nats_pings_tx": self._stats.sidecar_nats_pings_tx,
            "sidecar_nats_pongs_rx": self._stats.sidecar_nats_pongs_rx,
            "sidecar_nats_pings_outstanding": self._stats.sidecar_nats_pings_outstanding,
            "client_nats_pings_outstanding": self._stats.client_nats_pings_outstanding,
            "last_error": self._stats.last_error,
            "local_client_total": self._stats.local_client_total,
            "session_open_total": self._stats.session_open_total,
            "session_close_total": self._stats.session_close_total,
            "remote_connect_total": self._stats.remote_connect_total,
            "remote_connect_fail_total": self._stats.remote_connect_fail_total,
            "remote_connect_retry_total": self._stats.remote_connect_retry_total,
            "remote_connect_retrying": self._stats.remote_connect_retrying,
            "remote_connect_retry_delay_s": self._stats.remote_connect_retry_delay_s,
            "remote_quarantine_total": self._stats.remote_quarantine_total,
            "superseded_total": self._stats.superseded_total,
            "overlap_admitted_total": self._stats.overlap_admitted_total,
            "last_session_open_ago_s": _ago(self._stats.last_session_open_at),
            "last_session_close_ago_s": _ago(self._stats.last_session_close_at),
            "last_remote_disconnect_ago_s": _ago(self._stats.last_remote_disconnect_at),
            "last_remote_connect_error": self._stats.last_remote_connect_error,
            "last_remote_connect_error_ago_s": _ago(self._stats.last_remote_connect_error_at),
            "loop_policy": type(asyncio.get_event_loop_policy()).__name__,
            "loop": type(asyncio.get_running_loop()).__name__,
        }

    async def _diag_loop(self) -> None:
        try:
            every_s = float(os.getenv("ADAOS_REALTIME_DIAG_EVERY_S", "2") or "2")
        except Exception:
            every_s = 2.0
        if every_s <= 0:
            every_s = 2.0
        path = realtime_sidecar_diag_path()
        while not self._stopped.is_set():
            try:
                _rotate_realtime_sidecar_diag_if_needed(path)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(self._diag_snapshot(), ensure_ascii=False) + "\n")
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=every_s)
            except asyncio.TimeoutError:
                continue

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        await self._start_route_tunnel_listeners()
        await self._start_media_proxy_listener()
        self._diag_task = asyncio.create_task(self._diag_loop(), name="adaos-realtime-diag")
        self._log(
            f"serve start listen=nats://{self.listen_host}:{self.listen_port} remote_candidates={resolve_realtime_remote_candidates()} "
            f"loop={type(asyncio.get_running_loop()).__name__} log={realtime_sidecar_log_path()} diag={realtime_sidecar_diag_path()}"
        )

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        self._stopped.set()
        active_tasks = self._live_session_tasks()
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        self._active_tasks.clear()
        for server in list(self._route_servers.values()):
            server.close()
        for server in list(self._route_servers.values()):
            with contextlib.suppress(BaseException):
                await server.wait_closed()
        self._route_servers.clear()
        _reset_route_tunnel_runtime_state()
        if self._media_server is not None:
            self._media_server.close()
            with contextlib.suppress(BaseException):
                await self._media_server.wait_closed()
            self._media_server = None
        _reset_media_proxy_runtime_state()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(BaseException):
                await self._server.wait_closed()
        if self._diag_task is not None and not self._diag_task.done():
            self._diag_task.cancel()
            with contextlib.suppress(BaseException):
                await self._diag_task

    async def _start_route_tunnel_listeners(self) -> None:
        listeners = realtime_sidecar_route_tunnel_listeners()
        for kind, listener in listeners.items():
            if not isinstance(listener, dict):
                continue
            if not bool(listener.get("enabled")):
                continue
            if not bool(listener.get("upstream_configured")):
                self._log(f"{kind} proxy skipped because runtime upstream is not configured")
                continue
            host = str(listener.get("listener_host") or "127.0.0.1").strip() or "127.0.0.1"
            port = int(listener.get("listener_port") or 0)
            if port <= 0:
                continue
            try:
                server = await self._serve_route_tunnel_websocket(kind=kind, host=host, port=port)
            except Exception as exc:
                self._log(f"{kind} proxy bind failed listen={host}:{port} err={type(exc).__name__}: {exc}")
                continue
            self._route_servers[kind] = server
            _set_route_tunnel_runtime_state(
                kind,
                listener_ready=True,
                listener_host=host,
                listener_port=port,
            )
            self._log(
                f"{kind} proxy ready listen={listener.get('listener_url')} upstream={listener.get('upstream_url')}"
            )

    async def _start_media_proxy_listener(self) -> None:
        contract = realtime_sidecar_media_proxy_contract()
        if not bool(_media_proxy_enabled()):
            return
        listener = contract.get("listener") if isinstance(contract.get("listener"), dict) else {}
        host = str(listener.get("host") or "127.0.0.1").strip() or "127.0.0.1"
        port = int(listener.get("port") or 0)
        if port <= 0:
            return
        try:
            self._media_server = await asyncio.start_server(self._handle_media_proxy_http, host, port)
        except Exception as exc:
            details = f"{type(exc).__name__}: {exc}"
            _set_media_proxy_runtime_state(last_error=details, listener_ready=False)
            self._log(f"media proxy bind failed listen={host}:{port} err={details}")
            return
        _set_media_proxy_runtime_state(
            listener_ready=True,
            listener_host=host,
            listener_port=port,
            listener_url=_media_proxy_listener_url(host=host, port=port),
            public_bases=_media_proxy_public_bases(host=host, port=port),
            last_error=None,
        )
        self._log(
            f"media proxy ready listen={_media_proxy_listener_url(host=host, port=port)} "
            f"public_bases={_media_proxy_public_bases(host=host, port=port)}"
        )

    async def _read_media_proxy_request(self, reader: asyncio.StreamReader) -> bytes:
        limit = 16384
        try:
            limit = max(1024, int(os.getenv("ADAOS_REALTIME_MEDIA_PROXY_HEADER_LIMIT", "16384") or "16384"))
        except Exception:
            limit = 16384
        data = bytearray()
        deadline_s = 5.0
        try:
            deadline_s = max(0.5, float(os.getenv("ADAOS_REALTIME_MEDIA_PROXY_HEADER_TIMEOUT_S", "5") or "5"))
        except Exception:
            deadline_s = 5.0
        end_at = time.monotonic() + deadline_s
        while b"\r\n\r\n" not in data:
            remaining = end_at - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("request_header_timeout")
            chunk = await asyncio.wait_for(reader.read(1024), timeout=remaining)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("request_header_too_large")
        return bytes(data)

    async def _media_proxy_response(
        self,
        writer: asyncio.StreamWriter,
        *,
        status: int,
        reason: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        method: str = "GET",
    ) -> None:
        header_map = {
            "Connection": "close",
            "X-AdaOS-Route": "realtime-sidecar-media-proxy",
            "Content-Length": str(len(body)),
            "Content-Type": "text/plain; charset=utf-8",
            **(headers or {}),
        }
        writer.write(f"HTTP/1.1 {int(status)} {reason}\r\n".encode("ascii", errors="replace"))
        for key, value in header_map.items():
            writer.write(f"{key}: {value}\r\n".encode("latin-1", errors="replace"))
        writer.write(b"\r\n")
        if method.upper() != "HEAD" and body:
            writer.write(body)
        await writer.drain()

    def _media_proxy_parse_range(self, raw: str | None, *, size: int) -> tuple[int, int] | None:
        value = str(raw or "").strip().lower()
        if not value:
            return None
        if not value.startswith("bytes=") or "," in value:
            raise ValueError("unsupported_range")
        spec = value[6:].strip()
        if "-" not in spec:
            raise ValueError("invalid_range")
        start_raw, end_raw = spec.split("-", 1)
        if start_raw == "":
            suffix = int(end_raw)
            if suffix <= 0:
                raise ValueError("invalid_range")
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else size - 1
        if size <= 0 or start < 0 or end < start or start >= size:
            raise ValueError("invalid_range")
        return start, min(end, size - 1)

    async def _stream_media_proxy_file(
        self,
        writer: asyncio.StreamWriter,
        *,
        target: Path,
        mime_type: str,
        method: str,
        byte_range: tuple[int, int] | None,
    ) -> None:
        size = int(target.stat().st_size)
        start = 0
        end = max(0, size - 1)
        status = 200
        reason = "OK"
        headers: dict[str, str] = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=3600",
            "Content-Type": mime_type,
        }
        if byte_range is not None:
            start, end = byte_range
            status = 206
            reason = "Partial Content"
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = max(0, end - start + 1) if size > 0 else 0
        headers["Content-Length"] = str(length)
        writer.write(f"HTTP/1.1 {status} {reason}\r\n".encode("ascii", errors="replace"))
        for key, value in {
            "Connection": "close",
            "X-AdaOS-Route": "realtime-sidecar-media-proxy",
            **headers,
        }.items():
            writer.write(f"{key}: {value}\r\n".encode("latin-1", errors="replace"))
        writer.write(b"\r\n")
        if method.upper() == "HEAD" or length <= 0:
            await writer.drain()
            return
        remaining = length
        with target.open("rb") as fh:
            fh.seek(start)
            while remaining > 0:
                chunk = fh.read(min(262144, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                writer.write(chunk)
                await writer.drain()

    async def _handle_media_proxy_http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await self._read_media_proxy_request(reader)
            if not raw:
                return
            text = raw.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1", errors="replace")
            lines = text.split("\r\n")
            request_line = lines[0] if lines else ""
            parts = request_line.split()
            if len(parts) < 3:
                await self._media_proxy_response(writer, status=400, reason="Bad Request", body=b"bad_request")
                return
            method = parts[0].upper()
            target_raw = parts[1]
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
            if method not in {"GET", "HEAD"}:
                await self._media_proxy_response(
                    writer,
                    status=405,
                    reason="Method Not Allowed",
                    headers={"Allow": "GET, HEAD"},
                    body=b"method_not_allowed",
                    method=method,
                )
                return
            parsed = urlparse(target_raw)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if not _media_proxy_token_ok(headers=headers, query=query):
                await self._media_proxy_response(
                    writer,
                    status=401,
                    reason="Unauthorized",
                    body=b"invalid_or_missing_token",
                    method=method,
                )
                return
            path = unquote(str(parsed.path or ""))
            indexer_prefixes = (
                "/api/node/media-indexer/content/",
                "/media/media-indexer/content/",
            )
            playback_id = ""
            for prefix in indexer_prefixes:
                if path.startswith(prefix):
                    playback_id = path[len(prefix):]
                    break
            if playback_id:
                try:
                    from adaos.services.media_indexer_library import guess_indexer_media_type, resolve_media_indexer_content

                    target, payload = resolve_media_indexer_content(playback_id)
                    mime_type = str(payload.get("mime_type") or "") or guess_indexer_media_type(target.name)
                except ValueError:
                    await self._media_proxy_response(
                        writer,
                        status=400,
                        reason="Bad Request",
                        body=b"invalid_playback_id",
                        method=method,
                    )
                    return
                except PermissionError:
                    await self._media_proxy_response(
                        writer,
                        status=403,
                        reason="Forbidden",
                        body=b"path_outside_indexed_directory",
                        method=method,
                    )
                    return
                except FileNotFoundError:
                    await self._media_proxy_response(
                        writer,
                        status=404,
                        reason="Not Found",
                        body=b"media_indexer_item_not_found",
                        method=method,
                    )
                    return
                try:
                    byte_range = self._media_proxy_parse_range(headers.get("range"), size=int(target.stat().st_size))
                except Exception:
                    await self._media_proxy_response(
                        writer,
                        status=416,
                        reason="Range Not Satisfiable",
                        headers={"Content-Range": f"bytes */{int(target.stat().st_size)}"},
                        body=b"range_not_satisfiable",
                        method=method,
                    )
                    return
                await self._stream_media_proxy_file(
                    writer,
                    target=target,
                    mime_type=mime_type,
                    method=method,
                    byte_range=byte_range,
                )
                return

            prefixes = (
                "/api/node/media/files/content/",
                "/media/files/content/",
            )
            filename = ""
            for prefix in prefixes:
                if path.startswith(prefix):
                    filename = path[len(prefix):]
                    break
            if not filename:
                await self._media_proxy_response(writer, status=404, reason="Not Found", body=b"not_found", method=method)
                return
            try:
                target = _media_proxy_file_path(filename)
            except ValueError:
                try:
                    from adaos.services.media_indexer_library import guess_indexer_media_type, resolve_media_indexer_content_by_name

                    target, payload = resolve_media_indexer_content_by_name(filename)
                    mime_type = str(payload.get("mime_type") or "") or guess_indexer_media_type(target.name)
                except ValueError:
                    await self._media_proxy_response(
                        writer,
                        status=400,
                        reason="Bad Request",
                        body=b"invalid_filename",
                        method=method,
                    )
                    return
                except PermissionError:
                    await self._media_proxy_response(
                        writer,
                        status=403,
                        reason="Forbidden",
                        body=b"path_outside_indexed_directory",
                        method=method,
                    )
                    return
                except FileNotFoundError:
                    await self._media_proxy_response(
                        writer,
                        status=400,
                        reason="Bad Request",
                        body=b"invalid_filename",
                        method=method,
                    )
                    return
            else:
                mime_type = _media_proxy_guess_media_type(target.name)
            if not target.exists() or not target.is_file():
                try:
                    from adaos.services.media_indexer_library import guess_indexer_media_type, resolve_media_indexer_content_by_name

                    target, payload = resolve_media_indexer_content_by_name(filename)
                    mime_type = str(payload.get("mime_type") or "") or guess_indexer_media_type(target.name)
                except ValueError:
                    await self._media_proxy_response(
                        writer,
                        status=400,
                        reason="Bad Request",
                        body=b"invalid_filename",
                        method=method,
                    )
                    return
                except PermissionError:
                    await self._media_proxy_response(
                        writer,
                        status=403,
                        reason="Forbidden",
                        body=b"path_outside_indexed_directory",
                        method=method,
                    )
                    return
                except FileNotFoundError:
                    await self._media_proxy_response(
                        writer,
                        status=404,
                        reason="Not Found",
                        body=b"media_file_not_found",
                        method=method,
                    )
                    return
            try:
                byte_range = self._media_proxy_parse_range(headers.get("range"), size=int(target.stat().st_size))
            except Exception:
                await self._media_proxy_response(
                    writer,
                    status=416,
                    reason="Range Not Satisfiable",
                    headers={"Content-Range": f"bytes */{int(target.stat().st_size)}"},
                    body=b"range_not_satisfiable",
                    method=method,
                )
                return
            await self._stream_media_proxy_file(
                writer,
                target=target,
                mime_type=mime_type,
                method=method,
                byte_range=byte_range,
            )
        except Exception as exc:
            _set_media_proxy_runtime_state(last_error=f"{type(exc).__name__}: {exc}")
            with contextlib.suppress(Exception):
                await self._media_proxy_response(
                    writer,
                    status=500,
                    reason="Internal Server Error",
                    body=b"media_proxy_error",
                )
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _serve_route_tunnel_websocket(self, *, kind: str, host: str, port: int) -> Any:
        import websockets  # type: ignore

        async def _handler(websocket: Any, path: str | None = None) -> None:
            await self._handle_route_tunnel_websocket(kind=kind, websocket=websocket, path=path)

        kwargs = {
            "host": host,
            "port": port,
            "max_size": None,
            "compression": None,
            "ping_interval": None,
            "ping_timeout": None,
        }
        try:
            return await websockets.serve(_handler, **kwargs)
        except TypeError:
            kwargs.pop("ping_timeout", None)
            return await websockets.serve(_handler, **kwargs)

    def _route_tunnel_requested_path(self, *, websocket: Any, path: str | None, default_path: str) -> str:
        request = getattr(websocket, "request", None)
        raw = str(
            getattr(websocket, "path", None)
            or getattr(request, "path", None)
            or path
            or default_path
        ).strip()
        if not raw:
            return default_path
        if raw.startswith("/"):
            return raw
        return "/" + raw.lstrip("/")

    async def _connect_route_tunnel_upstream(self, *, target: str) -> Any:
        import websockets  # type: ignore

        kwargs = {
            "open_timeout": 2.5,
            "close_timeout": 2.0,
            "max_size": None,
            "compression": None,
            "ping_interval": None,
            "ping_timeout": None,
        }
        try:
            return await websockets.connect(target, **kwargs)
        except TypeError:
            kwargs.pop("ping_timeout", None)
            return await websockets.connect(target, **kwargs)

    def _route_tunnel_reconnect_delay_s(self) -> float:
        try:
            return max(0.05, float(os.getenv("ADAOS_REALTIME_ROUTE_RECONNECT_DELAY_S", "0.25") or "0.25"))
        except Exception:
            return 0.25

    def _route_tunnel_replay_limit(self) -> int:
        try:
            return max(0, int(str(os.getenv("ADAOS_REALTIME_ROUTE_REPLAY_LIMIT", "32") or "32").strip()))
        except Exception:
            return 32

    def _route_tunnel_queue_limit(self) -> int:
        try:
            return max(1, int(str(os.getenv("ADAOS_REALTIME_ROUTE_QUEUE_LIMIT", "256") or "256").strip()))
        except Exception:
            return 256

    def _route_tunnel_target_url(self, *, requested_path: str) -> str:
        target_host = _route_tunnel_upstream_host()
        target_port = _route_tunnel_upstream_port()
        return f"ws://{target_host}:{target_port}{requested_path}"

    async def _handle_route_tunnel_websocket(
        self,
        *,
        kind: str,
        websocket: Any,
        path: str | None,
    ) -> None:
        listeners = realtime_sidecar_route_tunnel_listeners()
        listener = listeners.get(str(kind or "").strip().lower())
        if not isinstance(listener, dict):
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="listener_unavailable")
            return
        target_host = str(listener.get("upstream_host") or "").strip() or "127.0.0.1"
        target_port = int(listener.get("upstream_port") or 0)
        if target_port <= 0:
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="upstream_unconfigured")
            return
        upstream_path = str(listener.get("upstream_path") or "/ws").strip() or "/ws"
        requested_path = self._route_tunnel_requested_path(
            websocket=websocket,
            path=path,
            default_path=upstream_path,
        )
        requested_path_only = str(urlparse(requested_path).path or "").strip() or upstream_path
        path_allowed = requested_path_only == upstream_path or (
            str(kind or "").strip().lower() == "yws"
            and requested_path_only.startswith(upstream_path.rstrip("/") + "/")
        )
        if not path_allowed:
            with contextlib.suppress(Exception):
                await websocket.close(code=1008, reason="unexpected_path")
            return
        reconnect_delay_s = self._route_tunnel_reconnect_delay_s()
        replay_limit = self._route_tunnel_replay_limit()
        outgoing: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._route_tunnel_queue_limit())
        replay: deque[Any] = deque(maxlen=replay_limit)
        client_done = asyncio.Event()

        async def _client_reader() -> None:
            try:
                async for message in websocket:
                    if replay_limit > 0:
                        replay.append(message)
                    await outgoing.put(message)
            finally:
                client_done.set()

        async def _send_to_upstream(upstream_ws: Any, replay_messages: list[Any]) -> None:
            for message in replay_messages:
                await upstream_ws.send(message)
            while not client_done.is_set():
                try:
                    message = await asyncio.wait_for(outgoing.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                await upstream_ws.send(message)

        async def _recv_from_upstream(upstream_ws: Any) -> None:
            async for message in upstream_ws:
                await websocket.send(message)

        client_task = asyncio.create_task(_client_reader(), name=f"adaos-realtime-{kind}-proxy-client")
        try:
            while not client_done.is_set() and not self._stopped.is_set():
                target = self._route_tunnel_target_url(requested_path=requested_path)
                try:
                    upstream_ws = await self._connect_route_tunnel_upstream(target=target)
                except Exception as exc:
                    self._log(
                        f"{kind} proxy upstream connect failed target={target} err={type(exc).__name__}: {exc}"
                    )
                    try:
                        await asyncio.wait_for(client_done.wait(), timeout=reconnect_delay_s)
                    except asyncio.TimeoutError:
                        continue
                    break

                replay_messages = list(replay)
                self._log(
                    f"{kind} proxy upstream connected target={target} replay={len(replay_messages)}"
                )
                send_task = asyncio.create_task(
                    _send_to_upstream(upstream_ws, replay_messages),
                    name=f"adaos-realtime-{kind}-proxy-upstream",
                )
                recv_task = asyncio.create_task(
                    _recv_from_upstream(upstream_ws),
                    name=f"adaos-realtime-{kind}-proxy-downstream",
                )
                done, pending = await asyncio.wait(
                    [send_task, recv_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in pending:
                    with contextlib.suppress(BaseException):
                        await task
                with contextlib.suppress(Exception):
                    await upstream_ws.close()
                if client_done.is_set():
                    break
                for task in done:
                    try:
                        await task
                    except Exception as exc:
                        self._log(
                            f"{kind} proxy upstream disconnected target={target} err={type(exc).__name__}: {exc}"
                        )
                        break
                try:
                    await asyncio.wait_for(client_done.wait(), timeout=reconnect_delay_s)
                except asyncio.TimeoutError:
                    continue
        finally:
            client_task.cancel()
            with contextlib.suppress(BaseException):
                await client_task
        with contextlib.suppress(Exception):
            await websocket.close()

    def _tagged_remote_url(self, url: str, *, session_id: str) -> str:
        if not _truthy(os.getenv("ADAOS_REALTIME_CONNECT_TAG_QUERY", "1"), default=True):
            return url
        try:
            parsed = urlparse(str(url))
            params = dict(parse_qsl(parsed.query, keep_blank_values=True))
            params.setdefault("adaos_conn", session_id)
            return urlunparse(parsed._replace(query=urlencode(params)))
        except Exception:
            return url

    async def _connect_remote(self, *, session_id: str) -> tuple[Any, str]:
        import websockets  # type: ignore

        last_exc: Exception | None = None
        heartbeat_s = _realtime_ws_heartbeat_s()
        max_queue = _realtime_ws_max_queue()
        proxy = _realtime_ws_proxy()
        for candidate in _available_realtime_remote_candidates():
            target = self._tagged_remote_url(candidate, session_id=session_id)
            try:
                kwargs = {
                    "subprotocols": ["nats"],
                    "open_timeout": 5.0,
                    "close_timeout": 2.0,
                    "max_size": None,
                    "max_queue": max_queue,
                    "compression": None,
                    "ping_interval": heartbeat_s,
                    "ping_timeout": None,
                    "proxy": proxy,
                }
                try:
                    ws = await websockets.connect(target, **kwargs)
                except TypeError:
                    kwargs.pop("proxy", None)
                    ws = await websockets.connect(target, **kwargs)
                sock = _ws_socket(ws)
                keepalive_ok = _set_tcp_keepalive(sock)
                self._stats.ws_ping_interval_s = heartbeat_s
                self._stats.remote_connect_total = int(self._stats.remote_connect_total or 0) + 1
                self._stats.last_remote_connect_error = None
                self._stats.last_remote_connect_error_at = None
                self._log(
                    f"remote connect ok url={target} ping_interval={heartbeat_s} max_queue={max_queue} "
                    f"proxy={proxy} tcp_keepalive={keepalive_ok}"
                )
                return ws, target
            except Exception as exc:
                last_exc = exc
                self._stats.remote_connect_fail_total = int(self._stats.remote_connect_fail_total or 0) + 1
                self._stats.last_remote_connect_error = f"{type(exc).__name__}: {exc}"
                self._stats.last_remote_connect_error_at = time.monotonic()
                self._log(f"remote connect failed url={target} err={type(exc).__name__}: {exc}")
        raise RuntimeError(f"realtime remote connect failed: {type(last_exc).__name__}: {last_exc}") from last_exc

    async def _connect_remote_with_retry(
        self,
        *,
        session_id: str,
        writer: asyncio.StreamWriter,
    ) -> tuple[Any, str]:
        delay_s = _realtime_remote_connect_retry_initial_s()
        max_delay_s = _realtime_remote_connect_retry_max_s()
        factor = _realtime_remote_connect_retry_factor()
        while not self._stopped.is_set() and not writer.is_closing():
            try:
                ws, remote_url = await self._connect_remote(session_id=session_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                details = f"{type(exc).__name__}: {exc}"
                self._stats.last_error = details
                self._stats.remote_connect_retrying = True
                self._stats.remote_connect_retry_delay_s = round(delay_s, 3)
                self._stats.remote_connect_retry_total = int(self._stats.remote_connect_retry_total or 0) + 1
                self._log(
                    f"remote connect retry scheduled id={session_id} in={delay_s:.3f}s err={details}"
                )
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=delay_s)
                    break
                except asyncio.TimeoutError:
                    delay_s = min(max_delay_s, delay_s * factor)
                    continue
            else:
                self._stats.remote_connect_retrying = False
                self._stats.remote_connect_retry_delay_s = None
                self._stats.last_error = None
                return ws, remote_url
        self._stats.remote_connect_retrying = False
        self._stats.remote_connect_retry_delay_s = None
        raise RuntimeError("realtime remote connect stopped before connection was established")

    async def _relay_local_to_remote(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, ws: Any) -> None:
        send_q: asyncio.Queue[bytes] = asyncio.Queue()
        send_event = asyncio.Event()
        recv_q: asyncio.Queue[bytes] = asyncio.Queue()
        pending_ping_sources: deque[str] = deque()
        if len(self._live_session_tasks()) <= 1:
            self._pending_ping_sources = pending_ping_sources
        client_pings_outstanding = 0
        sidecar_pings_outstanding = 0

        async def _queue_remote_payload(payload: bytes) -> None:
            await send_q.put(payload)
            send_event.set()

        async def _local_reader_loop() -> None:
            nonlocal client_pings_outstanding
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                self._stats.local_rx_bytes += len(chunk)
                self._stats.last_local_rx_at = time.monotonic()
                if chunk == NATS_PING:
                    self._stats.local_nats_pings_tx += 1
                    self._stats.client_nats_pings_outstanding += 1
                    client_pings_outstanding += 1
                    pending_ping_sources.append("client")
                elif chunk == NATS_PONG:
                    self._stats.local_nats_pongs_tx += 1
                await _queue_remote_payload(chunk)

        async def _remote_writer_loop() -> None:
            nonlocal client_pings_outstanding, sidecar_pings_outstanding
            recv_task: asyncio.Task[Any] | None = asyncio.create_task(ws.recv(), name="adaos-realtime-ws-recv")
            wake_task: asyncio.Task[Any] | None = None
            try:
                while True:
                    if recv_task is not None and recv_task.done():
                        try:
                            raw = await recv_task
                        except Exception as exc:
                            if _is_normal_ws_close(exc):
                                return
                            raise
                        finally:
                            recv_task = None
                        if isinstance(raw, str):
                            payload = raw.encode("utf-8", errors="replace")
                        else:
                            payload = bytes(raw)
                        if not payload:
                            recv_task = asyncio.create_task(ws.recv(), name="adaos-realtime-ws-recv")
                            continue
                        self._stats.remote_rx_bytes += len(payload)
                        self._stats.last_remote_rx_at = time.monotonic()
                        if payload == NATS_PING:
                            self._stats.remote_nats_pings_rx += 1
                        elif payload == NATS_PONG:
                            self._stats.remote_nats_pongs_rx += 1
                            source = pending_ping_sources.popleft() if pending_ping_sources else None
                            if source == "sidecar":
                                if sidecar_pings_outstanding > 0:
                                    sidecar_pings_outstanding -= 1
                                if self._stats.sidecar_nats_pings_outstanding > 0:
                                    self._stats.sidecar_nats_pings_outstanding -= 1
                                self._stats.sidecar_nats_pongs_rx += 1
                                recv_task = asyncio.create_task(ws.recv(), name="adaos-realtime-ws-recv")
                                continue
                            if source == "client":
                                if client_pings_outstanding > 0:
                                    client_pings_outstanding -= 1
                                if self._stats.client_nats_pings_outstanding > 0:
                                    self._stats.client_nats_pings_outstanding -= 1
                            elif client_pings_outstanding > 0:
                                client_pings_outstanding -= 1
                                if self._stats.client_nats_pings_outstanding > 0:
                                    self._stats.client_nats_pings_outstanding -= 1
                            elif sidecar_pings_outstanding > 0:
                                sidecar_pings_outstanding -= 1
                                if self._stats.sidecar_nats_pings_outstanding > 0:
                                    self._stats.sidecar_nats_pings_outstanding -= 1
                                self._stats.sidecar_nats_pongs_rx += 1
                                recv_task = asyncio.create_task(ws.recv(), name="adaos-realtime-ws-recv")
                                continue
                        await recv_q.put(payload)
                        recv_task = asyncio.create_task(ws.recv(), name="adaos-realtime-ws-recv")
                        continue

                    try:
                        payload = send_q.get_nowait()
                    except asyncio.QueueEmpty:
                        payload = None
                    if payload is not None:
                        await ws.send(payload)
                        self._stats.remote_tx_bytes += len(payload)
                        self._stats.last_remote_tx_at = time.monotonic()
                        if send_q.empty():
                            send_event.clear()
                        continue

                    send_event.clear()
                    wake_task = asyncio.create_task(send_event.wait(), name="adaos-realtime-ws-send")
                    done, pending = await asyncio.wait({recv_task, wake_task}, return_when=asyncio.FIRST_COMPLETED)
                    if wake_task in done:
                        wake_task = None
                        continue
                    if wake_task in pending and not wake_task.done():
                        wake_task.cancel()
                    wake_task = None
                    if recv_task not in done:
                        continue
            finally:
                cleanup_tasks: list[asyncio.Task[Any]] = []
                if recv_task is not None and not recv_task.done():
                    recv_task.cancel()
                    cleanup_tasks.append(recv_task)
                if wake_task is not None and not wake_task.done():
                    wake_task.cancel()
                    cleanup_tasks.append(wake_task)
                if cleanup_tasks:
                    await asyncio.gather(*cleanup_tasks, return_exceptions=True)

        async def _remote_reader_loop() -> None:
            while True:
                payload = await recv_q.get()
                writer.write(payload)
                await writer.drain()
                self._stats.local_tx_bytes += len(payload)
                self._stats.last_local_tx_at = time.monotonic()

        async def _sidecar_keepalive_loop(*, interval_s: float) -> None:
            nonlocal sidecar_pings_outstanding
            while True:
                await asyncio.sleep(interval_s)
                if getattr(ws, "closed", False):
                    return
                if sidecar_pings_outstanding > 0:
                    continue
                pending_ping_sources.append("sidecar")
                sidecar_pings_outstanding += 1
                self._stats.sidecar_nats_pings_tx += 1
                self._stats.sidecar_nats_pings_outstanding += 1
                await _queue_remote_payload(NATS_PING)

        interval_s = _realtime_nats_ping_interval_s()
        self._stats.sidecar_nats_ping_interval_s = interval_s
        tasks = [
            asyncio.create_task(_local_reader_loop(), name="adaos-realtime-l2r"),
            asyncio.create_task(_remote_writer_loop(), name="adaos-realtime-ws-io"),
            asyncio.create_task(_remote_reader_loop(), name="adaos-realtime-r2l"),
        ]
        if interval_s is not None:
            tasks.append(asyncio.create_task(_sidecar_keepalive_loop(interval_s=interval_s), name="adaos-realtime-ka"))
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise result

    async def _bridge_session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        ws = None
        remote_url: str | None = None
        session_id = f"rt-{uuid.uuid4().hex[:10]}"
        self._begin_session_stats(session_id=session_id)
        self._stats.session_open_total = int(self._stats.session_open_total or 0) + 1
        try:
            ws, remote_url = await self._connect_remote_with_retry(session_id=session_id, writer=writer)
            self._stats.remote_url = remote_url
            self._stats.remote_connected_at = time.monotonic()
            self._log(f"session open id={session_id} remote={remote_url}")
            await self._relay_local_to_remote(reader, writer, ws)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            details = f"{type(exc).__name__}: {exc}"
            try:
                code = getattr(exc, "code", None)
                reason = getattr(exc, "reason", None)
                rcvd = getattr(exc, "rcvd", None)
                sent = getattr(exc, "sent", None)
                if code is not None or reason is not None or rcvd is not None or sent is not None:
                    details += f" code={code} reason={reason} rcvd={rcvd} sent={sent}"
            except Exception:
                pass
            self._stats.last_error = details
            if remote_url and _should_quarantine_realtime_remote(details):
                _quarantine_realtime_remote(remote_url, details=details)
                self._stats.remote_quarantine_total = int(self._stats.remote_quarantine_total or 0) + 1
                self._log(
                    f"remote quarantined url={_realtime_remote_quarantine_key(remote_url)} "
                    f"for={_realtime_remote_quarantine_s():.0f}s err={details}"
                )
            self._log(f"session error id={session_id} err={details}")
        finally:
            self._stats.session_close_total = int(self._stats.session_close_total or 0) + 1
            self._stats.last_session_close_at = time.monotonic()
            self._stats.last_remote_disconnect_at = time.monotonic()
            if ws is not None:
                with contextlib.suppress(Exception):
                    await ws.close()
                with contextlib.suppress(Exception):
                    await ws.wait_closed()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            self._log(f"session close id={session_id}")

    def _live_session_tasks(self) -> set[asyncio.Task[Any]]:
        live = {task for task in self._active_tasks if not task.done()}
        if len(live) != len(self._active_tasks):
            self._active_tasks = live
        return live

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        try:
            if sock is not None:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        self._stats.local_client_total = int(self._stats.local_client_total or 0) + 1
        active_tasks = self._live_session_tasks()
        if active_tasks:
            try:
                await asyncio.wait_for(reader.read(1), timeout=_realtime_probe_grace_s())
            except asyncio.TimeoutError:
                pass
            except Exception:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                return
            else:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                return
            if len(active_tasks) >= _realtime_max_local_sessions():
                self._log("rejecting local NATS client: concurrent session limit reached")
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                return
            self._stats.overlap_admitted_total = int(self._stats.overlap_admitted_total or 0) + 1
            self._log("admitting overlapping local NATS client for runtime handoff")
        task = asyncio.create_task(self._bridge_session(reader, writer), name="adaos-realtime-session")
        self._active_tasks.add(task)
        self._stats.active_session = True
        try:
            with contextlib.suppress(BaseException):
                await task
        finally:
            self._active_tasks.discard(task)
            self._stats.active_session = bool(self._live_session_tasks())


async def run_realtime_sidecar(*, host: str | None = None, port: int | None = None) -> int:
    apply_realtime_loop_policy()
    server = RealtimeSidecarServer(host=host or realtime_sidecar_host(), port=port or realtime_sidecar_port())
    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        await server.close()
    return 0


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m adaos.services.realtime_sidecar",
        description="Run the AdaOS realtime sidecar without importing the full CLI.",
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    return int(asyncio.run(run_realtime_sidecar(host=args.host, port=args.port)))


if __name__ == "__main__":
    raise SystemExit(_main())
