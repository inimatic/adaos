from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from adaos.sdk.core.decorators import subscribe, tool


SKILL_ID = "builder_skill"
DIALOG_CHANNEL_ID = "builder"
AGENT_ID = "agent:builder_skill:builder"
AGENT_LABEL = "\u0421\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c"
SESSIONS_KEY = "builder_skill.sessions"
CURRENT_KEY = "builder_skill.current_session"
MAX_SESSIONS = 50
WORKBENCH_REFRESH_TOPIC = "builder.workbench.ensure_requested"
PROMPT_IDE_SCENARIO_ID = "prompt_engineer_scenario"
WORKBENCH_DIRECT_ENSURE_TIMEOUT_S = 2.0

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


def _prompt_project_topic_id(session: Mapping[str, Any] | None = None, binding: Mapping[str, Any] | None = None) -> str:
    source = session if isinstance(session, Mapping) else {}
    fallback = binding if isinstance(binding, Mapping) else {}
    scenario_id = str(source.get("scenario_id") or fallback.get("runtime_scenario_id") or "").strip()
    if not scenario_id:
        return ""
    return f"prompt-project:scenario:{scenario_id}"


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
    prompt_topic_id = _prompt_project_topic_id(session=session, binding=binding)
    if prompt_topic_id:
        meta.setdefault("conversation_topic_id", prompt_topic_id)
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
                "operation": refs.get("operation"),
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
    operation = str(domain_ref.get("operation") or "").strip()
    if not webspace_id or response_action_id not in {"approve", "refuse"}:
        return
    session = _load_session(webspace_id, session_id or None)
    if not session:
        return
    if operation == "delete_draft":
        draft_id = str(domain_ref.get("draft_id") or session.get("draft_id") or "").strip()
        binding = _workbench_binding(webspace_id)
        topic = _builder_topic_ref(webspace_id, session=session, binding=binding)
        if response_action_id == "approve" and draft_id:
            result = delete_development_skill(draft_id=draft_id, webspace_id=webspace_id)
            if result.get("ok"):
                message = f"{AGENT_LABEL}: \u0443\u0434\u0430\u043b\u0438\u043b \u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a {draft_id}."
            else:
                message = f"{AGENT_LABEL}: \u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0443\u0434\u0430\u043b\u0438\u0442\u044c {draft_id}: {result.get('error') or 'unknown_error'}."
        else:
            message = f"{AGENT_LABEL}: \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u0435 {draft_id or session.get('scenario_id')} \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e."
        _safe_emit_chat(message, webspace_id=webspace_id, session=session, binding=binding, topic_ref=topic)
        return
    patches = [dict(item) for item in session.get("patches", []) if isinstance(item, Mapping)]
    matched = False
    matched_patch: dict[str, Any] | None = None
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
        matched_patch = patch
        break
    if not matched:
        return
    session["patches"] = patches
    if pending_action_id and str(session.get("pending_action_id") or "") == pending_action_id:
        session.pop("pending_action_id", None)
    session["user_summary"] = _draft_user_summary(session)
    if (
        matched_patch
        and matched_patch.get("operation") == "llm_webui_transform"
        and isinstance(session.get("preview_state"), Mapping)
    ):
        preview = copy.deepcopy(dict(session["preview_state"]))
    else:
        preview = _preview_state(session=session)
    if (
        matched_patch
        and matched_patch.get("operation") == "llm_webui_transform"
        and isinstance(session.get("webui_payload"), Mapping)
    ):
        _write_webui_payload(str(session.get("artifact_root") or ""), session["webui_payload"])
    else:
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


def _ui_texts(session: Mapping[str, Any]) -> dict[str, str]:
    if str(session.get("ui_locale") or "").strip().lower().startswith("en"):
        return {
            "default_title": "Prototype",
            "input": "Input",
            "add": "Add",
            "list": "List",
            "cards": "Cards",
        }
    return {
        "default_title": "\u041f\u0440\u043e\u0442\u043e\u0442\u0438\u043f",
        "input": "\u0412\u0432\u043e\u0434",
        "add": "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c",
        "list": "\u0421\u043f\u0438\u0441\u043e\u043a",
        "cards": "\u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0438",
    }


