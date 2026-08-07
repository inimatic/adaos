"""Provider-neutral contracts for isolated relational-storage capabilities."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Mapping

from .ownership import OwnershipIsolationError, validate_owner_ref


_LOGICAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
_LOCATOR_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[a-z0-9_./-]+$")
_SECRET_REF_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[a-z0-9_./-]+$")
_ALLOWED_DURABILITY = frozenset({"durable", "ephemeral"})
_ALLOWED_LOCALITY = frozenset({"node", "network", "any"})
_ALLOWED_ISOLATION = frozenset({"file", "database", "schema"})
_ALLOWED_TRANSACTION_LEVELS = frozenset({"atomic", "serializable"})
_ALLOWED_RETENTION = frozenset({"retain", "delete_on_uninstall", "ttl"})
_ALLOWED_ROLLBACK = frozenset({"transaction", "restore", "none"})


class RelationalStorageContractError(ValueError):
    """Raised when a storage requirement or binding violates the ABI."""


class RelationalStorageCapabilityError(RuntimeError):
    """Raised when no registered provider can satisfy a requirement."""


class RelationalStorageIsolationError(OwnershipIsolationError):
    """Raised when a caller tries to use another owner's binding."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_token(value: Any, field_name: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise RelationalStorageContractError(f"{field_name} must be non-empty")
    return token

def validate_logical_name(value: Any) -> str:
    token = _required_token(value, "logical_name").lower()
    if not _LOGICAL_NAME_RE.fullmatch(token):
        raise RelationalStorageContractError(
            "logical_name must start with a lowercase letter and contain only "
            "lowercase letters, digits, '.', '_' or '-' (max 63 characters)"
        )
    return token


@dataclass(frozen=True, slots=True)
class RelationalStorageRequirements:
    """Requirements used by the broker to select a relational provider."""

    SCHEMA: ClassVar[str] = "adaos.storage.relational.requirement.v1"

    durability: str = "durable"
    transactions_required: bool = True
    concurrent_writers: int = 1
    json_required: bool = False
    backup_required: bool = False
    restore_required: bool = False
    locality: str = "node"
    transaction_level: str = "atomic"
    capacity_bytes: int | None = None
    retention_policy: str = "retain"
    retention_days: int | None = None
    rollback_policy: str = "transaction"
    roles_required: bool = False
    migration_owner: str | None = None
    preferred_providers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        durability = str(self.durability or "").strip().lower()
        locality = str(self.locality or "").strip().lower()
        transaction_level = str(self.transaction_level or "").strip().lower()
        retention_policy = str(self.retention_policy or "").strip().lower()
        rollback_policy = str(self.rollback_policy or "").strip().lower()
        if durability not in _ALLOWED_DURABILITY:
            raise RelationalStorageContractError(
                f"durability must be one of {sorted(_ALLOWED_DURABILITY)}"
            )
        if locality not in _ALLOWED_LOCALITY:
            raise RelationalStorageContractError(
                f"locality must be one of {sorted(_ALLOWED_LOCALITY)}"
            )
        if transaction_level not in _ALLOWED_TRANSACTION_LEVELS:
            raise RelationalStorageContractError(
                f"transaction_level must be one of {sorted(_ALLOWED_TRANSACTION_LEVELS)}"
            )
        if retention_policy not in _ALLOWED_RETENTION:
            raise RelationalStorageContractError(
                f"retention_policy must be one of {sorted(_ALLOWED_RETENTION)}"
            )
        if rollback_policy not in _ALLOWED_ROLLBACK:
            raise RelationalStorageContractError(
                f"rollback_policy must be one of {sorted(_ALLOWED_ROLLBACK)}"
            )
        capacity_bytes = None if self.capacity_bytes is None else int(self.capacity_bytes)
        if capacity_bytes is not None and capacity_bytes < 1:
            raise RelationalStorageContractError("capacity_bytes must be >= 1")
        retention_days = None if self.retention_days is None else int(self.retention_days)
        if retention_policy == "ttl" and (retention_days is None or retention_days < 1):
            raise RelationalStorageContractError("ttl retention requires retention_days >= 1")
        if retention_policy != "ttl" and retention_days is not None:
            raise RelationalStorageContractError("retention_days is valid only for ttl retention")
        try:
            writers = int(self.concurrent_writers)
        except (TypeError, ValueError) as exc:
            raise RelationalStorageContractError("concurrent_writers must be an integer") from exc
        if writers < 1:
            raise RelationalStorageContractError("concurrent_writers must be >= 1")
        migration_owner = self.migration_owner
        if migration_owner is not None:
            migration_owner = validate_owner_ref(migration_owner)
        preferred = tuple(
            dict.fromkeys(
                _required_token(item, "preferred_providers item").lower()
                for item in self.preferred_providers
            )
        )
        object.__setattr__(self, "durability", durability)
        object.__setattr__(self, "locality", locality)
        object.__setattr__(self, "transaction_level", transaction_level)
        object.__setattr__(self, "capacity_bytes", capacity_bytes)
        object.__setattr__(self, "retention_policy", retention_policy)
        object.__setattr__(self, "retention_days", retention_days)
        object.__setattr__(self, "rollback_policy", rollback_policy)
        object.__setattr__(self, "concurrent_writers", writers)
        object.__setattr__(self, "migration_owner", migration_owner)
        object.__setattr__(self, "preferred_providers", preferred)

    def for_owner(self, owner_ref: str) -> "RelationalStorageRequirements":
        owner = validate_owner_ref(owner_ref)
        if self.migration_owner not in (None, owner):
            raise RelationalStorageIsolationError(
                "a private relational binding cannot assign migrations to another owner"
            )
        if self.migration_owner == owner:
            return self
        return RelationalStorageRequirements(
            durability=self.durability,
            transactions_required=self.transactions_required,
            concurrent_writers=self.concurrent_writers,
            json_required=self.json_required,
            backup_required=self.backup_required,
            restore_required=self.restore_required,
            locality=self.locality,
            transaction_level=self.transaction_level,
            capacity_bytes=self.capacity_bytes,
            retention_policy=self.retention_policy,
            retention_days=self.retention_days,
            rollback_policy=self.rollback_policy,
            roles_required=self.roles_required,
            migration_owner=owner,
            preferred_providers=self.preferred_providers,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "durability": self.durability,
            "transactions_required": self.transactions_required,
            "concurrent_writers": self.concurrent_writers,
            "json_required": self.json_required,
            "backup_required": self.backup_required,
            "restore_required": self.restore_required,
            "locality": self.locality,
            "transaction_level": self.transaction_level,
            "capacity_bytes": self.capacity_bytes,
            "retention_policy": self.retention_policy,
            "retention_days": self.retention_days,
            "rollback_policy": self.rollback_policy,
            "roles_required": self.roles_required,
            "migration_owner": self.migration_owner,
            "preferred_providers": list(self.preferred_providers),
        }


