from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

from adaos.sdk.core.decorators import subscribe, tool


SKILL_ID = "builder_skill"
DIALOG_CHANNEL_ID = "builder"
AGENT_ID = "agent:builder_skill:builder"
AGENT_LABEL = "\u0421\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c"
SESSIONS_KEY = "builder_skill.sessions"
CURRENT_KEY = "builder_skill.current_session"
MAX_SESSIONS = 50
WORKBENCH_REFRESH_TOPIC = "builder.workbench.ensure_requested"

_FALLBACK_MEMORY: dict[str, Any] = {}


def _now() -> float:
    return time.time()


def _webspace_id(value: str | None = None, _meta: Mapping[str, Any] | None = None) -> str:
    token = str(value or "").strip()
    if token:
        return token
    if isinstance(_meta, Mapping):
        for key in ("webspace_id", "workspace_id"):
            raw = _meta.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return "default"


def _source_webspace_id(value: str | None = None, _meta: Mapping[str, Any] | None = None) -> str:
    if isinstance(_meta, Mapping):
        for key in ("source_webspace_id", "builder_source_webspace_id"):
            raw = _meta.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    token = _webspace_id(value, _meta)
    if token.endswith("-dev") and len(token) > 4:
        return token[:-4]
    return token


def _paired_dev_webspace_id(source_webspace_id: str) -> str | None:
    try:
        from adaos.services.builder.workbench import dev_webspace_id_for_source

        return dev_webspace_id_for_source(source_webspace_id)
    except Exception:
        source = str(source_webspace_id or "").strip()
        return f"{source}-dev" if source else None


def _scoped_key(base: str, webspace_id: str) -> str:
    return f"{base}.{webspace_id or 'default'}"


def _mem_get(key: str, default: Any = None) -> Any:
    try:
        from adaos.sdk.data import skill_memory

        return skill_memory.get(key, default)
    except Exception:
        return copy.deepcopy(_FALLBACK_MEMORY.get(key, default))


def _mem_set(key: str, value: Any) -> None:
    try:
        from adaos.sdk.data import skill_memory

        skill_memory.set(key, value)
    except Exception:
        _FALLBACK_MEMORY[key] = copy.deepcopy(value)


def _sessions(webspace_id: str) -> dict[str, dict[str, Any]]:
    raw = _mem_get(_scoped_key(SESSIONS_KEY, webspace_id), {})
    return copy.deepcopy(raw) if isinstance(raw, dict) else {}


def _save_sessions(webspace_id: str, sessions: Mapping[str, Mapping[str, Any]]) -> None:
    items = sorted((dict(v) for v in sessions.values()), key=lambda item: float(item.get("updated_at") or 0), reverse=True)
    trimmed = {str(item["id"]): item for item in items[:MAX_SESSIONS] if item.get("id")}
    _mem_set(_scoped_key(SESSIONS_KEY, webspace_id), trimmed)


def _current_session_id(webspace_id: str) -> str | None:
    raw = _mem_get(_scoped_key(CURRENT_KEY, webspace_id))
    token = str(raw or "").strip()
    return token or None


def _set_current_session_id(webspace_id: str, session_id: str) -> None:
    _mem_set(_scoped_key(CURRENT_KEY, webspace_id), str(session_id or "").strip())


def _hash_suffix(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="ignore")).hexdigest()[:8]


def _scenario_id_from_idea(idea: str) -> str:
    lowered = str(idea or "").lower()
    if "shopping" in lowered or "shop" in lowered or "\u043f\u043e\u043a\u0443\u043f" in lowered:
        base = "shopping_list"
    elif "todo" in lowered or "\u0437\u0430\u0434\u0430\u0447" in lowered:
        base = "todo_list"
    else:
        ascii_base = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
        base = ascii_base[:40].strip("_") or "prototype_app"
    return f"{base}_{_hash_suffix(idea)}"


def _conversation_id(webspace_id: str) -> str:
    return f"conv.skill.{SKILL_ID}.default.{webspace_id or 'default'}"


