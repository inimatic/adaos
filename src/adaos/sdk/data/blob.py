"""Owner-isolated immutable blob storage for skill artifacts."""

from __future__ import annotations

from adaos.domain.blob_storage import BlobStorageBinding, BlobStorageRequirements
from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.storage.blob import BlobStore, BlobStorageService


def store(
    name: str = "artifacts",
    *,
    requirements: BlobStorageRequirements | None = None,
) -> BlobStore:
    ctx = require_ctx("sdk.data.blob.store")
    require_skill_capability(ctx, "storage.blob")
    return BlobStorageService(ctx).acquire_for_current_skill(
        name,
        requirements=requirements,
    )


__all__ = ["BlobStorageBinding", "BlobStorageRequirements", "BlobStore", "store"]