@dataclass(frozen=True, slots=True)
class RelationalProviderCapabilities:
    """Machine-readable capability profile advertised to the storage broker."""

    provider_id: str
    durability: tuple[str, ...]
    transactions: bool
    max_concurrent_writers: int | None
    json: bool
    backup_restore: bool
    localities: tuple[str, ...]
    isolation: str
    transaction_levels: tuple[str, ...] = ("atomic",)
    capacity_enforcement: bool = False
    retention_policies: tuple[str, ...] = ("retain",)
    migration_rollback: tuple[str, ...] = ("transaction",)
    owner_roles: bool = False
    credential_rotation: bool = False
    protocol_version: str = "1.0"

    def __post_init__(self) -> None:
        provider_id = _required_token(self.provider_id, "provider_id").lower()
        durability = tuple(dict.fromkeys(str(item).strip().lower() for item in self.durability))
        localities = tuple(dict.fromkeys(str(item).strip().lower() for item in self.localities))
        isolation = str(self.isolation or "").strip().lower()
        transaction_levels = tuple(
            dict.fromkeys(str(item).strip().lower() for item in self.transaction_levels)
        )
        retention_policies = tuple(
            dict.fromkeys(str(item).strip().lower() for item in self.retention_policies)
        )
        migration_rollback = tuple(
            dict.fromkeys(str(item).strip().lower() for item in self.migration_rollback)
        )
        protocol_version = str(self.protocol_version or "").strip()
        if not durability or any(item not in _ALLOWED_DURABILITY for item in durability):
            raise RelationalStorageContractError("provider durability profile is invalid")
        if not localities or any(item not in _ALLOWED_LOCALITY for item in localities):
            raise RelationalStorageContractError("provider locality profile is invalid")
        if isolation not in _ALLOWED_ISOLATION:
            raise RelationalStorageContractError("provider isolation mode is invalid")
        if not transaction_levels or any(item not in _ALLOWED_TRANSACTION_LEVELS for item in transaction_levels):
            raise RelationalStorageContractError("provider transaction levels are invalid")
        if not retention_policies or any(item not in _ALLOWED_RETENTION for item in retention_policies):
            raise RelationalStorageContractError("provider retention policies are invalid")
        if not migration_rollback or any(item not in _ALLOWED_ROLLBACK for item in migration_rollback):
            raise RelationalStorageContractError("provider rollback policies are invalid")
        if protocol_version != "1.0":
            raise RelationalStorageContractError("unsupported relational provider protocol version")
        if self.max_concurrent_writers is not None and int(self.max_concurrent_writers) < 1:
            raise RelationalStorageContractError("max_concurrent_writers must be >= 1 or null")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "durability", durability)
        object.__setattr__(self, "localities", localities)
        object.__setattr__(self, "isolation", isolation)
        object.__setattr__(self, "transaction_levels", transaction_levels)
        object.__setattr__(self, "retention_policies", retention_policies)
        object.__setattr__(self, "migration_rollback", migration_rollback)
        object.__setattr__(self, "protocol_version", protocol_version)

    @property
    def features(self) -> tuple[str, ...]:
        values = ["transactions" if self.transactions else ""]
        values.extend(f"durability:{item}" for item in self.durability)
        values.extend(f"locality:{item}" for item in self.localities)
        values.append(f"isolation:{self.isolation}")
        if self.json:
            values.append("json")
        if self.backup_restore:
            values.append("backup_restore")
        values.extend(f"transaction_level:{item}" for item in self.transaction_levels)
        values.extend(f"retention:{item}" for item in self.retention_policies)
        values.extend(f"rollback:{item}" for item in self.migration_rollback)
        if self.capacity_enforcement:
            values.append("capacity_enforcement")
        if self.owner_roles:
            values.append("owner_roles")
        if self.credential_rotation:
            values.append("credential_rotation")
        return tuple(item for item in values if item)

    def rejection_reasons(self, requirements: RelationalStorageRequirements) -> tuple[str, ...]:
        reasons: list[str] = []
        if requirements.durability not in self.durability:
            reasons.append(f"durability:{requirements.durability}")
        if requirements.transactions_required and not self.transactions:
            reasons.append("transactions")
        if (
            self.max_concurrent_writers is not None
            and requirements.concurrent_writers > self.max_concurrent_writers
        ):
            reasons.append(f"concurrent_writers:{requirements.concurrent_writers}")
        if requirements.json_required and not self.json:
            reasons.append("json")
        if requirements.backup_required and not self.backup_restore:
            reasons.append("backup_restore")
        if requirements.restore_required and not self.backup_restore:
            reasons.append("restore")
        if requirements.transaction_level not in self.transaction_levels:
            reasons.append(f"transaction_level:{requirements.transaction_level}")
        if requirements.capacity_bytes is not None and not self.capacity_enforcement:
            reasons.append(f"capacity_bytes:{requirements.capacity_bytes}")
        if requirements.retention_policy not in self.retention_policies:
            reasons.append(f"retention:{requirements.retention_policy}")
        if requirements.rollback_policy not in self.migration_rollback:
            reasons.append(f"rollback:{requirements.rollback_policy}")
        if requirements.roles_required and not self.owner_roles:
            reasons.append("owner_roles")
        if requirements.locality != "any" and requirements.locality not in self.localities:
            reasons.append(f"locality:{requirements.locality}")
        return tuple(reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "durability": list(self.durability),
            "transactions": self.transactions,
            "max_concurrent_writers": self.max_concurrent_writers,
            "json": self.json,
            "backup_restore": self.backup_restore,
            "transaction_levels": list(self.transaction_levels),
            "capacity_enforcement": self.capacity_enforcement,
            "retention_policies": list(self.retention_policies),
            "migration_rollback": list(self.migration_rollback),
            "owner_roles": self.owner_roles,
            "credential_rotation": self.credential_rotation,
            "localities": list(self.localities),
            "isolation": self.isolation,
            "protocol_version": self.protocol_version,
            "features": list(self.features),
        }


