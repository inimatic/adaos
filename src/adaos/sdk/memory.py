from __future__ import annotations

from typing import Any, Mapping

from adaos.services import conversation_context, conversation_store


def remember(
    *,
    scope: str,
    owner: str,
    subject_id: str | None = None,
    key: str | None = None,
    text: str | None = None,
    value: Mapping[str, Any] | None = None,
    confidence: float | None = None,
    consent_state: str = "unknown",
    visibility: str | None = None,
    policy: Mapping[str, Any] | None = None,
    source_ref: Mapping[str, Any] | None = None,
    retention_class: str = "normal",
    retention_until: float | None = None,
    redaction_state: str = "active",
    redacted_at: float | None = None,
    redaction_reason: str | None = None,
    memory_id: str | None = None,
) -> str | None:
    return conversation_store.remember(
        scope=scope,
        owner=owner,
        subject_id=subject_id,
        key=key,
        text=text,
        value=value,
        confidence=confidence,
        consent_state=consent_state,
        visibility=visibility,
        policy=policy,
        source_ref=source_ref,
        retention_class=retention_class,
        retention_until=retention_until,
        redaction_state=redaction_state,
        redacted_at=redacted_at,
        redaction_reason=redaction_reason,
        memory_id=memory_id,
    )


def list(
    *,
    scope: str | None = None,
    owner: str | None = None,
    subject_id: str | None = None,
    limit: int = 50,
    include_redacted: bool = False,
) -> list[dict[str, Any]]:
    return conversation_store.list_memory(
        scope=scope,
        owner=owner,
        subject_id=subject_id,
        limit=limit,
        include_redacted=include_redacted,
    )


def search(
    query: str,
    *,
    scope: str | None = None,
    owner: str | None = None,
    subject_id: str | None = None,
    limit: int = 50,
    include_redacted: bool = False,
) -> list[dict[str, Any]]:
    return conversation_store.search_memory(
        query,
        scope=scope,
        owner=owner,
        subject_id=subject_id,
        limit=limit,
        include_redacted=include_redacted,
    )


def forget(
    *,
    memory_id: str | None = None,
    scope: str | None = None,
    owner: str | None = None,
    subject_id: str | None = None,
    key: str | None = None,
    reason: str = "user_request",
    hard_delete: bool = False,
) -> int:
    return conversation_store.forget_memory(
        memory_id=memory_id,
        scope=scope,
        owner=owner,
        subject_id=subject_id,
        key=key,
        reason=reason,
        hard_delete=hard_delete,
    )


def record_consent(
    *,
    scope: str,
    owner: str,
    consent_state: str,
    subject_id: str | None = None,
    actor_owner: str | None = None,
    actor_id: str | None = None,
    reason: str = "user_request",
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    return conversation_store.record_memory_consent(
        scope=scope,
        owner=owner,
        subject_id=subject_id,
        consent_state=consent_state,
        actor_owner=actor_owner,
        actor_id=actor_id,
        reason=reason,
        policy=policy,
    )


def propose_write(
    kind: conversation_context.MemoryWriteKind,
    *,
    owner: str,
    text: str | None = None,
    key: str | None = None,
    value: Mapping[str, Any] | None = None,
    confidence: float | None = None,
    conversation_id: str | None = None,
    agent_id: str | None = None,
    webspace_id: str | None = None,
    source_ref: Mapping[str, Any] | None = None,
    consent_state: str | None = None,
    visibility: str | None = None,
    reason: str = "memory_write_proposal",
) -> dict[str, Any]:
    from adaos.services import pending_actions

    policy = conversation_context.memory_write_policy(
        kind,
        owner=owner,
        conversation_id=conversation_id,
        agent_id=agent_id,
        consent_state=consent_state,
        visibility=visibility,
    )
    proposed_memory = {
        "scope": policy["scope"],
        "owner": policy["owner"],
        "subject_id": policy.get("subject_id"),
        "key": key,
        "text": text,
        "value": dict(value or {}),
        "confidence": confidence,
        "consent_state": policy.get("consent_state"),
        "policy": dict(policy.get("policy") or {}),
        "source_ref": dict(source_ref or {}),
    }
    return pending_actions.publish_pending_action(
        kind="memory.write.review",
        webspace_id=webspace_id,
        title="Review memory write",
        title_i18n={"key": "pending_actions.memory.write.title"},
        summary=f"Review reusable memory for {policy['scope']}:{policy.get('subject_id') or policy['owner']}",
        summary_i18n={"key": "pending_actions.memory.write.summary"},
        allowed_actions=["approve", "refuse", "postpone"],
        domain_ref={
            "type": "memory.write_proposal",
            "scope": policy["scope"],
            "owner": policy["owner"],
            "subject_id": policy.get("subject_id"),
            "conversation_id": conversation_id,
            "agent_id": agent_id,
        },
        source_refs=[dict(source_ref or {})] if source_ref else [],
        metadata={
            "schema": "adaos.memory.write_proposal.v1",
            "reason": reason,
            "write_policy": policy,
            "proposed_memory": proposed_memory,
        },
        producer={"type": "skill" if owner.startswith("skill:") else "system", "id": owner},
        response_topic="memory.pending_action.response",
    )


def write_policy(
    kind: conversation_context.MemoryWriteKind,
    *,
    owner: str,
    conversation_id: str | None = None,
    agent_id: str | None = None,
    consent_state: str | None = None,
    visibility: str | None = None,
) -> dict[str, Any]:
    return conversation_context.memory_write_policy(
        kind,
        owner=owner,
        conversation_id=conversation_id,
        agent_id=agent_id,
        consent_state=consent_state,
        visibility=visibility,
    )
