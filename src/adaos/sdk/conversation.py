from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping

from adaos.services import conversation_context, conversation_store
from adaos.services.yjs.webspace import default_webspace_id


def current(*, webspace_id: str | None = None, channel_id: str = "general") -> dict[str, Any] | None:
    """Return the persisted dialog channel pointer for the current node."""
    ws = str(webspace_id or default_webspace_id()).strip() or default_webspace_id()
    return conversation_store.get_dialog_channel(ws, str(channel_id or "general").strip() or "general")


def open(
    *,
    conversation_id: str,
    owner: str,
    webspace_id: str | None = None,
    channel_id: str | None = None,
    title: str | None = None,
    active_agent_id: str | None = None,
    policy: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a node-local conversation and optional channel pointer."""
    ws = str(webspace_id or default_webspace_id()).strip() or default_webspace_id()
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id=ws,
        owner=owner,
        title=title,
        active_agent_id=active_agent_id,
        policy=policy,
        meta=meta,
    )
    if channel_id:
        conversation_store.upsert_dialog_channel(
            webspace_id=ws,
            channel_id=channel_id,
            label=title or channel_id,
            owner=owner,
            conversation_id=conversation_id,
            active_agent_id=active_agent_id,
            policy=policy,
            meta=meta,
        )
    return {
        "conversation_id": conversation_id,
        "webspace_id": ws,
        "owner": owner,
        "channel_id": channel_id,
        "active_agent_id": active_agent_id,
    }


def append(
    *,
    conversation_id: str,
    text: str,
    role: str,
    thread_id: str | None = None,
    webspace_id: str | None = None,
    channel_id: str = "general",
    owner: str = "core",
    actor_id: str | None = None,
    actor_label: str | None = None,
    payload: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    retention_class: str = "normal",
    retention_until: float | None = None,
    redaction_state: str = "active",
    redacted_at: float | None = None,
    redaction_reason: str | None = None,
) -> dict[str, Any] | None:
    ws = str(webspace_id or default_webspace_id()).strip() or default_webspace_id()
    return conversation_store.append_message(
        conversation_id=conversation_id,
        thread_id=thread_id,
        webspace_id=ws,
        channel_id=channel_id,
        owner=owner,
        role=role,
        text=text,
        actor_id=actor_id,
        actor_label=actor_label,
        payload=payload,
        meta=meta,
        retention_class=retention_class,
        retention_until=retention_until,
        redaction_state=redaction_state,
        redacted_at=redacted_at,
        redaction_reason=redaction_reason,
    )


def get(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    before_cursor: Any = None,
    limit: int = 50,
) -> dict[str, Any]:
    return conversation_store.list_projection(
        conversation_id,
        thread_id=thread_id,
        before_cursor=before_cursor,
        limit=limit,
        max_items=max(limit, 200),
    )


def export(
    conversation_id: str,
    *,
    include_redacted: bool = False,
    include_memory: bool = True,
    include_traces: bool = True,
    limit: int = 5000,
) -> dict[str, Any]:
    """Return a privacy/audit aware conversation export bundle."""
    return conversation_store.export_conversation(
        conversation_id,
        include_redacted=include_redacted,
        include_memory=include_memory,
        include_traces=include_traces,
        limit=limit,
    )


def redact(
    conversation_id: str,
    *,
    reason: str = "user_request",
    include_memory: bool = True,
    include_traces: bool = True,
) -> dict[str, Any]:
    """Soft-redact a conversation bundle and record a durable audit event."""
    return conversation_store.redact_conversation(
        conversation_id,
        reason=reason,
        hard_delete=False,
        include_memory=include_memory,
        include_traces=include_traces,
    )


def delete(
    conversation_id: str,
    *,
    reason: str = "user_request",
    include_memory: bool = True,
    include_traces: bool = True,
) -> dict[str, Any]:
    """Hard-delete a conversation bundle and record a durable audit event."""
    return conversation_store.redact_conversation(
        conversation_id,
        reason=reason,
        hard_delete=True,
        include_memory=include_memory,
        include_traces=include_traces,
    )


def start_thread(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    title: str | None = None,
    created_by: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    return conversation_store.start_thread(
        conversation_id=conversation_id,
        thread_id=thread_id,
        title=title,
        created_by=created_by,
        meta=meta,
    )


def context(
    conversation_id: str,
    *,
    requester_owner: str,
    channel_id: str | None = None,
    thread_id: str | None = None,
    topic_ref: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    memory_owner: str | None = None,
    include_global_user: bool = True,
    allow_cross_owner_memory: bool = False,
    budgets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a budgeted conversation context packet for LLM/runtime calls."""
    return conversation_context.build_context_packet(
        conversation_id=conversation_id,
        requester_owner=requester_owner,
        channel_id=channel_id,
        thread_id=thread_id,
        topic_ref=topic_ref,
        agent_id=agent_id,
        memory_owner=memory_owner,
        include_global_user=include_global_user,
        allow_cross_owner_memory=allow_cross_owner_memory,
        budgets=budgets,
    )


def ensure_builder_topic(
    *,
    webspace_id: str | None = None,
    active_draft_id: str | None = None,
    scenario_id: str | None = None,
    dev_webspace_id: str | None = None,
    project_id: str | None = None,
    title: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensure the canonical Builder conversation and one project topic."""

    from adaos.services.conversation_links import ensure_builder_topic as _ensure

    return dict(
        _ensure(
            webspace_id,
            active_draft_id=active_draft_id,
            scenario_id=scenario_id,
            dev_webspace_id=dev_webspace_id,
            project_id=project_id,
            title=title,
            meta=meta,
        )
        or {}
    )


def upsert_development_change(
    *,
    change_id: str,
    conversation_id: str,
    thread_id: str | None = None,
    topic_id: str | None = None,
    status: str = "accepted",
    source_message_ids: Sequence[str] | None = None,
    source_refs: Mapping[str, Any] | None = None,
    artifact_refs: Sequence[Mapping[str, Any]] | None = None,
    revision_refs: Sequence[Mapping[str, Any] | str] | None = None,
    commit_refs: Sequence[Mapping[str, Any] | str] | None = None,
    result_message_id: str | None = None,
    request_id: str | None = None,
    model: str | None = None,
    summary: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Create or update the durable join for one Builder artifact change."""

    return conversation_store.upsert_development_change(
        change_id=change_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        topic_id=topic_id,
        status=status,
        source_message_ids=source_message_ids,
        source_refs=source_refs,
        artifact_refs=artifact_refs,
        revision_refs=revision_refs,
        commit_refs=commit_refs,
        result_message_id=result_message_id,
        request_id=request_id,
        model=model,
        summary=summary,
        meta=meta,
    )


def get_development_change(change_id: str) -> dict[str, Any] | None:
    """Return one Builder Change aggregate by stable change id."""

    return conversation_store.get_development_change(change_id)


def list_development_changes(
    *,
    conversation_id: str | None = None,
    topic_id: str | None = None,
    artifact_kind: str | None = None,
    artifact_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List bounded Builder Change aggregates."""

    return conversation_store.list_development_changes(
        conversation_id=conversation_id,
        topic_id=topic_id,
        artifact_kind=artifact_kind,
        artifact_id=artifact_id,
        limit=limit,
    )


def upsert_development_run(
    *,
    run_id: str,
    change_id: str,
    conversation_id: str,
    activity: str,
    executor: str,
    status: str = "queued",
    thread_id: str | None = None,
    topic_id: str | None = None,
    context_packet_digest: str | None = None,
    environment_ref: str | None = None,
    input_refs: Sequence[str] | None = None,
    output_refs: Sequence[str] | None = None,
    evidence_refs: Sequence[str] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Create or advance one Builder Run linked to a canonical Change."""

    return conversation_store.upsert_development_run(
        run_id=run_id,
        change_id=change_id,
        conversation_id=conversation_id,
        activity=activity,
        executor=executor,
        status=status,
        thread_id=thread_id,
        topic_id=topic_id,
        context_packet_digest=context_packet_digest,
        environment_ref=environment_ref,
        input_refs=input_refs,
        output_refs=output_refs,
        evidence_refs=evidence_refs,
        started_at=started_at,
        completed_at=completed_at,
        error=error,
    )


def get_development_run(run_id: str) -> dict[str, Any] | None:
    """Return one strict `adaos.builder.run.v1` projection."""

    return conversation_store.get_development_run(run_id)


def list_development_runs(
    *,
    change_id: str | None = None,
    conversation_id: str | None = None,
    topic_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List bounded Builder Runs without synthesizing extra Changes."""

    return conversation_store.list_development_runs(
        change_id=change_id,
        conversation_id=conversation_id,
        topic_id=topic_id,
        limit=limit,
    )


def classify_action_risk(action: Mapping[str, Any] | str | None) -> dict[str, Any]:
    """Classify one proposed action through the conversation safety policy."""

    from adaos.services.conversation_safety import classify_action_risk as _classify

    return dict(_classify(action) or {})


__all__ = [
    "append",
    "classify_action_risk",
    "context",
    "current",
    "delete",
    "ensure_builder_topic",
    "export",
    "get",
    "get_development_change",
    "get_development_run",
    "list_development_changes",
    "list_development_runs",
    "open",
    "redact",
    "start_thread",
    "upsert_development_change",
    "upsert_development_run",
]
