from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import time
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import uuid4

from adaos.domain.personalization_access import GrantConstraint, ScopeRef, SubjectRef
from adaos.sdk import applications as applications_sdk
from adaos.sdk import navigation as sdk_navigation
from adaos.services.agent_context import get_ctx
from adaos.services import personalization_runtime
from adaos.services.workspaces import index as workspace_index

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
_SUMMARY_SECTIONS = [
    "people",
    "guests",
    "children",
    "subjects",
    "devices",
    "sessions",
    "application_access",
    "permissions",
    "activity",
    "memberships",
    "grants",
    "invites",
    "recovery_actions",
    "audit",
]

_SUMMARY_WEBUI_RESULT_PATHS = {
    **{
        section: f"response.result.users_access.{section}"
        for section in (
            "people",
            "guests",
            "children",
            "subjects",
            "devices",
            "sessions",
            "application_access",
            "permissions",
        )
    },
    **{
        section: f"response.result.administration.{section}"
        for section in (
            "memberships",
            "grants",
            "invites",
            "recovery_actions",
            "audit",
        )
    },
}

_SUMMARY_WEBUI_ITEM_FIELDS = {
    "person": {
        "subject_ref": "string",
        "kind": "string",
        "display_label": "string",
        "display_label_source": "profile|subject_ref",
        "initials": "string",
        "profile": "object",
        "memberships": "array<object>",
        "membership_summary": "string",
        "membership_count": "integer",
        "primary_role": "string",
        "application_access": "array<object>",
        "application_access_count": "integer",
        "invite": "object?",
    },
    "device": {
        "device_id": "string",
        "label": "string?",
        "status": "string",
        "subject_ref": "string?",
        "created_at": "datetime?",
        "expires_at": "datetime?",
        "last_seen_at": "datetime?",
    },
    "session": {
        "session_id": "string",
        "device_id": "string?",
        "status": "string",
        "subject_ref": "string?",
        "scope_ref": "string?",
        "opened_at": "datetime?",
        "expires_at": "datetime?",
        "revoked_at": "datetime?",
        "authentication_source": "string?",
    },
    "permission": {
        "permission_id": "string",
        "application_count": "integer",
        "active_grant_count": "integer",
        "explicit_deny_count": "integer",
        "applications": "array<object>",
        "applications_summary": "string",
    },
    "application_access": {
        "grant_id": "string",
        "subject_ref": "string",
        "application_id": "string",
        "application_roles": "array<string>",
        "permission_ceiling": "array<string>",
        "explicit_denies": "array<string>",
        "constraints": "object",
        "issuer_ref": "string",
        "status": "string",
        "revision": "integer",
        "created_at": "datetime",
        "updated_at": "datetime",
    },
    "invite": {
        "invite_id": "string",
        "kind": "string?",
        "status": "string",
        "role": "string?",
        "profile_hint": "string?",
        "scope": "object?",
        "expires_at": "datetime|number?",
        "single_use": "boolean?",
        "max_sessions": "integer?",
        "claim_url": "string?",
        "qr_text": "string?",
        "telegram_share_url": "string?",
    },
    "audit": {
        "audit_id": "string",
        "event_type": "string",
        "actor_ref": "string?",
        "scope_ref": "string?",
        "decision": "object",
        "resource": "string?",
        "occurred_at": "datetime?",
        "source": "string?",
    },
}


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
                properties={
                    "audit_limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "sections": {
                        "type": "array",
                        "items": {"type": "string", "enum": _SUMMARY_SECTIONS},
                        "uniqueItems": True,
                    },
                    "detail": {"type": "string", "enum": ["compact", "full"]},
                }
            ),
            output_schema=deepcopy(response),
            required_capability="users_access.read",
            metadata={
                **metadata,
                "handler": "users_access_summary",
                "webui_data_binding": {
                    "schema": "adaos.root_mcp.webui_data_binding.v1",
                    "transport_envelope": "node_root_mcp_bridge.v1",
                    "result_paths": deepcopy(_SUMMARY_WEBUI_RESULT_PATHS),
                    "section_item_types": {
                        **{
                            section: "person"
                            for section in ("people", "guests", "children", "subjects")
                        },
                        "devices": "device",
                        "sessions": "session",
                        "application_access": "application_access",
                        "permissions": "permission",
                        "invites": "invite",
                        "audit": "audit",
                    },
                    "item_fields": deepcopy(_SUMMARY_WEBUI_ITEM_FIELDS),
                },
            },
        ),
        RootMcpToolContract(
            id="users_access.scope_options",
            title="List access scope options",
            surface=RootMcpSurface.OPERATIONS,
            summary="Resolve selectable subnet, workspace, or webspace scopes for access forms.",
            input_schema=schema_object(
                properties={
                    "scope_kind": {
                        "type": "string",
                        "enum": ["subnet", "workspace", "webspace"],
                    }
                },
                required=["scope_kind"],
            ),
            output_schema=deepcopy(response),
            required_capability="users_access.read",
            metadata={**metadata, "handler": "users_access_scope_options"},
        ),
        RootMcpToolContract(
            id="users_access.current_profile",
            title="Show current user profile",
            surface=RootMcpSurface.OPERATIONS,
            summary="Read the authoritative current-user profile and portable preferences.",
            input_schema=schema_object(),
            output_schema=deepcopy(response),
            required_capability="profile.read.self",
            metadata={**metadata, "handler": "users_access_current_profile"},
        ),
        RootMcpToolContract(
            id="users_access.update_current_profile",
            title="Update current user profile",
            surface=RootMcpSurface.OPERATIONS,
            summary="Update allowlisted current-user profile fields and portable preferences.",
            input_schema=schema_object(
                properties={
                    **context,
                    "display_name": {"type": ["string", "null"], "maxLength": 160},
                    "preferred_name": {"type": ["string", "null"], "maxLength": 160},
                    "language": {"type": ["string", "null"], "maxLength": 32},
                    "locale": {"type": ["string", "null"], "maxLength": 32},
                    "timezone": {"type": ["string", "null"], "maxLength": 80},
                    "start_destination": {"type": ["string", "null"], "maxLength": 80},
                    "show_presence": {"type": ["boolean", "null"]},
                },
                required=["idempotency_key"],
            ),
            output_schema=deepcopy(response),
            required_capability="profile.write.self",
            side_effects="write",
            metadata={**metadata, "handler": "users_access_update_current_profile"},
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
                    "expires_in_minutes": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 525600,
                    },
                },
                required=[
                    "subject_id",
                    "role",
                    "scope_kind",
                    "scope_id",
                    "idempotency_key",
                ],
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
                    "expires_in_minutes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10080,
                    },
                    "max_sessions": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                required=[
                    "kind",
                    "role",
                    "scope_kind",
                    "scope_id",
                    "expires_in_minutes",
                    "idempotency_key",
                ],
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
            id="users_access.create_device_pairing",
            title="Create personal device pairing",
            surface=RootMcpSurface.OPERATIONS,
            summary="Create a bounded device-pairing invitation for one user.",
            input_schema=schema_object(
                properties={
                    **context,
                    **scope,
                    "subject_id": {"type": "string", "minLength": 1, "maxLength": 240},
                    "role": {"type": "string", "enum": _ROLE_VALUES},
                    "device_id": {"type": ["string", "null"], "maxLength": 240},
                    "device_name": {"type": ["string", "null"], "maxLength": 240},
                    "expires_in_minutes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10080,
                    },
                },
                required=[
                    "subject_id",
                    "role",
                    "scope_kind",
                    "scope_id",
                    "expires_in_minutes",
                    "idempotency_key",
                ],
            ),
            output_schema=deepcopy(response),
            required_capability="users_access.manage",
            side_effects="write",
            metadata={**metadata, "handler": "users_access_create_device_pairing"},
        ),
        RootMcpToolContract(
            id="users_access.create_admin_recovery",
            title="Create administrator recovery",
            surface=RootMcpSurface.OPERATIONS,
            summary="Create a bounded administrator recovery link for one user and replacement device.",
            input_schema=schema_object(
                properties={
                    **context,
                    **scope,
                    **reason,
                    "subject_id": {"type": "string", "minLength": 1, "maxLength": 240},
                    "replacement_device_id": {"type": ["string", "null"], "maxLength": 240},
                    "revoke_device_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 240},
                        "uniqueItems": True,
                    },
                    "expires_in_minutes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10080,
                    },
                },
                required=[
                    "subject_id",
                    "scope_kind",
                    "scope_id",
                    "expires_in_minutes",
                    "idempotency_key",
                ],
            ),
            output_schema=deepcopy(response),
            required_capability="users_access.manage",
            side_effects="write",
            metadata={**metadata, "handler": "users_access_create_admin_recovery"},
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
    auth = (
        context.get("auth_context")
        if isinstance(context.get("auth_context"), Mapping)
        else {}
    )
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


