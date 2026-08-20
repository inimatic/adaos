from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections import deque
from ipaddress import ip_address
from typing import Any, Mapping

from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit


MAX_SERVICE_EVENT_BYTES = 256 * 1024
MAX_SERVICE_EVENTS_PER_SECOND = 50
_ALLOWED_TOPICS = {
    "io.out.chat.append",
    "io.out.media.route",
    "io.out.say",
    "io.out.stream.publish",
}
_lock = threading.Lock()
_skill_tokens: dict[str, str] = {}
_token_skills: dict[str, str] = {}
_token_windows: dict[str, deque[float]] = {}


class ServiceEventBridgeError(ValueError):
    def __init__(self, code: str, *, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def service_event_bridge_environment(skill_name: str) -> dict[str, str]:
    skill = str(skill_name or "").strip()
    if not skill:
        raise ValueError("service_skill_required")
    try:
        port = int(str(os.getenv("ADAOS_RUNTIME_PORT") or "8777").strip())
    except ValueError:
        port = 8777
    port = max(1, min(port, 65535))
    return {
        "ADAOS_SERVICE_EVENT_BRIDGE_URL": (
            f"http://127.0.0.1:{port}/api/node/internal/service-events"
        ),
        "ADAOS_SERVICE_EVENT_BRIDGE_TOKEN": _issue_token(skill),
    }


def publish_service_event(
    *,
    token: str,
    topic: str,
    payload: Mapping[str, Any],
    remote_host: str,
) -> dict[str, Any]:
    _require_loopback(remote_host)
    presented = str(token or "").strip()
    with _lock:
        skill = _token_skills.get(presented, "")
        if not skill:
            raise ServiceEventBridgeError(
                "service_event_token_invalid", status_code=401
            )
        _admit_rate_locked(presented)
    event_type = str(topic or "").strip()
    if event_type not in _ALLOWED_TOPICS:
        raise ServiceEventBridgeError(
            "service_event_topic_denied", status_code=403
        )
    event_payload = dict(payload or {})
    metadata = event_payload.get("_meta")
    if metadata is not None and not isinstance(metadata, Mapping):
        raise ServiceEventBridgeError("service_event_meta_invalid")
    normalized_meta = dict(metadata or {})
    normalized_meta["skill_name"] = skill
    normalized_meta["owner"] = f"skill:{skill}"
    normalized_meta["service_bridge"] = True
    event_payload["_meta"] = normalized_meta
    try:
        encoded = json.dumps(
            event_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except Exception as exc:
        raise ServiceEventBridgeError("service_event_payload_invalid") from exc
    if len(encoded) > MAX_SERVICE_EVENT_BYTES:
        raise ServiceEventBridgeError(
            "service_event_payload_too_large", status_code=413
        )
    ctx = get_ctx()
    emit(
        ctx.bus,
        event_type,
        event_payload,
        source=f"sdk.io.service:{skill}",
        source_authority=f"skill:{skill}",
    )
    return {
        "ok": True,
        "schema": "adaos.service_event_bridge.ack.v1",
        "skill": skill,
        "topic": event_type,
        "payload_bytes": len(encoded),
    }


def _issue_token(skill: str) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        previous = _skill_tokens.get(skill)
        if previous:
            _token_skills.pop(previous, None)
            _token_windows.pop(previous, None)
        _skill_tokens[skill] = token
        _token_skills[token] = skill
        _token_windows[token] = deque()
    return token


def _require_loopback(remote_host: str) -> None:
    host = str(remote_host or "").strip()
    try:
        is_loopback = ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback:
        raise ServiceEventBridgeError(
            "service_event_loopback_required", status_code=403
        )


def _admit_rate_locked(token: str) -> None:
    now = time.monotonic()
    window = _token_windows.setdefault(token, deque())
    while window and now - window[0] >= 1.0:
        window.popleft()
    if len(window) >= MAX_SERVICE_EVENTS_PER_SECOND:
        raise ServiceEventBridgeError(
            "service_event_rate_limited", status_code=429
        )
    window.append(now)
