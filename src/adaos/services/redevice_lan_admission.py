from __future__ import annotations

import secrets
import socket
import string
import time
import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping

from adaos.adapters.db import sqlite as sqlite_db
from adaos.sdk.core.decorators import subscribe
from adaos.services import access_links

_NS = "redevice_lan_admission"
_KEY = "state"
_LOCK = RLock()

_DEFAULT_DISCOVERY_TTL_S = 10 * 60
_REQUEST_TTL_S = 30 * 60
_COMMAND_TTL_S = 10 * 60
_COMMAND_LEASE_S = 12
_MAX_COMMANDS_PER_ENDPOINT = 50
_MAX_EVENTS_PER_ENDPOINT = 50


def _now_ts() -> float:
    return float(time.time())


def _iso(ts: float | int | None = None) -> str:
    value = float(ts if ts is not None else _now_ts())
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _command_id(command: Mapping[str, Any]) -> str:
    return _text(command.get("command_id") or command.get("id"))


def _load_state() -> dict[str, Any]:
    payload = sqlite_db.durable_state_get(_NS, _KEY) or {}
    return {
        "schema_version": "redevice-lan-admission-state.v1",
        "discovery": _mapping(payload.get("discovery")),
        "requests": _mapping(payload.get("requests")),
        "commands": _mapping(payload.get("commands")),
        "events": _mapping(payload.get("events")),
        "acks": _mapping(payload.get("acks")),
        "updated_at": float(payload.get("updated_at") or 0.0) or None,
    }


def _save_state(state: Mapping[str, Any]) -> None:
    payload = dict(state)
    payload["schema_version"] = "redevice-lan-admission-state.v1"
    payload["updated_at"] = _now_ts()
    sqlite_db.durable_state_put(_NS, _KEY, payload)


def _local_config() -> dict[str, Any]:
    conf = None
    try:
        from adaos.services.agent_context import get_ctx

        conf = getattr(get_ctx(), "config", None)
    except Exception:
        conf = None
    if conf is None:
        try:
            from adaos.services.node_config import load_config

            conf = load_config()
        except Exception:
            conf = None
    if conf is None:
        return {}
    root = getattr(conf, "root_settings", None)
    owner = getattr(root, "owner", None)
    subnet = getattr(conf, "subnet", None)
    node = getattr(conf, "node", None)
    return {
        "hub_id": _text(getattr(conf, "subnet_id", "")) or _text(getattr(subnet, "id", "")),
        "owner_id": _text(getattr(conf, "owner_id", "")) or _text(getattr(owner, "owner_id", "")),
        "node_id": _text(getattr(conf, "node_id", "")) or _text(getattr(node, "id", "")),
        "node_names": list(getattr(node, "node_names", []) or []),
        "subnet_names": list(getattr(subnet, "names", []) or []),
        "zone_id": _text(getattr(conf, "zone_id", "")),
        "local_api_url": _text(getattr(conf, "local_api_url", "")),
    }


def _first_lan_ip() -> str:
    candidates: list[str] = []
    try:
        hostname = socket.gethostname()
        candidates.extend(socket.gethostbyname_ex(hostname)[2])
    except Exception:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("8.8.8.8", 80))
        candidates.append(str(probe.getsockname()[0]))
        probe.close()
    except Exception:
        pass
    for item in candidates:
        token = _text(item)
        if token and not token.startswith("127.") and not token.startswith("169.254."):
            return token
    return "127.0.0.1"


def default_hub_base_url() -> str:
    raw = _text(_local_config().get("local_api_url"))
    if raw and not raw.startswith("http://127.") and "localhost" not in raw:
        return raw.rstrip("/")
    return f"http://{_first_lan_ip()}:8777"


def local_control_base_url() -> str:
    raw = _text(_local_config().get("local_api_url"))
    return (raw or "http://127.0.0.1:8777").rstrip("/")


def _request_id(endpoint_id: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in endpoint_id)[:80]
    return f"lan-{clean}" if clean else f"lan-{uuid.uuid4().hex[:12]}"


