from __future__ import annotations

import json
import os
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_SERVICE_EVENT_ENVELOPE_BYTES = 256 * 1024


def configured() -> bool:
    return bool(str(os.getenv("ADAOS_SERVICE_EVENT_BRIDGE_URL") or "").strip())


def publish(topic: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    url = str(os.getenv("ADAOS_SERVICE_EVENT_BRIDGE_URL") or "").strip()
    token = str(os.getenv("ADAOS_SERVICE_EVENT_BRIDGE_TOKEN") or "").strip()
    if not url or not token:
        raise RuntimeError("service_event_bridge_not_configured")
    body = json.dumps(
        {"topic": str(topic or "").strip(), "payload": dict(payload)},
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    if len(body) > MAX_SERVICE_EVENT_ENVELOPE_BYTES:
        raise RuntimeError("service_event_payload_too_large")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-AdaOS-Service-Event-Token": token,
        },
    )
    try:
        with urlopen(request, timeout=2.0) as response:
            status = int(getattr(response, "status", 0) or 0)
            raw = response.read(64 * 1024)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"service_event_bridge_failed:{type(exc).__name__}"
        ) from exc
    if status < 200 or status >= 300:
        raise RuntimeError(f"service_event_bridge_failed:http_{status}")
    try:
        result = json.loads(raw.decode("utf-8")) if raw else {"ok": True}
    except Exception:
        result = {"ok": True}
    return dict(result) if isinstance(result, Mapping) else {"ok": True}


__all__ = ["configured", "publish"]
