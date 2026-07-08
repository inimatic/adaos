from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen
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
_DEFAULT_LANGUAGE_OPTIONS = (
    {"value": "en", "label": "English", "locale": "en-US"},
    {"value": "ru", "label": "Russian", "locale": "ru-RU"},
)
_LANGUAGE_LABELS = {str(item["value"]): str(item["label"]) for item in _DEFAULT_LANGUAGE_OPTIONS}
_LANGUAGE_LOCALES = {str(item["value"]): str(item["locale"]) for item in _DEFAULT_LANGUAGE_OPTIONS}
_COMMON_TIMEZONES = (
    "UTC",
    "Europe/Moscow",
    "Europe/London",
    "Europe/Paris",
    "America/New_York",
    "America/Los_Angeles",
    "Asia/Dubai",
    "Asia/Shanghai",
    "Asia/Tokyo",
)


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
    device_id: str | None = None
    device_name: str | None = None
    key_id: str | None = None
    public_key_ref: str | None = None
    expected_scope: ScopePayload | None = None


class InviteRevokeRequest(BaseModel):
    reason: str | None = None


class DevicePairingLinkCreateRequest(BaseModel):
    scope: ScopePayload = Field(default_factory=ScopePayload)
    subject_id: str
    role: str = "member"
    device_id: str | None = None
    device_name: str | None = None
    expires_in_minutes: int = Field(default=15, ge=1, le=60 * 24 * 7)


class AdminRecoveryLinkCreateRequest(BaseModel):
    scope: ScopePayload = Field(default_factory=ScopePayload)
    subject_id: str
    replacement_device_id: str | None = None
    revoked_device_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    expires_in_minutes: int = Field(default=30, ge=1, le=60 * 24 * 7)


class AdminGrantRequest(BaseModel):
    subject_id: str
    scope: ScopePayload = Field(default_factory=ScopePayload)
    role: str
    expires_in_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 365)


class AdminRevokeRequest(BaseModel):
    reason: str | None = None


def _access(ctx: AgentContext):
    return personalization_runtime.personalization_access_service(ctx)


def _profile(ctx: AgentContext):
    return personalization_runtime.current_user_profile_service(ctx)


def _actor(ctx: AgentContext) -> SubjectRef:
    return SubjectRef("user", personalization_runtime.current_user_id(ctx))


def _user_subject(value: str, *, fallback: str = "user") -> SubjectRef:
    return SubjectRef("user", _safe_id(value, fallback=fallback))


def _scope(payload: ScopePayload | None, ctx: AgentContext) -> ScopeRef:
    if payload is None:
        return ScopeRef("workspace", "default")
    return ScopeRef(str(payload.kind or "workspace"), str(payload.id or "default"))


def _expires_in(minutes: int) -> float:
    return time.time() + max(1, int(minutes)) * 60.0


def _optional_expires_in(minutes: int | None) -> float | None:
    return _expires_in(minutes) if minutes is not None else None


def _safe_id(value: str, *, fallback: str) -> str:
    token = _ID_RE.sub("-", str(value or "").strip()).strip("-")
    return token or fallback


def _sync_browser_device_link(device_id: str, *, display_name: str | None = None) -> None:
    token = str(device_id or "").strip()
    if not token:
        return
    try:
        from adaos.services import access_links

        patch: dict[str, Any] = {
            "access_class": "device",
            "admission_policy": "allow",
            "lifetime_mode": "permanent",
        }
        name = str(display_name or "").strip()
        if name:
            patch["device_display_name"] = name
        access_links.upsert_link("browser", token, patch)
    except Exception:
        pass


def _sync_guest_browser_link(
    device_id: str,
    *,
    session_id: str | None = None,
    display_name: str | None = None,
    expires_at: float | None = None,
) -> None:
    token = str(device_id or session_id or "").strip()
    if not token:
        return
    try:
        from adaos.services import access_links

        patch: dict[str, Any] = {
            "access_class": "client",
            "admission_policy": "allow",
            "lifetime_mode": "fixed",
        }
        clean_session_id = str(session_id or "").strip()
        if clean_session_id:
            patch["admission_session_id"] = clean_session_id
        if expires_at is not None:
            patch["expires_at"] = float(expires_at)
        name = str(display_name or "").strip()
        if name:
            patch["device_display_name"] = name
        access_links.upsert_link("browser", token, patch)
    except Exception:
        pass


