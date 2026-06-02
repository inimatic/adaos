from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

TRANSPORT_LADDER = (
    "webrtc_p2p",
    "local_ws",
    "local_http",
    "http_chunked",
    "mjpeg",
    "segment_upload",
    "redevice_poll",
    "root_relay_inline",
    "root_relay",
)

DEFAULT_INLINE_COMMAND_BYTES = 80_000


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root_base(value: str | None = None) -> str:
    raw = (
        value
        or os.environ.get("ADAOS_ROOT_API_BASE")
        or os.environ.get("PUBLIC_ROOT_BASE")
        or os.environ.get("ROOT_API_BASE")
        or "https://ru.api.inimatic.com"
    )
    return _text(raw).rstrip("/")


def _age_seconds(value: Any, *, now: float | None = None) -> int | None:
    try:
        ts = float(value or 0)
    except Exception:
        return None
    if ts <= 0:
        return None
    return max(0, int((now or time.time()) - ts))


def online_state(value: Any, *, sticky_seconds: int = 15 * 60) -> str:
    age = _age_seconds(value)
    if age is None:
        return "unknown"
    if age < 120:
        return "online"
    if age < max(121, int(sticky_seconds)):
        return "stale"
    return "offline"


def display_name(endpoint: Mapping[str, Any]) -> str:
    manifest = _mapping(endpoint.get("endpoint_manifest"))
    return (
        _text(endpoint.get("display_name"))
        or _text(endpoint.get("device_label"))
        or _text(manifest.get("display_name"))
        or _text(endpoint.get("endpoint_id"))
        or _text(endpoint.get("code"))
        or "ReDevice"
    )


def endpoint_id(endpoint: Mapping[str, Any]) -> str:
    manifest = _mapping(endpoint.get("endpoint_manifest"))
    return _text(endpoint.get("endpoint_id")) or _text(manifest.get("endpoint_id")) or _text(endpoint.get("code"))


def pair_code(endpoint: Mapping[str, Any]) -> str:
    return _text(endpoint.get("code") or endpoint.get("pair_code"))


def default_transport_profile(endpoint: Mapping[str, Any] | None = None) -> dict[str, Any]:
    endpoint = endpoint or {}
    return {
        "schema_version": "transport-profile.v1",
        "endpoint_id": endpoint_id(endpoint) or pair_code(endpoint) or "unknown",
        "preferred_order": list(TRANSPORT_LADDER),
        "routes": {
            "webrtc_p2p": {
                "available": False,
                "state": "disabled",
                "directions": ["control", "events", "content_in", "audio_in", "audio_out", "video_in", "video_out", "sensor_out"],
                "requires_signaling": True,
                "reason": "not_negotiated",
            },
            "local_ws": {
                "available": False,
                "state": "disabled",
                "directions": ["control", "events", "content_in", "sensor_out"],
                "reason": "not_advertised",
            },
            "local_http": {
                "available": False,
                "state": "disabled",
                "directions": ["content_in"],
                "reason": "not_advertised",
            },
            "http_chunked": {
                "available": False,
                "state": "disabled",
                "directions": ["content_in", "content_out", "audio_in", "audio_out"],
                "reason": "not_advertised",
            },
            "mjpeg": {
                "available": False,
                "state": "disabled",
                "directions": ["video_in", "video_out"],
                "reason": "not_advertised",
            },
            "segment_upload": {
                "available": False,
                "state": "disabled",
                "directions": ["content_in", "content_out", "audio_in", "audio_out", "video_in", "video_out"],
                "reason": "not_advertised",
            },
            "redevice_poll": {
                "available": True,
                "state": "ready",
                "directions": ["control", "events"],
                "legacy_safe": True,
            },
            "root_relay_inline": {
                "available": True,
                "state": "degraded",
                "directions": ["content_in"],
                "legacy_safe": True,
                "requires_root_relay": True,
                "limits": {"max_inline_command_bytes": DEFAULT_INLINE_COMMAND_BYTES},
            },
            "root_relay": {
                "available": False,
                "state": "disabled",
                "directions": ["control", "events", "content_in", "content_out", "audio_in", "audio_out", "video_in", "video_out"],
                "requires_root_relay": True,
                "reason": "not_configured",
            },
        },
        "fallback_allowed": ["redevice_poll", "root_relay_inline"],
        "limits": {"max_inline_command_bytes": DEFAULT_INLINE_COMMAND_BYTES},
        "updated_at": _iso_now(),
    }


