from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import re
import time
from typing import Any, Literal, Mapping


PERSONALIZATION_ACCESS_CONTRACT_VERSION = "adaos.personalization_access.contract.v0"

ScopeKind = Literal[
    "subnet",
    "workspace",
    "webspace",
    "scenario",
    "skill",
    "device_session",
    "user_private",
    "shared_workspace",
]
SubjectKind = Literal["user", "device", "session", "service", "skill", "agent", "external", "anonymous"]
RolePreset = Literal["owner", "co_owner", "admin", "member", "child", "guest"]
GrantStatus = Literal["active", "pending", "expired", "revoked"]
InviteKind = Literal["guest_join_link", "targeted_invite_link", "device_pairing_link", "admin_recovery_link"]
InviteStatus = Literal["pending", "accepted", "expired", "revoked"]
DeviceTrustLevel = Literal["trusted", "limited", "guest"]
KeyStatus = Literal["active", "rotating", "revoked", "lost"]
DataZone = Literal["shared_workspace", "user_private", "admin_visible_metadata", "encrypted_private"]
PolicyDecisionValue = Literal["allow", "deny"]

ALLOWED_SCOPE_KINDS: frozenset[str] = frozenset(
    ("subnet", "workspace", "webspace", "scenario", "skill", "device_session", "user_private", "shared_workspace")
)
ALLOWED_SUBJECT_KINDS: frozenset[str] = frozenset(
    ("user", "device", "session", "service", "skill", "agent", "external", "anonymous")
)
ALLOWED_ROLE_PRESETS: frozenset[str] = frozenset(("owner", "co_owner", "admin", "member", "child", "guest"))
ALLOWED_GRANT_STATUSES: frozenset[str] = frozenset(("active", "pending", "expired", "revoked"))
ALLOWED_INVITE_KINDS: frozenset[str] = frozenset(
    ("guest_join_link", "targeted_invite_link", "device_pairing_link", "admin_recovery_link")
)
ALLOWED_INVITE_STATUSES: frozenset[str] = frozenset(("pending", "accepted", "expired", "revoked"))
ALLOWED_KEY_STATUSES: frozenset[str] = frozenset(("active", "rotating", "revoked", "lost"))
ALLOWED_DEVICE_TRUST_LEVELS: frozenset[str] = frozenset(("trusted", "limited", "guest"))
ALLOWED_DATA_ZONES: frozenset[str] = frozenset(
    ("shared_workspace", "user_private", "admin_visible_metadata", "encrypted_private")
)
ALLOWED_POLICY_DECISIONS: frozenset[str] = frozenset(("allow", "deny"))

SCOPE_LATTICE: tuple[dict[str, str | None], ...] = (
    {"kind": "subnet", "parent": None},
    {"kind": "workspace", "parent": "subnet"},
    {"kind": "webspace", "parent": "workspace"},
    {"kind": "scenario", "parent": "workspace"},
    {"kind": "skill", "parent": "workspace"},
    {"kind": "shared_workspace", "parent": "workspace"},
    {"kind": "user_private", "parent": "subnet"},
    {"kind": "device_session", "parent": "subnet"},
)

CAPABILITY_VOCABULARY: tuple[str, ...] = (
    "audit.read",
    "devices.add.any",
    "devices.add.self",
    "devices.revoke.any",
    "devices.revoke.self",
    "memberships.grant",
    "memory.read.self",
    "memory.read.subject",
    "memory.write.self",
    "memory.write.skill_user",
    "preferences.read.self",
    "preferences.write.self",
    "profile.read.members",
    "profile.read.self",
    "profile.write.self",
    "skills.install",
    "skills.invoke.allowed",
    "skills.invoke.child_allowed",
    "skills.invoke.guest_allowed",
    "subnet.admin",
    "tools.invoke.browser_automation",
    "users.invite",
    "users.manage",
    "workspace.read",
    "workspace.write",
)

