from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

_log = logging.getLogger("adaos.webio.snapshot_demand")

SNAPSHOT_EVENT_TYPES = frozenset(
    {
        "webio.stream.snapshot.requested",
        "webio.yjs.snapshot.requested",
    }
)

_DEFAULT_DEBOUNCE_S = max(
    0.0,
    float(os.getenv("ADAOS_WEBIO_SNAPSHOT_DEMAND_DEBOUNCE_MS", "150") or "150") / 1000.0,
)
_DEFAULT_COOLDOWN_S = max(
    0.0,
    float(os.getenv("ADAOS_WEBIO_SNAPSHOT_DEMAND_COOLDOWN_MS", "1500") or "1500") / 1000.0,
)
_MAX_PENDING = max(32, int(os.getenv("ADAOS_WEBIO_SNAPSHOT_DEMAND_MAX_PENDING", "1024") or "1024"))
_MAX_RECENT = max(32, int(os.getenv("ADAOS_WEBIO_SNAPSHOT_DEMAND_MAX_RECENT", "2048") or "2048"))

SnapshotPublisher = Callable[[str, dict[str, Any], str], None]


@dataclass(slots=True)
class _PendingDemand:
    event_type: str
    payload: dict[str, Any]
    source: str
    publish: SnapshotPublisher
    key: str
    summary: dict[str, Any]
    created_at: float
    updated_at: float
    count: int = 1
    handle: asyncio.TimerHandle | None = None


_LOCK = threading.RLock()
_PENDING: dict[str, _PendingDemand] = {}
_RECENT: dict[str, float] = {}
_STATS: dict[str, Any] = {
    "scheduled_total": 0,
    "published_total": 0,
    "coalesced_total": 0,
    "dropped_recent_total": 0,
    "immediate_total": 0,
    "max_pending_total": 0,
    "publish_error_total": 0,
    "last_event_type": "",
    "last_key": "",
    "last_published_at": 0.0,
}


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    except Exception:
        return str(value)


def _normalise_webspace_id(value: Any) -> str:
    raw = str(value or "").strip()
    return "desktop" if raw == "default" else raw


