from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from adaos.services import conversation_store, durable_delivery, workflow_persistence
from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    VerifiedWorkflowPrincipal,
    WorkflowResolver,
    apply_workflow_command,
    compile_definition,
    validate_workflow_record,
    workflow_definition_digest,
    workflow_ref,
)
from adaos.services.workflow_registry import WorkflowAdapterRegistry


WORKFLOW_INVOCATION_SCHEMA = "adaos.workflow.invocation.v1"


class WorkflowExecutionError(ValueError):
    """Raised when a command cannot enter the canonical execution boundary."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ref(value: Mapping[str, Any] | None, *, fallback_kind: str | None = None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    identifier = str(value.get("id") or value.get("route_id") or "").strip()
    kind = str(value.get("kind") or fallback_kind or "").strip()
    if not identifier or not kind:
        return None
    return workflow_ref(
        kind,
        identifier,
        version=str(value.get("version") or "").strip() or None,
        generation=(int(value["generation"]) if value.get("generation") is not None else None),
        digest=str(value.get("digest") or "").strip() or None,
    )


@dataclass(frozen=True, slots=True)
class WorkflowExecutorRegistration:
    adapter_id: str
    contract_digest: str
    executor_id: str
    available: bool = True
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "contract_digest": self.contract_digest,
            "executor_id": self.executor_id,
            "available": self.available,
            "reason_code": self.reason_code,
        }


@dataclass(slots=True)
class WorkflowExecutorRegistry:
    """Immutable runtime readiness bindings for registered activity contracts.

    The adapter registry proves what an activity may do. This registry proves
    that one concrete executor is currently able to accept the activity. A
    presentation may not infer readiness from the adapter name alone.
    """

    adapters: WorkflowAdapterRegistry
    registrations: Iterable[WorkflowExecutorRegistration] = ()
    _registrations: dict[str, WorkflowExecutorRegistration] = field(
        init=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        for registration in self.registrations:
            self.register(registration)

    def register(self, registration: WorkflowExecutorRegistration) -> None:
        adapter_id = str(registration.adapter_id or "").strip()
        contract = self.adapters.get("activity", adapter_id)
        if contract is None:
            raise WorkflowExecutionError(f"activity adapter is not registered: {adapter_id}")
        if str(contract["contract_digest"]) != str(registration.contract_digest):
            raise WorkflowExecutionError(
                f"executor contract digest does not match activity adapter: {adapter_id}"
            )
        previous = self._registrations.get(adapter_id)
        if previous is not None and previous != registration:
            raise WorkflowExecutionError(
                f"mutable workflow executor registration rejected: {adapter_id}"
            )
        self._registrations[adapter_id] = registration

    def status(self, adapter_id: str | None) -> dict[str, Any]:
        if not adapter_id:
            return {
                "available": True,
                "adapter_id": None,
                "executor_id": "adaos.workflow.pure_transition",
                "reason_code": None,
                "contract_digest": None,
            }
        registration = self._registrations.get(str(adapter_id))
        if registration is None:
            return {
                "available": False,
                "adapter_id": str(adapter_id),
                "executor_id": None,
                "reason_code": "executor_unavailable",
                "contract_digest": None,
            }
        return registration.to_dict()


def description_with_executor_readiness(
    description: Mapping[str, Any],
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
    executors: WorkflowExecutorRegistry,
) -> dict[str, Any]:
    """Move commands without a ready executor into the canonical blocked set."""

    compiled = definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)
    snapshot = copy.deepcopy(dict(description or {}))
    if snapshot.get("schema") != "adaos.workflow.description.v1":
        raise WorkflowExecutionError("workflow description must use adaos.workflow.description.v1")
    if snapshot.get("workflow_type") != compiled.workflow_type:
        raise WorkflowExecutionError("workflow description type does not match definition")
    if snapshot.get("definition_digest") != workflow_definition_digest(compiled):
        raise WorkflowExecutionError("workflow description definition digest is stale")
    state = str(snapshot.get("state") or "")
    ready: list[dict[str, Any]] = []
    blocked = [copy.deepcopy(dict(item)) for item in snapshot.get("blocked_commands") or []]
    for raw in snapshot.get("allowed_commands") or []:
        command = copy.deepcopy(dict(raw))
        transition = compiled.by_source_command.get((state, str(command.get("command") or "")))
        if transition is None:
            raise WorkflowExecutionError("workflow description contains an undeclared command")
        activity = str(transition.descriptor["effect"].get("activity") or "").strip() or None
        status = executors.status(activity)
        command["executor"] = status
        if status["available"]:
            ready.append(command)
            continue
        blocked.append(
            {
                **command,
                "reason_code": str(status.get("reason_code") or "executor_unavailable"),
                "reason_key": "workflow.reason.executor_unavailable",
                "explanation": (
                    f"{command.get('command')} is valid for this state, but its registered "
                    "activity executor is unavailable."
                ),
            }
        )
    snapshot["allowed_commands"] = ready
    snapshot["blocked_commands"] = blocked
    snapshot["blockers"] = [
        {
            "command": item.get("command"),
            "reason_code": item.get("reason_code"),
            "reason_key": item.get("reason_key"),
        }
        for item in blocked
    ]
    snapshot["executor_readiness"] = {
        "ready": len(ready),
        "blocked": sum(1 for item in blocked if item.get("reason_code") == "executor_unavailable"),
    }
    return snapshot


def _authoritative_interaction_response(
    response: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    response_id = str(response.get("response_id") or "").strip()
    interaction_id = str(response.get("interaction_id") or "").strip()
    if not response_id or not interaction_id:
        raise WorkflowExecutionError("interaction response identity is required")
    stored_response = conversation_store.get_interaction_response(response_id)
    stored_interaction = conversation_store.get_interaction(interaction_id)
    if stored_response is None or stored_interaction is None:
        raise WorkflowExecutionError("authoritative interaction response is unavailable")
    if _digest(stored_response) != _digest(dict(response)):
        raise WorkflowExecutionError("interaction response differs from the durable record")
    return stored_interaction, stored_response


def prepare_interaction_invocation(
    response: Mapping[str, Any],
    *,
    source: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Convert any channel's durable response into one WorkflowCommand."""

    interaction, stored_response = _authoritative_interaction_response(response)
    if str(stored_response.get("status") or "") not in {"answered", "accepted"}:
        raise WorkflowExecutionError("only an answered interaction response may be invoked")
    consumed = dict(stored_response.get("consumed_command") or {})
    if not consumed:
        raise WorkflowExecutionError("interaction response does not contain a consumed command")
    workflow = _ref(interaction.get("workflow_ref"), fallback_kind="workflow")
    if workflow is None or not workflow.get("version"):
        raise WorkflowExecutionError("interaction is not bound to an exact workflow definition")
    if workflow.get("generation") is None:
        raise WorkflowExecutionError("interaction is not bound to a workflow generation")
    expected_generation = int(consumed.get("expected_generation") or 0)
    if expected_generation != int(workflow["generation"]):
        raise WorkflowExecutionError("interaction command workflow generation is inconsistent")
    values = copy.deepcopy(dict(stored_response.get("values") or {}))
    action_id = str(values.get("action_id") or "").strip()
    action = next(
        (
            dict(item)
            for item in interaction.get("actions") or []
            if str(item.get("action_id") or "") == action_id
        ),
        None,
    )
    if action is None:
        raise WorkflowExecutionError("interaction command is not bound to an authoritative action")
    if str(action.get("command") or "") != str(consumed.get("command") or ""):
        raise WorkflowExecutionError("interaction action command binding changed")
    if _digest(action.get("target_ref")) != _digest(consumed.get("target_ref")):
        raise WorkflowExecutionError("interaction action target binding changed")
    confirmation_required = bool(consumed.get("confirmation_required"))
    if confirmation_required and values.get("confirmed") is not True:
        raise WorkflowExecutionError("workflow command requires an explicit confirmed response")
    command_input = values.get("command_input")
    if isinstance(command_input, Mapping):
        input_value = copy.deepcopy(dict(command_input))
    else:
        ignored = {"action_id", "command", "value", "choice", "choices", "text"}
        input_value = {
            key: copy.deepcopy(value)
            for key, value in values.items()
            if key not in ignored
        }
    actor_id = str(stored_response.get("actor_id") or "").strip()
    command_context = _ref(action.get("command_context_ref"), fallback_kind="command_context")
    reply_route = _ref(interaction.get("reply_route_ref"), fallback_kind="reply_route")
    created_at = now or str(stored_response.get("created_at") or _now())
    command = validate_workflow_record(
        "adaos.workflow.command.v1",
        {
            "schema": "adaos.workflow.command.v1",
            "command_id": str(consumed.get("command") or "").strip(),
            "workflow_type": str(dict(interaction.get("metadata") or {}).get("workflow_type") or "").strip(),
            "instance_ref": workflow,
            "actor_ref": workflow_ref("principal", actor_id),
            "expected_generation": expected_generation,
            "idempotency_key": str(stored_response.get("idempotency_key") or "").strip(),
            "input": input_value,
            "context_ref": command_context,
            "reply_route_ref": reply_route,
            "created_at": created_at,
        },
    )
    source_meta = dict(stored_response.get("metadata") or {})
    selected_source = str(source or source_meta.get("io_type") or stored_response.get("source") or "system").strip().lower()
    if selected_source == "action":
        selected_source = "web"
    if selected_source not in {"web", "telegram", "text", "intent", "sdk", "system"}:
        selected_source = "system"
    record = {
        "schema": WORKFLOW_INVOCATION_SCHEMA,
        "invocation_id": f"invocation:{stored_response['response_id']}",
        "source": selected_source,
        "conversation_id": str(interaction.get("conversation_id") or "").strip() or None,
        "interaction_ref": workflow_ref(
            "interaction",
            str(interaction["interaction_id"]),
            generation=int(stored_response["interaction_generation"]),
        ),
        "response_ref": workflow_ref("interaction_response", str(stored_response["response_id"])),
        "target_ref": copy.deepcopy(consumed.get("target_ref")),
        "risk": str(consumed.get("risk") or "read"),
        "confirmation_required": confirmation_required,
        "command": command,
        "created_at": created_at,
        "metadata": {
            "presentation_id": stored_response.get("presentation_id"),
            "source_message_ref": copy.deepcopy(stored_response.get("source_message_ref")),
        },
    }
    return validate_workflow_record(WORKFLOW_INVOCATION_SCHEMA, record)


