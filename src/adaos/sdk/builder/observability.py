"""Public Builder observability helpers."""

from __future__ import annotations

from typing import Any


def llm_input_attribution(**kwargs: Any) -> dict[str, Any]:
    from adaos.services.builder.llm_input_attribution import (
        build_llm_input_attribution,
    )

    return build_llm_input_attribution(**kwargs)


def retain_llm_evidence(value: Any) -> dict[str, Any]:
    """Retain exact private attempt evidence and verify its durable receipt."""
    from adaos.services.builder.llm_input_attribution import retain_llm_evidence as retain
    return retain(value)


__all__ = ["llm_input_attribution", "retain_llm_evidence"]
