"""Skill-scoped relational storage capability.

Skills acquire an opaque database handle. The active skill context determines
the owner; callers cannot request another skill's database or a physical DSN.
SQL uses SQLAlchemy-style named parameters (``:name``) across providers.
"""

from __future__ import annotations

from adaos.domain.relational_storage import (
    RelationalStorageBinding,
    RelationalStorageCapabilityError,
    RelationalStorageContractError,
    RelationalStorageIsolationError,
    RelationalStorageRequirements,
)
from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.storage.relational import (
    RelationalDatabase,
    RelationalResult,
    RelationalSession,
    RelationalStorageService,
)


def database(
    name: str = "main",
    *,
    requirements: RelationalStorageRequirements | None = None,
) -> RelationalDatabase:
    ctx = require_ctx("sdk.data.relational.database")
    require_skill_capability(ctx, "storage.relational")
    return RelationalStorageService(ctx).acquire_for_current_skill(
        name,
        requirements=requirements,
    )


__all__ = [
    "RelationalDatabase",
    "RelationalResult",
    "RelationalSession",
    "RelationalStorageBinding",
    "RelationalStorageCapabilityError",
    "RelationalStorageContractError",
    "RelationalStorageIsolationError",
    "RelationalStorageRequirements",
    "database",
]
