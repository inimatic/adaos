from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import anyio

from adaos.services.agent_context import get_ctx
from adaos.services.yjs.doc import async_read_ydoc
from adaos.services.yjs.webspace import coerce_webspace_id, default_webspace_id

_SAFE_LOG_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_EVENTS_PER_BATCH = 50
_MAX_DETAILS_BYTES = 16_000
_MAX_MESSAGE_CHARS = 1_500
_FALLBACK_SKILL = "__ui_runtime__"


async def ingest_ui_runtime_diagnostics(
    payload: Mapping[str, Any] | None,
    *,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    """Persist browser-side UI diagnostics into skill-scoped runtime logs."""

    body = dict(payload or {})
    events = body.get("events")
    if not isinstance(events, list):
        events = []
    target_webspace_id = coerce_webspace_id(
        webspace_id or body.get("webspace_id") or body.get("webspaceId"),
        fallback=default_webspace_id(),
    )
    accepted: list[dict[str, Any]] = []
    records: list[tuple[str, dict[str, Any]]] = []

    for raw in events[:_MAX_EVENTS_PER_BATCH]:
        if not isinstance(raw, Mapping):
            continue
        normalized = await _normalize_event(raw, webspace_id=target_webspace_id)
        if not normalized:
            continue
        if not _should_persist_event(normalized):
            continue
        records.append((str(normalized["skill_id"]), normalized))
        accepted.append(
            {
                "skill_id": normalized["skill_id"],
                "level": normalized.get("level"),
                "source": normalized.get("source"),
                "code": normalized.get("code"),
                "log_file": _log_path_for_skill(str(normalized["skill_id"])).name,
            }
        )

    if records:
        await anyio.to_thread.run_sync(_append_records, records)
    return {
        "ok": True,
        "accepted": len(records),
        "webspace_id": target_webspace_id,
        "events": accepted,
    }


def _should_persist_event(event: Mapping[str, Any]) -> bool:
    source = str(event.get("source") or "").strip()
    code = str(event.get("code") or "").strip()
    level = str(event.get("level") or "").strip().upper()
    if source == "ui.runtime_debug" and code == "webio.event" and level == "DEBUG":
        return False
    return True


async def _normalize_event(raw: Mapping[str, Any], *, webspace_id: str) -> dict[str, Any] | None:
    message = _compact_string(raw.get("message"), max_chars=_MAX_MESSAGE_CHARS)
    if not message:
        return None
    details = _sanitize_details(raw.get("details"))
    browser_identity = _extract_browser_identity(details)
    runtime_debug = _coerce_dict(details.get("runtime_debug"))
    runtime_debug_details = _coerce_dict(runtime_debug.get("details"))
    runtime_debug_cursor = _coerce_dict(details.get("runtime_debug_cursor"))
    level = _normalize_level(raw.get("level"))
    source = _compact_string(raw.get("source"), max_chars=120) or "ui.runtime"
    code = _compact_string(raw.get("code"), max_chars=160) or None
    node_id = _first_string(raw.get("nodeId"), raw.get("node_id"), _detail_value(details, "nodeId"), _detail_value(details, "node_id"))
    modal_id = _first_string(
        raw.get("modalId"),
        raw.get("modal_id"),
        raw.get("baseId"),
        raw.get("requestedId"),
        _detail_value(details, "modalId"),
        _detail_value(details, "modal_id"),
        _detail_value(details, "baseId"),
        _detail_value(details, "requestedId"),
    )
    current_scenario = _first_string(raw.get("currentScenario"), raw.get("current_scenario"))
    skill_id = _first_string(raw.get("skillId"), raw.get("skill_id"), _detail_value(details, "skillId"), _detail_value(details, "skill_id"))
    if not skill_id:
        skill_id = await _resolve_skill_from_yjs(
            webspace_id=webspace_id,
            raw=raw,
            details=details,
            node_id=node_id,
            modal_id=modal_id,
        )
    skill_id = _safe_log_token(skill_id or _FALLBACK_SKILL)
    return {
        "v": 1,
        "time": _compact_string(raw.get("ts"), max_chars=80) or datetime.now(timezone.utc).isoformat(),
        "ts": time.time(),
        "level": level,
        "logger": "ui.runtime",
        "msg": message,
        "source": source,
        "code": code,
        "skill_id": skill_id,
        "webspace_id": webspace_id,
        "current_scenario": current_scenario,
        "browser_device_id": _first_string(
            browser_identity.get("device_id"),
            runtime_debug_details.get("device_id"),
            runtime_debug_cursor.get("browser_identity", {}).get("device_id")
            if isinstance(runtime_debug_cursor.get("browser_identity"), Mapping)
            else None,
        ),
        "browser_family": _first_string(
            browser_identity.get("browser_family"),
            runtime_debug_details.get("browser_family"),
        ),
        "browser_os_name": _first_string(
            browser_identity.get("os_name"),
            runtime_debug_details.get("os_name"),
        ),
        "browser_form_factor": _first_string(
            browser_identity.get("form_factor"),
            runtime_debug_details.get("form_factor"),
        ),
        "browser_session_id": _first_string(
            runtime_debug.get("session_id"),
            runtime_debug_cursor.get("session_id"),
        ),
        "browser_tab_id": _first_string(
            runtime_debug.get("tab_id"),
            runtime_debug_cursor.get("tab_id"),
        ),
        "client_yws_attempt_id": _first_string(
            runtime_debug_details.get("client_attempt_id"),
            _coerce_dict(runtime_debug_cursor.get("yjs_provider")).get("client_attempt_id"),
            _coerce_dict(runtime_debug_cursor.get("yjs_close")).get("client_attempt_id"),
        ),
        "node_id": node_id,
        "modal_id": modal_id,
        "widget_id": _first_string(raw.get("widgetId"), raw.get("widget_id"), _detail_value(details, "widgetId"), _detail_value(details, "widget_id")),
        "details": details,
    }


async def _resolve_skill_from_yjs(
    *,
    webspace_id: str,
    raw: Mapping[str, Any],
    details: Mapping[str, Any],
    node_id: str | None,
    modal_id: str | None,
) -> str | None:
    modal_ids = _candidate_modal_ids(raw=raw, details=details, node_id=node_id, modal_id=modal_id)
    if not modal_ids:
        return None
    try:
        async with async_read_ydoc(webspace_id, prefer_live_room=False) as ydoc:
            ui_map = ydoc.get_map("ui")
            application = _coerce_dict(ui_map.get("application"))
            modals = _coerce_dict(application.get("modals"))
            for candidate in modal_ids:
                modal = _coerce_dict(modals.get(candidate))
                skill = _origin_skill_from_modal(modal)
                if skill:
                    return skill
    except Exception:
        return None
    return None


def _candidate_modal_ids(
    *,
    raw: Mapping[str, Any],
    details: Mapping[str, Any],
    node_id: str | None,
    modal_id: str | None,
) -> list[str]:
    candidates: list[str] = []
    for value in (
        modal_id,
        raw.get("requestedId"),
        raw.get("requested_id"),
        raw.get("baseId"),
        raw.get("base_id"),
        details.get("requestedId"),
        details.get("baseId"),
        details.get("modalId"),
    ):
        _append_candidate(candidates, value)
    for value in _list_values(raw.get("lookupIds")) + _list_values(details.get("lookupIds")):
        _append_candidate(candidates, value)
    if node_id:
        for value in list(candidates):
            if value.startswith("node:"):
                continue
            _append_candidate(candidates, f"node:{node_id}:{value}")
    return candidates[:40]


def _origin_skill_from_modal(modal: Mapping[str, Any]) -> str | None:
    for key in ("originSkill", "origin_skill", "skillId", "skill_id", "skill"):
        value = _compact_string(modal.get(key), max_chars=160)
        if value:
            return value
    origin = modal.get("origin")
    if isinstance(origin, Mapping):
        for key in ("skill", "skillId", "skill_id"):
            value = _compact_string(origin.get(key), max_chars=160)
            if value:
                return value
    else:
        origin_text = _compact_string(origin, max_chars=200)
        if origin_text.startswith("skill:"):
            return origin_text[len("skill:") :].strip()
    meta = modal.get("_adaos")
    if isinstance(meta, Mapping):
        for key in ("originSkill", "skillId", "skill"):
            value = _compact_string(meta.get(key), max_chars=160)
            if value:
                return value
    return None


def _append_records(records: list[tuple[str, dict[str, Any]]]) -> None:
    grouped: dict[Path, list[dict[str, Any]]] = {}
    for skill_id, record in records:
        grouped.setdefault(_log_path_for_skill(skill_id), []).append(record)
    for path, items in grouped.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def _log_path_for_skill(skill_id: str) -> Path:
    ctx = get_ctx()
    paths = ctx.paths
    fn = getattr(paths, "skill_ui_diagnostics_log_path", None)
    if callable(fn):
        return Path(fn(skill_id))
    return Path(paths.logs_dir()) / f"service.{_safe_log_token(skill_id)}.ui_runtime.log"


def _sanitize_details(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    safe = _json_safe(dict(value), depth=0)
    if not isinstance(safe, dict):
        return {}
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) <= _MAX_DETAILS_BYTES:
        return safe
    return {
        "_truncated": True,
        "_bytes": len(encoded.encode("utf-8")),
        "summary": encoded[: min(len(encoded), 2000)],
    }


def _extract_browser_identity(details: Mapping[str, Any]) -> dict[str, Any]:
    direct = details.get("browser_identity")
    if isinstance(direct, Mapping):
        return dict(direct)
    cursor = details.get("runtime_debug_cursor")
    if isinstance(cursor, Mapping):
        nested = cursor.get("browser_identity")
        if isinstance(nested, Mapping):
            return dict(nested)
    return {}


def _json_safe(value: Any, *, depth: int) -> Any:
    if depth >= 6:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 80:
                out["_truncated_keys"] = True
                break
            out[_compact_string(key, max_chars=120) or ""] = _json_safe(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:80]]
    return str(value)


def _safe_log_token(value: str) -> str:
    token = _SAFE_LOG_TOKEN_RE.sub("_", str(value or "").strip())
    return token.strip(".") or "unknown"


def _normalize_level(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"warn", "warning"}:
        return "WARNING"
    if token in {"error", "danger", "fatal"}:
        return "ERROR"
    if token in {"debug"}:
        return "DEBUG"
    if token in {"success"}:
        return "INFO"
    return "INFO"


def _compact_string(value: Any, *, max_chars: int) -> str:
    token = str(value or "").strip()
    if len(token) <= max_chars:
        return token
    return f"{token[:max_chars]}..."


def _first_string(*values: Any) -> str | None:
    for value in values:
        token = _compact_string(value, max_chars=240)
        if token:
            return token
    return None


def _detail_value(details: Mapping[str, Any], key: str) -> Any:
    if key in details:
        return details.get(key)
    options = details.get("options")
    if isinstance(options, Mapping) and key in options:
        return options.get(key)
    context = details.get("context")
    if isinstance(context, Mapping) and key in context:
        return context.get(key)
    return None


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_values(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _append_candidate(items: list[str], value: Any) -> None:
    token = _compact_string(value, max_chars=300)
    if token and token not in items:
        items.append(token)
