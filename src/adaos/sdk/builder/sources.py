"""Capability-gated source intake for Builder projects."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.skill_capabilities import require_skill_capability


def _service():
    from adaos.services.builder.sources import BuilderProjectSourceService

    return BuilderProjectSourceService.from_context()


def _admit(operation: str) -> None:
    ctx = require_ctx(operation)
    require_skill_capability(ctx, "builder.project_sources")


def add_path(
    path: str | Path,
    *,
    kind: str,
    project_id: str,
    name: str | None = None,
    media_type: str | None = None,
    role: str = "source",
    origin: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _admit("sdk.builder.sources.add_path")
    return _service().add_path(
        path,
        kind=kind,
        project_id=project_id,
        name=name,
        media_type=media_type,
        role=role,
        origin=origin,
    )


def current_bundle(kind: str, project_id: str) -> dict[str, Any]:
    _admit("sdk.builder.sources.current_bundle")
    return _service().current_bundle(kind, project_id)


def get_bundle(digest: str) -> dict[str, Any]:
    _admit("sdk.builder.sources.get_bundle")
    return _service().get_bundle(digest)


def read_text(digest: str, *, max_characters: int = 120_000) -> str:
    _admit("sdk.builder.sources.read_text")
    return _service().read_text(digest, max_characters=max_characters)


__all__ = ["add_path", "current_bundle", "get_bundle", "read_text"]