def _builder_topic_ref(
    webspace_id: str,
    *,
    session: Mapping[str, Any] | None = None,
    binding: Mapping[str, Any] | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(_meta or {})
    existing_topic = meta.get("builder_topic") if isinstance(meta.get("builder_topic"), Mapping) else {}
    thread_id = str(meta.get("thread_id") or meta.get("conversation_thread_id") or meta.get("conversation_topic_id") or "").strip()
    topic_id = str(meta.get("topic_id") or "").strip()
    if thread_id:
        topic = {k: v for k, v in dict(existing_topic or {}).items() if v is not None}
        topic.setdefault("schema", "adaos.conversation.topic_ref.v1")
        topic.setdefault("thread_id", thread_id)
        topic.setdefault("topic_id", topic_id or thread_id)
        topic.setdefault("topic_kind", "builder_runtime")
        topic.setdefault("webspace_id", webspace_id)
        topic.setdefault("source_webspace_id", webspace_id)
        topic.setdefault("conversation_id", _conversation_id(webspace_id))
        topic.setdefault("channel_id", DIALOG_CHANNEL_ID)
        topic.setdefault("owner", f"skill:{SKILL_ID}")
        return topic
    session = session if isinstance(session, Mapping) else {}
    binding = binding if isinstance(binding, Mapping) else {}
    try:
        from adaos.services.conversation_links import ensure_builder_topic

        return ensure_builder_topic(
            webspace_id,
            active_draft_id=str(session.get("draft_id") or binding.get("active_draft_id") or "").strip() or None,
            scenario_id=str(session.get("scenario_id") or binding.get("runtime_scenario_id") or "").strip() or None,
            dev_webspace_id=str(binding.get("dev_webspace_id") or _paired_dev_webspace_id(webspace_id) or "").strip() or None,
        )
    except Exception:
        token = str(session.get("draft_id") or session.get("scenario_id") or binding.get("runtime_scenario_id") or "default").strip()
        token = re.sub(r"[^A-Za-z0-9_.:-]+", ".", token).strip(".") or "default"
        return {
            "schema": "adaos.conversation.topic_ref.v1",
            "topic_id": f"builder:{webspace_id}:{token}",
            "thread_id": f"thread.builder.{webspace_id}.{token}",
            "topic_kind": "builder_runtime",
            "webspace_id": webspace_id,
            "source_webspace_id": webspace_id,
            "active_draft_id": str(session.get("draft_id") or binding.get("active_draft_id") or "").strip() or None,
            "scenario_id": str(session.get("scenario_id") or binding.get("runtime_scenario_id") or "").strip() or None,
            "dev_webspace_id": str(binding.get("dev_webspace_id") or _paired_dev_webspace_id(webspace_id) or "").strip() or None,
            "conversation_id": _conversation_id(webspace_id),
            "channel_id": DIALOG_CHANNEL_ID,
            "owner": f"skill:{SKILL_ID}",
            "stored": False,
        }


def _dialog_state(webspace_id: str, *, topic_ref: Mapping[str, Any] | None = None) -> dict[str, Any]:
    topic = dict(topic_ref or {}) if isinstance(topic_ref, Mapping) else {}
    state = {
        "state": "active",
        "dialog_channel_id": DIALOG_CHANNEL_ID,
        "conversation_id": _conversation_id(webspace_id),
        "owner": f"skill:{SKILL_ID}",
        "surface": f"skill:{SKILL_ID}",
        "default_tool": f"{SKILL_ID}.chat",
        "active_agent_id": AGENT_ID,
        "active_agent_label": AGENT_LABEL,
        "active_agent": {
            "id": AGENT_ID,
            "label": AGENT_LABEL,
            "owner": f"skill:{SKILL_ID}",
            "kind": "skill_agent",
            "skill_id": SKILL_ID,
            "channel_id": DIALOG_CHANNEL_ID,
            "memory_scope": "skill_user",
            "gender": "male",
            "voice": "ru-male",
            "icon": "construct-outline",
            "voice_profile": {
                "gender": "male",
                "voice": "ru-male",
                "lang": "ru-RU",
                "browser_voice_hint": "ru-male",
            },
        },
        "memory": {
            "status": "skill_memory_compat",
            "scopes": ["skill_user", "conversation"],
            "owner": f"skill:{SKILL_ID}",
            "active_agent_id": AGENT_ID,
        },
    }
    if topic:
        state["thread_id"] = str(topic.get("thread_id") or "").strip() or None
        state["topic_id"] = str(topic.get("topic_id") or "").strip() or None
        state["topic"] = {k: v for k, v in topic.items() if k != "stored"}
    return state


def _chat_meta(
    _meta: Mapping[str, Any] | None,
    *,
    webspace_id: str,
    session: Mapping[str, Any] | None = None,
    binding: Mapping[str, Any] | None = None,
    topic_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(_meta or {})
    meta.pop("webspace_ids", None)
    meta["webspace_id"] = webspace_id
    meta.setdefault("source_webspace_id", _source_webspace_id(webspace_id, _meta))
    meta.setdefault("route_id", "voice_chat")
    meta.setdefault("dialog_channel_id", DIALOG_CHANNEL_ID)
    meta["conversation_id"] = _conversation_id(webspace_id)
    meta["conversation_owner"] = f"skill:{SKILL_ID}"
    meta.setdefault("active_agent_id", AGENT_ID)
    meta.setdefault("active_agent_label", AGENT_LABEL)
    meta.setdefault("active_agent_gender", "male")
    meta.setdefault("active_agent_voice", "ru-male")
    meta.setdefault("active_agent_icon", "construct-outline")
    topic = dict(topic_ref or {}) if isinstance(topic_ref, Mapping) else _builder_topic_ref(
        webspace_id,
        session=session,
        binding=binding,
        _meta=meta,
    )
    thread_id = str(topic.get("thread_id") or "").strip()
    topic_id = str(topic.get("topic_id") or "").strip()
    if thread_id:
        meta.setdefault("thread_id", thread_id)
        meta.setdefault("conversation_thread_id", thread_id)
        meta.setdefault("conversation_topic_id", thread_id)
    if topic_id:
        meta.setdefault("topic_id", topic_id)
    if topic:
        meta.setdefault("builder_topic", {k: v for k, v in topic.items() if k != "stored"})
    return meta


def _source_refs(
    *,
    webspace_id: str,
    session: Mapping[str, Any],
    _meta: Mapping[str, Any] | None = None,
    patch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    meta = _chat_meta(_meta, webspace_id=webspace_id, session=session)
    refs: dict[str, Any] = {
        "conversation_id": meta.get("conversation_id") or _conversation_id(webspace_id),
        "dialog_channel_id": DIALOG_CHANNEL_ID,
        "owner": f"skill:{SKILL_ID}",
        "session_id": session.get("id"),
        "scenario_id": session.get("scenario_id"),
    }
    for key in ("thread_id", "topic_id", "turn_trace_id", "request_id", "message_id", "input_event_kind"):
        value = str(meta.get(key) or "").strip()
        if value:
            refs[key] = value
    draft_id = str(session.get("draft_id") or "").strip()
    if draft_id:
        refs["draft_id"] = draft_id
    if patch:
        patch_id = str(patch.get("id") or "").strip()
        if patch_id:
            refs["patch_id"] = patch_id
        operation = str(patch.get("operation") or "").strip()
        if operation:
            refs["operation"] = operation
    return refs


def _publish_review_pending_action(
    *,
    webspace_id: str,
    session: Mapping[str, Any],
    request_text: str,
    kind: str,
    summary: str,
    _meta: Mapping[str, Any] | None = None,
    patch: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    refs = _source_refs(webspace_id=webspace_id, session=session, _meta=_meta, patch=patch)
    action_input: dict[str, Any] = {
        "kind": kind,
        "request_text": request_text,
        "side_effect_class": "local_write",
    }
    if patch:
        action_input.update({key: value for key, value in dict(patch).items() if key in {"target", "operation", "summary", "side_effect_class"}})
    try:
        from adaos.services.conversation_safety import classify_action_risk

        action_risk = classify_action_risk(action_input)
    except Exception:
        action_risk = {
            "schema": "adaos.conversation.action_risk.v1",
            "risk_class": "local_write",
            "approval_required": False,
            "mandatory_review": False,
            "reasons": [{"risk_class": "local_write", "reason": "fallback"}],
        }
    try:
        from adaos.services.pending_actions import publish_pending_action

        return publish_pending_action(
            webspace_id=webspace_id,
            kind=kind,
            title="Review Builder change",
            summary=summary,
            request_text=request_text,
            producer={"type": "skill", "skill_id": SKILL_ID},
            owner_scope={
                "owner": f"skill:{SKILL_ID}",
                "webspace_id": webspace_id,
                "conversation_id": refs.get("conversation_id"),
                "thread_id": refs.get("thread_id"),
            },
            domain_ref={
                "skill_id": SKILL_ID,
                "session_id": refs.get("session_id"),
                "scenario_id": refs.get("scenario_id"),
                "draft_id": refs.get("draft_id"),
                "patch_id": refs.get("patch_id"),
                "conversation_id": refs.get("conversation_id"),
                "thread_id": refs.get("thread_id"),
            },
            actions=["preview", "approve", "refuse", "postpone"],
            response_topic="builder.pending_action.response",
            payload_ref={
                "kind": "builder.session",
                "session_id": refs.get("session_id"),
                "scenario_id": refs.get("scenario_id"),
            },
            metadata={
                "source": "builder_skill",
                "source_refs": refs,
                "patch": dict(patch or {}),
                "approval_policy": {
                    "decision": "human_review_required",
                    "reason": "builder_review_pending_action",
                    "action_risk": action_risk,
                },
            },
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": "pending_action_publish_failed",
            "detail": f"{type(exc).__name__}: {exc}",
            "metadata": {"source_refs": refs},
        }


def _safe_emit_chat(
    text: str,
    *,
    webspace_id: str,
    _meta: Mapping[str, Any] | None = None,
    session: Mapping[str, Any] | None = None,
    binding: Mapping[str, Any] | None = None,
    topic_ref: Mapping[str, Any] | None = None,
) -> None:
    try:
        from adaos.sdk.io.out import chat_append

        source_ws = _source_webspace_id(webspace_id, _meta)
        targets = [source_ws]
        dev_ws = _paired_dev_webspace_id(source_ws)
        if dev_ws and dev_ws not in targets:
            targets.append(dev_ws)
        for target in targets:
            chat_append(
                text,
                from_="hub",
                _meta=_chat_meta(_meta, webspace_id=target, session=session, binding=binding, topic_ref=topic_ref),
            )
    except Exception:
        return


def _event_payload(evt: Any) -> dict[str, Any]:
    payload = getattr(evt, "payload", None)
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(evt, Mapping):
        return dict(evt)
    return {}


@subscribe("builder.pending_action.response")
async def _on_builder_pending_action_response(evt: Any) -> None:
    payload = _event_payload(evt)
    action = payload.get("pending_action") if isinstance(payload.get("pending_action"), Mapping) else {}
    response = payload.get("response") if isinstance(payload.get("response"), Mapping) else {}
    domain_ref = payload.get("domain_ref") if isinstance(payload.get("domain_ref"), Mapping) else action.get("domain_ref") if isinstance(action, Mapping) else {}
    response_action_id = str(payload.get("response_action_id") or response.get("response_action_id") or "").strip()
    webspace_id = _source_webspace_id(str(payload.get("webspace_id") or action.get("webspace_id") or ""), None)
    session_id = str(domain_ref.get("session_id") or "").strip()
    patch_id = str(domain_ref.get("patch_id") or "").strip()
    pending_action_id = str(payload.get("pending_action_id") or action.get("id") or "").strip()
    if not webspace_id or response_action_id not in {"approve", "refuse"}:
        return
    session = _load_session(webspace_id, session_id or None)
    if not session:
        return
    patches = [dict(item) for item in session.get("patches", []) if isinstance(item, Mapping)]
    matched = False
    for patch in patches:
        if patch_id and str(patch.get("id") or "") == patch_id:
            matched = True
        elif pending_action_id and str(patch.get("pending_action_id") or "") == pending_action_id:
            matched = True
        else:
            continue
        patch["review_status"] = "approved" if response_action_id == "approve" else "refused"
        patch["reviewed_at"] = _now()
        patch["review_response_id"] = pending_action_id or None
        if response_action_id == "approve":
            patch["status"] = "applied"
        break
    if not matched:
        return
    session["patches"] = patches
    if pending_action_id and str(session.get("pending_action_id") or "") == pending_action_id:
        session.pop("pending_action_id", None)
    session["user_summary"] = _draft_user_summary(session)
    preview = _preview_state(session=session)
    _write_webui(str(session.get("artifact_root") or ""), preview)
    session["preview_state"] = preview
    _save_session(webspace_id, session)
    workbench = _ensure_workbench(webspace_id, session=session, preview_state=preview)
    binding = workbench.get("binding") if isinstance(workbench.get("binding"), Mapping) else {}
    topic = _builder_topic_ref(webspace_id, session=session, binding=binding)
    if response_action_id == "approve":
        message = f"{AGENT_LABEL}: \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f {session.get('scenario_id')} \u0443\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u044b."
    else:
        message = (
            f"{AGENT_LABEL}: \u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0438\u0435 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0439 {session.get('scenario_id')} "
            "\u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u043d\u043e. Rollback \u0434\u043b\u044f \u044d\u0442\u043e\u0439 \u0432\u0435\u0442\u043a\u0438 \u0435\u0449\u0435 \u043d\u0435 \u0440\u0435\u0430\u043b\u0438\u0437\u043e\u0432\u0430\u043d."
        )
    _safe_emit_chat(message, webspace_id=webspace_id, session=session, binding=binding, topic_ref=topic)


def _build_fields(idea: str) -> list[dict[str, Any]]:
    lowered = str(idea or "").lower()
    if "shopping" in lowered or "\u043f\u043e\u043a\u0443\u043f" in lowered:
        return [
            {"id": "item", "type": "string", "label": "\u0422\u043e\u0432\u0430\u0440", "required": True},
            {"id": "quantity", "type": "number", "label": "\u041a\u043e\u043b-\u0432\u043e", "required": False},
            {"id": "category", "type": "string", "label": "\u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f", "required": False},
            {"id": "done", "type": "boolean", "label": "\u041a\u0443\u043f\u043b\u0435\u043d\u043e", "required": False},
        ]
    return [
        {"id": "title", "type": "string", "label": "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435", "required": True},
        {"id": "notes", "type": "string", "label": "\u0417\u0430\u043c\u0435\u0442\u043a\u0438", "required": False},
        {"id": "status", "type": "string", "label": "\u0421\u0442\u0430\u0442\u0443\u0441", "required": False},
    ]


def _component_for_field(field: Mapping[str, Any]) -> dict[str, Any]:
    field_type = str(field.get("type") or "string")
    component_type = (
        "checkbox"
        if field_type == "boolean"
        else "number_input"
        if field_type == "number"
        else "date_input"
        if field_type == "date"
        else "text_input"
    )
    return {
        "id": f"input_{field['id']}",
        "type": component_type,
        "label": field.get("label") or field["id"],
        "binding": f"draft.{field['id']}",
        "visible": True,
    }


def _preview_state(*, session: Mapping[str, Any]) -> dict[str, Any]:
    fields = [dict(item) for item in session.get("fields", []) if isinstance(item, Mapping)]
    filters = [dict(item) for item in session.get("filters", []) if isinstance(item, Mapping)]
    datasource_id = str(session.get("datasource_id") or "items")
    table_columns = [{"field": item["id"], "label": item.get("label") or item["id"]} for item in fields]
    stored_mock_rows = session.get("mock_rows")
    mock_rows = [dict(item) for item in stored_mock_rows if isinstance(item, Mapping)] if isinstance(stored_mock_rows, list) else _mock_rows(fields)
    action_position = str(session.get("form_action_position") or "").strip().lower()
    ui = {
        "schema": "adaos.declarative_ui.v1",
        "id": str(session.get("scenario_id") or "prototype"),
        "type": "page",
        "title": session.get("title") or "\u041f\u0440\u043e\u0442\u043e\u0442\u0438\u043f",
        "children": [
            {
                "id": "editor",
                "type": "section",
                "label": "\u0412\u0432\u043e\u0434",
                "children": [_component_for_field(item) for item in fields],
                "action_position": "top" if action_position == "top" else "bottom",
                "actions": [{"id": "add_item", "type": "button", "label": "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c"}],
            },
            {
                "id": "items_table",
                "type": "table",
                "label": "\u0421\u043f\u0438\u0441\u043e\u043a",
                "binding": datasource_id,
                "columns": table_columns,
                "visible": True,
            },
        ],
    }
    if session.get("card_view"):
        ui["children"].append(
            {
                "id": "items_cards",
                "type": "card_list",
                "label": "\u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0438",
                "binding": datasource_id,
                "title": f"{{{{{fields[0]['id']}}}}}" if fields else "{{title}}",
                "subtitle": f"{{{{{fields[1]['id']}}}}}" if len(fields) > 1 else "",
                "visible": True,
            }
        )
    return {
        "session_id": session.get("id"),
        "title": session.get("title"),
        "current_ui": ui,
        "datasources": [
            {
                "id": datasource_id,
                "type": "internal_crud",
                "entity": "item",
                "fields": fields,
                "operations": ["create", "read", "update", "delete"],
            }
        ],
        "mock_data": {datasource_id: mock_rows},
        "filters": filters,
        "form_action_position": "top" if action_position == "top" else "bottom",
        "pending_patches": [item for item in session.get("patches", []) if item.get("status") == "proposed"],
        "user_summary": session.get("user_summary") if isinstance(session.get("user_summary"), Mapping) else _draft_user_summary(session),
        "version": str(session.get("version") or "v1"),
    }


def _mock_rows(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for index in range(1, 4):
        row: dict[str, Any] = {}
        for field in fields:
            field_id = str(field.get("id") or "")
            field_type = str(field.get("type") or "string")
            if field_type == "number":
                row[field_id] = index
            elif field_type == "boolean":
                row[field_id] = index == 1
            elif field_type == "date":
                row[field_id] = f"2026-07-0{index}"
            else:
                row[field_id] = f"{field.get('label') or field_id} {index}"
        rows.append(row)
    return rows


def _food_mock_rows(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    products = [
        {"item": "\u041c\u043e\u043b\u043e\u043a\u043e", "quantity": 2, "unit": "\u043b", "availability": "\u0432 \u043d\u0430\u043b\u0438\u0447\u0438\u0438", "category": "\u041c\u043e\u043b\u043e\u0447\u043d\u044b\u0435", "done": False, "price": 89.9},
        {"item": "\u0425\u043b\u0435\u0431", "quantity": 1, "unit": "\u0448\u0442", "availability": "\u0432 \u043d\u0430\u043b\u0438\u0447\u0438\u0438", "category": "\u0411\u0430\u043a\u0430\u043b\u0435\u044f", "done": True, "price": 54.0},
        {"item": "\u042f\u0431\u043b\u043e\u043a\u0438", "quantity": 6, "unit": "\u043a\u0433", "availability": "\u043d\u0435\u0442", "category": "\u0424\u0440\u0443\u043a\u0442\u044b", "done": False, "price": 129.5},
    ]
    dates = ["2026-07-01", "2026-07-02", "2026-07-03"]
    rows: list[dict[str, Any]] = []
    for index, product in enumerate(products, start=1):
        row: dict[str, Any] = {}
        for field in fields:
            field_id = str(field.get("id") or "")
            field_type = str(field.get("type") or "string")
            if field_id in product:
                row[field_id] = product[field_id]
            elif field_id in {"title", "name", "product"}:
                row[field_id] = product["item"]
            elif field_type == "number":
                row[field_id] = index
            elif field_type == "boolean":
                row[field_id] = index == 2
            elif field_type == "date" or field_id == "date":
                row[field_id] = dates[index - 1]
            else:
                row[field_id] = str(field.get("label") or field_id or "value")
        rows.append(row)
    return rows


def _write_webui(artifact_root: str | None, preview_state: Mapping[str, Any]) -> None:
    if not artifact_root:
        return
    root = Path(artifact_root)
    if not root.exists():
        return
    payload = {
        "schema": "adaos.webui.prototype.v1",
        "generated_by": SKILL_ID,
        "preview_state": preview_state,
        "nlu": {
            "llm_hints": {
                "aliases": {"app_id": {"prototype": [str(preview_state.get("title") or "prototype")]}},
                "primary_actions": [
                    {
                        "intent": "builder.chat",
                        "notes": "Prototype UI is edited through builder_skill.chat.",
                        "supported_operations": [
                            "add_field",
                            "remove_field",
                            "update_mock_data",
                            "change_view_representation",
                            "move_form_action",
                            "set_checkbox_column",
                        ],
                    }
                ],
            }
        },
    }
    (root / "webui.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_scenario_page_schema(root, preview_state)


def _write_scenario_manifest(root: Path, scenario: Mapping[str, Any], preview_state: Mapping[str, Any]) -> None:
    scenario_id = str(scenario.get("id") or preview_state.get("scenario_id") or preview_state.get("id") or root.name).strip() or root.name
    title = str(preview_state.get("title") or scenario.get("title") or scenario.get("name") or scenario_id).strip() or scenario_id
    lines = [
        f"id: {json.dumps(scenario_id, ensure_ascii=False)}",
        f"name: {json.dumps(str(scenario.get('name') or scenario_id), ensure_ascii=False)}",
        f"type: {json.dumps(str(scenario.get('type') or 'desktop'), ensure_ascii=False)}",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"description: {json.dumps(str(scenario.get('description') or 'Builder rapid prototype scenario.'), ensure_ascii=False)}",
        f"version: {json.dumps(str(scenario.get('version') or '0.1.0'), ensure_ascii=False)}",
        "depends:",
        "  - builder_skill",
        "runtime:",
        "  skills:",
        "    required:",
        "      - builder_skill",
        "",
    ]
    (root / "scenario.yaml").write_text("\n".join(lines), encoding="utf-8")


def _form_field_type(field: Mapping[str, Any]) -> str:
    field_type = str(field.get("type") or "string")
    if field_type == "boolean":
        return "toggle"
    if field_type == "number":
        return "number"
    if field_type == "date":
        return "date"
    return "text"


def _page_schema_from_preview(preview_state: Mapping[str, Any]) -> dict[str, Any]:
    ui = preview_state.get("current_ui") if isinstance(preview_state.get("current_ui"), Mapping) else {}
    title = str(preview_state.get("title") or ui.get("title") or "Prototype").strip() or "Prototype"
    datasources = preview_state.get("datasources") if isinstance(preview_state.get("datasources"), list) else []
    datasource = datasources[0] if datasources and isinstance(datasources[0], Mapping) else {}
    fields = [dict(item) for item in datasource.get("fields", []) if isinstance(item, Mapping)]
    datasource_id = str(datasource.get("id") or "items").strip() or "items"
    mock_data = preview_state.get("mock_data") if isinstance(preview_state.get("mock_data"), Mapping) else {}
    rows = mock_data.get(datasource_id) if isinstance(mock_data.get(datasource_id), list) else []
    filters = [dict(item) for item in preview_state.get("filters", []) if isinstance(item, Mapping)]
    has_card_view = any(
        isinstance(child, Mapping) and str(child.get("type") or "") == "card_list"
        for child in (ui.get("children") if isinstance(ui.get("children"), list) else [])
    )
    editor = next(
        (
            dict(child)
            for child in (ui.get("children") if isinstance(ui.get("children"), list) else [])
            if isinstance(child, Mapping) and str(child.get("id") or "") == "editor"
        ),
        {},
    )
    submit_placement = str(editor.get("action_position") or preview_state.get("form_action_position") or "").strip().lower()
    form_inputs = {
        "fields": [
            {
                "id": str(field.get("id") or f"field_{index}"),
                "type": _form_field_type(field),
                "label": field.get("label") or field.get("id") or f"Field {index + 1}",
            }
            for index, field in enumerate(fields)
        ],
        "submitLabel": "Add",
    }
    if submit_placement == "top":
        form_inputs["submitPlacement"] = "top"
    widgets: list[dict[str, Any]] = [
        {
            "id": "prototype-form",
            "type": "ui.form",
            "area": "main",
            "title": "Input",
            "inputs": form_inputs,
            "actions": [{"on": "submit", "type": "updateState", "params": {"lastPrototypeSubmit": "$event.values"}}],
        },
    ]
    for filter_obj in filters:
        field_id = str(filter_obj.get("field_id") or "").strip()
        if not field_id:
            continue
        state_key = str(filter_obj.get("state_key") or f"builderFilter_{field_id}").strip()
        raw_options = filter_obj.get("options") if isinstance(filter_obj.get("options"), list) else []
        buttons = [{"id": "all", "label": "\u0412\u0441\u0435"}]
        if field_id == "done":
            buttons.extend(
                [
                    {"id": "true", "label": "\u041a\u0443\u043f\u043b\u0435\u043d\u043e"},
                    {"id": "false", "label": "\u041d\u0435 \u043a\u0443\u043f\u043b\u0435\u043d\u043e"},
                ]
            )
        else:
            buttons.extend({"id": str(value), "label": str(value)} for value in raw_options if str(value).strip())
        widgets.append(
            {
                "id": f"prototype-filter-{field_id}",
                "type": "input.commandBar",
                "area": "main",
                "title": filter_obj.get("label") or field_id,
                "inputs": {
                    "variant": "segmented",
                    "size": "small",
                    "selectedStateKey": state_key,
                    "buttons": buttons,
                },
                "actions": [{"on": "click", "type": "updateState", "params": {state_key: "$event.id"}}],
            }
        )
    widgets.append(
        {
            "id": "prototype-table",
            "type": "ui.table",
            "area": "main",
            "title": "List",
            "dataSource": {"kind": "static", "value": rows},
            "inputs": {
                "columns": [
                    {
                        "key": str(field.get("id") or f"field_{index}"),
                        "label": field.get("label") or field.get("id") or f"Field {index + 1}",
                        **({"kind": "boolean", "width": "72px"} if str(field.get("type") or "") == "boolean" else {}),
                    }
                    for index, field in enumerate(fields)
                ],
                "filters": [
                    {
                        "key": str(filter_obj.get("field_id") or ""),
                        "stateKey": str(filter_obj.get("state_key") or f"builderFilter_{filter_obj.get('field_id')}"),
                        "any": "all",
                    }
                    for filter_obj in filters
                    if str(filter_obj.get("field_id") or "").strip()
                ],
                "emptyText": "No items yet",
            },
        },
    )
    if has_card_view:
        first = str(fields[0].get("id") if fields else "title")
        second = str(fields[1].get("id") if len(fields) > 1 else "")
        widgets.append(
            {
                "id": "prototype-cards",
                "type": "ui.list",
                "area": "right",
                "title": "Cards",
                "dataSource": {"kind": "static", "value": rows},
                "inputs": {
                    "variant": "cards",
                    "titleKey": first,
                    "subtitleKey": second,
                    "emptyText": "No cards yet",
                },
            }
        )
    else:
        widgets.append(
            {
                "id": "prototype-summary",
                "type": "item.details",
                "area": "right",
                "title": "Prototype",
                "dataSource": {"kind": "static", "value": {"title": title, "fields": [field.get("label") for field in fields]}},
            }
        )
    return {
        "id": str(ui.get("id") or preview_state.get("session_id") or "builder_prototype"),
        "title": title,
        "layout": {
            "type": "split",
            "pattern": "split",
            "areas": [
                {"id": "main", "role": "main"},
                {"id": "right", "role": "aux"},
            ],
        },
        "widgets": widgets,
    }


def _write_scenario_page_schema(root: Path, preview_state: Mapping[str, Any]) -> None:
    manifest = root / "scenario.json"
    if not manifest.exists():
        return
    try:
        scenario = json.loads(manifest.read_text(encoding="utf-8-sig") or "{}")
    except Exception:
        return
    if not isinstance(scenario, dict):
        return
    scenario.setdefault("id", root.name)
    scenario.setdefault("name", root.name)
    scenario.setdefault("type", "desktop")
    scenario.setdefault("title", preview_state.get("title") or scenario.get("name") or scenario.get("id") or "Prototype")
    depends = scenario.get("depends")
    depends_list = [str(item) for item in depends if isinstance(item, str)] if isinstance(depends, list) else []
    if SKILL_ID not in depends_list:
        depends_list.append(SKILL_ID)
    scenario["depends"] = depends_list
    runtime = scenario.get("runtime") if isinstance(scenario.get("runtime"), dict) else {}
    skills = runtime.get("skills") if isinstance(runtime.get("skills"), dict) else {}
    required = skills.get("required") if isinstance(skills.get("required"), list) else []
    required_list = [str(item) for item in required if isinstance(item, str)]
    if SKILL_ID not in required_list:
        required_list.append(SKILL_ID)
    skills["required"] = required_list
    runtime["skills"] = skills
    scenario["runtime"] = runtime
    scenario.setdefault("ui", {})
    scenario["ui"].setdefault("application", {})
    scenario["ui"]["application"].setdefault("version", "0.1")
    scenario["ui"]["application"].setdefault("desktop", {})
    scenario["ui"]["application"]["desktop"]["pageSchema"] = _page_schema_from_preview(preview_state)
    manifest.write_text(json.dumps(scenario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_scenario_manifest(root, scenario, preview_state)


def _save_session(webspace_id: str, session: dict[str, Any]) -> dict[str, Any]:
    session["updated_at"] = _now()
    sessions = _sessions(webspace_id)
    sessions[str(session["id"])] = copy.deepcopy(session)
    _save_sessions(webspace_id, sessions)
    _set_current_session_id(webspace_id, str(session["id"]))
    return session


def _load_session(webspace_id: str, session_id: str | None = None) -> dict[str, Any] | None:
    sessions = _sessions(webspace_id)
    sid = str(session_id or "").strip() or _current_session_id(webspace_id)
    if sid and sid in sessions:
        return copy.deepcopy(sessions[sid])
    if sessions:
        return copy.deepcopy(max(sessions.values(), key=lambda item: float(item.get("updated_at") or 0)))
    return None


def _message_created(session: Mapping[str, Any]) -> str:
    summary = session.get("user_summary") if isinstance(session.get("user_summary"), Mapping) else _draft_user_summary(session)
    assumptions = "; ".join(str(item) for item in summary.get("assumptions", [])[:2]) if isinstance(summary, Mapping) else ""
    preview = "; ".join(str(item) for item in summary.get("preview", [])[:2]) if isinstance(summary, Mapping) else ""
    risks = "; ".join(str(item) for item in summary.get("risks", [])[:2]) if isinstance(summary, Mapping) else ""
    return (
        f"{AGENT_LABEL}: \u0441\u043e\u0437\u0434\u0430\u043b dev-\u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0439 "
        f"{session.get('scenario_id')} \u0438 \u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a webui. "
        f"Assumptions: {assumptions}. Preview: {preview}. Risks: {risks}. "
        "\u041c\u043e\u0436\u043d\u043e \u0441\u0440\u0430\u0437\u0443 \u043f\u0440\u0430\u0432\u0438\u0442\u044c: "
        "\u0434\u043e\u0431\u0430\u0432\u044c \u043f\u043e\u043b\u0435, \u0443\u0431\u0435\u0440\u0438 \u043f\u043e\u043b\u0435, \u043f\u043e\u043a\u0430\u0436\u0438 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0430\u043c\u0438."
    )


def _draft_user_summary(session: Mapping[str, Any]) -> dict[str, list[str]]:
    fields = [dict(item) for item in session.get("fields", []) if isinstance(item, Mapping)]
    labels = ", ".join(str(item.get("label") or item.get("id") or "") for item in fields[:5] if str(item.get("label") or item.get("id") or "").strip())
    scenario_id = str(session.get("scenario_id") or "prototype").strip() or "prototype"
    datasource_id = str(session.get("datasource_id") or "items").strip() or "items"
    return {
        "assumptions": [
            "This is a local dev prototype, not an activated runtime change",
            f"The first data model uses fields: {labels or 'title, notes, status'}",
        ],
        "preview": [
            f"Scenario {scenario_id} has a form, table, mock data, and declarative webui.json",
            f"Data is stored in an internal CRUD datasource named {datasource_id}",
        ],
        "risks": [
            "No external network, device-control, or credential access is requested",
            "Validation and human review are still required before activation",
        ],
        "expected_behavior": [
            "The user can add records through the form and inspect them in the list",
            "Follow-up Builder turns patch the current draft and refresh the preview",
        ],
    }


def _extract_field_label(instruction: str) -> str | None:
    quoted = re.search(r"[\"'«](.*?)[\"'»]", instruction)
    if quoted:
        return _clean_field_label(quoted.group(1))
    match = re.search(r"(?:field|поле|column|колонк[ауи]?)\s+([A-Za-zА-Яа-я0-9 _-]{2,40})", instruction, re.IGNORECASE)
    if match:
        return _clean_field_label(match.group(1))
    return None


def _clean_field_label(label: str) -> str:
    token = str(label or "").strip(" \t\r\n:;,.!?()[]{}")
    token = re.split(r"\s+(?:в|на|к|для|со|с|to|in|as)\s+", token, maxsplit=1, flags=re.IGNORECASE)[0]
    return token.strip(" \t\r\n:;,.!?()[]{}")


def _field_id(label: str) -> str:
    lowered = str(label or "").strip().lower()
    known = {
        "\u0446\u0435\u043d\u0430": "price",
        "\u0434\u0430\u0442\u0430": "date",
        "\u043a\u0443\u043f\u043b\u0435\u043d\u043e": "done",
        "\u0442\u043e\u0432\u0430\u0440": "item",
        "\u043a\u043e\u043b-\u0432\u043e": "quantity",
        "\u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e": "quantity",
        "\u043c\u0435\u0440\u0430": "unit",
        "\u0435\u0434\u0438\u043d\u0438\u0446\u0430": "unit",
        "\u0435\u0434.": "unit",
        "\u043d\u0430\u043b\u0438\u0447\u0438\u0435": "availability",
        "\u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f": "category",
        "\u0442\u0435\u043b\u0435\u0444\u043e\u043d": "phone",
        "\u043e\u0440\u0433\u0430\u043d\u0438\u0437\u0430\u0446\u0438\u044f": "organization",
        "date": "date",
        "done": "done",
        "purchased": "done",
        "unit": "unit",
        "measure": "unit",
        "availability": "availability",
    }
    if lowered in known:
        return known[lowered]
    ascii_id = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return ascii_id or f"field_{_hash_suffix(label)}"


def _field_type_for_id(field_id: str, label: str | None = None) -> str:
    token = f"{field_id} {label or ''}".lower()
    if field_id == "done" or any(item in token for item in ("checkbox", "check box", "чекбокс", "куплено")):
        return "boolean"
    if field_id == "price" or any(item in token for item in ("price", "цена", "стоимость")):
        return "number"
    if field_id == "date" or any(item in token for item in ("date", "дата")):
        return "date"
    return "string"


def _default_label_for_field(field_id: str, fallback: str | None = None) -> str:
    labels = {
        "date": "\u0414\u0430\u0442\u0430",
        "done": "\u041a\u0443\u043f\u043b\u0435\u043d\u043e",
        "price": "\u0426\u0435\u043d\u0430",
        "unit": "\u041c\u0435\u0440\u0430",
        "availability": "\u041d\u0430\u043b\u0438\u0447\u0438\u0435",
    }
    return labels.get(field_id) or _clean_field_label(fallback or field_id).title()


def _ensure_field(
    fields: list[dict[str, Any]],
    *,
    label: str,
    field_id: str | None = None,
    field_type: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    fid = str(field_id or _field_id(label)).strip()
    for item in fields:
        if str(item.get("id") or "") == fid:
            if field_type and str(item.get("type") or "") != field_type:
                item["type"] = field_type
            if not str(item.get("label") or "").strip():
                item["label"] = _default_label_for_field(fid, label)
            options = _field_options(fid)
            if options and not isinstance(item.get("options"), list):
                item["options"] = options
            return fields, item, False
    field = {
        "id": fid,
        "type": field_type or _field_type_for_id(fid, label),
        "label": _default_label_for_field(fid, label),
        "required": False,
    }
    options = _field_options(fid)
    if options:
        field["options"] = options
    fields.append(field)
    return fields, field, True


def _field_options(field_id: str) -> list[Any]:
    if field_id == "unit":
        return ["\u0448\u0442", "\u043a\u0433", "\u0433", "\u043b"]
    if field_id == "availability":
        return ["\u0432 \u043d\u0430\u043b\u0438\u0447\u0438\u0438", "\u043d\u0435\u0442"]
    if field_id == "done":
        return [True, False]
    return []


def _ensure_filter(filters: list[dict[str, Any]], field: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    field_id = str(field.get("id") or "").strip()
    if not field_id:
        return filters, {}, False
    for item in filters:
        if str(item.get("field_id") or "") == field_id:
            return filters, item, False
    filter_obj = {
        "field_id": field_id,
        "label": field.get("label") or _default_label_for_field(field_id),
        "state_key": f"builderFilter_{field_id}",
        "options": _field_options(field_id),
    }
    filters.append(filter_obj)
    return filters, filter_obj, True


def _requested_known_fields(text: str) -> list[dict[str, Any]]:
    lowered = str(text or "").lower()
    words = set(re.findall(r"[A-Za-z0-9.\u0410-\u042f\u0430-\u044f\u0401\u0451]+", lowered))
    specs: list[dict[str, Any]] = []
    if (
        any(word.startswith("\u043c\u0435\u0440") for word in words)
        or words.intersection({"\u0435\u0434\u0438\u043d\u0438\u0446\u0430", "\u0435\u0434.", "unit", "measure"})
        or "\u0435\u0434\u0438\u043d\u0438\u0446\u0430 \u0438\u0437\u043c\u0435\u0440\u0435\u043d\u0438\u044f" in lowered
    ):
        specs.append({"label": "\u041c\u0435\u0440\u0430", "field_id": "unit", "field_type": "string"})
    if any(token in lowered for token in ("\u043d\u0430\u043b\u0438\u0447", "availability", "stock")):
        specs.append({"label": "\u041d\u0430\u043b\u0438\u0447\u0438\u0435", "field_id": "availability", "field_type": "string"})
    return specs


def _requested_filter_field_ids(text: str) -> list[str]:
    lowered = str(text or "").lower()
    if not any(token in lowered for token in ("\u0444\u0438\u043b\u044c\u0442\u0440", "filter")):
        return []
    ids: list[str] = []
    if any(token in lowered for token in ("\u043a\u0443\u043f\u043b\u0435\u043d", "done", "purchased")):
        ids.append("done")
    if any(token in lowered for token in ("\u043d\u0430\u043b\u0438\u0447", "availability", "stock")):
        ids.append("availability")
    if any(token in lowered for token in ("\u043a\u0430\u0442\u0435\u0433\u043e\u0440", "category")):
        ids.append("category")
    return ids


def _move_field_first(fields: list[dict[str, Any]], field_id: str) -> list[dict[str, Any]]:
    fid = str(field_id or "").strip()
    if not fid:
        return fields
    selected = [item for item in fields if str(item.get("id") or "") == fid]
    if not selected:
        return fields
    rest = [item for item in fields if str(item.get("id") or "") != fid]
    return [selected[0], *rest]


def _date_mock_rows(fields: list[dict[str, Any]], existing_rows: Any = None) -> list[dict[str, Any]]:
    base_rows = [dict(item) for item in existing_rows if isinstance(item, Mapping)] if isinstance(existing_rows, list) else _food_mock_rows(fields)
    if not base_rows:
        base_rows = _mock_rows(fields)
    dates = ["2026-07-01", "2026-07-02", "2026-07-03"]
    for index, row in enumerate(base_rows):
        row["date"] = dates[index % len(dates)]
    return base_rows


def _mentions_date(text: str) -> bool:
    lowered = str(text or "").lower()
    return "date" in lowered or "\u0434\u0430\u0442" in lowered


def _wants_add_button_above_form(text: str) -> bool:
    lowered = str(text or "").lower()
    mentions_button = "button" in lowered or "\u043a\u043d\u043e\u043f" in lowered
    mentions_add = "add" in lowered or "\u0434\u043e\u0431\u0430\u0432" in lowered
    mentions_top = "above" in lowered or "top" in lowered or "\u043d\u0430\u0434" in lowered or "\u0432\u0435\u0440\u0445" in lowered
    mentions_form = "form" in lowered or "\u0444\u043e\u0440\u043c" in lowered
    return mentions_button and mentions_add and mentions_top and mentions_form


def _wants_done_checkbox_first(text: str) -> bool:
    lowered = str(text or "").lower()
    mentions_done = "done" in lowered or "purchased" in lowered or "\u043a\u0443\u043f\u043b\u0435\u043d" in lowered
    mentions_checkbox = "checkbox" in lowered or "check box" in lowered or "\u0447\u0435\u043a\u0431\u043e\u043a\u0441" in lowered
    mentions_first_column = (
        ("first" in lowered or "\u043f\u0435\u0440\u0432" in lowered)
        and ("column" in lowered or "\u043a\u043e\u043b\u043e\u043d" in lowered)
    )
    return mentions_done and (mentions_checkbox or mentions_first_column)


def _wants_date_values(text: str) -> bool:
    lowered = str(text or "").lower()
    return _mentions_date(lowered) and any(
        token in lowered for token in ("data", "value", "values", "fill", "\u0434\u0430\u043d\u043d", "\u0437\u043d\u0430\u0447\u0435\u043d", "\u0437\u0430\u043f\u043e\u043b\u043d")
    )


def _workbench_service():
    from adaos.services.builder.workbench import BuilderWorkbenchService

    return BuilderWorkbenchService.from_context()


def _request_workbench_refresh(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        from adaos.sdk.data import events

        events.publish(WORKBENCH_REFRESH_TOPIC, payload, source=SKILL_ID)
        return {"ok": True, "topic": WORKBENCH_REFRESH_TOPIC}
    except Exception as exc:
        return {"ok": False, "topic": WORKBENCH_REFRESH_TOPIC, "error": f"{type(exc).__name__}: {exc}"}


def _active_draft_id(session: Mapping[str, Any] | None) -> str | None:
    if not isinstance(session, Mapping):
        return None
    if not str(session.get("artifact_root") or "").strip():
        return None
    return str(session.get("draft_id") or session.get("id") or "").strip() or None


def _runtime_scenario_id(session: Mapping[str, Any] | None) -> str | None:
    if not isinstance(session, Mapping):
        return None
    if not str(session.get("artifact_root") or "").strip():
        return None
    return str(session.get("scenario_id") or "").strip() or None


def _workbench_binding(webspace_id: str) -> dict[str, Any]:
    try:
        binding = _workbench_service().get_workspace_binding(webspace_id)
        return dict(binding) if isinstance(binding, Mapping) else {}
    except Exception:
        return {}


def _session_matches_binding(session: Mapping[str, Any], binding: Mapping[str, Any]) -> bool:
    draft_id = str(binding.get("active_draft_id") or "").strip()
    scenario_id = str(binding.get("runtime_scenario_id") or "").strip()
    if draft_id and str(session.get("draft_id") or session.get("id") or "").strip() == draft_id:
        return True
    if scenario_id and str(session.get("scenario_id") or "").strip() == scenario_id:
        return True
    return not draft_id and not scenario_id


def _target_session(webspace_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    binding = _workbench_binding(webspace_id)
    draft_id = str(binding.get("active_draft_id") or "").strip()
    scenario_id = str(binding.get("runtime_scenario_id") or "").strip()
    sessions = _sessions(webspace_id)
    if draft_id or scenario_id:
        for session in sessions.values():
            if draft_id and str(session.get("draft_id") or session.get("id") or "").strip() == draft_id:
                return copy.deepcopy(session), binding
            if scenario_id and str(session.get("scenario_id") or "").strip() == scenario_id:
                return copy.deepcopy(session), binding
        return None, binding
    session = _load_session(webspace_id)
    if session and _session_matches_binding(session, binding):
        return session, binding
    return None, binding


def _target_required_message(binding: Mapping[str, Any] | None = None) -> str:
    scenario_id = str((binding or {}).get("runtime_scenario_id") or "").strip()
    if scenario_id:
        return (
            f"{AGENT_LABEL}: \u0432 Prompt IDE \u0432\u044b\u0431\u0440\u0430\u043d \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0439 {scenario_id}, "
            "\u043d\u043e \u044f \u043d\u0435 \u0432\u0438\u0436\u0443 \u0434\u043b\u044f \u043d\u0435\u0433\u043e Builder-\u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a. "
            "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 Builder-\u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a \u0438\u043b\u0438 \u0441\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u043d\u043e\u0432\u044b\u0439: "
            "\u00ab\u0421\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c, \u0441\u043e\u0437\u0434\u0430\u0439 ...\u00bb."
        )
    return (
        f"{AGENT_LABEL}: \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043e\u0431\u044a\u0435\u043a\u0442 \u0434\u043b\u044f \u0434\u043e\u0440\u0430\u0431\u043e\u0442\u043a\u0438 "
        "\u0432 Prompt IDE (\u043d\u0430\u0432\u044b\u043a \u0438\u043b\u0438 \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0439). "
        "\u0415\u0441\u043b\u0438 \u043d\u0443\u0436\u0435\u043d \u043d\u043e\u0432\u044b\u0439 \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f, \u043d\u0430\u043f\u0438\u0448\u0438\u0442\u0435: "
        "\u00ab\u0421\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c, \u0441\u043e\u0437\u0434\u0430\u0439 ...\u00bb."
    )


def _normalized_builder_phrase(text: str) -> str:
    phrase = re.sub(r"\s+", " ", str(text or "").strip().lower()).strip(" .!?;:")
    for alias in ("builder", "\u0441\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c", "\u0431\u0438\u043b\u0434\u0435\u0440"):
        if phrase == alias:
            return ""
        for separator in (", ", ": ", " - "):
            prefix = f"{alias}{separator}"
            if phrase.startswith(prefix):
                return phrase[len(prefix) :].strip()
    return phrase


def _is_guided_clarification_request(text: str) -> bool:
    phrase = _normalized_builder_phrase(text)
    if not phrase:
        return False
    exact_vague_phrases = {
        "i have an idea",
        "i've got an idea",
        "there is an idea",
        "help me shape an idea",
        "help me build something",
        "\u0435\u0441\u0442\u044c \u0438\u0434\u0435\u044f",
        "\u0443 \u043c\u0435\u043d\u044f \u0435\u0441\u0442\u044c \u0438\u0434\u0435\u044f",
        "\u0434\u0430\u0432\u0430\u0439 \u0447\u0442\u043e-\u043d\u0438\u0431\u0443\u0434\u044c \u0441\u043e\u0431\u0435\u0440\u0435\u043c",
        "\u0434\u0430\u0432\u0430\u0439 \u0447\u0442\u043e-\u043d\u0438\u0431\u0443\u0434\u044c \u0441\u0434\u0435\u043b\u0430\u0435\u043c",
        "\u043f\u043e\u043c\u043e\u0433\u0438 \u0441\u0444\u043e\u0440\u043c\u0443\u043b\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0438\u0434\u0435\u044e",
    }
    if phrase in exact_vague_phrases:
        return True
    vague_starts = (
        "i have an idea for",
        "i want to build something",
        "\u0445\u043e\u0447\u0443 \u0441\u0434\u0435\u043b\u0430\u0442\u044c \u0447\u0442\u043e-\u0442\u043e",
        "\u043d\u0443\u0436\u043d\u043e \u0441\u043e\u0431\u0440\u0430\u0442\u044c \u0447\u0442\u043e-\u0442\u043e",
    )
    return any(phrase.startswith(item) for item in vague_starts)


def _builder_clarification_payload(
    *,
    text: str,
    webspace_id: str,
    topic: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "adaos.builder.guided_clarification.v1",
        "status": "clarification_required",
        "source_text": str(text or "").strip(),
        "webspace_id": webspace_id,
        "topic": dict(topic or {}),
        "questions": [
            {
                "id": "user_goal",
                "label": "\u0426\u0435\u043b\u044c",
                "prompt": "\u041a\u0430\u043a\u0443\u044e \u0437\u0430\u0434\u0430\u0447\u0443 \u0434\u043e\u043b\u0436\u0435\u043d \u0440\u0435\u0448\u0430\u0442\u044c \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f?",
                "required": True,
            },
            {
                "id": "primary_objects",
                "label": "\u0414\u0430\u043d\u043d\u044b\u0435",
                "prompt": "\u041a\u0430\u043a\u0438\u0435 \u043e\u0431\u044a\u0435\u043a\u0442\u044b, \u043f\u043e\u043b\u044f \u0438\u043b\u0438 \u0437\u0430\u043f\u0438\u0441\u0438 \u043d\u0443\u0436\u043d\u044b \u043d\u0430 \u043f\u0435\u0440\u0432\u043e\u043c \u044d\u043a\u0440\u0430\u043d\u0435?",
                "required": True,
            },
            {
                "id": "first_action",
                "label": "\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435",
                "prompt": "\u041a\u0430\u043a\u043e\u0435 \u043e\u0434\u043d\u043e \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u0434\u043e\u043b\u0436\u0435\u043d \u0441\u0440\u0430\u0437\u0443 \u0441\u043c\u043e\u0447\u044c \u0441\u0434\u0435\u043b\u0430\u0442\u044c?",
                "required": True,
            },
        ],
        "suggested_replies": [
            "\u0421\u0434\u0435\u043b\u0430\u0439 \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f \u0441\u043f\u0438\u0441\u043a\u0430 \u043f\u043e\u043a\u0443\u043f\u043e\u043a: \u0442\u043e\u0432\u0430\u0440, \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e, \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f; \u043d\u0443\u0436\u043d\u043e \u0434\u043e\u0431\u0430\u0432\u043b\u044f\u0442\u044c \u0438 \u043e\u0442\u043c\u0435\u0447\u0430\u0442\u044c \u043a\u0443\u043f\u043b\u0435\u043d\u043d\u043e\u0435.",
            "Build a simple task tracker with title, owner, status, due date, and a quick add form.",
        ],
        "next_turn_policy": {
            "creates_draft_when_answered": True,
            "minimum_answer_fields": ["user_goal"],
            "owner": f"skill:{SKILL_ID}",
            "agent_id": AGENT_ID,
        },
    }


def _guided_clarification_message(payload: Mapping[str, Any]) -> str:
    questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
    rendered = []
    for index, item in enumerate(questions[:3], start=1):
        if isinstance(item, Mapping):
            rendered.append(f"{index}. {item.get('prompt')}")
    return (
        f"{AGENT_LABEL}: \u0438\u0434\u0435\u044e \u043b\u0443\u0447\u0448\u0435 \u0443\u0442\u043e\u0447\u043d\u0438\u0442\u044c \u0434\u043e \u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a\u0430.\n\n"
        + "\n".join(rendered)
        + "\n\n\u041c\u043e\u0436\u043d\u043e \u043e\u0442\u0432\u0435\u0442\u0438\u0442\u044c \u043e\u0434\u043d\u043e\u0439 \u0444\u0440\u0430\u0437\u043e\u0439: \u0447\u0442\u043e \u0441\u0442\u0440\u043e\u0438\u043c, \u043a\u0430\u043a\u0438\u0435 \u043f\u043e\u043b\u044f \u043d\u0443\u0436\u043d\u044b, \u0438 \u043a\u0430\u043a\u043e\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u0432\u0430\u0436\u043d\u043e \u043f\u0435\u0440\u0432\u044b\u043c."
    )


def _is_create_request(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        token in lowered
        for token in (
            "i have an idea",
            "i've got an idea",
            "lets build",
            "let's build",
            "build it",
            "create",
            "new app",
            "new scenario",
            "app",
            "scenario",
            "skill",
            "prototype",
            "\u0441\u043e\u0437\u0434",
            "\u0441\u0434\u0435\u043b\u0430\u0435\u043c",
            "\u0434\u0430\u0432\u0430\u0439 \u0441\u0434\u0435\u043b",
            "\u0435\u0441\u0442\u044c \u0438\u0434\u0435\u044f",
            "\u0438\u0434\u0435\u044f",
            "\u0441\u043e\u0431\u0435\u0440",
            "\u043f\u043e\u0441\u0442\u0440\u043e\u0438",
            "\u043d\u043e\u0432\u044b\u0439",
            "\u043f\u0440\u0438\u043b\u043e\u0436",
            "\u0441\u0446\u0435\u043d\u0430\u0440",
            "\u043d\u0430\u0432\u044b\u043a",
        )
    )


def _wants_sample_data(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in ("sample", "mock", "example", "\u043f\u0440\u0438\u043c\u0435\u0440", "\u0434\u0430\u043d\u043d", "\u043f\u0440\u043e\u0434\u0443\u043a\u0442", "\u043f\u0438\u0442\u0430\u043d", "\u0435\u0434\u0430"))


def _ensure_workbench(
    webspace_id: str,
    *,
    session: Mapping[str, Any] | None = None,
    preview_state: Mapping[str, Any] | None = None,
    active_draft_id: str | None = None,
    runtime_scenario_id: str | None = None,
) -> dict[str, Any]:
    svc = _workbench_service()
    draft_id = str(active_draft_id or _active_draft_id(session) or "").strip() or None
    scenario_id = str(runtime_scenario_id or _runtime_scenario_id(session) or "").strip() or None
    try:
        binding = svc.set_active_draft(
            source_webspace_id=webspace_id,
            active_draft_id=draft_id,
            runtime_scenario_id=scenario_id,
            persist_projection=False,
        )
        snapshot = svc.snapshot(webspace_id, preview_state=preview_state)
        event = _request_workbench_refresh(
            {
                "source_webspace_id": webspace_id,
                "active_draft_id": draft_id,
                "runtime_scenario_id": scenario_id,
                "preview_state": dict(preview_state or {}),
            }
        )
    except Exception as exc:
        return {"ok": False, "error": "workbench_unavailable", "detail": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "binding": binding, "projection": {"ok": True, "snapshot": snapshot, "deferred": True, "event": event}}


def _delete_sessions_for_draft(webspace_id: str, draft_id: str) -> None:
    token = str(draft_id or "").strip()
    if not token:
        return
    sessions = _sessions(webspace_id)
    removed = [sid for sid, session in sessions.items() if str(session.get("draft_id") or session.get("id") or "") == token]
    if not removed:
        return
    for sid in removed:
        sessions.pop(sid, None)
    _save_sessions(webspace_id, sessions)
    current = _current_session_id(webspace_id)
    if current in removed:
        latest = max(sessions.values(), key=lambda item: float(item.get("updated_at") or 0), default=None)
        _set_current_session_id(webspace_id, str(latest.get("id") if latest else ""))


@tool(summary="Start Builder rapid prototyping dialog.", side_effects="local_write")
def start(
    text: str | None = None,
    webspace_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return chat(text=text or "", webspace_id=webspace_id, _meta=_meta)


@tool(summary="Handle Builder dialog turn.", side_effects="local_write")
def chat(
    text: str | None = None,
    webspace_id: str | None = None,
    auto_apply: bool = True,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    utterance = str(text or "").strip()
    session, binding = _target_session(ws)
    topic = _builder_topic_ref(ws, session=session, binding=binding, _meta=_meta)
    if _is_guided_clarification_request(utterance):
        clarification = _builder_clarification_payload(text=utterance, webspace_id=ws, topic=topic)
        message = _guided_clarification_message(clarification)
        _safe_emit_chat(message, webspace_id=ws, _meta=_meta, binding=binding, topic_ref=topic)
        return {
            "ok": True,
            "status": "clarification_required",
            "needs_clarification": True,
            "message": message,
            "clarification": clarification,
            "binding": binding,
            "topic": topic,
            "dialog": _dialog_state(ws, topic_ref=topic),
        }
    if _is_create_request(utterance):
        result = create_scenario_draft(idea=utterance or "prototype app", webspace_id=ws, _meta=_meta)
        if result.get("ok"):
            message = str(result.get("message") or "")
            _safe_emit_chat(message, webspace_id=ws, _meta=_meta, topic_ref=result.get("topic") if isinstance(result.get("topic"), Mapping) else None)
            return {**result, "dialog": _dialog_state(ws, topic_ref=result.get("topic") if isinstance(result.get("topic"), Mapping) else topic)}
        return {**result, "dialog": _dialog_state(ws, topic_ref=topic)}
    if not session:
        message = _target_required_message(binding)
        _safe_emit_chat(message, webspace_id=ws, _meta=_meta, binding=binding, topic_ref=topic)
        return {
            "ok": True,
            "status": "target_required",
            "needs_selection": True,
            "message": message,
            "binding": binding,
            "topic": topic,
            "dialog": _dialog_state(ws, topic_ref=topic),
        }
    result = update_current_scenario(instruction=utterance, webspace_id=ws, auto_apply=auto_apply, _meta=_meta)
    if result.get("ok"):
        _safe_emit_chat(
            str(result.get("message") or ""),
            webspace_id=ws,
            _meta=_meta,
            session=session,
            binding=binding,
            topic_ref=result.get("topic") if isinstance(result.get("topic"), Mapping) else topic,
        )
    return {**result, "dialog": _dialog_state(ws, topic_ref=result.get("topic") if isinstance(result.get("topic"), Mapping) else topic)}


@tool(summary="Create scenario prototype draft.", side_effects="local_write")
def create_scenario_draft(
    idea: str,
    scenario_id: str | None = None,
    webspace_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    source_idea = str(idea or "").strip() or "prototype app"
    sid = re.sub(r"[^a-z0-9_.-]+", "_", str(scenario_id or "").strip().lower()).strip("._-") or _scenario_id_from_idea(source_idea)
    fields = _build_fields(source_idea)
    session_id = f"builder_session_{_hash_suffix(ws + sid + source_idea)}"
    session = {
        "id": session_id,
        "webspace_id": ws,
        "status": "drafting",
        "title": "\u0421\u043f\u0438\u0441\u043e\u043a \u043f\u043e\u043a\u0443\u043f\u043e\u043a" if "shopping" in sid else sid.replace("_", " ").title(),
        "source_idea": source_idea,
        "scenario_id": sid,
        "datasource_id": "shopping_items" if "shopping" in sid else "prototype_items",
        "fields": fields,
        "patches": [],
        "version": "v1",
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        from adaos.services.builder.workspace import BuilderWorkspaceService

        draft = BuilderWorkspaceService.from_context().create_draft(
            kind="scenario",
            artifact_id=sid,
            source_idea=source_idea,
            template_id="builder_scenario",
            webspace_id=ws,
            source={
                "type": "builder_dialog",
                "utterance": source_idea,
                "side_effect_class": "local_write",
            },
        )
        draft_payload = draft.get("draft") if isinstance(draft.get("draft"), dict) else {}
        session["draft_id"] = draft_payload.get("draft_id")
        session["artifact_root"] = draft.get("artifact_root")
    except Exception as exc:
        session["status"] = "degraded"
        session["draft_error"] = f"{type(exc).__name__}: {exc}"
    session["user_summary"] = _draft_user_summary(session)
    preview = _preview_state(session=session)
    _write_webui(str(session.get("artifact_root") or ""), preview)
    session["preview_state"] = preview
    _save_session(ws, session)
    workbench = _ensure_workbench(ws, session=session, preview_state=preview)
    binding = workbench.get("binding") if isinstance(workbench.get("binding"), Mapping) else {}
    topic = _builder_topic_ref(ws, session=session, binding=binding, _meta=_meta)
    session["thread_id"] = str(topic.get("thread_id") or "").strip() or None
    session["topic_id"] = str(topic.get("topic_id") or "").strip() or None
    session["topic_ref"] = {k: v for k, v in topic.items() if k != "stored"}
    _save_session(ws, session)
    message = _message_created(session)
    if session.get("draft_error"):
        message += f" \u041f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435: dev draft \u043d\u0435 \u0441\u043e\u0437\u0434\u0430\u043d ({session['draft_error']})."
    pending_action = _publish_review_pending_action(
        webspace_id=ws,
        session=session,
        request_text=source_idea,
        kind="builder.scenario_draft.review",
        summary=f"Review Builder draft {sid}",
        _meta=_meta,
    )
    if pending_action and pending_action.get("id"):
        session["pending_action_id"] = pending_action.get("id")
        _save_session(ws, session)
    return {
        "ok": True,
        "session_id": session_id,
        "scenario_id": sid,
        "draft_id": session.get("draft_id"),
        "artifact_root": session.get("artifact_root"),
        "preview_state": preview,
        "workbench": workbench,
        "topic": {k: v for k, v in topic.items() if k != "stored"},
        "pending_action": pending_action,
        "message": message,
        "dialog": _dialog_state(ws, topic_ref=topic),
    }


@tool(summary="Update current scenario prototype.", side_effects="local_write")
def update_current_scenario(
    instruction: str,
    webspace_id: str | None = None,
    auto_apply: bool = True,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    session, binding = _target_session(ws)
    topic = _builder_topic_ref(ws, session=session, binding=binding, _meta=_meta)
    if not session:
        return {
            "ok": True,
            "status": "target_required",
            "needs_selection": True,
            "message": _target_required_message(binding),
            "binding": binding,
            "topic": topic,
            "dialog": _dialog_state(ws, topic_ref=topic),
        }
    text = str(instruction or "").strip()
    lowered = text.lower()
    patch = {
        "id": f"patch_{_hash_suffix(session['id'] + text + str(_now()))}",
        "target": "ui",
        "operation": "noop",
        "status": "applied" if auto_apply else "proposed",
        "created_by": "llm_agent",
        "created_at": _now(),
        "summary": text,
        "diff": {},
    }
    fields = [dict(item) for item in session.get("fields", []) if isinstance(item, Mapping)]
    filters = [dict(item) for item in session.get("filters", []) if isinstance(item, Mapping)]
    if any(token in lowered for token in ("карточ", "card")):
        session["card_view"] = True
        patch["operation"] = "change_view_representation"
        patch["diff"] = {"card_view": True}
    elif any(token in lowered for token in ("убери", "удали", "remove")):
        label = _extract_field_label(text) or text.rsplit(" ", 1)[-1]
        fid = _field_id(label)
        before = len(fields)
        fields = [item for item in fields if str(item.get("id")) != fid and str(item.get("label") or "").lower() != label.lower()]
        session["fields"] = fields
        patch["operation"] = "remove_field"
        patch["diff"] = {"field_id": fid, "removed": before != len(fields), "warning": "existing records may still contain this field"}
    elif _wants_add_button_above_form(text):
        session["form_action_position"] = "top"
        patch["operation"] = "move_form_action"
        patch["diff"] = {"form_id": "prototype-form", "action_id": "add_item", "submitPlacement": "top"}
    elif _wants_done_checkbox_first(text):
        fields, field, added = _ensure_field(fields, label="\u041a\u0443\u043f\u043b\u0435\u043d\u043e", field_id="done", field_type="boolean")
        fields = _move_field_first(fields, "done")
        session["fields"] = fields
        patch["operation"] = "set_checkbox_column"
        patch["diff"] = {
            "field": field,
            "added": added,
            "field_order": [str(item.get("id") or "") for item in fields],
            "table_column": {"key": "done", "kind": "boolean", "position": 0},
        }
    elif _requested_known_fields(text) or _requested_filter_field_ids(text):
        applied: list[str] = []
        changed_fields: list[dict[str, Any]] = []
        changed_filters: list[dict[str, Any]] = []
        not_implemented: list[str] = []

        for spec in _requested_known_fields(text):
            fields, field, added = _ensure_field(
                fields,
                label=str(spec["label"]),
                field_id=str(spec["field_id"]),
                field_type=str(spec["field_type"]),
            )
            changed_fields.append(dict(field))
            applied.append("add_field" if added else "ensure_field")

        fields_by_id = {str(item.get("id") or ""): item for item in fields}
        for field_id in _requested_filter_field_ids(text):
            field = fields_by_id.get(field_id)
            if field is None and field_id in {"done", "availability"}:
                fields, field, _added = _ensure_field(
                    fields,
                    label=_default_label_for_field(field_id),
                    field_id=field_id,
                    field_type="boolean" if field_id == "done" else "string",
                )
                fields_by_id[field_id] = field
                changed_fields.append(dict(field))
            if field is None:
                not_implemented.append(f"filter:{field_id}")
                continue
            filters, filter_obj, added = _ensure_filter(filters, field)
            changed_filters.append(dict(filter_obj))
            applied.append("add_filter" if added else "ensure_filter")

        session["fields"] = fields
        session["filters"] = filters
        rows = _food_mock_rows(fields)
        session["mock_rows"] = rows
        unique_applied = list(dict.fromkeys(applied))
        patch["operation"] = unique_applied[0] if len(unique_applied) == 1 else "multi_update"
        patch["status"] = "partial" if not_implemented else patch["status"]
        patch["diff"] = {
            "fields": changed_fields,
            "filters": changed_filters,
            "datasource_id": session.get("datasource_id") or "items",
            "rows": rows,
            "applied_operations": unique_applied,
            "not_implemented": not_implemented,
        }
    elif _mentions_date(text) and ("field" in lowered or "column" in lowered or "\u043f\u043e\u043b\u0435" in lowered or "\u043a\u043e\u043b\u043e\u043d" in lowered or _wants_date_values(text)):
        fields, field, added = _ensure_field(fields, label="\u0414\u0430\u0442\u0430", field_id="date", field_type="date")
        session["fields"] = fields
        rows = _date_mock_rows(fields, session.get("mock_rows"))
        session["mock_rows"] = rows
        patch["operation"] = "add_field" if added else "update_mock_data"
        patch["diff"] = {
            "field": field,
            "added": added,
            "datasource_id": session.get("datasource_id") or "items",
            "rows": rows,
        }
    elif _wants_sample_data(text):
        rows = _food_mock_rows(fields)
        session["mock_rows"] = rows
        patch["operation"] = "update_mock_data"
        patch["diff"] = {"datasource_id": session.get("datasource_id") or "items", "rows": rows}
    else:
        label = _extract_field_label(text) or ("\u0426\u0435\u043d\u0430" if "\u0446\u0435\u043d" in lowered or "price" in lowered else None)
        if label:
            fid = _field_id(label)
            if not any(str(item.get("id")) == fid for item in fields):
                field = {"id": fid, "type": _field_type_for_id(fid, label), "label": _default_label_for_field(fid, label), "required": False}
                fields.append(field)
                session["fields"] = fields
                patch["operation"] = "add_field"
                patch["diff"] = {"field": field}
    if patch["operation"] == "noop":
        if not isinstance(session.get("user_summary"), Mapping):
            session["user_summary"] = _draft_user_summary(session)
        preview = session.get("preview_state") if isinstance(session.get("preview_state"), dict) else _preview_state(session=session)
        workbench = _ensure_workbench(ws, session=session, preview_state=preview)
        binding = workbench.get("binding") if isinstance(workbench.get("binding"), Mapping) else binding
        topic = _builder_topic_ref(ws, session=session, binding=binding, _meta=_meta)
        message = (
            f"{AGENT_LABEL}: \u044f \u043d\u0435 \u043d\u0430\u0448\u0435\u043b \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0430\u043d\u043d\u043e\u0433\u043e "
            f"\u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u0434\u043b\u044f {session.get('scenario_id')}. "
            "\u0423\u0442\u043e\u0447\u043d\u0438\u0442\u0435, \u043a\u0430\u043a \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c UI: "
            "\u0434\u043e\u0431\u0430\u0432\u044c \u043f\u043e\u043b\u0435, \u0443\u0431\u0435\u0440\u0438 \u043f\u043e\u043b\u0435, "
            "\u043f\u043e\u043a\u0430\u0436\u0438 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0430\u043c\u0438 \u0438\u043b\u0438 \u0441\u0434\u0435\u043b\u0430\u0439 \u043f\u0440\u0438\u043c\u0435\u0440 \u0434\u0430\u043d\u043d\u044b\u0445."
        )
        return {
            "ok": True,
            "status": "noop",
            "session_id": session.get("id"),
            "scenario_id": session.get("scenario_id"),
            "patch": patch,
            "preview_state": preview,
            "workbench": workbench,
            "topic": {k: v for k, v in topic.items() if k != "stored"},
            "pending_action": None,
            "message": message,
            "dialog": _dialog_state(ws, topic_ref=topic),
        }
    session.setdefault("patches", []).append(patch)
    session["version"] = f"v{len(session.get('patches') or []) + 1}"
    session["user_summary"] = _draft_user_summary(session)
    preview = _preview_state(session=session)
    _write_webui(str(session.get("artifact_root") or ""), preview)
    session["preview_state"] = preview
    workbench = _ensure_workbench(ws, session=session, preview_state=preview)
    binding = workbench.get("binding") if isinstance(workbench.get("binding"), Mapping) else binding
    topic = _builder_topic_ref(ws, session=session, binding=binding, _meta=_meta)
    session["thread_id"] = str(topic.get("thread_id") or "").strip() or None
    session["topic_id"] = str(topic.get("topic_id") or "").strip() or None
    session["topic_ref"] = {k: v for k, v in topic.items() if k != "stored"}
    _save_session(ws, session)
    pending_action = _publish_review_pending_action(
        webspace_id=ws,
        session=session,
        request_text=text,
        kind="builder.scenario_patch.review",
        summary=f"Review Builder patch {patch['operation']} for {session.get('scenario_id')}",
        _meta=_meta,
        patch=patch,
    )
    if pending_action and pending_action.get("id"):
        patch["pending_action_id"] = pending_action.get("id")
        session["patches"][-1] = patch
        session["pending_action_id"] = pending_action.get("id")
        _save_session(ws, session)
    not_implemented = patch.get("diff", {}).get("not_implemented") if isinstance(patch.get("diff"), Mapping) else None
    if patch.get("status") == "partial" and isinstance(not_implemented, list) and not_implemented:
        message = (
            f"{AGENT_LABEL}: \u0447\u0430\u0441\u0442\u0438\u0447\u043d\u043e \u043e\u0431\u043d\u043e\u0432\u0438\u043b \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f "
            f"{session.get('scenario_id')}. \u041e\u043f\u0435\u0440\u0430\u0446\u0438\u044f: {patch['operation']}. "
            f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0440\u0435\u0430\u043b\u0438\u0437\u043e\u0432\u0430\u0442\u044c: {', '.join(str(item) for item in not_implemented)}."
        )
    else:
        message = (
            f"{AGENT_LABEL}: \u043e\u0431\u043d\u043e\u0432\u0438\u043b \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f "
            f"{session.get('scenario_id')}. \u041e\u043f\u0435\u0440\u0430\u0446\u0438\u044f: {patch['operation']}."
        )
    return {
        "ok": True,
        "session_id": session.get("id"),
        "scenario_id": session.get("scenario_id"),
        "patch": patch,
        "preview_state": preview,
        "workbench": workbench,
        "topic": {k: v for k, v in topic.items() if k != "stored"},
        "pending_action": pending_action,
        "message": message,
        "dialog": _dialog_state(ws, topic_ref=topic),
    }


@tool(summary="Get Builder session.", side_effects="none")
def get_session(
    session_id: str | None = None,
    webspace_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    session = _load_session(ws, session_id)
    workbench = _ensure_workbench(ws, session=session, preview_state=(session or {}).get("preview_state") if isinstance(session, dict) else None)
    return {"ok": bool(session), "session": session, "workbench": workbench, "dialog": _dialog_state(ws)}


@tool(summary="Get Builder preview state.", side_effects="none")
def get_preview_state(
    session_id: str | None = None,
    webspace_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    session = _load_session(ws, session_id)
    if not session:
        return {"ok": False, "error": "session_not_found", "preview_state": None, "dialog": _dialog_state(ws)}
    preview = session.get("preview_state") if isinstance(session.get("preview_state"), dict) else _preview_state(session=session)
    workbench = _ensure_workbench(ws, session=session, preview_state=preview)
    return {"ok": True, "session_id": session.get("id"), "preview_state": preview, "workbench": workbench, "dialog": _dialog_state(ws)}


@tool(summary="Ensure paired Builder Prompt IDE dev webspace.", side_effects="local_write")
def ensure_dev_webspace(
    webspace_id: str | None = None,
    active_draft_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    session = _load_session(ws)
    explicit_draft_id = str(active_draft_id or "").strip() or None
    if active_draft_id:
        session = dict(session or {})
        session["draft_id"] = explicit_draft_id
    workbench = _ensure_workbench(ws, session=session, active_draft_id=explicit_draft_id or None)
    if not workbench.get("ok"):
        return {**workbench, "dialog": _dialog_state(ws)}
    return {"ok": True, "binding": workbench["binding"], "workbench": workbench, "dialog": _dialog_state(ws)}


@tool(summary="Return Builder workbench binding.", side_effects="none")
def get_workspace_binding(
    webspace_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    binding = _workbench_service().get_workspace_binding(ws)
    return {"ok": True, "binding": binding, "dialog": _dialog_state(ws)}


@tool(summary="Return URL for paired Builder Prompt IDE dev webspace.", side_effects="local_write")
def open_dev_webspace(
    webspace_id: str | None = None,
    base_url: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    session, binding = _target_session(ws)
    workbench = _ensure_workbench(ws, session=session)
    if not workbench.get("ok"):
        return {**workbench, "dialog": _dialog_state(ws)}
    result = _workbench_service().open_dev_webspace(ws, base_url=base_url)
    return {**result, "binding": workbench["binding"], "workbench": workbench, "dialog": _dialog_state(ws)}


@tool(summary="Return embedded Voice Chat widget config for Builder workbench.", side_effects="none")
def attach_dialog_widget(
    webspace_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    binding = _workbench_service().get_workspace_binding(ws)
    widget = binding.get("dialog") if isinstance(binding.get("dialog"), Mapping) else _workbench_service().dialog_widget_config(ws)
    topic = widget.get("topic") if isinstance(widget.get("topic"), Mapping) else None
    return {"ok": True, "widget": widget, "binding": binding, "dialog": _dialog_state(ws, topic_ref=topic)}


@tool(summary="Switch active Builder development draft.", side_effects="local_write")
def set_active_draft(
    draft_id: str | None = None,
    webspace_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    session = _load_session(ws)
    if draft_id and (not session or str(session.get("draft_id") or "") != str(draft_id).strip()):
        sessions = _sessions(ws)
        for item in sessions.values():
            if str(item.get("draft_id") or item.get("id") or "").strip() == str(draft_id).strip():
                session = item
                break
    workbench = _ensure_workbench(
        ws,
        session=session,
        active_draft_id=str(draft_id or "").strip() or None,
        runtime_scenario_id=_runtime_scenario_id(session),
    )
    if not workbench.get("ok"):
        return {**workbench, "dialog": _dialog_state(ws)}
    return {"ok": True, "binding": workbench["binding"], "workbench": workbench, "dialog": _dialog_state(ws)}


@tool(summary="List Builder skills/scenarios in development.", side_effects="none")
def list_development_skills(
    webspace_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    return {**_workbench_service().list_development_skills(ws), "dialog": _dialog_state(ws)}


@tool(summary="Delete Builder development draft.", side_effects="local_write")
def delete_development_skill(
    draft_id: str,
    webspace_id: str | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _source_webspace_id(webspace_id, _meta)
    result = _workbench_service().delete_development_skill(draft_id, ws)
    if result.get("ok"):
        _delete_sessions_for_draft(ws, draft_id)
    return {**result, "dialog": _dialog_state(ws)}


def handle(topic: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = dict(payload or {})
    if topic.endswith("start"):
        return start(**data)
    if topic.endswith("create_scenario_draft"):
        return create_scenario_draft(**data)
    if topic.endswith("update_current_scenario"):
        return update_current_scenario(**data)
    if topic.endswith("get_preview_state"):
        return get_preview_state(**data)
    if topic.endswith("get_session"):
        return get_session(**data)
    if topic.endswith("ensure_dev_webspace"):
        return ensure_dev_webspace(**data)
    if topic.endswith("get_workspace_binding"):
        return get_workspace_binding(**data)
    if topic.endswith("open_dev_webspace"):
        return open_dev_webspace(**data)
    if topic.endswith("attach_dialog_widget"):
        return attach_dialog_widget(**data)
    if topic.endswith("set_active_draft"):
        return set_active_draft(**data)
    if topic.endswith("list_development_skills"):
        return list_development_skills(**data)
    if topic.endswith("delete_development_skill"):
        return delete_development_skill(**data)
    return chat(**data)
