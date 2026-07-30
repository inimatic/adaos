from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator


WORKFLOW_DEFINITION_SCHEMA = "adaos.workflow.definition.v1"
WORKFLOW_TRANSITION_SCHEMA = "adaos.workflow.transition.v1"
WORKFLOW_INSTANCE_SCHEMA = "adaos.workflow.instance.v1"
WORKFLOW_DECISION_SCHEMA = "adaos.workflow.decision.v1"
_MAX_LEDGER = 200


class WorkflowDefinitionError(ValueError):
    """Raised when a workflow definition is incomplete or inconsistent."""


class WorkflowResolutionError(ValueError):
    """Raised when an instance cannot be safely resolved against a definition."""


Guard = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Any]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _abi_schema(name: str) -> dict[str, Any]:
    filename = name.removeprefix("adaos.")
    path = Path(__file__).resolve().parents[1] / "abi" / f"{filename}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(schema_name: str, value: Mapping[str, Any]) -> None:
    validator = Draft202012Validator(_abi_schema(schema_name))
    errors = sorted(validator.iter_errors(dict(value)), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(item) for item in first.absolute_path) or "$"
    raise WorkflowDefinitionError(f"{schema_name} validation failed at {location}: {first.message}")


def _sources(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value or [])


def _actor_matches(allowed: tuple[str, ...], actor: str) -> bool:
    if "*" in allowed or actor in allowed:
        return True
    kind = actor.split(":", 1)[0]
    return kind in allowed or f"{kind}:*" in allowed


@dataclass(frozen=True, slots=True)
class CompiledTransition:
    transition_id: str
    sources: tuple[str, ...]
    target: str
    command: str
    descriptor: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CompiledWorkflowDefinition:
    workflow_type: str
    definition_version: str
    aggregate_type: str
    initial_state: str
    states: dict[str, dict[str, Any]]
    commands: dict[str, dict[str, Any]]
    transitions: tuple[CompiledTransition, ...]
    by_source_command: dict[tuple[str, str], CompiledTransition]
    source: dict[str, Any]


def compile_definition(value: Mapping[str, Any]) -> CompiledWorkflowDefinition:
    """Validate and compile a declarative workflow into deterministic indexes."""

    definition = copy.deepcopy(dict(value))
    _validate(WORKFLOW_DEFINITION_SCHEMA, definition)
    states = {str(item["id"]): dict(item) for item in definition["states"]}
    if len(states) != len(definition["states"]):
        raise WorkflowDefinitionError("workflow state ids must be unique")
    if definition["initial_state"] not in states:
        raise WorkflowDefinitionError("initial_state must identify a declared state")
    commands = {str(item["id"]): dict(item) for item in definition["commands"]}
    if len(commands) != len(definition["commands"]):
        raise WorkflowDefinitionError("workflow command ids must be unique")

    transitions: list[CompiledTransition] = []
    by_source_command: dict[tuple[str, str], CompiledTransition] = {}
    transition_ids: set[str] = set()
    for raw in definition["transitions"]:
        if not isinstance(raw, Mapping):
            raise WorkflowDefinitionError("workflow transitions must be objects")
        descriptor = copy.deepcopy(dict(raw))
        _validate(WORKFLOW_TRANSITION_SCHEMA, descriptor)
        transition_id = str(descriptor["transition_id"])
        if transition_id in transition_ids:
            raise WorkflowDefinitionError(f"duplicate transition_id: {transition_id}")
        transition_ids.add(transition_id)
        sources = _sources(descriptor["source"])
        missing_sources = [source for source in sources if source not in states]
        if missing_sources:
            raise WorkflowDefinitionError(
                f"transition {transition_id} has unknown source states: {', '.join(missing_sources)}"
            )
        target = str(descriptor["target"])
        if target not in states:
            raise WorkflowDefinitionError(f"transition {transition_id} has unknown target state: {target}")
        command = str(descriptor["trigger"]["command"])
        if command not in commands:
            raise WorkflowDefinitionError(f"transition {transition_id} uses undeclared command: {command}")
        if descriptor["trigger"]["input_schema"] != commands[command]["input_schema"]:
            raise WorkflowDefinitionError(
                f"transition {transition_id} input_schema differs from command {command}"
            )
        compiled = CompiledTransition(transition_id, sources, target, command, descriptor)
        transitions.append(compiled)
        for source in sources:
            key = (source, command)
            if key in by_source_command:
                raise WorkflowDefinitionError(
                    f"ambiguous command {command} in state {source}: "
                    f"{by_source_command[key].transition_id}, {transition_id}"
                )
            by_source_command[key] = compiled

    for state_id, state in states.items():
        if state.get("terminal") and any(source == state_id for source, _command in by_source_command):
            raise WorkflowDefinitionError(f"terminal state {state_id} must not have outgoing transitions")

    return CompiledWorkflowDefinition(
        workflow_type=str(definition["workflow_type"]),
        definition_version=str(definition["definition_version"]),
        aggregate_type=str(definition["aggregate_type"]),
        initial_state=str(definition["initial_state"]),
        states=states,
        commands=commands,
        transitions=tuple(transitions),
        by_source_command=by_source_command,
        source=definition,
    )


