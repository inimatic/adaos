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
    expected_generation: int | None = None,
) -> dict[str, Any]:
    return dict(
        _service().transition(
            object_type,
            object_id,
            action,
            actor=actor,
            reason=reason,
            metadata=metadata,
            expected_generation=expected_generation,
        )
    )


def build_context_packet(
    object_type: str,
    object_id: str,
    *,
    allowed_paths: list[str] | tuple[str, ...] | None = None,
    instruction_refs: list[str] | tuple[str, ...] | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    return dict(
        _service().build_context_packet(
            object_type,
            object_id,
            allowed_paths=allowed_paths,
            instruction_refs=instruction_refs,
            persist=persist,
        )
    )


__all__ = ["build_context_packet", "get_state", "transition"]