def _deny_browser_link(device_id: str) -> None:
    token = str(device_id or "").strip()
    if not token:
        return
    try:
        from adaos.services import access_links

        access_links.deny_link("browser", token)
        for entry in access_links.list_links("browser"):
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id or entry_id == token:
                continue
            if str(entry.get("admission_session_id") or "").strip() == token:
                access_links.deny_link("browser", entry_id)
    except Exception:
        pass


def _setting_text(ctx: AgentContext, name: str, fallback: str = "") -> str:
    token = str(getattr(ctx.settings, name, "") or "").strip()
    return token or fallback


def _root_hub_base(ctx: AgentContext) -> str:
    api_base = _setting_text(ctx, "api_base", "https://api.inimatic.com").rstrip("/")
    subnet_id = personalization_runtime.current_subnet_id(ctx)
    if subnet_id:
        return f"{api_base}/hubs/{subnet_id}"
    return api_base


def _base_join_url(request: Request, ctx: AgentContext, invite_id: str) -> str:
    app_base = _setting_text(ctx, "app_base", str(request.base_url)).rstrip("/") or str(request.base_url).rstrip("/")
    subnet_id = personalization_runtime.current_subnet_id(ctx)
    params: dict[str, str] = {
        "adaos_invite": invite_id,
        "try_local_hub": "0",
    }
    if subnet_id:
        params["mode"] = "login"
        params["target_subnet"] = subnet_id
        params["adaos_subnet"] = subnet_id
    hub_base = _root_hub_base(ctx)
    if hub_base:
        params["adaos_hub_base"] = hub_base
    return f"{app_base}/?{urlencode(params)}"


def _root_invite_registration_token(ctx: AgentContext) -> str:
    return str(getattr(ctx.settings, "root_token", None) or "").strip()


def _current_zone_id(ctx: AgentContext) -> str:
    for source in (getattr(ctx, "config", None), getattr(ctx, "settings", None)):
        value = str(getattr(source, "zone_id", "") or "").strip().lower()
        if value:
            return value
    return ""


def _register_root_invite_session(
    invite: Mapping[str, Any],
    request: Request,
    ctx: AgentContext,
    fallback_claim_url: str,
) -> str | None:
    token = _root_invite_registration_token(ctx)
    if not token:
        return None
    invite_id = str(invite.get("invite_id") or "").strip()
    subnet_id = personalization_runtime.current_subnet_id(ctx)
    if not invite_id or not subnet_id:
        return None
    api_base = _setting_text(ctx, "api_base", "").rstrip("/")
    if not api_base:
        return None
    app_base = _setting_text(ctx, "app_base", str(request.base_url)).rstrip("/") or str(request.base_url).rstrip("/")
    body = {
        "invite_id": invite_id,
        "subnet_id": subnet_id,
        "hub_base": _root_hub_base(ctx),
        "app_base": app_base,
        "kind": str(invite.get("kind") or ""),
        "role": str(invite.get("role") or ""),
        "status": str(invite.get("status") or ""),
        "expires_at": invite.get("expires_at"),
        "claim_url": fallback_claim_url,
    }
    for key in (
        "scope",
        "profile_hint",
        "subject_id",
        "device_id",
        "device_name",
        "replacement_device_id",
        "recovery_id",
        "revoked_device_ids",
        "claim_count",
        "max_sessions",
        "single_use",
    ):
        if key in invite:
            body[key] = invite.get(key)
    zone_id = _current_zone_id(ctx)
    if zone_id:
        body["zone"] = zone_id
        body["zone_id"] = zone_id
    try:
        req = UrlRequest(
            f"{api_base}/v1/personalization/invites/register",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Root-Token": token,
            },
            method="POST",
        )
        with urlopen(req, timeout=1.5) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
        invite_payload = payload.get("invite") if isinstance(payload, Mapping) else None
        claim_url = str(
            (payload.get("claim_url") if isinstance(payload, Mapping) else "")
            or (invite_payload.get("claim_url") if isinstance(invite_payload, Mapping) else "")
            or ""
        ).strip()
        if "mode=registration" in claim_url and "user_code=" in claim_url and "zone=" in claim_url:
            return claim_url
    except Exception:
        return None
    return None