ROLE_PRESET_CAPABILITIES: Mapping[str, tuple[str, ...]] = {
    "owner": (
        "subnet.admin",
        "users.manage",
        "users.invite",
        "memberships.grant",
        "devices.add.any",
        "devices.revoke.any",
        "skills.install",
        "skills.invoke.allowed",
        "tools.invoke.browser_automation",
        "profile.read.members",
        "profile.read.self",
        "profile.write.self",
        "preferences.read.self",
        "preferences.write.self",
        "memory.read.self",
        "memory.write.self",
        "workspace.read",
        "workspace.write",
        "audit.read",
    ),
    "co_owner": (
        "users.manage",
        "users.invite",
        "memberships.grant",
        "devices.add.any",
        "devices.revoke.any",
        "skills.invoke.allowed",
        "profile.read.members",
        "profile.read.self",
        "profile.write.self",
        "preferences.read.self",
        "preferences.write.self",
        "memory.read.self",
        "memory.write.self",
        "workspace.read",
        "workspace.write",
        "audit.read",
    ),
    "admin": (
        "users.manage",
        "users.invite",
        "memberships.grant",
        "devices.revoke.any",
        "skills.invoke.allowed",
        "profile.read.members",
        "workspace.read",
        "workspace.write",
        "audit.read",
    ),
    "member": (
        "profile.read.self",
        "profile.write.self",
        "preferences.read.self",
        "preferences.write.self",
        "devices.add.self",
        "devices.revoke.self",
        "skills.invoke.allowed",
        "memory.read.self",
        "memory.write.self",
        "workspace.read",
    ),
    "child": (
        "profile.read.self",
        "profile.write.self",
        "preferences.read.self",
        "preferences.write.self",
        "skills.invoke.child_allowed",
        "memory.read.self",
        "memory.write.self",
        "workspace.read",
    ),
    "guest": (
        "skills.invoke.guest_allowed",
        "workspace.read",
    ),
}

JOIN_FLOW_CONTRACTS: Mapping[str, Mapping[str, Any]] = {
    "guest_join_link": {
        "public": True,
        "profile_binding_allowed": False,
        "single_use_default": False,
        "requires_acceptance": True,
    },
    "targeted_invite_link": {
        "public": False,
        "profile_binding_allowed": True,
        "single_use_default": True,
        "requires_acceptance": True,
    },
    "device_pairing_link": {
        "public": False,
        "profile_binding_allowed": True,
        "single_use_default": True,
        "requires_acceptance": True,
    },
    "admin_recovery_link": {
        "public": False,
        "profile_binding_allowed": True,
        "single_use_default": True,
        "requires_acceptance": True,
        "privileged": True,
    },
}

DATA_ZONE_RULES: Mapping[str, Mapping[str, Any]] = {
    "shared_workspace": {"admin_visible": True, "content_visible_to_owner_ui": True},
    "user_private": {"admin_visible": True, "content_visible_to_owner_ui": False},
    "admin_visible_metadata": {"admin_visible": True, "content_visible_to_owner_ui": True},
    "encrypted_private": {"admin_visible": False, "content_visible_to_owner_ui": False, "deferred": True},
}

SECURITY_REGRESSION_MATRIX: tuple[str, ...] = (
    "expired_invite_rejected",
    "reused_targeted_invite_rejected",
    "public_guest_join_cannot_bind_profile",
    "revoked_guest_loses_live_browser_yjs_access",
    "revoked_device_loses_live_browser_yjs_api_access",
    "child_device_pairing_requires_approval_when_policy_requires_it",
    "member_adds_own_device_only_with_devices_add_self",
    "member_cannot_invite_without_users_invite",
    "skill_cannot_read_cross_user_private_memory_without_grant",
    "skill_cannot_write_long_term_memory_without_policy_path",
    "owner_admin_ui_sees_private_metadata_not_content",
    "global_identity_does_not_grant_subnet_access",
    "denied_tool_invocation_records_policy_reason",
)

_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/-]+$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+(\.\*)?$")
_PROFILE_POLICY_KEYS = frozenset(("role", "roles", "membership", "memberships", "grant", "grants"))


class PersonalizationAccessContractError(ValueError):
    """Raised when a personalization/access contract object is malformed."""


def now_ts() -> float:
    return time.time()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _require(value: Any, field_name: str) -> str:
    token = _clean(value)
    if not token:
        raise PersonalizationAccessContractError(f"{field_name} is required")
    return token


def _require_id(value: Any, field_name: str) -> str:
    token = _require(value, field_name)
    if not _ID_RE.match(token):
        raise PersonalizationAccessContractError(f"invalid {field_name}: {token!r}")
    return token


