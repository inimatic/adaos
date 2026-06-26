# \src\adaos\services\scenario\projection_service.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional
import json
import logging
import os
from pathlib import Path
import threading
import time

from adaos.sdk.data.context import get_current_skill
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.node_config import load_config
from adaos.services.runtime_paths import current_state_dir
from adaos.services.scenario.node_data_scope import node_scope_data_path
from adaos.services.yjs.doc import mutate_live_room, async_get_ydoc
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.user.profile import UserProfileService
from .projection_registry import ProjectionRegistry, ProjectionTarget

_log = logging.getLogger("adaos.scenario.projection")
_PRIMARY_DOC_PRESSURE_THROTTLE_SEC = max(
    0.0,
    float(os.getenv("ADAOS_YJS_PRIMARY_DOC_PRESSURE_THROTTLE_SEC") or "0.35"),
)
_PRIMARY_DOC_THROTTLE_LOCK = threading.Lock()
_PRIMARY_DOC_THROTTLE_NEXT_ALLOWED_AT: dict[str, float] = {}
_PRIMARY_DOC_GOVERNANCE_LOCK = threading.Lock()
_PRIMARY_DOC_GOVERNANCE_STATS: dict[str, dict[str, Any]] = {}


def _int_env(name: str, default: int, minimum: int) -> int:
    try:
        return max(int(minimum), int(str(os.getenv(name) or str(default)).strip()))
    except Exception:
        return max(int(minimum), int(default))


