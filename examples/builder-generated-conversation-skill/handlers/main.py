from __future__ import annotations

from typing import Any, Mapping

from adaos.sdk import chat as sdk_chat
from adaos.sdk import conversation as sdk_conversation
from adaos.sdk import memory as sdk_memory
from adaos.sdk.core.decorators import tool


SKILL_ID = "builder_generated_preferences_skill"
OWNER = f"skill:{SKILL_ID}"
CHANNEL = "builder_generated_preferences"
AGENT = f"agent:{SKILL_ID}:assistant"


def _dialog(payload: Mapping[str, Any]) -> dict[str, str | None]:
    runtime = payload.get("conversation_context") if isinstance(payload.get("conversation_context"), Mapping) else {}
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    webspace_id = str(payload.get("webspace_id") or meta.get("webspace_id") or runtime.get("webspace_id") or "default")
    conversation_id = str(runtime.get("conversation_id") or payload.get("conversation_id") or f"conv.skill.{SKILL_ID}.{webspace_id}")
    thread_id = str(runtime.get("thread_id") or payload.get("thread_id") or "").strip() or None
    sdk_conversation.open(
        conversation_id=conversation_id,
        owner=OWNER,
        webspace_id=webspace_id,
        channel_id=CHANNEL,
        title="Preferences",
        active_agent_id=AGENT,
        policy={"history": "node_ledger", "retrieval": "budgeted_context_packet"},
    )
    return {"webspace_id": webspace_id, "conversation_id": conversation_id, "thread_id": thread_id}


@tool(summary="Handle one bounded preference dialog turn.", side_effects="local_write")
def chat(text: str = "", **payload: Any) -> dict[str, Any]:
    dialog = _dialog(payload)
    request = str(text or payload.get("message") or "").strip()
    packet = sdk_chat.context(
        str(dialog["conversation_id"]),
        requester_owner=OWNER,
        channel_id=CHANNEL,
        agent_id=AGENT,
        budgets={"max_messages": 12, "max_segments": 2, "max_memory_items": 4, "max_tokens": 1200},
    )
    message = f"I can help review this preference: {request}" if request else "Which preference should we review?"
    response = sdk_chat.send(
        message,
        conversation_id=str(dialog["conversation_id"]),
        webspace_id=str(dialog["webspace_id"]),
        channel_id=CHANNEL,
        owner=OWNER,
        route_id="voice_chat",
        actor_id=AGENT,
        actor_label="Preference assistant",
        thread_id=dialog["thread_id"],
        meta={"context_packet_schema": packet.get("schema"), "response_policy": "text_tail_first"},
    )
    return {"ok": True, "message": message, "conversation_id": dialog["conversation_id"], "response": response}


@tool(summary="Propose a consent-gated preference memory write.", side_effects="local_write")
def remember_preference(key: str, text: str, confidence: float = 0.7, **payload: Any) -> dict[str, Any]:
    dialog = _dialog(payload)
    pending_action = sdk_memory.propose_write(
        "skill_preference",
        owner=OWNER,
        key=str(key).strip(),
        text=str(text).strip(),
        confidence=max(0.0, min(float(confidence), 1.0)),
        conversation_id=str(dialog["conversation_id"]),
        agent_id=AGENT,
        webspace_id=str(dialog["webspace_id"]),
        source_ref={"type": "conversation", "conversation_id": dialog["conversation_id"], "thread_id": dialog["thread_id"]},
        reason="builder_generated_preference",
    )
    return {"ok": True, "message": "Preference prepared for review.", "pending_action": pending_action}
