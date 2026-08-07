"""Provider-neutral contracts for isolated relational-storage capabilities."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Mapping

from .ownership import OwnershipIsolationError, validate_owner_ref


_LOGICAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
_LOCATOR_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[a-z0-9_./-]+$")
_SECRET_REF_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[a-z0-9_./-]+$")
_ALLOWED_DURABILITY = frozenset({"durable", "ephemeral"})
_ALLOWED_LOCALITY = frozenset({"node", "network", "any"})
_ALLOWED_ISOLATION = frozenset({"file", "database", "schema"})


class RelationalStorageContractError(ValueError):
    """Raised when a storage requirement or binding violates the ABI."""


class RelationalStorageCapabilityError(RuntimeError):
    """Raised when no registered provider can satisfy a requirement."""


class RelationalStorageIsolationError(OwnershipIsolationError):
    """Raised when a caller tries to use another owner's binding."""


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
    locality: str = "node"
    migration_owner: str | None = None
    preferred_providers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        durability = str(self.durability or "").strip().lower()
        locality = str(self.locality or "").strip().lower()
        if durability not in _ALLOWED_DURABILITY:
            raise RelationalStorageContractError(
                f"durability must be one of {sorted(_ALLOWED_DURABILITY)}"
            )
        if locality not in _ALLOWED_LOCALITY:
            raise RelationalStorageContractError(
                f"locality must be one of {sorted(_ALLOWED_LOCALITY)}"
            )
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
            locality=self.locality,
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
            "locality": self.locality,
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

    def __post_init__(self) -> None:
        provider_id = _required_token(self.provider_id, "provider_id").lower()
        durability = tuple(dict.fromkeys(str(item).strip().lower() for item in self.durability))
        localities = tuple(dict.fromkeys(str(item).strip().lower() for item in self.localities))
        isolation = str(self.isolation or "").strip().lower()
        if not durability or any(item not in _ALLOWED_DURABILITY for item in durability):
            raise RelationalStorageContractError("provider durability profile is invalid")
        if not localities or any(item not in _ALLOWED_LOCALITY for item in localities):
            raise RelationalStorageContractError("provider locality profile is invalid")
        if isolation not in _ALLOWED_ISOLATION:
            raise RelationalStorageContractError("provider isolation mode is invalid")
        if self.max_concurrent_writers is not None and int(self.max_concurrent_writers) < 1:
            raise RelationalStorageContractError("max_concurrent_writers must be >= 1 or null")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "durability", durability)
        object.__setattr__(self, "localities", localities)
        object.__setattr__(self, "isolation", isolation)

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
            "localities": list(self.localities),
            "isolation": self.isolation,
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
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    secret_ref: str | None = None

    def __post_init__(self) -> None:
        binding_id = _required_token(self.binding_id, "binding_id")
        provider_id = _required_token(self.provider_id, "provider_id").lower()
        owner_ref = validate_owner_ref(self.owner_ref)
        logical_name = validate_logical_name(self.logical_name)
        isolation = str(self.isolation or "").strip().lower()
        locator = _required_token(self.locator, "locator")
        migration_owner = validate_owner_ref(self.migration_owner)
        if isolation not in _ALLOWED_ISOLATION:
            raise RelationalStorageContractError("binding isolation mode is invalid")
        if migration_owner != owner_ref:
            raise RelationalStorageIsolationError(
                "private binding migration_owner must equal owner_ref"
            )
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
        object.__setattr__(self, "binding_id", binding_id)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "owner_ref", owner_ref)
        object.__setattr__(self, "logical_name", logical_name)
        object.__setattr__(self, "isolation", isolation)
        object.__setattr__(self, "locator", locator)
        object.__setattr__(self, "migration_owner", migration_owner)
        object.__setattr__(self, "capabilities", dict(self.capabilities))
        object.__setattr__(self, "secret_ref", secret_ref)

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
            "capabilities": dict(self.capabilities),
            "secret_ref": self.secret_ref,
        }


__all__ = [
    "RelationalProviderCapabilities",
    "RelationalStorageBinding",
    "RelationalStorageCapabilityError",
    "RelationalStorageContractError",
    "RelationalStorageIsolationError",
    "RelationalStorageRequirements",
    "validate_logical_name",
    "validate_owner_ref",
]
