from __future__ import annotations

import time
from typing import Any, Mapping

from .cards import StatusCard


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any, default: str = "") -> str:
    token = str(value or "").strip()
    return token or default


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _yjs_guard_status(pressure: Mapping[str, Any]) -> tuple[str, str]:
    policy = _text(pressure.get("policy_state"), "ok").lower()
    observed = _text(pressure.get("observed_state"), "idle").lower()
    quarantined = bool(pressure.get("quarantined") or pressure.get("active"))
    if quarantined or policy == "block":
        return "degraded", "critical"
    if policy == "throttle" or observed in {"critical", "pressure"}:
        return "degraded", "high"
    if policy in {"warn", "warning"} or observed in {"warn", "warning"}:
        return "warning", "warning"
    return "ready", "info"


def _stream_guard_status(guard: Mapping[str, Any]) -> tuple[str, str]:
    totals = _dict(guard.get("totals"))
    suppressed = _int(totals.get("suppressed"))
    throttled = _int(totals.get("throttled"))
    if suppressed or throttled:
        return ("degraded", "high") if suppressed else ("warning", "warning")
    return "ready", "info"


def _projection_guard_status(guard: Mapping[str, Any]) -> tuple[str, str]:
    totals = _dict(guard.get("totals"))
    guarded = _int(totals.get("guarded"))
    if guarded or _int(guard.get("total")):
        return "degraded", "high"
    return "ready", "info"


def _stream_control_status(backlog: Mapping[str, Any]) -> tuple[str, str]:
    rows = [_dict(item) for item in _list(backlog.get("top_webio_stream_controls"))]
    dropped = sum(_int(item.get("dropped_total")) for item in rows)
    queued = sum(_int(item.get("queued_total")) for item in rows)
    superseded = sum(_int(item.get("superseded_total")) for item in rows)
    if dropped:
        return "degraded", "high"
    if queued or superseded:
        return "warning", "warning"
    return "ready", "info"


def _yjs_guard_card(
    pressure: Mapping[str, Any],
    *,
    webspace_id: str | None,
    updated_at: float,
) -> StatusCard | None:
    if not pressure:
        return None
    status, severity = _yjs_guard_status(pressure)
    route = _dict(pressure.get("last_route"))
    projection = _dict(pressure.get("last_projection"))
    owner = _text(pressure.get("owner"), "-")
    reason = _text(pressure.get("reason"), "healthy")
    policy = _text(pressure.get("policy_state"), "ok")
    observed = _text(pressure.get("observed_state"), "idle")
    path = _text(pressure.get("last_path") or route.get("path") or projection.get("path"))
    summary = f"Yjs guard {policy}/{observed}: {reason}"
    if owner != "-":
        summary += f" owner={owner}"
    return StatusCard(
        id="guard:yjs_pressure",
        owner="core:yjs",
        kind="guard",
        scope="core",
        status=status,
        severity=severity,
        summary=summary[:240],
        webspace_id=webspace_id or _text(pressure.get("webspace_id")) or None,
        updated_at=updated_at,
        ttl_ms=15000,
        details_ref={
            "kind": "api",
            "path": "/api/node/reliability",
            "field": "runtime.yjs_pressure",
        },
        route=route or {"kind": "yjs_guard", "path": path},
        guard_ref={
            "guard": "yjs_pressure",
            "owner": owner,
            "webspace_id": webspace_id or _text(pressure.get("webspace_id")),
            "path": path,
            "budget": _dict(pressure.get("budget")),
            "observed_pressure": {
                "state": observed,
                "policy_state": policy,
                "recent_bytes": _int(pressure.get("recent_bytes")),
                "recent_writes": _int(pressure.get("recent_writes")),
                "peak_bps": _float(pressure.get("peak_bps")),
                "peak_wps": _float(pressure.get("peak_wps")),
            },
            "suppression_count": _int(pressure.get("blocked_total")),
            "throttled_count": _int(pressure.get("throttled_total")),
            "quarantine_ttl_s": _float(pressure.get("quarantine_remaining_s")),
            "quarantine_until": pressure.get("quarantine_until"),
            "quarantine_trigger": pressure.get("quarantine_trigger"),
            "correlation_id": pressure.get("correlation_id") or pressure.get("generation_id"),
            "reason": reason,
        },
    )