def _require_member(value: Any, allowed: frozenset[str], field_name: str) -> str:
    token = _require(value, field_name)
    if token not in allowed:
        raise PersonalizationAccessContractError(f"invalid {field_name}: {token!r}")
    return token


def _dict(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _as_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _as_jsonable(item) for key, item in asdict(value).items() if item is not None}
    if isinstance(value, Mapping):
        return {str(key): _as_jsonable(item) for key, item in value.items() if item is not None}
    if isinstance(value, tuple):
        return [_as_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_as_jsonable(item) for item in value]
    return value


def validate_capability(capability: str) -> str:
    token = _require(capability, "capability")
    if not _CAPABILITY_RE.match(token):
        raise PersonalizationAccessContractError(f"invalid capability: {token!r}")
    return token


def validate_capabilities(capabilities: Any) -> tuple[str, ...]:
    result = tuple(validate_capability(item) for item in _tuple(capabilities))
    if len(set(result)) != len(result):
        raise PersonalizationAccessContractError("capabilities must be unique")
    return result


def _validate_optional_ts(value: float | None, field_name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if number < 0:
        raise PersonalizationAccessContractError(f"{field_name} must be non-negative")
    return number


@dataclass(frozen=True, slots=True)
class ScopeRef:
    kind: ScopeKind
    id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _require_member(self.kind, ALLOWED_SCOPE_KINDS, "scope.kind"))
        object.__setattr__(self, "id", _require_id(self.id, "scope.id"))

    def ref(self) -> str:
        return f"{self.kind}:{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class SubjectRef:
    kind: SubjectKind
    id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _require_member(self.kind, ALLOWED_SUBJECT_KINDS, "subject.kind"))
        object.__setattr__(self, "id", _require_id(self.id, "subject.id"))

    def ref(self) -> str:
        return f"{self.kind}:{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class GrantConstraint:
    expires_at: float | None = None
    requires_approval_for: tuple[str, ...] = ()
    child_mode: bool = False
    allowed_scopes: tuple[ScopeRef, ...] = ()
    allowed_skill_classes: tuple[str, ...] = ()
    allowed_tool_classes: tuple[str, ...] = ()
    delegation: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "expires_at", _validate_optional_ts(self.expires_at, "constraints.expires_at"))
        object.__setattr__(self, "requires_approval_for", validate_capabilities(self.requires_approval_for))
        object.__setattr__(self, "allowed_scopes", tuple(self.allowed_scopes or ()))
        object.__setattr__(self, "allowed_skill_classes", tuple(_require_id(item, "allowed_skill_class") for item in _tuple(self.allowed_skill_classes)))
        object.__setattr__(self, "allowed_tool_classes", tuple(_require_id(item, "allowed_tool_class") for item in _tuple(self.allowed_tool_classes)))
        object.__setattr__(self, "delegation", validate_capabilities(self.delegation))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class UserProfile:
    user_id: str
    display_name: str | None = None
    preferred_name: str | None = None
    locale: str | None = None
    language: str | None = None
    timezone: str | None = None
    avatar_ref: str | None = None
    settings: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", _require_id(self.user_id, "user_id"))
        settings = _dict(self.settings)
        policy_keys = _PROFILE_POLICY_KEYS.intersection(settings)
        if policy_keys:
            joined = ", ".join(sorted(policy_keys))
            raise PersonalizationAccessContractError(f"profile settings cannot contain access policy keys: {joined}")
        object.__setattr__(self, "settings", settings)
        for field_name in ("display_name", "preferred_name", "locale", "language", "timezone", "avatar_ref"):
            object.__setattr__(self, field_name, _clean(getattr(self, field_name)) or None)

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class UserKey:
    user_id: str
    key_id: str
    public_key_ref: str
    algorithm: str | None = None
    status: KeyStatus = "active"
    created_at: float | None = None
    revoked_at: float | None = None
    external_binding_id: str | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", _require_id(self.user_id, "user_id"))
        object.__setattr__(self, "key_id", _require_id(self.key_id, "key_id"))
        object.__setattr__(self, "public_key_ref", _require(self.public_key_ref, "public_key_ref"))
        object.__setattr__(self, "status", _require_member(self.status, ALLOWED_KEY_STATUSES, "key.status"))
        object.__setattr__(self, "created_at", _validate_optional_ts(self.created_at, "created_at"))
        object.__setattr__(self, "revoked_at", _validate_optional_ts(self.revoked_at, "revoked_at"))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class DeviceKey:
    user_id: str
    device_id: str
    key_id: str
    public_key_ref: str
    trust_level: DeviceTrustLevel = "trusted"
    status: KeyStatus = "active"
    created_at: float | None = None
    last_used_at: float | None = None
    revoked_at: float | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", _require_id(self.user_id, "user_id"))
        object.__setattr__(self, "device_id", _require_id(self.device_id, "device_id"))
        object.__setattr__(self, "key_id", _require_id(self.key_id, "key_id"))
        object.__setattr__(self, "public_key_ref", _require(self.public_key_ref, "public_key_ref"))
        object.__setattr__(self, "trust_level", _require_member(self.trust_level, ALLOWED_DEVICE_TRUST_LEVELS, "device.trust_level"))
        object.__setattr__(self, "status", _require_member(self.status, ALLOWED_KEY_STATUSES, "device.status"))
        object.__setattr__(self, "created_at", _validate_optional_ts(self.created_at, "created_at"))
        object.__setattr__(self, "last_used_at", _validate_optional_ts(self.last_used_at, "last_used_at"))
        object.__setattr__(self, "revoked_at", _validate_optional_ts(self.revoked_at, "revoked_at"))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class SessionKey:
    session_id: str
    key_id: str
    subject: SubjectRef | None = None
    device_id: str | None = None
    status: KeyStatus = "active"
    created_at: float | None = None
    expires_at: float | None = None
    revoked_at: float | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _require_id(self.session_id, "session_id"))
        object.__setattr__(self, "key_id", _require_id(self.key_id, "key_id"))
        object.__setattr__(self, "device_id", _clean(self.device_id) or None)
        object.__setattr__(self, "status", _require_member(self.status, ALLOWED_KEY_STATUSES, "session.status"))
        object.__setattr__(self, "created_at", _validate_optional_ts(self.created_at, "created_at"))
        object.__setattr__(self, "expires_at", _validate_optional_ts(self.expires_at, "expires_at"))
        object.__setattr__(self, "revoked_at", _validate_optional_ts(self.revoked_at, "revoked_at"))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class Preference:
    subject: SubjectRef
    key: str
    value: Any
    scope: ScopeRef | None = None
    device_override: bool = False
    updated_at: float | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _require_id(self.key, "preference.key"))
        object.__setattr__(self, "updated_at", _validate_optional_ts(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class Membership:
    subject: SubjectRef
    scope: ScopeRef
    role: RolePreset
    status: GrantStatus = "active"
    grant_id: str | None = None
    issued_by: SubjectRef | None = None
    created_at: float | None = None
    expires_at: float | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_member(self.role, ALLOWED_ROLE_PRESETS, "membership.role"))
        object.__setattr__(self, "status", _require_member(self.status, ALLOWED_GRANT_STATUSES, "membership.status"))
        object.__setattr__(self, "grant_id", _clean(self.grant_id) or None)
        object.__setattr__(self, "created_at", _validate_optional_ts(self.created_at, "created_at"))
        object.__setattr__(self, "expires_at", _validate_optional_ts(self.expires_at, "expires_at"))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class Grant:
    grant_id: str
    subject: SubjectRef
    scope: ScopeRef
    capabilities: tuple[str, ...] = ()
    role: RolePreset | None = None
    constraints: GrantConstraint = field(default_factory=GrantConstraint)
    status: GrantStatus = "active"
    issued_by: SubjectRef | None = None
    created_at: float | None = None
    revoked_at: float | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "grant_id", _require_id(self.grant_id, "grant_id"))
        object.__setattr__(self, "capabilities", validate_capabilities(self.capabilities))
        role = _clean(self.role) or None
        if role is not None:
            role = _require_member(role, ALLOWED_ROLE_PRESETS, "grant.role")
        if not role and not self.capabilities:
            raise PersonalizationAccessContractError("grant requires role or capabilities")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "status", _require_member(self.status, ALLOWED_GRANT_STATUSES, "grant.status"))
        object.__setattr__(self, "created_at", _validate_optional_ts(self.created_at, "created_at"))
        object.__setattr__(self, "revoked_at", _validate_optional_ts(self.revoked_at, "revoked_at"))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class Invite:
    invite_id: str
    kind: InviteKind
    scope: ScopeRef
    role: RolePreset
    issued_by: SubjectRef
    profile_hint: str | None = None
    status: InviteStatus = "pending"
    expires_at: float | None = None
    single_use: bool = True
    max_sessions: int = 1
    constraints: GrantConstraint = field(default_factory=GrantConstraint)
    created_at: float | None = None
    accepted_by: SubjectRef | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "invite_id", _require_id(self.invite_id, "invite_id"))
        kind = _require_member(self.kind, ALLOWED_INVITE_KINDS, "invite.kind")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "role", _require_member(self.role, ALLOWED_ROLE_PRESETS, "invite.role"))
        object.__setattr__(self, "status", _require_member(self.status, ALLOWED_INVITE_STATUSES, "invite.status"))
        object.__setattr__(self, "profile_hint", _clean(self.profile_hint) or None)
        if kind == "guest_join_link" and self.profile_hint:
            raise PersonalizationAccessContractError("guest_join_link cannot bind or hint a personal profile")
        if kind == "guest_join_link" and self.role != "guest":
            raise PersonalizationAccessContractError("guest_join_link must use guest role")
        if self.max_sessions < 1:
            raise PersonalizationAccessContractError("invite.max_sessions must be positive")
        if self.single_use and self.max_sessions != 1:
            raise PersonalizationAccessContractError("single-use invite must have max_sessions=1")
        object.__setattr__(self, "expires_at", _validate_optional_ts(self.expires_at, "expires_at"))
        object.__setattr__(self, "created_at", _validate_optional_ts(self.created_at, "created_at"))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    recovery_id: str
    subject: SubjectRef
    issued_by: SubjectRef
    status: InviteStatus = "pending"
    replacement_device_id: str | None = None
    revoked_device_ids: tuple[str, ...] = ()
    reason: str | None = None
    created_at: float | None = None
    completed_at: float | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "recovery_id", _require_id(self.recovery_id, "recovery_id"))
        object.__setattr__(self, "status", _require_member(self.status, ALLOWED_INVITE_STATUSES, "recovery.status"))
        object.__setattr__(self, "replacement_device_id", _clean(self.replacement_device_id) or None)
        object.__setattr__(self, "revoked_device_ids", tuple(_require_id(item, "revoked_device_id") for item in _tuple(self.revoked_device_ids)))
        object.__setattr__(self, "reason", _clean(self.reason) or None)
        object.__setattr__(self, "created_at", _validate_optional_ts(self.created_at, "created_at"))
        object.__setattr__(self, "completed_at", _validate_optional_ts(self.completed_at, "completed_at"))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class ExternalIdentityBinding:
    binding_id: str
    subject: SubjectRef
    provider: str
    external_subject_id: str
    external_public_key: str | None = None
    pairwise_public_key: str | None = None
    bound_by: SubjectRef | None = None
    bound_at: float | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", _require_id(self.binding_id, "binding_id"))
        object.__setattr__(self, "provider", _require_id(self.provider, "provider"))
        object.__setattr__(self, "external_subject_id", _require(self.external_subject_id, "external_subject_id"))
        object.__setattr__(self, "external_public_key", _clean(self.external_public_key) or None)
        object.__setattr__(self, "pairwise_public_key", _clean(self.pairwise_public_key) or None)
        object.__setattr__(self, "bound_at", _validate_optional_ts(self.bound_at, "bound_at"))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class ActorContext:
    actor: SubjectRef
    current_user: SubjectRef | None = None
    subject_user: SubjectRef | None = None
    service: SubjectRef | None = None
    on_behalf_of: SubjectRef | None = None
    session: SubjectRef | None = None
    device: SubjectRef | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: PolicyDecisionValue
    actor: SubjectRef
    action: str
    subject: SubjectRef | None = None
    scope: ScopeRef | None = None
    resource: str | None = None
    reason_code: str | None = None
    grant_ids: tuple[str, ...] = ()
    trace_id: str | None = None
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", _require_member(self.decision, ALLOWED_POLICY_DECISIONS, "policy.decision"))
        object.__setattr__(self, "action", validate_capability(self.action))
        object.__setattr__(self, "resource", _clean(self.resource) or None)
        object.__setattr__(self, "reason_code", _clean(self.reason_code) or None)
        object.__setattr__(self, "grant_ids", tuple(_require_id(item, "grant_id") for item in _tuple(self.grant_ids)))
        object.__setattr__(self, "trace_id", _clean(self.trace_id) or None)

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    audit_id: str
    event_type: str
    actor: SubjectRef
    subject: SubjectRef | None = None
    scope: ScopeRef | None = None
    device: SubjectRef | None = None
    session: SubjectRef | None = None
    source: str | None = None
    decision: PolicyDecision | None = None
    redacted_diff: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    ts: float | None = None
    trace_id: str | None = None
    retention: str = "default"
    schema_version: str = PERSONALIZATION_ACCESS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "audit_id", _require_id(self.audit_id, "audit_id"))
        object.__setattr__(self, "event_type", validate_capability(self.event_type))
        object.__setattr__(self, "source", _clean(self.source) or None)
        object.__setattr__(self, "redacted_diff", _dict(self.redacted_diff))
        object.__setattr__(self, "metadata", _dict(self.metadata))
        object.__setattr__(self, "ts", _validate_optional_ts(self.ts, "ts"))
        object.__setattr__(self, "trace_id", _clean(self.trace_id) or None)
        object.__setattr__(self, "retention", _require_id(self.retention, "retention"))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