def _user_subject(value: Any) -> SubjectRef:
    token = str(value or "").strip()
    if not token:
        raise ValueError("subject_id is required")
    kind, separator, identifier = token.partition(":")
    if separator:
        if kind != "user" or not identifier:
            raise ValueError("subject_id must identify a user")
        return SubjectRef("user", identifier)
    return SubjectRef("user", token)


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
        zone=str(getattr(getattr(ctx, "config", None), "zone_id", "") or "").strip()
        or None,
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


def _invite_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    invite = deepcopy(dict(value))
    invite_id = str(invite.get("invite_id") or "").strip()
    if not invite_id:
        return invite
    claim_url = _claim_url(invite_id)
    invite["claim_url"] = claim_url
    invite["qr_text"] = claim_url
    invite["telegram_share_url"] = (
        "https://t.me/share/url?"
        + urlencode({"url": claim_url, "text": "AdaOS invitation"})
    )
    return invite


def _profile_projection() -> dict[str, Any]:
    profile = personalization_runtime.current_user_profile_service(get_ctx()).get_profile()
    preferences = dict(profile.preferences)
    return {
        "user_id": profile.user_id,
        "display_name": profile.display_name or profile.user_id,
        "preferred_name": profile.preferred_name or "",
        "language": profile.language or "en",
        "locale": profile.locale or "en-US",
        "timezone": profile.timezone or "UTC",
        "avatar_ref": profile.avatar_ref,
        "start_destination": str(preferences.get("start_destination") or "home"),
        "show_presence": bool(preferences.get("show_presence", True)),
    }


