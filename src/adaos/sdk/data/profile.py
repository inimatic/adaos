from __future__ import annotations

from typing import Any, Dict, Optional

from adaos.sdk.core._ctx import require_ctx
from adaos.services.user.profile import UserProfileService


def _svc() -> UserProfileService:
    ctx = require_ctx("sdk.data.profile")
    return UserProfileService(ctx)


def get_settings(user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Return profile settings for the given user_id or for the current
    logical user (owner_id) when user_id is omitted.
    """
    return _svc().get_profile(user_id).settings


def get_profile(user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Return the versioned current-user profile projection without access-policy
    fields such as role or membership.
    """
    profile = _svc().get_profile(user_id)
    return {
        "user_id": profile.user_id,
        "display_name": profile.display_name,
        "preferred_name": profile.preferred_name,
        "locale": profile.locale,
        "language": profile.language,
        "timezone": profile.timezone,
        "avatar_ref": profile.avatar_ref,
        "settings": dict(profile.settings),
        "preferences": dict(profile.preferences),
        "schema_version": profile.schema_version,
    }


def update_settings(patch: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Merge the given patch into the user's profile settings and return
    the updated settings mapping.
    """
    prof = _svc().update_profile(patch, user_id)
    return prof.settings


def get_preferences(user_id: Optional[str] = None) -> Dict[str, Any]:
    return _svc().get_preferences(user_id)


def update_preferences(
    patch: Dict[str, Any],
    user_id: Optional[str] = None,
    *,
    device_override: bool = False,
) -> Dict[str, Any]:
    return _svc().update_preferences(patch, user_id, device_override=device_override)


def get_header_settings(user_id: Optional[str] = None) -> Dict[str, Any]:
    return _svc().header_settings(user_id)


__all__ = [
    "get_header_settings",
    "get_preferences",
    "get_profile",
    "get_settings",
    "update_preferences",
    "update_settings",
]