def _preview_state(*, session: Mapping[str, Any]) -> dict[str, Any]:
    fields = [dict(item) for item in session.get("fields", []) if isinstance(item, Mapping)]
    filters = [dict(item) for item in session.get("filters", []) if isinstance(item, Mapping)]
    datasource_id = str(session.get("datasource_id") or "items")
    table_columns = [{"field": item["id"], "label": item.get("label") or item["id"]} for item in fields]
    stored_mock_rows = session.get("mock_rows")
    mock_rows = [dict(item) for item in stored_mock_rows if isinstance(item, Mapping)] if isinstance(stored_mock_rows, list) else _mock_rows(fields)
    action_position = str(session.get("form_action_position") or "").strip().lower()
    text = _ui_texts(session)
    ui = {
        "schema": "adaos.declarative_ui.v1",
        "id": str(session.get("scenario_id") or "prototype"),
        "type": "page",
        "title": session.get("title") or text["default_title"],
        "children": [
            {
                "id": "editor",
                "type": "section",
                "label": text["input"],
                "children": [_component_for_field(item) for item in fields],
                "action_position": "top" if action_position == "top" else "bottom",
                "actions": [{"id": "add_item", "type": "button", "label": text["add"]}],
            },
            {
                "id": "items_table",
                "type": "table",
                "label": text["list"],
                "binding": datasource_id,
                "columns": table_columns,
                "visible": not bool(session.get("hide_table")),
            },
        ],
    }
    if session.get("card_view"):
        ui["children"].append(
            {
                "id": "items_cards",
                "type": "card_list",
                "label": text["cards"],
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


def _write_webui_payload(artifact_root: str | None, payload: Mapping[str, Any]) -> None:
    if not artifact_root:
        return
    root = Path(artifact_root)
    if not root.exists():
        return
    data = dict(payload)
    preview_state = data.get("preview_state") if isinstance(data.get("preview_state"), Mapping) else {}
    data.setdefault("schema", "adaos.webui.prototype.v1")
    data.setdefault("generated_by", SKILL_ID)
    (root / "webui.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if isinstance(preview_state, Mapping):
        _write_scenario_page_schema(root, preview_state)


def _write_scenario_manifest(root: Path, scenario: Mapping[str, Any], preview_state: Mapping[str, Any]) -> None:
    scenario_id = str(scenario.get("id") or preview_state.get("scenario_id") or preview_state.get("id") or root.name).strip() or root.name
    title = str(preview_state.get("title") or scenario.get("title") or scenario.get("name") or scenario_id).strip() or scenario_id
    depends = [
        str(item).strip()
        for item in (scenario.get("depends") if isinstance(scenario.get("depends"), list) else [])
        if isinstance(item, str) and str(item).strip() and str(item).strip() != SKILL_ID
    ]
    runtime = scenario.get("runtime") if isinstance(scenario.get("runtime"), Mapping) else {}
    skills = runtime.get("skills") if isinstance(runtime.get("skills"), Mapping) else {}
    required = [
        str(item).strip()
        for item in (skills.get("required") if isinstance(skills.get("required"), list) else [])
        if isinstance(item, str) and str(item).strip() and str(item).strip() != SKILL_ID
    ]
    lines = [
        f"id: {json.dumps(scenario_id, ensure_ascii=False)}",
        f"name: {json.dumps(str(scenario.get('name') or scenario_id), ensure_ascii=False)}",
        f"type: {json.dumps(str(scenario.get('type') or 'desktop'), ensure_ascii=False)}",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"description: {json.dumps(str(scenario.get('description') or 'Builder rapid prototype scenario.'), ensure_ascii=False)}",
        f"version: {json.dumps(str(scenario.get('version') or '0.1.0'), ensure_ascii=False)}",
    ]
    if depends:
        lines.append("depends:")
        lines.extend(f"  - {json.dumps(item, ensure_ascii=False)}" for item in depends)
    else:
        lines.append("depends: []")
    lines.extend(["runtime:", "  skills:"])
    if required:
        lines.append("    required:")
        lines.extend(f"      - {json.dumps(item, ensure_ascii=False)}" for item in required)
    else:
        lines.append("    required: []")
    lines.append("")
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
    table_visible = True
    for child in (ui.get("children") if isinstance(ui.get("children"), list) else []):
        if isinstance(child, Mapping) and (
            str(child.get("id") or "") == "items_table" or str(child.get("type") or "") == "table"
        ):
            table_visible = child.get("visible") is not False
            break
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
    if table_visible:
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
    depends_list = [item for item in depends_list if item != SKILL_ID]
    scenario["depends"] = depends_list
    runtime = scenario.get("runtime") if isinstance(scenario.get("runtime"), dict) else {}
    skills = runtime.get("skills") if isinstance(runtime.get("skills"), dict) else {}
    required = skills.get("required") if isinstance(skills.get("required"), list) else []
    required_list = [str(item) for item in required if isinstance(item, str)]
    required_list = [item for item in required_list if item != SKILL_ID]
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


def _developer_evidence(
    *,
    webspace_id: str,
    session: Mapping[str, Any] | None,
    preview_state: Mapping[str, Any] | None = None,
    workbench: Mapping[str, Any] | None = None,
    topic_ref: Mapping[str, Any] | None = None,
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(session, Mapping):
        return None
    topic = dict(topic_ref or {}) if isinstance(topic_ref, Mapping) else _builder_topic_ref(webspace_id, session=session, _meta=_meta)
    artifact_root = str(session.get("artifact_root") or "").strip()
    artifact_path = Path(artifact_root) if artifact_root else None
    files: list[dict[str, Any]] = []
    if artifact_path is not None:
        for name, role in (
            ("webui.json", "runtime_preview"),
            ("scenario.json", "scenario_manifest_json"),
            ("scenario.yaml", "scenario_manifest_yaml"),
        ):
            path = artifact_path / name
            files.append({"role": role, "path": str(path), "exists": path.exists()})
    patches: list[dict[str, Any]] = []
    for patch in session.get("patches", []) if isinstance(session.get("patches"), list) else []:
        if not isinstance(patch, Mapping):
            continue
        diff = patch.get("diff") if isinstance(patch.get("diff"), Mapping) else {}
        patches.append(
            {
                "id": str(patch.get("id") or ""),
                "operation": str(patch.get("operation") or ""),
                "status": str(patch.get("status") or ""),
                "review_status": str(patch.get("review_status") or "") or None,
                "pending_action_id": str(patch.get("pending_action_id") or "") or None,
                "diff_keys": sorted(str(key) for key in diff.keys()),
                "not_implemented": list(diff.get("not_implemented") or []) if isinstance(diff.get("not_implemented"), list) else [],
            }
        )
    pending_action_ids = [
        str(value)
        for value in [session.get("pending_action_id"), *(item.get("pending_action_id") for item in patches)]
        if str(value or "").strip()
    ]
    preview = preview_state if isinstance(preview_state, Mapping) else session.get("preview_state")
    preview_payload = preview if isinstance(preview, Mapping) else {}
    workbench_payload = dict(workbench or {}) if isinstance(workbench, Mapping) else {}
    projection = workbench_payload.get("projection") if isinstance(workbench_payload.get("projection"), Mapping) else {}
    return {
        "schema": "adaos.builder.developer_evidence.v1",
        "session_id": str(session.get("id") or ""),
        "scenario_id": str(session.get("scenario_id") or "") or None,
        "draft_id": str(session.get("draft_id") or "") or None,
        "artifact_root": artifact_root or None,
        "files": files,
        "schemas": {
            "preview_state": "adaos.builder.preview_state.v1",
            "webui": "adaos.webui.v1",
            "topic_ref": "adaos.conversation.topic_ref.v1",
            "pending_action": "adaos.pending_action.v1",
        },
        "route_plan": {
            "webspace_id": webspace_id,
            "dialog_channel_id": DIALOG_CHANNEL_ID,
            "conversation_id": _conversation_id(webspace_id),
            "owner": f"skill:{SKILL_ID}",
            "default_tool": f"{SKILL_ID}.chat",
            "agent_id": AGENT_ID,
            "thread_id": str(topic.get("thread_id") or "") or None,
            "topic_id": str(topic.get("topic_id") or "") or None,
        },
        "topic": {key: value for key, value in topic.items() if key != "stored"},
        "preview_refs": {
            "current_ui_type": str(preview_payload.get("current_ui", {}).get("type") or "") if isinstance(preview_payload.get("current_ui"), Mapping) else None,
            "datasource_ids": [
                str(item.get("id") or "")
                for item in preview_payload.get("datasources", [])
                if isinstance(item, Mapping) and str(item.get("id") or "")
            ],
            "pending_patch_count": len(preview_payload.get("pending_patches") or []) if isinstance(preview_payload.get("pending_patches"), list) else 0,
        },
        "patches": patches,
        "pending_action_ids": pending_action_ids,
        "workbench": {
            "ok": bool(workbench_payload.get("ok")),
            "binding": dict(workbench_payload.get("binding") or {}) if isinstance(workbench_payload.get("binding"), Mapping) else {},
            "projection_deferred": bool(projection.get("deferred")),
        },
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
    fallback_text = str(fallback or "").strip().lower()
    if field_id == "done":
        if any(token in fallback_text for token in ("complete", "execution", "done", "\u0438\u0441\u043f\u043e\u043b\u043d", "\u0432\u044b\u043f\u043e\u043b\u043d")):
            return "\u0418\u0441\u043f\u043e\u043b\u043d\u0435\u043d\u043e"
        return "\u041a\u0443\u043f\u043b\u0435\u043d\u043e"
    labels = {
        "date": "\u0414\u0430\u0442\u0430",
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
    return _text_contains_any(text, ("date", "\u0434\u0430\u0442"))


def _text_variants(text: str) -> list[str]:
    raw = str(text or "")
    variants: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        lowered = str(value or "").lower()
        if lowered and lowered not in seen:
            seen.add(lowered)
            variants.append(lowered)

    add(raw)
    for encoding in ("latin1", "cp1251"):
        try:
            add(raw.encode(encoding).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return variants


def _text_contains_any(text: str, tokens: Iterable[str]) -> bool:
    token_list = [str(token or "").lower() for token in tokens if str(token or "")]
    if not token_list:
        return False
    return any(token in variant for variant in _text_variants(text) for token in token_list)


def _text_contains_all_groups(text: str, *groups: Iterable[str]) -> bool:
    normalized_groups = [
        [str(token or "").lower() for token in group if str(token or "")]
        for group in groups
    ]
    normalized_groups = [group for group in normalized_groups if group]
    if not normalized_groups:
        return False
    for variant in _text_variants(text):
        if all(any(token in variant for token in group) for group in normalized_groups):
            return True
    return False


def _wants_add_button_above_form(text: str) -> bool:
    return _text_contains_all_groups(
        text,
        ("button", "\u043a\u043d\u043e\u043f"),
        ("add", "\u0434\u043e\u0431\u0430\u0432"),
        ("above", "top", "\u043d\u0430\u0434", "\u0432\u0435\u0440\u0445"),
        ("form", "\u0444\u043e\u0440\u043c"),
    )


def _wants_done_checkbox_first(text: str) -> bool:
    mentions_done = _text_contains_any(text, ("done", "purchased", "\u043a\u0443\u043f\u043b\u0435\u043d"))
    mentions_checkbox = _text_contains_any(text, ("checkbox", "check box", "\u0447\u0435\u043a\u0431\u043e\u043a\u0441"))
    mentions_first_column = _text_contains_all_groups(
        text,
        ("first", "\u043f\u0435\u0440\u0432"),
        ("column", "\u043a\u043e\u043b\u043e\u043d"),
    )
    return mentions_done and (mentions_checkbox or mentions_first_column)


def _wants_date_values(text: str) -> bool:
    return _mentions_date(text) and _text_contains_any(
        text,
        ("data", "value", "values", "fill", "\u0434\u0430\u043d\u043d", "\u0437\u043d\u0430\u0447\u0435\u043d", "\u0437\u0430\u043f\u043e\u043b\u043d"),
    )


def _wants_card_view(text: str) -> bool:
    return _text_contains_any(text, ("card", "cards", "\u043a\u0430\u0440\u0442\u043e\u0447", "\u043f\u043b\u0438\u0442\u043a"))


def _wants_hide_list_or_table(text: str) -> bool:
    mentions_remove = _text_contains_any(
        text,
        ("remove", "hide", "without", "\u0443\u0431\u0435\u0440", "\u0443\u0434\u0430\u043b", "\u0441\u043a\u0440\u043e\u0439", "\u0431\u0435\u0437"),
    )
    mentions_list = _text_contains_any(text, ("list", "table", "\u0441\u043f\u0438\u0441\u043e\u043a", "\u0442\u0430\u0431\u043b\u0438\u0446"))
    mentions_only_cards = _text_contains_any(text, ("only", "\u0442\u043e\u043b\u044c\u043a")) and _wants_card_view(text)
    return (mentions_remove and mentions_list) or mentions_only_cards


def _wants_execution_checkbox(text: str) -> bool:
    mentions_checkbox = _text_contains_any(text, ("checkbox", "check box", "\u0447\u0435\u043a\u0431\u043e\u043a\u0441", "\u0444\u043b\u0430\u0436\u043e\u043a"))
    mentions_done = _text_contains_any(
        text,
        (
            "done",
            "complete",
            "completed",
            "execution",
            "\u0438\u0441\u043f\u043e\u043b\u043d",
            "\u0432\u044b\u043f\u043e\u043b\u043d",
            "\u0433\u043e\u0442\u043e\u0432",
            "\u043a\u0443\u043f\u043b\u0435\u043d",
        )
    )
    return mentions_checkbox and mentions_done


def _wants_english_ui(text: str) -> bool:
    return _text_contains_any(text, ("english", "in english", "\u0430\u043d\u0433\u043b\u0438\u0439\u0441\u043a", "\u043d\u0430 \u0430\u043d\u0433\u043b"))


def _english_title(value: str) -> str:
    lowered = str(value or "").strip().lower()
    if "\u043f\u043e\u043a\u0443\u043f" in lowered or "shopping" in lowered:
        return "Shopping List"
    if "\u0437\u0430\u0434\u0430\u0447" in lowered or "todo" in lowered:
        return "Todo List"
    if lowered:
        return str(value).replace("_", " ").title()
    return "Prototype"


def _english_label(field_id: str, label: str | None = None) -> str:
    token = f"{field_id} {label or ''}".strip().lower()
    mapping = {
        "item": "Item",
        "product": "Product",
        "title": "Title",
        "name": "Name",
        "quantity": "Quantity",
        "unit": "Unit",
        "price": "Price",
        "date": "Date",
        "category": "Category",
        "availability": "Availability",
        "done": "Done",
        "notes": "Notes",
        "status": "Status",
        "owner": "Owner",
    }
    for key, value in mapping.items():
        if key in token:
            return value
    if any(item in token for item in ("\u0442\u043e\u0432\u0430\u0440", "\u043f\u0440\u043e\u0434\u0443\u043a\u0442")):
        return "Item"
    if "\u043a\u043e\u043b" in token:
        return "Quantity"
    if "\u0446\u0435\u043d" in token:
        return "Price"
    if "\u0434\u0430\u0442" in token:
        return "Date"
    if "\u043a\u0430\u0442\u0435\u0433" in token:
        return "Category"
    if "\u043d\u0430\u043b\u0438\u0447" in token:
        return "Availability"
    if any(item in token for item in ("\u043a\u0443\u043f\u043b", "\u0438\u0441\u043f\u043e\u043b\u043d", "\u0432\u044b\u043f\u043e\u043b\u043d")):
        return "Done"
    fallback = str(label or field_id or "Field").strip()
    return fallback.replace("_", " ").title()


def _translate_session_to_english(session: dict[str, Any], fields: list[dict[str, Any]]) -> None:
    session["ui_locale"] = "en"
    session["title"] = _english_title(str(session.get("title") or session.get("scenario_id") or "Prototype"))
    for field in fields:
        field["label"] = _english_label(str(field.get("id") or ""), str(field.get("label") or ""))
    session["fields"] = fields


def _repo_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "src" / "adaos" / "abi" / "webui.v1.schema.json").exists():
        return cwd
    for parent in Path(__file__).resolve().parents:
        if (parent / "src" / "adaos" / "abi" / "webui.v1.schema.json").exists():
            return parent
    return cwd


def _load_webui_schema() -> dict[str, Any]:
    path = _repo_root() / "src" / "adaos" / "abi" / "webui.v1.schema.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _current_webui_payload(session: Mapping[str, Any], preview_state: Mapping[str, Any]) -> dict[str, Any]:
    artifact_root = str(session.get("artifact_root") or "").strip()
    payload: dict[str, Any] = {}
    if artifact_root:
        path = Path(artifact_root) / "webui.json"
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
                if isinstance(raw, dict):
                    payload = raw
            except Exception:
                payload = {}
    payload.setdefault("schema", "adaos.webui.prototype.v1")
    payload.setdefault("generated_by", SKILL_ID)
    payload["preview_state"] = copy.deepcopy(dict(preview_state))
    return payload


def _balanced_json_object(text: str) -> str | None:
    source = str(text or "")
    for start, char in enumerate(source):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(source)):
            current = source[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    return source[start : index + 1]
    return None


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    candidates = [raw]
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", raw, re.IGNORECASE | re.DOTALL):
        candidates.insert(0, match.group(1).strip())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        fragment = _balanced_json_object(candidate)
        if fragment:
            try:
                parsed = json.loads(fragment)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    raise ValueError("LLM response does not contain a JSON object")


def _validate_webui_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    schema = _load_webui_schema()
    if not schema:
        return {"ok": True, "schema": "missing"}
    try:
        from jsonschema import Draft202012Validator

        Draft202012Validator(schema).validate(dict(payload))
        return {"ok": True, "schema": schema.get("$id") or "adaos.webui.v1"}
    except Exception as exc:
        return {"ok": False, "error": "webui_schema_validation_failed", "detail": f"{type(exc).__name__}: {exc}"}


def _normalise_llm_webui_payload(
    payload: Mapping[str, Any],
    *,
    previous_preview: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    data = dict(payload)
    preview = data.get("preview_state") if isinstance(data.get("preview_state"), Mapping) else None
    if preview is None and isinstance(data.get("current_ui"), Mapping):
        preview = data
        data = {"schema": "adaos.webui.prototype.v1", "generated_by": SKILL_ID, "preview_state": preview}
    if not isinstance(preview, Mapping):
        raise ValueError("LLM payload must contain preview_state")
    preview_data = copy.deepcopy(dict(preview))
    if not isinstance(preview_data.get("current_ui"), Mapping):
        raise ValueError("preview_state.current_ui is required")
    if not isinstance(preview_data.get("datasources"), list):
        preview_data["datasources"] = copy.deepcopy(previous_preview.get("datasources") or [])
    if not isinstance(preview_data.get("mock_data"), Mapping):
        preview_data["mock_data"] = copy.deepcopy(previous_preview.get("mock_data") or {})
    for key in ("session_id", "title", "version"):
        if not preview_data.get(key) and previous_preview.get(key):
            preview_data[key] = copy.deepcopy(previous_preview.get(key))
    data.setdefault("schema", "adaos.webui.prototype.v1")
    data.setdefault("generated_by", SKILL_ID)
    data["preview_state"] = preview_data
    return data, preview_data


def _merge_session_from_preview(session: dict[str, Any], preview_state: Mapping[str, Any]) -> None:
    title = str(preview_state.get("title") or "").strip()
    if title:
        session["title"] = title
    datasources = preview_state.get("datasources") if isinstance(preview_state.get("datasources"), list) else []
    datasource = datasources[0] if datasources and isinstance(datasources[0], Mapping) else {}
    if datasource:
        datasource_id = str(datasource.get("id") or "").strip()
        if datasource_id:
            session["datasource_id"] = datasource_id
        fields = [dict(item) for item in datasource.get("fields", []) if isinstance(item, Mapping)]
        if fields:
            session["fields"] = fields
    mock_data = preview_state.get("mock_data") if isinstance(preview_state.get("mock_data"), Mapping) else {}
    datasource_id = str(session.get("datasource_id") or "items")
    rows = mock_data.get(datasource_id)
    if isinstance(rows, list):
        session["mock_rows"] = [dict(item) for item in rows if isinstance(item, Mapping)]
    filters = preview_state.get("filters") if isinstance(preview_state.get("filters"), list) else None
    if filters is not None:
        session["filters"] = [dict(item) for item in filters if isinstance(item, Mapping)]
    ui = preview_state.get("current_ui") if isinstance(preview_state.get("current_ui"), Mapping) else {}
    children = ui.get("children") if isinstance(ui.get("children"), list) else []
    session["card_view"] = any(
        isinstance(child, Mapping) and str(child.get("type") or "") == "card_list" and child.get("visible") is not False
        for child in children
    )
    table_children = [
        child
        for child in children
        if isinstance(child, Mapping) and (str(child.get("type") or "") == "table" or str(child.get("id") or "") == "items_table")
    ]
    session["hide_table"] = bool(
        (table_children and table_children[0].get("visible") is False)
        or (not table_children and session.get("card_view"))
    )
    editor = next(
        (
            child
            for child in children
            if isinstance(child, Mapping) and str(child.get("id") or "") == "editor"
        ),
        {},
    )
    action_position = str(editor.get("action_position") or preview_state.get("form_action_position") or "").strip().lower() if isinstance(editor, Mapping) else ""
    if action_position:
        session["form_action_position"] = "top" if action_position == "top" else "bottom"


def _apply_llm_webui_transform(
    *,
    session: Mapping[str, Any],
    instruction: str,
    preview_state: Mapping[str, Any],
    _meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current_payload = _current_webui_payload(session, preview_state)
    schema = _load_webui_schema()
    history = [
        {
            "operation": str(item.get("operation") or ""),
            "summary": str(item.get("summary") or ""),
            "status": str(item.get("status") or ""),
        }
        for item in (session.get("patches") if isinstance(session.get("patches"), list) else [])[-8:]
        if isinstance(item, Mapping)
    ]
    system_prompt = (
        "You are AdaOS Builder. Transform the current prototype UI according to the user's instruction. "
        "Return only one JSON object. The JSON must keep schema='adaos.webui.prototype.v1' and must contain preview_state. "
        "preview_state.current_ui is the immediate Builder preview contract; scenario.json will be regenerated from it. "
        "Use the supplied adaos.webui.v1 schema as the outer webui.json compatibility contract. "
        "Do not include markdown, explanations, code fences, or unsafe side effects."
    )
    user_prompt = json.dumps(
        {
            "instruction": instruction,
            "scenario_id": session.get("scenario_id"),
            "title": session.get("title"),
            "current_webui_json": current_payload,
            "recent_patch_history": history,
            "webui_v1_schema": schema,
            "required_output_shape": {
                "schema": "adaos.webui.prototype.v1",
                "generated_by": SKILL_ID,
                "preview_state": {
                    "title": "string",
                    "current_ui": "object",
                    "datasources": "array",
                    "mock_data": "object",
                    "filters": "array optional",
                    "form_action_position": "top|bottom optional",
                },
                "comment": "short text optional",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    try:
        from adaos.sdk.llm.llm_client import send_response

        response = send_response(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0,
            max_tokens=6000,
            timeout=75,
        )
        output_text = str(response.get("output_text") or "")
        parsed = _extract_json_object(output_text)
        payload, preview = _normalise_llm_webui_payload(parsed, previous_preview=preview_state)
        validation = _validate_webui_payload(payload)
        if not validation.get("ok"):
            return validation
        return {
            "ok": True,
            "payload": payload,
            "preview_state": preview,
            "comment": str(parsed.get("comment") or parsed.get("summary") or "").strip(),
            "validation": validation,
        }
    except Exception as exc:
        return {"ok": False, "error": "llm_webui_transform_failed", "detail": f"{type(exc).__name__}: {exc}"}


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


def _publish_prompt_project_selection(
    webspace_id: str,
    *,
    session: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    scenario_id = str(session.get("scenario_id") or "").strip()
    if not scenario_id:
        return {"ok": False, "error": "scenario_id_missing"}
    payload_base = {
        "source_webspace_id": webspace_id,
        "webspace_id": webspace_id,
        "object_type": "scenario",
        "object_id": scenario_id,
        "scenario_id": scenario_id,
        "draft_id": str(session.get("draft_id") or "").strip() or None,
        "reason": reason,
    }
    try:
        from adaos.sdk.data import events

        events.publish(
            "scenario.workflow.set_state",
            {
                "state": "tz",
                **payload_base,
                "scenario_id": PROMPT_IDE_SCENARIO_ID,
                "selected_scenario_id": scenario_id,
            },
            source=SKILL_ID,
        )
        events.publish("prompt.project.changed", payload_base, source=SKILL_ID)
        events.publish("builder.preview.selected", payload_base, source=SKILL_ID)
        return {
            "ok": True,
            "published": ["scenario.workflow.set_state", "prompt.project.changed", "builder.preview.selected"],
            "payload": payload_base,
        }
    except Exception as exc:
        return {"ok": False, "error": "prompt_project_selection_publish_failed", "detail": f"{type(exc).__name__}: {exc}"}


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


def _normalise_command_text(text: str) -> str:
    lowered = str(text or "").strip().lower().replace("\u0451", "\u0435")
    lowered = re.sub(r"^\s*(?:builder|\u0441\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c)\s*[:,;\-]?\s*", "", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _strip_command_ref(value: str) -> str:
    token = str(value or "").strip(" \t\r\n:;,.!?()[]{}\"'\u00ab\u00bb")
    fillers = (
        "\u043d\u0430 ",
        "\u043a ",
        "\u043f\u0440\u043e\u0435\u043a\u0442 ",
        "\u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f ",
        "\u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0439 ",
        "\u0441\u0446\u0435\u043d\u0430\u0440\u0438\u044e ",
        "\u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a ",
        "\u043d\u0430\u0432\u044b\u043a ",
        "project ",
        "prototype ",
        "scenario ",
        "draft ",
        "skill ",
    )
    changed = True
    while changed:
        changed = False
        lowered = token.lower()
        for filler in fillers:
            if lowered.startswith(filler):
                token = token[len(filler) :].strip(" \t\r\n:;,.!?()[]{}\"'\u00ab\u00bb")
                changed = True
                break
    return token


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _project_words() -> tuple[str, ...]:
    return (
        "\u043f\u0440\u043e\u0435\u043a\u0442",
        "\u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f",
        "\u0441\u0446\u0435\u043d\u0430\u0440",
        "\u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a",
        "\u043d\u0430\u0432\u044b\u043a",
        "project",
        "prototype",
        "scenario",
        "draft",
        "skill",
    )


def _is_explicit_create_request(text: str) -> bool:
    lowered = _normalise_command_text(text)
    return _has_any(
        lowered,
        (
            "create",
            "build",
            "make new",
            "new app",
            "new scenario",
            "new prototype",
            "new skill",
            "lets build",
            "let's build",
            "build it",
            "\u0441\u043e\u0437\u0434",
            "\u0441\u0434\u0435\u043b\u0430\u0435\u043c",
            "\u0434\u0430\u0432\u0430\u0439 \u0441\u0434\u0435\u043b",
            "\u0441\u043e\u0431\u0435\u0440",
            "\u043f\u043e\u0441\u0442\u0440\u043e\u0438",
            "\u043d\u043e\u0432\u044b\u0439 \u043f\u0440\u043e\u0435\u043a\u0442",
            "\u043d\u043e\u0432\u044b\u0439 \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f",
            "\u043d\u043e\u0432\u043e\u0435 \u043f\u0440\u0438\u043b\u043e\u0436",
            "\u043d\u043e\u0432\u044b\u0439 \u0441\u0446\u0435\u043d\u0430\u0440",
            "\u043d\u043e\u0432\u044b\u0439 \u043d\u0430\u0432\u044b\u043a",
        ),
    )


def _parse_builder_command(text: str, *, allow_create: bool = True, has_session: bool = False) -> dict[str, Any]:
    raw = str(text or "").strip()
    lowered = _normalise_command_text(raw)
    if not lowered:
        return {"intent": "none"}

    if _has_any(
        lowered,
        (
            "\u0447\u0442\u043e \u0432 \u0440\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u043a\u0435",
            "\u043f\u043e\u043a\u0430\u0436\u0438 \u043f\u0440\u043e\u0435\u043a\u0442",
            "\u043f\u043e\u043a\u0430\u0436\u0438 \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f",
            "\u043f\u043e\u043a\u0430\u0436\u0438 \u0447\u0435\u0440\u043d\u043e\u0432",
            "\u0441\u043f\u0438\u0441\u043e\u043a \u043f\u0440\u043e\u0435\u043a\u0442",
            "\u0441\u043f\u0438\u0441\u043e\u043a \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f",
            "\u0441\u043f\u0438\u0441\u043e\u043a \u0447\u0435\u0440\u043d\u043e\u0432",
            "list projects",
            "list drafts",
            "show projects",
            "show drafts",
            "show prototypes",
        ),
    ):
        return {"intent": "project.list", "confidence": 1.0, "source": "deterministic"}

    if _has_any(
        lowered,
        (
            "\u0447\u0442\u043e \u0432\u044b\u0431\u0440\u0430\u043d",
            "\u0447\u0442\u043e \u0441\u0435\u0439\u0447\u0430\u0441 \u0432\u044b\u0431\u0440\u0430\u043d",
            "\u0442\u0435\u043a\u0443\u0449\u0438\u0439 \u043f\u0440\u043e\u0435\u043a\u0442",
            "\u0442\u0435\u043a\u0443\u0449\u0438\u0439 \u043f\u0440\u043e\u0442\u043e\u0442\u0438\u043f",
            "\u043d\u0430\u0434 \u0447\u0435\u043c \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u043c",
            "current project",
            "current draft",
            "what is selected",
        ),
    ):
        return {"intent": "project.current", "confidence": 1.0, "source": "deterministic"}

    delete_verb = _has_any(lowered, ("delete", "remove project", "\u0443\u0434\u0430\u043b", "\u0441\u043e\u0442\u0440"))
    field_word = _has_any(lowered, ("field", "column", "\u043f\u043e\u043b\u0435", "\u043a\u043e\u043b\u043e\u043d"))
    if delete_verb and not field_word:
        current = _has_any(lowered, ("current", "\u0442\u0435\u043a\u0443\u0449", "\u0432\u044b\u0431\u0440\u0430\u043d"))
        if current or _has_any(lowered, _project_words()):
            ref = ""
            match = re.search(r"(?:delete|remove project|\u0443\u0434\u0430\u043b(?:\u0438|\u0438\u0442\u044c)?|\u0441\u043e\u0442\u0440(?:\u0438|\u0435\u0442\u044c)?)\s+(.+)$", lowered)
            if match:
                ref = _strip_command_ref(match.group(1))
            return {
                "intent": "project.delete",
                "project_ref": "" if current else ref,
                "target": "current" if current else "ref",
                "confidence": 1.0,
                "source": "deterministic",
            }

    for pattern in (
        r"^(?:switch to|select|open)\s+(.+)$",
        r"^(?:\u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447(?:\u0438\u0441\u044c|\u0438|\u0438\u0442\u044c\u0441\u044f)?|\u0432\u044b\u0431\u0435\u0440(?:\u0438|\u0430\u0442\u044c)?|\u043e\u0442\u043a\u0440\u043e\u0439|\u0440\u0430\u0431\u043e\u0442\u0430\u0435\u043c \u0441|\u043f\u0435\u0440\u0435\u0439\u0434\u0438 \u043a)\s+(.+)$",
    ):
        match = re.search(pattern, lowered)
        if match:
            ref = _strip_command_ref(match.group(1))
            if ref:
                return {"intent": "project.switch", "project_ref": ref, "confidence": 1.0, "source": "deterministic"}

    if allow_create and (_is_explicit_create_request(raw) or (not has_session and _is_create_request(raw))):
        return {"intent": "project.create", "idea": raw, "confidence": 1.0, "source": "deterministic"}

    return {"intent": "none"}


def _command_hint_message() -> str:
    return (
        f"{AGENT_LABEL}: \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u0440\u043e\u0435\u043a\u0442 \u0438\u043b\u0438 \u0441\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u043d\u043e\u0432\u044b\u0439. "
        "\u041f\u0440\u0438\u043c\u0435\u0440\u044b: \u00ab\u0441\u043e\u0437\u0434\u0430\u0439 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u0441\u043f\u0438\u0441\u043e\u043a \u043f\u043e\u043a\u0443\u043f\u043e\u043a\u00bb, "
        "\u00ab\u043f\u043e\u043a\u0430\u0436\u0438 \u043f\u0440\u043e\u0435\u043a\u0442\u044b\u00bb, \u00ab\u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0438\u0441\u044c \u043d\u0430 demo_scenario\u00bb."
    )


def _session_ref_values(session: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("id", "draft_id", "scenario_id", "title", "source_idea"):
        value = str(session.get(key) or "").strip()
        if value:
            values.append(value)
    return values


def _safe_ref_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalise_command_text(value)).strip("_")


def _session_summary(session: Mapping[str, Any]) -> dict[str, Any]:
    scenario_id = str(session.get("scenario_id") or "").strip()
    draft_id = str(session.get("draft_id") or session.get("id") or "").strip()
    return {
        "session_id": str(session.get("id") or "").strip(),
        "draft_id": draft_id or None,
        "scenario_id": scenario_id or None,
        "title": str(session.get("title") or scenario_id or draft_id or "prototype").strip(),
        "updated_at": session.get("updated_at"),
    }


def _development_sessions(webspace_id: str) -> list[dict[str, Any]]:
    sessions = [dict(item) for item in _sessions(webspace_id).values() if isinstance(item, Mapping)]
    return sorted(sessions, key=lambda item: float(item.get("updated_at") or 0), reverse=True)


def _resolve_project_session(webspace_id: str, project_ref: str, *, current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    ref = _strip_command_ref(project_ref)
    if not ref and isinstance(current, Mapping):
        return {"status": "found", "session": copy.deepcopy(dict(current)), "matches": [_session_summary(current)]}
    if not ref:
        return {"status": "not_found", "matches": []}
    ref_norm = _normalise_command_text(ref)
    ref_safe = _safe_ref_token(ref)
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for session in _development_sessions(webspace_id):
        values = _session_ref_values(session)
        value_norms = [_normalise_command_text(value) for value in values]
        value_safe = [_safe_ref_token(value) for value in values]
        if ref_norm in value_norms or ref_safe in value_safe:
            exact.append(session)
            continue
        blob_norm = " ".join(value_norms)
        blob_safe = " ".join(value_safe)
        if (ref_norm and ref_norm in blob_norm) or (ref_safe and ref_safe in blob_safe):
            partial.append(session)
    matches = exact or partial
    if len(matches) == 1:
        return {"status": "found", "session": copy.deepcopy(matches[0]), "matches": [_session_summary(matches[0])]}
    if len(matches) > 1:
        return {"status": "ambiguous", "matches": [_session_summary(item) for item in matches[:5]]}
    return {"status": "not_found", "matches": []}


def _builder_command_response(
    *,
    webspace_id: str,
    message: str,
    status: str,
    command: Mapping[str, Any],
    session: Mapping[str, Any] | None = None,
    binding: Mapping[str, Any] | None = None,
    topic_ref: Mapping[str, Any] | None = None,
    _meta: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    topic = dict(topic_ref or {}) if isinstance(topic_ref, Mapping) else _builder_topic_ref(webspace_id, session=session, binding=binding, _meta=_meta)
    _safe_emit_chat(message, webspace_id=webspace_id, _meta=_meta, session=session, binding=binding, topic_ref=topic)
    payload: dict[str, Any] = {
        "ok": True,
        "status": status,
        "command": dict(command),
        "message": message,
        "topic": {k: v for k, v in topic.items() if k != "stored"},
        "dialog": _dialog_state(webspace_id, topic_ref=topic),
    }
    if session is not None:
        payload["session"] = dict(session)
        payload["session_id"] = session.get("id")
        payload["scenario_id"] = session.get("scenario_id")
        payload["draft_id"] = session.get("draft_id")
    if binding is not None:
        payload["binding"] = dict(binding)
    if extra:
        payload.update(dict(extra))
    return payload


def _format_project_list(items: list[dict[str, Any]], active_session_id: str | None) -> str:
    if not items:
        return _command_hint_message()
    lines = []
    for item in items[:8]:
        mark = "* " if active_session_id and item.get("session_id") == active_session_id else "- "
        title = str(item.get("title") or item.get("scenario_id") or item.get("draft_id") or "prototype")
        scenario_id = str(item.get("scenario_id") or "")
        draft_id = str(item.get("draft_id") or "")
        ref = scenario_id or draft_id
        lines.append(f"{mark}{title} ({ref})")
    return f"{AGENT_LABEL}: \u043f\u0440\u043e\u0435\u043a\u0442\u044b \u0432 \u0440\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u043a\u0435:\n" + "\n".join(lines)


def _handle_project_list_command(
    *,
    webspace_id: str,
    session: Mapping[str, Any] | None,
    binding: Mapping[str, Any],
    topic: Mapping[str, Any],
    command: Mapping[str, Any],
    _meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    items = [_session_summary(item) for item in _development_sessions(webspace_id)]
    message = _format_project_list(items, str((session or {}).get("id") or ""))
    return _builder_command_response(
        webspace_id=webspace_id,
        message=message,
        status="project_list",
        command=command,
        session=session,
        binding=binding,
        topic_ref=topic,
        _meta=_meta,
        extra={"items": items},
    )


def _handle_project_current_command(
    *,
    webspace_id: str,
    session: Mapping[str, Any] | None,
    binding: Mapping[str, Any],
    topic: Mapping[str, Any],
    command: Mapping[str, Any],
    _meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(session, Mapping):
        return _builder_command_response(
            webspace_id=webspace_id,
            message=_command_hint_message(),
            status="target_required",
            command=command,
            binding=binding,
            topic_ref=topic,
            _meta=_meta,
            extra={"needs_selection": True},
        )
    summary = _session_summary(session)
    message = (
        f"{AGENT_LABEL}: \u0441\u0435\u0439\u0447\u0430\u0441 \u0432\u044b\u0431\u0440\u0430\u043d "
        f"{summary.get('title')} ({summary.get('scenario_id') or summary.get('draft_id')})."
    )
    return _builder_command_response(
        webspace_id=webspace_id,
        message=message,
        status="project_current",
        command=command,
        session=session,
        binding=binding,
        topic_ref=topic,
        _meta=_meta,
        extra={"project": summary},
    )


def _handle_project_switch_command(
    *,
    webspace_id: str,
    command: Mapping[str, Any],
    _meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    current, binding = _target_session(webspace_id)
    resolution = _resolve_project_session(webspace_id, str(command.get("project_ref") or ""), current=current)
    if resolution.get("status") != "found":
        topic = _builder_topic_ref(webspace_id, session=current, binding=binding, _meta=_meta)
        if resolution.get("status") == "ambiguous":
            message = f"{AGENT_LABEL}: \u043d\u0430\u0448\u0435\u043b \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u043f\u0440\u043e\u0435\u043a\u0442\u043e\u0432. \u0423\u0442\u043e\u0447\u043d\u0438\u0442\u0435 id."
            status = "project_ambiguous"
        else:
            message = f"{AGENT_LABEL}: \u043d\u0435 \u043d\u0430\u0448\u0435\u043b \u043f\u0440\u043e\u0435\u043a\u0442 \u00ab{command.get('project_ref') or ''}\u00bb. \u041d\u0430\u043f\u0438\u0448\u0438\u0442\u0435: \u00ab\u043f\u043e\u043a\u0430\u0436\u0438 \u043f\u0440\u043e\u0435\u043a\u0442\u044b\u00bb."
            status = "project_not_found"
        return _builder_command_response(
            webspace_id=webspace_id,
            message=message,
            status=status,
            command=command,
            session=current,
            binding=binding,
            topic_ref=topic,
            _meta=_meta,
            extra={"matches": resolution.get("matches") or []},
        )

    selected = dict(resolution["session"])
    preview = selected.get("preview_state") if isinstance(selected.get("preview_state"), Mapping) else _preview_state(session=selected)
    workbench = _ensure_workbench(webspace_id, session=selected, preview_state=preview)
    binding = workbench.get("binding") if isinstance(workbench.get("binding"), Mapping) else _workbench_binding(webspace_id)
    topic = _builder_topic_ref(webspace_id, session=selected, binding=binding, _meta=_meta)
    selected["preview_state"] = preview
    selected["thread_id"] = str(topic.get("thread_id") or "").strip() or None
    selected["topic_id"] = str(topic.get("topic_id") or "").strip() or None
    selected["topic_ref"] = {k: v for k, v in topic.items() if k != "stored"}
    _save_session(webspace_id, selected)
    prompt_selection = _publish_prompt_project_selection(
        webspace_id,
        session=selected,
        reason="builder_project_switched",
    )
    summary = _session_summary(selected)
    message = f"{AGENT_LABEL}: \u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0438\u043b\u0441\u044f \u043d\u0430 {summary.get('title')} ({summary.get('scenario_id') or summary.get('draft_id')})."
    return _builder_command_response(
        webspace_id=webspace_id,
        message=message,
        status="project_switched",
        command=command,
        session=selected,
        binding=binding,
        topic_ref=topic,
        _meta=_meta,
        extra={"project": summary, "workbench": workbench, "prompt_selection": prompt_selection},
    )


def _handle_project_delete_command(
    *,
    webspace_id: str,
    session: Mapping[str, Any] | None,
    binding: Mapping[str, Any],
    topic: Mapping[str, Any],
    command: Mapping[str, Any],
    _meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    resolution = _resolve_project_session(
        webspace_id,
        "" if command.get("target") == "current" else str(command.get("project_ref") or ""),
        current=session,
    )
    if resolution.get("status") != "found":
        message = f"{AGENT_LABEL}: \u043d\u0435 \u043f\u043e\u043d\u044f\u043b, \u043a\u0430\u043a\u043e\u0439 \u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a \u0443\u0434\u0430\u043b\u0438\u0442\u044c. \u041f\u0440\u0438\u043c\u0435\u0440: \u00ab\u0443\u0434\u0430\u043b\u0438 \u0442\u0435\u043a\u0443\u0449\u0438\u0439\u00bb."
        return _builder_command_response(
            webspace_id=webspace_id,
            message=message,
            status="target_required",
            command=command,
            session=session,
            binding=binding,
            topic_ref=topic,
            _meta=_meta,
            extra={"matches": resolution.get("matches") or [], "needs_selection": True},
        )
    selected = dict(resolution["session"])
    draft_id = str(selected.get("draft_id") or "").strip()
    if not draft_id:
        message = f"{AGENT_LABEL}: \u0443 {selected.get('scenario_id') or selected.get('id')} \u043d\u0435\u0442 Builder draft id \u0434\u043b\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f."
        return _builder_command_response(
            webspace_id=webspace_id,
            message=message,
            status="delete_not_available",
            command=command,
            session=selected,
            binding=binding,
            topic_ref=topic,
            _meta=_meta,
        )
    delete_patch = {
        "id": f"patch_delete_{_hash_suffix(draft_id + str(_now()))}",
        "target": "builder_draft",
        "operation": "delete_draft",
        "status": "proposed",
        "summary": f"Delete Builder draft {draft_id}",
        "side_effect_class": "local_delete",
        "diff": {"draft_id": draft_id, "scenario_id": selected.get("scenario_id")},
    }
    pending_action = _publish_review_pending_action(
        webspace_id=webspace_id,
        session=selected,
        request_text=str(command.get("raw") or command.get("project_ref") or "delete current draft"),
        kind="builder.scenario_delete.review",
        summary=f"Delete Builder draft {draft_id}",
        _meta=_meta,
        patch=delete_patch,
    )
    if pending_action and pending_action.get("id"):
        selected["pending_action_id"] = pending_action.get("id")
        _save_session(webspace_id, selected)
        message = f"{AGENT_LABEL}: \u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u0438\u043b \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u0435 {draft_id}. \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u0435 Pending Action."
        status = "delete_review_required"
    else:
        message = f"{AGENT_LABEL}: \u043d\u0435 \u0441\u043c\u043e\u0433 \u0441\u043e\u0437\u0434\u0430\u0442\u044c Pending Action \u0434\u043b\u044f \u0443\u0434\u0430\u043b\u0435\u043d\u0438\u044f {draft_id}."
        status = "delete_review_failed"
    return _builder_command_response(
        webspace_id=webspace_id,
        message=message,
        status=status,
        command=command,
        session=selected,
        binding=binding,
        topic_ref=topic,
        _meta=_meta,
        extra={"pending_action": pending_action, "patch": delete_patch},
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
        direct = _ensure_workbench_runtime_direct(
            svc,
            webspace_id=webspace_id,
            active_draft_id=draft_id,
            runtime_scenario_id=scenario_id,
            preview_state=preview_state,
        )
        if isinstance(direct.get("binding"), Mapping):
            binding = dict(direct["binding"])
        event = {"ok": True, "skipped": "direct_workbench_ensure"} if direct.get("ok") else _request_workbench_refresh(
            {
                "source_webspace_id": webspace_id,
                "active_draft_id": draft_id,
                "runtime_scenario_id": scenario_id,
                "preview_state": dict(preview_state or {}),
            }
        )
    except Exception as exc:
        return {"ok": False, "error": "workbench_unavailable", "detail": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": True,
        "binding": binding,
        "projection": {
            "ok": True,
            "snapshot": snapshot,
            "deferred": True,
            "event": event,
            "direct": direct,
        },
    }


def _ensure_workbench_runtime_direct(
    svc: Any,
    *,
    webspace_id: str,
    active_draft_id: str | None,
    runtime_scenario_id: str | None,
    preview_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    ensure = getattr(svc, "ensure_dev_webspace", None)
    if not callable(ensure):
        return {"ok": False, "skipped": "ensure_dev_webspace_unavailable"}
    try:
        value = ensure(
            webspace_id,
            active_draft_id=active_draft_id,
            runtime_scenario_id=runtime_scenario_id,
            preview_state=preview_state,
            wait_for_rebuild=False,
        )
    except TypeError:
        return {"ok": False, "skipped": "ensure_dev_webspace_signature_mismatch"}
    except Exception as exc:
        return {"ok": False, "error": "ensure_dev_webspace_failed", "detail": f"{type(exc).__name__}: {exc}"}

    if inspect.isawaitable(value):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                value = asyncio.run(asyncio.wait_for(value, timeout=WORKBENCH_DIRECT_ENSURE_TIMEOUT_S))
            except TimeoutError:
                return {
                    "ok": False,
                    "skipped": "ensure_dev_webspace_timeout",
                    "timeout_s": WORKBENCH_DIRECT_ENSURE_TIMEOUT_S,
                }
            except Exception as exc:
                return {"ok": False, "error": "ensure_dev_webspace_failed", "detail": f"{type(exc).__name__}: {exc}"}
        else:
            try:
                loop.create_task(value)
            except Exception as exc:
                return {"ok": False, "error": "ensure_dev_webspace_schedule_failed", "detail": f"{type(exc).__name__}: {exc}"}
            return {"ok": True, "scheduled": True}

    if isinstance(value, Mapping):
        return {"ok": True, "binding": dict(value), "result": dict(value)}
    return {"ok": True, "result": value}


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
    command = _parse_builder_command(utterance, has_session=bool(session))
    command["raw"] = utterance
    intent = str(command.get("intent") or "")
    if intent == "project.list":
        return _handle_project_list_command(webspace_id=ws, session=session, binding=binding, topic=topic, command=command, _meta=_meta)
    if intent == "project.current":
        return _handle_project_current_command(webspace_id=ws, session=session, binding=binding, topic=topic, command=command, _meta=_meta)
    if intent == "project.switch":
        return _handle_project_switch_command(webspace_id=ws, command=command, _meta=_meta)
    if intent == "project.delete":
        return _handle_project_delete_command(webspace_id=ws, session=session, binding=binding, topic=topic, command=command, _meta=_meta)
    if intent == "project.create":
        result = create_scenario_draft(idea=utterance or "prototype app", webspace_id=ws, _meta=_meta)
        if result.get("ok"):
            message = str(result.get("message") or "")
            _safe_emit_chat(message, webspace_id=ws, _meta=_meta, topic_ref=result.get("topic") if isinstance(result.get("topic"), Mapping) else None)
            return {**result, "command": command, "dialog": _dialog_state(ws, topic_ref=result.get("topic") if isinstance(result.get("topic"), Mapping) else topic)}
        return {**result, "command": command, "dialog": _dialog_state(ws, topic_ref=topic)}
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
    prompt_selection = _publish_prompt_project_selection(
        ws,
        session=session,
        reason="builder_project_created",
    )
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
        "prompt_selection": prompt_selection,
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
    if _wants_card_view(text):
        session["card_view"] = True
        session["hide_table"] = True
        patch["operation"] = "change_view_representation"
        patch["diff"] = {"card_view": True, "hide_table": True}
        lowered = ""
    elif _wants_hide_list_or_table(text):
        session["hide_table"] = True
        if _wants_card_view(text) or session.get("card_view"):
            session["card_view"] = True
        patch["operation"] = "change_view_representation"
        patch["diff"] = {"card_view": bool(session.get("card_view")), "hide_table": True}
        lowered = ""
    elif _wants_execution_checkbox(text):
        label = "\u0418\u0441\u043f\u043e\u043b\u043d\u0435\u043d\u043e" if _text_contains_any(
            text,
            ("\u0438\u0441\u043f\u043e\u043b\u043d", "\u0432\u044b\u043f\u043e\u043b\u043d", "complete", "execution"),
        ) else "\u041a\u0443\u043f\u043b\u0435\u043d\u043e"
        fields, field, added = _ensure_field(fields, label=label, field_id="done", field_type="boolean")
        session["fields"] = fields
        patch["operation"] = "add_field" if added else "ensure_field"
        patch["diff"] = {"field": field, "added": added, "component": "checkbox"}
        lowered = ""
    elif _wants_english_ui(text):
        _translate_session_to_english(session, fields)
        patch["operation"] = "translate_ui"
        patch["diff"] = {"locale": "en", "fields": [dict(item) for item in session.get("fields", []) if isinstance(item, Mapping)]}
        lowered = ""
    if any(token in lowered for token in ("карточ", "card")):
        session["card_view"] = True
        patch["operation"] = "change_view_representation"
        session["hide_table"] = True
        patch["diff"] = {"card_view": True, "hide_table": True}
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
        label = _extract_field_label(text) or ("\u0426\u0435\u043d\u0430" if _text_contains_any(text, ("\u0446\u0435\u043d", "price")) else None)
        if label:
            fid = _field_id(label)
            if not any(str(item.get("id")) == fid for item in fields):
                field = {"id": fid, "type": _field_type_for_id(fid, label), "label": _default_label_for_field(fid, label), "required": False}
                fields.append(field)
                session["fields"] = fields
                patch["operation"] = "add_field"
                patch["diff"] = {"field": field}
    if patch["operation"] == "noop":
        base_preview = session.get("preview_state") if isinstance(session.get("preview_state"), dict) else _preview_state(session=session)
        llm_patch = _apply_llm_webui_transform(session=session, instruction=text, preview_state=base_preview, _meta=_meta)
        if llm_patch.get("ok"):
            preview_from_llm = llm_patch.get("preview_state") if isinstance(llm_patch.get("preview_state"), Mapping) else base_preview
            payload_from_llm = llm_patch.get("payload") if isinstance(llm_patch.get("payload"), Mapping) else None
            patch["operation"] = "llm_webui_transform"
            patch["diff"] = {
                "schema_valid": True,
                "comment": str(llm_patch.get("comment") or ""),
                "validation": dict(llm_patch.get("validation") or {}) if isinstance(llm_patch.get("validation"), Mapping) else {},
            }
            session["preview_state"] = copy.deepcopy(dict(preview_from_llm))
            if payload_from_llm is not None:
                session["webui_payload"] = copy.deepcopy(dict(payload_from_llm))
            _merge_session_from_preview(session, preview_from_llm)
        else:
            patch["diff"] = {"llm_fallback": llm_patch}
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
    if patch.get("operation") == "llm_webui_transform" and isinstance(session.get("preview_state"), Mapping):
        preview = copy.deepcopy(dict(session["preview_state"]))
    else:
        preview = _preview_state(session=session)
    if patch.get("operation") == "llm_webui_transform" and isinstance(session.get("webui_payload"), Mapping):
        _write_webui_payload(str(session.get("artifact_root") or ""), session["webui_payload"])
    else:
        _write_webui(str(session.get("artifact_root") or ""), preview)
    session["preview_state"] = preview
    workbench = _ensure_workbench(ws, session=session, preview_state=preview)
    binding = workbench.get("binding") if isinstance(workbench.get("binding"), Mapping) else binding
    topic = _builder_topic_ref(ws, session=session, binding=binding, _meta=_meta)
    session["thread_id"] = str(topic.get("thread_id") or "").strip() or None
    session["topic_id"] = str(topic.get("topic_id") or "").strip() or None
    session["topic_ref"] = {k: v for k, v in topic.items() if k != "stored"}
    _save_session(ws, session)
    prompt_selection = _publish_prompt_project_selection(
        ws,
        session=session,
        reason="builder_project_updated",
    )
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
        "prompt_selection": prompt_selection,
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
    preview = (session or {}).get("preview_state") if isinstance(session, dict) else None
    workbench = _ensure_workbench(ws, session=session, preview_state=preview)
    binding = workbench.get("binding") if isinstance(workbench.get("binding"), Mapping) else {}
    topic = _builder_topic_ref(ws, session=session, binding=binding, _meta=_meta)
    return {
        "ok": bool(session),
        "session": session,
        "developer_evidence": _developer_evidence(
            webspace_id=ws,
            session=session,
            preview_state=preview if isinstance(preview, Mapping) else None,
            workbench=workbench,
            topic_ref=topic,
            _meta=_meta,
        ),
        "workbench": workbench,
        "dialog": _dialog_state(ws, topic_ref=topic),
    }


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
    binding = workbench.get("binding") if isinstance(workbench.get("binding"), Mapping) else {}
    topic = _builder_topic_ref(ws, session=session, binding=binding, _meta=_meta)
    return {
        "ok": True,
        "session_id": session.get("id"),
        "preview_state": preview,
        "developer_evidence": _developer_evidence(
            webspace_id=ws,
            session=session,
            preview_state=preview,
            workbench=workbench,
            topic_ref=topic,
            _meta=_meta,
        ),
        "workbench": workbench,
        "dialog": _dialog_state(ws, topic_ref=topic),
    }


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
