from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any

from adaos.services.yjs.doc import async_get_ydoc


_log = logging.getLogger("adaos.router.webio_stream_guard")
_WEBIO_STREAM_GUARD_STATS_LOCK = threading.Lock()
_WEBIO_STREAM_GUARD_STATS: dict[str, dict[str, Any]] = {}
VOICE_CHAT_STREAM_RECEIVER = "voice_chat.messages"


def _webio_receiver_metadata_timeout_s() -> float:
    try:
        return max(0.05, min(float(str(os.getenv("ADAOS_WEBIO_RECEIVER_METADATA_TIMEOUT_S") or "0.75").strip()), 10.0))
    except Exception:
        return 0.75


def _webio_stream_guard_enabled() -> bool:
    return str(os.getenv("ADAOS_WEBIO_STREAM_GUARD_ENABLE") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _webio_stream_warn_bytes() -> int:
    try:
        return max(1024, int(str(os.getenv("ADAOS_WEBIO_STREAM_WARN_BYTES") or "65536").strip()))
    except Exception:
        return 65536


def _webio_stream_block_bytes() -> int:
    try:
        return max(_webio_stream_warn_bytes(), int(str(os.getenv("ADAOS_WEBIO_STREAM_BLOCK_BYTES") or "262144").strip()))
    except Exception:
        return max(_webio_stream_warn_bytes(), 262144)


def _webio_stream_payload_bytes(payload: Any) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    except Exception:
        return 0


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        try:
            decoded = to_json()
            if isinstance(decoded, dict):
                return dict(decoded)
        except Exception:
            return {}
    return {}


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _receiver_declared_owner(receiver_meta: dict[str, Any]) -> str:
    origin = str(receiver_meta.get("origin") or "").strip()
    if origin:
        return origin
    route = receiver_meta.get("route") if isinstance(receiver_meta.get("route"), dict) else {}
    owner = str(route.get("owner") or receiver_meta.get("owner") or "").strip()
    return owner


def _static_webio_receiver_metadata(receiver: str) -> dict[str, Any]:
    receiver_id = str(receiver or "").strip()
    if receiver_id != VOICE_CHAT_STREAM_RECEIVER:
        return {}
    return {
        "origin": "skill:voice_chat_skill",
        "owner": "skill:voice_chat_skill",
        "mode": "stream",
        "snapshotPolicy": "compact_tail",
        "budget": {"maxPayloadBytes": 524288},
        "route": {
            "kind": "stream",
            "surface": "voice_chat",
            "owner": "skill:voice_chat_skill",
        },
    }


def _webio_stream_stats_key(webspace_id: str, receiver: str, owner: str) -> str:
    return "\0".join(
        [
            str(webspace_id or "").strip() or "default",
            str(receiver or "").strip() or "unknown",
            str(owner or "").strip() or "unknown",
        ]
    )


def _record_webio_stream_guard_event(
    *,
    webspace_id: str,
    receiver: str,
    owner: str,
    event: str,
    payload_bytes: int,
    fanout_total: int,
    effective_bytes: int,
    policy_state: str = "ok",
    reason: str = "healthy",
    receiver_meta: dict[str, Any] | None = None,
) -> None:
    receiver_meta = receiver_meta or {}
    route_meta = receiver_meta.get("route") if isinstance(receiver_meta.get("route"), dict) else {}
    budget = receiver_meta.get("budget") if isinstance(receiver_meta.get("budget"), dict) else {}
    token_event = str(event or "").strip().lower()
    if not token_event:
        return
    token_ws = str(webspace_id or "").strip() or "default"
    token_receiver = str(receiver or "").strip() or "unknown"
    token_owner = str(owner or "").strip() or "unknown"
    now = time.time()
    key = _webio_stream_stats_key(token_ws, token_receiver, token_owner)
    with _WEBIO_STREAM_GUARD_STATS_LOCK:
        current = dict(_WEBIO_STREAM_GUARD_STATS.get(key) or {})
        current["webspace_id"] = token_ws
        current["receiver"] = token_receiver
        current["owner"] = token_owner
        current["last_at"] = now
        current["last_event"] = token_event
        current["last_policy_state"] = str(policy_state or "").strip() or "ok"
        current["last_reason"] = str(reason or "").strip() or None
        current["last_payload_bytes"] = max(0, int(payload_bytes or 0))
        current["last_fanout_total"] = max(1, int(fanout_total or 1))
        current["last_effective_bytes"] = max(0, int(effective_bytes or 0))
        current["surface"] = str(route_meta.get("surface") or "").strip() or None
        current["route_kind"] = str(route_meta.get("kind") or "").strip() or None
        current["receiver_origin"] = str(receiver_meta.get("origin") or "").strip() or None
        current["receiver_mode"] = str(receiver_meta.get("mode") or "").strip() or None
        current["snapshot_policy"] = str(receiver_meta.get("snapshotPolicy") or "").strip() or None
        current["declared_max_payload_bytes"] = _positive_int(
            budget.get("maxPayloadBytes")
            or budget.get("max_payload_bytes")
            or receiver_meta.get("maxPayloadBytes")
        )
        field = f"{token_event}_total"
        current[field] = int(current.get(field) or 0) + 1
        if token_event == "published":
            current["published_fanout_total"] = int(current.get("published_fanout_total") or 0) + max(1, int(fanout_total or 1))
        _WEBIO_STREAM_GUARD_STATS[key] = current


def webio_stream_guard_snapshot(
    *,
    webspace_id: str | None = None,
    receiver: str | None = None,
    owner: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    token_ws = str(webspace_id or "").strip()
    token_receiver = str(receiver or "").strip()
    token_owner = str(owner or "").strip()
    try:
        max_items = max(1, min(500, int(limit)))
    except Exception:
        max_items = 50
    with _WEBIO_STREAM_GUARD_STATS_LOCK:
        rows = [dict(item) for item in _WEBIO_STREAM_GUARD_STATS.values()]
    if token_ws:
        rows = [row for row in rows if str(row.get("webspace_id") or "") == token_ws]
    if token_receiver:
        rows = [row for row in rows if str(row.get("receiver") or "") == token_receiver]
    if token_owner:
        rows = [row for row in rows if str(row.get("owner") or "") == token_owner]
    rows.sort(key=lambda item: float(item.get("last_at") or 0.0), reverse=True)
    totals = {
        "attempted": sum(int(row.get("attempted_total") or 0) for row in rows),
        "published": sum(int(row.get("published_total") or 0) for row in rows),
        "suppressed": sum(int(row.get("suppressed_total") or 0) for row in rows),
        "throttled": sum(int(row.get("throttled_total") or 0) for row in rows),
        "published_fanout": sum(int(row.get("published_fanout_total") or 0) for row in rows),
    }
    return {
        "schema": "adaos.webio_stream_guard.v1",
        "webspace_id": token_ws or None,
        "receiver": token_receiver or None,
        "owner": token_owner or None,
        "items": rows[:max_items],
        "total": len(rows),
        "totals": totals,
    }


async def _read_webio_receiver_metadata(webspace_id: str, receiver: str) -> dict[str, Any]:
    try:
        async with async_get_ydoc(
            webspace_id,
            read_only=True,
            prefer_live_room=True,
            load_mark_roots=["data"],
        ) as ydoc:
            data = _as_dict(ydoc.get_map("data"))
            webio = data.get("webio") if isinstance(data.get("webio"), dict) else {}
            receivers = webio.get("receivers") if isinstance(webio.get("receivers"), dict) else {}
            row = receivers.get(receiver) if isinstance(receivers, dict) else None
            return dict(row) if isinstance(row, dict) else {}
    except Exception:
        _log.debug(
            "failed to read webio receiver metadata webspace=%s receiver=%s",
            webspace_id,
            receiver,
            exc_info=True,
        )
        return {}


def _webio_stream_owner(payload: dict[str, Any], meta: dict[str, Any]) -> str:
    owner = str(
        payload.get("owner")
        or meta.get("owner")
        or payload.get("skill_owner")
        or meta.get("skill_owner")
        or ""
    ).strip()
    if owner:
        return owner
    skill_name = str(
        payload.get("skill_name")
        or meta.get("skill_name")
        or payload.get("skill")
        or meta.get("skill")
        or ""
    ).strip()
    return f"skill:{skill_name}" if skill_name else ""


def _webio_stream_admit(
    *,
    webspace_id: str,
    receiver: str,
    owner: str,
    payload_bytes: int,
    fanout_total: int = 1,
    receiver_meta: dict[str, Any] | None = None,
) -> bool:
    if not _webio_stream_guard_enabled():
        return True
    receiver_meta = receiver_meta or {}
    route_meta = receiver_meta.get("route") if isinstance(receiver_meta.get("route"), dict) else {}
    budget = receiver_meta.get("budget") if isinstance(receiver_meta.get("budget"), dict) else {}
    declared_max_payload = _positive_int(
        budget.get("maxPayloadBytes")
        or budget.get("max_payload_bytes")
        or receiver_meta.get("maxPayloadBytes")
    )
    warn_bytes = _webio_stream_warn_bytes()
    block_bytes = _webio_stream_block_bytes()
    if declared_max_payload:
        block_bytes = min(block_bytes, declared_max_payload)
        warn_bytes = min(warn_bytes, max(1, int(declared_max_payload * 0.8)))
    effective_bytes = max(0, int(payload_bytes or 0)) * max(1, int(fanout_total or 1))
    policy_state = "ok"
    reason = "healthy"
    if effective_bytes >= block_bytes:
        policy_state = "block"
        reason = (
            "browser_stream_declared_payload_budget_exceeded"
            if declared_max_payload and effective_bytes >= declared_max_payload
            else "browser_stream_payload_blocked"
        )
    elif effective_bytes >= warn_bytes:
        policy_state = "throttle"
        reason = (
            "browser_stream_declared_payload_budget_pressure"
            if declared_max_payload
            else "browser_stream_payload_pressure"
        )
    _record_webio_stream_guard_event(
        webspace_id=webspace_id,
        receiver=receiver,
        owner=owner,
        event="attempted",
        payload_bytes=payload_bytes,
        fanout_total=fanout_total,
        effective_bytes=effective_bytes,
        policy_state=policy_state,
        reason=reason,
        receiver_meta=receiver_meta,
    )
    if policy_state == "ok":
        return True
    if not owner:
        _record_webio_stream_guard_event(
            webspace_id=webspace_id,
            receiver=receiver,
            owner=owner,
            event="suppressed",
            payload_bytes=payload_bytes,
            fanout_total=fanout_total,
            effective_bytes=effective_bytes,
            policy_state=policy_state,
            reason=reason,
            receiver_meta=receiver_meta,
        )
        _log.warning(
            "webio stream dropped by payload guard webspace=%s receiver=%s surface=%s bytes=%s fanout=%s effective_bytes=%s budget_max=%s reason=%s owner=unknown",
            webspace_id,
            receiver,
            str(route_meta.get("surface") or "").strip() or "-",
            payload_bytes,
            fanout_total,
            effective_bytes,
            declared_max_payload or "-",
            reason,
        )
        return False
    try:
        from adaos.services.yjs.owner_guard import admit_owner_work

        admission = admit_owner_work(
            webspace_id=webspace_id,
            owner=owner,
            root_names=["stream"],
            path=f"stream/{receiver}",
            source="router.webio_stream",
            channel="webio.stream",
            work_kind="browser_stream",
            tool=f"{owner}:stream:{receiver}",
            policy={
                "policy_state": policy_state,
                "reason": reason,
                "observed_state": "critical" if policy_state == "block" else "high",
                "payload_bytes": payload_bytes,
                "fanout_total": fanout_total,
                "effective_bytes": effective_bytes,
                "budget": dict(budget) if budget else {},
                "declared_max_payload_bytes": declared_max_payload,
                "receiver_origin": str(receiver_meta.get("origin") or "").strip() or None,
                "receiver_mode": str(receiver_meta.get("mode") or "").strip() or None,
                "snapshot_policy": str(receiver_meta.get("snapshotPolicy") or "").strip() or None,
                "route": dict(route_meta) if route_meta else {},
                "guard_visibility": receiver_meta.get("guardVisibility"),
                "blocked_roots": ["stream"] if policy_state == "block" else [],
                "throttled_roots": ["stream"] if policy_state == "throttle" else [],
            },
        )
        if not bool(admission.get("allowed", True)):
            _record_webio_stream_guard_event(
                webspace_id=webspace_id,
                receiver=receiver,
                owner=admission.get("owner") or owner,
                event="suppressed",
                payload_bytes=payload_bytes,
                fanout_total=fanout_total,
                effective_bytes=effective_bytes,
                policy_state=policy_state,
                reason=admission.get("reason") or reason,
                receiver_meta=receiver_meta,
            )
            _log.warning(
                "webio stream denied by owner guard webspace=%s receiver=%s surface=%s owner=%s bytes=%s fanout=%s effective_bytes=%s budget_max=%s reason=%s retry_after_s=%s",
                webspace_id,
                receiver,
                str(route_meta.get("surface") or "").strip() or "-",
                admission.get("owner") or owner,
                payload_bytes,
                fanout_total,
                effective_bytes,
                declared_max_payload or "-",
                admission.get("reason") or reason,
                admission.get("retry_after_s") or 0,
            )
            return False
        if bool(admission.get("throttled")):
            _record_webio_stream_guard_event(
                webspace_id=webspace_id,
                receiver=receiver,
                owner=admission.get("owner") or owner,
                event="throttled",
                payload_bytes=payload_bytes,
                fanout_total=fanout_total,
                effective_bytes=effective_bytes,
                policy_state=policy_state,
                reason=admission.get("reason") or reason,
                receiver_meta=receiver_meta,
            )
            _log.warning(
                "webio stream allowed under pressure webspace=%s receiver=%s surface=%s owner=%s bytes=%s fanout=%s effective_bytes=%s budget_max=%s reason=%s",
                webspace_id,
                receiver,
                str(route_meta.get("surface") or "").strip() or "-",
                admission.get("owner") or owner,
                payload_bytes,
                fanout_total,
                effective_bytes,
                declared_max_payload or "-",
                admission.get("reason") or reason,
            )
        return True
    except Exception:
        _record_webio_stream_guard_event(
            webspace_id=webspace_id,
            receiver=receiver,
            owner=owner,
            event="suppressed",
            payload_bytes=payload_bytes,
            fanout_total=fanout_total,
            effective_bytes=effective_bytes,
            policy_state=policy_state,
            reason=reason,
            receiver_meta=receiver_meta,
        )
        _log.warning(
            "webio stream dropped after guard failure webspace=%s receiver=%s surface=%s owner=%s bytes=%s fanout=%s effective_bytes=%s budget_max=%s reason=%s",
            webspace_id,
            receiver,
            str(route_meta.get("surface") or "").strip() or "-",
            owner,
            payload_bytes,
            fanout_total,
            effective_bytes,
            declared_max_payload or "-",
            reason,
            exc_info=True,
        )
        return False
