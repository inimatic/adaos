from __future__ import annotations

import re
import time
from typing import Any, Mapping
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.domain.personalization_access import GrantConstraint, ScopeRef, SubjectRef
from adaos.services import personalization_runtime
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.personalization_access import PersonalizationAccessError


router = APIRouter(prefix="/personalization", tags=["personalization"])

_PROFILE_POLICY_KEYS = frozenset(("role", "roles", "membership", "memberships", "grant", "grants"))
_PROFILE_FIELDS = frozenset(
    ("display_name", "preferred_name", "locale", "language", "timezone", "avatar_ref")
)
_PREFERENCE_FIELDS = frozenset(
    (
        "theme",
        "ui_density",
        "memory_privacy",
        "accessibility_contrast",
        "accessibility_motion",
        "current_workspace",
        "locale",
        "language",
        "timezone",
    )
)
_ID_RE = re.compile(r"[^A-Za-z0-9_.:@/-]+")


class ScopePayload(BaseModel):
    kind: str = "workspace"
    id: str = "default"


class GuestInviteCreateRequest(BaseModel):
    scope: ScopePayload = Field(default_factory=ScopePayload)
    expires_in_minutes: int = Field(default=60, ge=1, le=60 * 24 * 30)
    max_sessions: int = Field(default=50, ge=1, le=500)


class TargetedInviteCreateRequest(BaseModel):
    scope: ScopePayload = Field(default_factory=ScopePayload)
    role: str = "member"
    profile_hint: str
    subject_id: str | None = None
    expires_in_minutes: int = Field(default=60 * 24 * 7, ge=1, le=60 * 24 * 90)


class InviteClaimRequest(BaseModel):
    subject_kind: str | None = None
    subject_id: str | None = None
    session_id: str | None = None
    expected_scope: ScopePayload | None = None


class InviteRevokeRequest(BaseModel):
    reason: str | None = None


def _access(ctx: AgentContext):
    return personalization_runtime.personalization_access_service(ctx)


def _profile(ctx: AgentContext):
    return personalization_runtime.current_user_profile_service(ctx)


def _actor(ctx: AgentContext) -> SubjectRef:
    return SubjectRef("user", personalization_runtime.current_user_id(ctx))


def _scope(payload: ScopePayload | None, ctx: AgentContext) -> ScopeRef:
    if payload is None:
        return ScopeRef("workspace", "default")
    return ScopeRef(str(payload.kind or "workspace"), str(payload.id or "default"))


def _expires_in(minutes: int) -> float:
    return time.time() + max(1, int(minutes)) * 60.0


def _safe_id(value: str, *, fallback: str) -> str:
    token = _ID_RE.sub("-", str(value or "").strip()).strip("-")
    return token or fallback


