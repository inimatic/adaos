"""Stable SDK facade for the Builder project workflow state machine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.workflow import BuilderWorkflowService

    return BuilderWorkflowService.from_context()


def get_state(object_type: str, object_id: str) -> dict[str, Any]:
    return dict(_service().describe(object_type, object_id))


def transition(
    object_type: str,
    object_id: str,
    action: str,
    *,
    actor: str = "builder",
    reason: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return dict(
        _service().transition(
            object_type,
            object_id,
            action,
            actor=actor,
            reason=reason,
            metadata=metadata,
        )
    )


__all__ = ["get_state", "transition"]