def new_instance(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
    instance_id: str,
    *,
    context: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    compiled = definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)
    token = str(instance_id or "").strip()
    if not token:
        raise WorkflowResolutionError("instance_id is required")
    timestamp = now or _now()
    return {
        "schema": WORKFLOW_INSTANCE_SCHEMA,
        "instance_id": token,
        "workflow_type": compiled.workflow_type,
        "definition_version": compiled.definition_version,
        "state": compiled.initial_state,
        "generation": 0,
        "context": copy.deepcopy(dict(context or {})),
        "history": [],
        "idempotency": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _guard_always(
    _instance: Mapping[str, Any],
    _input_value: Mapping[str, Any],
    _context: Mapping[str, Any],
    _params: Mapping[str, Any],
) -> bool:
    return True


def _guard_context_equals(
    _instance: Mapping[str, Any],
    _input_value: Mapping[str, Any],
    context: Mapping[str, Any],
    params: Mapping[str, Any],
) -> bool:
    return context.get(str(params.get("field") or "")) == params.get("value")


def _guard_instance_context_equals(
    instance: Mapping[str, Any],
    _input_value: Mapping[str, Any],
    _context: Mapping[str, Any],
    params: Mapping[str, Any],
) -> bool:
    return dict(instance.get("context") or {}).get(str(params.get("field") or "")) == params.get("value")


DEFAULT_GUARDS: dict[str, Guard] = {
    "always": _guard_always,
    "context_equals": _guard_context_equals,
    "instance_context_equals": _guard_instance_context_equals,
}


@dataclass(slots=True)
class WorkflowResolver:
    guards: Mapping[str, Guard] | None = None

    def __post_init__(self) -> None:
        self.guards = {**DEFAULT_GUARDS, **dict(self.guards or {})}

    @staticmethod
    def _compiled(value: CompiledWorkflowDefinition | Mapping[str, Any]) -> CompiledWorkflowDefinition:
        return value if isinstance(value, CompiledWorkflowDefinition) else compile_definition(value)

    @staticmethod
    def _instance(compiled: CompiledWorkflowDefinition, value: Mapping[str, Any]) -> dict[str, Any]:
        instance = copy.deepcopy(dict(value))
        try:
            _validate(WORKFLOW_INSTANCE_SCHEMA, instance)
        except WorkflowDefinitionError as exc:
            raise WorkflowResolutionError(str(exc)) from exc
        if instance["workflow_type"] != compiled.workflow_type:
            raise WorkflowResolutionError("workflow instance type does not match the definition")
        if instance["definition_version"] != compiled.definition_version:
            raise WorkflowResolutionError("workflow instance definition version is not pinned to this definition")
        if instance["state"] not in compiled.states:
            raise WorkflowResolutionError("workflow instance state is not declared by the definition")
        return instance

    def _guard_result(
        self,
        transition: CompiledTransition,
        instance: Mapping[str, Any],
        input_value: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> tuple[bool, str | None]:
        for descriptor in transition.descriptor["guards"]:
            guard_id = str(descriptor["id"])
            guard = dict(self.guards or {}).get(guard_id)
            if guard is None:
                raise WorkflowResolutionError(f"workflow guard is not registered: {guard_id}")
            result = guard(instance, input_value, context, descriptor["params"])
            accepted = bool(result[0]) if isinstance(result, tuple) else bool(result)
            reason = str(result[1]) if isinstance(result, tuple) and len(result) > 1 else None
            if not accepted:
                return False, reason or str(descriptor["reason_code"])
        return True, None

    @staticmethod
    def _authority_result(
        transition: CompiledTransition,
        *,
        actor: str,
        permissions: tuple[str, ...],
    ) -> tuple[bool, str | None]:
        authority = transition.descriptor["authority"]
        if not _actor_matches(tuple(authority["actors"]), actor):
            return False, "actor_not_authorized"
        missing = sorted(set(authority["permissions"]) - set(permissions))
        if missing:
            return False, f"missing_permission:{missing[0]}"
        return True, None

    def describe(
        self,
        definition: CompiledWorkflowDefinition | Mapping[str, Any],
        instance: Mapping[str, Any],
        *,
        actor: str,
        permissions: tuple[str, ...] | list[str] = (),
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        compiled = self._compiled(definition)
        current = self._instance(compiled, instance)
        runtime_context = dict(context or {})
        allowed: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for transition in compiled.transitions:
            if current["state"] not in transition.sources:
                continue
            accepted, reason = self._authority_result(
                transition,
                actor=actor,
                permissions=tuple(permissions),
            )
            if accepted:
                accepted, reason = self._guard_result(transition, current, {}, runtime_context)
            projection = {
                "command": transition.command,
                "transition_id": transition.transition_id,
                "target": transition.target,
                "risk": copy.deepcopy(transition.descriptor["risk"]),
                "capability_requirements": copy.deepcopy(
                    transition.descriptor["capability_requirements"]
                ),
                "explanation": transition.descriptor["explanations"]["allowed" if accepted else "rejected"],
            }
            if accepted:
                allowed.append(projection)
            else:
                blocked.append({**projection, "reason_code": reason})
        return {
            "schema": "adaos.workflow.description.v1",
            "workflow_type": compiled.workflow_type,
            "definition_version": compiled.definition_version,
            "instance_id": current["instance_id"],
            "state": current["state"],
            "generation": current["generation"],
            "terminal": bool(compiled.states[current["state"]].get("terminal")),
            "allowed_commands": allowed,
            "blocked_commands": blocked,
        }

    def apply(
        self,
        definition: CompiledWorkflowDefinition | Mapping[str, Any],
        instance: Mapping[str, Any],
        command: str,
        *,
        input_value: Mapping[str, Any] | None = None,
        actor: str,
        permissions: tuple[str, ...] | list[str] = (),
        expected_generation: int | None = None,
        idempotency_key: str | None = None,
        context: Mapping[str, Any] | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        compiled = self._compiled(definition)
        current = self._instance(compiled, instance)
        payload = copy.deepcopy(dict(input_value or {}))
        timestamp = now or _now()
        command_token = str(command or "").strip()
        key = str(idempotency_key or "").strip()
        payload_digest = _digest({"command": command_token, "input": payload})
        if key:
            previous = next(
                (item for item in current["idempotency"] if item.get("key") == key),
                None,
            )
            if previous:
                if previous.get("payload_digest") != payload_digest:
                    return self._rejection(current, command_token, "idempotency_conflict", timestamp)
                return {
                    "schema": WORKFLOW_DECISION_SCHEMA,
                    "accepted": True,
                    "status": "duplicate",
                    "reason_code": "already_applied",
                    "command": command_token,
                    "transition_id": previous.get("transition_id"),
                    "before": copy.deepcopy(current),
                    "after": copy.deepcopy(current),
                    "activity": None,
                    "events": [],
                    "async_reply": copy.deepcopy(previous.get("async_reply") or {"mode": "none", "reply_route": "none"}),
                    "explanation": str(previous.get("explanation") or "Command was already applied."),
                    "decided_at": timestamp,
                }
        transition = compiled.by_source_command.get((current["state"], command_token))
        if transition is None:
            return self._rejection(current, command_token, "command_not_allowed", timestamp)

        input_errors = sorted(
            Draft202012Validator(compiled.commands[command_token]["input_schema"]).iter_errors(payload),
            key=lambda item: list(item.absolute_path),
        )
        if input_errors:
            location = ".".join(str(item) for item in input_errors[0].absolute_path) or "$"
            return self._rejection(current, command_token, f"invalid_input:{location}", timestamp)

        concurrency = transition.descriptor["concurrency"]
        if concurrency["requires_generation"]:
            if expected_generation is None:
                return self._rejection(current, command_token, "expected_generation_required", timestamp)
            if int(expected_generation) != int(current["generation"]):
                return self._rejection(current, command_token, "stale_generation", timestamp)

        if concurrency["idempotency"] == "required" and not key:
            return self._rejection(current, command_token, "idempotency_key_required", timestamp)

        accepted, reason = self._authority_result(
            transition,
            actor=actor,
            permissions=tuple(permissions),
        )
        if not accepted:
            return self._rejection(current, command_token, reason or "not_authorized", timestamp)
        accepted, reason = self._guard_result(transition, current, payload, dict(context or {}))
        if not accepted:
            return self._rejection(current, command_token, reason or "guard_rejected", timestamp)
        confirmation = transition.descriptor["risk"]["confirmation"]
        if confirmation != "none" and not bool(payload.get("confirmed")):
            return self._rejection(current, command_token, "confirmation_required", timestamp)
        evidence = dict(transition.descriptor.get("evidence") or {})
        evidence_refs = payload.get("evidence_refs") or []
        if evidence.get("required") and len(evidence_refs) < int(evidence.get("minimum") or 1):
            return self._rejection(current, command_token, "evidence_required", timestamp)

        updated = copy.deepcopy(current)
        updated["state"] = transition.target
        updated["generation"] = int(updated["generation"]) + 1
        updated["updated_at"] = timestamp
        history_entry = {
            "generation": updated["generation"],
            "command": command_token,
            "transition_id": transition.transition_id,
            "actor": actor,
            "from": current["state"],
            "to": transition.target,
            "input_digest": _digest(payload),
            "at": timestamp,
        }
        updated["history"] = [*updated["history"], history_entry][-_MAX_LEDGER:]
        if key:
            updated["idempotency"] = [
                *updated["idempotency"],
                {
                    "key": key,
                    "payload_digest": payload_digest,
                    "transition_id": transition.transition_id,
                    "generation": updated["generation"],
                    "async_reply": copy.deepcopy(transition.descriptor["async_reply"]),
                    "explanation": transition.descriptor["explanations"]["completed"],
                    "at": timestamp,
                },
            ][-_MAX_LEDGER:]
        return {
            "schema": WORKFLOW_DECISION_SCHEMA,
            "accepted": True,
            "status": "accepted",
            "reason_code": None,
            "command": command_token,
            "transition_id": transition.transition_id,
            "before": current,
            "after": updated,
            "activity": copy.deepcopy(transition.descriptor["effect"]),
            "events": copy.deepcopy(transition.descriptor["events"]),
            "async_reply": copy.deepcopy(transition.descriptor["async_reply"]),
            "explanation": transition.descriptor["explanations"]["completed"],
            "decided_at": timestamp,
        }

    @staticmethod
    def _rejection(
        instance: Mapping[str, Any],
        command: str,
        reason: str,
        timestamp: str,
    ) -> dict[str, Any]:
        snapshot = copy.deepcopy(dict(instance))
        return {
            "schema": WORKFLOW_DECISION_SCHEMA,
            "accepted": False,
            "status": "rejected",
            "reason_code": reason,
            "command": command,
            "transition_id": None,
            "before": snapshot,
            "after": copy.deepcopy(snapshot),
            "activity": None,
            "events": [],
            "async_reply": {"mode": "none", "reply_route": "none"},
            "explanation": reason.replace("_", " "),
            "decided_at": timestamp,
        }


def workflow_contract_snapshot() -> dict[str, Any]:
    return {
        "schema_version": "adaos.workflow.contract.v1",
        "records": {
            "WorkflowDefinition": WORKFLOW_DEFINITION_SCHEMA,
            "TransitionDescriptor": WORKFLOW_TRANSITION_SCHEMA,
            "WorkflowInstance": WORKFLOW_INSTANCE_SCHEMA,
            "WorkflowDecision": WORKFLOW_DECISION_SCHEMA,
        },
        "invariants": {
            "resolver": "pure",
            "effects": "described_not_executed",
            "definition_version": "pinned_per_instance",
            "concurrency": "generation_guarded",
            "idempotency": "payload_bound",
        },
    }