def transport_profile(endpoint: Mapping[str, Any] | None = None) -> dict[str, Any]:
    endpoint = endpoint or {}
    policy = _mapping(endpoint.get("endpoint_policy"))
    manifest = _mapping(endpoint.get("endpoint_manifest"))
    candidates = (
        policy.get("transport_profile"),
        policy.get("transport_policy"),
        manifest.get("transport_profile"),
        endpoint.get("transport_profile"),
    )
    for candidate in candidates:
        profile = _mapping(candidate)
        if profile:
            base = default_transport_profile(endpoint)
            base.update(profile)
            routes = _mapping(base.get("routes"))
            default_routes = _mapping(default_transport_profile(endpoint).get("routes"))
            for key, value in default_routes.items():
                routes.setdefault(key, value)
            base["routes"] = routes
            order = [item for item in list(base.get("preferred_order") or []) if _text(item)]
            if not order:
                order = list(TRANSPORT_LADDER)
            base["preferred_order"] = order
            limits = _mapping(default_transport_profile(endpoint).get("limits"))
            limits.update(_mapping(base.get("limits")))
            base["limits"] = limits
            return base
    return default_transport_profile(endpoint)


def _route_ready(route: Mapping[str, Any]) -> bool:
    state = _text(route.get("state")) or "unknown"
    return bool(route.get("available")) and state not in {"failed", "disabled"}


def _route_supports(route: Mapping[str, Any], direction: str) -> bool:
    return direction in set(_text(item) for item in list(route.get("directions") or []))


def _select_direction(profile: Mapping[str, Any], direction: str, *, allow_root_relay: bool) -> tuple[str, dict[str, Any]]:
    routes = _mapping(profile.get("routes"))
    order = [_text(item) for item in list(profile.get("preferred_order") or TRANSPORT_LADDER)]
    for transport in order:
        if not transport:
            continue
        if not allow_root_relay and transport.startswith("root_relay"):
            continue
        route = _mapping(routes.get(transport))
        if _route_ready(route) and _route_supports(route, direction):
            return transport, route
    return "", {}


def select_transport(
    endpoint: Mapping[str, Any] | None,
    *,
    intent: str = "display.command",
    content_bytes: int = 0,
    allow_root_relay: bool = True,
) -> dict[str, Any]:
    profile = transport_profile(endpoint or {})
    control_transport, control_route = _select_direction(profile, "control", allow_root_relay=allow_root_relay)
    event_transport, event_route = _select_direction(profile, "events", allow_root_relay=allow_root_relay)
    content_transport = ""
    content_route: dict[str, Any] = {}
    if intent.startswith(("display.", "audio.", "content.")):
        content_transport, content_route = _select_direction(profile, "content_in", allow_root_relay=allow_root_relay)
    selected = content_transport or control_transport or event_transport or "unavailable"
    limits = _mapping(profile.get("limits"))
    route_limits = _mapping(content_route.get("limits"))
    max_inline = int(route_limits.get("max_inline_command_bytes") or limits.get("max_inline_command_bytes") or DEFAULT_INLINE_COMMAND_BYTES)
    inline_fits = not content_bytes or content_bytes <= max_inline
    requires_root = bool(content_route.get("requires_root_relay") or control_route.get("requires_root_relay") or selected.startswith("root_relay"))
    degraded = selected in {"redevice_poll", "root_relay_inline", "root_relay"} or bool(content_route.get("state") == "degraded")
    return {
        "schema_version": "transport-selection.v1",
        "intent": intent,
        "selected_transport": selected,
        "control": {
            "transport": control_transport or "unavailable",
            "state": _text(control_route.get("state")) or "unavailable",
        },
        "events": {
            "transport": event_transport or "unavailable",
            "state": _text(event_route.get("state")) or "unavailable",
        },
        "content": {
            "transport": content_transport or "unavailable",
            "state": _text(content_route.get("state")) or "unavailable",
            "inline_fits": inline_fits,
            "content_bytes": int(content_bytes or 0),
            "max_inline_command_bytes": max_inline,
        },
        "fallback_order": list(profile.get("preferred_order") or TRANSPORT_LADDER),
        "requires_root_relay": requires_root,
        "degraded": degraded,
        "legacy_safe": bool(content_route.get("legacy_safe") or control_route.get("legacy_safe")),
        "profile_schema": _text(profile.get("schema_version")) or "transport-profile.v1",
        "updated_at": _iso_now(),
    }


def compact_endpoint(endpoint: Mapping[str, Any], *, selected_codes: set[str] | None = None) -> dict[str, Any]:
    policy = _mapping(endpoint.get("endpoint_policy"))
    manifest = _mapping(endpoint.get("endpoint_manifest"))
    last_event = _mapping(endpoint.get("last_event"))
    active_app = _mapping(endpoint.get("active_app")) or _mapping(last_event.get("active_app"))
    active_surface = _mapping(endpoint.get("active_surface")) or _mapping(last_event.get("active_surface"))
    code = pair_code(endpoint)
    eid = endpoint_id(endpoint)
    state = _text(endpoint.get("state")) or "-"
    online = online_state(endpoint.get("last_seen_at"))
    selected = bool(code and selected_codes and code in selected_codes)
    age = _age_seconds(endpoint.get("last_seen_at"))
    return {
        "id": code or eid,
        "code": code,
        "endpoint_id": eid,
        "title": display_name(endpoint),
        "display_name": display_name(endpoint),
        "state": state,
        "selected": selected,
        "selected_label": "selected" if selected else "",
        "online_state": online,
        "online": online in {"online", "stale"},
        "last_seen_age_s": age,
        "last_seen": "-" if age is None else f"{age}s" if age < 60 else f"{age // 60}m {age % 60}s",
        "zone_id": _text(endpoint.get("zone_id")) or "-",
        "trust_level": _text(policy.get("trust_level") or manifest.get("trust_level")) or "limited",
        "active_app": active_app or None,
        "active_surface": active_surface or None,
        "service_state": _mapping(endpoint.get("service_state")) or None,
        "transport_profile": transport_profile(endpoint),
        "aliases": list(endpoint.get("aliases") or []),
        "labels": list(endpoint.get("labels") or []),
        "selectable": bool(code and state in {"approved", "consumed"}),
        "raw": dict(endpoint),
    }


