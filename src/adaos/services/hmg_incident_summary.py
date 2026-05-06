from __future__ import annotations

from typing import Any


def _top_pair(items: Any) -> tuple[str, int] | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        key = str(item[0] or "").strip()
        if not key:
            continue
        try:
            value = int(item[1] or 0)
        except Exception:
            value = 0
        return (key, value)
    return None


def _first_rebuild_key(snapshot: dict[str, Any]) -> str | None:
    items = snapshot.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in ("key", "node_webspace_key", "node_id", "webspace_id"):
            value = str(item.get(field) or "").strip()
            if value:
                return value
    return None


def _first_rebuild_request_id(snapshot: dict[str, Any]) -> str | None:
    items = snapshot.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in ("correlation_id", "current_request_id", "dirty_request_id", "last_request_id"):
            value = str(item.get(field) or "").strip()
            if value:
                return value
    return None


def build_hmg_incident_summary(
    *,
    route_diagnostics: dict[str, Any] | None = None,
    yjs_pressure: dict[str, Any] | None = None,
    member_snapshot_rebuild: dict[str, Any] | None = None,
    eventbus_backlog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route = dict(route_diagnostics or {})
    yjs = dict(yjs_pressure or {})
    rebuild = dict(member_snapshot_rebuild or {})
    eventbus = dict(eventbus_backlog or {})

    route_guardrail_active = bool(route.get("guardrail_active"))
    route_reason = str(route.get("guardrail_reason") or route.get("status") or "").strip() or None
    route_pending_bytes = int(route.get("pending_data_size") or 0)
    route_pending_oldest_age_s = float(route.get("pending_oldest_age_s") or 0.0)

    yjs_active = bool(yjs.get("active") or yjs.get("pressure_active"))
    yjs_reason = str(yjs.get("reason") or yjs.get("pressure_reason") or "").strip() or None
    yjs_pending_send = int(yjs.get("pending_send_tasks") or 0)
    yjs_pending_store = int(yjs.get("pending_store_tasks") or 0)
    yjs_buffer_used = int(yjs.get("buffer_used") or 0)

    rebuild_active_total = int(rebuild.get("active_total") or rebuild.get("active") or 0)
    rebuild_delayed_total = int(rebuild.get("delayed_total") or 0)
    rebuild_dirty_total = int(rebuild.get("dirty_total") or 0)
    rebuild_top_key = _first_rebuild_key(rebuild)
    rebuild_top_request_id = _first_rebuild_request_id(rebuild)

    eventbus_pending_tasks = int(eventbus.get("pending_tasks") or 0)
    eventbus_bounded_queue_total = int(eventbus.get("bounded_queue_total") or 0)
    eventbus_top_topic = _top_pair(eventbus.get("top_bounded_topics"))
    eventbus_top_drop = _top_pair(eventbus.get("top_bounded_drops"))
    eventbus_top_superseded = _top_pair(eventbus.get("top_bounded_superseded_topics"))

    signals: list[str] = []
    if route_guardrail_active:
        signals.append(
            "route_guardrail"
            f" reason={route_reason or 'active'}"
            f" pending_bytes={route_pending_bytes}"
            f" oldest_age_s={route_pending_oldest_age_s:.2f}"
        )
    if yjs_active:
        signals.append(
            "yjs_pressure"
            f" reason={yjs_reason or 'active'}"
            f" pending_send={yjs_pending_send}"
            f" pending_store={yjs_pending_store}"
            f" buffer_used={yjs_buffer_used}"
        )
    if rebuild_active_total > 0 or rebuild_delayed_total > 0 or rebuild_dirty_total > 0:
        signals.append(
            "snapshot_rebuild"
            f" active={rebuild_active_total}"
            f" delayed={rebuild_delayed_total}"
            f" dirty={rebuild_dirty_total}"
            f" top={rebuild_top_key or '-'}"
            f" req={rebuild_top_request_id or '-'}"
        )
    if eventbus_pending_tasks > 0 or eventbus_bounded_queue_total > 0:
        top_topic = eventbus_top_topic[0] if eventbus_top_topic else "-"
        signals.append(
            "eventbus_backlog"
            f" pending_tasks={eventbus_pending_tasks}"
            f" bounded_queue={eventbus_bounded_queue_total}"
            f" top_topic={top_topic}"
        )
    if eventbus_top_drop and eventbus_top_drop[1] > 0:
        signals.append(f"eventbus_drop topic={eventbus_top_drop[0]} total={eventbus_top_drop[1]}")
    if eventbus_top_superseded and eventbus_top_superseded[1] > 0:
        signals.append(
            f"eventbus_superseded topic={eventbus_top_superseded[0]} total={eventbus_top_superseded[1]}"
        )

    dominant_signal = "memory_profile_failure"
    if route_guardrail_active and (route_pending_bytes > 0 or route_pending_oldest_age_s > 0.0):
        dominant_signal = "route_pressure"
    elif yjs_active and (yjs_pending_send > 0 or yjs_pending_store > 0 or yjs_buffer_used > 0):
        dominant_signal = "yjs_pressure"
    elif eventbus_pending_tasks > 0 or eventbus_bounded_queue_total > 0:
        dominant_signal = "eventbus_backlog"
    elif rebuild_active_total > 0 or rebuild_delayed_total > 0 or rebuild_dirty_total > 0:
        dominant_signal = "snapshot_rebuild"

    headline = " | ".join(signals[:3]).strip()
    if not headline:
        headline = "no live route/yjs/rebuild/eventbus pressure captured"

    return {
        "headline": headline,
        "dominant_signal": dominant_signal,
        "signals": signals,
        "route_guardrail_active": route_guardrail_active,
        "route_reason": route_reason,
        "route_pending_bytes": route_pending_bytes,
        "route_pending_oldest_age_s": route_pending_oldest_age_s,
        "yjs_active": yjs_active,
        "yjs_reason": yjs_reason,
        "yjs_pending_send": yjs_pending_send,
        "yjs_pending_store": yjs_pending_store,
        "yjs_buffer_used": yjs_buffer_used,
        "rebuild_active_total": rebuild_active_total,
        "rebuild_delayed_total": rebuild_delayed_total,
        "rebuild_dirty_total": rebuild_dirty_total,
        "rebuild_top_key": rebuild_top_key,
        "rebuild_top_request_id": rebuild_top_request_id,
        "eventbus_pending_tasks": eventbus_pending_tasks,
        "eventbus_bounded_queue_total": eventbus_bounded_queue_total,
        "eventbus_top_topic": eventbus_top_topic,
        "eventbus_top_drop": eventbus_top_drop,
        "eventbus_top_superseded": eventbus_top_superseded,
    }
