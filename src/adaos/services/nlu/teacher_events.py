from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from collections.abc import Iterable
from typing import Any, Mapping, Optional

from adaos.services.yjs.doc import async_get_ydoc
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings

_MAX_EVENTS = int(os.getenv("ADAOS_NLU_TEACHER_EVENTS_MAX", "24") or "24")
_MAX_LLM_LOGS = int(os.getenv("ADAOS_NLU_TEACHER_LLM_LOGS_MAX", "12") or "12")
_MAX_ITEMS = int(os.getenv("ADAOS_NLU_TEACHER_ITEMS_MAX", "24") or "24")
_MAX_CANDIDATES = int(os.getenv("ADAOS_NLU_TEACHER_CANDIDATES_MAX", "16") or "16")
_MAX_REVISIONS = int(os.getenv("ADAOS_NLU_TEACHER_REVISIONS_MAX", "16") or "16")
_MAX_DATASET = int(os.getenv("ADAOS_NLU_TEACHER_DATASET_MAX", "24") or "24")
_MAX_PLAN = int(os.getenv("ADAOS_NLU_TEACHER_PLAN_MAX", "16") or "16")
_MAX_THREADS = int(os.getenv("ADAOS_NLU_TEACHER_THREADS_MAX", "10") or "10")
_MAX_CANDIDATE_THREADS = int(os.getenv("ADAOS_NLU_TEACHER_CANDIDATE_THREADS_MAX", "12") or "12")
_MAX_THREAD_DETAILS_CHARS = int(os.getenv("ADAOS_NLU_TEACHER_THREAD_DETAILS_MAX_CHARS", "3000") or "3000")
_MAX_PROJECTION_BYTES = int(os.getenv("ADAOS_NLU_TEACHER_PROJECTION_MAX_BYTES", str(512 * 1024)) or str(512 * 1024))
_MAX_PROJECTION_ROW_BYTES = int(os.getenv("ADAOS_NLU_TEACHER_PROJECTION_ROW_MAX_BYTES", str(24 * 1024)) or str(24 * 1024))
_MAX_PROJECTION_STRING_CHARS = int(os.getenv("ADAOS_NLU_TEACHER_PROJECTION_STRING_MAX_CHARS", "4096") or "4096")
_COLLECTION_BYTE_LIMITS = {
    "events": int(os.getenv("ADAOS_NLU_TEACHER_EVENTS_MAX_BYTES", str(80 * 1024)) or str(80 * 1024)),
    "llm_logs": int(os.getenv("ADAOS_NLU_TEACHER_LLM_LOGS_MAX_BYTES", str(80 * 1024)) or str(80 * 1024)),
    "items": int(os.getenv("ADAOS_NLU_TEACHER_ITEMS_MAX_BYTES", str(48 * 1024)) or str(48 * 1024)),
    "candidates": int(os.getenv("ADAOS_NLU_TEACHER_CANDIDATES_MAX_BYTES", str(96 * 1024)) or str(96 * 1024)),
    "revisions": int(os.getenv("ADAOS_NLU_TEACHER_REVISIONS_MAX_BYTES", str(24 * 1024)) or str(24 * 1024)),
    "dataset": int(os.getenv("ADAOS_NLU_TEACHER_DATASET_MAX_BYTES", str(24 * 1024)) or str(24 * 1024)),
    "plan": int(os.getenv("ADAOS_NLU_TEACHER_PLAN_MAX_BYTES", str(16 * 1024)) or str(16 * 1024)),
}
_LEDGER_BACKFILL_SCHEMA = "adaos.nlu_teacher.ledger_backfill.v1"

_COLLECTION_LIMITS = {
    "events": _MAX_EVENTS,
    "llm_logs": _MAX_LLM_LOGS,
    "items": _MAX_ITEMS,
    "candidates": _MAX_CANDIDATES,
    "revisions": _MAX_REVISIONS,
    "dataset": _MAX_DATASET,
    "plan": _MAX_PLAN,
}
_ROW_PRIORITY_KEYS = (
    "id",
    "log_id",
    "ts",
    "created_at",
    "request_id",
    "candidate_id",
    "kind",
    "status",
    "title",
    "subtitle",
    "text",
    "reason",
    "via",
    "target",
    "classification",
    "candidate",
    "action_candidate",
    "regex_rule",
    "training_strategy",
    "diagnostic",
    "error",
    "retry",
    "response",
    "_meta",
)


def _nlu_teacher_events_write_meta():
    return ystore_write_metadata(
        root_names=["data"],
        source="nlu.teacher_events",
        owner="core:nlu.teacher_events",
        channel="core.nlu.teacher_events.async",
    )


def _as_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes, bytearray)) or isinstance(value, Mapping) or not isinstance(value, Iterable):
        return []
    return [dict(x) for x in iter_mappings(value)]


def _row_ts(item: Mapping[str, Any]) -> float:
    try:
        return float(item.get("ts") or item.get("created_at") or 0.0)
    except Exception:
        return 0.0


def _json_size(value: Any) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
    except Exception:
        return len(str(value).encode("utf-8", errors="replace"))


def _truncate_projection_value(
    value: Any,
    *,
    string_chars: int,
    list_items: int,
    mapping_items: int,
    depth: int = 0,
) -> Any:
    if depth >= 8:
        return "<projection-depth-limit>"
    if isinstance(value, str):
        if string_chars > 0 and len(value) > string_chars:
            return value[:string_chars].rstrip() + "..."
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, Mapping):
        ordered_keys = [key for key in _ROW_PRIORITY_KEYS if key in value]
        ordered_keys.extend(key for key in value if key not in ordered_keys)
        if mapping_items > 0:
            ordered_keys = ordered_keys[:mapping_items]
        return {
            str(key): _truncate_projection_value(
                value[key],
                string_chars=string_chars,
                list_items=list_items,
                mapping_items=mapping_items,
                depth=depth + 1,
            )
            for key in ordered_keys
        }
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
        if list_items > 0:
            items = items[-list_items:]
        return [
            _truncate_projection_value(
                item,
                string_chars=string_chars,
                list_items=list_items,
                mapping_items=mapping_items,
                depth=depth + 1,
            )
            for item in items
        ]
    return str(value)