def _payload_meta(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("_meta")
    return meta if isinstance(meta, dict) else {}


def snapshot_demand_key(event_type: str, payload: dict[str, Any]) -> str:
    meta = _payload_meta(payload)
    params = payload.get("params")
    if params is None:
        params = meta.get("params")
    node_id = (
        str(payload.get("target_node_id") or "").strip()
        or str(payload.get("node_id") or "").strip()
        or str(meta.get("target_node_id") or "").strip()
        or str(meta.get("node_id") or "").strip()
    )
    return _stable_json(
        {
            "type": str(event_type or "").strip(),
            "topic": str(payload.get("topic") or "").strip(),
            "webspace_id": _normalise_webspace_id(payload.get("webspace_id") or meta.get("webspace_id")),
            "receiver": str(payload.get("receiver") or "").strip(),
            "slot": str(payload.get("slot") or payload.get("projection") or "").strip(),
            "node_id": node_id,
            "params": params if isinstance(params, (dict, list, str, int, float, bool)) or params is None else str(params),
        }
    )


def _summary(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    meta = _payload_meta(payload)
    node_id = (
        str(payload.get("target_node_id") or "").strip()
        or str(payload.get("node_id") or "").strip()
        or str(meta.get("target_node_id") or "").strip()
        or str(meta.get("node_id") or "").strip()
    )
    return {
        "event_type": str(event_type or "").strip(),
        "topic": str(payload.get("topic") or "").strip(),
        "webspace_id": _normalise_webspace_id(payload.get("webspace_id") or meta.get("webspace_id")),
        "receiver": str(payload.get("receiver") or "").strip(),
        "slot": str(payload.get("slot") or payload.get("projection") or "").strip(),
        "node_id": node_id,
    }


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop if loop.is_running() else None


def _prune_recent_locked(now: float, cooldown_s: float) -> None:
    if not _RECENT:
        return
    cutoff = now - max(cooldown_s, 1.0)
    stale = [key for key, ts in _RECENT.items() if ts < cutoff]
    for key in stale:
        _RECENT.pop(key, None)
    while len(_RECENT) > _MAX_RECENT:
        try:
            _RECENT.pop(next(iter(_RECENT)))
        except StopIteration:
            break


def _record_publish_locked(event_type: str, key: str, now: float, *, immediate: bool) -> None:
    _RECENT[key] = now
    _STATS["published_total"] = int(_STATS.get("published_total") or 0) + 1
    if immediate:
        _STATS["immediate_total"] = int(_STATS.get("immediate_total") or 0) + 1
    _STATS["last_event_type"] = event_type
    _STATS["last_key"] = key
    _STATS["last_published_at"] = now


def _publish_pending(key: str) -> None:
    now = time.monotonic()
    with _LOCK:
        pending = _PENDING.pop(key, None)
        if pending is None:
            return
        _record_publish_locked(pending.event_type, pending.key, now, immediate=False)
        _prune_recent_locked(now, _DEFAULT_COOLDOWN_S)
    try:
        pending.publish(pending.event_type, dict(pending.payload), pending.source)
    except Exception:
        with _LOCK:
            _STATS["publish_error_total"] = int(_STATS.get("publish_error_total") or 0) + 1
        _log.debug(
            "failed to publish coalesced webio snapshot demand type=%s key=%s",
            pending.event_type,
            pending.key,
            exc_info=True,
        )


def request_snapshot_event(
    event_type: str,
    payload: dict[str, Any],
    source: str,
    publish: SnapshotPublisher,
    *,
    debounce_s: float | None = None,
    cooldown_s: float | None = None,
) -> bool:
    if event_type not in SNAPSHOT_EVENT_TYPES:
        publish(event_type, dict(payload), source)
        return True
    if not isinstance(payload, dict):
        publish(event_type, {"value": payload}, source)
        return True

    debounce = _DEFAULT_DEBOUNCE_S if debounce_s is None else max(0.0, float(debounce_s))
    cooldown = _DEFAULT_COOLDOWN_S if cooldown_s is None else max(0.0, float(cooldown_s))
    loop = _running_loop()
    now = time.monotonic()
    payload_copy = dict(payload)
    key = snapshot_demand_key(event_type, payload_copy)
    summary = _summary(event_type, payload_copy)

    with _LOCK:
        _prune_recent_locked(now, cooldown)
        pending = _PENDING.get(key)
        if pending is not None:
            pending.payload = payload_copy
            pending.source = str(source or pending.source or "webio.snapshot_demand")
            pending.publish = publish
            pending.updated_at = now
            pending.count += 1
            pending.summary = summary
            _STATS["coalesced_total"] = int(_STATS.get("coalesced_total") or 0) + 1
            _STATS["last_event_type"] = event_type
            _STATS["last_key"] = key
            return False

        last_at = float(_RECENT.get(key) or 0.0)
        if cooldown > 0.0 and last_at > 0.0 and now - last_at < cooldown:
            _STATS["dropped_recent_total"] = int(_STATS.get("dropped_recent_total") or 0) + 1
            _STATS["last_event_type"] = event_type
            _STATS["last_key"] = key
            return False

        if debounce <= 0.0 or loop is None:
            _record_publish_locked(event_type, key, now, immediate=True)
            _prune_recent_locked(now, cooldown)
            immediate = True
        else:
            immediate = False
            if len(_PENDING) >= _MAX_PENDING:
                _STATS["max_pending_total"] = int(_STATS.get("max_pending_total") or 0) + 1
                _record_publish_locked(event_type, key, now, immediate=True)
                _prune_recent_locked(now, cooldown)
                immediate = True
            else:
                pending = _PendingDemand(
                    event_type=event_type,
                    payload=payload_copy,
                    source=str(source or "webio.snapshot_demand"),
                    publish=publish,
                    key=key,
                    summary=summary,
                    created_at=now,
                    updated_at=now,
                )
                pending.handle = loop.call_later(debounce, _publish_pending, key)
                _PENDING[key] = pending
                _STATS["scheduled_total"] = int(_STATS.get("scheduled_total") or 0) + 1
                _STATS["last_event_type"] = event_type
                _STATS["last_key"] = key
                return True

    if immediate:
        publish(event_type, payload_copy, source)
    return True


def snapshot_demand_snapshot(*, now_ts: float | None = None) -> dict[str, Any]:
    now = time.monotonic() if now_ts is None else float(now_ts)
    with _LOCK:
        pending = sorted(_PENDING.values(), key=lambda item: item.created_at)[:16]
        recent_total = len(_RECENT)
        result = {
            "enabled": _DEFAULT_DEBOUNCE_S > 0.0 or _DEFAULT_COOLDOWN_S > 0.0,
            "debounce_ms": int(round(_DEFAULT_DEBOUNCE_S * 1000.0)),
            "cooldown_ms": int(round(_DEFAULT_COOLDOWN_S * 1000.0)),
            "pending": len(_PENDING),
            "recent": recent_total,
            **dict(_STATS),
            "top_pending": [
                {
                    **item.summary,
                    "count": item.count,
                    "age_ms": int(round(max(0.0, now - item.created_at) * 1000.0)),
                    "updated_ms": int(round(max(0.0, now - item.updated_at) * 1000.0)),
                }
                for item in pending
            ],
        }
    return result


def clear_snapshot_demand_for_tests() -> None:
    with _LOCK:
        for pending in _PENDING.values():
            handle = pending.handle
            if handle is not None:
                handle.cancel()
        _PENDING.clear()
        _RECENT.clear()
        for key in list(_STATS):
            if key in {"last_event_type", "last_key"}:
                _STATS[key] = ""
            else:
                _STATS[key] = 0
