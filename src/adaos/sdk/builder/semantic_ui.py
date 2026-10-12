"""Stable SDK facade for bounded Builder semantic UI operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.semantic_ui import BuilderSemanticUIService

    return BuilderSemanticUIService.from_context()


def apply(operation: Mapping[str, Any]) -> dict[str, Any]:
    if operation.get("schema") == "adaos.builder.semantic_graph_change.v1":
        from adaos.services.builder.semantic_graph import SemanticGraphService
        return dict(SemanticGraphService.from_context().apply(operation))
    return dict(_service().apply(operation))


def replay(document: Mapping[str, Any], ops, *, write_refs) -> dict[str, Any]:
    """Pure replay of retained semantic operations; no model or Application effects."""
    from adaos.services.builder.semantic_graph import reduce_graph
    return reduce_graph(document, ops, write_refs=write_refs)


def verify_source(document: Mapping[str, Any], webui: Mapping[str, Any], *, project_ref: str) -> dict[str, Any]:
    from adaos.services.builder.semantic_graph import verify_baseline
    return verify_baseline(document, webui, project_ref=project_ref)


__all__ = ["apply", "replay", "verify_source"]
