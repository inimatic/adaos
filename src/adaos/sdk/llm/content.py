"""Generate typed drafts through the subscribed Root broker; applying them is explicit."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from adaos.sdk.core._ctx import require_ctx


def _service():
    from adaos.sdk.llm import llm_client
    from adaos.services.content_generation import ContentGenerationService
    from adaos.services.personalization_runtime import current_user_id

    ctx = require_ctx("sdk.llm.content")
    current = ctx.skill_ctx.get()
    name = str(getattr(current, "name", "") or "")
    if not name:
        raise ValueError("Content generation requires an active skill context")
    return ContentGenerationService(Path(ctx.paths.state_dir()) / "content-generation", 
                                    f"skill:{name}|user:{current_user_id(ctx)}", llm_client)


def generate(*, request_id: str, purpose: str, prompt: str, schema: Mapping[str, Any], data: Any = None,
             model: str | None = None, reasoning: Mapping[str, Any] | None = None,
             temperature: float | None = None, max_output_tokens: int | None = None,
             context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Submit once; reuse request_id only for retrying exactly the same input."""
    return _service().submit(request_id=request_id, purpose=purpose, prompt=prompt, schema=schema,
                             data=data, model=model, reasoning=reasoning, temperature=temperature,
                             max_output_tokens=max_output_tokens, context=context)


def get(request_id: str) -> dict[str, Any]:
    """Poll a durable draft without submitting another model invocation."""
    return _service().get(request_id)
