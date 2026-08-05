"""Public SDK boundary for governed, data-defined workflows.

Skills and scenarios use this module instead of importing workflow services.
The definition remains package-owned data; AdaOS owns validation, optimistic
concurrency, durable admission, interactions and asynchronous effect records.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from adaos.services import conversation_interactions, workflow_persistence
from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    WorkflowResolver,
    compile_definition,
    new_instance,
    verified_workflow_principal,
    workflow_ref,
)
from adaos.services.workflow_execution import (
    WorkflowExecutorRegistration,
    WorkflowExecutorRegistry,
    description_with_executor_readiness,
    execute_invocation,
    prepare_interaction_invocation,
    prepare_sdk_invocation,
)
from adaos.services.workflow_registry import platform_workflow_adapter_registry


DefinitionInput = CompiledWorkflowDefinition | Mapping[str, Any] | Path | str


def load_definition(value: DefinitionInput) -> CompiledWorkflowDefinition:
    if isinstance(value, CompiledWorkflowDefinition):
        return value
    if isinstance(value, Mapping):
        return compile_definition(value)
    path = Path(value).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("workflow definition must contain a JSON object")
    return compile_definition(payload)


def _principal(actor_id: str, *, authenticated: bool = True):
    return verified_workflow_principal(
        str(actor_id or "").strip(),
        authenticated=authenticated,
        issuer="adaos.sdk.workflow",
    )


def _executors(
    registrations: Iterable[WorkflowExecutorRegistration] = (),
) -> WorkflowExecutorRegistry:
    return WorkflowExecutorRegistry(
        platform_workflow_adapter_registry(),
        tuple(registrations),
    )


def ensure_instance(
    definition: DefinitionInput,
    instance_id: str,
    *,
    context: Mapping[str, Any] | None = None,
    package_digest: str | None = None,
    binding_digest: str | None = None,
) -> dict[str, Any]:
    """Return the durable instance, creating its initial snapshot once."""

    compiled = load_definition(definition)
    selected_id = str(instance_id or "").strip()
    if not selected_id:
        raise ValueError("workflow instance_id is required")
    stored = workflow_persistence.get_instance(selected_id)
    if stored is not None:
        if stored["workflow_type"] != compiled.workflow_type:
            raise ValueError("workflow instance type differs from the definition")
        if stored["definition_version"] != compiled.definition_version:
            raise ValueError("workflow instance requires an explicit definition migration")
        return stored
    instance = new_instance(
        compiled,
        selected_id,
        context=dict(context or {}),
        package_digest=package_digest,
        binding_digest=binding_digest,
    )
    return workflow_persistence.create_instance(instance)


def describe(
    definition: DefinitionInput,
    instance_id: str,
    *,
    actor_id: str,
    authenticated: bool = True,
    context: Mapping[str, Any] | None = None,
    executor_registrations: Iterable[WorkflowExecutorRegistration] = (),
) -> dict[str, Any]:
    compiled = load_definition(definition)
    instance = ensure_instance(compiled, instance_id)
    description = WorkflowResolver(require_verified_principal=True).describe(
        compiled,
        instance,
        actor=actor_id,
        principal=_principal(actor_id, authenticated=authenticated),
        context=context,
    )
    return description_with_executor_readiness(
        description,
        compiled,
        _executors(executor_registrations),
    )


def create_interaction(
    definition: DefinitionInput,
    instance_id: str,
    *,
    actor_id: str,
    conversation_id: str,
    owner: str,
    command_context_id: str,
    interaction_id: str | None = None,
    prompt: str | None = None,
    thread_id: str | None = None,
    reply_route_id: str | None = None,
    context: Mapping[str, Any] | None = None,
    executor_registrations: Iterable[WorkflowExecutorRegistration] = (),
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one authoritative workflow snapshot into an interaction."""

    compiled = load_definition(definition)
    description = describe(
        compiled,
        instance_id,
        actor_id=actor_id,
        context=context,
        executor_registrations=executor_registrations,
    )
    instance = ensure_instance(compiled, instance_id)
    return conversation_interactions.interaction_from_workflow_description(
        description,
        conversation_id=conversation_id,
        owner=owner,
        interaction_id=interaction_id,
        thread_id=thread_id,
        prompt=prompt,
        workflow_ref=workflow_ref(
            "workflow",
            instance_id,
            version=compiled.definition_version,
            generation=int(instance["generation"]),
            digest=str(instance.get("definition_digest") or "") or None,
        ),
        command_context_ref=workflow_ref("command_context", command_context_id),
        reply_route_ref=(
            workflow_ref("reply_route", reply_route_id) if reply_route_id else None
        ),
        metadata=dict(metadata or {}),
    )


def invoke(
    definition: DefinitionInput,
    instance_id: str,
    command_id: str,
    *,
    actor_id: str,
    idempotency_key: str,
    input_value: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    target_ref: Mapping[str, Any] | None = None,
    command_context_id: str | None = None,
    executor_registrations: Iterable[WorkflowExecutorRegistration] = (),
) -> dict[str, Any]:
    compiled = load_definition(definition)
    instance = ensure_instance(compiled, instance_id)
    invocation = prepare_sdk_invocation(
        workflow_type=compiled.workflow_type,
        instance_ref=workflow_ref(
            "workflow",
            instance_id,
            version=compiled.definition_version,
            generation=int(instance["generation"]),
            digest=str(instance.get("definition_digest") or "") or None,
        ),
        actor_id=actor_id,
        command_id=command_id,
        expected_generation=int(instance["generation"]),
        idempotency_key=idempotency_key,
        input_value=input_value,
        target_ref=target_ref,
        context_ref=(
            workflow_ref("command_context", command_context_id)
            if command_context_id
            else None
        ),
    )
    return execute_invocation(
        invocation,
        compiled,
        instance,
        principal=_principal(actor_id),
        adapters=platform_workflow_adapter_registry(),
        executors=_executors(executor_registrations),
        context=context,
    )


def invoke_interaction_response(
    definition: DefinitionInput,
    instance_id: str,
    response: Mapping[str, Any],
    *,
    actor_id: str,
    context: Mapping[str, Any] | None = None,
    executor_registrations: Iterable[WorkflowExecutorRegistration] = (),
) -> dict[str, Any]:
    compiled = load_definition(definition)
    instance = ensure_instance(compiled, instance_id)
    invocation = prepare_interaction_invocation(response)
    if str(invocation["command"]["instance_ref"]["id"]) != str(instance_id):
        raise ValueError("interaction response targets another workflow instance")
    return execute_invocation(
        invocation,
        compiled,
        instance,
        principal=_principal(actor_id),
        adapters=platform_workflow_adapter_registry(),
        executors=_executors(executor_registrations),
        context=context,
    )


__all__ = [
    "WorkflowExecutorRegistration",
    "create_interaction",
    "describe",
    "ensure_instance",
    "invoke",
    "invoke_interaction_response",
    "load_definition",
]
