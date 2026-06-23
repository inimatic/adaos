"""SDK helpers for the core Pending Actions plane."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from adaos.sdk.core._ctx import require_ctx

__all__ = [
    "expire_pending_actions",
    "list_pending_actions",
    "publish_pending_action",
    "respond_pending_action",
]


def publish_pending_action(
    *,
    kind: str,
    title: str = "",
    summary: str = "",
    title_i18n: Mapping[str, Any] | None = None,
    summary_i18n: Mapping[str, Any] | None = None,
    request_text: str = "",
    request_locale: str = "",
    preferred_locales: Sequence[str] | None = None,
    producer: Mapping[str, Any] | None = None,
    owner_scope: Mapping[str, Any] | None = None,
    domain_ref: Mapping[str, Any] | None = None,
    allowed_actions: Sequence[Any] | None = None,
    actions: Sequence[Any] | None = None,
    default_text_binding: bool | None = None,
    response_route: Mapping[str, Any] | None = None,
    response_topic: str | None = None,
    ttl_s: Any = None,
    expires_at: Any = None,
    priority: int | None = None,
    payload_ref: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    webspace_id: str | None = None,
    action_id: str | None = None,
) -> dict[str, Any]:
    ctx = require_ctx("sdk.pending_actions.publish")
    from adaos.services.pending_actions import publish_pending_action as _publish

    kwargs: dict[str, Any] = {
        "ctx": ctx,
        "webspace_id": webspace_id,
        "action_id": action_id,
        "kind": kind,
        "title": title,
        "summary": summary,
        "title_i18n": title_i18n,
        "summary_i18n": summary_i18n,
        "request_text": request_text,
        "request_locale": request_locale,
        "preferred_locales": preferred_locales,
        "producer": producer,
        "owner_scope": owner_scope,
        "domain_ref": domain_ref,
        "allowed_actions": allowed_actions,
        "actions": actions,
        "default_text_binding": default_text_binding,
        "response_route": response_route,
        "response_topic": response_topic,
        "ttl_s": ttl_s,
        "priority": priority,
        "payload_ref": payload_ref,
        "metadata": metadata,
    }
    if expires_at is not None:
        kwargs["expires_at"] = expires_at
    return _publish(**kwargs)


def respond_pending_action(
    action_id: str,
    response_action_id: str,
    *,
    responder: Mapping[str, Any] | None = None,
    response_payload: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    ctx = require_ctx("sdk.pending_actions.respond")
    from adaos.services.pending_actions import respond_pending_action as _respond

    return _respond(
        action_id,
        response_action_id,
        ctx=ctx,
        webspace_id=webspace_id,
        responder=responder,
        response_payload=response_payload,
        idempotency_key=idempotency_key,
    )


def list_pending_actions(
    *,
    webspace_id: str | None = None,
    include_terminal: bool = True,
) -> dict[str, Any]:
    require_ctx("sdk.pending_actions.list")
    from adaos.services.pending_actions import list_pending_actions as _list_pending

    return _list_pending(webspace_id=webspace_id, include_terminal=include_terminal)


def expire_pending_actions(*, webspace_id: str | None = None) -> dict[str, Any]:
    ctx = require_ctx("sdk.pending_actions.expire")
    from adaos.services.pending_actions import expire_pending_actions as _expire

    return _expire(ctx=ctx, webspace_id=webspace_id)
