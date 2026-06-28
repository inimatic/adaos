from __future__ import annotations

from typing import Any, Mapping

from adaos.services import conversation_store
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


def get(conversation_id: str, *, before_cursor: Any = None, limit: int = 50) -> dict[str, Any]:
    return conversation_store.list_projection(
        conversation_id,
        before_cursor=before_cursor,
        limit=limit,
        max_items=max(limit, 200),
    )