def prepare_sdk_invocation(
    *,
    workflow_type: str,
    instance_ref: Mapping[str, Any],
    actor_id: str,
    command_id: str,
    expected_generation: int,
    idempotency_key: str,
    input_value: Mapping[str, Any] | None = None,
    target_ref: Mapping[str, Any] | None = None,
    context_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    risk: str = "read",
    confirmation_required: bool = False,
    now: str | None = None,
) -> dict[str, Any]:
    """Build the same invocation record for an internal SDK caller."""

    timestamp = now or _now()
    instance = _ref(instance_ref, fallback_kind="workflow")
    if instance is None or not instance.get("version") or instance.get("generation") is None:
        raise WorkflowExecutionError("SDK invocation requires an exact workflow instance ref")
    command = validate_workflow_record(
        "adaos.workflow.command.v1",
        {
            "schema": "adaos.workflow.command.v1",
            "command_id": str(command_id or "").strip(),
            "workflow_type": str(workflow_type or "").strip(),
            "instance_ref": instance,
            "actor_ref": workflow_ref("principal", str(actor_id or "").strip()),
            "expected_generation": int(expected_generation),
            "idempotency_key": str(idempotency_key or "").strip(),
            "input": copy.deepcopy(dict(input_value or {})),
            "context_ref": _ref(context_ref, fallback_kind="command_context"),
            "reply_route_ref": _ref(reply_route_ref, fallback_kind="reply_route"),
            "created_at": timestamp,
        },
    )
    record = {
        "schema": WORKFLOW_INVOCATION_SCHEMA,
        "invocation_id": "invocation:sdk:" + _digest(command).removeprefix("sha256:"),
        "source": "sdk",
        "conversation_id": None,
        "interaction_ref": None,
        "response_ref": None,
        "target_ref": copy.deepcopy(dict(target_ref)) if target_ref is not None else None,
        "risk": str(risk),
        "confirmation_required": bool(confirmation_required),
        "command": command,
        "created_at": timestamp,
        "metadata": {},
    }
    return validate_workflow_record(WORKFLOW_INVOCATION_SCHEMA, record)


