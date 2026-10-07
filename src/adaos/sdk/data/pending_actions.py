"""SDK helpers for the core Pending Actions plane."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from adaos.sdk import access
from adaos.sdk.core.contracts import public_contract
from adaos.sdk.core._ctx import require_ctx

__all__ = [
    "list_pending_interactions",
    "list_pending_actions",
    "publish_pending_action",
]


@public_contract(
    capabilities=("human_decision.read", "conversation.interaction"),
    permissions=("workspace.read",),
    effects=("read_only",),
    errors=("caller_access_denied", "invalid_cursor", "unsupported_field_mask", "query_too_broad"),
    boundedness={"kind": "bounded_page", "arguments": ["limit"]},
    pagination={"supported": True, "arguments": ["cursor"]},
    stability="beta",
    since="1.5.0",
    runtime_support={"owner": "conversation_runtime", "min_contract": 1},
)
def list_pending_interactions(
    *,
    conversation_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    statuses: Sequence[str] | None = None,
    owners: Sequence[str] | None = None,
    active_only: bool = True,
    field_mask: str = "summary",
) -> dict[str, Any]:
    """List canonical human-decision interactions visible to the caller."""

    require_ctx("sdk.pending_actions.list_interactions")
    access.require("workspace.read")
    application = access.application() or {}
    caller = access.caller() or {}
    principal = {**caller}
    if application.get("application_id"):
        principal["application_id"] = str(application["application_id"])
    from adaos.services.conversation_interactions import query_interactions

    return query_interactions(
        principal=principal,
        conversation_id=conversation_id,
        statuses=statuses,
        owners=owners,
        active_only=active_only,
        field_mask=field_mask,
        limit=limit,
        cursor=cursor,
    )


@public_contract(
    capabilities=("human_decision.legacy_publish",),
    effects=("durable_write",),
    errors=("pending_action_kind_not_admitted", "pending_action_choice_closure_missing"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="deprecated",
    since="1.0.0",
    deprecated=True,
    replacement="adaos.sdk.workflow.create_interaction + adaos.sdk.chat.request",
    migration_recipe="Model the decision as a governed workflow Interaction and present it through adaos.sdk.chat.request.",
)
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
    """Rejected legacy API; responses require an authenticated channel ingress."""

    del action_id, response_action_id, responder, response_payload, idempotency_key, webspace_id
    raise PermissionError(
        "pending_action_response_requires_verified_ingress; use an interaction action token "
        "or the trusted Client Pending Actions surface"
    )


@public_contract(
    capabilities=("human_decision.read",),
    permissions=("workspace.read",),
    effects=("read_only",),
    errors=("caller_access_denied", "invalid_cursor", "unsupported_field_mask"),
    boundedness={"kind": "bounded_page", "arguments": ["limit"]},
    pagination={"supported": True, "arguments": ["cursor"]},
    stability="beta",
    since="1.5.0",
    runtime_support={"owner": "pending_action_projection", "min_contract": 1},
)
def list_pending_actions(
    *,
    webspace_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    statuses: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    field_mask: str = "summary",
) -> dict[str, Any]:
    require_ctx("sdk.pending_actions.list")
    access.require("workspace.read")
    application = access.application() or {}
    caller = access.caller() or {}
    principal = {**caller}
    if application.get("application_id"):
        principal["application_id"] = str(application["application_id"])
    from adaos.services.pending_actions import query_pending_actions as _query_pending

    return _query_pending(
        webspace_id=webspace_id,
        limit=limit,
        cursor=cursor,
        statuses=statuses,
        kinds=kinds,
        field_mask=field_mask,
        principal=principal,
    )


def expire_pending_actions(*, webspace_id: str | None = None) -> dict[str, Any]:
    ctx = require_ctx("sdk.pending_actions.expire")
    from adaos.services.pending_actions import expire_pending_actions as _expire

    return _expire(ctx=ctx, webspace_id=webspace_id)
