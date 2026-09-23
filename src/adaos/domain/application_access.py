from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .artifact_release import canonical_payload_digest


PERMISSION_PROFILE_SCHEMA = "adaos.application.permission_profile.v1"
APPLICATION_ACCESS_GRANT_SCHEMA = "adaos.application.access_grant.v1"
APPLICATION_ACCESS_DECISION_SCHEMA = "adaos.application.access_decision.v1"
APPLICATION_VERIFICATION_REPORT_SCHEMA = "adaos.application.verification_report.v1"
APPLICATION_ACCESS_PROFILE_DIFF_SCHEMA = "adaos.application.access_profile_diff.v1"

PLATFORM_ROLE_PRESETS = {"owner", "co_owner", "admin", "member", "child", "guest"}
GRANT_STATUSES = {"active", "revoked", "expired", "suspended"}
CHECK_RESULTS = {"passed", "failed", "inconclusive", "skipped"}
CHECK_GATES = {"hard_gate", "warning", "attestation"}
HIGH_RISK_PERMISSION_PREFIXES = (
    "llm.",
    "model.",
    "network.",
    "secrets.",
    "notifications.",
    "background.",
)
HIGH_RISK_PERMISSION_IDS = {"workspace.write", "external_provider.use"}
STANDARD_ACTOR_CHAIN_FIELDS = (
    "user_ref",
    "subject_ref",
    "application_id",
    "component_ref",
    "tool_ref",
    "agent_ref",
    "service_ref",
    "external_provider_ref",
    "device_ref",
    "session_ref",
    "webspace_id",
    "resource_ref",
    "approval_id",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ApplicationAccessContractError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any, field_name: str, *, maximum: int = 500) -> str:
    token = str(value or "").strip()
    if not token:
        raise ApplicationAccessContractError(f"{field_name} is required")
    if len(token) > maximum:
        raise ApplicationAccessContractError(f"{field_name} exceeds {maximum} characters")
    return token


def _optional_text(value: Any, *, maximum: int = 500) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    if len(token) > maximum:
        raise ApplicationAccessContractError(f"text exceeds {maximum} characters")
    return token


def _identifier(value: Any, field_name: str) -> str:
    token = _text(value, field_name, maximum=128).lower()
    if not _ID_RE.fullmatch(token):
        raise ApplicationAccessContractError(f"{field_name} must be a canonical identifier")
    return token


def _ref(value: Any, field_name: str) -> str:
    token = _text(value, field_name, maximum=256)
    if not _REF_RE.fullmatch(token):
        raise ApplicationAccessContractError(f"{field_name} must be a stable reference")
    return token


def _digest(value: Any, field_name: str) -> str:
    token = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(token):
        raise ApplicationAccessContractError(
            f"{field_name} must be sha256:<64 lowercase hex characters>"
        )
    return token


def _timestamp(value: Any, field_name: str) -> str:
    token = _text(value, field_name, maximum=80)
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApplicationAccessContractError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ApplicationAccessContractError(f"{field_name} must include a timezone")
    return token


def _revision(value: Any, field_name: str = "revision", *, minimum: int = 1) -> int:
    if isinstance(value, bool):
        raise ApplicationAccessContractError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ApplicationAccessContractError(f"{field_name} must be an integer") from exc
    if parsed < minimum:
        raise ApplicationAccessContractError(f"{field_name} must be at least {minimum}")
    return parsed


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ApplicationAccessContractError(f"{field_name} must be an object")
    return dict(value)


def _mapping_tuple(value: Any, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ApplicationAccessContractError(f"{field_name} must be an array")
    result: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ApplicationAccessContractError(f"{field_name} must contain only objects")
        result.append(dict(item))
    return tuple(result)


def _texts(value: Any, field_name: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ApplicationAccessContractError(f"{field_name} must be an array")
    values = [_identifier(item, f"{field_name} item") for item in value]
    if len(values) != len(set(values)):
        raise ApplicationAccessContractError(f"{field_name} must be unique")
    return tuple(sorted(values))


def _refs(value: Any, field_name: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ApplicationAccessContractError(f"{field_name} must be an array")
    values = [_ref(item, f"{field_name} item") for item in value]
    if len(values) != len(set(values)):
        raise ApplicationAccessContractError(f"{field_name} must be unique")
    return tuple(sorted(values))


def _schema_mapping(
    value: Mapping[str, Any],
    *,
    schema: str,
    allowed: set[str],
    required: set[str],
    field_name: str,
) -> dict[str, Any]:
    payload = _mapping(value, field_name)
    if payload.get("schema") != schema:
        raise ApplicationAccessContractError(f"unsupported {field_name} schema")
    unknown = set(payload) - allowed
    missing = required - set(payload)
    if unknown:
        raise ApplicationAccessContractError(
            f"{field_name} contains unsupported fields: {', '.join(sorted(unknown))}"
        )
    if missing:
        raise ApplicationAccessContractError(
            f"{field_name} is missing required fields: {', '.join(sorted(missing))}"
        )
    return payload


def is_high_risk_permission(permission_id: str) -> bool:
    token = str(permission_id or "").strip().lower()
    return token in HIGH_RISK_PERMISSION_IDS or token.startswith(HIGH_RISK_PERMISSION_PREFIXES)


@dataclass(frozen=True, slots=True)
class PermissionDeclaration:
    permission_id: str
    purpose: str
    title: str | None = None
    authorization_details: Mapping[str, Any] = field(default_factory=dict)
    approval_policy: str = "grant_on_install"
    sensitive: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "permission_id", _identifier(self.permission_id, "permission id"))
        object.__setattr__(self, "purpose", _text(self.purpose, "permission purpose", maximum=500))
        object.__setattr__(self, "title", _optional_text(self.title, maximum=160))
        details = _mapping(self.authorization_details, "authorization_details")
        object.__setattr__(self, "authorization_details", details)
        object.__setattr__(self, "approval_policy", _identifier(self.approval_policy, "approval_policy"))
        object.__setattr__(self, "sensitive", bool(self.sensitive) or is_high_risk_permission(self.permission_id))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.permission_id,
            "purpose": self.purpose,
            "approval_policy": self.approval_policy,
        }
        if self.title:
            payload["title"] = self.title
        if self.authorization_details:
            payload["authorization_details"] = dict(self.authorization_details)
        if self.sensitive:
            payload["sensitive"] = True
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, fallback_purpose: str = "") -> "PermissionDeclaration":
        payload = _mapping(value, "permission declaration")
        allowed = {"id", "title", "purpose", "authorization_details", "approval_policy", "sensitive"}
        unknown = set(payload) - allowed
        if unknown:
            raise ApplicationAccessContractError(
                "permission declaration contains unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        return cls(
            permission_id=payload.get("id"),
            title=payload.get("title"),
            purpose=payload.get("purpose") or fallback_purpose or "Application permission",
            authorization_details=payload.get("authorization_details") or {},
            approval_policy=payload.get("approval_policy") or "grant_on_install",
            sensitive=bool(payload.get("sensitive", False)),
        )


