from __future__ import annotations

from typing import Any, Mapping

from adaos.sdk import conversation
from adaos.services import conversation_response
from adaos.services.agent_context import get_ctx
from adaos.services.yjs.webspace import default_webspace_id


def send(
    content: Any,
    *,
    conversation_id: str,
    webspace_id: str | None = None,
    channel_id: str = "general",
    owner: str = "core",
    route_id: str = "dialog",
    actor_id: str | None = None,
    actor_label: str | None = None,
    actor_icon: str | None = None,
    request_id: str | None = None,
    turn_trace_id: str | None = None,
    thread_id: str | None = None,
    render_targets: tuple[str, ...] | list[str] = ("text_tail",),
    speech_text: str | None = None,
    meta: Mapping[str, Any] | None = None,
    bus: Any | None = None,
) -> dict[str, Any]:
    ws = str(webspace_id or default_webspace_id()).strip() or default_webspace_id()
    response = _response_from_content(
        content,
        conversation_id=conversation_id,
        request_id=request_id,
        render_targets=render_targets,
        speech_text=speech_text,
        meta=meta,
    )
    return conversation_response.materialize_response(
        response,
        webspace_id=ws,
        conversation_id=conversation_id,
        channel_id=channel_id,
        owner=owner,
        bus=bus if bus is not None else _ctx_bus(),
        route_id=route_id,
        actor_id=actor_id,
        actor_label=actor_label,
        actor_icon=actor_icon,
        request_id=request_id,
        turn_trace_id=turn_trace_id,
        thread_id=thread_id,
        meta=meta,
        source="adaos.sdk.chat",
    )


def ask(
    prompt: Any,
    *,
    conversation_id: str,
    webspace_id: str | None = None,
    channel_id: str = "general",
    owner: str = "core",
    route_id: str = "dialog",
    actor_id: str | None = None,
    actor_label: str | None = None,
    actor_icon: str | None = None,
    request_id: str | None = None,
    turn_trace_id: str | None = None,
    thread_id: str | None = None,
    meta: Mapping[str, Any] | None = None,
    bus: Any | None = None,
) -> dict[str, Any]:
    question_meta = {**dict(meta or {}), "dialog_act": "ask", "expects_reply": True}
    return send(
        prompt,
        conversation_id=conversation_id,
        webspace_id=webspace_id,
        channel_id=channel_id,
        owner=owner,
        route_id=route_id,
        actor_id=actor_id,
        actor_label=actor_label,
        actor_icon=actor_icon,
        request_id=request_id,
        turn_trace_id=turn_trace_id,
        thread_id=thread_id,
        render_targets=("text_tail", "speech_text"),
        meta=question_meta,
        bus=bus,
    )


def history(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    before_cursor: Any = None,
    limit: int = 50,
) -> dict[str, Any]:
    return conversation.get(
        conversation_id,
        thread_id=thread_id,
        before_cursor=before_cursor,
        limit=limit,
    )


def context(
    conversation_id: str,
    *,
    requester_owner: str,
    channel_id: str | None = None,
    agent_id: str | None = None,
    memory_owner: str | None = None,
    include_global_user: bool = True,
    allow_cross_owner_memory: bool = False,
    budgets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return conversation.context(
        conversation_id,
        requester_owner=requester_owner,
        channel_id=channel_id,
        agent_id=agent_id,
        memory_owner=memory_owner,
        include_global_user=include_global_user,
        allow_cross_owner_memory=allow_cross_owner_memory,
        budgets=budgets,
    )


def start_thread(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    title: str | None = None,
    created_by: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    return conversation.start_thread(
        conversation_id,
        thread_id=thread_id,
        title=title,
        created_by=created_by,
        meta=meta,
    )


def _response_from_content(
    content: Any,
    *,
    conversation_id: str,
    request_id: str | None,
    render_targets: tuple[str, ...] | list[str],
    speech_text: str | None,
    meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(content, Mapping):
        response = dict(content)
        response.setdefault("conversation_id", conversation_id)
        if request_id:
            response.setdefault("request_id", request_id)
        response.setdefault("render_targets", tuple(render_targets))
        if speech_text:
            response.setdefault("speech_text", speech_text)
        response.setdefault("meta", dict(meta or {}))
        return response
    return {
        "conversation_id": conversation_id,
        "request_id": request_id,
        "content": [{"type": "text", "text": str(content or "")}],
        "render_targets": tuple(render_targets),
        "speech_text": speech_text,
        "meta": dict(meta or {}),
    }


def _ctx_bus() -> Any | None:
    try:
        return getattr(get_ctx(), "bus", None)
    except Exception:
        return None
