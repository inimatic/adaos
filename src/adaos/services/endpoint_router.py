from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _iso_now() -> str:
    return datetime.fromtimestamp(time.time(), tz=timezone.utc).replace(microsecond=0).isoformat()


def service_for_command(command_type: str | None) -> str:
    token = _text(command_type)
    if token.startswith(("display.", "surface.", "slideshow.")):
        return "display_endpoint"
    if token.startswith(("audio.capture", "audio.input", "audio.stream.in")):
        return "audio_input_endpoint"
    if token.startswith(("audio.output", "audio.play", "voice.prompt")):
        return "audio_output_endpoint"
    if token.startswith(("settings.", "system.", "device.")):
        return "settings_endpoint"
    if token.startswith(("sensor.", "gps.", "location.")):
        return "sensor_endpoint"
    if token.startswith(("camera.", "qr.")):
        return "camera_endpoint"
    return "endpoint_service"


def command_type(command: Mapping[str, Any] | None) -> str:
    payload = _mapping(command)
    return _text(payload.get("type") or payload.get("command_type") or "endpoint.command")


def build_endpoint_command(
    command: Mapping[str, Any] | None,
    *,
    endpoint: Mapping[str, Any] | None = None,
    device_ref: str | None = None,
    code: str | None = None,
    requested_by: Mapping[str, Any] | None = None,
    transport: Mapping[str, Any] | None = None,
    constraints: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _mapping(command)
    endpoint_map = _mapping(endpoint)
    identity = _mapping(endpoint_map.get("identity"))
    policy = _mapping(endpoint_map.get("policy"))
    cmd_type = command_type(payload)
    endpoint_id = _text(endpoint_map.get("endpoint_id") or identity.get("endpoint_id") or identity.get("node_id") or endpoint_map.get("id"))
    pair_code = _text(code or endpoint_map.get("code") or endpoint_map.get("pair_code") or identity.get("pair_code") or payload.get("code"))
    command_id = _text(payload.get("command_id") or payload.get("id")) or f"cmd:{uuid.uuid4().hex}"
    target = {
        "kind": "redevice",
        "device_ref": _text(device_ref) or (f"redevice:{endpoint_id}" if endpoint_id else ""),
        "endpoint_id": endpoint_id or None,
        "code": pair_code or None,
        "service": service_for_command(cmd_type),
    }
    target = {key: value for key, value in target.items() if value not in ("", None)}
    envelope: dict[str, Any] = {
        "schema_version": "endpoint-command.v1",
        "command_id": command_id,
        "type": cmd_type,
        "target": target,
        "payload": payload,
        "constraints": dict(constraints or {}),
        "requested_by": dict(requested_by or {}),
        "created_at": _iso_now(),
        "evidence": {
            "policy_present": bool(policy),
            "transport": dict(transport or {}),
            "compatibility_bridge": "redevice_root_command",
        },
    }
    if "idempotency_key" not in envelope["constraints"]:
        idempotency = _text(payload.get("idempotency_key") or payload.get("command_id") or "")
        if idempotency:
            envelope["constraints"]["idempotency_key"] = idempotency
    return envelope


def legacy_payload_from_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    payload = _mapping(envelope.get("payload"))
    if "command_id" not in payload:
        payload["command_id"] = _text(envelope.get("command_id"))
    if "endpoint_command" not in payload:
        payload["endpoint_command"] = {
            "schema_version": _text(envelope.get("schema_version")),
            "command_id": _text(envelope.get("command_id")),
            "target": _mapping(envelope.get("target")),
            "requested_by": _mapping(envelope.get("requested_by")),
            "constraints": _mapping(envelope.get("constraints")),
        }
    return payload


def _endpoint_ref(endpoint: Mapping[str, Any] | None, code: str | None = None, device_ref: str | None = None) -> str:
    endpoint_map = _mapping(endpoint)
    identity = _mapping(endpoint_map.get("identity"))
    endpoint_id = _text(endpoint_map.get("endpoint_id") or identity.get("endpoint_id") or identity.get("node_id") or endpoint_map.get("id"))
    if _text(device_ref):
        return _text(device_ref)
    if endpoint_id:
        return f"redevice:{endpoint_id}"
    pair_code = _text(code or endpoint_map.get("code") or endpoint_map.get("pair_code") or identity.get("pair_code"))
    return f"redevice:{pair_code}" if pair_code else ""


def send_redevice_command(
    endpoint: Mapping[str, Any] | None,
    code: str,
    command: Mapping[str, Any] | None,
    *,
    device_ref: str | None = None,
    requested_by: Mapping[str, Any] | None = None,
    constraints: Mapping[str, Any] | None = None,
    timeout: int | float = 12,
) -> dict[str, Any]:
    """Route a command to a ReDevice endpoint through the current compatibility path.

    The important ownership boundary is that skills and SDK callers receive a
    generic endpoint-command envelope and router evidence. The legacy root
    command queue remains an implementation detail until LAN/WebRTC media
    sessions are stable enough to replace it.
    """

    pair_code = _text(code)
    if not pair_code:
        return {"ok": False, "error": "endpoint_ref_required", "code": pair_code}
    endpoint_map = _mapping(endpoint)
    payload = _mapping(command)
    try:
        from adaos.sdk.redevice import ReDeviceBridge, endpoint_root_base, select_transport

        transport = select_transport(endpoint_map, intent=_text(payload.get("type")) or "endpoint.command")
        endpoint_command = build_endpoint_command(
            payload,
            endpoint=endpoint_map,
            device_ref=_endpoint_ref(endpoint_map, pair_code, device_ref),
            code=pair_code,
            requested_by=requested_by,
            transport=transport,
            constraints=constraints,
        )
        legacy_payload = legacy_payload_from_envelope(endpoint_command)
        root_base = endpoint_root_base(endpoint_map)
        try:
            bridge = ReDeviceBridge(root_base=root_base, timeout=timeout)
        except TypeError:
            bridge = ReDeviceBridge(timeout=timeout)
        result = bridge.send_command(pair_code, legacy_payload)
        router_evidence = {
            "schema_version": "endpoint-router-result.v1",
            "route": "redevice_compat_command_queue",
            "target": dict(endpoint_command.get("target") or {}),
            "transport": transport,
            "legacy_payload": True,
            "updated_at": _iso_now(),
        }
        return {
            **dict(result or {}),
            "device_ref": _endpoint_ref(endpoint_map, pair_code, device_ref),
            "code": pair_code,
            "endpoint": endpoint_map or None,
            "transport": transport,
            "endpoint_command": endpoint_command,
            "endpoint_router": router_evidence,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "endpoint_command_failed",
            "detail": str(exc),
            "device_ref": _endpoint_ref(endpoint_map, pair_code, device_ref),
            "code": pair_code,
        }


def build_media_session(
    *,
    endpoint: Mapping[str, Any] | None = None,
    code: str | None = None,
    owner: Mapping[str, Any] | None = None,
    intent: str = "display.media",
    item_count: int = 0,
    primary_transport: str | None = None,
    fallback_transport: str | None = None,
    inline_fallback: bool = False,
) -> dict[str, Any]:
    endpoint_map = _mapping(endpoint)
    session_id = "ems:" + uuid.uuid4().hex
    return {
        "schema_version": "endpoint-media-session.v1",
        "session_id": session_id,
        "intent": _text(intent) or "display.media",
        "target": {
            key: value
            for key, value in {
                "kind": "redevice",
                "device_ref": _endpoint_ref(endpoint_map, code),
                "code": _text(code),
            }.items()
            if value
        },
        "owner": dict(owner or {}),
        "item_count": max(0, int(item_count or 0)),
        "primary_transport": _text(primary_transport) or "endpoint_media_pull",
        "fallback_transport": _text(fallback_transport) or "root_relay_inline",
        "inline_fallback": bool(inline_fallback),
        "created_at": _iso_now(),
    }


__all__ = [
    "build_endpoint_command",
    "build_media_session",
    "command_type",
    "legacy_payload_from_envelope",
    "send_redevice_command",
    "service_for_command",
]