def _permission_tuple(value: Any, field_name: str) -> tuple[PermissionDeclaration, ...]:
    declarations = tuple(
        PermissionDeclaration.from_mapping(item, fallback_purpose=field_name)
        for item in _mapping_tuple(value or (), field_name)
    )
    ids = [item.permission_id for item in declarations]
    if len(ids) != len(set(ids)):
        raise ApplicationAccessContractError(f"{field_name} permission ids must be unique")
    return tuple(sorted(declarations, key=lambda item: item.permission_id))


def _secret_declarations(value: Any) -> tuple[Mapping[str, Any], ...]:
    secrets: list[dict[str, Any]] = []
    for raw in _mapping_tuple(value or (), "secrets"):
        allowed = {"id", "provider", "scopes", "purpose", "required", "binding"}
        unknown = set(raw) - allowed
        if unknown:
            raise ApplicationAccessContractError(
                "secret declaration contains unsupported fields: " + ", ".join(sorted(unknown))
            )
        scopes = _refs(raw.get("scopes") or (), "secret.scopes")
        secrets.append(
            {
                "id": _identifier(raw.get("id"), "secret id"),
                "provider": _identifier(raw.get("provider"), "secret provider"),
                "scopes": list(scopes),
                "purpose": _text(raw.get("purpose"), "secret purpose", maximum=500),
                "required": bool(raw.get("required", False)),
                "binding": _identifier(raw.get("binding") or "user_or_app", "secret binding"),
            }
        )
    ids = [item["id"] for item in secrets]
    if len(ids) != len(set(ids)):
        raise ApplicationAccessContractError("secret ids must be unique")
    return tuple(sorted(secrets, key=lambda item: str(item["id"])))


def _data_practices(value: Any) -> Mapping[str, Any]:
    if value in (None, ""):
        value = {}
    payload = _mapping(value, "data_practices")
    allowed = {"collected", "sent_off_device", "linked_to_user", "tracking", "retention"}
    unknown = set(payload) - allowed
    if unknown:
        raise ApplicationAccessContractError(
            "data_practices contains unsupported fields: " + ", ".join(sorted(unknown))
        )
    return {
        "collected": list(_texts(payload.get("collected") or (), "data_practices.collected")),
        "sent_off_device": list(
            _texts(payload.get("sent_off_device") or (), "data_practices.sent_off_device")
        ),
        "linked_to_user": list(
            _texts(payload.get("linked_to_user") or (), "data_practices.linked_to_user")
        ),
        "tracking": bool(payload.get("tracking", False)),
        "retention": _optional_text(payload.get("retention"), maximum=120),
    }