def _reply_route_ids(invocation: Mapping[str, Any]) -> list[str]:
    route = dict(dict(invocation["command"]).get("reply_route_ref") or {})
    route_id = str(route.get("id") or "").strip()
    return [route_id] if route_id else []


def execute_invocation(
    invocation: Mapping[str, Any],
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
    instance: Mapping[str, Any],
    *,
    principal: VerifiedWorkflowPrincipal,
    adapters: WorkflowAdapterRegistry,
    executors: WorkflowExecutorRegistry,
    context: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Resolve and durably admit a channel-neutral workflow invocation.

    External activities are only scheduled here. Their worker owns effect
    execution and terminal reporting, so transport retries cannot repeat work.
    """

    record = validate_workflow_record(WORKFLOW_INVOCATION_SCHEMA, invocation)
    command = dict(record["command"])
    compiled = definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)
    adapters.bind(compiled)
    if command["workflow_type"] != compiled.workflow_type:
        raise WorkflowExecutionError("invocation workflow type does not match definition")
    current = copy.deepcopy(dict(instance))
    if persist:
        stored = workflow_persistence.get_instance(str(command["instance_ref"]["id"]))
        if stored is None:
            workflow_persistence.create_instance(current)
        else:
            current = stored
    transition = compiled.by_source_command.get((str(current.get("state") or ""), str(command["command_id"])))
    if transition is None:
        status = {"available": True, "adapter_id": None, "executor_id": "adaos.workflow.pure_transition"}
    else:
        activity = str(transition.descriptor["effect"].get("activity") or "").strip() or None
        status = executors.status(activity)
        if not status.get("available"):
            return {
                "accepted": False,
                "status": "rejected",
                "reason_code": str(status.get("reason_code") or "executor_unavailable"),
                "invocation": record,
                "decision": None,
                "commit": None,
                "responses": [],
            }
    decision = apply_workflow_command(
        compiled,
        current,
        command,
        resolver=WorkflowResolver(require_verified_principal=True),
        principal=principal,
        context=context,
    )
    if not decision["accepted"]:
        return {
            "accepted": False,
            "status": "rejected",
            "reason_code": decision["reason_code"],
            "invocation": record,
            "decision": decision,
            "commit": None,
            "responses": [],
        }
    commit: dict[str, Any] | None = None
    if decision["status"] == "accepted" and persist:
        activity = str(dict(decision.get("activity") or {}).get("activity") or "").strip()
        effect_binding = None
        if activity:
            effect_binding = {
                "activity": activity,
                "executor": status.get("executor_id"),
                "contract_digest": status.get("contract_digest"),
            }
        target = dict(record.get("target_ref") or {})
        target_digest = str(target.get("digest") or "").strip() or None
        commit = workflow_persistence.commit_decision(
            decision,
            idempotency_key=str(command["idempotency_key"]),
            permission_granted=True,
            target_digest=target_digest,
            expected_target_digest=target_digest,
            approval_required=bool(record["confirmation_required"]),
            approval_witness=(
                {
                    "actor": command["actor_ref"]["id"],
                    "interaction_ref": copy.deepcopy(record.get("interaction_ref")),
                    "response_ref": copy.deepcopy(record.get("response_ref")),
                }
                if record["confirmation_required"]
                else None
            ),
            effect_binding=effect_binding,
        )
    route_ids = _reply_route_ids(record)
    responses: list[dict[str, Any]] = []
    async_reply = dict(decision.get("async_reply") or {})
    if async_reply.get("mode") != "none" and record.get("conversation_id"):
        common = {
            "workflow_ref": copy.deepcopy(command["instance_ref"]),
            "interaction_ref": copy.deepcopy(record.get("interaction_ref")),
            "command_id": str(command["command_id"]),
            "reply_route_ids": route_ids,
        }
        responses.append(
            durable_delivery.enqueue_response(
                str(record["conversation_id"]),
                "accepted",
                text=str(decision.get("explanation") or "Command accepted."),
                data={"invocation_id": record["invocation_id"]},
                envelope_id=f"response:{record['invocation_id']}:accepted",
                **common,
            )
        )
        if not dict(decision.get("activity") or {}).get("activity"):
            responses.append(
                durable_delivery.enqueue_response(
                    str(record["conversation_id"]),
                    "terminal",
                    text=str(decision.get("explanation") or "Command completed."),
                    data={
                        "invocation_id": record["invocation_id"],
                        "state": dict(decision["after"])["state"],
                        "generation": dict(decision["after"])["generation"],
                    },
                    envelope_id=f"response:{record['invocation_id']}:terminal",
                    **common,
                )
            )
    return {
        "accepted": True,
        "status": decision["status"],
        "reason_code": decision.get("reason_code"),
        "invocation": record,
        "decision": decision,
        "commit": commit,
        "responses": responses,
    }


__all__ = [
    "WORKFLOW_INVOCATION_SCHEMA",
    "WorkflowExecutionError",
    "WorkflowExecutorRegistration",
    "WorkflowExecutorRegistry",
    "description_with_executor_readiness",
    "execute_invocation",
    "prepare_interaction_invocation",
    "prepare_sdk_invocation",
]