def _expires_at(arguments: Mapping[str, Any]) -> float | None:
    value = arguments.get("expires_in_minutes")
    if value in (None, ""):
        return None
    return time.time() + int(value) * 60


def _typed_ref(value: Any) -> str:
    item = value if isinstance(value, Mapping) else {}
    kind = str(item.get("kind") or "").strip()
    identifier = str(item.get("id") or "").strip()
    return f"{kind}:{identifier}" if kind and identifier else ""


def _audit_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    decision = value.get("decision")
    decision = decision if isinstance(decision, Mapping) else {}
    metadata = value.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    timestamp = value.get("ts")
    occurred_at = ""
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        occurred_at = datetime.fromtimestamp(
            float(timestamp), tz=timezone.utc
        ).isoformat()
    elif timestamp:
        occurred_at = str(timestamp)
    return {
        "audit_id": str(value.get("audit_id") or ""),
        "event_type": str(value.get("event_type") or ""),
        "actor": deepcopy(value.get("actor") or {}),
        "actor_ref": _typed_ref(value.get("actor")),
        "scope": deepcopy(value.get("scope") or {}),
        "scope_ref": _typed_ref(value.get("scope")),
        "decision": {
            key: deepcopy(decision.get(key))
            for key in ("decision", "reason_code", "action")
            if key in decision
        },
        "resource": str(metadata.get("resource") or decision.get("resource") or ""),
        "occurred_at": occurred_at,
        "source": str(value.get("source") or ""),
    }