def _declaration_array(value: Any, field_name: str) -> tuple[Mapping[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for raw in _mapping_tuple(value or (), field_name):
        payload = dict(raw)
        if "id" in payload:
            payload["id"] = _identifier(payload["id"], f"{field_name}.id")
        records.append(payload)
    return tuple(sorted(records, key=lambda item: str(item.get("id") or item)))


@dataclass(frozen=True, slots=True)
class ApplicationPermissionProfile:
    required: tuple[PermissionDeclaration, ...] = ()
    optional: tuple[PermissionDeclaration, ...] = ()
    secrets: tuple[Mapping[str, Any], ...] = ()
    data_practices: Mapping[str, Any] = field(default_factory=dict)
    llm_model_use: tuple[Mapping[str, Any], ...] = ()
    notifications: tuple[Mapping[str, Any], ...] = ()
    background_actions: tuple[Mapping[str, Any], ...] = ()
    external_providers: tuple[Mapping[str, Any], ...] = ()
    privacy_labels: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required_ids = {item.permission_id for item in self.required}
        optional_ids = {item.permission_id for item in self.optional}
        if required_ids & optional_ids:
            raise ApplicationAccessContractError("required and optional permissions must not overlap")
        object.__setattr__(self, "secrets", _secret_declarations(self.secrets))
        practices = _data_practices(self.data_practices)
        object.__setattr__(self, "data_practices", practices)
        privacy_labels = dict(self.privacy_labels or {})
        privacy_labels.setdefault("collected", list(practices["collected"]))
        privacy_labels.setdefault("sent_off_device", bool(practices["sent_off_device"]))
        privacy_labels.setdefault("linked_to_user", bool(practices["linked_to_user"]))
        privacy_labels.setdefault("tracking", bool(practices["tracking"]))
        object.__setattr__(self, "privacy_labels", privacy_labels)
        object.__setattr__(self, "llm_model_use", _declaration_array(self.llm_model_use, "llm_model_use"))
        object.__setattr__(self, "notifications", _declaration_array(self.notifications, "notifications"))
        object.__setattr__(self, "background_actions", _declaration_array(self.background_actions, "background_actions"))
        object.__setattr__(self, "external_providers", _declaration_array(self.external_providers, "external_providers"))

    @property
    def flat_permissions(self) -> tuple[str, ...]:
        ids = [item.permission_id for item in self.required + self.optional]
        return tuple(sorted(dict.fromkeys(ids)))

    @property
    def digest(self) -> str:
        return canonical_payload_digest(self.to_dict())

    def declaration_for(self, permission_id: str) -> PermissionDeclaration | None:
        token = str(permission_id or "").strip().lower()
        for item in self.required + self.optional:
            if item.permission_id == token:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": PERMISSION_PROFILE_SCHEMA,
            "required": [item.to_dict() for item in self.required],
            "optional": [item.to_dict() for item in self.optional],
            "secrets": [dict(item) for item in self.secrets],
            "data_practices": dict(self.data_practices),
            "llm_model_use": [dict(item) for item in self.llm_model_use],
            "notifications": [dict(item) for item in self.notifications],
            "background_actions": [dict(item) for item in self.background_actions],
            "external_providers": [dict(item) for item in self.external_providers],
            "privacy_labels": dict(self.privacy_labels),
        }
        return payload

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
        *,
        legacy_permissions: Iterable[Any] = (),
    ) -> "ApplicationPermissionProfile":
        if value is None:
            return cls.from_legacy_permissions(legacy_permissions)
        payload = _schema_mapping(
            value,
            schema=PERMISSION_PROFILE_SCHEMA,
            allowed={
                "schema",
                "required",
                "optional",
                "secrets",
                "data_practices",
                "llm_model_use",
                "notifications",
                "background_actions",
                "external_providers",
                "privacy_labels",
            },
            required={"schema", "required", "optional"},
            field_name="ApplicationPermissionProfile",
        )
        return cls(
            required=_permission_tuple(payload.get("required") or (), "required"),
            optional=_permission_tuple(payload.get("optional") or (), "optional"),
            secrets=tuple(payload.get("secrets") or ()),
            data_practices=payload.get("data_practices") or {},
            llm_model_use=tuple(payload.get("llm_model_use") or ()),
            notifications=tuple(payload.get("notifications") or ()),
            background_actions=tuple(payload.get("background_actions") or ()),
            external_providers=tuple(payload.get("external_providers") or ()),
            privacy_labels=payload.get("privacy_labels") or {},
        )

    @classmethod
    def from_legacy_permissions(cls, values: Iterable[Any]) -> "ApplicationPermissionProfile":
        declarations = tuple(
            PermissionDeclaration(
                permission_id=item,
                purpose="Legacy ProjectRelease permission",
                authorization_details={},
                approval_policy="grant_on_install",
            )
            for item in sorted({_identifier(item, "legacy permission") for item in values})
        )
        return cls(required=declarations)


