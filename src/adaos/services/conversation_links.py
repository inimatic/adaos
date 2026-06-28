from __future__ import annotations

import re
from typing import Any, Mapping

from adaos.services import conversation_context, conversation_store


BUILDER_CHANNEL_ID = "builder"
BUILDER_OWNER = "skill:builder_skill"
BUILDER_SKILL = "builder_skill"
TEACHER_CHANNEL_ID = "teacher"
TEACHER_OWNER = "core:nlu_teacher"


def _clean(value: Any, default: str = "") -> str:
    token = str(value or "").strip()
    return token or default


def _safe_id(value: Any, default: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.:-]+", ".", str(value or "").strip()).strip(".")
    return token or default


def builder_conversation_id(webspace_id: str | None = None) -> str:
    ws = _safe_id(webspace_id, "default")
    return f"conv.skill.{BUILDER_SKILL}.default.{ws}"


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
    cid = builder_conversation_id(ws)
    stored = False
    try:
        if conversation_store.ensure_schema():
            conversation_store.upsert_conversation(
                conversation_id=cid,
                webspace_id=ws,
                owner=BUILDER_OWNER,
                kind="builder",
                title="Builder",
                meta={"channel_id": BUILDER_CHANNEL_ID, "surface": "builder"},
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
    budgets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ref = ensure_builder_conversation(webspace_id)
    try:
        packet = conversation_context.build_context_packet(
            conversation_id=ref["conversation_id"],
            requester_owner=BUILDER_OWNER,
            channel_id=BUILDER_CHANNEL_ID,
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
