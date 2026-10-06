"""Demand-driven hardware telemetry for the Management System surface.

The selected Application runtime can be an immutable Trial.  Trial tools are
resolved on demand, but their module-level event subscriptions are deliberately
not installed in the global process.  Hardware telemetry is a node-owned
control-plane projection, so the core owns its subscription lifecycle and only
samples while a browser is actually displaying the selected node.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections.abc import Mapping
from typing import Any

from adaos.domain.node_identity import node_identities_match
from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import AgentContext, get_ctx, use_ctx
from adaos.services.eventbus import emit


RECEIVER = "web_desktop.system.hardware"
_INTERVAL_S = 5.0
_LOG = logging.getLogger("adaos.system.hardware_stream")
_LOCK = threading.RLock()
_SUBSCRIPTIONS: dict[tuple[str, str], set[str]] = {}
_WORKERS: dict[tuple[str, str], tuple[threading.Event, threading.Thread]] = {}


def _payload(event: Any) -> dict[str, Any]:
    value = getattr(event, "payload", event)
    return dict(value) if isinstance(value, Mapping) else {}


def _meta(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = value.get("_meta")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _webspace(value: Mapping[str, Any]) -> str:
    return str(value.get("webspace_id") or value.get("workspace_id") or "default").strip() or "default"


def _node(value: Mapping[str, Any]) -> str:
    meta = _meta(value)
    return str(
        value.get("target_node_id")
        or value.get("node_target_id")
        or value.get("node_id")
        or meta.get("target_node_id")
        or meta.get("node_target_id")
        or meta.get("node_id")
        or ""
    ).strip()


def _targets_local_node(ctx: AgentContext, node_id: str) -> bool:
    if not node_id:
        return True
    config = getattr(ctx, "config", None)
    local_id = str(
        getattr(config, "node_id_value", "")
        or getattr(config, "node_id", "")
        or ""
    ).strip()
    return bool(local_id and node_identities_match(node_id, local_id))


def _subscription_id(value: Mapping[str, Any], webspace_id: str, node_id: str) -> str:
    explicit = str(value.get("subscription_id") or value.get("connection_id") or "").strip()
    if explicit:
        return explicit
    params = value.get("params") if isinstance(value.get("params"), Mapping) else {}
    stable = json.dumps(
        {
            "topic": str(value.get("topic") or RECEIVER),
            "webspace_id": webspace_id,
            "node_id": node_id,
            "params": params,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "command:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _bytes_display(value: Any) -> str | None:
    number = _number(value)
    if number is None:
        return None
    amount = number
    unit = "B"
    for candidate in ("B", "kB", "MB", "GB", "TB"):
        unit = candidate
        if amount < 1000 or candidate == "TB":
            break
        amount /= 1000
    return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"


def _project(snapshot: Mapping[str, Any], *, node_id: str) -> dict[str, Any]:
    resources = snapshot.get("resources") if isinstance(snapshot.get("resources"), Mapping) else {}
    available = resources.get("available") is True
    freshness = str(resources.get("freshness") or "unavailable")
    metrics: list[dict[str, Any]] = []
    for key, label in (("cpu", "CPU"), ("memory", "RAM"), ("disk", "Disk")):
        sample = resources.get(key) if isinstance(resources.get(key), Mapping) else {}
        percent = _number(sample.get("percent")) if available else None
        if percent is not None and percent > 100:
            percent = None
        display = f"{percent:g}%" if percent is not None else "Unavailable"
        used = _bytes_display(sample.get("used_bytes"))
        total = _bytes_display(sample.get("total_bytes"))
        if percent is not None and used and total:
            display += f" · {used} / {total}"
        metrics.append(
            {
                "id": key,
                "label": label,
                "value": percent,
                "display": display,
                "description": freshness,
            }
        )
    cpu = metrics[0]
    observed_at = resources.get("observed_at") or snapshot.get("observed_at")
    return {
        "observed_at": observed_at,
        "hardware_metrics": metrics,
        "center": {
            "value": cpu["display"] if cpu["value"] is not None else "—",
            "label": "CPU" if cpu["value"] is not None else "CPU unavailable",
        },
        "subtitle": observed_at or "Observation time unavailable",
        "value": freshness,
        "label": " · ".join(f"{item['label']} {item['display']}" for item in metrics),
        "description": "Last reported hardware telemetry",
        "freshness": freshness,
        "node_id": node_id,
    }


def _publish(ctx: AgentContext, *, webspace_id: str, node_id: str) -> None:
    try:
        from adaos.sdk.system import get_operational_snapshot

        with use_ctx(ctx):
            snapshot = get_operational_snapshot(
                sections={"summary", "resources"},
                webspace_id=webspace_id,
                limit=1,
            )
        data = _project(snapshot if isinstance(snapshot, Mapping) else {}, node_id=node_id)
    except Exception as exc:
        _LOG.debug("hardware telemetry sample failed", exc_info=True)
        data = {
            "hardware_metrics": [],
            "center": {"value": "—", "label": "Unavailable"},
            "subtitle": "",
            "description": "Hardware telemetry is temporarily unavailable.",
            "freshness": "unavailable",
            "node_id": node_id,
            "reason": type(exc).__name__,
        }
    event_meta: dict[str, Any] = {"webspace_id": webspace_id}
    if node_id:
        event_meta.update(
            node_id=node_id,
            source_node_id=node_id,
            target_node_id=node_id,
            # The receiver is parameterized by the selected node.  The browser
            # rejects an otherwise valid snapshot when this correlation value
            # is omitted, because it cannot distinguish a stale selection.
            params={"target_node_id": node_id},
        )
    emit(
        ctx.bus,
        "io.out.stream.publish",
        {
            "receiver": RECEIVER,
            "data": data,
            "ts": time.time(),
            "_meta": event_meta,
        },
        "core.system.hardware_stream",
    )


def _worker(key: tuple[str, str], stop: threading.Event, ctx: AgentContext) -> None:
    webspace_id, node_id = key
    while not stop.is_set():
        _publish(ctx, webspace_id=webspace_id, node_id=node_id)
        stop.wait(_INTERVAL_S)
    with _LOCK:
        current = _WORKERS.get(key)
        if current is not None and current[0] is stop:
            _WORKERS.pop(key, None)


def _ensure_worker(key: tuple[str, str], ctx: AgentContext) -> None:
    with _LOCK:
        current = _WORKERS.get(key)
        if current is not None and current[1].is_alive():
            return
        stop = threading.Event()
        worker = threading.Thread(
            target=_worker,
            args=(key, stop, ctx),
            name=f"adaos-hardware-stream-{key[1] or 'local'}",
            daemon=True,
        )
        _WORKERS[key] = (stop, worker)
        worker.start()


def _stop_worker_if_idle(key: tuple[str, str]) -> None:
    with _LOCK:
        if _SUBSCRIPTIONS.get(key):
            return
        _SUBSCRIPTIONS.pop(key, None)
        current = _WORKERS.pop(key, None)
        if current is not None:
            current[0].set()


@subscribe("webio.stream.snapshot.requested", receivers=(RECEIVER,))
def on_snapshot_requested(event: Any) -> None:
    value = _payload(event)
    if str(value.get("receiver") or "").strip() != RECEIVER:
        return
    ctx = get_ctx()
    node_id = _node(value)
    if not _targets_local_node(ctx, node_id):
        return
    _publish(ctx, webspace_id=_webspace(value), node_id=node_id)


@subscribe("webio.stream.subscription.changed", receivers=(RECEIVER,))
def on_subscription_changed(event: Any) -> None:
    value = _payload(event)
    if str(value.get("receiver") or "").strip() != RECEIVER:
        return
    ctx = get_ctx()
    node_id = _node(value)
    if not _targets_local_node(ctx, node_id):
        return
    webspace_id = _webspace(value)
    key = (webspace_id, node_id)
    token = _subscription_id(value, webspace_id, node_id)
    action = str(value.get("action") or "subscribed").strip().lower()
    if action == "unsubscribed":
        with _LOCK:
            subscribers = _SUBSCRIPTIONS.get(key)
            if subscribers is not None:
                subscribers.discard(token)
        _stop_worker_if_idle(key)
        return
    with _LOCK:
        _SUBSCRIPTIONS.setdefault(key, set()).add(token)
    _ensure_worker(key, ctx)


__all__ = [
    "RECEIVER",
    "on_snapshot_requested",
    "on_subscription_changed",
]