@dataclass(frozen=True, slots=True)
class ApplicationRoleDeclaration:
    role_id: str
    title: str
    grants: tuple[str, ...]
    assignable_to: tuple[str, ...] = ("owner", "co_owner", "admin", "member")
    default_for: Mapping[str, str] = field(default_factory=dict)
    requires_permissions: tuple[str, ...] = ()
    sensitive: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _identifier(self.role_id, "role_id"))
        object.__setattr__(self, "title", _text(self.title, "role title", maximum=160))
        grants = _texts(self.grants, "role grants")
        if not grants:
            raise ApplicationAccessContractError("Application role requires at least one grant")
        object.__setattr__(self, "grants", grants)
        assignable = _texts(self.assignable_to, "assignable_to")
        unsupported = set(assignable) - PLATFORM_ROLE_PRESETS
        if unsupported:
            raise ApplicationAccessContractError(
                "assignable_to contains unsupported platform roles: " + ", ".join(sorted(unsupported))
            )
        object.__setattr__(self, "assignable_to", assignable)
        defaults: dict[str, str] = {}
        for key, value in _mapping(self.default_for or {}, "default_for").items():
            platform_role = _identifier(key, "default_for platform role")
            if platform_role not in PLATFORM_ROLE_PRESETS:
                raise ApplicationAccessContractError("default_for contains unsupported platform role")
            defaults[platform_role] = _identifier(value, "default_for role id")
        object.__setattr__(self, "default_for", defaults)
        object.__setattr__(self, "requires_permissions", _texts(self.requires_permissions, "requires_permissions"))
        object.__setattr__(self, "sensitive", bool(self.sensitive))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.role_id,
            "title": self.title,
            "grants": list(self.grants),
            "assignable_to": list(self.assignable_to),
            "default_for": dict(self.default_for),
            "requires_permissions": list(self.requires_permissions),
        }
        if self.sensitive:
            payload["sensitive"] = True
        return payload

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        known_permissions: Iterable[str] = (),
    ) -> "ApplicationRoleDeclaration":
        payload = _mapping(value, "application role")
        allowed = {
            "id",
            "title",
            "grants",
            "assignable_to",
            "default_for",
            "requires_permissions",
            "sensitive",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ApplicationAccessContractError(
                "application role contains unsupported fields: " + ", ".join(sorted(unknown))
            )
        role = cls(
            role_id=payload.get("id"),
            title=payload.get("title"),
            grants=tuple(payload.get("grants") or ()),
            assignable_to=tuple(payload.get("assignable_to") or ("owner", "co_owner", "admin", "member")),
            default_for=payload.get("default_for") or {},
            requires_permissions=tuple(payload.get("requires_permissions") or ()),
            sensitive=bool(payload.get("sensitive", False)),
        )
        known = set(known_permissions)
        unknown_permissions = set(role.requires_permissions) - known
        if unknown_permissions:
            raise ApplicationAccessContractError(
                "role requires unknown permissions: " + ", ".join(sorted(unknown_permissions))
            )
        default_role_ids = set(role.default_for.values())
        if default_role_ids - {role.role_id}:
            raise ApplicationAccessContractError("default_for can only point to the declaring role in V1")
        return role


def normalize_application_roles(
    values: Iterable[ApplicationRoleDeclaration | Mapping[str, Any]] | None,
    *,
    known_permissions: Iterable[str] = (),
) -> tuple[ApplicationRoleDeclaration, ...]:
    roles = tuple(
        item
        if isinstance(item, ApplicationRoleDeclaration)
        else ApplicationRoleDeclaration.from_mapping(item, known_permissions=known_permissions)
        for item in (values or ())
    )
    ids = [item.role_id for item in roles]
    if len(ids) != len(set(ids)):
        raise ApplicationAccessContractError("Application role ids must be unique")
    return tuple(sorted(roles, key=lambda item: item.role_id))


def classify_access_profile_diff(
    old_profile: ApplicationPermissionProfile,
    new_profile: ApplicationPermissionProfile,
    *,
    old_roles: Iterable[ApplicationRoleDeclaration] = (),
    new_roles: Iterable[ApplicationRoleDeclaration] = (),
    existing_role_assignments: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    old_permissions = {item.permission_id: item for item in old_profile.required + old_profile.optional}
    new_permissions = {item.permission_id: item for item in new_profile.required + new_profile.optional}
    old_required = {item.permission_id for item in old_profile.required}
    new_required = {item.permission_id for item in new_profile.required}
    added = sorted(set(new_permissions) - set(old_permissions))
    removed = sorted(set(old_permissions) - set(new_permissions))
    unchanged = sorted(
        pid for pid in set(old_permissions) & set(new_permissions)
        if old_permissions[pid].to_dict() == new_permissions[pid].to_dict()
    )
    elevated = sorted(
        pid for pid in set(old_permissions) & set(new_permissions)
        if (
            pid in new_required - old_required
            or old_permissions[pid].to_dict() != new_permissions[pid].to_dict()
            or (new_permissions[pid].sensitive and not old_permissions[pid].sensitive)
        )
    )
    sensitive_added = sorted(pid for pid in added if new_permissions[pid].sensitive)

    old_role_map = {item.role_id: item for item in old_roles}
    new_role_map = {item.role_id: item for item in new_roles}
    role_added = sorted(set(new_role_map) - set(old_role_map))
    role_removed = sorted(set(old_role_map) - set(new_role_map))
    role_unchanged = sorted(
        role_id for role_id in set(old_role_map) & set(new_role_map)
        if old_role_map[role_id].to_dict() == new_role_map[role_id].to_dict()
    )
    role_elevated = sorted(
        role_id for role_id in set(old_role_map) & set(new_role_map)
        if (
            set(new_role_map[role_id].grants) - set(old_role_map[role_id].grants)
            or set(new_role_map[role_id].requires_permissions) - set(old_role_map[role_id].requires_permissions)
            or (new_role_map[role_id].sensitive and not old_role_map[role_id].sensitive)
        )
    )
    assignments = {
        role_id: sorted(str(subject) for subject in subjects)
        for role_id, subjects in (existing_role_assignments or {}).items()
    }
    affected_users = {
        role_id: assignments.get(role_id, [])
        for role_id in sorted(set(role_removed) | set(role_elevated))
        if assignments.get(role_id)
    }
    return {
        "schema": APPLICATION_ACCESS_PROFILE_DIFF_SCHEMA,
        "permission_changes": {
            "unchanged": unchanged,
            "removed": removed,
            "added": added,
            "elevated": elevated,
            "sensitive_added": sensitive_added,
        },
        "role_changes": {
            "unchanged": role_unchanged,
            "removed": role_removed,
            "added": role_added,
            "elevated": role_elevated,
            "sensitive_added": sorted(
                role_id for role_id in role_added if new_role_map[role_id].sensitive
            ),
            "remap_required": sorted(role_id for role_id in role_removed if assignments.get(role_id)),
            "affected_users": affected_users,
        },
    }


@dataclass(frozen=True, slots=True)
class ApplicationAccessGrant:
    grant_id: str
    subject_ref: str
    application_id: str
    application_roles: tuple[str, ...]
    permission_ceiling: tuple[str, ...]
    explicit_denies: tuple[str, ...]
    constraints: Mapping[str, Any]
    issuer_ref: str
    reviewed_permission_profile_digest: str
    status: str = "active"
    expires_at: str | None = None
    revision: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "grant_id", _ref(self.grant_id, "grant_id"))
        object.__setattr__(self, "subject_ref", _ref(self.subject_ref, "subject_ref"))
        object.__setattr__(self, "application_id", _identifier(self.application_id, "application_id"))
        object.__setattr__(self, "application_roles", _texts(self.application_roles, "application_roles"))
        object.__setattr__(self, "permission_ceiling", _texts(self.permission_ceiling, "permission_ceiling"))
        object.__setattr__(self, "explicit_denies", _texts(self.explicit_denies, "explicit_denies"))
        if set(self.permission_ceiling) & set(self.explicit_denies):
            raise ApplicationAccessContractError("permission ceiling and denies must not overlap")
        object.__setattr__(self, "constraints", _mapping(self.constraints or {}, "constraints"))
        object.__setattr__(self, "issuer_ref", _ref(self.issuer_ref, "issuer_ref"))
        object.__setattr__(self, "reviewed_permission_profile_digest", _digest(self.reviewed_permission_profile_digest, "reviewed_permission_profile_digest"))
        if self.status not in GRANT_STATUSES:
            raise ApplicationAccessContractError("ApplicationAccessGrant status is invalid")
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _timestamp(self.expires_at, "expires_at"))
        object.__setattr__(self, "revision", _revision(self.revision))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": APPLICATION_ACCESS_GRANT_SCHEMA,
            "grant_id": self.grant_id,
            "subject_ref": self.subject_ref,
            "application_id": self.application_id,
            "application_roles": list(self.application_roles),
            "permission_ceiling": list(self.permission_ceiling),
            "explicit_denies": list(self.explicit_denies),
            "constraints": dict(self.constraints),
            "issuer_ref": self.issuer_ref,
            "reviewed_permission_profile_digest": self.reviewed_permission_profile_digest,
            "status": self.status,
            "expires_at": self.expires_at,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApplicationAccessGrant":
        payload = _schema_mapping(
            value,
            schema=APPLICATION_ACCESS_GRANT_SCHEMA,
            allowed={
                "schema",
                "grant_id",
                "subject_ref",
                "application_id",
                "application_roles",
                "permission_ceiling",
                "explicit_denies",
                "constraints",
                "issuer_ref",
                "reviewed_permission_profile_digest",
                "status",
                "expires_at",
                "revision",
                "created_at",
                "updated_at",
            },
            required={
                "schema",
                "grant_id",
                "subject_ref",
                "application_id",
                "application_roles",
                "permission_ceiling",
                "explicit_denies",
                "constraints",
                "issuer_ref",
                "reviewed_permission_profile_digest",
                "status",
                "expires_at",
                "revision",
                "created_at",
                "updated_at",
            },
            field_name="ApplicationAccessGrant",
        )
        payload.pop("schema")
        payload["application_roles"] = tuple(payload["application_roles"])
        payload["permission_ceiling"] = tuple(payload["permission_ceiling"])
        payload["explicit_denies"] = tuple(payload["explicit_denies"])
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ApplicationAccessDecision:
    application_id: str
    subject_ref: str
    permission_id: str
    app_capability: str
    decision: str
    reason_code: str
    actor_chain: Mapping[str, Any]
    approval_id: str | None = None
    grant_id: str | None = None
    policy_explanation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_id", _identifier(self.application_id, "application_id"))
        object.__setattr__(self, "subject_ref", _ref(self.subject_ref, "subject_ref"))
        object.__setattr__(self, "permission_id", _identifier(self.permission_id, "permission_id"))
        object.__setattr__(self, "app_capability", _identifier(self.app_capability, "app_capability"))
        if self.decision not in {"allow", "deny", "pending_action"}:
            raise ApplicationAccessContractError("access decision must be allow, deny, or pending_action")
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, "reason_code"))
        object.__setattr__(self, "actor_chain", _mapping(self.actor_chain, "actor_chain"))
        if self.approval_id is not None:
            object.__setattr__(self, "approval_id", _ref(self.approval_id, "approval_id"))
        if self.grant_id is not None:
            object.__setattr__(self, "grant_id", _ref(self.grant_id, "grant_id"))
        object.__setattr__(self, "policy_explanation", _optional_text(self.policy_explanation, maximum=500))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": APPLICATION_ACCESS_DECISION_SCHEMA,
            "application_id": self.application_id,
            "subject_ref": self.subject_ref,
            "permission_id": self.permission_id,
            "app_capability": self.app_capability,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "actor_chain": dict(self.actor_chain),
        }
        if self.approval_id:
            payload["approval_id"] = self.approval_id
        if self.grant_id:
            payload["grant_id"] = self.grant_id
        if self.policy_explanation:
            payload["policy_explanation"] = self.policy_explanation
        return payload


