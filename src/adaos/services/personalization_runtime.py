from __future__ import annotations

from pathlib import Path
from typing import Any

from adaos.domain.personalization_access import SubjectRef
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.user.profile import UserProfileService


def _ctx(ctx: AgentContext | None = None) -> AgentContext:
    return ctx or get_ctx()


def _state_dir(ctx: AgentContext) -> Path:
    raw = ctx.paths.state_dir()
    return Path(raw() if callable(raw) else raw)


def current_user_id(ctx: AgentContext | None = None) -> str:
    resolved = _ctx(ctx)
    owner = getattr(resolved.settings, "owner_id", None) or "local-owner"
    return str(owner).strip() or "local-owner"


def current_subnet_id(ctx: AgentContext | None = None) -> str:
    resolved = _ctx(ctx)
    for source in (getattr(resolved, "settings", None), getattr(resolved, "config", None)):
        value = getattr(source, "subnet_id", None)
        if value:
            token = str(value).strip()
            if token:
                return token
    return "local-subnet"


def personalization_access_store(ctx: AgentContext | None = None) -> PersonalizationAccessStore:
    resolved = _ctx(ctx)
    return PersonalizationAccessStore(_state_dir(resolved) / "personalization" / "access.v0.json")


def deny_browser_session(session_id: str) -> dict[str, Any] | None:
    token = str(session_id or "").strip()
    if not token:
        return None
    from adaos.services import access_links

    return access_links.deny_link("browser", token)


def personalization_access_service(ctx: AgentContext | None = None) -> PersonalizationAccessService:
    resolved = _ctx(ctx)
    owner = SubjectRef("user", current_user_id(resolved))
    return PersonalizationAccessService(
        personalization_access_store(resolved),
        owner=owner,
        access_link_denier=deny_browser_session,
    )


def current_user_profile_service(ctx: AgentContext | None = None) -> UserProfileService:
    resolved = _ctx(ctx)
    return UserProfileService(resolved, access=personalization_access_service(resolved))


__all__ = [
    "current_subnet_id",
    "current_user_id",
    "current_user_profile_service",
    "deny_browser_session",
    "personalization_access_service",
    "personalization_access_store",
]
