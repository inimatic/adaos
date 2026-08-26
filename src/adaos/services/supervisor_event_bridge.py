from __future__ import annotations

import json
import time
from ipaddress import ip_address
from typing import Any, Mapping

from adaos.domain import Event
from adaos.services.agent_context import get_ctx
from adaos.services.core_update import build_public_update_status_payload


MAX_SUPERVISOR_EVENT_BYTES = 256 * 1024
_ALLOWED_TOPICS = frozenset({"core.update.status"})


class SupervisorEventBridgeError(ValueError):
    def __init__(self, code: str, *, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def publish_supervisor_event(
    *,
    topic: str,
    payload: Mapping[str, Any],
    remote_host: str,
) -> dict[str, Any]:
    _require_loopback(remote_host)
    event_type = str(topic or "").strip()
    if event_type not in _ALLOWED_TOPICS:
        raise SupervisorEventBridgeError(
            "supervisor_event_topic_denied",
            status_code=403,
        )
    event_payload = dict(payload or {})
    try:
        encoded = json.dumps(
            event_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except Exception as exc:
        raise SupervisorEventBridgeError("supervisor_event_payload_invalid") from exc
    if len(encoded) > MAX_SUPERVISOR_EVENT_BYTES:
        raise SupervisorEventBridgeError(
            "supervisor_event_payload_too_large",
            status_code=413,
        )

    observed_at = float(event_payload.get("updated_at") or time.time())
    bus = get_ctx().bus
    bus.publish(
        Event(
            type=event_type,
            payload=event_payload,
            source="supervisor.event_bridge",
            ts=observed_at,
        )
    )
    bus.publish(
        Event(
            type="supervisor.update.status.raw",
            payload=build_public_update_status_payload(
                event_payload,
                served_by="supervisor_event_bridge",
            ),
            source="supervisor.event_bridge",
            ts=observed_at,
        )
    )
    return {
        "ok": True,
        "schema": "adaos.supervisor_event_bridge.ack.v1",
        "topic": event_type,
        "payload_bytes": len(encoded),
        "published_topics": [event_type, "supervisor.update.status.raw"],
    }


def _require_loopback(remote_host: str) -> None:
    host = str(remote_host or "").strip()
    try:
        is_loopback = ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback:
        raise SupervisorEventBridgeError(
            "supervisor_event_loopback_required",
            status_code=403,
        )