def _subject_kind(grant: ApplicationAccessGrant) -> str:
    explicit = str(grant.constraints.get("subject_kind") or "").strip().lower()
    if explicit:
        return explicit
    if grant.subject_ref.startswith("session:guest") or ":guest" in grant.subject_ref:
        return "guest"
    if grant.subject_ref.startswith("child:") or ":child" in grant.subject_ref:
        return "child"
    return "user"


def _standard_actor_chain(
    value: Mapping[str, Any],
    *,
    application_id: str,
    subject_ref: str,
    approval_id: str | None,
) -> dict[str, Any]:
    chain = dict(value or {})
    aliases = {
        "user": "user_ref",
        "component": "component_ref",
        "tool": "tool_ref",
        "agent": "agent_ref",
        "service": "service_ref",
        "external_provider": "external_provider_ref",
        "device": "device_ref",
        "session": "session_ref",
        "webspace": "webspace_id",
        "resource": "resource_ref",
    }
    for old, new in aliases.items():
        if new not in chain and old in chain:
            chain[new] = chain[old]
    if application_id:
        chain["application_id"] = application_id
    if subject_ref:
        chain["subject_ref"] = subject_ref
    if approval_id:
        chain["approval_id"] = approval_id
    for field_name in STANDARD_ACTOR_CHAIN_FIELDS:
        chain.setdefault(field_name, None)
    return chain


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def evaluate_application_access(
    *,
    profile: ApplicationPermissionProfile,
    roles: Iterable[ApplicationRoleDeclaration],
    grant: ApplicationAccessGrant | None,
    permission_id: str,
    app_capability: str,
    actor_chain: Mapping[str, Any],
    component_capabilities: Iterable[str] = (),
    approval_id: str | None = None,
    now: str | None = None,
) -> ApplicationAccessDecision:
    permission = _identifier(permission_id, "permission_id")
    capability = _identifier(app_capability, "app_capability")
    raw_chain = dict(actor_chain or {})
    application_id = str(raw_chain.get("application_id") or (grant.application_id if grant else "")).strip()
    subject_ref = str(raw_chain.get("subject_ref") or (grant.subject_ref if grant else "")).strip()
    chain = _standard_actor_chain(
        raw_chain,
        application_id=application_id,
        subject_ref=subject_ref,
        approval_id=approval_id,
    )

    def decision(result: str, reason: str, explanation: str) -> ApplicationAccessDecision:
        return ApplicationAccessDecision(
            application_id=application_id or "unknown_application",
            subject_ref=subject_ref or "unknown:subject",
            permission_id=permission,
            app_capability=capability,
            decision=result,
            reason_code=reason,
            actor_chain=chain,
            approval_id=approval_id,
            grant_id=grant.grant_id if grant else None,
            policy_explanation=explanation,
        )

    declaration = profile.declaration_for(permission)
    if declaration is None:
        return decision("deny", "permission_not_declared", "Application release does not declare this permission.")
    component_set = {_identifier(item, "component capability") for item in component_capabilities}
    if not component_set:
        return decision("deny", "component_capability_unverified", "Component capability declarations were not supplied.")
    if permission not in component_set:
        return decision("deny", "component_capability_missing", "Component manifest did not declare this capability.")
    if grant is None:
        return decision("pending_action", "application_grant_missing", "No Application access grant covers this subject.")
    if grant.reviewed_permission_profile_digest != profile.digest:
        return decision("pending_action", "permission_profile_review_required", "Grant reviewed another permission profile digest.")
    if grant.status != "active":
        return decision("deny", f"grant_{grant.status}", "Application access grant is not active.")
    if grant.expires_at is not None and _parse_time(grant.expires_at) <= _parse_time(now or utc_now()):
        return decision("deny", "grant_expired", "Application access grant has expired.")
    if permission in grant.explicit_denies:
        return decision("deny", "explicit_deny", "Application access grant explicitly denies this permission.")
    if permission not in grant.permission_ceiling:
        return decision("pending_action", "permission_not_granted", "Application access grant does not include this permission.")
    webspace_id = str(grant.constraints.get("webspace_id") or "").strip()
    if webspace_id and str(chain.get("webspace_id") or "").strip() != webspace_id:
        return decision("deny", "webspace_scope_mismatch", "Application grant is scoped to another webspace.")
    resource_scope = grant.constraints.get("resource_scope") or grant.constraints.get("resource_refs") or ()
    if resource_scope:
        allowed_resources = set(_refs(resource_scope, "resource_scope"))
        resource_ref = str(chain.get("resource_ref") or "").strip()
        if resource_ref not in allowed_resources:
            return decision("deny", "resource_scope_mismatch", "Application grant is scoped to another resource.")
    if grant.constraints.get("requires_trusted_device") and not chain.get("device_trusted"):
        return decision("pending_action", "device_trust_required", "Application grant requires a trusted device.")
    if grant.constraints.get("requires_trusted_session") and not chain.get("session_trusted"):
        return decision("pending_action", "session_trust_required", "Application grant requires a trusted session.")
    expected_holder = str(grant.constraints.get("holder_ref") or "").strip()
    actual_holder = str(chain.get("session_ref") or chain.get("device_ref") or "").strip()
    if expected_holder and actual_holder != expected_holder:
        return decision("deny", "holder_scope_mismatch", "Application grant is bound to another holder.")
    expected_session = str(grant.constraints.get("session_ref") or "").strip()
    actual_session = str(chain.get("session_ref") or "").strip()
    if expected_session and actual_session != expected_session:
        return decision("deny", "session_scope_mismatch", "Application grant is bound to another session.")
    expected_device = str(grant.constraints.get("device_ref") or "").strip()
    actual_device = str(chain.get("device_ref") or "").strip()
    if expected_device and actual_device != expected_device:
        return decision("deny", "device_scope_mismatch", "Application grant is bound to another device.")
    if grant.constraints.get("session_bound") and not actual_session:
        return decision("deny", "session_scope_required", "Application grant requires a bound session.")

    role_map = {item.role_id: item for item in roles}
    if role_map:
        selected_roles = [role_map[role_id] for role_id in grant.application_roles if role_id in role_map]
        if not selected_roles:
            return decision("deny", "application_role_missing", "Subject has no valid Application role.")
        if not any(capability in role.grants for role in selected_roles):
            return decision("deny", "application_role_capability_missing", "Application role does not grant this capability.")
        required_permissions = set().union(*(set(role.requires_permissions) for role in selected_roles))
        missing_role_permissions = sorted(required_permissions - set(grant.permission_ceiling))
        if missing_role_permissions:
            return decision("pending_action", "role_required_permission_missing", "Role requires a permission not granted to the subject.")
    elif grant.application_roles:
        return decision("deny", "application_role_undeclared", "Roleless Application release cannot accept Application roles.")

    kind = _subject_kind(grant)
    if kind == "guest" and is_high_risk_permission(permission):
        if not grant.constraints.get("guest_sensitive_override"):
            return decision("deny", "guest_floor_denied", "Guest floor denies sensitive Application access by default.")
    if kind == "child" and is_high_risk_permission(permission):
        if not (approval_id or grant.constraints.get("guardian_approval_id")):
            return decision("pending_action", "guardian_approval_required", "Child access requires guardian approval for this permission.")
    if kind == "child" and (
        profile.privacy_labels.get("sent_off_device") or profile.privacy_labels.get("tracking")
    ):
        if not (approval_id or grant.constraints.get("guardian_approval_id")):
            return decision("pending_action", "guardian_approval_required", "Child access requires guardian approval for external data sharing.")

    return decision(
        "allow",
        "allowed",
        (
            "Application permission, grant, role, component, and subject floors allow the action."
            if role_map
            else "Application permission, grant, component, and subject floors allow the action."
        ),
    )


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    check_id: str
    gate: str
    result: str
    evidence: str | None = None
    message: str | None = None
    actor_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", _identifier(self.check_id, "check_id"))
        if self.gate not in CHECK_GATES:
            raise ApplicationAccessContractError("verification gate is invalid")
        if self.result not in CHECK_RESULTS:
            raise ApplicationAccessContractError("verification result is invalid")
        object.__setattr__(self, "evidence", _optional_text(self.evidence, maximum=500))
        object.__setattr__(self, "message", _optional_text(self.message, maximum=500))
        if self.actor_ref is not None:
            object.__setattr__(self, "actor_ref", _ref(self.actor_ref, "actor_ref"))

    def to_dict(self) -> dict[str, Any]:
        payload = {"id": self.check_id, "gate": self.gate, "result": self.result}
        if self.evidence:
            payload["evidence"] = self.evidence
        if self.message:
            payload["message"] = self.message
        if self.actor_ref:
            payload["actor_ref"] = self.actor_ref
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "VerificationCheck":
        payload = _mapping(value, "verification check")
        allowed = {"id", "gate", "result", "evidence", "message", "actor_ref"}
        unknown = set(payload) - allowed
        if unknown:
            raise ApplicationAccessContractError(
                "verification check contains unsupported fields: " + ", ".join(sorted(unknown))
            )
        return cls(
            check_id=payload.get("id"),
            gate=payload.get("gate"),
            result=payload.get("result"),
            evidence=payload.get("evidence"),
            message=payload.get("message"),
            actor_ref=payload.get("actor_ref"),
        )


