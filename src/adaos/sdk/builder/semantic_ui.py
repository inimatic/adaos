"""Stable SDK facade for bounded Builder semantic UI operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.semantic_ui import BuilderSemanticUIService

    return BuilderSemanticUIService.from_context()


def apply(operation: Mapping[str, Any]) -> dict[str, Any]:
    return dict(_service().apply(operation))


__all__ = ["apply"]