def _compact_projection_row(collection: str, row: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    item = dict(row)
    row_limit = max(0, int(_MAX_PROJECTION_ROW_BYTES))
    if row_limit <= 0 or _json_size(item) <= row_limit:
        return item, False

    for string_chars, list_items, mapping_items in (
        (max(256, int(_MAX_PROJECTION_STRING_CHARS)), 32, 64),
        (1024, 12, 32),
        (256, 4, 16),
    ):
        compacted = _truncate_projection_value(
            item,
            string_chars=string_chars,
            list_items=list_items,
            mapping_items=mapping_items,
        )
        if not isinstance(compacted, dict):
            compacted = {}
        compacted["_projection_truncated"] = True
        compacted["_projection_source"] = "conversation_ledger"
        compacted.setdefault(
            "history_query",
            {
                "request_id": item.get("request_id"),
                "candidate_id": item.get("candidate_id") or item.get("id") if collection == "candidates" else item.get("candidate_id"),
            },
        )
        if _json_size(compacted) <= row_limit:
            return compacted, True

    minimal = {
        key: item.get(key)
        for key in _ROW_PRIORITY_KEYS[:13]
        if item.get(key) not in (None, "", [], {})
    }
    minimal.update(
        {
            "_projection_truncated": True,
            "_projection_source": "conversation_ledger",
            "history_query": {
                "request_id": item.get("request_id"),
                "candidate_id": item.get("candidate_id") or (item.get("id") if collection == "candidates" else None),
            },
        }
    )
    return minimal, True


def _bound_projection_collection(
    teacher: dict[str, Any],
    *,
    collection: str,
    maximum: int,
    byte_limit: int,
) -> tuple[bool, int, int]:
    rows = sorted(_as_list_of_dicts(teacher.get(collection)), key=_row_ts)
    original_total = len(rows)
    if maximum > 0 and len(rows) > maximum:
        rows = rows[-maximum:]
    compacted_rows: list[tuple[dict[str, Any], int]] = []
    compacted_total = 0
    for row in rows:
        compacted, changed = _compact_projection_row(collection, row)
        compacted_total += int(changed)
        compacted_rows.append((compacted, _json_size(compacted)))

    retained_reversed: list[dict[str, Any]] = []
    retained_bytes = 0
    limit = max(0, int(byte_limit))
    for row, row_bytes in reversed(compacted_rows):
        if limit > 0 and retained_reversed and retained_bytes + row_bytes > limit:
            continue
        if limit > 0 and not retained_reversed and row_bytes > limit:
            row, _ = _compact_projection_row(collection, row)
            row_bytes = _json_size(row)
        if limit <= 0 or retained_bytes + row_bytes <= limit or not retained_reversed:
            retained_reversed.append(row)
            retained_bytes += row_bytes
    retained = list(reversed(retained_reversed))
    teacher[collection] = retained
    dropped_total = max(0, original_total - len(retained))
    return bool(dropped_total or compacted_total), dropped_total, retained_bytes


def teacher_projection_limits() -> dict[str, int]:
    return {
        "events": max(0, _MAX_EVENTS),
        "llm_logs": max(0, _MAX_LLM_LOGS),
        "items": max(0, _MAX_ITEMS),
        "candidates": max(0, _MAX_CANDIDATES),
        "revisions": max(0, _MAX_REVISIONS),
        "dataset": max(0, _MAX_DATASET),
        "plan": max(0, _MAX_PLAN),
        "threads_by_request": max(0, _MAX_THREADS),
        "threads_by_candidate": max(0, _MAX_CANDIDATE_THREADS),
        "thread_details_chars": max(0, _MAX_THREAD_DETAILS_CHARS),
        "projection_bytes": max(0, _MAX_PROJECTION_BYTES),
        "projection_row_bytes": max(0, _MAX_PROJECTION_ROW_BYTES),
    }


def _bounded_tail(items: list[dict[str, Any]], maximum: int) -> tuple[list[dict[str, Any]], bool]:
    if maximum <= 0 or len(items) <= maximum:
        return items, False
    return items[-maximum:], True


def bound_teacher_projection(teacher: dict[str, Any]) -> dict[str, Any]:
    """Keep only the operational Teacher window in replicated durable state."""
    limits = teacher_projection_limits()
    previous = coerce_dict(teacher.get("projection_window"))
    truncated = coerce_dict(previous.get("truncated"))

    retained_bytes: dict[str, int] = {}
    dropped_totals: dict[str, int] = {}
    for key, maximum in _COLLECTION_LIMITS.items():
        changed, dropped_total, collection_bytes = _bound_projection_collection(
            teacher,
            collection=key,
            maximum=max(0, int(maximum)),
            byte_limit=max(0, int(_COLLECTION_BYTE_LIMITS.get(key) or 0)),
        )
        truncated[key] = bool(truncated.get(key)) or changed
        dropped_totals[key] = dropped_total
        retained_bytes[key] = collection_bytes

    projection_window = {
        "schema": "adaos.nlu_teacher.projection_window.v1",
        "source_of_truth": "conversation_ledger",
        "history_mode": "on_demand",
        "history_endpoint": "/api/nlu/teacher/{webspace_id}/history",
        "limits": limits,
        "retained": {
            key: len(_as_list_of_dicts(teacher.get(key)))
            for key in _COLLECTION_LIMITS
        },
        "truncated": truncated,
        "dropped": dropped_totals,
        "retained_bytes": retained_bytes,
        "byte_budget": {
            "max_bytes": max(0, int(_MAX_PROJECTION_BYTES)),
            "max_row_bytes": max(0, int(_MAX_PROJECTION_ROW_BYTES)),
            "collection_limits": {
                key: max(0, int(value))
                for key, value in _COLLECTION_BYTE_LIMITS.items()
            },
        },
    }
    ledger_backfill = coerce_dict(previous.get("ledger_backfill"))
    if ledger_backfill:
        projection_window["ledger_backfill"] = ledger_backfill
    teacher["projection_window"] = projection_window
    return teacher


def teacher_ledger_backfill_completed(teacher: Mapping[str, Any]) -> bool:
    marker = coerce_dict(coerce_dict(teacher.get("projection_window")).get("ledger_backfill"))
    return marker.get("schema") == _LEDGER_BACKFILL_SCHEMA and marker.get("completed") is True


def _ledger_record_idempotency_key(record_kind: str, item: Mapping[str, Any]) -> str:
    item_id = str(item.get("id") or item.get("log_id") or "").strip()
    canonical = json.dumps(dict(item), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if record_kind == "llm_log":
        item_id = f"{item_id or 'anonymous'}:{content_hash}"
    elif not item_id:
        item_id = content_hash
    return f"nlu-teacher-ledger-v1:{record_kind}:{item_id}"


def _ledger_event_candidate_id(event: Mapping[str, Any]) -> str | None:
    raw = coerce_dict(event.get("raw"))
    value = raw.get("id") if str(event.get("kind") or "").startswith("candidate.") else event.get("candidate_id")
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def append_llm_log_to_ledger(
    webspace_id: str,
    log: Mapping[str, Any],
    *,
    migration: str | None = None,
) -> dict[str, Any] | None:
    from adaos.services import conversation_links

    log_dict = dict(log)
    request_id = log_dict.get("request_id") if isinstance(log_dict.get("request_id"), str) else None
    meta = {"migration": migration} if migration else {}
    return conversation_links.append_teacher_event_message(
        webspace_id=webspace_id,
        text=f"NLU Teacher LLM log{f' {request_id}' if request_id else ''}",
        request_id=request_id,
        candidate_id=log_dict.get("candidate_id") if isinstance(log_dict.get("candidate_id"), str) else None,
        kind="llm_log",
        payload={"llm_log": log_dict},
        meta=meta,
        idempotency_key=_ledger_record_idempotency_key("llm_log", log_dict),
    )


def backfill_teacher_history_to_ledger(webspace_id: str, teacher: Mapping[str, Any]) -> dict[str, Any]:
    """Ensure legacy durable Teacher rows exist in the canonical ledger."""
    from adaos.services import conversation_links, conversation_store

    previous = coerce_dict(coerce_dict(teacher.get("projection_window")).get("ledger_backfill"))
    was_completed = previous.get("schema") == _LEDGER_BACKFILL_SCHEMA and previous.get("completed") is True

    started = time.perf_counter()
    events = sorted(_as_list_of_dicts(teacher.get("events")), key=_row_ts)
    llm_logs = sorted(_as_list_of_dicts(teacher.get("llm_logs")), key=_row_ts)
    conversation_id = conversation_links.teacher_conversation_id(webspace_id)
    existing_event_ids: set[str] = set()
    existing_llm_log_ids: set[str] = set()
    existing_messages = conversation_store.list_messages(conversation_id, limit=5000, ascending=False)
    for message in existing_messages:
        embedded_event = coerce_dict(message.get("event"))
        event_id = str(embedded_event.get("id") or "").strip()
        if event_id:
            existing_event_ids.add(event_id)
        embedded_log = coerce_dict(message.get("llm_log"))
        log_id = str(embedded_log.get("id") or embedded_log.get("log_id") or "").strip()
        if log_id:
            existing_llm_log_ids.add(log_id)

    already_present = 0
    pending_records: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event.get("id") or "").strip()
        if event_id and event_id in existing_event_ids:
            already_present += 1
            continue
        pending_records.append(
            {
                "text": str(
                    event.get("request_text") or event.get("title") or event.get("kind") or "NLU Teacher event"
                ),
                "request_id": event.get("request_id") if isinstance(event.get("request_id"), str) else None,
                "candidate_id": _ledger_event_candidate_id(event),
                "kind": f"event.{event.get('kind') or 'teacher'}",
                "payload": {"event": event},
                "meta": {"migration": _LEDGER_BACKFILL_SCHEMA, **coerce_dict(event.get("_meta"))},
                "idempotency_key": _ledger_record_idempotency_key("event", event),
            }
        )

    for log in llm_logs:
        log_id = str(log.get("id") or log.get("log_id") or "").strip()
        if log_id and log_id in existing_llm_log_ids:
            already_present += 1
            continue
        request_id = log.get("request_id") if isinstance(log.get("request_id"), str) else None
        pending_records.append(
            {
                "text": f"NLU Teacher LLM log{f' {request_id}' if request_id else ''}",
                "request_id": request_id,
                "candidate_id": log.get("candidate_id") if isinstance(log.get("candidate_id"), str) else None,
                "kind": "llm_log",
                "payload": {"llm_log": log},
                "meta": {"migration": _LEDGER_BACKFILL_SCHEMA},
                "idempotency_key": _ledger_record_idempotency_key("llm_log", log),
            }
        )

    stored = conversation_links.append_teacher_event_messages(webspace_id=webspace_id, records=pending_records)
    if len(stored) != len(pending_records):
        raise RuntimeError(
            f"Teacher ledger batch backfill failed for webspace={webspace_id} "
            f"expected={len(pending_records)} stored={len(stored)}"
        )
    ensured = already_present + len(stored)

    if was_completed:
        return previous
    return {
        "schema": _LEDGER_BACKFILL_SCHEMA,
        "completed": True,
        "completed_at": time.time(),
        "events_total": len(events),
        "llm_logs_total": len(llm_logs),
        "records_ensured": ensured,
        "already_present": already_present,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


def teacher_projection_needs_compaction(teacher: Mapping[str, Any]) -> bool:
    limits = teacher_projection_limits()
    if "events_by_candidate" in teacher:
        return True
    for key in _COLLECTION_LIMITS:
        rows = _as_list_of_dicts(teacher.get(key))
        if limits[key] > 0 and len(rows) > limits[key]:
            return True
        byte_limit = max(0, int(_COLLECTION_BYTE_LIMITS.get(key) or 0))
        if byte_limit > 0 and _json_size(rows) > byte_limit:
            return True
        if any(
            _MAX_PROJECTION_ROW_BYTES > 0 and _json_size(item) > _MAX_PROJECTION_ROW_BYTES
            for item in rows
        ):
            return True
    if limits["threads_by_request"] > 0 and len(_as_list_of_dicts(teacher.get("threads_by_request"))) > limits["threads_by_request"]:
        return True
    if limits["threads_by_candidate"] > 0 and len(_as_list_of_dicts(teacher.get("threads_by_candidate"))) > limits["threads_by_candidate"]:
        return True
    for item in _as_list_of_dicts(teacher.get("threads_by_request")):
        if limits["thread_details_chars"] > 0 and len(str(item.get("details") or "")) > limits["thread_details_chars"]:
            return True
    if limits["projection_bytes"] > 0 and _json_size(teacher) > limits["projection_bytes"]:
        return True
    return not bool(coerce_dict(teacher.get("projection_window")))


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        import json

        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        try:
            return str(value)
        except Exception:
            return ""


def _format_ts(value: Any) -> str:
    try:
        ts = float(value)
    except Exception:
        return ""
    if ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except Exception:
        return ""


def _min_positive_ts(*groups: list[dict[str, Any]]) -> float:
    values: list[float] = []
    for group in groups:
        for item in group:
            try:
                ts = float(item.get("ts") or 0.0)
            except Exception:
                ts = 0.0
            if ts > 0:
                values.append(ts)
    return min(values) if values else 0.0


def _llm_log_has_error(log: Mapping[str, Any]) -> bool:
    status = str(log.get("status") or "").strip().lower()
    return status in {"error", "failed", "timeout"} or log.get("error") not in (None, "", [], {})


def _request_diagnostics(
    *,
    events: list[dict[str, Any]],
    llm_logs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for log in llm_logs:
        if not _llm_log_has_error(log):
            continue
        diagnostics.append(
            {
                "source": "llm_log",
                "id": log.get("id"),
                "status": log.get("status"),
                "error": log.get("error"),
                "diagnostic": coerce_dict(log.get("diagnostic")),
                "retry": coerce_dict(log.get("retry")),
                "ts": log.get("ts"),
            }
        )
    for event in events:
        kind = str(event.get("kind") or "").strip()
        if kind not in {"llm.deferred", "llm.retrying", "llm.skipped"}:
            continue
        raw = coerce_dict(event.get("raw"))
        diagnostic = coerce_dict(raw.get("diagnostic"))
        if not diagnostic and kind != "llm.deferred":
            continue
        diagnostics.append(
            {
                "source": "event",
                "kind": kind,
                "title": event.get("title"),
                "subtitle": event.get("subtitle"),
                "error": raw.get("error"),
                "reason": raw.get("reason"),
                "diagnostic": diagnostic,
                "ts": event.get("ts"),
            }
        )
    return diagnostics


def _candidate_action_summary(candidate: Mapping[str, Any]) -> str:
    action = coerce_dict(candidate.get("action_candidate"))
    rr = coerce_dict(candidate.get("regex_rule"))
    target = candidate.get("target") if isinstance(candidate.get("target"), Mapping) else {}
    parts: list[str] = []
    intent = action.get("intent") or rr.get("intent") or candidate.get("intent")
    if isinstance(intent, str) and intent.strip():
        parts.append(f"intent={intent.strip()}")
    side_effect = action.get("side_effect_class")
    if isinstance(side_effect, str) and side_effect.strip():
        parts.append(f"effect={side_effect.strip()}")
    target_type = target.get("type") if isinstance(target, Mapping) else None
    target_id = target.get("id") if isinstance(target, Mapping) else None
    if isinstance(target_type, str) and isinstance(target_id, str) and target_type.strip() and target_id.strip():
        parts.append(f"target={target_type.strip()}:{target_id.strip()}")
    slots = action.get("slots") if isinstance(action.get("slots"), Mapping) else {}
    if slots:
        parts.append(f"slots={_json_text(slots).strip()}")
    return "; ".join(parts)


def _compact_candidate_for_thread(candidate: Mapping[str, Any]) -> dict[str, Any]:
    rr = coerce_dict(candidate.get("regex_rule"))
    target = candidate.get("target") if isinstance(candidate.get("target"), Mapping) else None
    strategy = candidate.get("training_strategy") if isinstance(candidate.get("training_strategy"), Mapping) else None
    return {
        key: value
        for key, value in {
            "id": candidate.get("id"),
            "kind": candidate.get("kind"),
            "status": candidate.get("status"),
            "request_id": candidate.get("request_id"),
            "target": dict(target) if isinstance(target, Mapping) else None,
            "training_strategy": dict(strategy) if isinstance(strategy, Mapping) else None,
            "regex_rule": {
                key: value
                for key, value in {
                    "intent": rr.get("intent"),
                    "pattern": rr.get("pattern"),
                }.items()
                if value not in (None, "", [], {})
            }
            if rr
            else None,
        }.items()
        if value not in (None, "", [], {})
    }


def _compact_event_raw_for_thread(event: Mapping[str, Any]) -> Any:
    kind = event.get("kind") if isinstance(event.get("kind"), str) else ""
    raw = event.get("raw")
    if not isinstance(raw, Mapping):
        return raw
    if kind == "llm.request":
        return {
            key: value
            for key, value in {
                "log_id": raw.get("log_id"),
                "model": raw.get("model"),
                "max_tokens": raw.get("max_tokens"),
                "timeout_s": raw.get("timeout_s"),
                "audit": raw.get("audit") if isinstance(raw.get("audit"), Mapping) else None,
            }.items()
            if value not in (None, "", [], {})
        }
    if kind == "llm.response":
        suggestion = raw.get("suggestion") if isinstance(raw.get("suggestion"), Mapping) else {}
        return {
            key: value
            for key, value in {
                "log_id": raw.get("log_id"),
                "decision": raw.get("decision") or suggestion.get("decision"),
                "intent": suggestion.get("intent"),
                "training_strategy": suggestion.get("training_strategy"),
                "why_not_regex": suggestion.get("why_not_regex"),
                "need_clarification": suggestion.get("need_clarification"),
                "confidence": suggestion.get("confidence"),
            }.items()
            if value not in (None, "", [], {})
        }
    if kind in {"candidate.proposed", "candidate.applied"}:
        return _compact_candidate_for_thread(raw)
    return raw


def _append_unique(items: list[dict[str, Any]], item: Mapping[str, Any], *, fallback_key: str = "") -> None:
    next_item = dict(item)
    key = str(next_item.get("source_message_id") or next_item.get("id") or fallback_key or "").strip()
    if key:
        for index, existing in enumerate(items):
            existing_key = str(existing.get("source_message_id") or existing.get("id") or "").strip()
            if existing_key == key:
                items[index] = {**existing, **next_item}
                return
    items.append(next_item)


def _teacher_item_from_ledger_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = coerce_dict(message)
    kind = str(payload.get("kind") or "").strip()
    if kind.startswith("event."):
        return None
    if kind not in {"not_obtained", "not_obtained.skipped"}:
        return None
    meta = coerce_dict(payload.get("_meta"))
    classification = coerce_dict(payload.get("classification"))
    source_message_id = str(payload.get("id") or payload.get("message_id") or "").strip()
    try:
        ts = float(payload.get("ts") or payload.get("created_at") or time.time())
    except Exception:
        ts = time.time()
    return {
        "id": str(payload.get("item_id") or f"teach.ledger.{source_message_id or int(ts * 1000)}"),
        "ts": ts,
        "text": str(payload.get("text") or "").strip(),
        "reason": payload.get("reason"),
        "via": payload.get("via"),
        "request_id": payload.get("request_id"),
        "classification": classification,
        "status": "pending" if classification.get("teachable") else "skipped",
        "conversation_ref": coerce_dict(meta.get("conversation_ref")),
        "source_message_id": source_message_id or None,
        "_meta": meta,
    }


def _event_from_ledger_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = coerce_dict(message)
    embedded = coerce_dict(payload.get("event"))
    if embedded:
        event = dict(embedded)
        event.setdefault("id", f"evt.ledger.{payload.get('id') or payload.get('message_id') or int(time.time() * 1000)}")
        event.setdefault("ts", payload.get("ts") or time.time())
        event.setdefault("webspace_id", payload.get("webspace_id"))
        event.setdefault("request_id", payload.get("request_id"))
        event.setdefault("request_text", payload.get("text"))
        meta = coerce_dict(event.get("_meta"))
        meta.setdefault("ledger_message_id", payload.get("id") or payload.get("message_id"))
        event["_meta"] = meta
        return event

    item = _teacher_item_from_ledger_message(message)
    if not item:
        return None
    kind = "not_obtained" if item.get("status") == "pending" else "not_obtained.skipped"
    return {
        "id": f"evt.ledger.{item.get('source_message_id') or item.get('id')}",
        "ts": item.get("ts"),
        "webspace_id": payload.get("webspace_id"),
        "request_id": item.get("request_id"),
        "request_text": item.get("text"),
        "kind": kind,
        "title": "Intent not obtained" if kind == "not_obtained" else "Teacher skipped",
        "subtitle": str(item.get("reason") or "").strip(),
        "raw": dict(item),
        "_meta": {**coerce_dict(item.get("_meta")), "ledger_message_id": item.get("source_message_id")},
    }


def _llm_log_from_ledger_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = coerce_dict(message)
    embedded = coerce_dict(payload.get("llm_log"))
    if not embedded:
        return None
    log = dict(embedded)
    log.setdefault("id", log.get("log_id") or f"llm.ledger.{payload.get('id') or payload.get('message_id')}")
    log.setdefault("ts", payload.get("ts") or time.time())
    log.setdefault("request_id", payload.get("request_id"))
    meta = coerce_dict(log.get("_meta"))
    meta.setdefault("ledger_message_id", payload.get("id") or payload.get("message_id"))
    log["_meta"] = meta
    return log


def _accumulate_event_projection(teacher: dict[str, Any], event: Mapping[str, Any]) -> None:
    kind = str(event.get("kind") or "").strip()
    raw = coerce_dict(event.get("raw"))
    if kind in {"not_obtained", "not_obtained.skipped"} and raw:
        item = dict(raw)
        item.setdefault("status", "pending" if kind == "not_obtained" else "skipped")
        _append_unique(teacher.setdefault("items", []), item, fallback_key=str(event.get("id") or ""))
    if kind in {"candidate.proposed", "candidate.applied"} and raw:
        _append_unique(teacher.setdefault("candidates", []), raw, fallback_key=str(event.get("id") or ""))
    if kind in {"revision.proposed", "revision.suggested", "revision.applied"} and raw:
        _append_unique(teacher.setdefault("revisions", []), raw, fallback_key=str(event.get("id") or ""))
    if kind.startswith("llm.") and raw:
        log = dict(raw)
        if "id" not in log and log.get("log_id"):
            log["id"] = log.get("log_id")
        _append_unique(teacher.setdefault("llm_logs", []), log, fallback_key=str(event.get("id") or ""))


def rebuild_teacher_projection_from_ledger(webspace_id: str, *, limit: int = 1000) -> dict[str, Any]:
    from adaos.services import conversation_links, conversation_store

    conversation_id = conversation_links.teacher_conversation_id(webspace_id)
    messages = conversation_store.list_messages(conversation_id, limit=limit, ascending=True)
    teacher: dict[str, Any] = {
        "items": [],
        "events": [],
        "candidates": [],
        "revisions": [],
        "llm_logs": [],
        "projection_source": {
            "kind": "conversation_ledger",
            "conversation_id": conversation_id,
            "message_count": len(messages),
        },
    }
    for message in messages:
        item = _teacher_item_from_ledger_message(message)
        if item:
            _append_unique(teacher["items"], item, fallback_key=str(message.get("id") or ""))
        event = _event_from_ledger_message(message)
        if event:
            _append_unique(teacher["events"], event, fallback_key=str(message.get("id") or ""))
            _accumulate_event_projection(teacher, event)
        llm_log = _llm_log_from_ledger_message(message)
        if llm_log:
            _append_unique(teacher["llm_logs"], llm_log, fallback_key=str(message.get("id") or ""))

    for key in ("items", "events", "candidates", "revisions", "llm_logs"):
        teacher[key] = sorted(
            [dict(item) for item in iter_mappings(teacher.get(key))],
            key=_row_ts,
        )
    rebuild_teacher_derived_views(teacher)
    return teacher


async def write_teacher_projection_from_ledger(webspace_id: str, *, limit: int = 1000) -> dict[str, Any]:
    teacher = rebuild_teacher_projection_from_ledger(webspace_id, limit=limit)
    async with _nlu_teacher_events_write_meta():
        async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            with ydoc.begin_transaction() as txn:
                data_map.set(txn, "nlu_teacher", teacher)
    try:
        from adaos.services.nlu.teacher_store import save_teacher_state

        save_teacher_state(webspace_id=webspace_id, teacher=teacher)
    except Exception:
        pass
    return teacher


def _thread_log_text(
    *,
    request_id: str,
    request_text: str,
    items: list[dict[str, Any]],
    events: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    revisions: list[dict[str, Any]],
    llm_logs: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append(f"request_id: {request_id}")
    if request_text:
        lines.append(f"text: {request_text}")
    lines.append("")

    if items:
        lines.append("teacher_items:")
        for item in sorted(items, key=lambda x: float(x.get("ts") or 0.0)):
            status = item.get("status") if isinstance(item.get("status"), str) else ""
            reason = item.get("reason") if isinstance(item.get("reason"), str) else ""
            via = item.get("via") if isinstance(item.get("via"), str) else ""
            lines.append(f"- id={item.get('id')} status={status} reason={reason} via={via}".rstrip())
            classification = coerce_dict(item.get("classification"))
            if classification:
                lines.append(f"  classification: {_json_text(classification).strip()}")
        lines.append("")

    if candidates:
        lines.append("candidates:")
        for c in candidates:
            cand = coerce_dict(c.get("candidate"))
            name = cand.get("name") if isinstance(cand.get("name"), str) else ""
            kind = c.get("kind") if isinstance(c.get("kind"), str) else ""
            status = c.get("status") if isinstance(c.get("status"), str) else ""
            lines.append(f"- id={c.get('id')} kind={kind} status={status} name={name}")
            desc = cand.get("description") if isinstance(cand.get("description"), str) else ""
            if desc:
                lines.append(f"  description: {desc}")
            if kind == "regex_rule":
                rr = coerce_dict(c.get("regex_rule"))
                intent = rr.get("intent") if isinstance(rr.get("intent"), str) else ""
                pattern = rr.get("pattern") if isinstance(rr.get("pattern"), str) else ""
                if intent:
                    lines.append(f"  regex.intent: {intent}")
                if pattern:
                    lines.append(f"  regex.pattern: {pattern}")
            action_summary = _candidate_action_summary(c)
            if action_summary:
                lines.append(f"  action: {action_summary}")
            validation = coerce_dict(c.get("validation"))
            if validation:
                lines.append(f"  validation: {_json_text(validation).strip()}")
        lines.append("")

    if revisions:
        lines.append("revisions:")
        for r in revisions:
            status = r.get("status") if isinstance(r.get("status"), str) else ""
            proposal = coerce_dict(r.get("proposal"))
            intent = proposal.get("intent") if isinstance(proposal.get("intent"), str) else ""
            lines.append(f"- id={r.get('id')} status={status} intent={intent}")
            examples = proposal.get("examples")
            if isinstance(examples, list) and examples:
                ex = [x for x in examples if isinstance(x, str)]
                if ex:
                    lines.append("  examples:")
                    for x in ex[:25]:
                        lines.append(f"  - {x}")
        lines.append("")

    # Events are the primary canonical chronological record.
    if events:
        lines.append("events:")
        for e in sorted(events, key=lambda x: float(x.get("ts") or 0.0)):
            ts = e.get("ts")
            kind = e.get("kind") if isinstance(e.get("kind"), str) else ""
            title = e.get("title") if isinstance(e.get("title"), str) else ""
            subtitle = e.get("subtitle") if isinstance(e.get("subtitle"), str) else ""
            lines.append(f"- ts={ts} kind={kind} title={title} subtitle={subtitle}".rstrip())
            raw = _compact_event_raw_for_thread(e)
            raw_txt = _json_text(raw).strip()
            if raw_txt:
                # Keep the log readable; raw can be large and threads duplicate it.
                raw_lines = raw_txt.splitlines()
                for ln in raw_lines[:40]:
                    lines.append(f"  {ln}")
                if len(raw_lines) > 40:
                    lines.append("  ... (truncated)")
        lines.append("")

    if llm_logs:
        lines.append("llm_logs:")
        for log in sorted(llm_logs, key=lambda x: float(x.get("ts") or 0.0)):
            status = log.get("status") if isinstance(log.get("status"), str) else ""
            model = log.get("model") if isinstance(log.get("model"), str) else ""
            lines.append(f"- id={log.get('id')} status={status} model={model}".rstrip())
            if log.get("error") not in (None, "", [], {}):
                lines.append(f"  error: {log.get('error')}")
            diagnostic = coerce_dict(log.get("diagnostic"))
            if diagnostic:
                lines.append(f"  diagnostic: {_json_text(diagnostic).strip()}")
            resp = coerce_dict(log.get("response"))
            raw_txt = resp.get("raw") if isinstance(resp.get("raw"), str) else ""
            if raw_txt:
                for ln in raw_txt.splitlines()[:60]:
                    lines.append(f"  {ln}")
                if len(raw_txt.splitlines()) > 60:
                    lines.append("  ... (truncated)")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def rebuild_threads(teacher: dict[str, Any], *, bounded: bool = True) -> dict[str, Any]:
    """
    Builds derived thread views for schema-driven UI:

    - threads_by_request: 1 item per request_id
    - threads_by_candidate: 1 item per (candidate_id) with header=LLM candidate name
    """
    events = _as_list_of_dicts(teacher.get("events"))
    items = _as_list_of_dicts(teacher.get("items"))
    candidates = _as_list_of_dicts(teacher.get("candidates"))
    revisions = _as_list_of_dicts(teacher.get("revisions"))
    llm_logs = _as_list_of_dicts(teacher.get("llm_logs"))

    request_ids: set[str] = set()
    for item in items:
        rid = item.get("request_id")
        if isinstance(rid, str) and rid:
            request_ids.add(rid)
    for e in events:
        rid = e.get("request_id")
        if isinstance(rid, str) and rid:
            request_ids.add(rid)
    for c in candidates:
        rid = c.get("request_id")
        if isinstance(rid, str) and rid:
            request_ids.add(rid)
    for r in revisions:
        rid = r.get("request_id")
        if isinstance(rid, str) and rid:
            request_ids.add(rid)
    for l in llm_logs:
        rid = l.get("request_id")
        if isinstance(rid, str) and rid:
            request_ids.add(rid)

    def _request_text_for(rid: str) -> str:
        for item in items:
            if item.get("request_id") == rid and isinstance(item.get("text"), str) and item.get("text"):
                return item.get("text") or ""
        for e in events:
            if e.get("request_id") == rid and isinstance(e.get("request_text"), str) and e.get("request_text"):
                return e.get("request_text") or ""
        for c in candidates:
            if c.get("request_id") == rid and isinstance(c.get("text"), str) and c.get("text"):
                return c.get("text") or ""
        for r in revisions:
            if r.get("request_id") == rid and isinstance(r.get("text"), str) and r.get("text"):
                return r.get("text") or ""
        return ""

    threads_by_request: list[dict[str, Any]] = []
    threads_by_candidate: list[dict[str, Any]] = []

    for rid in sorted(request_ids):
        req_text = _request_text_for(rid)
        req_items = [item for item in items if item.get("request_id") == rid]
        ev = [e for e in events if e.get("request_id") == rid]
        cand = [c for c in candidates if c.get("request_id") == rid]
        rev = [r for r in revisions if r.get("request_id") == rid]
        llm = [l for l in llm_logs if l.get("request_id") == rid]
        request_ts = _min_positive_ts(req_items, ev, cand, rev, llm)
        request_time = _format_ts(request_ts)
        diagnostics = _request_diagnostics(events=ev, llm_logs=llm)

        # Default "Apply" action for the request thread: apply the first pending candidate.
        pending_candidate_id = ""
        for c in cand:
            if c.get("status") == "pending" and isinstance(c.get("id"), str):
                pending_candidate_id = c.get("id") or ""
                break

        details = _thread_log_text(
            request_id=rid,
            request_text=req_text,
            items=req_items,
            events=ev,
            candidates=cand,
            revisions=rev,
            llm_logs=llm,
        )

        subtitle_parts: list[str] = []
        if request_time:
            subtitle_parts.append(request_time)
        if cand:
            subtitle_parts.append(f"candidates={len(cand)}")
        if rev:
            subtitle_parts.append(f"revisions={len(rev)}")
        if diagnostics:
            subtitle_parts.append(f"errors={len(diagnostics)}")
        subtitle = ", ".join(subtitle_parts)

        details_truncated = False
        if bounded and _MAX_THREAD_DETAILS_CHARS > 0 and len(details) > _MAX_THREAD_DETAILS_CHARS:
            suffix = "\n... (load full history from ledger)\n"
            prefix_limit = max(0, _MAX_THREAD_DETAILS_CHARS - len(suffix))
            details = details[:prefix_limit].rstrip() + suffix
            details = details[:_MAX_THREAD_DETAILS_CHARS]
            details_truncated = True

        threads_by_request.append(
            {
                "id": f"req.{rid}",
                "request_id": rid,
                "title": req_text or rid,
                "subtitle": subtitle,
                "created_at": request_ts or None,
                "created_at_label": request_time,
                "details": details,
                "details_truncated": details_truncated,
                "history_query": {"request_id": rid},
                "diagnostics": _json_text(diagnostics) if diagnostics else "",
                "has_error_details": bool(diagnostics),
                "candidate_id": pending_candidate_id,
            }
        )

        for c in cand:
            cand_obj = coerce_dict(c.get("candidate"))
            name = cand_obj.get("name") if isinstance(cand_obj.get("name"), str) else ""
            description = cand_obj.get("description") if isinstance(cand_obj.get("description"), str) else ""
            action_summary = _candidate_action_summary(c)
            if not description and action_summary:
                description = action_summary
            target_obj = c.get("target") if isinstance(c.get("target"), Mapping) else None
            target_type = target_obj.get("type") if isinstance(target_obj, Mapping) else None
            target_id = target_obj.get("id") if isinstance(target_obj, Mapping) else None
            if not isinstance(target_type, str) or not target_type.strip():
                target_type = ""
            if not isinstance(target_id, str) or not target_id.strip():
                target_id = ""
            target_label = f"{target_type}:{target_id}".strip(":") if target_type and target_id else ""

            cand_kind = c.get("kind") if isinstance(c.get("kind"), str) else ""
            candidate_meta = cand_kind
            if target_label:
                candidate_meta = f"{cand_kind} → {target_label}".strip()

            if action_summary:
                candidate_meta = f"{candidate_meta}; {action_summary}".strip("; ")

            cid = c.get("id") if isinstance(c.get("id"), str) else ""
            if not cid:
                continue
            candidate_details = details
            if bounded:
                candidate_details = _json_text(
                    {
                        "request_id": rid,
                        "candidate_id": cid,
                        "kind": cand_kind,
                        "name": name,
                        "description": description,
                        "status": c.get("status"),
                        "target": dict(target_obj) if isinstance(target_obj, Mapping) else None,
                        "action": action_summary,
                        "history": "load from conversation ledger",
                    }
                )
            threads_by_candidate.append(
                {
                    "id": cid,
                    "candidate_id": cid,
                    "candidate_kind": cand_kind,
                    "candidate_name": name,
                    "candidate_description": description,
                    "candidate_action_summary": action_summary,
                    "candidate_target": dict(target_obj) if isinstance(target_obj, Mapping) else None,
                    "candidate_target_type": target_type,
                    "candidate_target_id": target_id,
                    "candidate_target_label": target_label,
                    "candidate_meta": candidate_meta,
                    "candidate_origin_scenario_id": c.get("origin_scenario_id")
                    if isinstance(c.get("origin_scenario_id"), str)
                    else "",
                    "candidate_status": c.get("status") if isinstance(c.get("status"), str) else "",
                    "request_id": rid,
                    "title": name or cid,
                    "subtitle": req_text or rid,
                    "details": candidate_details,
                    "details_truncated": bounded,
                    "history_query": {"request_id": rid, "candidate_id": cid},
                }
            )

    threads_by_request.sort(key=_row_ts)
    if bounded and _MAX_THREADS > 0 and len(threads_by_request) > _MAX_THREADS:
        threads_by_request = threads_by_request[-_MAX_THREADS:]
    request_created_at = {str(item.get("request_id") or ""): _row_ts(item) for item in threads_by_request}
    threads_by_candidate.sort(key=lambda item: request_created_at.get(str(item.get("request_id") or ""), 0.0))
    if bounded and _MAX_CANDIDATE_THREADS > 0 and len(threads_by_candidate) > _MAX_CANDIDATE_THREADS:
        threads_by_candidate = threads_by_candidate[-_MAX_CANDIDATE_THREADS:]

    teacher["threads_by_request"] = threads_by_request
    teacher["threads_by_candidate"] = threads_by_candidate
    return teacher


def rebuild_workbench_signals(teacher: dict[str, Any]) -> dict[str, Any]:
    events = _as_list_of_dicts(teacher.get("events"))
    candidates = _as_list_of_dicts(teacher.get("candidates"))
    items = _as_list_of_dicts(teacher.get("items"))
    llm_logs = _as_list_of_dicts(teacher.get("llm_logs"))

    status_counts: dict[str, int] = {}
    for candidate in candidates:
        status = str(candidate.get("status") or "unknown").strip() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
    item_status_counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown").strip() or "unknown"
        item_status_counts[status] = item_status_counts.get(status, 0) + 1

    pending_candidates = [item for item in candidates if item.get("status") == "pending"]
    quarantined_candidates = [item for item in candidates if item.get("status") == "quarantined"]
    acquired_events = [item for item in events if item.get("kind") == "understanding.acquired"]
    skipped_events = [item for item in events if item.get("kind") == "not_obtained.skipped"]
    failed_logs = [
        item
        for item in llm_logs
        if str(item.get("status") or "").lower() in {"error", "failed", "timeout"}
        or item.get("error") not in (None, "", [], {})
    ]

    signals: list[dict[str, Any]] = [
        {
            "id": "teacher.queue",
            "title": "Teacher queue",
            "subtitle": f"pending candidates={len(pending_candidates)}, skipped requests={item_status_counts.get('skipped', 0)}",
            "status": "attention" if pending_candidates else "ok",
            "severity": "info",
            "details": _json_text({"candidate_status_counts": status_counts, "request_status_counts": item_status_counts}),
        },
        {
            "id": "teacher.acquired",
            "title": "Acquired understandings",
            "subtitle": str(len(acquired_events)),
            "status": "ok",
            "severity": "info",
            "details": _json_text(acquired_events[-10:]),
        },
    ]
    if quarantined_candidates:
        signals.append(
            {
                "id": "teacher.quarantine",
                "title": "Quarantined candidates",
                "subtitle": str(len(quarantined_candidates)),
                "status": "attention",
                "severity": "warning",
                "details": _json_text(quarantined_candidates[-10:]),
            }
        )
    if skipped_events:
        signals.append(
            {
                "id": "teacher.skipped",
                "title": "Teacher skipped requests",
                "subtitle": str(len(skipped_events)),
                "status": "info",
                "severity": "info",
                "details": _json_text(skipped_events[-10:]),
            }
        )
    if failed_logs:
        signals.append(
            {
                "id": "teacher.llm_errors",
                "title": "LLM Teacher errors",
                "subtitle": str(len(failed_logs)),
                "status": "attention",
                "severity": "error",
                "details": _json_text(failed_logs[-10:]),
            }
        )
    if events:
        def _ts(item: Mapping[str, Any]) -> float:
            try:
                return float(item.get("ts") or 0.0)
            except Exception:
                return 0.0

        latest = sorted(events, key=_ts)[-1]
        signals.append(
            {
                "id": "teacher.latest_event",
                "title": "Latest Teacher event",
                "subtitle": f"{latest.get('kind') or ''} {latest.get('subtitle') or ''}".strip(),
                "status": "info",
                "severity": "info",
                "details": _json_text(latest),
            }
        )

    teacher["workbench_summary"] = {
        "candidate_status_counts": status_counts,
        "request_status_counts": item_status_counts,
        "event_count": len(events),
        "candidate_count": len(candidates),
        "llm_log_count": len(llm_logs),
        "pending_candidate_count": len(pending_candidates),
        "quarantined_candidate_count": len(quarantined_candidates),
        "understanding_acquired_count": len(acquired_events),
    }
    teacher["workbench_signals"] = signals
    return teacher


def _refresh_projection_window_retained(teacher: dict[str, Any]) -> dict[str, Any]:
    window = coerce_dict(teacher.get("projection_window"))
    window["retained"] = {
        **coerce_dict(window.get("retained")),
        **{
            key: len(_as_list_of_dicts(teacher.get(key)))
            for key in _COLLECTION_LIMITS
        },
        "threads_by_request": len(_as_list_of_dicts(teacher.get("threads_by_request"))),
        "threads_by_candidate": len(_as_list_of_dicts(teacher.get("threads_by_candidate"))),
    }
    teacher["projection_window"] = window
    return window


def _enforce_teacher_projection_total_budget(teacher: dict[str, Any]) -> None:
    maximum = max(0, int(_MAX_PROJECTION_BYTES))
    if maximum <= 0:
        return

    # Collection limits are the normal path. This second boundary prevents one
    # unusually rich row, or a future producer-added collection, from turning
    # the replicated projection into a channel-sized payload.
    attempts = 0
    while _json_size(teacher) > maximum and attempts < 12:
        attempts += 1
        candidates: list[tuple[int, str, list[dict[str, Any]]]] = []
        for key in _COLLECTION_LIMITS:
            rows = _as_list_of_dicts(teacher.get(key))
            if rows:
                candidates.append((_json_size(rows), key, rows))
        if not candidates:
            break
        _size, key, rows = max(candidates)
        drop_total = max(1, len(rows) // 3)
        teacher[key] = rows[drop_total:]
        window = coerce_dict(teacher.get("projection_window"))
        truncated = coerce_dict(window.get("truncated"))
        dropped = coerce_dict(window.get("dropped"))
        truncated[key] = True
        dropped[key] = int(dropped.get(key) or 0) + drop_total
        window["truncated"] = truncated
        window["dropped"] = dropped
        teacher["projection_window"] = window
        rebuild_threads(teacher, bounded=True)
        rebuild_workbench_signals(teacher)
        _refresh_projection_window_retained(teacher)

    # Derived rows can still contain formatted diagnostics. Keep their newest
    # identity and route-to-ledger metadata if the total projection is tight.
    for key in ("workbench_signals", "threads_by_request", "threads_by_candidate"):
        if _json_size(teacher) <= maximum:
            break
        rows = _as_list_of_dicts(teacher.get(key))
        compacted = [_compact_projection_row(key, row)[0] for row in rows]
        while compacted and _json_size(teacher) > maximum:
            compacted = compacted[1:]
            teacher[key] = compacted

    # Unknown producer fields are not allowed to bypass the projection budget.
    # Their authoritative state must live in the ledger or a skill-local store.
    protected = {
        *list(_COLLECTION_LIMITS),
        "projection_window",
        "threads_by_request",
        "threads_by_candidate",
        "workbench_signals",
    }
    if _json_size(teacher) > maximum:
        auxiliary = sorted(
            (str(item) for item in teacher if str(item) not in protected),
            key=lambda item: _json_size(teacher.get(item)),
            reverse=True,
        )
        for key in auxiliary:
            if _json_size(teacher) <= maximum:
                break
            value = teacher.get(key)
            compacted = _truncate_projection_value(
                value,
                string_chars=512,
                list_items=8,
                mapping_items=24,
            )
            if _json_size(compacted) >= _json_size(value):
                compacted = {
                    "_projection_truncated": True,
                    "_projection_source": "skill_local_or_ledger",
                }
            teacher[key] = compacted
            window = coerce_dict(teacher.get("projection_window"))
            truncated = coerce_dict(window.get("truncated"))
            truncated[key] = True
            window["truncated"] = truncated
            teacher["projection_window"] = window

    if _json_size(teacher) > maximum:
        for key in ("workbench_signals", "threads_by_candidate", "threads_by_request"):
            if _json_size(teacher) <= maximum:
                break
            teacher[key] = []

    window = _refresh_projection_window_retained(teacher)
    byte_budget = coerce_dict(window.get("byte_budget"))
    estimated_bytes = _json_size(teacher)
    byte_budget.update(
        {
            "max_bytes": maximum,
            "estimated_bytes": estimated_bytes,
            "over_budget": estimated_bytes > maximum,
            "enforcement_passes": attempts,
        }
    )
    window["byte_budget"] = byte_budget
    teacher["projection_window"] = window


def rebuild_teacher_derived_views(teacher: dict[str, Any], *, bounded: bool = True) -> dict[str, Any]:
    """Rebuild bounded UI views without persisting duplicate event history."""
    teacher.pop("events_by_candidate", None)
    if bounded:
        bound_teacher_projection(teacher)
    rebuild_threads(teacher, bounded=bounded)
    rebuild_workbench_signals(teacher)
    if bounded:
        _refresh_projection_window_retained(teacher)
        _enforce_teacher_projection_total_budget(teacher)
    return teacher


def read_teacher_history_page(
    webspace_id: str,
    *,
    request_id: str | None = None,
    candidate_id: str | None = None,
    before_cursor: Any = None,
    limit: int = 32,
) -> dict[str, Any]:
    """Reconstruct a paged Teacher view from the canonical conversation ledger."""
    from adaos.services import conversation_links, conversation_store

    clean_request_id = str(request_id or "").strip() or None
    clean_candidate_id = str(candidate_id or "").strip() or None
    conversation_id = conversation_links.teacher_conversation_id(webspace_id)
    thread_id = (
        conversation_links.teacher_thread_id(webspace_id=webspace_id, request_id=clean_request_id)
        if clean_request_id
        else None
    )
    page = conversation_store.list_projection(
        conversation_id,
        thread_id=thread_id,
        before_cursor=before_cursor,
        limit=max(1, min(int(limit or 32), 64)),
        max_items=64,
    )
    messages = [dict(item) for item in iter_mappings(page.get("messages"))]
    teacher: dict[str, Any] = {
        "items": [],
        "events": [],
        "candidates": [],
        "revisions": [],
        "llm_logs": [],
    }
    for message in messages:
        item = _teacher_item_from_ledger_message(message)
        if item:
            _append_unique(teacher["items"], item, fallback_key=str(message.get("id") or ""))
        event = _event_from_ledger_message(message)
        if event:
            _append_unique(teacher["events"], event, fallback_key=str(message.get("id") or ""))
            _accumulate_event_projection(teacher, event)
        llm_log = _llm_log_from_ledger_message(message)
        if llm_log:
            _append_unique(teacher["llm_logs"], llm_log, fallback_key=str(message.get("id") or ""))

    def _request_matches(item: Mapping[str, Any]) -> bool:
        return not clean_request_id or str(item.get("request_id") or "").strip() == clean_request_id

    def _candidate_matches(item: Mapping[str, Any]) -> bool:
        if not clean_candidate_id:
            return True
        direct = str(item.get("candidate_id") or item.get("id") or "").strip()
        raw = coerce_dict(item.get("raw"))
        raw_id = str(raw.get("candidate_id") or raw.get("id") or "").strip()
        return clean_candidate_id in {direct, raw_id}

    for key in ("items", "events", "candidates", "revisions", "llm_logs"):
        rows = [item for item in _as_list_of_dicts(teacher.get(key)) if _request_matches(item)]
        if key in {"events", "candidates", "revisions"} and clean_candidate_id:
            rows = [item for item in rows if _candidate_matches(item)]
        teacher[key] = sorted(
            rows,
            key=_row_ts,
        )
    rebuild_teacher_derived_views(teacher, bounded=False)
    return {
        "ok": True,
        "schema": "adaos.nlu_teacher.ledger_history.v1",
        "webspace_id": webspace_id,
        "request_id": clean_request_id,
        "candidate_id": clean_candidate_id,
        "source": "conversation_ledger",
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "events": teacher["events"],
        "llm_logs": teacher["llm_logs"],
        "items": teacher["items"],
        "candidates": teacher["candidates"],
        "revisions": teacher["revisions"],
        "threads_by_request": teacher.get("threads_by_request") or [],
        "threads_by_candidate": teacher.get("threads_by_candidate") or [],
        "messages": messages,
        "has_more_before": bool(page.get("has_more_before")),
        "before_cursor": str(page.get("before_cursor") or ""),
        "total_message_count": int(page.get("total_message_count") or 0),
    }


def make_event(
    *,
    webspace_id: str,
    request_id: Optional[str],
    request_text: str,
    kind: str,
    title: str,
    subtitle: str = "",
    raw: Optional[Mapping[str, Any]] = None,
    meta: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "id": f"evt.{int(time.time() * 1000)}",
        "ts": time.time(),
        "webspace_id": webspace_id,
        "request_id": request_id,
        "request_text": request_text,
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "raw": coerce_dict(raw) if raw is not None else None,
        "_meta": coerce_dict(meta),
    }


async def append_event(webspace_id: str, event: Mapping[str, Any]) -> None:
    next_teacher: dict[str, Any] | None = None
    async with _nlu_teacher_events_write_meta():
        async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            current = data_map.get("nlu_teacher")
            teacher: dict[str, Any] = coerce_dict(current)

            events = teacher.get("events")
            if isinstance(events, (str, bytes, bytearray)) or isinstance(events, Mapping) or not isinstance(events, Iterable):
                events = []
            events = [dict(x) for x in iter_mappings(events)]
            events.append(dict(event) if isinstance(event, Mapping) else {})
            if _MAX_EVENTS > 0 and len(events) > _MAX_EVENTS:
                events = events[-_MAX_EVENTS:]
            teacher["events"] = events

            rebuild_teacher_derived_views(teacher)

            with ydoc.begin_transaction() as txn:
                data_map.set(txn, "nlu_teacher", teacher)
            next_teacher = dict(teacher)

    if next_teacher is not None:
        try:
            from adaos.services.nlu.teacher_store import save_teacher_state

            save_teacher_state(webspace_id=webspace_id, teacher=next_teacher)
        except Exception:
            pass
    try:
        from adaos.services import conversation_links

        event_dict = dict(event)
        raw = coerce_dict(event_dict.get("raw"))
        candidate_id = raw.get("id") if str(event_dict.get("kind") or "").startswith("candidate.") else event_dict.get("candidate_id")
        conversation_links.append_teacher_event_message(
            webspace_id=webspace_id,
            text=str(event_dict.get("request_text") or event_dict.get("title") or event_dict.get("kind") or "NLU Teacher event"),
            request_id=event_dict.get("request_id") if isinstance(event_dict.get("request_id"), str) else None,
            candidate_id=candidate_id if isinstance(candidate_id, str) else None,
            kind=f"event.{event_dict.get('kind') or 'teacher'}",
            payload={"event": event_dict},
            meta=coerce_dict(event_dict.get("_meta")),
            idempotency_key=_ledger_record_idempotency_key("event", event_dict),
        )
    except Exception:
        pass