@dataclass(frozen=True, slots=True)
class RelationalStorageBinding:
    """Redacted, owner-scoped result of relational capability resolution."""

    SCHEMA: ClassVar[str] = "adaos.storage.relational.binding.v1"

    binding_id: str
    provider_id: str
    owner_ref: str
    logical_name: str
    isolation: str
    locator: str
    migration_owner: str
    protocol_version: str = "1.0"
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    secret_ref: str | None = None
    requirements: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        binding_id = _required_token(self.binding_id, "binding_id")
        provider_id = _required_token(self.provider_id, "provider_id").lower()
        owner_ref = validate_owner_ref(self.owner_ref)
        logical_name = validate_logical_name(self.logical_name)
        isolation = str(self.isolation or "").strip().lower()
        locator = _required_token(self.locator, "locator")
        migration_owner = validate_owner_ref(self.migration_owner)
        protocol_version = str(self.protocol_version or "").strip()
        if isolation not in _ALLOWED_ISOLATION:
            raise RelationalStorageContractError("binding isolation mode is invalid")
        if migration_owner != owner_ref:
            raise RelationalStorageIsolationError(
                "private binding migration_owner must equal owner_ref"
            )
        if protocol_version != "1.0":
            raise RelationalStorageContractError("unsupported relational binding protocol version")
        if (
            any(token in locator.lower() for token in ("password=", "://", "@"))
            or not _LOCATOR_RE.fullmatch(locator)
        ):
            raise RelationalStorageContractError(
                "binding locator must be an opaque AdaOS locator, not a DSN or URL"
            )
        secret_ref = str(self.secret_ref or "").strip() or None
        if secret_ref is not None and (
            any(
                token in secret_ref.lower()
                for token in ("password=", "token=", "://", "@")
            )
            or not _SECRET_REF_RE.fullmatch(secret_ref)
        ):
            raise RelationalStorageContractError(
                "secret_ref must be an opaque reference, not inline credentials"
            )
        raw_requirements = dict(self.requirements)
        raw_requirements.pop("schema", None)
        negotiated = RelationalStorageRequirements(**raw_requirements).for_owner(owner_ref)
        if negotiated.migration_owner != migration_owner:
            raise RelationalStorageIsolationError(
                "binding requirements migration_owner must equal binding migration_owner"
            )
        object.__setattr__(self, "binding_id", binding_id)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "owner_ref", owner_ref)
        object.__setattr__(self, "logical_name", logical_name)
        object.__setattr__(self, "isolation", isolation)
        object.__setattr__(self, "locator", locator)
        object.__setattr__(self, "migration_owner", migration_owner)
        object.__setattr__(self, "protocol_version", protocol_version)
        object.__setattr__(self, "capabilities", dict(self.capabilities))
        object.__setattr__(self, "secret_ref", secret_ref)
        object.__setattr__(self, "requirements", negotiated.to_dict())

    def assert_owner(self, owner_ref: str) -> None:
        owner = validate_owner_ref(owner_ref)
        if owner != self.owner_ref:
            raise RelationalStorageIsolationError(
                f"binding {self.binding_id!r} belongs to {self.owner_ref!r}, not {owner!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "capability": "storage.relational",
            "binding_id": self.binding_id,
            "provider_id": self.provider_id,
            "owner_ref": self.owner_ref,
            "logical_name": self.logical_name,
            "isolation": self.isolation,
            "locator": self.locator,
            "migration_owner": self.migration_owner,
            "protocol_version": self.protocol_version,
            "capabilities": dict(self.capabilities),
            "secret_ref": self.secret_ref,
            "requirements": dict(self.requirements),
        }