def _handle_summary(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    directory = _service().admin_summary(
        actor=_actor(arguments),
        audit_limit=int(arguments.get("audit_limit") or 50),
    )
    surface = applications_sdk.get_users_access_surface(directory)
    requested = {
        str(item).strip()
        for item in arguments.get("sections") or _SUMMARY_SECTIONS
        if str(item).strip() in _SUMMARY_SECTIONS
    }
    detail = str(arguments.get("detail") or "full").strip().lower()

    def compact_people(items: Any) -> list[dict[str, Any]]:
        if detail != "compact" or not isinstance(items, list):
            return list(items or [])
        rows: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, Mapping):
                continue
            rows.append(
                {
                    key: deepcopy(raw.get(key))
                    for key in (
                        "subject_ref",
                        "kind",
                        "display_label",
                        "display_label_source",
                        "initials",
                        "profile",
                        "memberships",
                        "membership_summary",
                        "membership_count",
                        "primary_role",
                        "application_access_count",
                        "invite",
                    )
                    if key in raw
                }
            )
        return rows

    surface_result = {
        key: compact_people(value)
        if key in {"people", "guests", "children", "subjects"}
        else deepcopy(value)
        for key, value in surface.items()
        if key in {"schema", "diagnostics"} or key in requested
    }
    administration = {}
    for key in ("memberships", "grants", "invites", "recovery_actions", "audit"):
        if key not in requested:
            continue
        items = directory.get(key) or []
        administration[key] = (
            [_audit_projection(item) for item in items if isinstance(item, Mapping)]
            if key == "audit"
            else [_invite_projection(item) for item in items if isinstance(item, Mapping)]
            if key == "invites"
            else deepcopy(items)
        )
    return {
        "users_access": surface_result,
        "administration": administration,
    }


