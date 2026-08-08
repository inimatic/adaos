"""Provider-neutral contracts for large immutable skill-owned content."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, ClassVar

from .ownership import validate_owner_ref


_SECRET_REF_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[a-z0-9_./-]+$")


@dataclass(frozen=True, slots=True)
class BlobStorageRequirements:
    SCHEMA: ClassVar[str] = "adaos.storage.blob.requirement.v1"

    durability: str = "durable"
    locality: str = "node"
    capacity_bytes: int | None = None
    content_addressed: bool = True
    retention_policy: str = "retain"

    def __post_init__(self) -> None:
        if self.durability not in {"durable", "ephemeral"}:
            raise ValueError("unsupported blob durability")
        if self.locality not in {"node", "network", "any"}:
            raise ValueError("unsupported blob locality")
        if self.capacity_bytes is not None and int(self.capacity_bytes) < 1:
            raise ValueError("capacity_bytes must be >= 1")
        if self.retention_policy not in {"retain", "delete_on_uninstall", "ttl"}:
            raise ValueError("unsupported blob retention policy")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "durability": self.durability,
            "locality": self.locality,
            "capacity_bytes": self.capacity_bytes,
            "content_addressed": self.content_addressed,
            "retention_policy": self.retention_policy,
        }


@dataclass(frozen=True, slots=True)
class BlobStorageBinding:
    SCHEMA: ClassVar[str] = "adaos.storage.blob.binding.v1"

    binding_id: str
    provider_id: str
    owner_ref: str
    locator: str
    secret_ref: str | None = None
    protocol_version: str = "1.0"

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_ref", validate_owner_ref(self.owner_ref))
        if not self.binding_id or not self.provider_id or not self.locator:
            raise ValueError("blob binding identity fields are required")
        if "://" in self.locator:
            raise ValueError("blob binding locator must be opaque")
        if self.protocol_version != "1.0":
            raise ValueError("unsupported blob binding protocol")
        secret_ref = str(self.secret_ref or "").strip() or None
        if secret_ref is not None and (
            any(token in secret_ref.lower() for token in ("://", "@", "password=", "token="))
            or not _SECRET_REF_RE.fullmatch(secret_ref)
        ):
            raise ValueError("blob secret_ref must be an opaque reference")
        object.__setattr__(self, "secret_ref", secret_ref)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.SCHEMA, "capability": "storage.blob", **asdict(self)}


__all__ = ["BlobStorageBinding", "BlobStorageRequirements"]
