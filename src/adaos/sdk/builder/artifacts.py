"""SDK operations for durable Builder artifact checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def checkpoint(
    *,
    kind: str,
    artifact_id: str,
    message: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from adaos.services.builder.workspace import BuilderWorkspaceService

    service = BuilderWorkspaceService.from_context()
    return dict(
        service.checkpoint_artifact(
            kind=kind,
            artifact_id=artifact_id,
            message=message,
            metadata=metadata,
        )
        or {}
    )


__all__ = ["checkpoint"]
