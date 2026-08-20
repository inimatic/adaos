"""Stable SDK facade for the Builder project workflow state machine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.workflow import BuilderWorkflowService

    return BuilderWorkflowService.from_context()


def get_state(object_type: str, object_id: str) -> dict[str, Any]:
    return dict(_service().describe(object_type, object_id))


def get_interaction_frame(
    object_type: str,
    object_id: str,
    *,
    locale: str | None = None,
) -> dict[str, Any]:
    return dict(_service().interaction_frame(object_type, object_id, locale=locale))


def get_process_explanation(
    object_type: str,
    object_id: str,
    *,
    locale: str | None = None,
) -> dict[str, Any]:
    return dict(_service().process_explanation(object_type, object_id, locale=locale))


def create_conversation_interaction(
    object_type: str,
    object_id: str,
    *,
    conversation_id: str,
    principal_id: str,
    command_context_id: str,
    prompt: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    locale: str | None = None,
) -> dict[str, Any]:
    return dict(
        _service().conversation_interaction(
            object_type,
            object_id,
            conversation_id=conversation_id,
            principal_id=principal_id,
            command_context_id=command_context_id,
            prompt=prompt,
            metadata=metadata,
            locale=locale,
        )
    )


def create_conversation_input_interaction(
    object_type: str,
    object_id: str,
    *,
    surface_command: str,
    conversation_id: str,
    principal_id: str,
    command_context_id: str,
    metadata: Mapping[str, Any] | None = None,
    locale: str | None = None,
) -> dict[str, Any]:
    return dict(
        _service().conversation_input_interaction(
            object_type,
            object_id,
            surface_command=surface_command,
            conversation_id=conversation_id,
            principal_id=principal_id,
            command_context_id=command_context_id,
            metadata=metadata,
            locale=locale,
        )
    )


def invoke_interaction_response(
    object_type: str,
    object_id: str,
    response: Mapping[str, Any],
    *,
    actor: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return dict(
        _service().invoke_interaction_response(
            object_type,
            object_id,
            response,
            actor=actor,
            metadata=metadata,
        )
    )


def invoke_command(
    object_type: str,
    object_id: str,
    command: str,
    *,
    actor: str,
    idempotency_key: str,
    input_value: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return dict(
        _service().invoke_command(
            object_type,
            object_id,
            command,
            actor=actor,
            idempotency_key=idempotency_key,
            input_value=input_value,
            metadata=metadata,
        )
    )


def record_project_placement(
    object_type: str,
    object_id: str,
    placement: Mapping[str, Any],
    *,
    expected_generation: int,
) -> dict[str, Any]:
    return dict(
        _service().record_project_placement(
            object_type,
            object_id,
            placement,
            expected_generation=expected_generation,
        )
    )


def rebase_change(
    object_type: str,
    object_id: str,
    change_id: str,
    *,
    expected_project_generation: int,
    verified_unchanged_refs: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Rebase one reviewed Change after its affected refs were verified."""

    return dict(
        _service().rebase_change(
            object_type,
            object_id,
            change_id,
            expected_project_generation=expected_project_generation,
            verified_unchanged_refs=verified_unchanged_refs,
        )
    )


def get_project_placement_navigation(
    object_type: str,
    object_id: str,
    *,
    kind: str = "stable",
    base_url: str | None = None,
) -> dict[str, Any]:
    return dict(
        _service().project_placement_navigation(
            object_type,
            object_id,
            kind=kind,
            base_url=base_url,
        )
    )


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
    conversation_context: Mapping[str, Any] | None = None,
    pending_action_refs: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
    run_purpose: str = "iteration",
    required_facets: list[str] | tuple[str, ...] | None = None,
    enforce_context_coverage: bool = False,
    persist: bool = False,
) -> dict[str, Any]:
    return dict(
        _service().build_context_packet(
            object_type,
            object_id,
            allowed_paths=allowed_paths,
            instruction_refs=instruction_refs,
            conversation_context=conversation_context,
            pending_action_refs=pending_action_refs,
            run_purpose=run_purpose,
            required_facets=required_facets,
            enforce_context_coverage=enforce_context_coverage,
            persist=persist,
        )
    )


def update_interaction_context(
    object_type: str,
    object_id: str,
    updates: Mapping[str, Any],
    *,
    expected_generation: int,
) -> dict[str, Any]:
    return dict(
        _service().update_interaction_context(
            object_type,
            object_id,
            updates,
            expected_generation=expected_generation,
        )
    )


__all__ = [
    "build_context_packet",
    "create_conversation_interaction",
    "create_conversation_input_interaction",
    "get_interaction_frame",
    "get_process_explanation",
    "get_project_placement_navigation",
    "get_state",
    "invoke_command",
    "invoke_interaction_response",
    "rebase_change",
    "record_project_placement",
    "transition",
    "update_interaction_context",
]
