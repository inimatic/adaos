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
    new submission. max_bytes must be an integer in 1..67108864. The default
    remains 12 MiB; larger native PNG edits require an explicit read allowance.
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




def edit_limits(model: str) -> dict:
    """Inspect the current Core/SDK image-edit admission contract, without IO.

    These are local contract bounds, not a claim of remote model availability
    or a guarantee of preserved detail after a caller resamples its input.
    """
    import re
    if not re.fullmatch(r"gpt-image-[a-z0-9.-]+", str(model or "")):
        raise ValueError("An explicit GPT Image model is required")
    modern = bool(re.match(r"^gpt-image-2(?:[.-]|$)", model))
    return {
        "model": model, "source": "core_sdk_admission", "requires_remote_availability_check": True,
        "standard_sizes": ["1024x1024", "1536x1024", "1024x1536"],
        "custom_sizes": modern, "dimension_multiple": 16 if modern else None,
        "max_edge": 3840 if modern else 1536, "min_pixels": 655360 if modern else 1048576,
        "max_pixels": 8294400 if modern else 1572864, "max_aspect_ratio": 3 if modern else 1.5,
        "qualities": ["low", "medium", "high"] + (["xhigh", "max"] if model.startswith("gpt-image-2.5-") else []),
        "max_inline_bytes": 16 * 1024 * 1024, "max_images": 16,
        "backgrounds": ["opaque", "transparent"], "formats": ["png", "jpeg", "webp"],
    }


def validate_edit(*, model: str, prompt: str, images: list[dict[str, str]],
                  mask: dict[str, str] | None = None, size: str = "1024x1024",
                  quality: str = "high", output_format: str = "png", background: str = "opaque") -> dict:
    """Validate exact edit inputs before reserving a request or submitting.

    Pure bounded local decode/contract checks, shared with real submission.
    Does not create a draft, reserve budget, access Root or establish availability.
    Invalid input raises ValueError; the caller can correct it without a job.
    """
    from adaos.sdk.llm.llm_client import _validated_image_edit
    _validated_image_edit(model=model, prompt=prompt, images=images, mask=mask, size=size,
        quality=quality, output_format=output_format, background=background)
    references = [*images, *([mask] if mask is not None else [])]
    return {"ok": True, "model": model, "size": size,
            "inline_bytes": sum(len(ref["image_url"]) for ref in references),
            "limits_source": "core_sdk_admission", "provider_contacted": False}


__all__ = ["generate", "edit", "get", "read_bytes", "list_drafts", "edit_limits", "validate_edit", "SubmissionNotAdmitted"]
