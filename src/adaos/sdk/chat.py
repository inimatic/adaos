from __future__ import annotations

from typing import Any, Mapping

from adaos.sdk import conversation
from adaos.services import conversation_response
from adaos.services import conversation_interactions, conversation_store
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


def request(
    interaction: Mapping[str, Any] | str,
    *,
    conversation_id: str,
    owner: str,
    webspace_id: str | None = None,
    channel_id: str = "general",
    route_id: str = "dialog",
    thread_id: str | None = None,
    input_spec: Mapping[str, Any] | None = None,
    actions: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
    required_capabilities: tuple[str, ...] | list[str] = (),
    optional_capabilities: tuple[str, ...] | list[str] = (),
    fallbacks: tuple[str, ...] | list[str] = ("numbered_text", "plain_text", "unsupported"),
    task_ref: Mapping[str, Any] | None = None,
    workflow_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    expires_at: str | None = None,
    capability_profile: Mapping[str, Any] | None = None,
    deep_link_base: str | None = None,
    actor_id: str | None = None,
    actor_label: str | None = None,
    request_id: str | None = None,
    turn_trace_id: str | None = None,
    meta: Mapping[str, Any] | None = None,
    bus: Any | None = None,
) -> dict[str, Any]:
    """Persist a semantic Interaction and materialize a negotiated presentation.

    This call never holds a process-local waiter. The returned handle can be
    resumed later through :func:`respond`, including after process restart.
    """

    specification = dict(interaction) if isinstance(interaction, Mapping) else {}
    prompt = str(specification.get("prompt") or interaction or "").strip()
    created = conversation_interactions.create_interaction(
        interaction_id=str(specification.get("interaction_id") or "").strip() or None,
        conversation_id=conversation_id,
        thread_id=thread_id,
        owner=owner,
        prompt=prompt,
        input_spec=specification.get("input_spec") if isinstance(specification.get("input_spec"), Mapping) else input_spec,
        actions=specification.get("actions") if isinstance(specification.get("actions"), list) else actions,
        required_capabilities=specification.get("required_capabilities") or required_capabilities,
        optional_capabilities=specification.get("optional_capabilities") or optional_capabilities,
        fallbacks=specification.get("fallbacks") or fallbacks,
        task_ref=specification.get("task_ref") if isinstance(specification.get("task_ref"), Mapping) else task_ref,
        workflow_ref=specification.get("workflow_ref") if isinstance(specification.get("workflow_ref"), Mapping) else workflow_ref,
        reply_route_ref=specification.get("reply_route_ref") if isinstance(specification.get("reply_route_ref"), Mapping) else reply_route_ref,
        expires_at=str(specification.get("expires_at") or expires_at or "").strip() or None,
        metadata={**dict(specification.get("metadata") or {}), **dict(meta or {})},
    )
    transport = str(dict(meta or {}).get("io_type") or ("telegram" if route_id == "telegram" else "web")).strip()
    profile = dict(capability_profile) if isinstance(capability_profile, Mapping) else conversation_interactions.standard_capability_profile(
        transport,
        client=str(dict(meta or {}).get("client") or transport),
        surface=channel_id,
    )
    presentation = conversation_interactions.negotiate_presentation(
        created,
        profile,
        deep_link_base=deep_link_base,
    )
    latest = conversation_store.get_interaction(created["interaction_id"]) or created
    response = {
        "conversation_id": conversation_id,
        "request_id": request_id,
        "content": [{"type": "text", "text": presentation["prompt"]}],
        "render_targets": ("text_tail",),
        "actions": presentation["actions"],
        "interaction": {
            "interaction_id": created["interaction_id"],
            "generation": created["generation"],
            "presentation_id": presentation["presentation_id"],
            "mode": presentation["mode"],
            "supported": presentation["supported"],
        },
        "meta": {
            **dict(meta or {}),
            "dialog_act": "request",
            "expects_reply": True,
            "interaction_id": created["interaction_id"],
            "interaction_generation": created["generation"],
            "interaction_presentation_id": presentation["presentation_id"],
        },
    }
    materialization = send(
        response,
        conversation_id=conversation_id,
        webspace_id=webspace_id,
        channel_id=channel_id,
        owner=owner,
        route_id=route_id,
        actor_id=actor_id,
        actor_label=actor_label,
        request_id=request_id,
        turn_trace_id=turn_trace_id,
        thread_id=thread_id,
        meta=meta,
        bus=bus,
    )
    return {
        "ok": True,
        "handle": conversation_interactions.interaction_handle(latest).to_dict(),
        "interaction": latest,
        "presentation": presentation,
        "materialization": materialization,
    }


def respond(
    interaction_id: str,
    *,
    actor_id: str,
    expected_generation: int,
    idempotency_key: str,
    values: Mapping[str, Any] | None = None,
    text: str | None = None,
    action_token: str | None = None,
    intent_proposal: Mapping[str, Any] | None = None,
    supersedes_response_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return conversation_interactions.submit_response(
        interaction_id,
        actor_id=actor_id,
        expected_generation=expected_generation,
        idempotency_key=idempotency_key,
        values=values,
        original_text=text,
        action_token=action_token,
        intent_proposal=intent_proposal,
        supersedes_response_id=supersedes_response_id,
        metadata=metadata,
    )


def accept(
    interaction_id: str,
    response_id: str,
    *,
    expected_generation: int,
) -> dict[str, Any]:
    return conversation_interactions.accept_response(
        interaction_id,
        response_id,
        expected_generation=expected_generation,
    )


def pending(
    conversation_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return conversation_store.list_interactions(
        conversation_id=conversation_id,
        statuses=["created", "projected", "awaiting_input", "partially_answered", "validation_failed"],
        limit=limit,
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
