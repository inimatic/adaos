"""Public Builder observability helpers."""

from __future__ import annotations

from typing import Any


def llm_input_attribution(**kwargs: Any) -> dict[str, Any]:
    from adaos.services.builder.llm_input_attribution import (
        build_llm_input_attribution,
    )

    return build_llm_input_attribution(**kwargs)


__all__ = ["llm_input_attribution"]