def _register_root_invite_revocation(
    invite: Mapping[str, Any],
    request: Request,
    ctx: AgentContext,
    *,
    reason: str | None = None,
    guest_sessions_only: bool = False,
) -> None:
    token = _root_invite_registration_token(ctx)
    if not token:
        return
    invite_id = str(invite.get("invite_id") or "").strip()
    subnet_id = personalization_runtime.current_subnet_id(ctx)
    if not invite_id or not subnet_id:
        return
    api_base = _setting_text(ctx, "api_base", "").rstrip("/")
    if not api_base:
        return
    path = "guest-sessions/revoke" if guest_sessions_only else "revoke"
    body: dict[str, Any] = {
        "invite_id": invite_id,
        "subnet_id": subnet_id,
        "hub_id": subnet_id,
        "status": str(invite.get("status") or ""),
        "revoked_at": invite.get("revoked_at") or time.time(),
        "reason": str(reason or "").strip(),
    }
    try:
        req = UrlRequest(
            f"{api_base}/v1/personalization/invites/{invite_id}/{path}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Root-Token": token,
            },
            method="POST",
        )
        with urlopen(req, timeout=1.5):
            pass
    except Exception:
        return


def _public_invite_view(invite: Mapping[str, Any], request: Request, ctx: AgentContext) -> dict[str, Any]:
    invite_id = str(invite.get("invite_id") or "").strip()
    result = dict(invite)
    fallback_claim_url = _base_join_url(request, ctx, invite_id)
    claim_url = _register_root_invite_session(invite, request, ctx, fallback_claim_url)
    result["claim_url"] = claim_url or ""
    if not claim_url:
        result["claim_url_error"] = "root_invite_session_unavailable"
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


def _path_from_accessor(accessor: Any) -> Any:
    try:
        value = accessor() if callable(accessor) else accessor
    except Exception:
        return None
    return value


def _normalize_language_code(raw: str) -> str:
    token = str(raw or "").strip().lower().replace("_", "-")
    if not token:
        return ""
    if token.startswith("zh") or token.startswith("ch"):
        return "ch"
    return token.split("-", 1)[0]


def _discover_language_codes(ctx: AgentContext) -> list[str]:
    codes: set[str] = set()
    for accessor_name in ("locales_dir", "locales_base_dir", "skills_locales_dir", "scenarios_locales_dir"):
        accessor = getattr(ctx.paths, accessor_name, None)
        path_value = _path_from_accessor(accessor)
        if not path_value:
            continue
        path = path_value if hasattr(path_value, "glob") else Path(path_value)
        try:
            if not path.exists():
                continue
            for item in path.glob("*.json"):
                code = _normalize_language_code(item.stem)
                if code:
                    codes.add(code)
        except Exception:
            continue
    return sorted(codes)


def _language_options(ctx: AgentContext) -> list[dict[str, str]]:
    codes = [str(item["value"]) for item in _DEFAULT_LANGUAGE_OPTIONS]
    for code in _discover_language_codes(ctx):
        if code not in codes:
            codes.append(code)
    return [
        {
            "value": code,
            "label": _LANGUAGE_LABELS.get(code, code),
            "locale": _LANGUAGE_LOCALES.get(code, code),
        }
        for code in codes
    ]


def _timezone_options() -> list[dict[str, str]]:
    zones = set(_COMMON_TIMEZONES)
    try:
        from zoneinfo import available_timezones

        zones.update(str(item) for item in available_timezones())
    except Exception:
        pass
    ordered = list(_COMMON_TIMEZONES) + sorted(zone for zone in zones if zone not in _COMMON_TIMEZONES)
    return [{"value": zone, "label": zone} for zone in ordered]


def _workspace_scope_options(ctx: AgentContext) -> list[dict[str, str]]:
    scopes: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, scope_id: str, label: str | None = None) -> None:
        token = str(scope_id or "").strip()
        normalized_kind = str(kind or "workspace").strip() or "workspace"
        if not token:
            return
        key = (normalized_kind, token)
        if key in seen:
            return
        seen.add(key)
        scopes.append(
            {
                "kind": normalized_kind,
                "id": token,
                "value": f"{normalized_kind}:{token}",
                "label": str(label or token).strip() or token,
            }
        )

    try:
        from adaos.services.scenario.webspace_runtime import WebspaceService

        for item in WebspaceService().list(mode="mixed"):
            add("workspace", getattr(item, "id", ""), getattr(item, "title", None))
    except Exception:
        pass

    try:
        prefs = _profile(ctx).get_preferences()
        add("workspace", str(prefs.get("current_workspace") or "").strip())
    except Exception:
        pass
    add("workspace", "default", "Default workspace")
    add("subnet", personalization_runtime.current_subnet_id(ctx), "Current subnet")
    return scopes


def _device_name(entry: Mapping[str, Any] | None) -> str:
    if not isinstance(entry, Mapping):
        return ""
    for key in ("device_display_name", "display_name", "hostname", "browser_family", "os_name"):
        token = str(entry.get(key) or "").strip()
        if token:
            return token
    for key in ("aliases", "node_names"):
        raw = entry.get(key)
        if isinstance(raw, (list, tuple)):
            for item in raw:
                token = str(item or "").strip()
                if token:
                    return token
    return ""