def _base_join_url(request: Request, invite_id: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/?adaos_invite={invite_id}"


def _public_invite_view(invite: Mapping[str, Any], request: Request) -> dict[str, Any]:
    invite_id = str(invite.get("invite_id") or "").strip()
    result = dict(invite)
    result["claim_url"] = _base_join_url(request, invite_id)
    return result


def _http_error(exc: Exception, *, status_code: int = 400) -> HTTPException:
    message = str(exc)
    status = status_code
    code = "personalization_error"
    if isinstance(exc, PermissionError):
        status = 403
        code = "policy_denied"
    elif "not found" in message:
        status = 404
        code = "not_found"
    elif "not pending" in message or "limit reached" in message or "expired" in message:
        status = 409
        code = "invite_not_acceptable"
    elif "profile settings cannot contain access policy keys" in message:
        status = 403
        code = "profile_policy_key_denied"
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _profile_patch(body: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(body or {})
    settings = data.get("settings")
    patch = dict(settings) if isinstance(settings, Mapping) else {}
    for key in _PROFILE_FIELDS:
        if key in data:
            patch[key] = data.get(key)
    policy_keys = _PROFILE_POLICY_KEYS.intersection(data).union(_PROFILE_POLICY_KEYS.intersection(patch))
    if policy_keys:
        joined = ", ".join(sorted(policy_keys))
        raise ValueError(f"profile settings cannot contain access policy keys: {joined}")
    return patch


def _preferences_patch(body: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(body or {})
    preferences = data.get("preferences")
    patch = dict(preferences) if isinstance(preferences, Mapping) else {}
    for key in _PREFERENCE_FIELDS:
        if key in data:
            patch[key] = data.get(key)
    return patch


def _profile_view(profile: Any) -> dict[str, Any]:
    return {
        "user_id": profile.user_id,
        "settings": dict(profile.settings),
        "display_name": profile.display_name,
        "preferred_name": profile.preferred_name,
        "locale": profile.locale,
        "language": profile.language,
        "timezone": profile.timezone,
        "avatar_ref": profile.avatar_ref,
        "schema_version": profile.schema_version,
        "preferences": dict(profile.preferences),
    }


def _listed_invites(service: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for status in ("pending", "accepted", "expired", "revoked"):
        for item in service.store.iter_invites(status=status):
            row = dict(item)
            try:
                preview = service.preview_invite(str(row.get("invite_id") or ""))
                row.update(preview)
            except Exception:
                pass
            expires_at = row.get("expires_at")
            try:
                if row.get("status") == "pending" and expires_at is not None and float(expires_at) <= time.time():
                    row["status"] = "expired"
                    row["can_accept"] = False
            except Exception:
                pass
            rows.append(row)
    rows.sort(key=lambda item: float(item.get("created_at") or item.get("expires_at") or 0), reverse=True)
    return rows


@router.get("/current-user/header-settings", dependencies=[Depends(require_token)])
async def get_current_user_header(ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    service = _profile(ctx)
    return {"ok": True, "settings": service.header_settings()}


@router.get("/current-user/profile", dependencies=[Depends(require_token)])
async def get_current_user_profile(ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    service = _profile(ctx)
    return {"ok": True, "profile": _profile_view(service.get_profile())}


@router.patch("/current-user/profile", dependencies=[Depends(require_token)])
async def update_current_user_profile(body: dict[str, Any], ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        service = _profile(ctx)
        profile = service.update_profile(_profile_patch(body), actor=_actor(ctx))
        return {"ok": True, "profile": _profile_view(profile)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/current-user/preferences", dependencies=[Depends(require_token)])
async def get_current_user_preferences(ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    service = _profile(ctx)
    return {"ok": True, "preferences": service.get_preferences()}


@router.patch("/current-user/preferences", dependencies=[Depends(require_token)])
async def update_current_user_preferences(body: dict[str, Any], ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        service = _profile(ctx)
        preferences = service.update_preferences(_preferences_patch(body), actor=_actor(ctx))
        return {"ok": True, "preferences": preferences}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/policy/explain", dependencies=[Depends(require_token)])
async def explain_policy(
    action: str,
    subject_kind: str = Query(default="user"),
    subject_id: str | None = Query(default=None),
    scope_kind: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        actor = _actor(ctx)
        subject = SubjectRef(subject_kind, subject_id or actor.id) if subject_kind and (subject_id or actor.id) else None
        scope = ScopeRef(scope_kind, scope_id) if scope_kind and scope_id else None
        decision = _access(ctx).evaluate(actor=actor, action=action, subject=subject, scope=scope)
        return {"ok": True, "decision": decision.to_dict()}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/invites/guest", dependencies=[Depends(require_token)])
async def create_guest_invite(
    body: GuestInviteCreateRequest,
    request: Request,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        invite_id = f"guest-{uuid4().hex}"
        service = _access(ctx)
        invite = service.create_guest_join_link(
            invite_id=invite_id,
            scope=_scope(body.scope, ctx),
            issued_by=_actor(ctx),
            expires_at=_expires_in(body.expires_in_minutes),
            max_sessions=body.max_sessions,
        )
        return {"ok": True, "invite": _public_invite_view(invite, request)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/invites/targeted", dependencies=[Depends(require_token)])
async def create_targeted_invite(
    body: TargetedInviteCreateRequest,
    request: Request,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        hint = str(body.profile_hint or "").strip()
        if not hint:
            raise ValueError("profile_hint is required")
        invite_id = f"invite-{uuid4().hex}"
        service = _access(ctx)
        invite = service.create_targeted_invite_link(
            invite_id=invite_id,
            scope=_scope(body.scope, ctx),
            role=body.role,
            issued_by=_actor(ctx),
            profile_hint=hint,
            expires_at=_expires_in(body.expires_in_minutes),
            constraints=GrantConstraint(),
        )
        if body.subject_id:
            invite["subject_id"] = _safe_id(body.subject_id, fallback=hint)
        return {"ok": True, "invite": _public_invite_view(invite, request)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/invites", dependencies=[Depends(require_token)])
async def list_invites(request: Request, ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    service = _access(ctx)
    return {
        "ok": True,
        "invites": [_public_invite_view(item, request) for item in _listed_invites(service)],
    }


@router.get("/invites/{invite_id}/preview")
async def preview_invite(invite_id: str, ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        preview = _access(ctx).preview_invite(invite_id)
        return {"ok": True, "preview": preview}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/invites/{invite_id}/claim")
async def claim_invite(invite_id: str, body: InviteClaimRequest, ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        service = _access(ctx)
        preview = service.preview_invite(invite_id)
        kind = str(preview.get("kind") or "")
        session_id = str(body.session_id or body.subject_id or f"join-{uuid4().hex}").strip()
        if kind == "guest_join_link":
            subject = SubjectRef("session", _safe_id(session_id, fallback=f"join-{uuid4().hex}"))
        else:
            subject_kind = str(body.subject_kind or "user")
            subject_id = str(body.subject_id or "").strip()
            if not subject_id:
                raise ValueError("subject_id is required for targeted invite claim")
            subject = SubjectRef(subject_kind, _safe_id(subject_id, fallback="invited-user"))
        data = service.claim_invite(
            invite_id,
            accepted_by=subject,
            actor=subject,
            expected_scope=_scope(body.expected_scope, ctx) if body.expected_scope else None,
            session_id=_safe_id(session_id, fallback=subject.id) if session_id else None,
        )
        return {"ok": True, "invite": data}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/invites/{invite_id}/revoke", dependencies=[Depends(require_token)])
async def revoke_invite(
    invite_id: str,
    body: InviteRevokeRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        data = _access(ctx).revoke_invite(invite_id, actor=_actor(ctx), reason=body.reason)
        return {"ok": True, "invite": data}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/invites/{invite_id}/guest-sessions/revoke", dependencies=[Depends(require_token)])
async def revoke_guest_sessions(
    invite_id: str,
    body: InviteRevokeRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        data = _access(ctx).revoke_guest_join_sessions(invite_id, actor=_actor(ctx), reason=body.reason)
        return {"ok": True, "invite": data}
    except Exception as exc:
        raise _http_error(exc) from exc