_PRIMARY_DOC_MAX_STRING_CHARS = _int_env("ADAOS_YJS_PRIMARY_DOC_MAX_STRING_CHARS", 4096, 512)
_YJS_PROJECTION_GUARD_ENABLED = str(os.getenv("ADAOS_YJS_PROJECTION_GUARD_ENABLE") or "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_YJS_PROJECTION_DEFAULT_MAX_PAYLOAD_BYTES = _int_env(
    "ADAOS_YJS_PROJECTION_DEFAULT_MAX_PAYLOAD_BYTES",
    256 * 1024,
    1024,
)
_YJS_PROJECTION_DEFAULT_MAX_ITEMS = _int_env(
    "ADAOS_YJS_PROJECTION_DEFAULT_MAX_ITEMS",
    1000,
    1,
)
_YJS_PROJECTION_GUARD_EVENT_READ_LIMIT = _int_env(
    "ADAOS_YJS_PROJECTION_GUARD_EVENT_READ_LIMIT",
    5000,
    100,
)
_YJS_PROJECTION_GUARD_EVENT_TAIL_BYTES = _int_env(
    "ADAOS_YJS_PROJECTION_GUARD_EVENT_TAIL_BYTES",
    4 * 1024 * 1024,
    64 * 1024,
)
_YJS_PROJECTION_WRITE_AMPLIFICATION_MIN_UPDATE_BYTES = _int_env(
    "ADAOS_YJS_PROJECTION_WRITE_AMPLIFICATION_MIN_UPDATE_BYTES",
    32 * 1024,
    1024,
)
_YJS_PROJECTION_WRITE_AMPLIFICATION_RATIO = _int_env(
    "ADAOS_YJS_PROJECTION_WRITE_AMPLIFICATION_RATIO",
    8,
    2,
)
_YJS_PROJECTION_AUTOCOMPACT_ON_WRITE_AMPLIFICATION = str(
    os.getenv("ADAOS_YJS_PROJECTION_AUTOCOMPACT_ON_WRITE_AMPLIFICATION") or "1"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_YJS_PROJECTION_AUTOCOMPACT_DELAY_SEC = max(
    0.0,
    float(os.getenv("ADAOS_YJS_PROJECTION_AUTOCOMPACT_DELAY_SEC") or "0.5"),
)
_YJS_PROJECTION_AUTOCOMPACT_QUIET_SEC = max(
    0.0,
    float(os.getenv("ADAOS_YJS_PROJECTION_AUTOCOMPACT_QUIET_SEC") or "0.0"),
)
_YJS_PROJECTION_AUTOCOMPACT_MALLOC_TRIM = str(
    os.getenv("ADAOS_YJS_PROJECTION_AUTOCOMPACT_MALLOC_TRIM") or "1"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_YJS_PROJECTION_GUARD_LOCK = threading.Lock()
_YJS_PROJECTION_GUARD_STATS: dict[str, dict[str, Any]] = {}


def _projection_write_owner() -> str:
    current = get_current_skill()
    name = str(getattr(current, "name", "") or "").strip()
    if name:
        return f"skill:{name}"
    return "core"


def _local_node_id() -> str:
    try:
        conf = load_config()
        node_id = str(getattr(conf, "node_id", "") or "").strip()
        if node_id:
            return node_id
        nested = str(getattr(getattr(conf, "node_settings", None), "id", "") or "").strip()
        if nested:
            return nested
    except Exception:
        pass
    return "hub"


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except Exception:
        return None
    return result if result > 0 else None


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _json_payload_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    except Exception:
        return len(str(value).encode("utf-8", errors="replace"))


def _projection_collection_metrics(value: Any) -> dict[str, Any]:
    max_list_items = 0
    max_list_path = ""
    list_total = 0
    mapping_total = 0

    def _walk(item: Any, path: str) -> None:
        nonlocal max_list_items, max_list_path, list_total, mapping_total
        if isinstance(item, (str, bytes, bytearray)):
            return
        if isinstance(item, list) or isinstance(item, tuple):
            count = len(item)
            list_total += count
            if count > max_list_items:
                max_list_items = count
                max_list_path = path or "$"
            for index, child in enumerate(item[: min(count, 2048)]):
                _walk(child, f"{path}[{index}]" if path else f"$[{index}]")
            return
        mapping_items = _mapping_items(item)
        if mapping_items is not None:
            mapping_total += len(mapping_items)
            for key, child in mapping_items:
                next_path = f"{path}.{key}" if path else str(key)
                _walk(child, next_path)

    _walk(value, "")
    return {
        "max_list_items": max_list_items,
        "max_list_path": max_list_path or None,
        "list_item_total": list_total,
        "mapping_key_total": mapping_total,
    }


def _projection_guard_key(webspace_id: str, owner: str, path: str) -> str:
    return "\0".join([str(webspace_id or "default"), str(owner or "unknown"), str(path or "")])


def _projection_amplification_domain(path: str) -> tuple[str, str] | None:
    segments = [str(item or "").strip() for item in str(path or "").split("/") if str(item or "").strip()]
    if len(segments) < 3:
        return None
    return segments[0], segments[1]


def _projection_amplification_suspects(
    *,
    webspace_id: str,
    writer_owner: str,
    path: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    domain = _projection_amplification_domain(path)
    if domain is None:
        return []
    token_ws = str(webspace_id or "").strip() or "default"
    token_owner = str(writer_owner or "").strip()
    candidates: list[dict[str, Any]] = []
    for row in _yjs_projection_guard_rows():
        row_path = str(row.get("path") or "").strip()
        if _projection_amplification_domain(row_path) != domain:
            continue
        if str(row.get("webspace_id") or "").strip() != token_ws:
            continue
        row_owner = str(row.get("owner") or "").strip()
        if token_owner and row_owner == token_owner:
            continue
        candidates.append(
            {
                "domain": "/".join(domain),
                "owner": row_owner or None,
                "slot": str(row.get("slot") or "").strip() or None,
                "path": row_path or None,
                "reason": str(row.get("reason") or "").strip() or None,
                "payload_bytes": max(0, _int_or_zero(row.get("payload_bytes"))),
                "degraded_bytes": max(0, _int_or_zero(row.get("degraded_bytes"))),
                "max_payload_bytes": row.get("max_payload_bytes"),
                "max_list_items": max(0, _int_or_zero(row.get("max_list_items"))),
                "last_at": _float_or_zero(row.get("last_at")) or None,
            }
        )
    candidates.sort(
        key=lambda item: (
            -int(item.get("payload_bytes") or 0),
            -float(item.get("last_at") or 0.0),
            str(item.get("owner") or ""),
            str(item.get("path") or ""),
        )
    )
    return candidates[: max(1, min(int(limit or 5), 20))]


def _projection_guard_events_path(*, create: bool) -> Path:
    root = current_state_dir() / "observability"
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root / "yjs_projection_guard.ndjson"


def _append_yjs_projection_guard_event(row: Mapping[str, Any]) -> None:
    try:
        path = _projection_guard_events_path(create=True)
        event = {
            "schema": "adaos.yjs_projection_guard.event.v1",
            "event_count": 1,
            "pid": os.getpid(),
            **dict(row),
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")
    except Exception:
        _log.debug("failed to persist YJS projection guard event", exc_info=True)


def _iter_persisted_yjs_projection_guard_events() -> list[dict[str, Any]]:
    try:
        path = _projection_guard_events_path(create=False)
        if not path.exists():
            return []
        size = path.stat().st_size
        max_bytes = int(_YJS_PROJECTION_GUARD_EVENT_TAIL_BYTES)
        max_events = int(_YJS_PROJECTION_GUARD_EVENT_READ_LIMIT)
        rows: list[dict[str, Any]] = []
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(max(0, size - max_bytes))
                handle.readline()
            for raw in handle:
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
                    if len(rows) > max_events:
                        del rows[: len(rows) - max_events]
        return rows
    except Exception:
        _log.debug("failed to read persisted YJS projection guard events", exc_info=True)
        return []


def _aggregate_yjs_projection_guard_events(events: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for event in events:
        webspace_id = str(event.get("webspace_id") or "").strip() or "default"
        owner = str(event.get("owner") or "").strip() or "unknown"
        path = str(event.get("path") or "").strip()
        key = _projection_guard_key(webspace_id, owner, path)
        last_at = _float_or_zero(event.get("last_at"))
        current = rows.get(key)
        if current is None:
            current = {}
            rows[key] = current
        guarded_total = _int_or_zero(current.get("guarded_total")) + max(1, _int_or_zero(event.get("event_count") or 1))
        if last_at >= _float_or_zero(current.get("last_at")):
            current.update(
                {
                    "webspace_id": webspace_id,
                    "owner": owner,
                    "scope": str(event.get("scope") or "").strip() or None,
                    "slot": str(event.get("slot") or "").strip() or None,
                    "path": path or None,
                    "root": str(event.get("root") or "").strip() or None,
                    "reason": str(event.get("reason") or "").strip() or "yjs_projection_payload_guarded",
                    "payload_bytes": max(0, _int_or_zero(event.get("payload_bytes"))),
                    "projected_bytes": max(0, _int_or_zero(event.get("projected_bytes"))),
                    "degraded_bytes": max(0, _int_or_zero(event.get("degraded_bytes"))),
                    "update_bytes": max(0, _int_or_zero(event.get("update_bytes"))),
                    "amplification_ratio": round(max(0.0, _float_or_zero(event.get("amplification_ratio"))), 3),
                    "max_payload_bytes": event.get("max_payload_bytes"),
                    "max_items": event.get("max_items"),
                    "max_list_items": _int_or_zero(event.get("max_list_items")),
                    "max_list_path": str(event.get("max_list_path") or "").strip() or None,
                    "list_item_total": _int_or_zero(event.get("list_item_total")),
                    "mapping_key_total": _int_or_zero(event.get("mapping_key_total")),
                    "route": dict(event.get("route") or {}) if isinstance(event.get("route"), dict) else {},
                    "recovery": dict(event.get("recovery") or {}) if isinstance(event.get("recovery"), dict) else {},
                    "last_at": last_at,
                    "last_pid": _int_or_zero(event.get("pid")) or None,
                }
            )
        current["guarded_total"] = guarded_total
    return rows


def _yjs_projection_guard_rows() -> list[dict[str, Any]]:
    persisted = _aggregate_yjs_projection_guard_events(_iter_persisted_yjs_projection_guard_events())
    with _YJS_PROJECTION_GUARD_LOCK:
        memory_rows = [dict(item) for item in _YJS_PROJECTION_GUARD_STATS.values()]
    if not persisted:
        return memory_rows
    for row in memory_rows:
        key = _projection_guard_key(
            str(row.get("webspace_id") or ""),
            str(row.get("owner") or ""),
            str(row.get("path") or ""),
        )
        existing = persisted.get(key)
        if existing is None:
            persisted[key] = row
            continue
        guarded_total = max(_int_or_zero(existing.get("guarded_total")), _int_or_zero(row.get("guarded_total")))
        if _float_or_zero(row.get("last_at")) > _float_or_zero(existing.get("last_at")):
            existing.update(row)
        existing["guarded_total"] = guarded_total
    return [dict(item) for item in persisted.values()]


def _record_yjs_projection_guard_event(
    *,
    webspace_id: str,
    owner: str,
    scope: str,
    slot: str,
    path: str,
    root_name: str,
    reason: str,
    payload_bytes: int,
    projected_bytes: int,
    degraded_bytes: int,
    max_payload_bytes: int | None,
    max_items: int | None,
    collection_metrics: Mapping[str, Any],
    route: Mapping[str, Any] | None,
    update_bytes: int | None = None,
    amplification_ratio: float | None = None,
    recovery: Mapping[str, Any] | None = None,
) -> None:
    key = _projection_guard_key(webspace_id, owner, path)
    persisted_row: dict[str, Any] | None = None
    with _YJS_PROJECTION_GUARD_LOCK:
        current = dict(_YJS_PROJECTION_GUARD_STATS.get(key) or {})
        current["webspace_id"] = str(webspace_id or "").strip() or "default"
        current["owner"] = str(owner or "").strip() or "unknown"
        current["scope"] = str(scope or "").strip() or None
        current["slot"] = str(slot or "").strip() or None
        current["path"] = str(path or "").strip() or None
        current["root"] = str(root_name or "").strip() or None
        current["reason"] = str(reason or "").strip() or "yjs_projection_payload_guarded"
        current["payload_bytes"] = max(0, int(payload_bytes or 0))
        current["projected_bytes"] = max(0, int(projected_bytes or 0))
        current["degraded_bytes"] = max(0, int(degraded_bytes or 0))
        current["update_bytes"] = max(0, int(update_bytes or 0))
        current["amplification_ratio"] = round(float(amplification_ratio or 0.0), 3)
        current["max_payload_bytes"] = max_payload_bytes
        current["max_items"] = max_items
        current["max_list_items"] = int(collection_metrics.get("max_list_items") or 0)
        current["max_list_path"] = str(collection_metrics.get("max_list_path") or "").strip() or None
        current["list_item_total"] = int(collection_metrics.get("list_item_total") or 0)
        current["mapping_key_total"] = int(collection_metrics.get("mapping_key_total") or 0)
        current["route"] = dict(route or {})
        current["recovery"] = dict(recovery or {})
        current["last_at"] = time.time()
        current["guarded_total"] = int(current.get("guarded_total") or 0) + 1
        _YJS_PROJECTION_GUARD_STATS[key] = current
        persisted_row = dict(current)
    _append_yjs_projection_guard_event(persisted_row)


def _projection_compaction_runtime_summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "log_mode": snapshot.get("log_mode"),
        "update_log_entries": max(0, _int_or_zero(snapshot.get("update_log_entries"))),
        "update_log_bytes": max(0, _int_or_zero(snapshot.get("update_log_bytes"))),
        "replay_window_entries": max(0, _int_or_zero(snapshot.get("replay_window_entries"))),
        "replay_window_bytes": max(0, _int_or_zero(snapshot.get("replay_window_bytes"))),
        "runtime_compaction_eligible": bool(snapshot.get("runtime_compaction_eligible")),
        "snapshot_file_exists": bool(snapshot.get("snapshot_file_exists")),
        "snapshot_file_size": max(0, _int_or_zero(snapshot.get("snapshot_file_size"))),
        "persisted_up_to_date": bool(snapshot.get("persisted_up_to_date")),
        "compact_total": max(0, _int_or_zero(snapshot.get("compact_total"))),
        "backup_total": max(0, _int_or_zero(snapshot.get("backup_total"))),
        "auto_backup_total": max(0, _int_or_zero(snapshot.get("auto_backup_total"))),
    }


def _trim_allocator_after_projection_compaction() -> bool:
    if not _YJS_PROJECTION_AUTOCOMPACT_MALLOC_TRIM:
        return False
    if os.name != "posix":
        return False
    try:
        import ctypes  # pylint: disable=import-outside-toplevel

        libc = ctypes.CDLL("libc.so.6")
        trim = getattr(libc, "malloc_trim", None)
        if not callable(trim):
            return False
        return bool(trim(0))
    except Exception:
        return False


async def _compact_projection_amplification_store(
    webspace_id: str,
    *,
    mode: str,
    delay_sec: float | None = None,
) -> dict[str, Any]:
    key = str(webspace_id or "").strip() or "default"
    delay = float(_YJS_PROJECTION_AUTOCOMPACT_DELAY_SEC if delay_sec is None else delay_sec)
    result = {
        "action": "ystore_runtime_compaction",
        "requested": False,
        "executed": False,
        "reason": "projection_write_amplification",
        "webspace_id": key,
        "mode": str(mode or "").strip() or "background",
        "delay_sec": delay,
        "min_quiet_sec": float(_YJS_PROJECTION_AUTOCOMPACT_QUIET_SEC),
    }
    if not _YJS_PROJECTION_AUTOCOMPACT_ON_WRITE_AMPLIFICATION:
        result["disabled"] = True
        return result
    result["requested"] = True
    if delay > 0.0:
        await asyncio.sleep(delay)
    try:
        from adaos.services.yjs.store import get_ystore_for_webspace

        store = get_ystore_for_webspace(key)
        before = store.runtime_snapshot()
        result["before"] = _projection_compaction_runtime_summary(before)
        quiet_sec = float(_YJS_PROJECTION_AUTOCOMPACT_QUIET_SEC)
        last_write_at = _float_or_zero(before.get("last_write_at"))
        if quiet_sec > 0.0 and last_write_at > 0.0 and time.time() - last_write_at < quiet_sec:
            result["skipped"] = "quiet_window"
            return result
        if not bool(before.get("runtime_compaction_eligible")) and bool(before.get("persisted_up_to_date")):
            result["skipped"] = "no_runtime_replay_tail"
            result["after"] = result["before"]
            return result

        await store.backup_to_disk(
            compact_runtime=True,
            backup_kind="projection_write_amplification",
        )
        after = store.runtime_snapshot()
        result["after"] = _projection_compaction_runtime_summary(after)
        result["executed"] = True
        before_replay_bytes = max(0, _int_or_zero(before.get("replay_window_bytes")))
        after_replay_bytes = max(0, _int_or_zero(after.get("replay_window_bytes")))
        before_entries = max(0, _int_or_zero(before.get("update_log_entries")))
        after_entries = max(0, _int_or_zero(after.get("update_log_entries")))
        result["compacted"] = bool(after_entries < before_entries or after_replay_bytes < before_replay_bytes)
        result["released_replay_bytes"] = max(0, before_replay_bytes - after_replay_bytes)
        try:
            import gc  # pylint: disable=import-outside-toplevel

            result["gc_collected"] = int(gc.collect() or 0)
        except Exception:
            result["gc_collected"] = 0
        result["malloc_trimmed"] = _trim_allocator_after_projection_compaction()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        _log.debug("failed to compact YStore after projection amplification webspace=%s", key, exc_info=True)
    return result


def _request_projection_amplification_compaction(webspace_id: str) -> dict[str, Any]:
    key = str(webspace_id or "").strip() or "default"
    result = {
        "action": "ystore_runtime_compaction",
        "requested": False,
        "reason": "projection_write_amplification",
        "webspace_id": key,
        "mode": "background",
        "delay_sec": float(_YJS_PROJECTION_AUTOCOMPACT_DELAY_SEC),
        "min_quiet_sec": float(_YJS_PROJECTION_AUTOCOMPACT_QUIET_SEC),
    }
    if not _YJS_PROJECTION_AUTOCOMPACT_ON_WRITE_AMPLIFICATION:
        result["disabled"] = True
        return result

    async def _runner() -> None:
        outcome = await _compact_projection_amplification_store(key, mode="background")
        if outcome.get("executed"):
            _log.warning(
                "YStore compacted after projection amplification webspace=%s compacted=%s released_replay_bytes=%s malloc_trimmed=%s",
                key,
                bool(outcome.get("compacted")),
                int(outcome.get("released_replay_bytes") or 0),
                bool(outcome.get("malloc_trimmed")),
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        thread = threading.Thread(
            target=lambda: asyncio.run(_runner()),
            name=f"adaos-yjs-projection-compact-{key}",
            daemon=True,
        )
        thread.start()
    else:
        loop.create_task(_runner())
    result["requested"] = True
    return result


def yjs_projection_guard_snapshot(
    *,
    webspace_id: str | None = None,
    owner: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    token_ws = str(webspace_id or "").strip()
    token_owner = str(owner or "").strip()
    max_items = max(1, min(int(limit or 20), 100))
    rows = _yjs_projection_guard_rows()
    if token_ws:
        rows = [row for row in rows if str(row.get("webspace_id") or "") == token_ws]
    if token_owner:
        rows = [row for row in rows if str(row.get("owner") or "") == token_owner]
    rows.sort(key=lambda row: float(row.get("last_at") or 0.0), reverse=True)
    guarded_total = sum(int(row.get("guarded_total") or 0) for row in rows)
    return {
        "schema": "adaos.yjs_projection_guard.v1",
        "enabled": bool(_YJS_PROJECTION_GUARD_ENABLED),
        "webspace_id": token_ws or None,
        "owner": token_owner or None,
        "total": len(rows),
        "totals": {
            "guarded": guarded_total,
        },
        "items": rows[:max_items],
    }


def _record_yjs_projection_write_amplification(
    *,
    webspace_id: str,
    owner: str,
    scope: str,
    slot: str,
    path: str,
    root_name: str,
    payload_bytes: int,
    projected_bytes: int,
    update_bytes: int,
    max_payload_bytes: int | None,
    max_items: int | None,
    collection_metrics: Mapping[str, Any],
    route: Mapping[str, Any] | None,
    compaction_mode: str = "background",
) -> bool:
    if not _YJS_PROJECTION_GUARD_ENABLED:
        return False
    observed_update_bytes = max(0, int(update_bytes or 0))
    observed_payload_bytes = max(1, int(projected_bytes or payload_bytes or 1))
    ratio = float(observed_update_bytes) / float(observed_payload_bytes)
    if observed_update_bytes < int(_YJS_PROJECTION_WRITE_AMPLIFICATION_MIN_UPDATE_BYTES):
        return False
    if ratio < float(_YJS_PROJECTION_WRITE_AMPLIFICATION_RATIO):
        return False
    mode = str(compaction_mode or "").strip() or "background"
    if mode == "inline_after_detached_write":
        recovery = {
            "action": "ystore_runtime_compaction",
            "requested": True,
            "reason": "projection_write_amplification",
            "webspace_id": str(webspace_id or "").strip() or "default",
            "mode": mode,
            "delay_sec": 0.0,
            "min_quiet_sec": float(_YJS_PROJECTION_AUTOCOMPACT_QUIET_SEC),
            "deferred_until": "detached_writer_flush_complete",
        }
        if not _YJS_PROJECTION_AUTOCOMPACT_ON_WRITE_AMPLIFICATION:
            recovery["disabled"] = True
    else:
        recovery = _request_projection_amplification_compaction(webspace_id)
    _record_yjs_projection_guard_event(
        webspace_id=webspace_id,
        owner=owner,
        scope=scope,
        slot=slot,
        path=path,
        root_name=root_name,
        reason="yjs_projection_write_amplification",
        payload_bytes=max(0, int(payload_bytes or 0)),
        projected_bytes=max(0, int(projected_bytes or 0)),
        degraded_bytes=max(0, int(projected_bytes or payload_bytes or 0)),
        max_payload_bytes=max_payload_bytes,
        max_items=max_items,
        collection_metrics=collection_metrics,
        route=route,
        update_bytes=observed_update_bytes,
        amplification_ratio=ratio,
        recovery=recovery,
    )
    policy = {
        "policy_state": "warn",
        "reason": "yjs_projection_write_amplification",
        "observed_state": "high",
        "route": dict(route or {}),
        "projection": {
            "scope": str(scope or "").strip() or None,
            "slot": str(slot or "").strip() or None,
            "root": str(root_name or "").strip() or None,
            "path": str(path or "").strip() or None,
            "payload_bytes": max(0, int(payload_bytes or 0)),
            "projected_bytes": max(0, int(projected_bytes or 0)),
            "update_bytes": observed_update_bytes,
            "amplification_ratio": round(ratio, 3),
        },
        "recovery": dict(recovery),
    }
    try:
        from adaos.services.yjs.governance import govern_primary_doc_write_sync

        govern_primary_doc_write_sync(
            webspace_id=webspace_id,
            owner=owner,
            root_names=[root_name] if root_name else [],
            path=path,
            source="projection_service.post_write",
            channel="projection.yjs.amplification",
            policy=policy,
            update_bytes=observed_update_bytes,
        )
    except Exception:
        _log.debug(
            "failed to record post-write YJS projection amplification webspace=%s owner=%s path=%s",
            webspace_id,
            owner,
            path,
            exc_info=True,
        )
    _log.warning(
        "YJS projection write amplification webspace=%s owner=%s slot=%s path=%s payload_bytes=%s update_bytes=%s ratio=%.3f",
        webspace_id,
        owner,
        slot,
        path,
        max(0, int(projected_bytes or payload_bytes or 0)),
        observed_update_bytes,
        ratio,
    )
    return True


def _yjs_primary_doc_policy_state(*, webspace_id: str, owner: str, root_name: str) -> dict[str, Any]:
    if _PRIMARY_DOC_PRESSURE_THROTTLE_SEC <= 0.0:
        return {"policy_state": "ok"}
    if not str(owner or "").strip().startswith("skill:"):
        return {"policy_state": "ok"}
    try:
        from adaos.services.yjs.load_mark import yjs_primary_doc_policy_snapshot

        payload = yjs_primary_doc_policy_snapshot(
            webspace_id=webspace_id,
            owner=owner,
            root_names=[root_name],
        )
        if isinstance(payload, dict):
            return payload
    except Exception:
        _log.debug("failed to evaluate YJS primary-doc pressure policy webspace=%s root=%s", webspace_id, root_name, exc_info=True)
    return {"policy_state": "ok"}


def _record_primary_doc_governance_event(*, webspace_id: str, owner: str, path: str, policy: dict[str, Any]) -> None:
    token_ws = str(webspace_id or "").strip() or "default"
    token_owner = str(owner or "").strip() or "unknown"
    key = f"{token_ws}\0{token_owner}"
    policy_state = str(policy.get("policy_state") or "").strip().lower()
    if policy_state not in {"block", "throttle"}:
        return
    with _PRIMARY_DOC_GOVERNANCE_LOCK:
        current = dict(_PRIMARY_DOC_GOVERNANCE_STATS.get(key) or {})
        if policy_state == "block":
            current["blocked_total"] = int(current.get("blocked_total") or 0) + 1
        if policy_state == "throttle":
            current["throttled_total"] = int(current.get("throttled_total") or 0) + 1
        current["webspace_id"] = token_ws
        current["owner"] = token_owner
        current["last_policy_state"] = policy_state
        current["last_reason"] = str(policy.get("reason") or "").strip() or None
        current["last_path"] = str(path or "").strip() or None
        current["last_at"] = time.time()
        suspects = policy.get("write_amplification_suspects")
        current["last_write_amplification_suspects"] = (
            [dict(item) for item in suspects[:10] if isinstance(item, dict)] if isinstance(suspects, list) else []
        )
        if policy_state == "block":
            current["last_blocked_roots"] = list(policy.get("blocked_roots") or [])
            current["last_affected_roots"] = list(policy.get("blocked_roots") or [])
        if policy_state == "throttle":
            current["last_throttled_roots"] = list(policy.get("throttled_roots") or [])
            current["last_affected_roots"] = list(policy.get("throttled_roots") or [])
        _PRIMARY_DOC_GOVERNANCE_STATS[key] = current


def primary_doc_governance_snapshot(*, webspace_id: str | None = None, owner: str | None = None) -> dict[str, Any]:
    try:
        from adaos.services.yjs.governance import primary_doc_governance_snapshot as shared_snapshot

        return shared_snapshot(webspace_id=webspace_id, owner=owner)
    except Exception:
        _log.debug("failed to read shared primary-doc governance snapshot", exc_info=True)
    token_ws = str(webspace_id or "").strip() or "default"
    token_owner = str(owner or "").strip()
    with _PRIMARY_DOC_GOVERNANCE_LOCK:
        current = dict(_PRIMARY_DOC_GOVERNANCE_STATS.get(f"{token_ws}\0{token_owner}") or {})
    return {
        "webspace_id": token_ws,
        "owner": token_owner or None,
        "blocked_total": int(current.get("blocked_total") or 0),
        "throttled_total": int(current.get("throttled_total") or 0),
        "last_policy_state": str(current.get("last_policy_state") or "").strip() or None,
        "last_reason": str(current.get("last_reason") or "").strip() or None,
        "last_path": str(current.get("last_path") or "").strip() or None,
        "last_at": float(current.get("last_at") or 0.0) or None,
        "last_blocked_roots": list(current.get("last_blocked_roots") or []),
        "last_throttled_roots": list(current.get("last_throttled_roots") or []),
        "last_affected_roots": list(current.get("last_affected_roots") or []),
        "last_write_amplification_suspects": list(current.get("last_write_amplification_suspects") or []),
    }


async def _govern_primary_doc_write(*, policy: dict[str, Any], webspace_id: str, path: str, owner: str) -> bool:
    try:
        from adaos.services.yjs.owner_guard import admit_owner_work

        root_name = path.split("/", 1)[0] if path else ""
        admission = admit_owner_work(
            webspace_id=webspace_id,
            owner=owner,
            root_names=[root_name] if root_name else [],
            path=path,
            source="projection_service",
            channel="projection.yjs",
            work_kind="projection",
            policy=policy,
        )
        if not bool(admission.get("allowed", True)):
            _log.warning(
                "YJS projection denied by owner guard webspace=%s owner=%s path=%s reason=%s retry_after_s=%s",
                webspace_id,
                owner,
                path or "-",
                admission.get("reason") or "owner_quarantined",
                admission.get("retry_after_s") or 0,
            )
            return False
    except Exception:
        _log.debug("failed to apply YJS owner guard for projection webspace=%s owner=%s path=%s", webspace_id, owner, path, exc_info=True)
    try:
        from adaos.services.yjs.governance import govern_primary_doc_write

        root_name = path.split("/", 1)[0] if path else ""
        return await govern_primary_doc_write(
            webspace_id=webspace_id,
            owner=owner,
            root_names=[root_name] if root_name else [],
            path=path,
            source="projection_service",
            channel="projection.yjs",
            policy=policy,
        )
    except Exception:
        _log.debug("failed to apply shared primary-doc governance", exc_info=True)
        return True


def _clone_json_like(value: Any) -> Any:
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        try:
            raw = to_json()
            if isinstance(raw, str):
                return json.loads(raw)
            return json.loads(json.dumps(raw))
        except Exception:
            pass
    try:
        return json.loads(json.dumps(value))
    except Exception:
        if isinstance(value, dict):
            return {str(k): _clone_json_like(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_clone_json_like(v) for v in value]
        if isinstance(value, tuple):
            return [_clone_json_like(v) for v in value]
        items = getattr(value, "items", None)
        if callable(items):
            try:
                return {str(k): _clone_json_like(v) for k, v in items()}
            except Exception:
                return value
        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, bytearray)):
            try:
                return [_clone_json_like(v) for v in list(value)]
            except Exception:
                return value
        return value


def _mapping_items(value: Any) -> list[tuple[str, Any]] | None:
    if isinstance(value, dict):
        return [(str(key), item) for key, item in value.items() if str(key)]
    items = getattr(value, "items", None)
    if callable(items):
        try:
            return [(str(key), item) for key, item in items() if str(key)]
        except Exception:
            return None
    return None


def _compact_projection_string(value: str) -> str:
    limit = int(_PRIMARY_DOC_MAX_STRING_CHARS)
    if len(value) <= limit:
        return value
    marker = f"... [truncated chars={len(value)} limit={limit}] ..."
    head_len = max(128, int(limit * 0.75))
    tail_len = max(64, limit - head_len - len(marker))
    if head_len + tail_len + len(marker) >= len(value):
        return value
    return value[:head_len] + marker + value[-tail_len:]


def _compact_projection_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _compact_projection_string(value)
    if isinstance(value, dict):
        return {str(key): _compact_projection_payload(item) for key, item in value.items() if str(key)}
    if isinstance(value, list):
        return [_compact_projection_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_compact_projection_payload(item) for item in value]
    items = _mapping_items(value)
    if items is not None:
        return {key: _compact_projection_payload(item) for key, item in items if key}
    return _clone_json_like(value)


def _projection_budget(rule: Any) -> dict[str, Any]:
    budget = getattr(rule, "budget", None)
    return dict(budget) if isinstance(budget, dict) else {}


def _projection_route(rule: Any) -> dict[str, Any]:
    route = getattr(rule, "route", None)
    return dict(route) if isinstance(route, dict) else {}


def _guarded_projection_payload(
    value: Any,
    *,
    scope: str,
    slot: str,
    path: str,
    owner: str,
    budget: Mapping[str, Any],
    route: Mapping[str, Any],
) -> tuple[Any, dict[str, Any] | None]:
    if not _YJS_PROJECTION_GUARD_ENABLED:
        return value, None
    payload_bytes = _json_payload_bytes(value)
    max_payload_bytes = _positive_int(budget.get("max_payload_bytes")) or int(_YJS_PROJECTION_DEFAULT_MAX_PAYLOAD_BYTES)
    max_items = _positive_int(budget.get("max_items")) or int(_YJS_PROJECTION_DEFAULT_MAX_ITEMS)
    collection_metrics = _projection_collection_metrics(value)
    reason = ""
    if max_payload_bytes and payload_bytes > max_payload_bytes:
        reason = "yjs_projection_payload_budget_exceeded"
    if max_items and int(collection_metrics.get("max_list_items") or 0) > max_items:
        reason = reason or "yjs_projection_item_budget_exceeded"
    if not reason:
        return value, None

    preserved: dict[str, Any] = {}
    if isinstance(value, dict):
        for key in ("summary", "count", "total_bytes", "capabilities", "runtime", "updated_at", "ok"):
            item = value.get(key)
            if item is not None and _json_payload_bytes(item) <= 16 * 1024:
                preserved[key] = item
    guard = {
        "schema": "adaos.yjs_projection_guard.v1",
        "state": "degraded",
        "reason": reason,
        "owner": owner,
        "scope": scope,
        "slot": slot,
        "path": path,
        "payload_bytes": payload_bytes,
        "max_payload_bytes": max_payload_bytes,
        "max_items": max_items,
        "max_list_items": int(collection_metrics.get("max_list_items") or 0),
        "max_list_path": collection_metrics.get("max_list_path"),
        "list_item_total": int(collection_metrics.get("list_item_total") or 0),
        "route": dict(route or {}),
    }
    degraded = {
        "ok": False,
        "state": "degraded",
        "error": reason,
        "guard": guard,
        "preserved": preserved,
    }
    guard["degraded_bytes"] = _json_payload_bytes(degraded)
    return degraded, {
        **guard,
        "projected_bytes": payload_bytes,
        "collection_metrics": dict(collection_metrics),
    }


def _projection_policy_metadata(
    *,
    scope: str,
    slot: str,
    target: ProjectionTarget,
    path: str,
    root_name: str,
) -> dict[str, Any]:
    surface = ".".join(part for part in (str(scope or "").strip(), str(slot or "").strip()) if part)
    return {
        "route": {
            "kind": "yjs_projection",
            "surface": surface or None,
            "backend": str(target.backend or "yjs"),
            "path": str(path or "").strip() or None,
            "root": str(root_name or "").strip() or None,
        },
        "projection": {
            "scope": str(scope or "").strip() or None,
            "slot": str(slot or "").strip() or None,
            "backend": str(target.backend or "yjs"),
            "webspace_id": str(target.webspace_id or "").strip() or None,
            "path": str(path or "").strip() or None,
            "root": str(root_name or "").strip() or None,
        },
    }


def _enrich_projection_policy(
    policy: dict[str, Any],
    *,
    scope: str,
    slot: str,
    target: ProjectionTarget,
    path: str,
    root_name: str,
) -> dict[str, Any]:
    result = dict(policy or {})
    metadata = _projection_policy_metadata(
        scope=scope,
        slot=slot,
        target=target,
        path=path,
        root_name=root_name,
    )
    for key, value in metadata.items():
        result.setdefault(key, value)
    return result


def _json_like_equal(current: Any, next_value: Any) -> bool:
    if current is next_value:
        return True

    current_items = _mapping_items(current)
    next_items = _mapping_items(next_value)
    if current_items is not None or next_items is not None:
        if current_items is None or next_items is None:
            return False
        if len(current_items) != len(next_items):
            return False
        next_lookup = {key: item for key, item in next_items}
        if len(next_lookup) != len(next_items):
            return False
        for key, current_item in current_items:
            if key not in next_lookup:
                return False
            if not _json_like_equal(current_item, next_lookup[key]):
                return False
        return True

    if isinstance(current, (list, tuple)) or isinstance(next_value, (list, tuple)):
        if not isinstance(current, (list, tuple)) or not isinstance(next_value, (list, tuple)):
            return False
        if len(current) != len(next_value):
            return False
        return all(_json_like_equal(left, right) for left, right in zip(current, next_value))

    try:
        return current == next_value
    except Exception:
        return _clone_json_like(current) == _clone_json_like(next_value)


def _merge_nested_path(existing: Any, segments: List[str], payload: Any) -> tuple[bool, Any]:
    if not segments:
        if _json_like_equal(existing, payload):
            return False, existing
        return True, _clone_json_like(payload)

    key = str(segments[0] or "")
    if not key:
        return False, _clone_json_like(existing)

    child_existing = None
    if isinstance(existing, dict):
        child_existing = existing.get(key)
    else:
        items = _mapping_items(existing)
        if items is not None:
            for item_key, item_value in items:
                if item_key == key:
                    child_existing = item_value
                    break

    changed, merged_child = _merge_nested_path(child_existing, segments[1:], payload)
    if not changed:
        return False, existing

    base = _clone_json_like(existing)
    if not isinstance(base, dict):
        base = {}
    merged = dict(base)
    merged[key] = merged_child
    return True, merged


def _yjs_map_class() -> Any | None:
    try:
        import y_py as Y  # pylint: disable=import-outside-toplevel

        cls = getattr(Y, "YMap", None)
        return cls if callable(cls) else None
    except Exception:
        return None


def _is_mutable_y_map(value: Any) -> bool:
    return callable(getattr(value, "get", None)) and callable(getattr(value, "set", None))


def _to_nested_y_map(value: Any, txn: Any, ymap_cls: Any) -> Any:
    items = _mapping_items(value)
    if items is None:
        return _clone_json_like(value)
    result = ymap_cls({})
    for key, child in items:
        result.set(txn, key, _to_nested_y_map(child, txn, ymap_cls))
    return result


def _ensure_nested_y_map(parent: Any, txn: Any, key: str, ymap_cls: Any) -> Any | None:
    try:
        current = parent.get(key)
    except Exception:
        current = None
    if _is_mutable_y_map(current):
        return current
    items = _mapping_items(current)
    try:
        child = _to_nested_y_map(current, txn, ymap_cls) if items is not None else ymap_cls({})
        parent.set(txn, key, child)
        return child
    except Exception:
        return None


def _set_nested_y_map_path(root: Any, txn: Any, segments: List[str], payload: Any) -> bool | None:
    """
    Mutate long Yjs projection paths in-place when the backing values are YMaps.

    The legacy fallback rewrites the top-level branch, for example
    ``data["nodes"]``. On a large shared document that makes a tiny leaf update
    encode the whole branch. This helper converts plain mapping ancestors into
    nested YMaps once, then future writes touch only the leaf key.
    """

    if len(segments) < 2 or not _is_mutable_y_map(root):
        return None
    ymap_cls = _yjs_map_class()
    if ymap_cls is None:
        return None
    parent = root
    for raw_key in segments[:-1]:
        key = str(raw_key or "").strip()
        if not key:
            return None
        parent = _ensure_nested_y_map(parent, txn, key, ymap_cls)
        if parent is None or not _is_mutable_y_map(parent):
            return None
    leaf_key = str(segments[-1] or "").strip()
    if not leaf_key:
        return None
    try:
        current = parent.get(leaf_key)
        if _json_like_equal(current, payload):
            return False
        parent.set(txn, leaf_key, _clone_json_like(payload))
        return True
    except Exception:
        return None


@dataclass(slots=True)
class ProjectionService:
    """
    Apply logical ctx.* writes to physical backends using ProjectionRegistry.

    For MVP supports:
      - backend="yjs": writes to YDoc paths (data/...),
      - backend="kv":  profile settings via UserProfileService (current_user).
    """

    ctx: AgentContext
    registry: ProjectionRegistry

    @classmethod
    def from_ctx(cls, ctx: Optional[AgentContext] = None) -> "ProjectionService":
        c = ctx or get_ctx()
        return cls(ctx=c, registry=c.projections)

    async def apply(
        self,
        scope: str,
        slot: str,
        value: Any,
        *,
        user_id: Optional[str] = None,
        webspace_id: Optional[str] = None,
    ) -> None:
        resolve_rule = getattr(self.registry, "resolve_rule", None)
        rule = resolve_rule(scope, slot) if callable(resolve_rule) else None
        targets = list(getattr(rule, "targets", []) or []) if rule is not None else self.registry.resolve(scope, slot)
        if not targets:
            _log.debug("no projections configured for scope=%s slot=%s", scope, slot)
            return
        for t in targets:
            if t.backend == "yjs":
                await self._apply_yjs(t, value, scope=scope, slot=slot, user_id=user_id, webspace_id=webspace_id, rule=rule)
            elif t.backend == "kv":
                self._apply_kv(scope, slot, value, user_id=user_id)
            else:
                # sql/other backends are reserved for future use
                _log.debug("backend %s is not implemented yet for scope=%s slot=%s", t.backend, scope, slot)

    async def _apply_yjs(
        self,
        target: ProjectionTarget,
        value: Any,
        *,
        scope: str,
        slot: str,
        user_id: Optional[str],
        webspace_id: Optional[str],
        rule: Any = None,
    ) -> None:
        # For projections we trust the calling context (events_ws, ctx.* helpers)
        # to pass the actual webspace id used by the Y websocket room. Fall back
        # to a literal "default" when nothing is provided so that the same id is
        # used consistently across YDoc, events and projections.
        token = (webspace_id or target.webspace_id or "default").strip()
        ws_id = token or "default"
        path = target.path or ""
        if not path:
            return
        if str(scope or "").strip() == "subnet":
            path = node_scope_data_path(path, _local_node_id())

        # Allow simple {user_id} templating inside Yjs paths.
        if "{user_id}" in path:
            uid = user_id or UserProfileService(self.ctx).current_user_id()
            path = path.replace("{user_id}", uid)

        segments = [s for s in path.split("/") if s]
        if len(segments) < 2:
            return
        root_name = segments[0]
        owner = _projection_write_owner()
        # ProjectionService is the authority boundary for skill-visible Yjs
        # writes. Prefer the active live room for every governed projection so
        # browser sessions observe skill state changes immediately; detached
        # YStore writes remain the fallback when no room is active.
        prefer_live_room = True
        policy = _yjs_primary_doc_policy_state(webspace_id=ws_id, owner=owner, root_name=root_name)
        policy = _enrich_projection_policy(
            policy,
            scope=scope,
            slot=slot,
            target=target,
            path=path,
            root_name=root_name,
        )
        suspects = _projection_amplification_suspects(webspace_id=ws_id, writer_owner=owner, path=path)
        if suspects:
            policy["write_amplification_suspects"] = suspects
            policy["amplified_branch_owner"] = suspects[0].get("owner")
        if not await _govern_primary_doc_write(policy=policy, webspace_id=ws_id, path=path, owner=owner):
            return
        projected_value = _compact_projection_payload(value)
        budget = _projection_budget(rule)
        route = _projection_route(rule)
        projected_value, guard = _guarded_projection_payload(
            projected_value,
            scope=scope,
            slot=slot,
            path=path,
            owner=owner,
            budget=budget,
            route=route,
        )
        if guard is not None:
            degraded_bytes = int(guard.get("degraded_bytes") or _json_payload_bytes(projected_value))
            _record_yjs_projection_guard_event(
                webspace_id=ws_id,
                owner=owner,
                scope=scope,
                slot=slot,
                path=path,
                root_name=root_name,
                reason=str(guard.get("reason") or "yjs_projection_payload_guarded"),
                payload_bytes=int(guard.get("payload_bytes") or 0),
                projected_bytes=int(guard.get("projected_bytes") or 0),
                degraded_bytes=degraded_bytes,
                max_payload_bytes=_positive_int(guard.get("max_payload_bytes")),
                max_items=_positive_int(guard.get("max_items")),
                collection_metrics=guard.get("collection_metrics") if isinstance(guard.get("collection_metrics"), dict) else {},
                route=route,
            )
            policy.setdefault("projection_guard", {key: value for key, value in guard.items() if key != "collection_metrics"})
            policy.setdefault("reason", str(guard.get("reason") or "yjs_projection_payload_guarded"))
            _log.warning(
                "YJS projection payload guarded webspace=%s owner=%s slot=%s path=%s bytes=%s max_bytes=%s max_list_items=%s max_items=%s reason=%s",
                ws_id,
                owner,
                slot,
                path,
                int(guard.get("payload_bytes") or 0),
                guard.get("max_payload_bytes") or "-",
                guard.get("max_list_items") or 0,
                guard.get("max_items") or "-",
                guard.get("reason") or "yjs_projection_payload_guarded",
            )

        write_payload_bytes = int(guard.get("payload_bytes") or 0) if isinstance(guard, dict) else _json_payload_bytes(projected_value)
        write_projected_bytes = _json_payload_bytes(projected_value)
        write_collection_metrics = (
            dict(guard.get("collection_metrics"))
            if isinstance(guard, dict) and isinstance(guard.get("collection_metrics"), dict)
            else _projection_collection_metrics(projected_value)
        )
        write_max_payload_bytes = _positive_int(budget.get("max_payload_bytes")) or int(_YJS_PROJECTION_DEFAULT_MAX_PAYLOAD_BYTES)
        write_max_items = _positive_int(budget.get("max_items")) or int(_YJS_PROJECTION_DEFAULT_MAX_ITEMS)
        policy_route = policy.get("route") if isinstance(policy.get("route"), dict) else {}
        write_route = dict(route or policy_route or {})
        detached_compaction_needed = False

        def _on_yjs_update(update_meta: Mapping[str, Any]) -> None:
            nonlocal detached_compaction_needed
            live_room_update = bool(update_meta.get("live_room"))
            compaction_needed = _record_yjs_projection_write_amplification(
                webspace_id=ws_id,
                owner=owner,
                scope=scope,
                slot=slot,
                path=path,
                root_name=root_name,
                payload_bytes=write_payload_bytes,
                projected_bytes=write_projected_bytes,
                update_bytes=_int_or_zero(update_meta.get("update_bytes")),
                max_payload_bytes=write_max_payload_bytes,
                max_items=write_max_items,
                collection_metrics=write_collection_metrics,
                route=write_route,
                compaction_mode="background" if live_room_update else "inline_after_detached_write",
            )
            if compaction_needed and not live_room_update:
                detached_compaction_needed = True

        def _mutator(doc, txn) -> None:
            root = doc.get_map(root_name)

            # For simple two-segment paths like ``data/weather`` keep the
            # legacy flat ``data["weather"]`` behaviour so existing widgets
            # continue to work. For longer paths such as ``data/infra/status``
            # merge into the existing top-level subtree so sibling branches
            # like other user ids are preserved.
            if len(segments) == 2:
                key = segments[1]
                current = root.get(key)
                if _json_like_equal(current, projected_value):
                    return
                root.set(txn, key, _clone_json_like(projected_value))
                return

            changed_y_map = _set_nested_y_map_path(root, txn, segments[1:], projected_value)
            if changed_y_map is not None:
                return

            top_key = segments[1]
            current_top = root.get(top_key)
            changed, merged = _merge_nested_path(current_top, segments[2:], projected_value)
            if not changed:
                return
            root.set(txn, top_key, merged)

        if prefer_live_room and mutate_live_room(
            ws_id,
            _mutator,
            root_names=[root_name],
            source="projection_service",
            owner=owner,
            channel=f"projection.{str(target.backend or 'yjs')}.live_room",
            governed=True,
            update_callback=_on_yjs_update,
        ):
            return
        try:
            async with ystore_write_metadata(
                root_names=[root_name],
                source="projection_service",
                owner=owner,
                channel=f"projection.{str(target.backend or 'yjs')}",
                governed=True,
            ):
                async with async_get_ydoc(
                    ws_id,
                    load_mark_roots=[root_name],
                    governed=True,
                    write_update_callback=_on_yjs_update,
                ) as ydoc:
                    with ydoc.begin_transaction() as txn:
                        _mutator(ydoc, txn)
            if detached_compaction_needed:
                outcome = await _compact_projection_amplification_store(
                    ws_id,
                    mode="inline_after_detached_write",
                    delay_sec=0.0,
                )
                if outcome.get("executed"):
                    _log.warning(
                        "YStore compacted inline after detached projection amplification webspace=%s compacted=%s released_replay_bytes=%s malloc_trimmed=%s",
                        ws_id,
                        bool(outcome.get("compacted")),
                        int(outcome.get("released_replay_bytes") or 0),
                        bool(outcome.get("malloc_trimmed")),
                    )
        except Exception:
            _log.warning("failed to apply yjs projection webspace=%s path=%s", ws_id, path, exc_info=True)

    def _apply_kv(self, scope: str, slot: str, value: Any, *, user_id: Optional[str]) -> None:
        # For MVP treat (current_user, "profile.settings") specially and
        # route it through the UserProfileService, so profile can be
        # managed via ctx.current_user.set("profile.settings", ...).
        if scope == "current_user" and slot == "profile.settings":
            svc = UserProfileService(self.ctx)
            if isinstance(value, dict):
                svc.update_profile(value, user_id=user_id)
            else:
                _log.debug("profile.settings expects a mapping, got %r", type(value))
        else:
            _log.debug("kv projection ignored for scope=%s slot=%s (no handler)", scope, slot)


__all__ = ["ProjectionService", "primary_doc_governance_snapshot", "yjs_projection_guard_snapshot"]