def _yjs_projection_guard_card(
    guard: Mapping[str, Any],
    *,
    webspace_id: str | None,
    updated_at: float,
) -> StatusCard | None:
    if not guard:
        return None
    items = [_dict(item) for item in _list(guard.get("items"))]
    top = items[0] if items else {}
    status, severity = _projection_guard_status(guard)
    totals = _dict(guard.get("totals"))
    owner = _text(top.get("owner"), "unknown")
    reason = _text(top.get("reason"), "healthy")
    slot = _text(top.get("slot"))
    path = _text(top.get("path"))
    payload_bytes = _int(top.get("payload_bytes"))
    update_bytes = _int(top.get("update_bytes"))
    amplification_ratio = _float(top.get("amplification_ratio"))
    max_payload_bytes = _int(top.get("max_payload_bytes"))
    summary = f"Yjs projection guard {reason}: guarded={_int(totals.get('guarded'))}"
    if slot:
        summary += f" slot={slot}"
    if payload_bytes:
        summary += f" bytes={payload_bytes}/{max_payload_bytes or '-'}"
    if update_bytes:
        summary += f" update={update_bytes} ratio={amplification_ratio:.1f}"
    return StatusCard(
        id="guard:yjs_projection",
        owner="core:yjs",
        kind="guard",
        scope="core",
        status=status,
        severity=severity,
        summary=summary[:240],
        webspace_id=webspace_id or _text(guard.get("webspace_id")) or _text(top.get("webspace_id")) or None,
        updated_at=updated_at,
        ttl_ms=15000,
        details_ref={
            "kind": "api",
            "path": "/api/node/reliability",
            "field": "runtime.yjs_projection_guard",
        },
        route=_dict(top.get("route")) or {
            "kind": "yjs_projection_guard",
            "scope": _text(top.get("scope")) or None,
            "slot": slot or None,
            "path": path or None,
        },
        guard_ref={
            "guard": "yjs_projection",
            "owner": owner,
            "webspace_id": webspace_id or _text(guard.get("webspace_id")) or _text(top.get("webspace_id")),
            "path": path,
            "budget": {
                "max_payload_bytes": top.get("max_payload_bytes"),
                "max_items": top.get("max_items"),
            },
            "observed_pressure": {
                "payload_bytes": payload_bytes,
                "update_bytes": update_bytes,
                "amplification_ratio": amplification_ratio,
                "degraded_bytes": _int(top.get("degraded_bytes")),
                "max_list_items": _int(top.get("max_list_items")),
                "max_list_path": top.get("max_list_path"),
                "list_item_total": _int(top.get("list_item_total")),
            },
            "suppression_count": _int(totals.get("guarded")),
            "correlation_id": guard.get("correlation_id") or guard.get("generation_id"),
            "reason": reason,
        },
    )


def _stream_guard_card(
    guard: Mapping[str, Any],
    *,
    webspace_id: str | None,
    updated_at: float,
) -> StatusCard | None:
    if not guard:
        return None
    totals = _dict(guard.get("totals"))
    items = [_dict(item) for item in _list(guard.get("items"))]
    top = items[0] if items else {}
    status, severity = _stream_guard_status(guard)
    suppressed = _int(totals.get("suppressed"))
    throttled = _int(totals.get("throttled"))
    published = _int(totals.get("published"))
    fanout = _int(totals.get("published_fanout"))
    receiver = _text(top.get("receiver"))
    reason = _text(top.get("last_reason"), "published")
    summary = (
        f"Stream guard published={published} fanout={fanout} "
        f"suppressed={suppressed} throttled={throttled}"
    )
    if receiver:
        summary += f" top={receiver}"
    return StatusCard(
        id="guard:webio_stream",
        owner="core:router",
        kind="guard",
        scope="core",
        status=status,
        severity=severity,
        summary=summary[:240],
        webspace_id=webspace_id or _text(guard.get("webspace_id")) or None,
        updated_at=updated_at,
        ttl_ms=15000,
        details_ref={
            "kind": "api",
            "path": "/api/node/reliability",
            "field": "runtime.webio_stream_guard",
        },
        route={
            "kind": "stream_guard",
            "receiver": receiver,
            "surface": _text(top.get("surface")) or None,
        },
        guard_ref={
            "guard": "webio_stream",
            "owner": _text(top.get("owner"), "unknown"),
            "webspace_id": webspace_id or _text(guard.get("webspace_id")),
            "receiver": receiver,
            "budget": {"max_payload_bytes": top.get("declared_max_payload_bytes")},
            "observed_pressure": {
                "attempted": _int(totals.get("attempted")),
                "published": published,
                "published_fanout": fanout,
                "last_reason": reason,
            },
            "suppression_count": suppressed,
            "throttled_count": throttled,
            "correlation_id": guard.get("correlation_id") or guard.get("generation_id"),
        },
    )