def personalization_access_contract_snapshot(*, now: float | None = None) -> dict[str, Any]:
    ts = float(now if now is not None else now_ts())
    return {
        "contract": PERSONALIZATION_ACCESS_CONTRACT_VERSION,
        "ts": ts,
        "scope_lattice": list(SCOPE_LATTICE),
        "subject_kinds": sorted(ALLOWED_SUBJECT_KINDS),
        "role_presets": {key: list(value) for key, value in ROLE_PRESET_CAPABILITIES.items()},
        "capabilities": list(CAPABILITY_VOCABULARY),
        "join_flows": {key: dict(value) for key, value in JOIN_FLOW_CONTRACTS.items()},
        "data_zones": {key: dict(value) for key, value in DATA_ZONE_RULES.items()},
        "security_regression_matrix": list(SECURITY_REGRESSION_MATRIX),
        "schemas": [
            "UserProfile",
            "UserKey",
            "DeviceKey",
            "SessionKey",
            "Membership",
            "Grant",
            "GrantConstraint",
            "Preference",
            "Invite",
            "RecoveryAction",
            "ExternalIdentityBinding",
            "ActorContext",
            "PolicyDecision",
            "AuditRecord",
        ],
        "migration_sources": [
            "Settings.owner_id",
            "local-owner",
            "UserProfileService",
            "profile.settings projections",
            "access_links",
            "browser scoped storage",
        ],
    }