def _device_option(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    device_id = str(entry.get("id") or "").strip()
    if not device_id:
        return None
    name = _device_name(entry)
    label = f"{name} | {device_id}" if name else device_id
    return {
        "id": device_id,
        "value": device_id,
        "name": name,
        "label": label,
        "trust": str(entry.get("admission_policy") or "").strip() or "allow",
    }


def _device_options() -> list[dict[str, Any]]:
    try:
        from adaos.services import access_links

        return [item for entry in access_links.list_links("browser") if (item := _device_option(entry))]
    except Exception:
        return []


def _current_device_status(request: Request) -> dict[str, Any]:
    device_id = str(request.headers.get("X-AdaOS-Device-Id") or request.headers.get("x-adaos-device-id") or "").strip()
    if not device_id:
        return {"id": "", "name": "", "label": "current", "trust": "unknown"}
    entry: dict[str, Any] | None = None
    try:
        from adaos.services import access_links

        entry = access_links.get_link("browser", device_id)
    except Exception:
        entry = None
    option = _device_option(entry or {"id": device_id}) or {"id": device_id, "name": "", "label": device_id}
    option.setdefault("trust", "unknown")
    return option


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
async def get_current_user_header(request: Request, ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    service = _profile(ctx)
    settings = service.header_settings()
    device_status = _current_device_status(request)
    settings["device_status"] = device_status
    settings["device_trust_status"] = str(device_status.get("label") or settings.get("device_trust_status") or "current")
    settings["identity_source"] = "owner_settings_fallback"
    return {"ok": True, "settings": settings}


@router.get("/options", dependencies=[Depends(require_token)])
async def get_personalization_options(ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    languages = _language_options(ctx)
    return {
        "ok": True,
        "options": {
            "languages": languages,
            "locales": [
                {"value": str(item.get("locale") or item.get("value") or ""), "label": str(item.get("locale") or item.get("value") or "")}
                for item in languages
                if str(item.get("locale") or item.get("value") or "").strip()
            ],
            "timezones": _timezone_options(),
            "scopes": _workspace_scope_options(ctx),
            "devices": _device_options(),
        },
    }


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
        return {"ok": True, "invite": _public_invite_view(invite, request, ctx)}
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
        return {"ok": True, "invite": _public_invite_view(invite, request, ctx)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/devices/pairing-links", dependencies=[Depends(require_token)])
async def create_device_pairing_link(
    body: DevicePairingLinkCreateRequest,
    request: Request,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        invite_id = f"device-{uuid4().hex}"
        invite = _access(ctx).create_device_pairing_link(
            invite_id=invite_id,
            subject=_user_subject(body.subject_id, fallback="paired-user"),
            scope=_scope(body.scope, ctx),
            role=body.role,
            issued_by=_actor(ctx),
            expires_at=_expires_in(body.expires_in_minutes),
            device_id=body.device_id,
            device_name=body.device_name,
        )
        return {"ok": True, "invite": _public_invite_view(invite, request, ctx)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/recovery/admin-links", dependencies=[Depends(require_token)])
async def create_admin_recovery_link(
    body: AdminRecoveryLinkCreateRequest,
    request: Request,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        invite_id = f"recovery-{uuid4().hex}"
        recovery_id = f"recovery-{uuid4().hex}"
        result = _access(ctx).create_admin_recovery_link(
            invite_id=invite_id,
            recovery_id=recovery_id,
            subject=_user_subject(body.subject_id, fallback="recovery-user"),
            scope=_scope(body.scope, ctx),
            issued_by=_actor(ctx),
            expires_at=_expires_in(body.expires_in_minutes),
            replacement_device_id=body.replacement_device_id,
            revoked_device_ids=tuple(_safe_id(item, fallback="device") for item in body.revoked_device_ids),
            reason=body.reason,
        )
        invite = _public_invite_view(result["invite"], request, ctx)
        return {"ok": True, "invite": invite, "recovery": result["recovery"]}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/invites", dependencies=[Depends(require_token)])
async def list_invites(request: Request, ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    service = _access(ctx)
    return {
        "ok": True,
        "invites": [_public_invite_view(item, request, ctx) for item in _listed_invites(service)],
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
            device_id = str(body.device_id or session_id or "").strip()
        elif kind == "device_pairing_link":
            subject_id = str(body.subject_id or preview.get("subject_id") or preview.get("profile_hint") or "").strip()
            if not subject_id:
                raise ValueError("subject_id is required for device pairing claim")
            device_id = str(body.device_id or preview.get("device_id") or session_id or "").strip()
            if not device_id:
                raise ValueError("device_id is required for device pairing claim")
            result = service.claim_device_pairing_link(
                invite_id,
                subject=_user_subject(subject_id, fallback="paired-user"),
                actor=_user_subject(subject_id, fallback="paired-user"),
                device_id=_safe_id(device_id, fallback="paired-device"),
                key_id=body.key_id,
                public_key_ref=body.public_key_ref,
                session_id=_safe_id(session_id or device_id, fallback="paired-session"),
                device_name=body.device_name,
            )
            _sync_browser_device_link(device_id, display_name=body.device_name)
            return {"ok": True, **result}
        elif kind == "admin_recovery_link":
            subject_id = str(body.subject_id or preview.get("subject_id") or preview.get("profile_hint") or "").strip()
            if not subject_id:
                raise ValueError("subject_id is required for admin recovery claim")
            replacement_device_id = str(body.device_id or preview.get("device_id") or session_id or "").strip()
            if not replacement_device_id:
                raise ValueError("device_id is required for admin recovery claim")
            result = service.complete_admin_recovery_link(
                invite_id,
                subject=_user_subject(subject_id, fallback="recovery-user"),
                replacement_device_id=_safe_id(replacement_device_id, fallback="replacement-device"),
                key_id=body.key_id,
                public_key_ref=body.public_key_ref,
                session_id=_safe_id(session_id or replacement_device_id, fallback="recovery-session"),
                revoke_device_ids=tuple(str(item or "").strip() for item in preview.get("revoked_device_ids") or []),
            )
            _sync_browser_device_link(replacement_device_id, display_name=body.device_name)
            return {"ok": True, **result}
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
        if kind == "guest_join_link":
            _sync_guest_browser_link(
                device_id,
                session_id=session_id,
                display_name=body.device_name,
                expires_at=preview.get("expires_at"),
            )
        return {"ok": True, "invite": data, "session_id": session_id, "device_id": device_id if kind == "guest_join_link" else None}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/admin/summary", dependencies=[Depends(require_token)])
async def admin_summary(
    audit_limit: int = Query(default=50, ge=1, le=200),
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return {"ok": True, "summary": _access(ctx).admin_summary(actor=_actor(ctx), audit_limit=audit_limit)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/admin/grants", dependencies=[Depends(require_token)])
async def admin_grant_role(body: AdminGrantRequest, ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        result = _access(ctx).grant_role_preset(
            subject=_user_subject(body.subject_id, fallback="granted-user"),
            scope=_scope(body.scope, ctx),
            role=body.role,
            actor=_actor(ctx),
            expires_at=_optional_expires_in(body.expires_in_minutes),
        )
        return {"ok": True, **result}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/admin/devices/{device_id}/revoke", dependencies=[Depends(require_token)])
async def admin_revoke_device(
    device_id: str,
    body: AdminRevokeRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        token = _safe_id(device_id, fallback="device")
        data = _access(ctx).revoke_device(token, actor=_actor(ctx), reason=body.reason)
        _deny_browser_link(token)
        return {"ok": True, "device": data}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/admin/sessions/{session_id}/revoke", dependencies=[Depends(require_token)])
async def admin_revoke_session(
    session_id: str,
    body: AdminRevokeRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        token = _safe_id(session_id, fallback="session")
        data = _access(ctx).revoke_session(token, actor=_actor(ctx), reason=body.reason)
        _deny_browser_link(token)
        return {"ok": True, "session": data}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/invites/{invite_id}/revoke", dependencies=[Depends(require_token)])
async def revoke_invite(
    invite_id: str,
    body: InviteRevokeRequest,
    request: Request,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        data = _access(ctx).revoke_invite(invite_id, actor=_actor(ctx), reason=body.reason)
        _register_root_invite_revocation(data, request, ctx, reason=body.reason)
        return {"ok": True, "invite": data}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/invites/{invite_id}/guest-sessions/revoke", dependencies=[Depends(require_token)])
async def revoke_guest_sessions(
    invite_id: str,
    body: InviteRevokeRequest,
    request: Request,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        data = _access(ctx).revoke_guest_join_sessions(invite_id, actor=_actor(ctx), reason=body.reason)
        _register_root_invite_revocation(data, request, ctx, reason=body.reason, guest_sessions_only=True)
        return {"ok": True, "invite": data}
    except Exception as exc:
        raise _http_error(exc) from exc
