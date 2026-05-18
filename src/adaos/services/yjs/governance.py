from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any

_log = logging.getLogger("adaos.yjs.governance")

_ENABLED = str(os.getenv("ADAOS_YJS_PRIMARY_DOC_GOVERNANCE_ENABLE") or "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_THROTTLE_SEC = max(0.0, float(os.getenv("ADAOS_YJS_PRIMARY_DOC_PRESSURE_THROTTLE_SEC") or "0.35"))
_FAIL_OPEN = str(os.getenv("ADAOS_YJS_PRIMARY_DOC_GOVERNANCE_FAIL_OPEN") or "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_LOCK = threading.RLock()
_THROTTLE_NEXT_ALLOWED_AT: dict[str, float] = {}
_STATS: dict[str, dict[str, Any]] = {}


def _normalize_owner(owner: Any) -> str:
    return str(owner or "").strip()


def _normalize_webspace(webspace_id: Any) -> str:
    return str(webspace_id or "").strip() or "default"


def _normalize_roots(root_names: list[str] | tuple[str, ...] | None) -> list[str]:
    result: list[str] = []
    for raw in list(root_names or ()):
        token = str(raw or "").strip()
        if token and token not in result:
            result.append(token)
    return result


def _governs_owner(owner: Any) -> bool:
    if not _ENABLED:
        return False
    token = _normalize_owner(owner).lower()
    if not token:
        return False
    return (
        token.startswith("skill:")
        or token.startswith("sdk:")
        or token.startswith("_by_owner/skill_")
        or token.startswith("_by_owner/sdk_")
    )


def _policy_snapshot(
    *,
    webspace_id: str,
    owner: str,
    root_names: list[str],
    now_ts: float | None = None,
) -> dict[str, Any]:
    try:
        from adaos.services.yjs.load_mark import yjs_primary_doc_policy_snapshot

        payload = yjs_primary_doc_policy_snapshot(
            webspace_id=webspace_id,
            owner=owner,
            root_names=root_names,
            now_ts=now_ts,
        )
        if isinstance(payload, dict):
            return payload
    except Exception:
        _log.debug(
            "failed to evaluate primary-doc governance policy webspace=%s owner=%s roots=%s",
            webspace_id,
            owner,
            ",".join(root_names) or "-",
            exc_info=True,
        )
    return {"policy_state": "ok" if _FAIL_OPEN else "block", "reason": "policy_unavailable"}


def _stats_key(webspace_id: str, owner: str) -> str:
    return f"{webspace_id}\0{owner}"


def _record_event(
    *,
    webspace_id: str,
    owner: str,
    root_names: list[str],
    path: str,
    source: str,
    channel: str,
    policy: dict[str, Any],
    decision: str,
    update_bytes: int | None,
) -> dict[str, Any]:
    now = time.time()
    key = _stats_key(webspace_id, owner)
    policy_state = str(policy.get("policy_state") or "ok").strip().lower() or "ok"
    with _LOCK:
        current = dict(_STATS.get(key) or {})
        current["webspace_id"] = webspace_id
        current["owner"] = owner
        current["attempted_total"] = int(current.get("attempted_total") or 0) + 1
        if decision == "block":
            current["blocked_total"] = int(current.get("blocked_total") or 0) + 1
        elif decision == "throttle":
            current["throttled_total"] = int(current.get("throttled_total") or 0) + 1
            current["allowed_total"] = int(current.get("allowed_total") or 0) + 1
        else:
            current["allowed_total"] = int(current.get("allowed_total") or 0) + 1
        current["last_decision"] = decision
        current["last_policy_state"] = policy_state
        current["last_reason"] = str(policy.get("reason") or "").strip() or None
        current["last_path"] = str(path or "").strip() or None
        current["last_source"] = str(source or "").strip() or None
        current["last_channel"] = str(channel or "").strip() or None
        current["last_roots"] = list(root_names)
        current["last_update_bytes"] = int(update_bytes or 0)
        current["last_route"] = dict(policy.get("route")) if isinstance(policy.get("route"), dict) else {}
        current["last_projection"] = (
            dict(policy.get("projection")) if isinstance(policy.get("projection"), dict) else {}
        )
        current["last_guard_visibility"] = (
            dict(policy.get("guard_visibility")) if isinstance(policy.get("guard_visibility"), dict) else {}
        )
        current["last_correlation_id"] = str(policy.get("correlation_id") or "").strip() or None
        current["last_generation_id"] = str(policy.get("generation_id") or "").strip() or None
        current["last_at"] = now
        current["last_blocked_roots"] = list(policy.get("blocked_roots") or [])
        current["last_throttled_roots"] = list(policy.get("throttled_roots") or [])
        current["last_affected_roots"] = list(policy.get("blocked_roots") or policy.get("throttled_roots") or [])
        _STATS[key] = current
        return dict(current)


def _reserve_throttle_delay(*, webspace_id: str, owner: str, root_names: list[str], path: str) -> float:
    if _THROTTLE_SEC <= 0.0:
        return 0.0
    key = f"{webspace_id}\0{owner}\0{','.join(root_names)}\0{path}"
    with _LOCK:
        now = time.monotonic()
        deadline = float(_THROTTLE_NEXT_ALLOWED_AT.get(key) or 0.0)
        if deadline > now:
            wait_s = deadline - now
            next_allowed = deadline + _THROTTLE_SEC
        else:
            wait_s = 0.0
            next_allowed = now + _THROTTLE_SEC
        _THROTTLE_NEXT_ALLOWED_AT[key] = next_allowed
        return max(0.0, wait_s)


def _maybe_log_decision(stats: dict[str, Any], *, policy: dict[str, Any], decision: str) -> None:
    total_key = "blocked_total" if decision == "block" else "throttled_total" if decision == "throttle" else ""
    total = int(stats.get(total_key) or 0) if total_key else 0
    if decision not in {"block", "throttle"}:
        return
    if total not in {1, 2, 3} and total % 25 != 0:
        return
    level = logging.WARNING if decision == "block" else logging.INFO
    _log.log(
        level,
        "%s YJS primary-doc write webspace=%s owner=%s roots=%s path=%s source=%s channel=%s route=%s surface=%s reason=%s update_bytes=%s total=%s",
        "blocked" if decision == "block" else "throttled",
        str(stats.get("webspace_id") or "default"),
        str(stats.get("owner") or "unknown"),
        ",".join(str(item) for item in list(stats.get("last_roots") or [])) or "-",
        str(stats.get("last_path") or "-"),
        str(stats.get("last_source") or "-"),
        str(stats.get("last_channel") or "-"),
        str((stats.get("last_route") if isinstance(stats.get("last_route"), dict) else {}).get("kind") or "-"),
        str((stats.get("last_route") if isinstance(stats.get("last_route"), dict) else {}).get("surface") or "-"),
        str(policy.get("reason") or "write_amplification"),
        int(stats.get("last_update_bytes") or 0),
        total,
    )


async def govern_primary_doc_write(
    *,
    webspace_id: str | None,
    owner: str | None,
    root_names: list[str] | tuple[str, ...] | None = None,
    path: str = "",
    source: str = "",
    channel: str = "",
    policy: dict[str, Any] | None = None,
    update_bytes: int | None = None,
) -> bool:
    token_owner = _normalize_owner(owner)
    if not _governs_owner(token_owner):
        return True
    token_ws = _normalize_webspace(webspace_id)
    roots = _normalize_roots(root_names)
    payload = dict(policy or _policy_snapshot(webspace_id=token_ws, owner=token_owner, root_names=roots))
    policy_state = str(payload.get("policy_state") or "ok").strip().lower() or "ok"
    try:
        from adaos.services.yjs.owner_guard import admit_owner_work

        admission = admit_owner_work(
            webspace_id=token_ws,
            owner=token_owner,
            root_names=roots,
            path=path,
            source=source,
            channel=channel,
            work_kind="yjs_write",
            policy=payload,
        )
        if not bool(admission.get("allowed", True)):
            deny_policy = dict(payload)
            deny_policy["policy_state"] = "block"
            deny_policy["reason"] = str(admission.get("reason") or deny_policy.get("reason") or "owner_quarantined")
            stats = _record_event(
                webspace_id=token_ws,
                owner=token_owner,
                root_names=roots,
                path=path,
                source=source,
                channel=channel,
                policy=deny_policy,
                decision="block",
                update_bytes=update_bytes,
            )
            _maybe_log_decision(stats, policy=deny_policy, decision="block")
            return False
    except Exception:
        _log.debug("failed to apply YJS owner guard webspace=%s owner=%s", token_ws, token_owner, exc_info=True)
    if policy_state == "block":
        stats = _record_event(
            webspace_id=token_ws,
            owner=token_owner,
            root_names=roots,
            path=path,
            source=source,
            channel=channel,
            policy=payload,
            decision="block",
            update_bytes=update_bytes,
        )
        _maybe_log_decision(stats, policy=payload, decision="block")
        return False
    if policy_state == "throttle":
        stats = _record_event(
            webspace_id=token_ws,
            owner=token_owner,
            root_names=roots,
            path=path,
            source=source,
            channel=channel,
            policy=payload,
            decision="throttle",
            update_bytes=update_bytes,
        )
        _maybe_log_decision(stats, policy=payload, decision="throttle")
        wait_s = _reserve_throttle_delay(webspace_id=token_ws, owner=token_owner, root_names=roots, path=path)
        if wait_s > 0.0:
            await asyncio.sleep(wait_s)
        return True
    _record_event(
        webspace_id=token_ws,
        owner=token_owner,
        root_names=roots,
        path=path,
        source=source,
        channel=channel,
        policy=payload,
        decision="allow",
        update_bytes=update_bytes,
    )
    return True


def govern_primary_doc_write_sync(
    *,
    webspace_id: str | None,
    owner: str | None,
    root_names: list[str] | tuple[str, ...] | None = None,
    path: str = "",
    source: str = "",
    channel: str = "",
    policy: dict[str, Any] | None = None,
    update_bytes: int | None = None,
) -> bool:
    token_owner = _normalize_owner(owner)
    if not _governs_owner(token_owner):
        return True
    token_ws = _normalize_webspace(webspace_id)
    roots = _normalize_roots(root_names)
    payload = dict(policy or _policy_snapshot(webspace_id=token_ws, owner=token_owner, root_names=roots))
    policy_state = str(payload.get("policy_state") or "ok").strip().lower() or "ok"
    try:
        from adaos.services.yjs.owner_guard import admit_owner_work

        admission = admit_owner_work(
            webspace_id=token_ws,
            owner=token_owner,
            root_names=roots,
            path=path,
            source=source,
            channel=channel,
            work_kind="yjs_write",
            policy=payload,
        )
        if not bool(admission.get("allowed", True)):
            deny_policy = dict(payload)
            deny_policy["policy_state"] = "block"
            deny_policy["reason"] = str(admission.get("reason") or deny_policy.get("reason") or "owner_quarantined")
            stats = _record_event(
                webspace_id=token_ws,
                owner=token_owner,
                root_names=roots,
                path=path,
                source=source,
                channel=channel,
                policy=deny_policy,
                decision="block",
                update_bytes=update_bytes,
            )
            _maybe_log_decision(stats, policy=deny_policy, decision="block")
            return False
    except Exception:
        _log.debug("failed to apply YJS owner guard webspace=%s owner=%s", token_ws, token_owner, exc_info=True)
    if policy_state == "block":
        stats = _record_event(
            webspace_id=token_ws,
            owner=token_owner,
            root_names=roots,
            path=path,
            source=source,
            channel=channel,
            policy=payload,
            decision="block",
            update_bytes=update_bytes,
        )
        _maybe_log_decision(stats, policy=payload, decision="block")
        return False
    if policy_state == "throttle":
        stats = _record_event(
            webspace_id=token_ws,
            owner=token_owner,
            root_names=roots,
            path=path,
            source=source,
            channel=channel,
            policy=payload,
            decision="throttle",
            update_bytes=update_bytes,
        )
        _maybe_log_decision(stats, policy=payload, decision="throttle")
        wait_s = _reserve_throttle_delay(webspace_id=token_ws, owner=token_owner, root_names=roots, path=path)
        if wait_s > 0.0:
            time.sleep(wait_s)
        return True
    _record_event(
        webspace_id=token_ws,
        owner=token_owner,
        root_names=roots,
        path=path,
        source=source,
        channel=channel,
        policy=payload,
        decision="allow",
        update_bytes=update_bytes,
    )
    return True


def primary_doc_governance_snapshot(*, webspace_id: str | None = None, owner: str | None = None) -> dict[str, Any]:
    token_ws = _normalize_webspace(webspace_id) if webspace_id is not None else ""
    token_owner = _normalize_owner(owner)
    with _LOCK:
        if token_ws and token_owner:
            exact = dict(_STATS.get(_stats_key(token_ws, token_owner)) or {})
            if exact:
                rows = [exact]
            else:
                rows = [dict(item) for item in _STATS.values() if str(item.get("webspace_id") or "") == token_ws]
        elif token_ws:
            rows = [dict(item) for item in _STATS.values() if str(item.get("webspace_id") or "") == token_ws]
        else:
            rows = [dict(item) for item in _STATS.values()]
    rows = [row for row in rows if row]
    rows.sort(key=lambda item: float(item.get("last_at") or 0.0), reverse=True)
    selected = rows[0] if rows else {}
    try:
        from adaos.services.yjs.owner_guard import owner_guard_snapshot

        owner_guard = owner_guard_snapshot(
            webspace_id=token_ws or None,
            owner=token_owner or None,
        )
    except Exception:
        owner_guard = {}
    owner_guard_route = owner_guard.get("last_route") if isinstance(owner_guard.get("last_route"), dict) else {}
    if not owner_guard_route:
        owner_guard_route = (
            owner_guard.get("quarantine_route") if isinstance(owner_guard.get("quarantine_route"), dict) else {}
        )
    owner_guard_projection = (
        owner_guard.get("last_projection") if isinstance(owner_guard.get("last_projection"), dict) else {}
    )
    if not owner_guard_projection:
        owner_guard_projection = (
            owner_guard.get("quarantine_projection")
            if isinstance(owner_guard.get("quarantine_projection"), dict)
            else {}
        )
    return {
        "enabled": bool(_ENABLED),
        "throttle_sec": float(_THROTTLE_SEC),
        "webspace_id": str(selected.get("webspace_id") or token_ws or "").strip() or None,
        "owner": str(selected.get("owner") or token_owner or "").strip() or None,
        "attempted_total": sum(int(row.get("attempted_total") or 0) for row in rows),
        "allowed_total": sum(int(row.get("allowed_total") or 0) for row in rows),
        "blocked_total": sum(int(row.get("blocked_total") or 0) for row in rows),
        "throttled_total": sum(int(row.get("throttled_total") or 0) for row in rows),
        "last_decision": str(selected.get("last_decision") or "").strip() or None,
        "last_policy_state": str(selected.get("last_policy_state") or "").strip() or None,
        "last_reason": str(selected.get("last_reason") or "").strip() or None,
        "last_path": str(selected.get("last_path") or "").strip() or None,
        "last_source": str(selected.get("last_source") or "").strip() or None,
        "last_channel": str(selected.get("last_channel") or "").strip() or None,
        "last_route": (
            dict(selected.get("last_route"))
            if isinstance(selected.get("last_route"), dict) and selected.get("last_route")
            else dict(owner_guard_route)
        ),
        "last_projection": (
            dict(selected.get("last_projection"))
            if isinstance(selected.get("last_projection"), dict) and selected.get("last_projection")
            else dict(owner_guard_projection)
        ),
        "last_guard_visibility": (
            dict(selected.get("last_guard_visibility"))
            if isinstance(selected.get("last_guard_visibility"), dict)
            else {}
        ),
        "last_correlation_id": str(selected.get("last_correlation_id") or "").strip() or None,
        "last_generation_id": str(selected.get("last_generation_id") or "").strip() or None,
        "last_at": float(selected.get("last_at") or 0.0) or None,
        "last_update_bytes": int(selected.get("last_update_bytes") or 0),
        "last_roots": list(selected.get("last_roots") or []),
        "last_blocked_roots": list(selected.get("last_blocked_roots") or []),
        "last_throttled_roots": list(selected.get("last_throttled_roots") or []),
        "last_affected_roots": list(selected.get("last_affected_roots") or []),
        "quarantined": bool(owner_guard.get("active")),
        "quarantine_enabled": bool(owner_guard.get("quarantine_enabled", False)),
        "quarantine_total": int(owner_guard.get("quarantine_total") or 0),
        "quarantine_denied_total": int(owner_guard.get("denied_total") or 0),
        "quarantine_remaining_s": float(owner_guard.get("quarantine_remaining_s") or 0.0) or None,
        "quarantine_until": float(owner_guard.get("quarantine_until") or 0.0) or None,
        "quarantine_reason": str(owner_guard.get("quarantine_reason") or "").strip() or None,
        "quarantine_trigger": str(owner_guard.get("quarantine_trigger") or "").strip() or None,
        "quarantine_path": str(owner_guard.get("quarantine_path") or "").strip() or None,
        "quarantine_tool": str(owner_guard.get("quarantine_tool") or "").strip() or None,
        "owner_guard": owner_guard,
        "rows": rows[:50],
    }


__all__ = [
    "govern_primary_doc_write",
    "govern_primary_doc_write_sync",
    "primary_doc_governance_snapshot",
]
