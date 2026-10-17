"""Generate an owner-scoped image draft; applying it to application assets is explicit."""

from pathlib import Path
from typing import Any, Mapping

from adaos.sdk import access
from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.llm.errors import SubmissionNotAdmitted


def _service(capability: str):
    from adaos.sdk.llm import llm_client
    from adaos.sdk.io.media import publish_media_file, browser_media_descriptor
    from adaos.services.image_generation import ImageGenerationService

    access.require(capability)
    ctx = require_ctx("sdk.llm.images")
    actor = access.caller()
    skill = ctx.skill_ctx.get()
    if not actor or not skill or not skill.name:
        raise ValueError("Image generation requires an authenticated caller and executing skill")

    def publish(path: Path, *, content_ref: str, mime: str) -> dict[str, Any]:
        media = publish_media_file(path, content_ref=content_ref, namespace="generated-image", mime=mime)
        return browser_media_descriptor(media, content_ref=content_ref)

    return ImageGenerationService(Path(ctx.paths.state_dir()) / "image-generation",
        f"skill:{skill.name}|{actor['kind']}:{actor['id']}", llm_client, publish)


def generate(*, request_id: str, model: str, prompt: str, size: str = "1024x1024", quality: str = "low",
             output_format: str = "png", background: str = "opaque", context: Mapping[str, Any] | None = None,
             spend_scope: str | None = None, spend_run_id: str | None = None,
             spend_reserve_usd: float | None = None) -> dict[str, Any]:
    """Submit once with an explicit image model. Context is local and not sent to the provider."""
    return _service("workspace.write").submit(request_id=request_id, model=model, prompt=prompt, size=size,
        quality=quality, output_format=output_format, background=background, context=context,
        spend_scope=spend_scope, spend_run_id=spend_run_id, spend_reserve_usd=spend_reserve_usd)


def get(request_id: str) -> dict[str, Any]:
    """Read/poll an owned draft without starting a new image generation."""
    return _service("workspace.read").get(request_id)


def read_bytes(request_id: str, *, max_bytes: int = 12582912) -> bytes:
    """Read verified bytes of a completed owned draft; no provider calls or URLs.

    Requires the same authenticated actor and executing skill as edit/generate.
    Call get to poll first. A pending/failed/missing draft is an error, never a
    new submission. max_bytes must be an integer in 1..12582912.
    """
    return _service("workspace.read").read_bytes(request_id, max_bytes=max_bytes)


def edit(*, request_id: str, model: str, prompt: str, images: list[dict[str, str]],
         mask: dict[str, str] | None = None, size: str = "1024x1024", quality: str = "high",
         output_format: str = "png", background: str = "opaque",
         context: Mapping[str, Any] | None = None, spend_scope: str | None = None,
         spend_run_id: str | None = None, spend_reserve_usd: float | None = None) -> dict[str, Any]:
    """Submit an owned image edit once; polling uses get with the same identity."""
    return _service("workspace.write").submit(request_id=request_id, model=model, prompt=prompt,
        images=images, mask=mask, size=size, quality=quality, output_format=output_format,
        background=background, context=context, spend_scope=spend_scope,
        spend_run_id=spend_run_id, spend_reserve_usd=spend_reserve_usd)


def list_drafts(*, context: Mapping[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    """Observe current actor/skill drafts matching explicit local context without paid work."""
    return _service("workspace.read").list_drafts(context=context, limit=limit)


__all__ = ["generate", "edit", "get", "read_bytes", "list_drafts", "SubmissionNotAdmitted"]