def _handle_scope_options(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    kind = str(arguments.get("scope_kind") or "").strip()
    context = _mcp_context(arguments)
    if kind == "subnet":
        subnet_id = str(context.get("subnet_id") or "").strip()
        return {
            "items": [
                {
                    "id": subnet_id,
                    "label": f"Subnet {subnet_id}",
                    "kind": "subnet",
                    "current": True,
                }
            ]
            if subnet_id
            else []
        }
    if kind not in {"workspace", "webspace"}:
        raise ValueError("scope_kind must be subnet, workspace, or webspace")

    rows = []
    for item in workspace_index.list_workspaces():
        workspace_id = str(item.workspace_id or "").strip()
        if not workspace_id or workspace_id.startswith("$"):
            continue
        if kind == "workspace" and item.is_dev:
            continue
        rows.append(
            {
                "id": workspace_id,
                "label": item.title,
                "kind": kind,
                "development": item.is_dev,
            }
        )
    rows.sort(key=lambda item: (str(item["label"]).casefold(), item["id"]))
    return {"items": rows}


def _handle_current_profile(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    return {"profile": _profile_projection()}


def _handle_update_current_profile(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    profile_fields = (
        "display_name",
        "preferred_name",
        "language",
        "locale",
        "timezone",
    )
    preference_fields = ("start_destination", "show_presence")
    profile_patch = {
        key: arguments.get(key)
        for key in profile_fields
        if key in arguments and arguments.get(key) is not None
    }
    preference_patch = {
        key: arguments.get(key)
        for key in preference_fields
        if key in arguments and arguments.get(key) is not None
    }
    if dry_run:
        return {
            "would_update": True,
            "profile_fields": sorted(profile_patch),
            "preference_fields": sorted(preference_patch),
        }
    service = personalization_runtime.current_user_profile_service(get_ctx())
    actor = _actor(arguments)
    if profile_patch:
        service.update_profile(profile_patch, actor=actor)
    if preference_patch:
        service.update_preferences(preference_patch, actor=actor)
    personalization_runtime.invalidate_current_user_header_settings(get_ctx())
    return {"profile": _profile_projection()}


def _handle_grant_role(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {
            "would_grant": True,
            "subject_id": arguments.get("subject_id"),
            "role": arguments.get("role"),
        }
    service = _service()
    subject = _user_subject(arguments.get("subject_id"))
    scope = _scope(arguments)
    role = str(arguments.get("role") or "").strip()
    for grant in service.store.iter_grants(status="active"):
        grant_subject = (
            grant.get("subject") if isinstance(grant.get("subject"), Mapping) else {}
        )
        grant_scope = (
            grant.get("scope") if isinstance(grant.get("scope"), Mapping) else {}
        )
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


def _handle_create_invite(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {
            "would_create": True,
            "kind": arguments.get("kind"),
            "role": arguments.get("role"),
        }
    service = _service()
    actor = _actor(arguments)
    kind = str(arguments.get("kind") or "").strip()
    idempotency_key = str(arguments.get("idempotency_key") or "").strip()
    invite_id = f"{kind}-{uuid4().hex}"
    for item in service.store.iter_invites(status="pending"):
        if str(item.get("idempotency_key") or "") == idempotency_key:
            return {"invite": _invite_projection(item), "duplicate": True}
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
    metadata = {"idempotency_key": idempotency_key}
    if kind == "targeted":
        metadata["subject_id"] = f"user-{uuid4().hex}"
    stored = service.store.update_invite(invite_id, metadata)
    return {"invite": _invite_projection(stored), "duplicate": False}


def _pending_invite_for_idempotency(idempotency_key: str) -> dict[str, Any] | None:
    for item in _service().store.iter_invites(status="pending"):
        if str(item.get("idempotency_key") or "") == idempotency_key:
            return _invite_projection(item)
    return None


def _handle_create_device_pairing(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {
            "would_create": True,
            "kind": "device_pairing_link",
            "subject_id": arguments.get("subject_id"),
        }
    idempotency_key = str(arguments.get("idempotency_key") or "").strip()
    duplicate = _pending_invite_for_idempotency(idempotency_key)
    if duplicate:
        return {"invite": duplicate, "duplicate": True}
    invite_id = f"device-{uuid4().hex}"
    stored = _service().create_device_pairing_link(
        invite_id=invite_id,
        subject=_user_subject(arguments.get("subject_id")),
        scope=_scope(arguments),
        role=str(arguments.get("role") or "member"),
        issued_by=_actor(arguments),
        expires_at=_expires_at(arguments),
        device_id=str(arguments.get("device_id") or "").strip() or None,
        device_name=str(arguments.get("device_name") or "").strip() or None,
    )
    stored = _service().store.update_invite(
        invite_id, {"idempotency_key": idempotency_key}
    )
    return {"invite": _invite_projection(stored), "duplicate": False}


def _handle_create_admin_recovery(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {
            "would_create": True,
            "kind": "admin_recovery_link",
            "subject_id": arguments.get("subject_id"),
        }
    idempotency_key = str(arguments.get("idempotency_key") or "").strip()
    duplicate = _pending_invite_for_idempotency(idempotency_key)
    if duplicate:
        return {"invite": duplicate, "duplicate": True}
    invite_id = f"recovery-{uuid4().hex}"
    recovery_id = f"recovery-{uuid4().hex}"
    result = _service().create_admin_recovery_link(
        invite_id=invite_id,
        recovery_id=recovery_id,
        subject=_user_subject(arguments.get("subject_id")),
        scope=_scope(arguments),
        issued_by=_actor(arguments),
        expires_at=_expires_at(arguments),
        replacement_device_id=str(arguments.get("replacement_device_id") or "").strip() or None,
        revoked_device_ids=tuple(
            str(item).strip()
            for item in arguments.get("revoke_device_ids") or ()
            if str(item).strip()
        ),
        reason=str(arguments.get("reason") or "").strip() or None,
    )
    stored = _service().store.update_invite(
        invite_id, {"idempotency_key": idempotency_key}
    )
    return {
        **result,
        "invite": _invite_projection(stored),
        "duplicate": False,
    }


def _handle_revoke_invite(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
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


def _handle_revoke_device(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
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


def _handle_revoke_session(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
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
        "users_access.scope_options": _handle_scope_options,
        "users_access.current_profile": _handle_current_profile,
        "users_access.update_current_profile": _handle_update_current_profile,
        "users_access.grant_role": _handle_grant_role,
        "users_access.create_invite": _handle_create_invite,
        "users_access.create_device_pairing": _handle_create_device_pairing,
        "users_access.create_admin_recovery": _handle_create_admin_recovery,
        "users_access.revoke_invite": _handle_revoke_invite,
        "users_access.revoke_device": _handle_revoke_device,
        "users_access.revoke_session": _handle_revoke_session,
    }


__all__ = ["contracts", "handlers"]