__all__ = [
    "ALLOWED_DATA_ZONES",
    "ALLOWED_DEVICE_TRUST_LEVELS",
    "ALLOWED_GRANT_STATUSES",
    "ALLOWED_INVITE_KINDS",
    "ALLOWED_INVITE_STATUSES",
    "ALLOWED_KEY_STATUSES",
    "ALLOWED_POLICY_DECISIONS",
    "ALLOWED_ROLE_PRESETS",
    "ALLOWED_SCOPE_KINDS",
    "ALLOWED_SUBJECT_KINDS",
    "AuditRecord",
    "ActorContext",
    "CAPABILITY_VOCABULARY",
    "DATA_ZONE_RULES",
    "DataZone",
    "DeviceKey",
    "DeviceTrustLevel",
    "ExternalIdentityBinding",
    "Grant",
    "GrantConstraint",
    "GrantStatus",
    "Invite",
    "InviteKind",
    "InviteStatus",
    "JOIN_FLOW_CONTRACTS",
    "KeyStatus",
    "Membership",
    "PERSONALIZATION_ACCESS_CONTRACT_VERSION",
    "PersonalizationAccessContractError",
    "PolicyDecision",
    "PolicyDecisionValue",
    "Preference",
    "RecoveryAction",
    "ROLE_PRESET_CAPABILITIES",
    "RolePreset",
    "SCOPE_LATTICE",
    "SECURITY_REGRESSION_MATRIX",
    "ScopeKind",
    "ScopeRef",
    "SessionKey",
    "SubjectKind",
    "SubjectRef",
    "UserKey",
    "UserProfile",
    "now_ts",
    "personalization_access_contract_snapshot",
    "validate_capabilities",
    "validate_capability",
]
