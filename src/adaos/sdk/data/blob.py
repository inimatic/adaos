"""Owner-isolated immutable blob storage for skill artifacts."""

from __future__ import annotations

import hashlib
import re

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

    This is the production-attachment write boundary. Bytes are never accepted
    from JSON tool arguments. The HTTP tool ingress binds a one-use context only
    after authenticating the caller and applying normal Application permission
    and action-risk gates. Call this exactly once from an upload tool after
    validating its admitted ``filename``, ``field_id``, ``media_type``,
    ``size_bytes`` and ``digest`` metadata.

    The skill and Project permission profile must declare ``storage.blob``.
    The upload tool also declares and requires ``workspace.write``. A separate
    read tool declares and requires ``workspace.read``; it accepts the requested
    ``sha256:<hex>`` ref and returns ``{"ok": true, "ref": <same-ref>}`` only
    when the caller may read it. The WebUI file field uses
    ``fileStorage: skill`` and same-skill qualified ``uploadTarget`` and
    ``readTarget`` names. Core invokes these tools and turns the receipt into an
    authenticated same-origin ``/api/tools/.../attachments/...`` reference.

    Returns an ``adaos.storage.blob.object.v1`` receipt with ``ref`` (an
    internal ``adaos-blob:`` identity), ``digest``, ``size_bytes``,
    ``media_type``, ``owner_ref``, ``logical_name``, ``field_id`` and
    ``filename``. Persist the browser attachment reference supplied to the form,
    not this internal blob identity. Failed upload/save must retain the prior
    record value. The ingress rejects empty bodies and bodies over 10 MiB; an
    application should enforce its narrower MIME and size policy before calling
    this function.
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


def read_bytes(ref: str, name: str = "attachments", *, max_bytes: int = 10485760) -> bytes:
    """Read a verified sha256 object from the current skill's isolated store.

    Requires storage.blob. The tool must first require workspace.read for a
    read, or workspace.write when validating its just-admitted upload. The
    digest alone grants no cross-owner or cross-runtime access. Missing owned
    objects raise FileNotFoundError; malformed refs, size limits and digest
    mismatches raise ValueError. No URL, filesystem path or credentials are
    accepted. Returns bytes, never a browser attachment URL.
    """
    if not isinstance(ref, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", ref):
        raise ValueError("invalid blob digest")
    if type(max_bytes) is not int or not 1 <= max_bytes <= 10485760:
        raise ValueError("max_bytes must be between 1 and 10485760")
    path = store(name).materialize_digest(ref)
    with path.open("rb") as stream:
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("blob exceeds the requested size limit")
    if "sha256:" + hashlib.sha256(content).hexdigest() != ref:
        raise ValueError("blob digest verification failed")
    return content


__all__ = [
    "BlobStorageBinding",
    "BlobStorageRequirements",
    "BlobStore",
    "put_upload",
    "read_bytes",
    "store",
]