def _pair_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _enabled_discovery(discovery: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = dict(discovery or _load_state().get("discovery") or {})
    expires_at = float(data.get("expires_at") or 0.0)
    enabled = bool(data.get("enabled")) and expires_at > _now_ts()
    data["enabled"] = enabled
    data["state"] = "enabled" if enabled else "disabled"
    if expires_at:
        data["expires_at_iso"] = _iso(expires_at)
    return data


def _public_hub_descriptor(base_url: str | None = None) -> dict[str, Any]:
    config = _local_config()
    hub_base = (_text(base_url) or default_hub_base_url()).rstrip("/")
    node_names = [_text(item) for item in _list(config.get("node_names")) if _text(item)]
    subnet_names = [_text(item) for item in _list(config.get("subnet_names")) if _text(item)]
    return {
        "hub_id": _text(config.get("hub_id")),
        "subnet_id": _text(config.get("hub_id")),
        "owner_id": _text(config.get("owner_id")),
        "node_id": _text(config.get("node_id")),
        "node_name": node_names[0] if node_names else _text(config.get("node_id")) or "AdaOS Hub",
        "assistant_name": subnet_names[0] if subnet_names else _text(config.get("hub_id")) or "AdaOS",
        "zone_id": _text(config.get("zone_id")) or "local",
        "hub_base_url": hub_base,
        "control_base_url": local_control_base_url(),
    }


def discovery_status() -> dict[str, Any]:
    with _LOCK:
        state = _load_state()
        discovery = _enabled_discovery(state.get("discovery"))
        pending = [
            request
            for request in _mapping(state.get("requests")).values()
            if isinstance(request, Mapping) and _text(request.get("state")) == "pending"
        ]
        return {
            "ok": True,
            "schema_version": "redevice-lan-discovery.v1",
            "discovery": discovery,
            "hub": _public_hub_descriptor(_text(discovery.get("hub_base_url")) or None),
            "pending_count": len(pending),
            "updated_at": _iso(),
        }


def enable_discovery(*, ttl_s: int | float | None = None, hub_base_url: str | None = None) -> dict[str, Any]:
    now = _now_ts()
    ttl = max(30.0, min(3600.0, float(ttl_s or _DEFAULT_DISCOVERY_TTL_S)))
    hub = _public_hub_descriptor(hub_base_url)
    discovery = {
        "enabled": True,
        "state": "enabled",
        "enabled_at": now,
        "expires_at": now + ttl,
        "expires_at_iso": _iso(now + ttl),
        "hub_base_url": hub["hub_base_url"],
        "control_base_url": hub["control_base_url"],
        "ttl_s": ttl,
    }
    with _LOCK:
        state = _load_state()
        state["discovery"] = discovery
        _save_state(state)
    return {"ok": True, "discovery": discovery, "hub": hub, "updated_at": _iso()}


def disable_discovery() -> dict[str, Any]:
    with _LOCK:
        state = _load_state()
        discovery = _mapping(state.get("discovery"))
        discovery["enabled"] = False
        discovery["state"] = "disabled"
        discovery["disabled_at"] = _now_ts()
        state["discovery"] = discovery
        _save_state(state)
    return {"ok": True, "discovery": _enabled_discovery(discovery), "updated_at": _iso()}


def _publish_pending_action(request: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        from adaos.services.pending_actions import publish_pending_action

        endpoint_id = _text(request.get("endpoint_id"))
        title = f"Approve ReDevice {request.get('device_label') or endpoint_id}"
        summary = f"Local endpoint {endpoint_id} asks to join this subnet via LAN discovery."
        return publish_pending_action(
            action_id=f"redevice.lan_admission.{_text(request.get('request_id'))}",
            kind="redevice.lan_admission",
            title=title,
            summary=summary,
            producer={"type": "endpoint", "id": endpoint_id, "label": _text(request.get("device_label"))},
            owner_scope={"kind": "redevice", "endpoint_id": endpoint_id},
            domain_ref={"kind": "redevice.lan_admission", "request_id": _text(request.get("request_id"))},
            allowed_actions=[
                {"id": "approve", "label": "Approve", "terminal": True},
                {"id": "refuse", "label": "Deny", "terminal": True},
                {"id": "postpone", "label": "Later", "terminal": False},
            ],
            response_topic="redevice.lan_admission.response",
            ttl_s=_REQUEST_TTL_S,
            metadata={
                "request_id": _text(request.get("request_id")),
                "endpoint_id": endpoint_id,
                "device_label": _text(request.get("device_label")),
            },
        )
    except ValueError as exc:
        if "already exists" in str(exc):
            return None
        raise
    except Exception:
        return None


def submit_request(payload: Mapping[str, Any], *, client_host: str | None = None) -> dict[str, Any]:
    now = _now_ts()
    endpoint_id = _text(payload.get("endpoint_id")) or f"redevice-{uuid.uuid4().hex[:12]}"
    request_id = _request_id(endpoint_id)
    with _LOCK:
        state = _load_state()
        discovery = _enabled_discovery(state.get("discovery"))
        if not bool(discovery.get("enabled")):
            return {
                "ok": True,
                "state": "discovery_closed",
                "request_id": request_id,
                "retry_after_s": 10,
                "message": "LAN admission discovery is not enabled on this hub.",
            }
        requests = _mapping(state.get("requests"))
        existing = _mapping(requests.get(request_id))
        if _text(existing.get("state")) in {"approved", "denied"}:
            existing["last_seen_at"] = now
            requests[request_id] = existing
            state["requests"] = requests
            _save_state(state)
            return _request_poll_response(existing)
        request = {
            **existing,
            "schema_version": "redevice-lan-admission-request.v1",
            "request_id": request_id,
            "endpoint_id": endpoint_id,
            "device_label": _text(payload.get("device_label")) or "Android ReDevice",
            "language": _text(payload.get("language")) or "en",
            "diagnostic_report": _mapping(payload.get("diagnostic_report")),
            "endpoint_manifest": _mapping(payload.get("endpoint_manifest")),
            "client_host": _text(client_host),
            "state": "pending",
            "created_at": float(existing.get("created_at") or now),
            "last_seen_at": now,
            "expires_at": now + _REQUEST_TTL_S,
            "hub": _public_hub_descriptor(_text(discovery.get("hub_base_url")) or None),
        }
        requests[request_id] = request
        state["requests"] = requests
        _save_state(state)
    _publish_pending_action(request)
    return _request_poll_response(request)


def list_requests(*, include_terminal: bool = True) -> dict[str, Any]:
    now = _now_ts()
    with _LOCK:
        state = _load_state()
        requests = []
        for item in _mapping(state.get("requests")).values():
            if not isinstance(item, Mapping):
                continue
            request = dict(item)
            if not include_terminal and _text(request.get("state")) in {"approved", "denied", "expired"}:
                continue
            if _text(request.get("state")) == "pending" and float(request.get("expires_at") or 0.0) <= now:
                request["state"] = "expired"
            requests.append(request)
    requests.sort(key=lambda item: float(item.get("last_seen_at") or item.get("created_at") or 0.0), reverse=True)
    return {
        "ok": True,
        "requests": [_request_public_view(item) for item in requests],
        "count": len(requests),
        "discovery": discovery_status().get("discovery"),
        "updated_at": _iso(),
    }


def get_request(request_id: str) -> dict[str, Any] | None:
    token = _text(request_id)
    if not token:
        return None
    with _LOCK:
        request = _mapping(_mapping(_load_state().get("requests")).get(token))
    return request or None


def _policy_for_request(request: Mapping[str, Any], *, pair_code: str, endpoint_token: str) -> dict[str, Any]:
    hub = _mapping(request.get("hub")) or _public_hub_descriptor()
    endpoint_id = _text(request.get("endpoint_id"))
    issued_at = _iso()
    return {
        "schema_version": "endpoint-policy.v1",
        "policy_id": f"policy:lan:{endpoint_id}:{pair_code}",
        "policy_version": "endpoint-policy.v1",
        "policy_issued_at": issued_at,
        "endpoint_id": endpoint_id,
        "pair_code": pair_code,
        "node_type": "redevice",
        "trust_level": "limited",
        "hub_id": _text(hub.get("hub_id")),
        "subnet_id": _text(hub.get("subnet_id") or hub.get("hub_id")),
        "owner_id": _text(hub.get("owner_id")),
        "node_id": _text(hub.get("node_id")),
        "assistant_name": _text(hub.get("assistant_name")),
        "node_name": _text(hub.get("node_name")),
        "root_url": _text(hub.get("hub_base_url")),
        "control_root_url": _text(hub.get("control_base_url")) or local_control_base_url(),
        "endpoint_token": endpoint_token,
        "service_permissions": {
            "display_endpoint": True,
            "button_endpoint": True,
            "health_endpoint": True,
            "audio_input_endpoint": False,
            "camera_endpoint": False,
        },
        "transport_profile": {
            "schema_version": "transport-profile.v1",
            "endpoint_id": endpoint_id,
            "preferred_order": ["local_http", "redevice_poll", "root_relay_inline"],
            "routes": {
                "local_http": {
                    "available": True,
                    "state": "ready",
                    "directions": ["control", "events", "content_in"],
                    "legacy_safe": False,
                    "base_url": _text(hub.get("hub_base_url")),
                },
                "redevice_poll": {
                    "available": True,
                    "state": "ready",
                    "directions": ["control", "events"],
                    "legacy_safe": True,
                },
                "root_relay_inline": {
                    "available": False,
                    "state": "disabled",
                    "directions": ["content_in"],
                    "reason": "local_lan_admission",
                },
            },
        },
    }


def approve_request(request_id: str, *, display_name: str | None = None) -> dict[str, Any]:
    token = _text(request_id)
    if not token:
        return {"ok": False, "error": "request_id_required"}
    now = _now_ts()
    with _LOCK:
        state = _load_state()
        requests = _mapping(state.get("requests"))
        request = _mapping(requests.get(token))
        if not request:
            return {"ok": False, "error": "request_not_found", "request_id": token}
        if _text(request.get("state")) == "approved":
            return {"ok": True, "state": "approved", "request": _request_public_view(request)}
        pair_code = _text(request.get("pair_code")) or _pair_code()
        endpoint_token = _text(request.get("endpoint_token")) or secrets.token_urlsafe(32)
        policy = _policy_for_request(request, pair_code=pair_code, endpoint_token=endpoint_token)
        manifest = _mapping(request.get("endpoint_manifest"))
        manifest.update(
            {
                "endpoint_id": _text(request.get("endpoint_id")),
                "display_name": _text(display_name) or _text(request.get("device_label")) or "Android ReDevice",
                "admission_status": "accepted",
                "admission_session_id": token,
                "hub_id": policy["hub_id"],
                "subnet_id": policy["subnet_id"],
                "owner_id": policy["owner_id"],
                "policy_id": policy["policy_id"],
                "trust_level": policy["trust_level"],
                "root_url": policy["root_url"],
                "pair_code": pair_code,
            }
        )
        credentials = {
            "schema_version": "redevice-lan-credentials.v1",
            "root_url": policy["root_url"],
            "pair_code": pair_code,
            "endpoint_token": endpoint_token,
            "endpoint_policy": policy,
            "endpoint_manifest": manifest,
            "issued_at": _iso(now),
        }
        request.update(
            {
                "state": "approved",
                "approved_at": now,
                "pair_code": pair_code,
                "endpoint_token": endpoint_token,
                "endpoint_policy": policy,
                "endpoint_manifest": manifest,
                "credentials": credentials,
                "last_seen_at": now,
            }
        )
        requests[token] = request
        state["requests"] = requests
        _save_state(state)
    access_links.touch_redevice_link(
        _text(request.get("endpoint_id")),
        display_name=_text(display_name) or _text(request.get("device_label")) or "Android ReDevice",
        pair_code=pair_code,
        hub_id=_text(policy.get("hub_id")),
        owner_id=_text(policy.get("owner_id")),
        online=False,
        connection_state="approved",
        trust_level=_text(policy.get("trust_level")) or "limited",
        endpoint_policy=policy,
        endpoint_manifest=manifest,
        diagnostic_report=_mapping(request.get("diagnostic_report")) or None,
    )
    access_links.upsert_link(
        "redevice",
        _text(request.get("endpoint_id")),
        {
            "root_url": _text(policy.get("control_root_url")) or local_control_base_url(),
            "endpoint_root_url": _text(policy.get("root_url")),
            "endpoint_token": endpoint_token,
            "admission_session_id": token,
        },
    )
    return {"ok": True, "state": "approved", "request": _request_public_view(request), "credentials": credentials}


def deny_request(request_id: str, *, reason: str | None = None) -> dict[str, Any]:
    token = _text(request_id)
    if not token:
        return {"ok": False, "error": "request_id_required"}
    now = _now_ts()
    with _LOCK:
        state = _load_state()
        requests = _mapping(state.get("requests"))
        request = _mapping(requests.get(token))
        if not request:
            return {"ok": False, "error": "request_not_found", "request_id": token}
        request.update({"state": "denied", "denied_at": now, "deny_reason": _text(reason), "last_seen_at": now})
        requests[token] = request
        state["requests"] = requests
        _save_state(state)
    endpoint_id = _text(request.get("endpoint_id"))
    if endpoint_id:
        access_links.deny_link("redevice", endpoint_id)
    return {"ok": True, "state": "denied", "request": _request_public_view(request)}


def poll_request(request_id: str) -> dict[str, Any]:
    token = _text(request_id)
    if not token:
        return {"ok": False, "error": "request_id_required"}
    with _LOCK:
        state = _load_state()
        requests = _mapping(state.get("requests"))
        request = _mapping(requests.get(token))
        if not request:
            return {"ok": False, "state": "not_found", "request_id": token}
        request["last_seen_at"] = _now_ts()
        requests[token] = request
        state["requests"] = requests
        _save_state(state)
    return _request_poll_response(request)


def _request_public_view(request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "request_id": _text(request.get("request_id")),
        "endpoint_id": _text(request.get("endpoint_id")),
        "device_label": _text(request.get("device_label")),
        "language": _text(request.get("language")),
        "state": _text(request.get("state")) or "pending",
        "pair_code": _text(request.get("pair_code")),
        "client_host": _text(request.get("client_host")),
        "created_at": request.get("created_at"),
        "created_at_iso": _iso(float(request.get("created_at") or _now_ts())),
        "last_seen_at": request.get("last_seen_at"),
        "last_seen_at_iso": _iso(float(request.get("last_seen_at") or _now_ts())),
        "expires_at": request.get("expires_at"),
        "expires_at_iso": _iso(float(request.get("expires_at") or _now_ts())) if request.get("expires_at") else None,
        "hub": _mapping(request.get("hub")),
    }


def _request_poll_response(request: Mapping[str, Any]) -> dict[str, Any]:
    state = _text(request.get("state")) or "pending"
    response = {
        "ok": True,
        "schema_version": "redevice-lan-admission-poll.v1",
        "request_id": _text(request.get("request_id")),
        "endpoint_id": _text(request.get("endpoint_id")),
        "state": state if state != "pending" else "not_confirmed",
        "retry_after_s": 3,
        "hub": _mapping(request.get("hub")),
        "updated_at": _iso(),
    }
    if state == "approved":
        credentials = _mapping(request.get("credentials"))
        response.update(credentials)
        response["state"] = "credentials"
    elif state == "denied":
        response["state"] = "denied"
        response["reason"] = _text(request.get("deny_reason"))
    return response


def _endpoint_by_code(code: str) -> dict[str, Any] | None:
    token = _text(code)
    if not token:
        return None
    for item in access_links.list_links("redevice"):
        if not _entry_matches_local_scope(item):
            continue
        if _text(item.get("pair_code") or item.get("code")) == token:
            return dict(item)
    return None


def _entry_matches_local_scope(entry: Mapping[str, Any]) -> bool:
    config = _local_config()
    expected_hub = _text(config.get("hub_id"))
    expected_owner = _text(config.get("owner_id"))
    policy = _mapping(entry.get("endpoint_policy"))
    manifest = _mapping(entry.get("endpoint_manifest"))
    hub_id = (
        _text(entry.get("hub_id"))
        or _text(entry.get("subnet_id"))
        or _text(policy.get("hub_id"))
        or _text(policy.get("subnet_id"))
        or _text(manifest.get("hub_id"))
        or _text(manifest.get("subnet_id"))
    )
    owner_id = (
        _text(entry.get("owner_id"))
        or _text(policy.get("owner_id"))
        or _text(policy.get("subnet_owner_id"))
        or _text(manifest.get("owner_id"))
    )
    if expected_hub and hub_id and hub_id != expected_hub:
        return False
    if expected_owner and owner_id and owner_id != expected_owner:
        return False
    if (expected_hub or expected_owner) and not (hub_id or owner_id):
        return False
    return True


def _entry_endpoint_id(entry: Mapping[str, Any]) -> str:
    policy = _mapping(entry.get("endpoint_policy"))
    profile = _mapping(policy.get("transport_profile")) or _mapping(policy.get("transport_policy"))
    manifest = _mapping(entry.get("endpoint_manifest"))
    return (
        _text(policy.get("endpoint_id"))
        or _text(profile.get("endpoint_id"))
        or _text(entry.get("endpoint_id"))
        or _text(entry.get("id"))
        or _text(manifest.get("endpoint_id"))
        or _text(entry.get("pair_code") or entry.get("code"))
    )


def _entry_primary_endpoint_id(entry: Mapping[str, Any]) -> str:
    return _text(entry.get("id") or entry.get("endpoint_id"))


def _entry_rank(entry: Mapping[str, Any], endpoint_id: str) -> tuple[int, int, float]:
    state = _text(entry.get("connection_state"))
    state_rank = 0 if state in {"online", "stale"} or bool(entry.get("online")) else 1
    try:
        last_seen = float(entry.get("last_seen_at") or 0.0)
    except Exception:
        last_seen = 0.0
    return (state_rank, 0 if _entry_primary_endpoint_id(entry) == endpoint_id else 1, -last_seen)


def _current_entries(entries: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    anonymous: list[dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        endpoint_id = _entry_endpoint_id(item)
        if not endpoint_id:
            anonymous.append(item)
            continue
        groups.setdefault(endpoint_id, []).append(item)

    current: list[dict[str, Any]] = []
    for endpoint_id, items in groups.items():
        items.sort(key=lambda item: _entry_rank(item, endpoint_id))
        item = dict(items[0])
        alias_ids: list[str] = []
        seen_alias_ids: set[str] = set()
        history: list[dict[str, Any]] = []
        for candidate in items:
            alias_id = _entry_primary_endpoint_id(candidate)
            if alias_id and alias_id != endpoint_id and alias_id not in seen_alias_ids:
                seen_alias_ids.add(alias_id)
                alias_ids.append(alias_id)
        for candidate in items[1:]:
            history.append(
                {
                    "code": _text(candidate.get("pair_code") or candidate.get("code")),
                    "endpoint_id": _entry_primary_endpoint_id(candidate),
                    "connection_state": _text(candidate.get("connection_state")),
                    "last_seen_at": candidate.get("last_seen_at"),
                }
            )
        item["_canonical_endpoint_id"] = endpoint_id
        if alias_ids:
            item["_endpoint_alias_ids"] = alias_ids
        if history:
            item["_admission_history"] = history
        current.append(item)
    current.extend(anonymous)
    current.sort(
        key=lambda item: _entry_rank(
            item,
            _text(item.get("_canonical_endpoint_id")) or _entry_endpoint_id(item),
        )
    )
    return current


def _authorize_endpoint(code: str, endpoint_token: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    entry = _endpoint_by_code(code)
    if not entry:
        return None, {"ok": False, "error": "endpoint_not_found", "code": _text(code)}
    expected = _text(entry.get("endpoint_token"))
    presented = _text(endpoint_token)
    if expected and presented != expected:
        return None, {"ok": False, "error": "endpoint_token_invalid", "code": _text(code)}
    if bool(entry.get("revoked")) or _text(entry.get("admission_policy")) == "deny":
        return None, {"ok": False, "error": "endpoint_revoked", "code": _text(code)}
    return entry, None


def list_devices() -> dict[str, Any]:
    devices = []
    state = _load_state()
    commands = _mapping(state.get("commands"))
    events = _mapping(state.get("events"))
    acks = _mapping(state.get("acks"))
    now = _now_ts()
    entries: list[dict[str, Any]] = []
    for entry in access_links.list_links("redevice"):
        item = dict(entry)
        if not _entry_matches_local_scope(item):
            continue
        if _text(item.get("admission_policy")) == "deny":
            continue
        entries.append(item)
    for item in _current_entries(entries):
        endpoint_id = _text(item.get("_canonical_endpoint_id")) or _entry_endpoint_id(item)
        pair_code = _text(item.get("pair_code") or item.get("code"))
        command_queue = [dict(command) for command in _list(commands.get(pair_code)) if isinstance(command, Mapping)]
        event_history = [dict(event) for event in _list(events.get(pair_code)) if isinstance(event, Mapping)]
        ack_history = [dict(ack) for ack in _list(acks.get(pair_code)) if isinstance(ack, Mapping)]
        device = {
            "code": pair_code,
            "pair_code": pair_code,
            "endpoint_id": endpoint_id,
            "endpoint_alias_ids": list(item.get("_endpoint_alias_ids") or []),
            "admission_history": list(item.get("_admission_history") or []),
            "display_name": _text(item.get("display_name")) or "ReDevice",
            "device_label": _text(item.get("display_name")) or "ReDevice",
            "state": "consumed" if _text(item.get("connection_state")) in {"online", "stale"} else "approved",
            "hub_id": _text(item.get("hub_id")),
            "subnet_id": _text(item.get("subnet_id") or item.get("hub_id")),
            "owner_id": _text(item.get("owner_id")),
            "endpoint_policy": _mapping(item.get("endpoint_policy")),
            "endpoint_manifest": _mapping(item.get("endpoint_manifest")),
            "diagnostic_report": _mapping(item.get("diagnostic_report")),
            "endpoint_health": _mapping(item.get("endpoint_health")),
            "service_state": _mapping(item.get("service_state")),
            "active_app": _mapping(item.get("active_app")) or None,
            "active_surface": _mapping(item.get("active_surface")) or None,
            "last_seen_at": item.get("last_seen_at"),
            "endpoint_token": _text(item.get("endpoint_token")),
            "root_url": _text(item.get("root_url")),
            "pending_command_count": len(command_queue),
            "inflight_command_count": len([command for command in command_queue if float(command.get("leased_until") or 0.0) > now]),
            "last_command": command_queue[-1] if command_queue else None,
            "last_event": event_history[-1] if event_history else None,
            "last_ack": ack_history[-1] if ack_history else None,
        }
        devices.append(device)
    return {"ok": True, "devices": devices, "count": len(devices), "updated_at": _iso()}


def enqueue_command(code: str, command: Mapping[str, Any]) -> dict[str, Any]:
    token = _text(code)
    if not token:
        return {"ok": False, "error": "code_required"}
    entry = _endpoint_by_code(token)
    if not entry:
        return {"ok": False, "error": "endpoint_not_found", "code": token}
    payload = dict(command or {})
    payload.setdefault("command_id", f"cmd:local:{uuid.uuid4().hex[:16]}")
    payload.setdefault("created_at", _iso())
    payload.setdefault("expires_at", _now_ts() + _COMMAND_TTL_S)
    with _LOCK:
        state = _load_state()
        commands = _mapping(state.get("commands"))
        queue = [dict(item) for item in _list(commands.get(token)) if isinstance(item, Mapping)]
        queue.append(payload)
        commands[token] = queue[-_MAX_COMMANDS_PER_ENDPOINT:]
        state["commands"] = commands
        _save_state(state)
    return {"ok": True, "code": token, "command_id": _text(payload.get("command_id")), "state": "queued"}


def next_command(code: str, *, endpoint_token: str | None = None) -> dict[str, Any]:
    token = _text(code)
    entry, error = _authorize_endpoint(token, endpoint_token)
    if error:
        return error
    now = _now_ts()
    with _LOCK:
        state = _load_state()
        commands = _mapping(state.get("commands"))
        queue = [dict(item) for item in _list(commands.get(token)) if isinstance(item, Mapping)]
        command: dict[str, Any] | None = None
        retained: list[dict[str, Any]] = []
        for candidate in queue:
            expires_at = float(candidate.get("expires_at") or 0.0)
            if expires_at > 0 and expires_at <= now:
                continue
            if command is None:
                leased_until = float(candidate.get("leased_until") or 0.0)
                if leased_until <= now:
                    command = dict(candidate)
                    command["delivery_attempts"] = int(command.get("delivery_attempts") or 0) + 1
                    command["leased_until"] = now + _COMMAND_LEASE_S
                    candidate = dict(command)
            retained.append(candidate)
        commands[token] = retained[-_MAX_COMMANDS_PER_ENDPOINT:]
        state["commands"] = commands
        _save_state(state)
    access_links.touch_redevice_link(
        _text(entry.get("id")),
        pair_code=token,
        hub_id=_text(entry.get("hub_id")),
        owner_id=_text(entry.get("owner_id")),
        online=True,
        connection_state="online",
        endpoint_policy=_mapping(entry.get("endpoint_policy")) or None,
        endpoint_manifest=_mapping(entry.get("endpoint_manifest")) or None,
    )
    return {"ok": True, "command": command, "updated_at": _iso()}


def heartbeat(code: str, payload: Mapping[str, Any] | None = None, *, endpoint_token: str | None = None) -> dict[str, Any]:
    token = _text(code)
    entry, error = _authorize_endpoint(token, endpoint_token)
    if error:
        return error
    data = _mapping(payload)
    endpoint_health = _mapping(data.get("endpoint_health"))
    service_state = _mapping(data.get("service_state"))
    active_app = _mapping(data.get("active_app"))
    active_surface = _mapping(data.get("active_surface"))
    saved = access_links.touch_redevice_link(
        _text(entry.get("id")),
        pair_code=token,
        hub_id=_text(entry.get("hub_id")),
        owner_id=_text(entry.get("owner_id")),
        online=True,
        connection_state="online",
        endpoint_policy=_mapping(entry.get("endpoint_policy")) or None,
        endpoint_manifest=_mapping(entry.get("endpoint_manifest")) or None,
        endpoint_health=endpoint_health or None,
        service_state=service_state or None,
        active_app=active_app or None,
        active_surface=active_surface or None,
    )
    response_entry = saved if isinstance(saved, Mapping) else entry
    policy = _mapping(response_entry.get("endpoint_policy"))
    manifest = _mapping(response_entry.get("endpoint_manifest"))
    return {
        "ok": True,
        "code": token,
        "pair_code": token,
        "endpoint_id": _text(response_entry.get("id")),
        "connection_state": "online",
        "root_url": _text(response_entry.get("endpoint_root_url")) or _text(policy.get("root_url")) or _text(manifest.get("root_url")),
        "control_root_url": _text(response_entry.get("root_url")) or _text(policy.get("control_root_url")),
        "updated_at": _iso(),
    }


def record_event(code: str, event: Mapping[str, Any], *, endpoint_token: str | None = None) -> dict[str, Any]:
    token = _text(code)
    entry, error = _authorize_endpoint(token, endpoint_token)
    if error:
        return error
    payload = dict(event or {})
    payload.setdefault("recorded_at", _iso())
    active_app = _mapping(payload.get("active_app"))
    surface = _mapping(payload.get("surface"))
    if not surface:
        surface = _mapping(payload.get("active_surface"))
    with _LOCK:
        state = _load_state()
        events = _mapping(state.get("events"))
        history = [dict(item) for item in _list(events.get(token)) if isinstance(item, Mapping)]
        history.append(payload)
        events[token] = history[-_MAX_EVENTS_PER_ENDPOINT:]
        state["events"] = events
        _save_state(state)
    access_links.touch_redevice_link(
        _text(entry.get("id")),
        pair_code=token,
        hub_id=_text(entry.get("hub_id")),
        owner_id=_text(entry.get("owner_id")),
        online=True,
        connection_state="online",
        endpoint_policy=_mapping(entry.get("endpoint_policy")) or None,
        endpoint_manifest=_mapping(entry.get("endpoint_manifest")) or None,
        active_app=active_app or None,
        active_surface=surface or None,
        service_state={"last_event": payload},
    )
    return {"ok": True, "code": token, "state": "recorded", "updated_at": _iso()}


def ack_command(code: str, command_id: str, ack: Mapping[str, Any], *, endpoint_token: str | None = None) -> dict[str, Any]:
    token = _text(code)
    _entry, error = _authorize_endpoint(token, endpoint_token)
    if error:
        return error
    ack_id = _text(command_id)
    payload = dict(ack or {})
    payload.setdefault("command_id", ack_id)
    payload.setdefault("recorded_at", _iso())
    with _LOCK:
        state = _load_state()
        commands = _mapping(state.get("commands"))
        if ack_id:
            queue = [dict(item) for item in _list(commands.get(token)) if isinstance(item, Mapping)]
            commands[token] = [item for item in queue if _command_id(item) != ack_id][-_MAX_COMMANDS_PER_ENDPOINT:]
            state["commands"] = commands
        acks = _mapping(state.get("acks"))
        history = [dict(item) for item in _list(acks.get(token)) if isinstance(item, Mapping)]
        history.append(payload)
        acks[token] = history[-_MAX_EVENTS_PER_ENDPOINT:]
        state["acks"] = acks
        _save_state(state)
    return {"ok": True, "code": token, "command_id": ack_id, "state": "acknowledged"}


def update_profile(code: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    token = _text(code)
    entry = _endpoint_by_code(token)
    if not entry:
        return {"ok": False, "error": "endpoint_not_found", "code": token}
    endpoint_id = _text(entry.get("id"))
    display_name = payload.get("display_name")
    if display_name is not None:
        access_links.rename_link("redevice", endpoint_id, _text(display_name))
    aliases = payload.get("aliases")
    if isinstance(aliases, list):
        access_links.upsert_link("redevice", endpoint_id, {"aliases": [_text(item) for item in aliases if _text(item)]})
    return {"ok": True, "code": token, "endpoint_id": endpoint_id, "updated_at": _iso()}


def revoke(code: str) -> dict[str, Any]:
    entry = _endpoint_by_code(code)
    if not entry:
        return {"ok": False, "error": "endpoint_not_found", "code": _text(code)}
    saved = access_links.deny_link("redevice", _text(entry.get("id")))
    return {"ok": True, "code": _text(code), "endpoint_id": _text(entry.get("id")), "entry": saved}


def retire(code: str) -> dict[str, Any]:
    entry = _endpoint_by_code(code)
    if not entry:
        return {"ok": False, "error": "endpoint_not_found", "code": _text(code)}
    saved = access_links.detach_link("redevice", _text(entry.get("id")))
    return {"ok": True, "code": _text(code), "endpoint_id": _text(entry.get("id")), "entry": saved}


@subscribe("redevice.lan_admission.response")
def on_lan_admission_pending_response(evt: Any) -> None:
    payload = getattr(evt, "payload", evt)
    data = _mapping(payload)
    response = _mapping(data.get("response"))
    action = _mapping(data.get("pending_action"))
    metadata = _mapping(action.get("metadata"))
    request_id = _text(metadata.get("request_id")) or _text(_mapping(data.get("domain_ref")).get("request_id"))
    response_action = _text(response.get("response_action_id"))
    if not request_id:
        return
    if response_action == "approve":
        approve_request(request_id)
    elif response_action in {"refuse", "deny"}:
        deny_request(request_id, reason="pending_action_refused")
