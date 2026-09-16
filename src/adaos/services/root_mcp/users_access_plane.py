from __future__ import annotations

from copy import deepcopy
import time
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import uuid4

from adaos.domain.personalization_access import GrantConstraint, ScopeRef, SubjectRef
from adaos.sdk import applications as applications_sdk
from adaos.sdk import navigation as sdk_navigation
from adaos.services.agent_context import get_ctx
from adaos.services import personalization_runtime

from .model import (
    ROOT_MCP_RESPONSE_SCHEMA,
    RootMcpSurface,
    RootMcpToolContract,
    schema_object,
)


_ROLE_VALUES = ["owner", "co_owner", "admin", "member", "child", "guest"]
_SCOPE_VALUES = [
    "subnet",
    "workspace",
    "webspace",
    "scenario",
    "skill",
    "device_session",
    "user_private",
    "shared_workspace",
]


def contracts() -> list[RootMcpToolContract]:
    response = deepcopy(ROOT_MCP_RESPONSE_SCHEMA)
    metadata = {
        "published_by": "plane:users_access",
        "adapter": "adaos.services.personalization_access",
    }
    context = {
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
    }
    scope = {
        "scope_kind": {"type": "string", "enum": _SCOPE_VALUES},
        "scope_id": {"type": "string", "minLength": 1, "maxLength": 240},
    }
    reason = {"reason": {"type": ["string", "null"], "maxLength": 500}}
    return [
        RootMcpToolContract(
            id="users_access.summary",
            title="Show Users and Access",
            surface=RootMcpSurface.OPERATIONS,
            summary="Read owner-governed people, invitations, roles, devices, sessions, Application access and redacted activity.",
            input_schema=schema_object(
                properties={"audit_limit": {"type": "integer", "minimum": 1, "maximum": 200}}
            ),
            output_schema=deepcopy(response),
            required_capability="users_access.read",
            metadata={**metadata, "handler": "users_access_summary"},
        ),
        RootMcpToolContract(
            id="users_access.grant_role",
            title="Grant platform role",
            surface=RootMcpSurface.OPERATIONS,
            summary="Grant one built-in platform role to a user in an explicit scope.",
            input_schema=schema_object(
                properties={
                    **context,
                    **scope,
                    "subject_id": {"type": "string", "minLength": 1, "maxLength": 240},
                    "role": {"type": "string", "enum": _ROLE_VALUES},
                    "expires_in_minutes": {"type": ["integer", "null"], "minimum": 1, "maximum": 525600},
                },
                required=["subject_id", "role", "scope_kind", "scope_id", "idempotency_key"],
            ),
            output_schema=deepcopy(response),
            required_capability="users_access.manage",
            side_effects="write",
            metadata={**metadata, "handler": "users_access_grant_role"},
        ),
        RootMcpToolContract(
            id="users_access.create_invite",
            title="Create access invitation",
            surface=RootMcpSurface.OPERATIONS,
            summary="Create a bounded guest or targeted invitation and return its direct claim URL.",
            input_schema=schema_object(
                properties={
                    **context,
                    **scope,
                    "kind": {"type": "string", "enum": ["guest", "targeted"]},
                    "role": {"type": "string", "enum": _ROLE_VALUES},
                    "profile_hint": {"type": ["string", "null"], "maxLength": 240},
                    "expires_in_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
                    "max_sessions": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                required=["kind", "role", "scope_kind", "scope_id", "expires_in_minutes", "idempotency_key"],
            ),
            output_schema=deepcopy(response),
            required_capability="users_access.invite",
            side_effects="write",
            metadata={**metadata, "handler": "users_access_create_invite"},
        ),
        RootMcpToolContract(
            id="users_access.revoke_invite",
            title="Revoke access invitation",
            surface=RootMcpSurface.OPERATIONS,
            summary="Revoke an invitation and every access grant or session derived from it.",
            input_schema=schema_object(
                properties={
                    **context,
                    **reason,
                    "invite_id": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                required=["invite_id", "idempotency_key"],
            ),
            output_schema=deepcopy(response),
            required_capability="users_access.manage",
            side_effects="write",
            metadata={**metadata, "handler": "users_access_revoke_invite"},
        ),
        RootMcpToolContract(
            id="users_access.revoke_device",
            title="Revoke device",
            surface=RootMcpSurface.OPERATIONS,
            summary="Revoke a device and its active sessions through the shared access service.",
            input_schema=schema_object(
                properties={
                    **context,
                    **reason,
                    "device_id": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                required=["device_id", "idempotency_key"],
            ),
            output_schema=deepcopy(response),
            required_capability="users_access.manage",
            side_effects="write",
            metadata={**metadata, "handler": "users_access_revoke_device"},
        ),
        RootMcpToolContract(
            id="users_access.revoke_session",
            title="Revoke session",
            surface=RootMcpSurface.OPERATIONS,
            summary="Revoke one browser or device session through the shared access service.",
            input_schema=schema_object(
                properties={
                    **context,
                    **reason,
                    "session_id": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                required=["session_id", "idempotency_key"],
            ),
            output_schema=deepcopy(response),
            required_capability="users_access.manage",
            side_effects="write",
            metadata={**metadata, "handler": "users_access_revoke_session"},
        ),
    ]


def _mcp_context(arguments: Mapping[str, Any]) -> dict[str, Any]:
    raw = arguments.get("_mcp_context")
    context = dict(raw) if isinstance(raw, Mapping) else {}
    auth = context.get("auth_context") if isinstance(context.get("auth_context"), Mapping) else {}
    scope = context.get("scope") if isinstance(context.get("scope"), Mapping) else {}
    actor_ref = str(context.get("actor") or auth.get("actor") or "").strip()
    subnet_id = str(scope.get("subnet_id") or auth.get("subnet_id") or "").strip()
    if not actor_ref:
        raise ValueError("MCP actor context is required")
    if not subnet_id:
        raise ValueError("MCP subnet context is required")
    return {"actor_ref": actor_ref, "subnet_id": subnet_id}


def _subject(value: str) -> SubjectRef:
    kind, separator, identifier = str(value or "").strip().partition(":")
    if not separator or not kind or not identifier:
        raise ValueError("MCP actor must be a typed subject reference")
    return SubjectRef(kind, identifier)  # type: ignore[arg-type]


def _actor(arguments: Mapping[str, Any]) -> SubjectRef:
    return _subject(_mcp_context(arguments)["actor_ref"])


def _scope(arguments: Mapping[str, Any]) -> ScopeRef:
    context = _mcp_context(arguments)
    kind = str(arguments.get("scope_kind") or "subnet").strip()
    identifier = str(arguments.get("scope_id") or context["subnet_id"]).strip()
    return ScopeRef(kind, identifier)  # type: ignore[arg-type]


def _service():
    return personalization_runtime.personalization_access_service(get_ctx())


def _claim_url(invite_id: str) -> str:
    ctx = get_ctx()
    app_base = str(getattr(ctx.settings, "app_base", "") or "").strip().rstrip("/")
    if not app_base:
        raise RuntimeError("Application base URL is not configured")
    subnet_id = personalization_runtime.current_subnet_id(ctx)
    destination = sdk_navigation.login_destination(
        zone=str(getattr(getattr(ctx, "config", None), "zone_id", "") or "").strip() or None,
        subnet_id=subnet_id,
        auto_login=True,
        try_local_hub=False,
    )
    destination_url = sdk_navigation.build_url(destination, base_url=app_base)
    params = dict(parse_qsl(urlsplit(destination_url).query, keep_blank_values=True))
    params["adaos_invite"] = invite_id
    api_base = str(getattr(ctx.settings, "api_base", "") or "").strip().rstrip("/")
    if api_base and subnet_id:
        params["adaos_hub_base"] = f"{api_base}/hubs/{subnet_id}"
    return f"{app_base}/?{urlencode(params)}"


def _expires_at(arguments: Mapping[str, Any]) -> float | None:
    value = arguments.get("expires_in_minutes")
    if value in (None, ""):
        return None
    return time.time() + int(value) * 60


def _handle_summary(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    directory = _service().admin_summary(
        actor=_actor(arguments),
        audit_limit=int(arguments.get("audit_limit") or 50),
    )
    surface = applications_sdk.get_users_access_surface(directory)
    return {
        "users_access": surface,
        "administration": {
            "memberships": directory.get("memberships") or [],
            "grants": directory.get("grants") or [],
            "invites": directory.get("invites") or [],
            "recovery_actions": directory.get("recovery_actions") or [],
            "audit": directory.get("audit") or [],
        },
    }


def _handle_grant_role(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"would_grant": True, "subject_id": arguments.get("subject_id"), "role": arguments.get("role")}
    service = _service()
    subject = SubjectRef("user", str(arguments.get("subject_id") or "").strip())
    scope = _scope(arguments)
    role = str(arguments.get("role") or "").strip()
    for grant in service.store.iter_grants(status="active"):
        grant_subject = grant.get("subject") if isinstance(grant.get("subject"), Mapping) else {}
        grant_scope = grant.get("scope") if isinstance(grant.get("scope"), Mapping) else {}
        if (
            grant_subject.get("kind") == subject.kind
            and grant_subject.get("id") == subject.id
            and grant_scope.get("kind") == scope.kind
            and grant_scope.get("id") == scope.id
            and str(grant.get("role") or "") == role
        ):
            return {"grant": dict(grant), "duplicate": True}
    return {
        **service.grant_role_preset(
            subject=subject,
            scope=scope,
            role=role,
            actor=_actor(arguments),
            expires_at=_expires_at(arguments),
        ),
        "duplicate": False,
    }


def _handle_create_invite(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"would_create": True, "kind": arguments.get("kind"), "role": arguments.get("role")}
    service = _service()
    actor = _actor(arguments)
    kind = str(arguments.get("kind") or "").strip()
    idempotency_key = str(arguments.get("idempotency_key") or "").strip()
    invite_id = f"{kind}-{uuid4().hex}"
    for item in service.store.iter_invites(status="pending"):
        if str(item.get("idempotency_key") or "") == idempotency_key:
            invite = dict(item)
            invite["claim_url"] = _claim_url(str(invite.get("invite_id") or ""))
            return {"invite": invite, "duplicate": True}
    if kind == "guest":
        invite = service.create_guest_join_link(
            invite_id=invite_id,
            scope=_scope(arguments),
            issued_by=actor,
            expires_at=_expires_at(arguments),
            max_sessions=int(arguments.get("max_sessions") or 1),
        )
    else:
        hint = str(arguments.get("profile_hint") or "").strip()
        if not hint:
            raise ValueError("profile_hint is required for a targeted invitation")
        invite = service.create_targeted_invite_link(
            invite_id=invite_id,
            scope=_scope(arguments),
            role=str(arguments.get("role") or "member"),
            issued_by=actor,
            profile_hint=hint,
            expires_at=_expires_at(arguments),
            constraints=GrantConstraint(),
        )
    # The access store intentionally ignores unknown manifest fields. Persist
    # the idempotency marker as metadata owned by this adapter.
    stored = service.store.update_invite(invite_id, {"idempotency_key": idempotency_key})
    stored["claim_url"] = _claim_url(invite_id)
    return {"invite": stored, "duplicate": False}


def _handle_revoke_invite(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    invite_id = str(arguments.get("invite_id") or "").strip()
    if dry_run:
        return {"would_revoke": True, "invite_id": invite_id}
    current = _service().store.get_invite(invite_id)
    if current and str(current.get("status") or "") == "revoked":
        return {"invite": current, "duplicate": True}
    return {
        "invite": _service().revoke_invite(
            invite_id,
            actor=_actor(arguments),
            reason=str(arguments.get("reason") or "").strip() or None,
        ),
        "duplicate": False,
    }


def _handle_revoke_device(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    device_id = str(arguments.get("device_id") or "").strip()
    if dry_run:
        return {"would_revoke": True, "device_id": device_id}
    current = _service().store.get_device_key(device_id)
    if current and str(current.get("status") or "") == "revoked":
        return {"device": current, "duplicate": True}
    return {
        "device": _service().revoke_device(
            device_id,
            actor=_actor(arguments),
            reason=str(arguments.get("reason") or "").strip() or None,
        ),
        "duplicate": False,
    }


def _handle_revoke_session(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    session_id = str(arguments.get("session_id") or "").strip()
    if dry_run:
        return {"would_revoke": True, "session_id": session_id}
    current = _service().store.get_session(session_id)
    if current and str(current.get("status") or "") == "revoked":
        return {"session": current, "duplicate": True}
    return {
        "session": _service().revoke_session(
            session_id,
            actor=_actor(arguments),
            reason=str(arguments.get("reason") or "").strip() or None,
        ),
        "duplicate": False,
    }


def handlers() -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "users_access.summary": _handle_summary,
        "users_access.grant_role": _handle_grant_role,
        "users_access.create_invite": _handle_create_invite,
        "users_access.revoke_invite": _handle_revoke_invite,
        "users_access.revoke_device": _handle_revoke_device,
        "users_access.revoke_session": _handle_revoke_session,
    }


__all__ = ["contracts", "handlers"]