def _stream_control_card(
    backlog: Mapping[str, Any],
    *,
    webspace_id: str | None,
    updated_at: float,
) -> StatusCard | None:
    rows = [_dict(item) for item in _list(backlog.get("top_webio_stream_controls"))]
    if not rows:
        return None
    top = rows[0]
    status, severity = _stream_control_status(backlog)
    incoming = sum(_int(item.get("incoming_total")) for item in rows)
    queued = sum(_int(item.get("queued_total")) for item in rows)
    superseded = sum(_int(item.get("superseded_total")) for item in rows)
    dropped = sum(_int(item.get("dropped_total")) for item in rows)
    receiver = _text(top.get("receiver"))
    summary = (
        f"Stream controls incoming={incoming} queued={queued} "
        f"superseded={superseded} dropped={dropped}"
    )
    if receiver:
        summary += f" top={receiver}"
    return StatusCard(
        id="guard:webio_stream_control",
        owner="core:eventbus",
        kind="guard",
        scope="core",
        status=status,
        severity=severity,
        summary=summary[:240],
        webspace_id=webspace_id or _text(top.get("webspace_id")) or None,
        updated_at=updated_at,
        ttl_ms=15000,
        details_ref={
            "kind": "api",
            "path": "/api/node/reliability",
            "field": "runtime.eventbus_backlog.top_webio_stream_controls",
        },
        route={
            "kind": "eventbus_stream_control",
            "receiver": receiver,
            "event_type": _text(top.get("event_type")) or None,
        },
        guard_ref={
            "guard": "webio_stream_control",
            "webspace_id": webspace_id or _text(top.get("webspace_id")),
            "receiver": receiver,
            "observed_pressure": {
                "incoming": incoming,
                "queued": queued,
                "superseded": superseded,
                "dropped": dropped,
            },
            "suppression_count": dropped,
            "coalesced_count": superseded,
            "correlation_id": backlog.get("correlation_id") or backlog.get("generation_id"),
        },
    )


def _incident_status(severity: str) -> tuple[str, str]:
    token = str(severity or "").strip().lower()
    if token == "critical":
        return "degraded", "critical"
    if token in {"degraded", "high"}:
        return "degraded", "high"
    if token in {"warning", "warn", "medium"}:
        return "warning", "warning"
    return "ready", "info"


def _incident_registry_cards(
    registry: Mapping[str, Any],
    *,
    webspace_id: str | None,
    updated_at: float,
    limit: int = 5,
) -> list[StatusCard]:
    items = [_dict(item) for item in _list(registry.get("items"))]
    cards: list[StatusCard] = []
    for item in items:
        if not bool(item.get("active")):
            continue
        status, severity = _incident_status(_text(item.get("severity"), "warning"))
        if severity == "info":
            continue
        incident_id = _text(item.get("id"), "unknown")
        domain = _text(item.get("domain"), "core.runtime")
        inc_class = _text(item.get("class"), "incident")
        signal = _text(item.get("signal"), inc_class)
        component = _text(item.get("component"))
        source = _text(item.get("source"))
        cards.append(
            StatusCard(
                id=f"incident:{incident_id}",
                owner=domain,
                kind="incident",
                scope=component or inc_class,
                status=status,
                severity=severity,
                summary=_text(item.get("summary"), signal)[:240],
                webspace_id=webspace_id,
                updated_at=updated_at,
                ttl_ms=15000,
                incident_id=incident_id,
                details_ref={
                    "kind": "api",
                    "path": "/api/node/reliability",
                    "field": "runtime.incident_registry.items",
                    "id": incident_id,
                },
                route={
                    "kind": "incident_registry",
                    "class": inc_class,
                    "signal": signal,
                    "component": component or None,
                    "source": source or None,
                },
                guard_ref={
                    "guard": "incident_registry",
                    "domain": domain,
                    "class": inc_class,
                    "signal": signal,
                    "occurrence_count": _int(item.get("occurrence_count")),
                    "last_seen_ago_s": _float(item.get("last_seen_ago_s")),
                },
                metadata={
                    "tags": [str(tag) for tag in _list(item.get("tags"))],
                    "fingerprint": item.get("fingerprint"),
                },
            )
        )
        if len(cards) >= max(1, int(limit or 5)):
            break
    return cards


def guard_status_cards_from_runtime(
    runtime: Mapping[str, Any],
    *,
    webspace_id: str | None = None,
    updated_at: float | None = None,
) -> list[StatusCard]:
    ts = float(updated_at if updated_at is not None else time.time())
    cards = [
        _yjs_guard_card(_dict(runtime.get("yjs_pressure")), webspace_id=webspace_id, updated_at=ts),
        _yjs_projection_guard_card(
            _dict(runtime.get("yjs_projection_guard")),
            webspace_id=webspace_id,
            updated_at=ts,
        ),
        _stream_guard_card(_dict(runtime.get("webio_stream_guard")), webspace_id=webspace_id, updated_at=ts),
        _stream_control_card(_dict(runtime.get("eventbus_backlog")), webspace_id=webspace_id, updated_at=ts),
    ]
    cards.extend(
        _incident_registry_cards(_dict(runtime.get("incident_registry")), webspace_id=webspace_id, updated_at=ts)
    )
    return [card for card in cards if card is not None]


__all__ = ["guard_status_cards_from_runtime"]