@dataclass(frozen=True, slots=True)
class ApplicationVerificationReport:
    application_id: str
    release_digest: str
    source_commit: str
    permission_profile_digest: str
    observed_capabilities_digest: str
    checks: tuple[VerificationCheck, ...]
    warnings: tuple[str, ...] = ()
    attestations: tuple[Mapping[str, Any], ...] = ()
    residual_risks: tuple[str, ...] = ()
    release_scope: str = "candidate"
    created_at: str = field(default_factory=utc_now)
    report_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_id", _identifier(self.application_id, "application_id"))
        object.__setattr__(self, "release_digest", _digest(self.release_digest, "release_digest"))
        object.__setattr__(self, "source_commit", _ref(self.source_commit, "source_commit"))
        object.__setattr__(self, "permission_profile_digest", _digest(self.permission_profile_digest, "permission_profile_digest"))
        object.__setattr__(self, "observed_capabilities_digest", _digest(self.observed_capabilities_digest, "observed_capabilities_digest"))
        if not self.checks:
            raise ApplicationAccessContractError("verification report requires checks")
        ids = [item.check_id for item in self.checks]
        if len(ids) != len(set(ids)):
            raise ApplicationAccessContractError("verification check ids must be unique")
        object.__setattr__(self, "checks", tuple(sorted(self.checks, key=lambda item: item.check_id)))
        object.__setattr__(self, "warnings", tuple(str(item) for item in self.warnings if str(item).strip()))
        object.__setattr__(self, "attestations", tuple(dict(item) for item in self.attestations))
        object.__setattr__(self, "residual_risks", tuple(str(item) for item in self.residual_risks if str(item).strip()))
        if self.release_scope not in {"dev", "candidate", "trial", "publication"}:
            raise ApplicationAccessContractError("verification release_scope is invalid")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        if self.report_digest is not None:
            if self.report_digest != self.computed_digest():
                raise ApplicationAccessContractError("report_digest does not match verification report content")

    @property
    def overall(self) -> str:
        if any(item.gate == "hard_gate" and item.result in {"failed", "inconclusive"} for item in self.checks):
            return "failed"
        if any(item.result == "failed" for item in self.checks):
            return "warning"
        if any(item.result == "inconclusive" for item in self.checks):
            return "inconclusive"
        return "passed"

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema": APPLICATION_VERIFICATION_REPORT_SCHEMA,
            "application_id": self.application_id,
            "release_digest": self.release_digest,
            "source_commit": self.source_commit,
            "permission_profile_digest": self.permission_profile_digest,
            "observed_capabilities_digest": self.observed_capabilities_digest,
            "overall": self.overall,
            "checks": [item.to_dict() for item in sorted(self.checks, key=lambda value: value.check_id)],
            "warnings": list(self.warnings),
            "attestations": [dict(item) for item in self.attestations],
            "residual_risks": list(self.residual_risks),
            "release_scope": self.release_scope,
            "created_at": self.created_at,
        }

    def computed_digest(self) -> str:
        return canonical_payload_digest(self.unsigned_dict())

    def seal(self) -> "ApplicationVerificationReport":
        return ApplicationVerificationReport(
            application_id=self.application_id,
            release_digest=self.release_digest,
            source_commit=self.source_commit,
            permission_profile_digest=self.permission_profile_digest,
            observed_capabilities_digest=self.observed_capabilities_digest,
            checks=self.checks,
            warnings=self.warnings,
            attestations=self.attestations,
            residual_risks=self.residual_risks,
            release_scope=self.release_scope,
            created_at=self.created_at,
            report_digest=self.computed_digest(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["report_digest"] = self.report_digest or self.computed_digest()
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApplicationVerificationReport":
        payload = _schema_mapping(
            value,
            schema=APPLICATION_VERIFICATION_REPORT_SCHEMA,
            allowed={
                "schema",
                "application_id",
                "release_digest",
                "source_commit",
                "permission_profile_digest",
                "observed_capabilities_digest",
                "overall",
                "checks",
                "warnings",
                "attestations",
                "residual_risks",
                "release_scope",
                "created_at",
                "report_digest",
            },
            required={
                "schema",
                "application_id",
                "release_digest",
                "source_commit",
                "permission_profile_digest",
                "observed_capabilities_digest",
                "overall",
                "checks",
                "created_at",
                "report_digest",
            },
            field_name="ApplicationVerificationReport",
        )
        checks = tuple(VerificationCheck.from_mapping(item) for item in _mapping_tuple(payload["checks"], "checks"))
        report = cls(
            application_id=payload["application_id"],
            release_digest=payload["release_digest"],
            source_commit=payload["source_commit"],
            permission_profile_digest=payload["permission_profile_digest"],
            observed_capabilities_digest=payload["observed_capabilities_digest"],
            checks=checks,
            warnings=tuple(payload.get("warnings") or ()),
            attestations=_mapping_tuple(payload.get("attestations") or (), "attestations"),
            residual_risks=tuple(payload.get("residual_risks") or ()),
            release_scope=str(payload.get("release_scope") or "candidate"),
            created_at=payload["created_at"],
            report_digest=payload["report_digest"],
        )
        if payload.get("overall") != report.overall:
            raise ApplicationAccessContractError("verification report overall does not match checks")
        return report


def build_application_verification_report(
    *,
    application_id: str,
    release_digest: str,
    source_commit: str,
    profile: ApplicationPermissionProfile,
    roles: Iterable[ApplicationRoleDeclaration],
    observed_capabilities: Iterable[str] = (),
    inferred_capabilities: Iterable[str] = (),
    regression_evidence: Iterable[str] = (),
    access_matrix_evidence: Iterable[str] = (),
    pending_action_fallbacks: Iterable[str] = (),
    audit_evidence: Iterable[str] = (),
    attestations: Iterable[Mapping[str, Any]] = (),
    residual_risks: Iterable[str] = (),
) -> ApplicationVerificationReport:
    declared = set(profile.flat_permissions)
    observed = {_identifier(item, "observed capability") for item in observed_capabilities}
    inferred = {_identifier(item, "inferred capability") for item in inferred_capabilities}
    undeclared = sorted((observed | inferred) - declared)
    undeclared_high = [item for item in undeclared if is_high_risk_permission(item)]
    role_tuple = tuple(roles)
    regression_refs = tuple(str(item).strip() for item in regression_evidence if str(item).strip())
    access_refs = tuple(str(item).strip() for item in access_matrix_evidence if str(item).strip())
    pending_refs = tuple(str(item).strip() for item in pending_action_fallbacks if str(item).strip())
    audit_refs = tuple(str(item).strip() for item in audit_evidence if str(item).strip())
    observed_digest = canonical_payload_digest(sorted(observed | inferred))
    checks = [
        VerificationCheck(
            "permission_profile.schema",
            "hard_gate",
            "passed",
            message="Permission profile normalized and digest computed.",
        ),
        VerificationCheck(
            "permissions.declared_vs_observed",
            "hard_gate",
            "failed" if undeclared_high else "passed",
            message=(
                "Undeclared high-risk capabilities: " + ", ".join(undeclared_high)
                if undeclared_high
                else "Declared, inferred, and observed high-risk capabilities are aligned."
            ),
        ),
        VerificationCheck(
            "roles.declaration",
            "hard_gate",
            "passed" if role_tuple else "skipped",
            message="Application roles normalized." if role_tuple else "Application does not declare roles.",
        ),
        VerificationCheck(
            "regression.changed_behavior",
            "hard_gate",
            "passed" if regression_refs else "inconclusive",
            evidence=regression_refs[0] if regression_refs else None,
            message="Regression evidence recorded." if regression_refs else "Regression evidence is required.",
        ),
        VerificationCheck(
            "access_matrix.owner_member_child_guest",
            "hard_gate",
            "passed" if access_refs else "inconclusive",
            evidence=access_refs[0] if access_refs else None,
            message="Access matrix evidence recorded." if access_refs else "Access matrix evidence is required.",
        ),
        VerificationCheck(
            "secrets.connected_accounts.disclosure",
            "hard_gate",
            "passed",
            message="Secret and connected-account declarations are normalized.",
        ),
        VerificationCheck(
            "pending_action.fallback",
            "hard_gate",
            "passed" if pending_refs else "inconclusive",
            evidence=pending_refs[0] if pending_refs else None,
            message="Pending Action fallback evidence recorded." if pending_refs else "Pending Action fallback evidence is required.",
        ),
        VerificationCheck(
            "auditability.decisions",
            "hard_gate",
            "passed" if audit_refs else "inconclusive",
            evidence=audit_refs[0] if audit_refs else None,
            message="Audit evidence recorded." if audit_refs else "Audit evidence is required.",
        ),
    ]
    warnings = []
    if undeclared and not undeclared_high:
        warnings.append("Undeclared low-risk capabilities observed: " + ", ".join(undeclared))
    report = ApplicationVerificationReport(
        application_id=application_id,
        release_digest=release_digest,
        source_commit=source_commit,
        permission_profile_digest=profile.digest,
        observed_capabilities_digest=observed_digest,
        checks=tuple(checks),
        warnings=tuple(warnings),
        attestations=tuple(attestations),
        residual_risks=tuple(residual_risks),
    )
    return report.seal()


__all__ = [
    "APPLICATION_ACCESS_DECISION_SCHEMA",
    "APPLICATION_ACCESS_GRANT_SCHEMA",
    "APPLICATION_ACCESS_PROFILE_DIFF_SCHEMA",
    "APPLICATION_VERIFICATION_REPORT_SCHEMA",
    "ApplicationAccessContractError",
    "ApplicationAccessDecision",
    "ApplicationAccessGrant",
    "ApplicationPermissionProfile",
    "ApplicationRoleDeclaration",
    "ApplicationVerificationReport",
    "PERMISSION_PROFILE_SCHEMA",
    "PermissionDeclaration",
    "VerificationCheck",
    "build_application_verification_report",
    "classify_access_profile_diff",
    "evaluate_application_access",
    "is_high_risk_permission",
    "normalize_application_roles",
]
