from __future__ import annotations

import re
from typing import Any, Mapping

from adaos.services import conversation_context, conversation_store


BUILDER_CHANNEL_ID = "builder"
BUILDER_OWNER = "skill:builder_skill"
BUILDER_SKILL = "builder_skill"
BUILDER_CONVERSATION_ID = f"conv.skill.{BUILDER_SKILL}.default"
TEACHER_CHANNEL_ID = "teacher"
TEACHER_OWNER = "core:nlu_teacher"


def _clean(value: Any, default: str = "") -> str:
    token = str(value or "").strip()
    return token or default


def _safe_id(value: Any, default: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.:-]+", ".", str(value or "").strip()).strip(".")
    return token or default


def builder_conversation_id(webspace_id: str | None = None) -> str:
    del webspace_id
    return BUILDER_CONVERSATION_ID


def builder_topic_ref(
    webspace_id: str | None = None,
    *,
    active_draft_id: str | None = None,
    scenario_id: str | None = None,
    dev_webspace_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    ws = _safe_id(webspace_id, "default")
    draft = _clean(active_draft_id)
    scenario = _clean(scenario_id)
    dev_ws = _clean(dev_webspace_id)
    project = _clean(project_id) or draft or scenario or dev_ws or "default"
    token = _safe_id(project, "default")
    if draft:
        kind = "builder_draft"
        topic_id = f"builder:{ws}:{token}"
        thread_id = f"thread.builder.{ws}.{token}"
    elif scenario:
        kind = "builder_scenario"
        scenario_token = _safe_id(scenario, token)
        topic_id = f"prompt-project:scenario:{scenario_token}"
        thread_id = topic_id
    elif dev_ws:
        kind = "builder_workspace"
        topic_id = f"builder:{ws}:{token}"
        thread_id = f"thread.builder.{ws}.{token}"
    else:
        kind = "builder_default"
        topic_id = f"builder:{ws}:{token}"
        thread_id = f"thread.builder.{ws}.{token}"
    return {
        "schema": "adaos.conversation.topic_ref.v1",
        "topic_id": topic_id,
        "thread_id": thread_id,
        "topic_kind": kind,
        "webspace_id": ws,
        "source_webspace_id": ws,
        "active_draft_id": draft or None,
        "scenario_id": scenario or None,
        "dev_webspace_id": dev_ws or None,
        "project_id": project,
    }


def ensure_builder_topic(
    webspace_id: str | None = None,
    *,
    active_draft_id: str | None = None,
    scenario_id: str | None = None,
    dev_webspace_id: str | None = None,
    project_id: str | None = None,
    title: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    conversation_ref = ensure_builder_conversation(webspace_id)
    topic = builder_topic_ref(
        conversation_ref.get("webspace_id") or webspace_id,
        active_draft_id=active_draft_id,
        scenario_id=scenario_id,
        dev_webspace_id=dev_webspace_id,
        project_id=project_id,
    )
    stored = bool(conversation_ref.get("stored"))
    try:
        if conversation_store.ensure_schema():
            conversation_store.start_thread(
                conversation_id=conversation_ref["conversation_id"],
                thread_id=topic["thread_id"],
                title=_clean(title, f"Builder: {topic['project_id']}"),
                created_by={"type": "skill", "id": BUILDER_SKILL},
                meta={**topic, **dict(meta or {})},
            )
            stored = True
    except Exception:
        stored = False
    return {
        **topic,
        "conversation_id": conversation_ref["conversation_id"],
        "channel_id": BUILDER_CHANNEL_ID,
        "owner": BUILDER_OWNER,
        "stored": stored,
    }


def teacher_conversation_id(webspace_id: str | None = None) -> str:
    ws = _safe_id(webspace_id, "default")
    return f"conv.teacher.default.{ws}"


def teacher_thread_id(
    *,
    webspace_id: str | None = None,
    request_id: str | None = None,
    candidate_id: str | None = None,
) -> str:
    ws = _safe_id(webspace_id, "default")
    token = _safe_id(request_id or candidate_id, "default")
    return f"thread.teacher.{ws}.{token}"


def ensure_builder_conversation(webspace_id: str | None = None) -> dict[str, Any]:
    ws = _safe_id(webspace_id, "default")
    cid = builder_conversation_id()
    stored = False
    try:
        if conversation_store.ensure_schema():
            conversation_store.upsert_conversation(
                conversation_id=cid,
                webspace_id="global",
                owner=BUILDER_OWNER,
                kind="builder",
                title="Builder",
                meta={
                    "channel_id": BUILDER_CHANNEL_ID,
                    "surface": "builder",
                    "scope": "global",
                    "topic_contract": "project",
                },
            )
            conversation_store.merge_conversations_by_prefix(
                prefix=f"{BUILDER_CONVERSATION_ID}.",
                target_conversation_id=cid,
            )
            conversation_store.upsert_dialog_channel(
                webspace_id=ws,
                channel_id=BUILDER_CHANNEL_ID,
                label="Builder",
                owner=BUILDER_OWNER,
                conversation_id=cid,
                default_skill=BUILDER_SKILL,
                default_tool="chat",
                route_id="voice_chat",
                policy={
                    "entry_intents": ["builder.start", "builder.agent_addressed"],
                    "fallback": "owner_default_tool",
                    "switch_intents": ["general.agent_addressed", "conversation.agent_addressed"],
                },
                meta={"surface": "builder"},
            )
            stored = True
    except Exception:
        stored = False
    return {
        "conversation_id": cid,
        "webspace_id": ws,
        "channel_id": BUILDER_CHANNEL_ID,
        "owner": BUILDER_OWNER,
        "kind": "builder",
        "stored": stored,
    }


def ensure_teacher_conversation(
    webspace_id: str | None = None,
    *,
    request_id: str | None = None,
    candidate_id: str | None = None,
    title: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ws = _safe_id(webspace_id, "default")
    cid = teacher_conversation_id(ws)
    tid = teacher_thread_id(webspace_id=ws, request_id=request_id, candidate_id=candidate_id)
    stored = False
    try:
        if conversation_store.ensure_schema():
            conversation_store.upsert_conversation(
                conversation_id=cid,
                webspace_id=ws,
                owner=TEACHER_OWNER,
                kind="teacher",
                title="NLU Teacher",
                meta={"channel_id": TEACHER_CHANNEL_ID, "surface": "teacher"},
            )
            conversation_store.upsert_dialog_channel(
                webspace_id=ws,
                channel_id=TEACHER_CHANNEL_ID,
                label="Teacher",
                owner=TEACHER_OWNER,
                conversation_id=cid,
                default_skill="nlu_teacher",
                default_tool="handle_teacher_turn",
                route_id="voice_chat",
                policy={
                    "entry_intents": ["teacher.start", "teacher.clarify", "teacher.confirm"],
                    "fallback": "teacher_queue",
                    "switch_intents": ["general.agent_addressed", "builder.start"],
                },
                meta={"surface": "teacher"},
            )
            conversation_store.start_thread(
                conversation_id=cid,
                thread_id=tid,
                title=_clean(title, request_id or candidate_id or "NLU Teacher request"),
                created_by={"type": "core", "id": "nlu_teacher"},
                meta={
                    "webspace_id": ws,
                    "request_id": _clean(request_id),
                    "candidate_id": _clean(candidate_id),
                    **dict(meta or {}),
                },
            )
            stored = True
    except Exception:
        stored = False
    return {
        "conversation_id": cid,
        "thread_id": tid,
        "webspace_id": ws,
        "channel_id": TEACHER_CHANNEL_ID,
        "owner": TEACHER_OWNER,
        "kind": "teacher",
        "request_id": _clean(request_id) or None,
        "candidate_id": _clean(candidate_id) or None,
        "stored": stored,
    }


def builder_context_packet(
    webspace_id: str | None = None,
    *,
    thread_id: str | None = None,
    topic_ref: Mapping[str, Any] | None = None,
    active_draft_id: str | None = None,
    scenario_id: str | None = None,
    dev_webspace_id: str | None = None,
    project_id: str | None = None,
    budgets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    clean_topic_ref = dict(topic_ref or {}) if isinstance(topic_ref, Mapping) else {}
    if any(
        str(value or "").strip()
        for value in (thread_id, active_draft_id, scenario_id, dev_webspace_id, project_id)
    ) or clean_topic_ref:
        ref = ensure_builder_topic(
            webspace_id,
            active_draft_id=active_draft_id or str(clean_topic_ref.get("active_draft_id") or "").strip() or None,
            scenario_id=scenario_id or str(clean_topic_ref.get("scenario_id") or "").strip() or None,
            dev_webspace_id=dev_webspace_id or str(clean_topic_ref.get("dev_webspace_id") or "").strip() or None,
            project_id=project_id or str(clean_topic_ref.get("project_id") or "").strip() or None,
        )
        if thread_id:
            ref["thread_id"] = str(thread_id).strip()
        clean_topic_ref = {k: v for k, v in ref.items() if k not in {"stored"}}
    else:
        ref = ensure_builder_conversation(webspace_id)
    try:
        packet = conversation_context.build_context_packet(
            conversation_id=ref["conversation_id"],
            requester_owner=BUILDER_OWNER,
            channel_id=BUILDER_CHANNEL_ID,
            thread_id=str(ref.get("thread_id") or thread_id or "").strip() or None,
            topic_ref=clean_topic_ref or None,
            memory_owner=BUILDER_OWNER,
            include_global_user=True,
            allow_cross_owner_memory=False,
            budgets=budgets or {"max_tokens": 2_000, "max_messages": 12, "max_memory_items": 8},
        )
    except Exception as exc:
        packet = {
            "schema": "adaos.context_packet.v1",
            "conversation_id": ref["conversation_id"],
            "requester_owner": BUILDER_OWNER,
            "channel_id": BUILDER_CHANNEL_ID,
            "thread_id": str(ref.get("thread_id") or thread_id or "").strip() or None,
            "topic": clean_topic_ref or None,
            "messages": [],
            "memory": [],
            "token_estimate": 0,
            "diagnostics": {
                "available": False,
                "reason": type(exc).__name__,
                "fallbacks": ["conversation_context_unavailable"],
            },
        }
    packet.setdefault("conversation_ref", {k: v for k, v in ref.items() if k != "stored"})
    if clean_topic_ref:
        packet.setdefault("topic", clean_topic_ref)
        packet.setdefault("topic_id", str(clean_topic_ref.get("topic_id") or "").strip() or None)
    return packet


def append_teacher_event_message(
    *,
    webspace_id: str | None = None,
    text: str,
    request_id: str | None = None,
    candidate_id: str | None = None,
    kind: str = "teacher_event",
    payload: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    ref = ensure_teacher_conversation(
        webspace_id,
        request_id=request_id,
        candidate_id=candidate_id,
        title=text[:120],
        meta=meta,
    )
    if not ref.get("stored"):
        return None
    return conversation_store.append_message(
        conversation_id=ref["conversation_id"],
        thread_id=ref["thread_id"],
        webspace_id=ref["webspace_id"],
        channel_id=TEACHER_CHANNEL_ID,
        owner=TEACHER_OWNER,
        role="system",
        text=text,
        actor_id="core:nlu_teacher",
        actor_label="NLU Teacher",
        payload={
            "kind": kind,
            "text": text,
            "conversation_id": ref["conversation_id"],
            "thread_id": ref["thread_id"],
            "request_id": request_id,
            "candidate_id": candidate_id,
            **dict(payload or {}),
        },
        meta={"conversation_ref": ref, **dict(meta or {})},
        route_id="nlu_teacher",
    )
