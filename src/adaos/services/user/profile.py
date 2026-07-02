from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from adaos.domain.personalization_access import (
    Preference,
    ScopeRef,
    SubjectRef,
    UserProfile as ContractUserProfile,
)
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.eventbus import emit
from adaos.services.personalization_access import PersonalizationAccessService


@dataclass(slots=True)
class UserProfile:
    user_id: str
    settings: Dict[str, object]
    display_name: str | None = None
    preferred_name: str | None = None
    locale: str | None = None
    language: str | None = None
    timezone: str | None = None
    avatar_ref: str | None = None
    schema_version: str | None = None
    preferences: Dict[str, object] = field(default_factory=dict)


class UserProfileService:
    """
    User profile/preference layer.

    - Preserves the MVP settings API under:
        users/<user_id>/settings
    - Also writes versioned Phase 0 profile/preference records.
    - Keeps role and membership out of profile data.
    """

    def __init__(
        self,
        ctx: Optional[AgentContext] = None,
        *,
        access: PersonalizationAccessService | None = None,
    ) -> None:
        self.ctx: AgentContext = ctx or get_ctx()
        owner = SubjectRef("user", self.current_user_id())
        self.access = access or PersonalizationAccessService(owner=owner)

    def current_user_id(self) -> str:
        """
        Return the logical user identifier for the current process.
        For MVP this is Settings.owner_id or 'local-owner' if not set.
        """
        owner = getattr(self.ctx.settings, "owner_id", None) or "local-owner"
        return str(owner)

    def _kv_key(self, user_id: str) -> str:
        return f"users/{user_id}/settings"

    def _profile_key(self, user_id: str) -> str:
        return f"users/{user_id}/profile.v0"

    def _preferences_key(self, user_id: str) -> str:
        return f"users/{user_id}/preferences.v0"

    def _subject(self, user_id: str) -> SubjectRef:
        return SubjectRef("user", str(user_id or "").strip())

    def _scope(self, user_id: str) -> ScopeRef:
        return ScopeRef("user_private", str(user_id or "").strip())

    def _actor(self, actor: SubjectRef | str | None = None) -> SubjectRef:
        if isinstance(actor, SubjectRef):
            return actor
        return self._subject(str(actor or self.current_user_id()))

    def _check(self, actor: SubjectRef, action: str, subject: SubjectRef) -> None:
        decision = self.access.evaluate(
            actor=actor,
            action=action,
            subject=subject,
            scope=self._scope(subject.id),
        )
        if decision.decision != "allow":
            raise PermissionError(f"profile policy denied: {decision.reason_code or action}")

    def _contract_profile(self, user_id: str, settings: Mapping[str, object]) -> ContractUserProfile:
        return ContractUserProfile(
            user_id=user_id,
            display_name=self._optional_text(settings.get("display_name")),
            preferred_name=self._optional_text(settings.get("preferred_name")),
            locale=self._optional_text(settings.get("locale")),
            language=self._optional_text(settings.get("language")),
            timezone=self._optional_text(settings.get("timezone")),
            avatar_ref=self._optional_text(settings.get("avatar_ref")),
            settings=dict(settings),
        )

    @staticmethod
    def _optional_text(value: object) -> str | None:
        token = str(value or "").strip()
        return token or None

    def get_profile(self, user_id: Optional[str] = None) -> UserProfile:
        uid = user_id or self.current_user_id()
        raw = self.ctx.kv.get(self._kv_key(uid), {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        contract = self._contract_profile(uid, raw)
        preferences = self.get_preferences(uid)
        return UserProfile(
            user_id=uid,
            settings=dict(raw),
            display_name=contract.display_name,
            preferred_name=contract.preferred_name,
            locale=contract.locale,
            language=contract.language,
            timezone=contract.timezone,
            avatar_ref=contract.avatar_ref,
            schema_version=contract.schema_version,
            preferences=preferences,
        )

    def update_profile(
        self,
        settings: Dict[str, object],
        user_id: Optional[str] = None,
        *,
        actor: SubjectRef | str | None = None,
    ) -> UserProfile:
        uid = user_id or self.current_user_id()
        actor_ref = self._actor(actor)
        subject = self._subject(uid)
        self._check(actor_ref, "profile.write.self", subject)
        current = self.get_profile(uid).settings
        current.update(settings)
        contract = self._contract_profile(uid, current)
        self.ctx.kv.set(self._kv_key(uid), dict(current))
        self.ctx.kv.set(self._profile_key(uid), contract.to_dict())
        self.access.put_user(subject, actor=actor_ref)
        self.access.put_profile(contract, actor=actor_ref)
        try:
            emit(
                self.ctx.bus,
                "user.profile.changed",
                {"user_id": uid, "settings": dict(current)},
                "user.profile",
            )
        except Exception:
            pass
        return self.get_profile(uid)

    def get_preferences(self, user_id: Optional[str] = None) -> Dict[str, object]:
        uid = user_id or self.current_user_id()
        raw = self.ctx.kv.get(self._preferences_key(uid), {}) or {}
        if not isinstance(raw, dict):
            return {}
        result: Dict[str, object] = {}
        for key, value in raw.items():
            token = str(key or "").strip()
            if not token:
                continue
            if isinstance(value, Mapping) and "value" in value:
                result[token] = value.get("value")
            else:
                result[token] = value
        return result

    def update_preferences(
        self,
        patch: Mapping[str, object],
        user_id: Optional[str] = None,
        *,
        actor: SubjectRef | str | None = None,
        device_override: bool = False,
    ) -> Dict[str, object]:
        uid = user_id or self.current_user_id()
        actor_ref = self._actor(actor)
        subject = self._subject(uid)
        self._check(actor_ref, "preferences.write.self", subject)
        current = self.get_preferences(uid)
        current.update(dict(patch))
        records: dict[str, dict[str, object]] = {}
        for key, value in current.items():
            preference = Preference(
                subject=subject,
                key=str(key),
                value=value,
                scope=self._scope(uid),
                device_override=device_override,
            )
            records[str(key)] = preference.to_dict()
            if str(key) in patch:
                self.access.put_preference(preference, actor=actor_ref)
        self.ctx.kv.set(self._preferences_key(uid), records)
        try:
            emit(
                self.ctx.bus,
                "user.preferences.changed",
                {"user_id": uid, "keys": sorted(str(key) for key in patch.keys())},
                "user.profile",
            )
        except Exception:
            pass
        return self.get_preferences(uid)

    def header_settings(self, user_id: Optional[str] = None) -> dict[str, object]:
        uid = user_id or self.current_user_id()
        profile = self.get_profile(uid)
        preferences = self.get_preferences(uid)
        settings = dict(profile.settings)
        role_value = "owner" if uid == self.current_user_id() else None
        return {
            "user_id": uid,
            "display_name": profile.display_name or settings.get("display_name") or uid,
            "preferred_name": profile.preferred_name or settings.get("preferred_name"),
            "locale": profile.locale or preferences.get("locale") or settings.get("locale"),
            "language": profile.language or preferences.get("language") or settings.get("language"),
            "timezone": profile.timezone or preferences.get("timezone") or settings.get("timezone"),
            "theme": preferences.get("theme") or settings.get("theme") or "system",
            "memory_privacy": preferences.get("memory_privacy") or settings.get("memory_privacy") or "default",
            "current_subnet": getattr(self.ctx.settings, "subnet_id", None),
            "current_workspace": preferences.get("current_workspace") or settings.get("current_workspace"),
            "role_status": {"value": role_value, "editable": False},
            "device_trust_status": preferences.get("device_trust_status") or settings.get("device_trust_status"),
        }


__all__ = ["UserProfile", "UserProfileService"]