@dataclass(frozen=True)
class ReDeviceBridge:
    root_base: str | None = None
    timeout: int = 20

    @property
    def base_url(self) -> str:
        return _root_base(self.root_base)

    def request_json(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = None if payload is None else json.dumps(dict(payload)).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method.upper())
        req.add_header("accept", "application/json")
        if body is not None:
            req.add_header("content-type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return json.loads(res.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return {"ok": False, "error": f"http_{exc.code}", "detail": detail}
        except Exception as exc:
            return {"ok": False, "error": "request_failed", "detail": str(exc)}

    def list_endpoints(self, *, sync_registry: bool = True) -> list[dict[str, Any]]:
        res = self.request_json("GET", "/v1/redevice/devices")
        devices = res.get("devices") if isinstance(res, Mapping) else None
        endpoints = [dict(item) for item in devices if isinstance(item, Mapping)] if isinstance(devices, list) else []
        if sync_registry:
            self.sync_local_registry(endpoints)
        return endpoints

    def sync_local_registry(self, endpoints: list[Mapping[str, Any]]) -> None:
        try:
            from adaos.services import access_links
        except Exception:
            return
        for endpoint in endpoints:
            eid = endpoint_id(endpoint)
            if not eid:
                continue
            compact = compact_endpoint(endpoint)
            policy = _mapping(endpoint.get("endpoint_policy"))
            trust = _text(policy.get("trust_level") or compact.get("trust_level")) or "limited"
            try:
                access_links.touch_redevice_link(
                    eid,
                    display_name=display_name(endpoint),
                    online=compact.get("online_state") in {"online", "stale"},
                    connection_state=_text(compact.get("online_state")) or None,
                    trust_level=trust,
                    endpoint_policy=policy or None,
                    diagnostic_report=_mapping(endpoint.get("diagnostic_report")) or None,
                    endpoint_health=_mapping(endpoint.get("endpoint_health")) or None,
                    service_state=_mapping(endpoint.get("service_state")) or None,
                    active_app=_mapping(compact.get("active_app")) or None,
                    active_surface=_mapping(compact.get("active_surface")) or None,
                )
            except Exception:
                continue

    def send_command(self, code: str, command: Mapping[str, Any]) -> dict[str, Any]:
        token = urllib.parse.quote(_text(code), safe="")
        if not token:
            return {"ok": False, "error": "code_required"}
        return self.request_json("POST", f"/v1/redevice/devices/{token}/commands", {"command": dict(command)})

    def update_profile(
        self,
        code: str,
        *,
        display_name: str | None = None,
        aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        token = urllib.parse.quote(_text(code), safe="")
        if not token:
            return {"ok": False, "error": "code_required"}
        payload: dict[str, Any] = {}
        if display_name is not None:
            payload["display_name"] = _text(display_name)
        if aliases is not None:
            payload["aliases"] = [_text(item) for item in aliases if _text(item)]
        return self.request_json("PATCH", f"/v1/redevice/devices/{token}/profile", payload)

    def revoke(self, code: str) -> dict[str, Any]:
        token = urllib.parse.quote(_text(code), safe="")
        if not token:
            return {"ok": False, "error": "code_required"}
        return self.request_json("POST", f"/v1/redevice/devices/{token}/revoke", {})

    def retire(self, code: str) -> dict[str, Any]:
        token = urllib.parse.quote(_text(code), safe="")
        if not token:
            return {"ok": False, "error": "code_required"}
        return self.request_json("POST", f"/v1/redevice/devices/{token}/retire", {})


def bridge(root_base: str | None = None) -> ReDeviceBridge:
    return ReDeviceBridge(root_base=root_base)


def list_endpoints(*, root_base: str | None = None, sync_registry: bool = True) -> list[dict[str, Any]]:
    return bridge(root_base).list_endpoints(sync_registry=sync_registry)


def send_command(code: str, command: Mapping[str, Any], *, root_base: str | None = None) -> dict[str, Any]:
    return bridge(root_base).send_command(code, command)
