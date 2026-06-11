from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings
from adaos.services.nlu_lookup_tables import collect_desktop_lookup_tables, collect_desktop_lookup_tables_async
from adaos.services.yjs.webspace import default_webspace_id


def _webspace_id(token: Any) -> str:
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


def _limit(value: Any, *, default: int = 50, max_value: int = 250) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, min(parsed, max_value))


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes, bytearray)) or isinstance(value, Mapping) or not isinstance(value, Iterable):
        return []
    return [dict(item) for item in iter_mappings(value)]


def _as_text_list(value: Any, *, limit: int = 100) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, Mapping)):
        items = list(value)
    else:
        items = []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _hash_payload(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _read_yaml(path: Path) -> dict[str, Any] | None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _path_from_ctx(ctx: AgentContext, name: str) -> Path | None:
    paths = getattr(ctx, "paths", None)
    if paths is None:
        return None
    value = getattr(paths, name, None)
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    if not value:
        return None
    return Path(value)


def _package_workspace_dir(ctx: AgentContext) -> Path | None:
    paths = getattr(ctx, "paths", None)
    package_dir = getattr(paths, "package_dir", None)
    if callable(package_dir):
        try:
            package_dir = package_dir()
        except Exception:
            package_dir = None
    if not package_dir:
        return None
    workspace = Path(package_dir) / ".adaos" / "workspace"
    return workspace if workspace.exists() else None


def _unique_paths(paths: Iterable[Path | None]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        if path is None:
            continue
        try:
            key = str(Path(path).resolve())
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(Path(path))
    return out


def _skill_roots(ctx: AgentContext) -> list[Path]:
    package = _package_workspace_dir(ctx)
    return _unique_paths(
        [
            _path_from_ctx(ctx, "skills_dir"),
            _path_from_ctx(ctx, "skills_workspace_dir"),
            package / "skills" if package else None,
        ]
    )


def _scenario_roots(ctx: AgentContext) -> list[Path]:
    package = _package_workspace_dir(ctx)
    return _unique_paths(
        [
            _path_from_ctx(ctx, "scenarios_dir"),
            _path_from_ctx(ctx, "scenarios_workspace_dir"),
            package / "scenarios" if package else None,
        ]
    )


def _read_skill_manifest(skill_id: str, *, ctx: AgentContext | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    token = str(skill_id or "").strip()
    if not token:
        return None, None
    ctx = ctx or get_ctx()
    for root in _skill_roots(ctx):
        for name in ("skill.yaml", "skill.yml"):
            path = root / token / name
            if not path.exists():
                continue
            payload = _read_yaml(path)
            if payload is not None:
                return payload, path
    return None, None


def _read_scenario_manifest(scenario_id: str, *, ctx: AgentContext | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    token = str(scenario_id or "").strip()
    if not token:
        return None, None
    ctx = ctx or get_ctx()
    for root in _scenario_roots(ctx):
        base = root / token
        for name in ("scenario.json", "scenario.yaml", "scenario.yml"):
            path = base / name
            if not path.exists():
                continue
            payload = _read_json(path) if name.endswith(".json") else _read_yaml(path)
            if payload is not None:
                return payload, path
    return None, None


def _read_yjs_teacher_snapshot(webspace_id: str) -> dict[str, Any]:
    from adaos.services.yjs.doc import get_ydoc

    ws = _webspace_id(webspace_id)
    try:
        with get_ydoc(ws, read_only=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            teacher = data_map.get("nlu_teacher")
            trace = data_map.get("nlu_trace")
            nlu = data_map.get("nlu")
            return {
                "webspace_id": ws,
                "teacher": coerce_dict(teacher),
                "trace": coerce_dict(trace),
                "nlu": coerce_dict(nlu),
            }
    except Exception as exc:
        return {
            "webspace_id": ws,
            "teacher": {},
            "trace": {},
            "nlu": {},
            "read_error": f"{type(exc).__name__}: {exc}",
        }


def _read_yjs_context_snapshot(webspace_id: str) -> dict[str, Any]:
    from adaos.services.yjs.doc import get_ydoc

    ws = _webspace_id(webspace_id)
    try:
        with get_ydoc(ws, read_only=True, load_mark_roots=["ui", "data", "registry"]) as ydoc:
            ui_map = ydoc.get_map("ui")
            data_map = ydoc.get_map("data")
            registry_map = ydoc.get_map("registry")
            return {
                "webspace_id": ws,
                "ui": coerce_dict(ui_map),
                "data": coerce_dict(data_map),
                "registry": coerce_dict(registry_map),
            }
    except Exception as exc:
        return {
            "webspace_id": ws,
            "ui": {},
            "data": {},
            "registry": {},
            "read_error": f"{type(exc).__name__}: {exc}",
        }


async def _read_yjs_context_snapshot_async(webspace_id: str) -> dict[str, Any]:
    from adaos.services.yjs.doc import async_get_ydoc

    ws = _webspace_id(webspace_id)
    try:
        async with async_get_ydoc(
            ws,
            read_only=True,
            prefer_live_room=True,
            load_mark_roots=["ui", "data", "registry"],
        ) as ydoc:
            ui_map = ydoc.get_map("ui")
            data_map = ydoc.get_map("data")
            registry_map = ydoc.get_map("registry")
            return {
                "webspace_id": ws,
                "ui": coerce_dict(ui_map),
                "data": coerce_dict(data_map),
                "registry": coerce_dict(registry_map),
            }
    except Exception as exc:
        return {
            "webspace_id": ws,
            "ui": {},
            "data": {},
            "registry": {},
            "read_error": f"{type(exc).__name__}: {exc}",
        }


def _event_ts(item: Mapping[str, Any]) -> float:
    try:
        return float(item.get("ts") or 0.0)
    except Exception:
        return 0.0


def _request_id(item: Mapping[str, Any]) -> str | None:
    rid = item.get("request_id")
    return rid if isinstance(rid, str) and rid.strip() else None


def _candidate_id_from_event(item: Mapping[str, Any]) -> str | None:
    cid = item.get("candidate_id")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()
    raw = item.get("raw") if isinstance(item.get("raw"), Mapping) else {}
    cid = raw.get("candidate_id")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()
    candidate = raw.get("candidate") if isinstance(raw.get("candidate"), Mapping) else {}
    cid = candidate.get("id")
    return cid.strip() if isinstance(cid, str) and cid.strip() else None


def _compact_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    regex_rule = candidate.get("regex_rule") if isinstance(candidate.get("regex_rule"), Mapping) else {}
    target = candidate.get("target") if isinstance(candidate.get("target"), Mapping) else {}
    preview = candidate.get("preview") if isinstance(candidate.get("preview"), Mapping) else {}
    verification = candidate.get("verification") if isinstance(candidate.get("verification"), Mapping) else {}
    promotion = candidate.get("promotion") if isinstance(candidate.get("promotion"), Mapping) else {}
    provenance = candidate.get("provenance") if isinstance(candidate.get("provenance"), Mapping) else {}
    privacy = candidate.get("privacy") if isinstance(candidate.get("privacy"), Mapping) else {}
    dispatch = candidate.get("dispatch") if isinstance(candidate.get("dispatch"), Mapping) else {}
    return {
        "id": candidate.get("id"),
        "request_id": candidate.get("request_id"),
        "text": candidate.get("text"),
        "kind": candidate.get("kind"),
        "status": candidate.get("status"),
        "target": dict(target) if target else None,
        "regex_rule": dict(regex_rule) if regex_rule else None,
        "preview": dict(preview) if preview else None,
        "verification": dict(verification) if verification else None,
        "promotion": dict(promotion) if promotion else None,
        "provenance": dict(provenance) if provenance else None,
        "privacy": dict(privacy) if privacy else None,
        "dispatch_status": candidate.get("dispatch_status"),
        "dispatch": dict(dispatch) if dispatch else None,
        "created_at": candidate.get("ts"),
        "applied": dict(candidate.get("applied") or {}) if isinstance(candidate.get("applied"), Mapping) else None,
        "rolled_back_at": candidate.get("rolled_back_at"),
    }


def _mapping_collection_rows(value: Any, *, id_fields: tuple[str, ...] = ("id",)) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        iterator = value.items()
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        iterator = ((None, item) for item in value)
    else:
        return rows

    for key, item in iterator:
        item_map = coerce_dict(item)
        if not item_map and item is not None and not isinstance(item, Mapping):
            item_map = {"id": item}
        row_id = str(key or "").strip()
        if not row_id:
            for field in id_fields:
                candidate = item_map.get(field)
                if isinstance(candidate, str) and candidate.strip():
                    row_id = candidate.strip()
                    break
        row = dict(item_map)
        if row_id:
            row.setdefault("id", row_id)
        if row:
            rows.append(row)
    return rows


def _compact_registry_item(item: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    out = {
        "id": item.get("id") or item.get(f"{kind}_id") or item.get("value"),
        "title": item.get("title") or item.get("name") or item.get("label") or item.get("display_name"),
        "origin": item.get("origin"),
        "type": item.get("type"),
        "launchModal": item.get("launchModal") or item.get("launch_modal") or item.get("modalId"),
        "scenario_id": item.get("scenario_id") or item.get("scenarioId"),
    }
    return {key: value for key, value in out.items() if value not in (None, "", [], {})}


def _active_teacher_sessions(teacher: Mapping[str, Any]) -> dict[str, Any]:
    confirmations = [
        item
        for item in _as_list(teacher.get("pending_confirmations"))
        if str(item.get("status") or "").strip() == "awaiting_user"
    ]
    clarifications = [
        item
        for item in _as_list(teacher.get("clarification_sessions"))
        if str(item.get("status") or "").strip() == "awaiting_user"
    ]
    return {
        "pending_confirmations": [
            {
                "id": item.get("id"),
                "request_id": item.get("request_id"),
                "candidate_id": item.get("candidate_id"),
                "question": item.get("question"),
                "attempt": item.get("attempt"),
                "ts": item.get("ts"),
            }
            for item in sorted(confirmations, key=_event_ts, reverse=True)[:10]
        ],
        "clarification_sessions": [
            {
                "id": item.get("id"),
                "kind": item.get("kind"),
                "uncertainty_kind": item.get("uncertainty_kind"),
                "request_id": item.get("request_id"),
                "question": item.get("question"),
                "allowed_answers": list(item.get("allowed_answers") or [])[:6]
                if isinstance(item.get("allowed_answers"), list)
                else [],
                "attempt": item.get("attempt"),
                "ts": item.get("ts"),
            }
            for item in sorted(clarifications, key=_event_ts, reverse=True)[:10]
        ],
    }


def _recent_teacher_errors(teacher: Mapping[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(teacher.get("events")):
        kind = str(item.get("kind") or "").strip()
        if not kind:
            continue
        lowered = kind.casefold()
        if not any(marker in lowered for marker in ("error", "failed", "quarantine", "timeout", "rejected")):
            continue
        rows.append(
            {
                "ts": item.get("ts"),
                "kind": kind,
                "request_id": item.get("request_id"),
                "title": item.get("title"),
                "subtitle": item.get("subtitle"),
            }
        )
    return sorted(rows, key=_event_ts, reverse=True)[: max(1, int(limit))]


def get_nlu_trace(
    *,
    webspace_id: str | None = None,
    request_id: str | None = None,
    candidate_id: str | None = None,
    limit: int = 80,
) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    snapshot = _read_yjs_teacher_snapshot(ws)
    max_items = _limit(limit, default=80, max_value=500)
    teacher = snapshot["teacher"]
    trace_obj = snapshot["trace"]
    trace_items = _as_list(trace_obj.get("items"))
    events = _as_list(teacher.get("events"))
    candidates = _as_list(teacher.get("candidates"))

    request_filter = str(request_id or "").strip() or None
    candidate_filter = str(candidate_id or "").strip() or None
    if candidate_filter and not request_filter:
        for candidate in candidates:
            if candidate.get("id") == candidate_filter:
                request_filter = _request_id(candidate)
                break

    def _match_request(item: Mapping[str, Any]) -> bool:
        return not request_filter or _request_id(item) == request_filter

    def _match_event(item: Mapping[str, Any]) -> bool:
        if request_filter and _request_id(item) != request_filter:
            return False
        if candidate_filter and _candidate_id_from_event(item) != candidate_filter:
            return False
        return True

    trace_rows = [item for item in trace_items if _match_request(item)]
    event_rows = [item for item in events if _match_event(item)]
    candidate_rows = []
    for candidate in candidates:
        if candidate_filter and candidate.get("id") != candidate_filter:
            continue
        if request_filter and candidate.get("request_id") != request_filter:
            continue
        candidate_rows.append(_compact_candidate(candidate))

    trace_rows = sorted(trace_rows, key=_event_ts)[-max_items:]
    event_rows = sorted(event_rows, key=_event_ts)[-max_items:]
    return {
        "ok": True,
        "webspace_id": ws,
        "request_id": request_filter,
        "candidate_id": candidate_filter,
        "trace": trace_rows,
        "teacher_events": event_rows,
        "candidates": candidate_rows[-max_items:],
        "read_error": snapshot.get("read_error"),
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def get_nlu_dialog_context(
    *,
    webspace_id: str | None = None,
    request_id: str | None = None,
    candidate_id: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    snapshot = _read_yjs_teacher_snapshot(ws)
    teacher = snapshot["teacher"]
    max_items = _limit(limit, default=25, max_value=100)
    request_filter = str(request_id or "").strip() or None
    candidate_filter = str(candidate_id or "").strip() or None

    candidates = _as_list(teacher.get("candidates"))
    if candidate_filter and not request_filter:
        for candidate in candidates:
            if candidate.get("id") == candidate_filter:
                request_filter = _request_id(candidate)
                break

    events = _as_list(teacher.get("events"))
    llm_logs = _as_list(teacher.get("llm_logs"))
    request_threads = _as_list(teacher.get("threads_by_request"))
    candidate_threads = _as_list(teacher.get("threads_by_candidate"))

    if request_filter:
        events = [item for item in events if item.get("request_id") == request_filter]
        llm_logs = [item for item in llm_logs if item.get("request_id") == request_filter]
        request_threads = [item for item in request_threads if item.get("request_id") == request_filter]
        candidates = [item for item in candidates if item.get("request_id") == request_filter]
        candidate_threads = [item for item in candidate_threads if item.get("request_id") == request_filter]
    if candidate_filter:
        candidates = [item for item in candidates if item.get("id") == candidate_filter]
        candidate_threads = [item for item in candidate_threads if item.get("candidate_id") == candidate_filter]

    latest_candidate = None
    if candidates:
        latest_candidate = _compact_candidate(sorted(candidates, key=_event_ts)[-1])

    latest_request_id = request_filter
    if latest_request_id is None:
        for item in sorted(events, key=_event_ts, reverse=True):
            latest_request_id = _request_id(item)
            if latest_request_id:
                break

    correction_target = None
    for candidate in sorted(_as_list(teacher.get("candidates")), key=_event_ts, reverse=True):
        status = str(candidate.get("status") or "")
        if status in {"rolled_back"}:
            continue
        correction_target = _compact_candidate(candidate)
        break

    return {
        "ok": True,
        "webspace_id": ws,
        "request_id": latest_request_id,
        "candidate_id": candidate_filter,
        "threads_by_request": request_threads[-max_items:],
        "threads_by_candidate": candidate_threads[-max_items:],
        "events": sorted(events, key=_event_ts)[-max_items:],
        "llm_logs": sorted(llm_logs, key=_event_ts)[-max_items:],
        "candidates": [_compact_candidate(item) for item in sorted(candidates, key=_event_ts)[-max_items:]],
        "latest_candidate": latest_candidate,
        "correction_context": {
            "active": bool(correction_target),
            "previous_candidate": correction_target,
        },
        "read_error": snapshot.get("read_error"),
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def get_nlu_recent_failures(*, webspace_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    snapshot = _read_yjs_teacher_snapshot(ws)
    teacher = snapshot["teacher"]
    trace_obj = snapshot["trace"]
    max_items = _limit(limit, default=50, max_value=200)

    rows: list[dict[str, Any]] = []
    for item in _as_list(teacher.get("items")):
        classification = item.get("classification") if isinstance(item.get("classification"), Mapping) else {}
        rows.append(
            {
                "ts": item.get("ts"),
                "source": "nlu_teacher.items",
                "request_id": item.get("request_id"),
                "text": item.get("text"),
                "reason": item.get("reason"),
                "via": item.get("via"),
                "status": item.get("status"),
                "classification": dict(classification),
                "teachable": bool(classification.get("teachable")),
            }
        )
    for item in _as_list(trace_obj.get("items")):
        if item.get("type") != "nlp.intent.not_obtained":
            continue
        rows.append(
            {
                "ts": item.get("ts"),
                "source": "nlu_trace",
                "request_id": item.get("request_id"),
                "text": item.get("text"),
                "reason": item.get("reason"),
                "via": item.get("via"),
                "status": "not_obtained",
                "classification": {},
                "teachable": None,
            }
        )
    rows = sorted(rows, key=_event_ts, reverse=True)[:max_items]
    return {
        "ok": True,
        "webspace_id": ws,
        "failures": rows,
        "read_error": snapshot.get("read_error"),
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def get_desktop_registry_lookup(*, webspace_id: str | None = None, include_live: bool = True) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    payload = collect_desktop_lookup_tables(get_ctx(), webspace_id=ws, include_live=include_live)
    return {
        **payload,
        "registry_kind": "desktop.lookup_tables",
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


async def get_desktop_registry_lookup_async(*, webspace_id: str | None = None, include_live: bool = True) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    payload = await collect_desktop_lookup_tables_async(get_ctx(), webspace_id=ws, include_live=include_live)
    return {
        **payload,
        "registry_kind": "desktop.lookup_tables",
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def _intent_action_summary(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions = spec.get("actions")
    out: list[dict[str, Any]] = []
    for action in iter_mappings(actions if isinstance(actions, Iterable) and not isinstance(actions, (str, bytes, Mapping)) else []):
        out.append({k: v for k, v in dict(action).items() if k in {"type", "target", "params"}})
    return out[:10]


def _nlu_descriptor_from_manifest(payload: Mapping[str, Any], *, owner_type: str, owner_id: str, path: Path | None) -> dict[str, Any]:
    nlu = payload.get("nlu") if isinstance(payload.get("nlu"), Mapping) else {}
    intents: dict[str, Any] = {}
    for name, spec in (nlu.get("intents") if isinstance(nlu.get("intents"), Mapping) else {}).items():
        if not isinstance(name, str) or not isinstance(spec, Mapping):
            continue
        examples = spec.get("examples")
        if not isinstance(examples, list):
            examples = []
        intents[name] = {
            "description": spec.get("description"),
            "scope": spec.get("scope"),
            "examples_count": len([item for item in examples if isinstance(item, str) and item.strip()]),
            "examples": [item for item in examples if isinstance(item, str) and item.strip()][:20],
            "actions": _intent_action_summary(spec),
        }
    rules = []
    for rule in iter_mappings(nlu.get("regex_rules")):
        rules.append(
            {
                "id": rule.get("id"),
                "intent": rule.get("intent"),
                "pattern": rule.get("pattern"),
                "enabled": rule.get("enabled", True),
                "source": rule.get("source"),
                "candidate_id": rule.get("candidate_id"),
            }
        )
    descriptor = {
        "ok": True,
        "owner": {"type": owner_type, "id": owner_id},
        "path": str(path) if path else None,
        "fingerprint": _hash_payload({"owner": [owner_type, owner_id], "nlu": nlu}),
        "nlu": {
            "intents": intents,
            "regex_rules": rules,
            "regex_rules_count": len(rules),
        },
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }
    if owner_type == "skill":
        events = payload.get("events") if isinstance(payload.get("events"), Mapping) else {}
        descriptor["skill_surface"] = {
            "subscribes": list(events.get("subscribe") or []) if isinstance(events.get("subscribe"), list) else [],
            "publishes": list(events.get("publish") or []) if isinstance(events.get("publish"), list) else [],
            "llm_policy": dict(payload.get("llm_policy") or {}) if isinstance(payload.get("llm_policy"), Mapping) else {},
        }
    return descriptor


def _target_matches(owner: Mapping[str, Any], *, owner_type: str | None, owner_id: str | None) -> bool:
    if owner_type and str(owner.get("type") or "") != owner_type:
        return False
    if owner_id and str(owner.get("id") or "") != owner_id:
        return False
    return True


def _template_id(payload: Mapping[str, Any]) -> str:
    owner = payload.get("owner") if isinstance(payload.get("owner"), Mapping) else {}
    kind = str(payload.get("kind") or "template").replace(".", "_")
    owner_type = str(owner.get("type") or "owner")
    owner_id = str(owner.get("id") or "unknown")
    digest = _hash_payload(payload)[:16]
    return f"tpl.{owner_type}.{owner_id}.{kind}.{digest}"


def _template_row(
    *,
    owner: Mapping[str, Any],
    intent: str | None,
    kind: str,
    path: Path | None,
    payload: Mapping[str, Any],
    mutation: str,
    status: str = "active",
) -> dict[str, Any]:
    base = {
        "owner": dict(owner),
        "intent": intent,
        "kind": kind,
        "status": status,
        "source_path": str(path) if path else None,
        "payload": dict(payload),
        "mutation": mutation,
    }
    base["fingerprint"] = _hash_payload(base)
    base["id"] = _template_id(base)
    return base


def _iter_artifact_nlu_templates(
    *,
    owner_type: str,
    owner_id: str,
    payload: Mapping[str, Any],
    path: Path | None,
) -> list[dict[str, Any]]:
    owner = {"type": owner_type, "id": owner_id}
    nlu = payload.get("nlu") if isinstance(payload.get("nlu"), Mapping) else {}
    rows: list[dict[str, Any]] = []
    intents = nlu.get("intents") if isinstance(nlu.get("intents"), Mapping) else {}
    for intent, spec in sorted(intents.items()):
        if not isinstance(intent, str) or not isinstance(spec, Mapping):
            continue
        examples = spec.get("examples")
        if isinstance(examples, list):
            for index, example in enumerate(examples):
                if not isinstance(example, str) or not example.strip():
                    continue
                rows.append(
                    _template_row(
                        owner=owner,
                        intent=intent,
                        kind="example",
                        path=path,
                        payload={"text": example.strip(), "index": index},
                        mutation="append_example",
                    )
                )
        actions = _intent_action_summary(spec)
        if actions:
            rows.append(
                _template_row(
                    owner=owner,
                    intent=intent,
                    kind="intent_route",
                    path=path,
                    payload={"actions": actions, "slots": _slots_from_actions(actions)},
                    mutation="none",
                )
            )
    for index, rule in enumerate(iter_mappings(nlu.get("regex_rules"))):
        intent = rule.get("intent") if isinstance(rule.get("intent"), str) else None
        rows.append(
            _template_row(
                owner=owner,
                intent=intent,
                kind="regex_rule",
                path=path,
                payload={
                    "id": rule.get("id"),
                    "pattern": rule.get("pattern"),
                    "enabled": rule.get("enabled", True),
                    "source": rule.get("source"),
                    "candidate_id": rule.get("candidate_id"),
                    "index": index,
                },
                mutation="append_regex_rule",
                status="active" if bool(rule.get("enabled", True)) else "disabled",
            )
        )
    return rows


def describe_skill_nlu(skill_id: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    token = str(skill_id or "").strip()
    payload, path = _read_skill_manifest(token, ctx=ctx)
    if payload is None:
        return {"ok": False, "status": "not_found", "owner": {"type": "skill", "id": token}}
    return _nlu_descriptor_from_manifest(payload, owner_type="skill", owner_id=token, path=path)


def describe_scenario_nlu(scenario_id: str, *, ctx: AgentContext | None = None) -> dict[str, Any]:
    token = str(scenario_id or "").strip()
    payload, path = _read_scenario_manifest(token, ctx=ctx)
    if payload is None:
        return {"ok": False, "status": "not_found", "owner": {"type": "scenario", "id": token}}
    return _nlu_descriptor_from_manifest(payload, owner_type="scenario", owner_id=token, path=path)


def list_nlu_templates(
    *,
    webspace_id: str | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
    include_system_actions: bool = True,
) -> dict[str, Any]:
    from adaos.services.nlu.system_actions_catalog import system_action_nlu_intents

    ctx = get_ctx()
    owner_type_token = str(owner_type or "").strip() or None
    owner_id_token = str(owner_id or "").strip() or None
    rows: list[dict[str, Any]] = []
    for root in _skill_roots(ctx):
        if not root.exists():
            continue
        for skill_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            payload, path = _read_skill_manifest(skill_dir.name, ctx=ctx)
            if payload is None:
                continue
            owner = {"type": "skill", "id": skill_dir.name}
            if not _target_matches(owner, owner_type=owner_type_token, owner_id=owner_id_token):
                continue
            rows.extend(_iter_artifact_nlu_templates(owner_type="skill", owner_id=skill_dir.name, payload=payload, path=path))
    for root in _scenario_roots(ctx):
        if not root.exists():
            continue
        for scenario_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            payload, path = _read_scenario_manifest(scenario_dir.name, ctx=ctx)
            if payload is None:
                continue
            owner = {"type": "scenario", "id": scenario_dir.name}
            if not _target_matches(owner, owner_type=owner_type_token, owner_id=owner_id_token):
                continue
            rows.extend(_iter_artifact_nlu_templates(owner_type="scenario", owner_id=scenario_dir.name, payload=payload, path=path))
    if include_system_actions:
        for intent, spec in sorted(system_action_nlu_intents().items()):
            owner = {"type": "system_action", "id": str(spec.get("action_id") or intent)}
            if not _target_matches(owner, owner_type=owner_type_token, owner_id=owner_id_token):
                continue
            examples = spec.get("examples")
            if isinstance(examples, list):
                for index, example in enumerate(examples):
                    if not isinstance(example, str) or not example.strip():
                        continue
                    rows.append(
                        _template_row(
                            owner=owner,
                            intent=intent,
                            kind="system_action_example",
                            path=None,
                            payload={"text": example.strip(), "index": index, "host_action": spec.get("host_action")},
                            mutation="none",
                        )
                    )
            actions = [dict(item) for item in iter_mappings(spec.get("actions"))]
            rows.append(
                _template_row(
                    owner=owner,
                    intent=intent,
                    kind="system_action_route",
                    path=None,
                    payload={"host_action": spec.get("host_action"), "actions": actions, "slots": _slots_from_actions(actions)},
                    mutation="none",
                )
            )

    rows = sorted(rows, key=lambda item: (str((item.get("owner") or {}).get("type") or ""), str((item.get("owner") or {}).get("id") or ""), str(item.get("kind") or ""), str(item.get("intent") or "")))
    return {
        "ok": True,
        "webspace_id": _webspace_id(webspace_id),
        "templates": rows,
        "summary": {
            "count": len(rows),
            "by_kind": {
                kind: sum(1 for item in rows if item.get("kind") == kind)
                for kind in sorted({str(item.get("kind") or "") for item in rows})
                if kind
            },
            "by_owner_type": {
                kind: sum(1 for item in rows if (item.get("owner") or {}).get("type") == kind)
                for kind in sorted({str((item.get("owner") or {}).get("type") or "") for item in rows})
                if kind
            },
        },
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def describe_sdk_surface(*, level: str = "std") -> dict[str, Any]:
    token = str(level or "std").strip().lower()
    items = [
        {
            "id": "adaos.eventbus.emit",
            "kind": "sdk_function",
            "status": "descriptive_only",
            "description": "Emit governed AdaOS events through service/runtime boundaries.",
            "execution_boundary": "not_callable_by_llm",
            "nlu_usage": "Map user intent to scenario/skill host actions; AdaOS dispatches, LLM does not call SDK directly.",
        },
        {
            "id": "adaos.nlu.probe_phrase",
            "kind": "sdk_function",
            "status": "descriptive_only",
            "description": "Run a dry NLU probe and return stage/ranking/slots evidence.",
            "execution_boundary": "available through nlu_authoring.check_phrase MCP only",
        },
        {
            "id": "adaos.desktop.host_actions",
            "kind": "host_action_surface",
            "status": "descriptive_only",
            "description": "Desktop actions are exposed as callHost scenario actions and previewed through AdaOS, not called by the LLM.",
            "execution_boundary": "scenario callHost/event bus",
        },
    ]
    if token == "mini":
        items = items[:2]
    return {
        "ok": True,
        "surface_id": "adaos.sdk.describe_surface.v1",
        "level": token if token in {"mini", "std", "rich"} else "std",
        "items": items,
        "authoring_boundaries": {
            "mode": "descriptive_only",
            "llm_direct_sdk_calls": False,
            "dispatch": False,
            "training_mutation": False,
            "side_effects": "none",
        },
    }


_SLOT_PATTERN = re.compile(r"\$slot\.([a-zA-Z_][a-zA-Z0-9_]*)")


def _slots_from_actions(actions: list[Mapping[str, Any]]) -> list[str]:
    slots: set[str] = set()
    for action in actions:
        text = json.dumps(action, ensure_ascii=False, default=str)
        slots.update(_SLOT_PATTERN.findall(text))
    return sorted(slots)


def list_training_targets(*, webspace_id: str | None = None, include_system_actions: bool = True) -> dict[str, Any]:
    from adaos.services.nlu.system_actions_catalog import system_action_nlu_intents

    ctx = get_ctx()
    targets: list[dict[str, Any]] = []
    for root in _skill_roots(ctx):
        if not root.exists():
            continue
        for skill_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            descriptor = describe_skill_nlu(skill_dir.name, ctx=ctx)
            if descriptor.get("ok"):
                targets.append(
                    {
                        "type": "skill",
                        "id": skill_dir.name,
                        "path": descriptor.get("path"),
                        "fingerprint": descriptor.get("fingerprint"),
                        "intents": sorted((descriptor.get("nlu") or {}).get("intents") or {}),
                        "regex_rules_count": (descriptor.get("nlu") or {}).get("regex_rules_count", 0),
                        "llm_policy": (descriptor.get("skill_surface") or {}).get("llm_policy") or {},
                    }
                )
    for root in _scenario_roots(ctx):
        if not root.exists():
            continue
        for scenario_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            descriptor = describe_scenario_nlu(scenario_dir.name, ctx=ctx)
            if descriptor.get("ok"):
                targets.append(
                    {
                        "type": "scenario",
                        "id": scenario_dir.name,
                        "path": descriptor.get("path"),
                        "fingerprint": descriptor.get("fingerprint"),
                        "intents": sorted((descriptor.get("nlu") or {}).get("intents") or {}),
                        "regex_rules_count": (descriptor.get("nlu") or {}).get("regex_rules_count", 0),
                    }
                )
    if include_system_actions:
        for intent, spec in sorted(system_action_nlu_intents().items()):
            actions = [dict(item) for item in iter_mappings(spec.get("actions"))]
            targets.append(
                {
                    "type": "system_action",
                    "id": str(spec.get("action_id") or intent),
                    "intent": intent,
                    "host_action": spec.get("host_action"),
                    "slots": _slots_from_actions(actions),
                    "examples_count": len(list(spec.get("examples") or [])) if isinstance(spec.get("examples"), list) else 0,
                    "fingerprint": _hash_payload({"intent": intent, "spec": spec}),
                }
            )
    return {
        "ok": True,
        "webspace_id": _webspace_id(webspace_id),
        "targets": targets,
        "summary": {
            "count": len(targets),
            "by_type": {
                kind: sum(1 for item in targets if item.get("type") == kind)
                for kind in sorted({str(item.get("type") or "") for item in targets})
                if kind
            },
        },
        "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
    }


def _side_effect_class_for_host_action(host_action: str) -> str:
    token = str(host_action or "").strip()
    if token in {"desktop.modal.open", "desktop.scenario.set"}:
        return "ui_navigation"
    if token in {"desktop.webspace.reload", "desktop.webspace.reset", "desktop.toggleInstall"}:
        return "local_state_change"
    if token.startswith("nlp.teacher."):
        return "durable_configuration_change"
    if token.startswith("scenario.workflow."):
        return "local_state_change"
    return "unknown"


def _action_class_for_route(owner: Mapping[str, Any], actions: list[Mapping[str, Any]]) -> str:
    owner_type = str(owner.get("type") or "").strip()
    if owner_type == "system_action":
        return "interface_action"
    for action in actions:
        if str(action.get("type") or "").strip() == "callHost":
            return "interface_action"
        if str(action.get("type") or "").strip() == "callSkill":
            return "skill_action"
    if owner_type == "scenario":
        return "scenario_flow"
    if owner_type == "skill":
        return "skill_action"
    return "unknown"


def _route_side_effect_class(actions: list[Mapping[str, Any]]) -> str:
    classes: list[str] = []
    for action in actions:
        if str(action.get("type") or "").strip() == "callHost":
            classes.append(_side_effect_class_for_host_action(str(action.get("target") or "")))
        elif str(action.get("type") or "").strip() == "callSkill":
            classes.append("skill_action")
    if not classes:
        return "unknown"
    priority = [
        "destructive",
        "external_io",
        "durable_configuration_change",
        "local_state_change",
        "skill_action",
        "ui_navigation",
        "read_only",
        "unknown",
    ]
    for item in priority:
        if item in classes:
            return item
    return classes[0]


def _system_action_surface_rows() -> list[dict[str, Any]]:
    from adaos.services.nlu.system_actions_catalog import describe_system_actions

    rows: list[dict[str, Any]] = []
    for action in describe_system_actions():
        host_action = str(action.get("action") or "").strip()
        slots = action.get("slots") if isinstance(action.get("slots"), Mapping) else {}
        rows.append(
            {
                "id": action.get("id"),
                "class": "interface_action",
                "owner": {"type": "system_action", "id": action.get("id")},
                "host_action": host_action,
                "intents": list(action.get("intents") or []) if isinstance(action.get("intents"), list) else [],
                "required_slots": [
                    str(name)
                    for name, spec in slots.items()
                    if isinstance(name, str) and isinstance(spec, Mapping) and bool(spec.get("required"))
                ],
                "optional_slots": [
                    str(name)
                    for name, spec in slots.items()
                    if isinstance(name, str) and isinstance(spec, Mapping) and not bool(spec.get("required"))
                ],
                "examples": _as_text_list(action.get("examples"), limit=12),
                "side_effect_class": _side_effect_class_for_host_action(host_action),
                "preview_method": "desktop.preview_action",
                "description": action.get("description"),
            }
        )
    return rows


def _template_action_surface_rows(*, webspace_id: str | None = None, limit: int = 120) -> list[dict[str, Any]]:
    templates = list_nlu_templates(webspace_id=webspace_id, include_system_actions=False)
    rows: list[dict[str, Any]] = []
    template_rows = templates.get("templates") if isinstance(templates.get("templates"), list) else []
    for item in template_rows:
        if not isinstance(item, Mapping) or str(item.get("kind") or "") != "intent_route":
            continue
        owner = item.get("owner") if isinstance(item.get("owner"), Mapping) else {}
        payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
        actions = [dict(action) for action in iter_mappings(payload.get("actions"))]
        intent = str(item.get("intent") or "").strip()
        if not intent or not actions:
            continue
        owner_type = str(owner.get("type") or "").strip()
        owner_id = str(owner.get("id") or "").strip()
        rows.append(
            {
                "id": f"{owner_type}.{owner_id}.{intent}",
                "class": _action_class_for_route(owner, actions),
                "owner": {"type": owner_type, "id": owner_id},
                "intent": intent,
                "actions": actions[:5],
                "required_slots": _as_text_list(payload.get("slots"), limit=20),
                "side_effect_class": _route_side_effect_class(actions),
                "preview_method": "desktop.preview_action"
                if any(str(action.get("type") or "") == "callHost" for action in actions)
                else "nlu_authoring.check_phrase",
                "source_path": item.get("source_path"),
                "fingerprint": item.get("fingerprint"),
            }
        )
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def _hint_containers(payload: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    nlu = payload.get("nlu") if isinstance(payload.get("nlu"), Mapping) else {}
    containers: list[tuple[str, Mapping[str, Any]]] = []
    for key, value in (
        ("llm_hints", payload.get("llm_hints")),
        ("nlu_hints", payload.get("nlu_hints")),
        ("nlu.llm_hints", nlu.get("llm_hints")),
        ("nlu.nlu_hints", nlu.get("nlu_hints")),
    ):
        if isinstance(value, Mapping) and value:
            containers.append((key, value))
    return containers


def _compact_hints(container: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "aliases",
        "entities",
        "primary_actions",
        "actions",
        "examples",
        "slot_schemas",
        "slots",
        "side_effect_class",
        "owner_hints",
    ):
        value = container.get(key)
        if value not in (None, "", [], {}):
            out[key] = value
    return out


def _developer_hint_rows(*, limit: int = 120) -> list[dict[str, Any]]:
    ctx = get_ctx()
    rows: list[dict[str, Any]] = []

    def _append(owner_type: str, owner_id: str, payload: Mapping[str, Any], path: Path | None) -> None:
        for container_key, container in _hint_containers(payload):
            hints = _compact_hints(container)
            if not hints:
                continue
            rows.append(
                {
                    "owner": {"type": owner_type, "id": owner_id},
                    "source": container_key,
                    "source_path": str(path) if path else None,
                    "hints": hints,
                    "fingerprint": _hash_payload({"owner": [owner_type, owner_id], "source": container_key, "hints": hints}),
                }
            )

    for root in _skill_roots(ctx):
        if not root.exists():
            continue
        for skill_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            payload, path = _read_skill_manifest(skill_dir.name, ctx=ctx)
            if payload:
                _append("skill", skill_dir.name, payload, path)
            webui_path = skill_dir / "webui.json"
            webui = _read_json(webui_path) if webui_path.exists() else None
            if webui:
                _append("skill", skill_dir.name, webui, webui_path)
            if len(rows) >= limit:
                return rows[:limit]

    for root in _scenario_roots(ctx):
        if not root.exists():
            continue
        for scenario_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            payload, path = _read_scenario_manifest(scenario_dir.name, ctx=ctx)
            if payload:
                _append("scenario", scenario_dir.name, payload, path)
            if len(rows) >= limit:
                return rows[:limit]
    return rows[:limit]


def _voice_label_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, list[str]] = {}
    for locale, items in value.items():
        key = str(locale or "").strip() or "und"
        labels = _as_text_list(items, limit=40)
        if labels:
            out[key] = labels
    return out


def _voice_activation_steps(value: Any) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for item in iter_mappings(value):
        step_type = str(item.get("type") or "").strip()
        if not step_type:
            continue
        step = {"type": step_type}
        params = item.get("params")
        if isinstance(params, Mapping):
            step["params"] = dict(params)
        for key in ("description", "target", "id"):
            if item.get(key) not in (None, "", [], {}):
                step[key] = item.get(key)
        steps.append(step)
    return steps


def _activation_modal_ids(activation: list[dict[str, Any]]) -> list[str]:
    modal_ids: list[str] = []
    for step in activation:
        params = step.get("params") if isinstance(step.get("params"), Mapping) else {}
        for key in ("modal_id", "modalId"):
            token = str(params.get(key) or "").strip()
            if token and not token.startswith("$"):
                modal_ids.append(token)
    return modal_ids


def _voice_availability(
    item: Mapping[str, Any],
    *,
    activation: list[dict[str, Any]],
    runtime_state: Mapping[str, Any],
) -> dict[str, Any]:
    available_modal_ids = {
        str(value or "").strip()
        for value in (runtime_state.get("available_modal_ids") if isinstance(runtime_state.get("available_modal_ids"), list) else [])
        if str(value or "").strip()
    }
    parent = str(item.get("parent") or "").strip()
    modal_ids = _activation_modal_ids(activation)
    checked_ids = [value for value in [parent, *modal_ids] if value]
    if not available_modal_ids:
        return {"status": "descriptor_only", "reason": "runtime_modal_inventory_unavailable", "checked_ids": checked_ids}
    if not checked_ids:
        return {"status": "unknown", "reason": "no_container_reference"}
    if any(value in available_modal_ids for value in checked_ids):
        return {"status": "reachable", "checked_ids": checked_ids}
    return {"status": "not_currently_reachable", "checked_ids": checked_ids}


def _voice_item_row(
    *,
    item: Mapping[str, Any],
    collection: str,
    owner_type: str,
    owner_id: str,
    source_path: Path | None,
    runtime_state: Mapping[str, Any],
) -> dict[str, Any] | None:
    item_id = str(item.get("id") or "").strip()
    if not item_id:
        return None
    activation = _voice_activation_steps(item.get("activation") or item.get("activation_plan"))
    labels = _voice_label_map(item.get("labels"))
    aliases = _as_text_list(item.get("aliases"), limit=80)
    title = str(item.get("title") or "").strip()
    if title and title not in aliases:
        aliases = [title, *aliases]
    owner = {"type": owner_type, "id": owner_id}
    default_side_effect = "read_only" if collection == "voice_capabilities" else "ui_navigation"
    side_effect_class = str(item.get("side_effect_class") or default_side_effect)
    row: dict[str, Any] = {
        "id": item_id,
        "class": "voice_capability" if collection == "voice_capabilities" else "voice_affordance",
        "kind": str(item.get("kind") or ("capability" if collection == "voice_capabilities" else "ui_affordance")),
        "owner": owner,
        "source_path": str(source_path) if source_path else None,
        "labels": labels,
        "aliases": aliases,
        "side_effect_class": side_effect_class,
        "activation": activation,
        "availability": _voice_availability(item, activation=activation, runtime_state=runtime_state),
        "fingerprint": _hash_payload({"owner": owner, "collection": collection, "item": item}),
    }
    for key in (
        "parent",
        "description",
        "locale",
        "visibility",
        "parameters",
        "result_modes",
        "default_result_mode",
        "query_contract",
        "verify",
    ):
        if item.get(key) not in (None, "", [], {}):
            row[key] = item.get(key)
    return row


def _voice_containers(payload: Mapping[str, Any]) -> list[tuple[str, list[Mapping[str, Any]]]]:
    containers: list[tuple[str, list[Mapping[str, Any]]]] = []
    nlu = payload.get("nlu") if isinstance(payload.get("nlu"), Mapping) else {}
    for key, value in (
        ("voice_capabilities", payload.get("voice_capabilities")),
        ("voice_affordances", payload.get("voice_affordances")),
        ("nlu.voice_capabilities", nlu.get("voice_capabilities")),
        ("nlu.voice_affordances", nlu.get("voice_affordances")),
    ):
        rows = [dict(item) for item in iter_mappings(value)]
        if rows:
            containers.append((key, rows))
    return containers


def _voice_surface_rows(*, runtime_state: Mapping[str, Any], limit: int = 200) -> dict[str, Any]:
    ctx = get_ctx()
    capabilities: list[dict[str, Any]] = []
    affordances: list[dict[str, Any]] = []

    def _append(owner_type: str, owner_id: str, payload: Mapping[str, Any], path: Path | None) -> None:
        for collection_key, items in _voice_containers(payload):
            collection = "voice_capabilities" if collection_key.endswith("voice_capabilities") else "voice_affordances"
            target = capabilities if collection == "voice_capabilities" else affordances
            for item in items:
                row = _voice_item_row(
                    item=item,
                    collection=collection,
                    owner_type=owner_type,
                    owner_id=owner_id,
                    source_path=path,
                    runtime_state=runtime_state,
                )
                if row:
                    target.append(row)

    for root in _skill_roots(ctx):
        if not root.exists():
            continue
        for skill_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            payload, path = _read_skill_manifest(skill_dir.name, ctx=ctx)
            if payload:
                _append("skill", skill_dir.name, payload, path)
            webui_path = skill_dir / "webui.json"
            webui = _read_json(webui_path) if webui_path.exists() else None
            if webui:
                _append("skill", skill_dir.name, webui, webui_path)
            if len(capabilities) + len(affordances) >= limit:
                return {"voice_capabilities": capabilities[:limit], "voice_affordances": affordances[:limit]}

    for root in _scenario_roots(ctx):
        if not root.exists():
            continue
        for scenario_dir in sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")):
            payload, path = _read_scenario_manifest(scenario_dir.name, ctx=ctx)
            if payload:
                _append("scenario", scenario_dir.name, payload, path)
            if len(capabilities) + len(affordances) >= limit:
                return {"voice_capabilities": capabilities[:limit], "voice_affordances": affordances[:limit]}
    return {"voice_capabilities": capabilities[:limit], "voice_affordances": affordances[:limit]}


def _runtime_state_from_snapshot(snapshot: Mapping[str, Any], *, lookup_payload: Mapping[str, Any]) -> dict[str, Any]:
    ui = coerce_dict(snapshot.get("ui"))
    data = coerce_dict(snapshot.get("data"))
    registry = coerce_dict(snapshot.get("registry"))
    application = coerce_dict(ui.get("application"))
    catalog = coerce_dict(data.get("catalog"))
    installed = coerce_dict(data.get("installed"))
    teacher = coerce_dict(data.get("nlu_teacher"))
    merged = coerce_dict(registry.get("merged"))

    modal_rows = _mapping_collection_rows(application.get("modals")) + _mapping_collection_rows(merged.get("modals"))
    app_rows = _mapping_collection_rows(catalog.get("apps"), id_fields=("id", "app_id"))
    widget_rows = _mapping_collection_rows(catalog.get("widgets"), id_fields=("id", "widget_id"))
    node_rows = _mapping_collection_rows(data.get("nodes"), id_fields=("node_id", "id", "ref"))

    def _lookup_count(name: str) -> int:
        lookups = lookup_payload.get("lookups") if isinstance(lookup_payload.get("lookups"), Mapping) else {}
        rows = lookups.get(name)
        return len(rows) if isinstance(rows, list) else 0

    current_scenario = ui.get("current_scenario")
    catalog_apps: list[dict[str, Any]] = []
    for row in app_rows:
        item = _compact_registry_item(row, kind="app")
        if item.get("id"):
            catalog_apps.append(item)

    catalog_widgets: list[dict[str, Any]] = []
    for row in widget_rows:
        item = _compact_registry_item(row, kind="widget")
        if item.get("id"):
            catalog_widgets.append(item)

    return {
        "webspace_id": snapshot.get("webspace_id"),
        "current_scenario": current_scenario if isinstance(current_scenario, str) and current_scenario.strip() else None,
        "available_modal_ids": [
            str(item.get("id") or "").strip()
            for item in (_compact_registry_item(row, kind="modal") for row in modal_rows)
            if str(item.get("id") or "").strip()
        ][:150],
        "catalog_apps": catalog_apps[:150],
        "catalog_widgets": catalog_widgets[:150],
        "installed": {
            "apps": _as_text_list(installed.get("apps"), limit=150),
            "widgets": _as_text_list(installed.get("widgets"), limit=150),
            "removed_apps": _as_text_list(installed.get("removedApps") or installed.get("removed_apps"), limit=150),
            "removed_widgets": _as_text_list(installed.get("removedWidgets") or installed.get("removed_widgets"), limit=150),
        },
        "nodes": [
            {
                key: value
                for key, value in {
                    "id": row.get("id") or row.get("node_id") or row.get("ref"),
                    "label": row.get("label") or row.get("name") or row.get("display_name"),
                    "status": row.get("status") or row.get("state"),
                }.items()
                if value not in (None, "", [], {})
            }
            for row in node_rows[:80]
        ],
        "active_teacher_sessions": _active_teacher_sessions(teacher),
        "recent_errors": _recent_teacher_errors(teacher),
        "teacher_budget": dict(teacher.get("budget") or {}) if isinstance(teacher.get("budget"), Mapping) else {},
        "teacher_policies": dict(teacher.get("policies") or {}) if isinstance(teacher.get("policies"), Mapping) else {},
        "deferred_enrichment_queue": [
            {
                "id": item.get("id"),
                "ts": item.get("ts"),
                "status": item.get("status"),
                "request_id": item.get("request_id"),
                "reason": item.get("reason"),
                "log_id": item.get("log_id"),
            }
            for item in sorted(_as_list(teacher.get("deferred_enrichment_queue")), key=_event_ts, reverse=True)[:20]
        ],
        "lookup_counts": {
            "modal_id": _lookup_count("modal_id"),
            "app_id": _lookup_count("app_id"),
            "scenario_id": _lookup_count("scenario_id"),
            "node_ref": _lookup_count("node_ref"),
            "skill_id": _lookup_count("skill_id"),
        },
        "read_error": snapshot.get("read_error"),
    }


def _process_state_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    data = coerce_dict(snapshot.get("data"))
    teacher = coerce_dict(data.get("nlu_teacher"))
    events = sorted(_as_list(teacher.get("events")), key=_event_ts, reverse=True)
    workbench = _as_list(teacher.get("workbench_signals"))

    process_rows: list[dict[str, Any]] = []
    for source_key in ("jobs", "operations", "processes", "tasks"):
        source = data.get(source_key)
        for row in _mapping_collection_rows(source, id_fields=("id", "job_id", "operation_id", "task_id"))[:50]:
            process_rows.append(
                {
                    "source": f"data.{source_key}",
                    "id": row.get("id") or row.get("job_id") or row.get("operation_id") or row.get("task_id"),
                    "title": row.get("title") or row.get("name"),
                    "status": row.get("status") or row.get("state"),
                    "owner": row.get("owner") or row.get("skill") or row.get("scenario"),
                    "updated_at": row.get("updated_at") or row.get("ts"),
                }
            )

    return {
        "teacher_queue": {
            "pending_candidates": sum(1 for item in _as_list(teacher.get("candidates")) if item.get("status") == "pending"),
            "quarantined_candidates": sum(
                1 for item in _as_list(teacher.get("candidates")) if item.get("status") == "quarantined"
            ),
            "deferred_enrichment": len(_as_list(teacher.get("deferred_enrichment_queue"))),
            "active_confirmations": len(_active_teacher_sessions(teacher).get("pending_confirmations") or []),
            "active_clarifications": len(_active_teacher_sessions(teacher).get("clarification_sessions") or []),
        },
        "teacher_budget": dict(teacher.get("budget") or {}) if isinstance(teacher.get("budget"), Mapping) else {},
        "teacher_policies": dict(teacher.get("policies") or {}) if isinstance(teacher.get("policies"), Mapping) else {},
        "workbench_signals": workbench[:20],
        "recent_teacher_events": [
            {
                "ts": item.get("ts"),
                "kind": item.get("kind"),
                "request_id": item.get("request_id"),
                "title": item.get("title"),
                "subtitle": item.get("subtitle"),
            }
            for item in events[:20]
        ],
        "process_rows": process_rows[:100],
        "read_error": snapshot.get("read_error"),
    }


def _build_contextual_action_surface(
    *,
    webspace_id: str,
    lookup_payload: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    include_hints: bool,
    max_actions: int,
) -> dict[str, Any]:
    action_limit = max(1, int(max_actions))
    system_actions = _system_action_surface_rows()
    route_actions = _template_action_surface_rows(webspace_id=webspace_id, limit=action_limit)
    available_actions = (system_actions + route_actions)[:action_limit]
    developer_hints = _developer_hint_rows(limit=120) if include_hints else []
    runtime_state = _runtime_state_from_snapshot(snapshot, lookup_payload=lookup_payload)
    process_state = _process_state_from_snapshot(snapshot)
    voice_surface = _voice_surface_rows(runtime_state=runtime_state, limit=max(200, action_limit))
    voice_capabilities = voice_surface.get("voice_capabilities") if isinstance(voice_surface.get("voice_capabilities"), list) else []
    voice_affordances = voice_surface.get("voice_affordances") if isinstance(voice_surface.get("voice_affordances"), list) else []
    return {
        "ok": True,
        "surface_id": "adaos.nlu.contextual_action_surface.v1",
        "webspace_id": webspace_id,
        "runtime_state": runtime_state,
        "available_actions": available_actions,
        "voice_capabilities": voice_capabilities,
        "voice_affordances": voice_affordances,
        "voice_surface": {
            "surface_id": "adaos.nlu.voice_surface.v1",
            "voice_capabilities_count": len(voice_capabilities),
            "voice_affordances_count": len(voice_affordances),
        },
        "process_state": process_state,
        "developer_hints": developer_hints,
        "lookup_summary": list(lookup_payload.get("summary") or []) if isinstance(lookup_payload.get("summary"), list) else [],
        "fingerprint": _hash_payload(
            {
                "lookup": lookup_payload.get("fingerprint"),
                "runtime": runtime_state,
                "actions": available_actions,
                "voice_capabilities": voice_capabilities,
                "voice_affordances": voice_affordances,
                "hints": developer_hints,
            }
        ),
        "authoring_boundaries": {
            "mode": "read_only_context",
            "side_effects": "none",
            "dispatch": False,
            "training_mutation": False,
            "llm_direct_sdk_calls": False,
        },
    }


def get_contextual_action_surface(
    *,
    webspace_id: str | None = None,
    include_live: bool = True,
    include_hints: bool = True,
    max_actions: int = 200,
) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    lookup_payload = get_desktop_registry_lookup(webspace_id=ws, include_live=include_live)
    snapshot = _read_yjs_context_snapshot(ws) if include_live else {"webspace_id": ws, "ui": {}, "data": {}, "registry": {}}
    return _build_contextual_action_surface(
        webspace_id=ws,
        lookup_payload=lookup_payload,
        snapshot=snapshot,
        include_hints=include_hints,
        max_actions=max_actions,
    )


async def get_contextual_action_surface_async(
    *,
    webspace_id: str | None = None,
    include_live: bool = True,
    include_hints: bool = True,
    max_actions: int = 200,
) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    lookup_payload = await get_desktop_registry_lookup_async(webspace_id=ws, include_live=include_live)
    snapshot = (
        await _read_yjs_context_snapshot_async(ws)
        if include_live
        else {"webspace_id": ws, "ui": {}, "data": {}, "registry": {}}
    )
    return _build_contextual_action_surface(
        webspace_id=ws,
        lookup_payload=lookup_payload,
        snapshot=snapshot,
        include_hints=include_hints,
        max_actions=max_actions,
    )


def _compile_regex(pattern: str, *, text: str | None = None) -> dict[str, Any]:
    try:
        compiled = re.compile(pattern, re.IGNORECASE | re.UNICODE)
    except re.error as exc:
        return {"ok": False, "status": "invalid_regex", "error": str(exc)}
    if text is None or not str(text).strip():
        return {"ok": True, "status": "compiled", "slots": {}}
    match = compiled.search(str(text))
    if not match:
        return {"ok": False, "status": "source_text_miss", "slots": {}}
    slots = {key: value.strip() if isinstance(value, str) else value for key, value in match.groupdict().items() if value is not None}
    return {"ok": True, "status": "source_text_matched", "matched": match.group(0), "slots": slots}


def _target_descriptor_for_preview(target: Mapping[str, Any]) -> dict[str, Any]:
    target_type = str(target.get("type") or "").strip()
    target_id = str(target.get("id") or "").strip()
    if target_type == "skill":
        return describe_skill_nlu(target_id)
    if target_type == "scenario":
        return describe_scenario_nlu(target_id)
    if target_type == "system_action":
        from adaos.services.nlu.system_actions_catalog import find_system_action_by_id, find_system_action_by_intent

        action = find_system_action_by_id(target_id) or find_system_action_by_intent(target_id)
        if action:
            return {
                "ok": True,
                "owner": {"type": "system_action", "id": action.get("id")},
                "fingerprint": _hash_payload(action),
                "system_action": action,
                "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False},
            }
        return {"ok": False, "status": "not_found", "owner": {"type": "system_action", "id": target_id}}
    return {"ok": False, "status": "unsupported_target", "owner": {"type": target_type, "id": target_id}}


def _template_duplicates(
    *,
    owner_type: str,
    owner_id: str,
    intent: str,
    kind: str,
    text: str | None = None,
    pattern: str | None = None,
) -> list[dict[str, Any]]:
    templates = list_nlu_templates(owner_type=owner_type, owner_id=owner_id, include_system_actions=owner_type == "system_action")
    rows = templates.get("templates") if isinstance(templates.get("templates"), list) else []
    duplicates: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("intent") or "") != intent:
            continue
        if str(item.get("kind") or "") != kind:
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
        if kind in {"example", "system_action_example"} and text and str(payload.get("text") or "").strip() == text.strip():
            duplicates.append({"template_id": item.get("id"), "fingerprint": item.get("fingerprint"), "kind": kind})
        if kind == "regex_rule" and pattern and str(payload.get("pattern") or "").strip() == pattern.strip():
            duplicates.append({"template_id": item.get("id"), "fingerprint": item.get("fingerprint"), "kind": kind})
    return duplicates


def preview_template_patch(
    *,
    webspace_id: str | None = None,
    operation: str,
    target: Mapping[str, Any],
    intent: str,
    text: str | None = None,
    pattern: str | None = None,
    slots: Mapping[str, Any] | None = None,
    base_fingerprint: str | None = None,
) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    op = str(operation or "").strip()
    intent_token = str(intent or "").strip()
    target_obj = {"type": str((target or {}).get("type") or "").strip(), "id": str((target or {}).get("id") or "").strip()}
    target_type = target_obj["type"]
    target_id = target_obj["id"]
    checks: list[dict[str, Any]] = []

    descriptor = _target_descriptor_for_preview(target_obj)
    target_ok = bool(descriptor.get("ok"))
    checks.append({"name": "target_exists", "ok": target_ok, "status": descriptor.get("status") or ("found" if target_ok else "not_found")})
    descriptor_fingerprint = descriptor.get("fingerprint") if isinstance(descriptor.get("fingerprint"), str) else None
    if base_fingerprint:
        fresh = bool(descriptor_fingerprint and base_fingerprint == descriptor_fingerprint)
        checks.append(
            {
                "name": "base_fingerprint",
                "ok": fresh,
                "status": "fresh" if fresh else "stale",
                "expected": descriptor_fingerprint,
                "actual": base_fingerprint,
            }
        )

    if not intent_token:
        checks.append({"name": "intent", "ok": False, "status": "missing"})
    if op not in {"add_regex_rule", "save_example"}:
        checks.append({"name": "operation", "ok": False, "status": "unsupported"})

    duplicates: list[dict[str, Any]] = []
    regex_preview: dict[str, Any] | None = None
    normalized_patch: dict[str, Any] = {
        "operation": op,
        "target": target_obj,
        "intent": intent_token,
        "slots": dict(slots or {}) if isinstance(slots, Mapping) else {},
    }

    if op == "add_regex_rule":
        if target_type not in {"skill", "scenario"}:
            checks.append({"name": "target_mutability", "ok": False, "status": "regex_rules_require_skill_or_scenario"})
        pattern_token = str(pattern or "").strip()
        if not pattern_token:
            checks.append({"name": "pattern", "ok": False, "status": "missing"})
        else:
            regex_preview = _compile_regex(pattern_token, text=text)
            checks.append({"name": "regex_compile", "ok": regex_preview["status"] != "invalid_regex", "status": regex_preview["status"], "error": regex_preview.get("error")})
            if text:
                checks.append({"name": "source_text_match", "ok": bool(regex_preview.get("ok")), "status": regex_preview.get("status")})
            if target_type and target_id and intent_token:
                duplicates = _template_duplicates(
                    owner_type=target_type,
                    owner_id=target_id,
                    intent=intent_token,
                    kind="regex_rule",
                    pattern=pattern_token,
                )
                checks.append({"name": "duplicate_regex", "ok": not duplicates, "status": "duplicate" if duplicates else "unique"})
            normalized_patch["regex_rule"] = {"intent": intent_token, "pattern": pattern_token}
    elif op == "save_example":
        example = str(text or "").strip()
        if target_type not in {"skill", "scenario", "system_action"}:
            checks.append({"name": "target_mutability", "ok": False, "status": "examples_require_skill_scenario_or_system_action"})
        if not example:
            checks.append({"name": "example", "ok": False, "status": "missing"})
        elif target_type and target_id and intent_token:
            duplicates = _template_duplicates(
                owner_type=target_type,
                owner_id=target_id,
                intent=intent_token,
                kind="system_action_example" if target_type == "system_action" else "example",
                text=example,
            )
            checks.append({"name": "duplicate_example", "ok": not duplicates, "status": "duplicate" if duplicates else "unique"})
        normalized_patch["example"] = example

    ok = bool(checks) and all(bool(item.get("ok")) for item in checks)
    status = "ready" if ok else "blocked"
    return {
        "ok": ok,
        "status": status,
        "webspace_id": ws,
        "operation": op,
        "target": target_obj,
        "intent": intent_token,
        "checks": checks,
        "duplicates": duplicates,
        "regex_preview": regex_preview,
        "normalized_patch": normalized_patch,
        "target_fingerprint": descriptor_fingerprint,
        "authoring_boundaries": {
            "side_effects": "none",
            "dispatch": False,
            "training_mutation": False,
            "dry_run": True,
        },
    }


def _find_action_for_preview(*, action_id: str | None, intent: str | None, host_action: str | None) -> dict[str, Any] | None:
    from adaos.services.nlu.system_actions_catalog import describe_system_actions, find_system_action_by_id, find_system_action_by_intent

    if action_id:
        found = find_system_action_by_id(action_id)
        if found:
            return found
    if intent:
        found = find_system_action_by_intent(intent)
        if found:
            return found
    if host_action:
        token = str(host_action or "").strip()
        for action in describe_system_actions():
            if str(action.get("action") or "").strip() == token:
                return action
    return None


def _lookup_contains(lookup_payload: Mapping[str, Any], lookup: str, value: Any) -> bool | None:
    token = str(value or "").strip()
    if not token or token.startswith("$"):
        return None
    token_key = re.sub(r"\s+", " ", token.casefold()).strip()
    lookups = lookup_payload.get("lookups") if isinstance(lookup_payload.get("lookups"), Mapping) else {}
    rows = lookups.get(lookup) if isinstance(lookups.get(lookup), list) else []
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        canonical = str(item.get("value") or "").strip()
        labels = item.get("labels") if isinstance(item.get("labels"), list) else []
        values = [canonical, *(str(label or "").strip() for label in labels)]
        if any(re.sub(r"\s+", " ", candidate.casefold()).strip() == token_key for candidate in values if candidate):
            return True
    return False


def preview_interface_action(
    *,
    webspace_id: str | None = None,
    action_id: str | None = None,
    intent: str | None = None,
    host_action: str | None = None,
    params: Mapping[str, Any] | None = None,
    lookup_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    action = _find_action_for_preview(action_id=action_id, intent=intent, host_action=host_action)
    params_obj = dict(params or {}) if isinstance(params, Mapping) else {}
    checks: list[dict[str, Any]] = []
    if not action:
        checks.append({"name": "action_exists", "ok": False, "status": "not_found"})
        return {
            "ok": False,
            "status": "blocked",
            "webspace_id": ws,
            "checks": checks,
            "would_dispatch": None,
            "authoring_boundaries": {"side_effects": "none", "dispatch": False, "training_mutation": False, "dry_run": True},
        }

    checks.append({"name": "action_exists", "ok": True, "status": "found"})
    slots = action.get("slots") if isinstance(action.get("slots"), Mapping) else {}
    missing_slots: list[str] = []
    for slot_name, slot_spec in slots.items():
        if not isinstance(slot_name, str):
            continue
        required = bool(slot_spec.get("required")) if isinstance(slot_spec, Mapping) else False
        if required and not params_obj.get(slot_name):
            missing_slots.append(slot_name)
    checks.append({"name": "required_slots", "ok": not missing_slots, "status": "complete" if not missing_slots else "missing", "missing": missing_slots})

    if lookup_payload is None:
        include_live = True
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            include_live = True
        else:
            # This sync preview API can be called from async request handlers or
            # tests. Avoid sync YDoc reads inside an active event loop; async
            # Apply uses preview_interface_action_async for the live path.
            include_live = False
        lookup_payload = get_desktop_registry_lookup(webspace_id=ws, include_live=include_live)
    for lookup in ("modal_id", "scenario_id", "app_id", "node_ref", "skill_id", "webspace_id"):
        if lookup not in params_obj:
            continue
        found = _lookup_contains(lookup_payload, lookup, params_obj.get(lookup))
        if found is None:
            checks.append({"name": f"lookup.{lookup}", "ok": True, "status": "symbolic_or_empty"})
        else:
            checks.append({"name": f"lookup.{lookup}", "ok": bool(found), "status": "found" if found else "not_found", "value": params_obj.get(lookup)})

    ok = all(bool(item.get("ok")) for item in checks)
    host_event = str(action.get("action") or "").strip()
    would_dispatch = {
        "type": "callHost",
        "target": host_event,
        "params": {**params_obj, "webspace_id": params_obj.get("webspace_id") or ws},
    }
    return {
        "ok": ok,
        "status": "ready" if ok else "blocked",
        "webspace_id": ws,
        "action": action,
        "checks": checks,
        "would_dispatch": would_dispatch,
        "lookup_fingerprint": lookup_payload.get("fingerprint"),
        "authoring_boundaries": {
            "side_effects": "none",
            "dispatch": False,
            "training_mutation": False,
            "dry_run": True,
        },
    }


async def preview_interface_action_async(
    *,
    webspace_id: str | None = None,
    action_id: str | None = None,
    intent: str | None = None,
    host_action: str | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _webspace_id(webspace_id)
    lookup_payload = await get_desktop_registry_lookup_async(webspace_id=ws, include_live=True)
    return preview_interface_action(
        webspace_id=ws,
        action_id=action_id,
        intent=intent,
        host_action=host_action,
        params=params,
        lookup_payload=lookup_payload,
    )