@dataclass(frozen=True, slots=True)
class RelationalMigration:
    """One owner-supplied, immutable and checksum-pinned migration."""

    version: int
    name: str
    statements: tuple[str, ...]
    idempotent: bool = False
    dialects: tuple[str, ...] = ("sqlite", "postgresql")

    def __post_init__(self) -> None:
        version = int(self.version)
        if version < 1:
            raise RelationalStorageContractError("migration version must be >= 1")
        name = _required_token(self.name, "migration name")
        statements = tuple(_required_token(item, "migration statement") for item in self.statements)
        if not statements:
            raise RelationalStorageContractError("migration must declare at least one statement")
        dialects = tuple(dict.fromkeys(str(item).strip().lower() for item in self.dialects))
        if not dialects or any(item not in {"sqlite", "postgresql"} for item in dialects):
            raise RelationalStorageContractError("migration dialects are invalid")
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "statements", statements)
        object.__setattr__(self, "dialects", dialects)

    @property
    def checksum(self) -> str:
        encoded = json.dumps(
            {
                "version": self.version,
                "name": self.name,
                "statements": self.statements,
                "idempotent": self.idempotent,
                "dialects": self.dialects,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class RelationalMigrationResult:
    binding_id: str
    owner_ref: str
    applied_versions: tuple[int, ...]
    current_version: int
    staged: bool


@dataclass(frozen=True, slots=True)
class RelationalBackup:
    backup_id: str
    binding_id: str
    owner_ref: str
    provider_id: str
    locator: str
    digest: str
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "backup_id", _required_token(self.backup_id, "backup_id"))
        object.__setattr__(self, "binding_id", _required_token(self.binding_id, "binding_id"))
        object.__setattr__(self, "owner_ref", validate_owner_ref(self.owner_ref))
        object.__setattr__(self, "provider_id", _required_token(self.provider_id, "provider_id"))
        locator = _required_token(self.locator, "locator")
        if "://" in locator or not _LOCATOR_RE.fullmatch(locator):
            raise RelationalStorageContractError("backup locator must be opaque")
        digest = str(self.digest or "").strip().lower()
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise RelationalStorageContractError("backup digest must be sha256")
        object.__setattr__(self, "locator", locator)
        object.__setattr__(self, "digest", digest)


__all__ = [
    "RelationalBackup",
    "RelationalMigration",
    "RelationalMigrationResult",
    "RelationalProviderCapabilities",
    "RelationalStorageBinding",
    "RelationalStorageCapabilityError",
    "RelationalStorageContractError",
    "RelationalStorageIsolationError",
    "RelationalStorageRequirements",
    "validate_logical_name",
    "validate_owner_ref",
]
