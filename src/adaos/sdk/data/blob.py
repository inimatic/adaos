"""Owner-isolated immutable blob storage for skill artifacts."""

from __future__ import annotations

from adaos.domain.blob_storage import BlobStorageBinding, BlobStorageRequirements
from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.storage.blob import BlobStore, BlobStorageService
from adaos.services.storage.upload_context import consume_verified_tool_upload


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


def put_upload(name: str = "attachments") -> dict:
    """Consume the binary body admitted for the active tool invocation.

    Bytes are never accepted from tool arguments. The HTTP tool ingress binds
    this context only after authenticating the caller and applying the normal
    Application permission and action-risk gates.
    """

    upload = consume_verified_tool_upload()
    result = store(name).put_bytes(
        upload.filename,
        upload.content,
        media_type=upload.media_type,
    )
    result.update(
        {
            "field_id": upload.field_id,
            "filename": upload.filename,
        }
    )
    return result


__all__ = [
    "BlobStorageBinding",
    "BlobStorageRequirements",
    "BlobStore",
    "put_upload",
    "store",
]
