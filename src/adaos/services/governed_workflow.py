from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


WORKFLOW_DEFINITION_SCHEMA = "adaos.workflow.definition.v1"
WORKFLOW_TRANSITION_SCHEMA = "adaos.workflow.transition.v1"
WORKFLOW_INSTANCE_SCHEMA = "adaos.workflow.instance.v1"
WORKFLOW_REF_SCHEMA = "adaos.workflow.ref.v1"
WORKFLOW_COMMAND_SCHEMA = "adaos.workflow.command.v1"
WORKFLOW_EVENT_SCHEMA = "adaos.workflow.event.v1"
WORKFLOW_DEFINITION_MIGRATION_SCHEMA = "adaos.workflow.definition_migration.v1"
WORKFLOW_COMPOSITION_SCHEMA = "adaos.workflow.composition.v1"
WORKFLOW_DECISION_SCHEMA = "adaos.workflow.decision.v1"
WORKFLOW_VALIDATION_REPORT_SCHEMA = "adaos.workflow.validation_report.v1"
WORKFLOW_ADAPTER_CONTRACT_SCHEMA = "adaos.workflow.adapter_contract.v1"
WORKFLOW_REGISTRY_ENTRY_SCHEMA = "adaos.workflow.registry_entry.v1"
WORKFLOW_BINDING_SCHEMA = "adaos.workflow.binding.v1"
WORKFLOW_PRINCIPAL_SCHEMA = "adaos.workflow.principal.v1"
_MAX_LEDGER = 200


class WorkflowDefinitionError(ValueError):
    """Raised when a workflow definition is incomplete or inconsistent."""


class WorkflowResolutionError(ValueError):
    """Raised when an instance cannot be safely resolved against a definition."""


Guard = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Any]


class WorkflowInstanceStore(Protocol):
    """Persistence port; implementations must compare-and-swap generation."""

    def load(self, instance_id: str) -> Mapping[str, Any] | None: ...

    def compare_and_swap(
        self,
        instance_id: str,
        *,
        expected_generation: int,
        snapshot: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
    ) -> bool: ...


class WorkflowActivityDispatcher(Protocol):
    """Execution port; the pure resolver only returns dispatch intent."""

    def dispatch(
        self,
        activity: Mapping[str, Any],
        *,
        command: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _abi_schema(name: str) -> dict[str, Any]:
    filename = name.removeprefix("adaos.")
    path = Path(__file__).resolve().parents[1] / "abi" / f"{filename}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _abi_registry() -> Registry:
    root = Path(__file__).resolve().parents[1] / "abi"
    registry = Registry()
    for path in sorted(root.glob("workflow.*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema)
        registry = registry.with_resource(path.name, resource)
        schema_id = str(schema.get("$id") or "").strip()
        if schema_id:
            registry = registry.with_resource(schema_id, resource)
    return registry


def _validate(schema_name: str, value: Mapping[str, Any]) -> None:
    validator = Draft202012Validator(_abi_schema(schema_name), registry=_abi_registry())
    errors = sorted(validator.iter_errors(dict(value)), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(item) for item in first.absolute_path) or "$"
    raise WorkflowDefinitionError(f"{schema_name} validation failed at {location}: {first.message}")


def workflow_schema_diagnostics(
    schema_name: str,
    value: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return stable, machine-readable ABI diagnostics without weakening admission."""

    validator = Draft202012Validator(_abi_schema(schema_name), registry=_abi_registry())
    errors = sorted(
        validator.iter_errors(dict(value)),
        key=lambda item: (list(item.absolute_path), item.validator, item.message),
    )
    diagnostics: list[dict[str, Any]] = []
    for error in errors:
        path = "$" + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}"
            for item in error.absolute_path
        )
        diagnostics.append(
            {
                "code": f"workflow.schema.{str(error.validator).replace('$', 'ref')}",
                "severity": "error",
                "path": path,
                "message": error.message,
                "details": {
                    "validator": str(error.validator),
                    "schema_path": "/".join(str(item) for item in error.absolute_schema_path),
                },
            }
        )
    return diagnostics


def _sources(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value or [])


def normalize_transition_descriptor(
    value: Mapping[str, Any],
    *,
    definition_version: str,
) -> dict[str, Any]:
    """Materialize the complete normative TransitionDescriptor.

    Workflow authors may omit protocol values that have one safe platform
    default. The compiler records every such value in the executable
    descriptor so the resolver, reviews, audit tools, and generated clients do
    not independently invent transition semantics.
    """

    descriptor = copy.deepcopy(dict(value))
    descriptor["version"] = str(
        descriptor.get("version")
        or dict(descriptor.get("migration") or {}).get("introduced_in")
        or definition_version
    )
    sources = list(_sources(descriptor.get("source")))
    descriptor.setdefault(
        "source_selector",
        {"states": sources, "predicate": None},
    )
    descriptor.setdefault("invariants", [])

    concurrency = descriptor.setdefault("concurrency", {})
    scope = str(concurrency.get("conflict_scope") or "aggregate")
    concurrency.setdefault("conflict_key", f"{scope}:{{instance_id}}")
    concurrency.setdefault(
        "expected_generation",
        "required" if concurrency.get("requires_generation") else "not_applicable",
    )
    idempotency_mode = str(concurrency.get("idempotency") or "not_applicable")
    descriptor.setdefault(
        "idempotency_contract",
        {
            "mode": idempotency_mode,
            "key_scope": (
                "transition_instance" if idempotency_mode != "not_applicable" else "none"
            ),
            "result_reuse": (
                "return_recorded_outcome"
                if idempotency_mode != "not_applicable"
                else "not_applicable"
            ),
            "ttl_seconds": None,
        },
    )

    effect = descriptor.setdefault("effect", {})
    effect.setdefault("input_schema", copy.deepcopy(dict(descriptor.get("trigger") or {}).get("input_schema") or {}))
    effect.setdefault("output_schema", {"type": "object"})
    effect.setdefault("activity_params", {})
    effect.setdefault("compensation_params", {})
    effect.setdefault(
        "transaction_boundary",
        {
            "none": "none",
            "atomic": "aggregate",
            "outbox": "aggregate_and_outbox",
        }.get(str(effect.get("transaction") or "none"), "external_saga"),
    )

    recovery = descriptor.setdefault("recovery", {})
    retry_mode = str(effect.get("retry") or "never")
    recovery.setdefault(
        "retry_policy",
        {
            "mode": retry_mode,
            "max_attempts": 3 if retry_mode == "bounded" else None,
            "backoff": "exponential" if retry_mode == "bounded" else "none",
            "retryable_reason_codes": [],
        },
    )

    outcomes = descriptor.setdefault("outcomes", {})
    outcomes.setdefault(
        "reason_codes",
        {
            "success": "transition_succeeded",
            "failure": "transition_failed",
            "input_required": "transition_input_required",
            "cancelled": "transition_cancelled",
            "unknown": "transition_outcome_unknown",
        },
    )
    outcomes.setdefault("terminal_result_once", True)

    evidence = descriptor.setdefault("evidence", {})
    evidence.setdefault("types", [])
    evidence.setdefault("immutable_refs", True)
    approval = descriptor.setdefault("approval", {})
    approval.setdefault("mode", "single" if approval.get("required") else "none")

    async_reply = descriptor.setdefault("async_reply", {})
    reply_mode = str(async_reply.get("mode") or "none")
    async_reply.setdefault("acknowledge_acceptance", reply_mode != "none")
    async_reply.setdefault("terminal_once", True)
    async_reply.setdefault("delivery_retry_without_execution", True)
    async_reply.setdefault("resume_after_restart", True)
    async_reply.setdefault("late_result_policy", "origin_thread")
    async_reply.setdefault(
        "progress",
        {
            "ordered": True,
            "coalesce": reply_mode == "progress_and_terminal",
            "rate_limit_seconds": 2 if reply_mode == "progress_and_terminal" else 0,
        },
    )
    async_reply.setdefault("route_expiry", "query_only")

    capabilities = descriptor.setdefault("capability_requirements", {})
    capabilities.setdefault("fail_closed", True)
    capabilities.setdefault("semantic_equivalence_required", True)

    explanations = descriptor.setdefault("explanations", {})
    allowed = str(explanations.get("allowed") or "Transition is available")
    rejected = str(explanations.get("rejected") or "Transition is blocked")
    completed = str(explanations.get("completed") or "Transition completed")
    explanations.setdefault("available", allowed)
    explanations.setdefault("blocked", rejected)
    explanations.setdefault("running", f"{allowed}; work is running")
    explanations.setdefault("failed", f"{rejected}; execution failed")
    explanations.setdefault("input_required", f"{rejected}; input is required")
    explanations.setdefault("unknown", f"{rejected}; outcome requires reconciliation")

    events = descriptor.setdefault("events", {})
    events.setdefault("correlation_required", True)
    events.setdefault("causation_required", True)
    observability = descriptor.setdefault("observability", {})
    observability.setdefault("correlation_id_required", True)
    observability.setdefault("causation_id_required", True)
    observability.setdefault("actor_id_required", True)
    migration = descriptor.setdefault("migration", {})
    migration.setdefault("policy", "pin_in_flight")
    migration.setdefault("compatible_from", [])
    migration.setdefault("migration_ref", None)
    return descriptor


def _actor_matches(allowed: tuple[str, ...], actor: str) -> bool:
    if "*" in allowed or actor in allowed:
        return True
    kind = actor.split(":", 1)[0]
    return kind in allowed or f"{kind}:*" in allowed


def workflow_definition_digest(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
) -> str:
    source = definition.source if isinstance(definition, CompiledWorkflowDefinition) else definition
    return _digest(dict(source))


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


def compile_definition(
    value: Mapping[str, Any],
    *,
    registered_guards: set[str] | frozenset[str] | None = None,
    registered_activities: set[str] | frozenset[str] | None = None,
) -> CompiledWorkflowDefinition:
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
        descriptor = normalize_transition_descriptor(
            raw,
            definition_version=str(definition["definition_version"]),
        )
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
        if registered_guards is not None:
            unknown_guards = sorted(
                str(item["id"])
                for item in descriptor["guards"]
                if str(item["id"]) not in registered_guards
            )
            if unknown_guards:
                raise WorkflowDefinitionError(
                    f"transition {transition_id} uses unregistered guards: {', '.join(unknown_guards)}"
                )
        activity = descriptor["effect"].get("activity")
        if activity and registered_activities is not None and activity not in registered_activities:
            raise WorkflowDefinitionError(
                f"transition {transition_id} uses unregistered activity: {activity}"
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
        if state.get("waiting") and not str(state.get("wait_explanation") or "").strip():
            raise WorkflowDefinitionError(f"waiting state {state_id} requires wait_explanation")

    if not any(bool(state.get("terminal")) for state in states.values()):
        raise WorkflowDefinitionError("workflow must declare at least one terminal state")
    reachable = {str(definition["initial_state"])}
    changed = True
    while changed:
        changed = False
        for transition in transitions:
            if any(source in reachable for source in transition.sources) and transition.target not in reachable:
                reachable.add(transition.target)
                changed = True
    unreachable = sorted(set(states) - reachable)
    if unreachable:
        raise WorkflowDefinitionError(f"workflow has unreachable states: {', '.join(unreachable)}")

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
    package_digest: str | None = None,
    binding_digest: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    compiled = definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)
    token = str(instance_id or "").strip()
    if not token:
        raise WorkflowResolutionError("instance_id is required")
    timestamp = now or _now()
    instance = {
        "schema": WORKFLOW_INSTANCE_SCHEMA,
        "instance_id": token,
        "workflow_type": compiled.workflow_type,
        "definition_version": compiled.definition_version,
        "definition_digest": workflow_definition_digest(compiled),
        "state": compiled.initial_state,
        "generation": 0,
        "context": copy.deepcopy(dict(context or {})),
        "history": [],
        "idempotency": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    if package_digest is not None:
        instance["package_digest"] = str(package_digest).strip()
    if binding_digest is not None:
        instance["binding_digest"] = str(binding_digest).strip()
    _validate(WORKFLOW_INSTANCE_SCHEMA, instance)
    return instance


def workflow_ref(
    kind: str,
    id: str,
    *,
    version: str | None = None,
    generation: int | None = None,
    digest: str | None = None,
) -> dict[str, Any]:
    value = {
        "schema": WORKFLOW_REF_SCHEMA,
        "kind": str(kind or "").strip(),
        "id": str(id or "").strip(),
        "version": str(version).strip() if version is not None else None,
        "generation": generation,
        "digest": str(digest).strip() if digest is not None else None,
    }
    _validate(WORKFLOW_REF_SCHEMA, value)
    return value


def workflow_command(
    command_id: str,
    *,
    instance_id: str,
    workflow_type: str,
    definition_version: str,
    actor_id: str,
    expected_generation: int,
    idempotency_key: str,
    input_value: Mapping[str, Any] | None = None,
    context_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    value = {
        "schema": WORKFLOW_COMMAND_SCHEMA,
        "command_id": str(command_id or "").strip(),
        "workflow_type": str(workflow_type or "").strip(),
        "instance_ref": workflow_ref(
            "workflow",
            instance_id,
            version=definition_version,
            generation=expected_generation,
        ),
        "actor_ref": workflow_ref("principal", actor_id),
        "expected_generation": int(expected_generation),
        "idempotency_key": str(idempotency_key or "").strip(),
        "input": copy.deepcopy(dict(input_value or {})),
        "context_ref": copy.deepcopy(dict(context_ref)) if context_ref is not None else None,
        "reply_route_ref": copy.deepcopy(dict(reply_route_ref)) if reply_route_ref is not None else None,
        "created_at": created_at or _now(),
    }
    if value["instance_ref"]["id"] != str(instance_id).strip():
        raise WorkflowResolutionError("instance_id is required")
    _validate(WORKFLOW_COMMAND_SCHEMA, value)
    return value


def validate_workflow_record(schema_name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    _validate(schema_name, value)
    return copy.deepcopy(dict(value))


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


@dataclass(frozen=True, slots=True)
class VerifiedWorkflowPrincipal:
    actor_id: str
    issuer: str
    subject: str
    authentication: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    claims_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_PRINCIPAL_SCHEMA,
            "actor_id": self.actor_id,
            "issuer": self.issuer,
            "subject": self.subject,
            "authentication": self.authentication,
            "roles": list(self.roles),
            "permissions": list(self.permissions),
            "claims_digest": self.claims_digest,
        }


def verified_workflow_principal(
    actor_id: str,
    *,
    authenticated: bool,
    issuer: str,
    subject: str | None = None,
    permissions: Sequence[str] = (),
) -> VerifiedWorkflowPrincipal:
    """Derive coarse workflow roles from trusted authentication, never caller input."""

    actor = str(actor_id or "").strip()
    issuer_token = str(issuer or "").strip()
    subject_token = str(subject or actor).strip()
    if not actor or not issuer_token or not subject_token:
        raise WorkflowResolutionError("verified workflow principal identity is incomplete")
    claims = {
        "actor_id": actor,
        "issuer": issuer_token,
        "subject": subject_token,
        "authentication": "verified" if authenticated else "anonymous",
        "roles": ["registered" if authenticated else "guest"],
        "permissions": sorted({str(item) for item in permissions if str(item).strip()}),
    }
    principal = VerifiedWorkflowPrincipal(
        actor_id=actor,
        issuer=issuer_token,
        subject=subject_token,
        authentication=str(claims["authentication"]),
        roles=tuple(claims["roles"]),
        permissions=tuple(claims["permissions"]),
        claims_digest=_digest(claims),
    )
    _validate(WORKFLOW_PRINCIPAL_SCHEMA, principal.to_dict())
    return principal


@dataclass(slots=True)
class WorkflowResolver:
    guards: Mapping[str, Guard] | None = None
    require_verified_principal: bool = False

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
        bound_digest = str(instance.get("definition_digest") or "").strip()
        if bound_digest and bound_digest != workflow_definition_digest(compiled):
            raise WorkflowResolutionError("workflow instance definition digest does not match the definition")
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

    def _principal_result(
        self,
        *,
        actor: str,
        permissions: tuple[str, ...],
        roles: tuple[str, ...],
        principal: VerifiedWorkflowPrincipal | None,
    ) -> tuple[str, tuple[str, ...], tuple[str, ...], str | None]:
        if principal is None:
            if self.require_verified_principal:
                return actor, (), (), "unverified_principal"
            return actor, permissions, roles, None
        try:
            _validate(WORKFLOW_PRINCIPAL_SCHEMA, principal.to_dict())
        except (AttributeError, WorkflowDefinitionError):
            return actor, (), (), "invalid_principal_claims"
        unsigned = principal.to_dict()
        supplied_digest = str(unsigned.pop("claims_digest"))
        unsigned.pop("schema", None)
        if supplied_digest != _digest(unsigned):
            return actor, (), (), "invalid_principal_claims"
        if principal.actor_id != actor:
            return actor, (), (), "principal_actor_mismatch"
        return (
            principal.actor_id,
            tuple(principal.permissions),
            tuple(principal.roles),
            None,
        )

    @staticmethod
    def _authority_result(
        transition: CompiledTransition,
        *,
        actor: str,
        permissions: tuple[str, ...],
        roles: tuple[str, ...],
    ) -> tuple[bool, str | None]:
        authority = transition.descriptor["authority"]
        if not _actor_matches(tuple(authority["actors"]), actor):
            return False, "actor_not_authorized"
        admitted_roles = tuple(str(item) for item in authority.get("roles") or ())
        if admitted_roles and not set(admitted_roles).intersection(roles):
            return False, f"role_not_authorized:{admitted_roles[0]}"
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
        roles: tuple[str, ...] | list[str] = (),
        principal: VerifiedWorkflowPrincipal | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        compiled = self._compiled(definition)
        current = self._instance(compiled, instance)
        runtime_context = dict(context or {})
        resolved_actor, resolved_permissions, resolved_roles, principal_reason = (
            self._principal_result(
                actor=actor,
                permissions=tuple(permissions),
                roles=tuple(roles),
                principal=principal,
            )
        )
        allowed: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for transition in compiled.transitions:
            if current["state"] not in transition.sources:
                continue
            accepted, reason = (False, principal_reason) if principal_reason else self._authority_result(
                transition,
                actor=resolved_actor,
                permissions=resolved_permissions,
                roles=resolved_roles,
            )
            if accepted:
                accepted, reason = self._guard_result(transition, current, {}, runtime_context)
            projection = {
                "command": transition.command,
                "transition_id": transition.transition_id,
                "target": transition.target,
                "target_ref": copy.deepcopy(current.get("context", {}).get("target_ref")),
                "input_schema": copy.deepcopy(compiled.commands[transition.command]["input_schema"]),
                "authority": copy.deepcopy(transition.descriptor["authority"]),
                "concurrency": copy.deepcopy(transition.descriptor["concurrency"]),
                "risk": copy.deepcopy(transition.descriptor["risk"]),
                "async_reply": copy.deepcopy(transition.descriptor["async_reply"]),
                "capability_requirements": copy.deepcopy(
                    transition.descriptor["capability_requirements"]
                ),
                "explanation": transition.descriptor["explanations"]["allowed" if accepted else "rejected"],
                "explanation_key": f"workflow.{compiled.workflow_type}.{transition.transition_id}.{'allowed' if accepted else 'rejected'}",
            }
            if accepted:
                allowed.append(projection)
            else:
                blocked.append(
                    {
                        **projection,
                        "reason_code": reason,
                        "reason_key": f"workflow.reason.{str(reason or 'blocked').replace(':', '.')}",
                    }
                )
        terminal = bool(compiled.states[current["state"]].get("terminal"))
        return {
            "schema": "adaos.workflow.description.v1",
            "workflow_type": compiled.workflow_type,
            "definition_version": compiled.definition_version,
            "definition_digest": workflow_definition_digest(compiled),
            "instance_id": current["instance_id"],
            "state": current["state"],
            "generation": current["generation"],
            "terminal": terminal,
            "target": copy.deepcopy(current.get("context", {}).get("target_ref")),
            "progress": {
                "completed_transitions": len(current.get("history") or []),
                "terminal": terminal,
                "waiting": bool(compiled.states[current["state"]].get("waiting")),
                "wait_explanation": compiled.states[current["state"]].get("wait_explanation"),
            },
            "blockers": [
                {"command": item["command"], "reason_code": item["reason_code"], "reason_key": item["reason_key"]}
                for item in blocked
            ],
            "evidence_refs": copy.deepcopy(current.get("context", {}).get("evidence_refs") or []),
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
        roles: tuple[str, ...] | list[str] = (),
        principal: VerifiedWorkflowPrincipal | None = None,
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
                    "event_records": [],
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

        resolved_actor, resolved_permissions, resolved_roles, principal_reason = (
            self._principal_result(
                actor=actor,
                permissions=tuple(permissions),
                roles=tuple(roles),
                principal=principal,
            )
        )
        if principal_reason:
            return self._rejection(current, command_token, principal_reason, timestamp)
        accepted, reason = self._authority_result(
            transition,
            actor=resolved_actor,
            permissions=resolved_permissions,
            roles=resolved_roles,
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
            "actor": resolved_actor,
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
        emitted_events = copy.deepcopy(transition.descriptor["events"]["emitted"])
        event_record = {
            "schema": WORKFLOW_EVENT_SCHEMA,
            "event_id": "evt:" + _digest(
                [updated["instance_id"], updated["generation"], transition.transition_id]
            ).removeprefix("sha256:"),
            "type": "workflow.transition.applied",
            "instance_ref": workflow_ref(
                "workflow",
                str(updated["instance_id"]),
                version=compiled.definition_version,
                generation=int(updated["generation"]),
            ),
            "definition_version": compiled.definition_version,
            "generation": int(updated["generation"]),
            "transition_id": transition.transition_id,
            "command_id": command_token,
            "actor_ref": workflow_ref("principal", resolved_actor),
            "before_state": str(current["state"]),
            "after_state": transition.target,
            "idempotency_key": key,
            "payload_digest": payload_digest,
            "input_digest": _digest(payload),
            "evidence_refs": [
                workflow_ref("evidence", str(item))
                for item in evidence_refs
                if str(item).strip()
            ][:100],
            "data": {
                "declared_events": emitted_events,
                "outbox": bool(transition.descriptor["events"]["outbox"]),
                "conflict_scope": str(concurrency["conflict_scope"]),
            },
            "created_at": timestamp,
        }
        _validate(WORKFLOW_EVENT_SCHEMA, event_record)
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
            "events": emitted_events,
            "event_records": [event_record],
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
            "event_records": [],
            "async_reply": {"mode": "none", "reply_route": "none"},
            "explanation": reason.replace("_", " "),
            "decided_at": timestamp,
        }


def apply_workflow_command(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
    instance: Mapping[str, Any],
    command: Mapping[str, Any],
    *,
    resolver: WorkflowResolver | None = None,
    permissions: tuple[str, ...] | list[str] = (),
    roles: tuple[str, ...] | list[str] = (),
    principal: VerifiedWorkflowPrincipal | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    record = validate_workflow_record(WORKFLOW_COMMAND_SCHEMA, command)
    compiled = definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)
    instance_ref = dict(record["instance_ref"])
    if record["workflow_type"] != compiled.workflow_type:
        raise WorkflowResolutionError("command workflow_type does not match definition")
    if instance_ref["id"] != str(instance.get("instance_id") or ""):
        raise WorkflowResolutionError("command instance_ref does not match instance")
    if instance_ref.get("version") != compiled.definition_version:
        raise WorkflowResolutionError("command definition version does not match definition")
    return (resolver or WorkflowResolver()).apply(
        compiled,
        instance,
        str(record["command_id"]),
        input_value=dict(record["input"]),
        actor=str(record["actor_ref"]["id"]),
        permissions=permissions,
        roles=roles,
        principal=principal,
        expected_generation=int(record["expected_generation"]),
        idempotency_key=str(record["idempotency_key"]),
        context=context,
        now=str(record["created_at"]),
    )


def migrate_workflow_instance(
    source_definition: CompiledWorkflowDefinition | Mapping[str, Any],
    target_definition: CompiledWorkflowDefinition | Mapping[str, Any],
    instance: Mapping[str, Any],
    migration: Mapping[str, Any],
    *,
    actor: str,
    permissions: tuple[str, ...] | list[str] = (),
    roles: tuple[str, ...] | list[str] = (),
    principal: VerifiedWorkflowPrincipal | None = None,
    require_verified_principal: bool = False,
    expected_generation: int,
    idempotency_key: str,
    target_package_digest: str | None = None,
    target_binding_digest: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Create a pure, generation-guarded definition migration decision."""

    source = source_definition if isinstance(source_definition, CompiledWorkflowDefinition) else compile_definition(source_definition)
    target = target_definition if isinstance(target_definition, CompiledWorkflowDefinition) else compile_definition(target_definition)
    record = validate_workflow_record(WORKFLOW_DEFINITION_MIGRATION_SCHEMA, migration)
    current = WorkflowResolver._instance(source, instance)
    timestamp = now or _now()
    key = str(idempotency_key or "").strip()
    if not key:
        raise WorkflowResolutionError("definition migration requires idempotency_key")
    if int(expected_generation) != int(current["generation"]):
        raise WorkflowResolutionError("stale generation for definition migration")
    if source.workflow_type != target.workflow_type or record["workflow_type"] != source.workflow_type:
        raise WorkflowResolutionError("definition migration workflow_type mismatch")
    if record["from_definition_version"] != source.definition_version:
        raise WorkflowResolutionError("definition migration source version mismatch")
    if record["to_definition_version"] != target.definition_version:
        raise WorkflowResolutionError("definition migration target version mismatch")
    if source.definition_version == target.definition_version:
        raise WorkflowResolutionError("definition migration must advance to a different version")
    if current.get("package_digest") is not None and target_package_digest is None:
        raise WorkflowResolutionError(
            "package-pinned definition migration requires target_package_digest"
        )
    if current.get("binding_digest") is not None and target_binding_digest is None:
        raise WorkflowResolutionError(
            "binding-pinned definition migration requires target_binding_digest"
        )
    allowed_states = set(record["allowed_source_states"])
    if not allowed_states.issubset(source.states):
        raise WorkflowDefinitionError("definition migration names an unknown source state")
    if current["state"] not in allowed_states:
        raise WorkflowResolutionError("current state is not admitted by definition migration")
    state_map = dict(record["state_map"])
    missing_mappings = sorted(allowed_states - set(state_map))
    if missing_mappings:
        raise WorkflowDefinitionError(
            "definition migration does not map admitted states: " + ", ".join(missing_mappings)
        )
    unknown_targets = sorted(set(state_map.values()) - set(target.states))
    if unknown_targets:
        raise WorkflowDefinitionError(
            "definition migration maps to unknown target states: " + ", ".join(unknown_targets)
        )
    resolved_actor, resolved_permissions, resolved_roles, principal_reason = WorkflowResolver(
        require_verified_principal=require_verified_principal
    )._principal_result(
        actor=actor,
        permissions=tuple(permissions),
        roles=tuple(roles),
        principal=principal,
    )
    if principal_reason:
        raise WorkflowResolutionError(principal_reason)
    authority = dict(record["authority"])
    if not _actor_matches(tuple(authority["actors"]), resolved_actor):
        raise WorkflowResolutionError("actor_not_authorized")
    missing_permissions = sorted(set(authority["permissions"]) - set(resolved_permissions))
    if missing_permissions:
        raise WorkflowResolutionError(f"missing_permission:{missing_permissions[0]}")
    admitted_roles = tuple(str(item) for item in authority.get("roles") or ())
    if admitted_roles and not set(admitted_roles).intersection(resolved_roles):
        raise WorkflowResolutionError(f"role_not_authorized:{admitted_roles[0]}")

    updated = copy.deepcopy(current)
    updated["definition_version"] = target.definition_version
    updated["definition_digest"] = workflow_definition_digest(target)
    if target_package_digest is not None:
        updated["package_digest"] = str(target_package_digest).strip()
    if target_binding_digest is not None:
        updated["binding_digest"] = str(target_binding_digest).strip()
    updated["state"] = str(state_map[current["state"]])
    updated["generation"] = int(current["generation"]) + 1
    updated["updated_at"] = timestamp
    migrated_context = copy.deepcopy(dict(updated.get("context") or {}))
    for field in record["context_remove"]:
        migrated_context.pop(str(field), None)
    migrated_context.update(copy.deepcopy(dict(record["context_set"])))
    updated["context"] = migrated_context
    transition_id = f"definition_migration:{record['migration_id']}"
    input_value = {
        "migration_id": record["migration_id"],
        "from_definition_version": source.definition_version,
        "to_definition_version": target.definition_version,
    }
    payload_digest = _digest({"command": "migrate_definition", "input": input_value})
    history_entry = {
        "generation": updated["generation"],
        "command": "migrate_definition",
        "transition_id": transition_id,
        "actor": resolved_actor,
        "from": current["state"],
        "to": updated["state"],
        "input_digest": _digest(input_value),
        "at": timestamp,
    }
    updated["history"] = [*updated["history"], history_entry][-_MAX_LEDGER:]
    updated["idempotency"] = [
        *updated["idempotency"],
        {
            "key": key,
            "payload_digest": payload_digest,
            "transition_id": transition_id,
            "generation": updated["generation"],
            "async_reply": {"mode": "terminal", "reply_route": "origin"},
            "explanation": record["explanation"],
            "at": timestamp,
        },
    ][-_MAX_LEDGER:]
    event_record = {
        "schema": WORKFLOW_EVENT_SCHEMA,
        "event_id": "evt:" + _digest(
            [updated["instance_id"], updated["generation"], transition_id]
        ).removeprefix("sha256:"),
        "type": "workflow.definition.migrated",
        "instance_ref": workflow_ref(
            "workflow",
            str(updated["instance_id"]),
            version=target.definition_version,
            generation=int(updated["generation"]),
        ),
        "definition_version": target.definition_version,
        "generation": int(updated["generation"]),
        "transition_id": transition_id,
        "command_id": "migrate_definition",
        "actor_ref": workflow_ref("principal", resolved_actor),
        "before_state": str(current["state"]),
        "after_state": str(updated["state"]),
        "idempotency_key": key,
        "payload_digest": payload_digest,
        "input_digest": _digest(input_value),
        "evidence_refs": [],
        "data": {
            "migration_id": record["migration_id"],
            "from_definition_version": source.definition_version,
            "to_definition_version": target.definition_version,
            "context_set": copy.deepcopy(record["context_set"]),
            "context_remove": list(record["context_remove"]),
        },
        "created_at": timestamp,
    }
    _validate(WORKFLOW_INSTANCE_SCHEMA, updated)
    _validate(WORKFLOW_EVENT_SCHEMA, event_record)
    return {
        "schema": WORKFLOW_DECISION_SCHEMA,
        "accepted": True,
        "status": "accepted",
        "reason_code": None,
        "command": "migrate_definition",
        "transition_id": transition_id,
        "before": current,
        "after": updated,
        "activity": None,
        "events": ["workflow.definition.migrated"],
        "event_records": [event_record],
        "async_reply": {"mode": "terminal", "reply_route": "origin"},
        "explanation": record["explanation"],
        "decided_at": timestamp,
    }


def validate_workflow_composition(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a bounded parent/child workflow composition contract."""

    record = validate_workflow_record(WORKFLOW_COMPOSITION_SCHEMA, value)
    children = list(record["children"])
    child_ids = [str(item["child_id"]) for item in children]
    if len(child_ids) != len(set(child_ids)):
        raise WorkflowDefinitionError("workflow composition child_id values must be unique")
    correlations = [str(item["correlation_key"]) for item in children]
    if len(correlations) != len(set(correlations)):
        raise WorkflowDefinitionError("workflow composition correlation keys must be unique")
    parent_permissions = set(record["parent_authority"]["permissions"])
    for child in children:
        delegated = set(child["delegated_authority"]["permissions"])
        if not delegated.issubset(parent_permissions):
            raise WorkflowDefinitionError(
                f"child {child['child_id']} delegates authority outside the parent scope"
            )
    join = dict(record["join"])
    required_count = sum(1 for child in children if child["required"])
    participant_count = required_count or len(children)
    if join["mode"] == "quorum":
        quorum = join.get("quorum")
        if quorum is None or int(quorum) > participant_count:
            raise WorkflowDefinitionError("workflow composition quorum exceeds participating children")
    elif join.get("quorum") is not None:
        raise WorkflowDefinitionError("workflow composition quorum is only valid for quorum joins")
    return record


def resolve_workflow_join(
    composition: Mapping[str, Any],
    child_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve child outcomes without mutating the parent workflow."""

    record = validate_workflow_composition(composition)
    declared = {str(item["child_id"]): item for item in record["children"]}
    results: dict[str, dict[str, Any]] = {}
    late_results: list[str] = []
    for raw in child_results:
        result = copy.deepcopy(dict(raw))
        child_id = str(result.get("child_id") or "")
        if child_id not in declared:
            raise WorkflowResolutionError(f"undeclared workflow child result: {child_id}")
        if child_id in results:
            raise WorkflowResolutionError(f"duplicate workflow child result: {child_id}")
        status = str(result.get("status") or "")
        if status not in {"running", "waiting", "succeeded", "failed", "cancelled", "unknown"}:
            raise WorkflowResolutionError(f"invalid workflow child status: {status}")
        if bool(result.get("late")):
            if record["late_result"] == "reject":
                raise WorkflowResolutionError(f"late workflow child result rejected: {child_id}")
            late_results.append(child_id)
        results[child_id] = result

    participants = [item for item in record["children"] if item["required"]]
    if not participants:
        participants = list(record["children"])
    statuses = {str(item["child_id"]): str(results.get(str(item["child_id"]), {}).get("status") or "waiting") for item in participants}
    succeeded = sorted(child_id for child_id, status in statuses.items() if status == "succeeded")
    failed = sorted(child_id for child_id, status in statuses.items() if status in {"failed", "cancelled", "unknown"})
    pending = sorted(child_id for child_id, status in statuses.items() if status in {"running", "waiting"})
    mode = record["join"]["mode"]
    needed = len(participants) if mode == "all" else 1
    if mode == "quorum":
        needed = int(record["join"]["quorum"])
    possible = len(succeeded) + len(pending)
    if len(succeeded) >= needed:
        outcome = "partial_succeeded" if failed and record["partial_outcome"] == "continue_partial" else "succeeded"
        complete = True
    elif possible < needed:
        outcome = "partial_failed" if succeeded else "failed"
        complete = record["partial_outcome"] != "wait"
    else:
        outcome = "waiting"
        complete = False

    evidence: list[Any] = []
    if record["evidence_aggregation"] != "none":
        for child_id in sorted(results):
            result = results[child_id]
            if record["evidence_aggregation"] == "successful_only" and result["status"] != "succeeded":
                continue
            evidence.extend(copy.deepcopy(list(result.get("evidence_refs") or [])))
    return {
        "schema": "adaos.workflow.join_result.v1",
        "composition_id": record["composition_id"],
        "outcome": outcome,
        "complete": complete,
        "promotable": outcome in {"succeeded", "partial_succeeded"},
        "succeeded_children": succeeded,
        "failed_children": failed,
        "pending_children": pending,
        "evidence_refs": evidence,
        "late_results": sorted(late_results),
        "late_result_policy": record["late_result"],
        "cancellation": record["cancellation"],
        "compensation": record["compensation"],
    }


def rebuild_instance(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
    instance_id: str,
    events: Sequence[Mapping[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    compiled = definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)
    snapshot = new_instance(compiled, instance_id, context=context, now=created_at)
    ordered = sorted((copy.deepcopy(dict(item)) for item in events), key=lambda item: int(item.get("generation") or 0))
    for event in ordered:
        validate_workflow_record(WORKFLOW_EVENT_SCHEMA, event)
        if event["instance_ref"]["id"] != instance_id:
            raise WorkflowResolutionError("workflow event instance_ref does not match replay target")
        if event["definition_version"] != compiled.definition_version:
            raise WorkflowResolutionError("workflow event definition version does not match replay definition")
        expected = int(snapshot["generation"]) + 1
        if int(event["generation"]) != expected:
            raise WorkflowResolutionError(
                f"workflow event generation gap: expected {expected}, got {event['generation']}"
            )
        if event["before_state"] != snapshot["state"]:
            raise WorkflowResolutionError("workflow event before_state does not match replay state")
        transition = next(
            (item for item in compiled.transitions if item.transition_id == event["transition_id"]),
            None,
        )
        if transition is None or transition.target != event["after_state"]:
            raise WorkflowResolutionError("workflow event transition does not match definition")
        snapshot["state"] = str(event["after_state"])
        snapshot["generation"] = int(event["generation"])
        snapshot["updated_at"] = str(event["created_at"])
        snapshot["history"] = [
            *snapshot["history"],
            {
                "generation": int(event["generation"]),
                "command": str(event["command_id"]),
                "transition_id": str(event["transition_id"]),
                "actor": str(event["actor_ref"]["id"]),
                "from": str(event["before_state"]),
                "to": str(event["after_state"]),
                "input_digest": str(event["input_digest"]),
                "at": str(event["created_at"]),
            },
        ][-_MAX_LEDGER:]
        snapshot["idempotency"] = [
            *snapshot["idempotency"],
            {
                "key": str(event["idempotency_key"]),
                "payload_digest": str(event["payload_digest"]),
                "transition_id": str(event["transition_id"]),
                "generation": int(event["generation"]),
                "at": str(event["created_at"]),
            },
        ][-_MAX_LEDGER:]
    return snapshot


def rebuild_versioned_instance(
    definitions: Mapping[str, CompiledWorkflowDefinition | Mapping[str, Any]],
    instance_id: str,
    events: Sequence[Mapping[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Replay an instance across explicit definition-migration events."""

    compiled_by_version = {
        str(version): value if isinstance(value, CompiledWorkflowDefinition) else compile_definition(value)
        for version, value in definitions.items()
    }
    if not compiled_by_version:
        raise WorkflowResolutionError("versioned replay requires at least one definition")
    ordered = sorted((copy.deepcopy(dict(item)) for item in events), key=lambda item: int(item.get("generation") or 0))
    if not ordered:
        if len(compiled_by_version) != 1:
            raise WorkflowResolutionError("empty versioned replay requires exactly one definition")
        return new_instance(next(iter(compiled_by_version.values())), instance_id, context=context, now=created_at)
    first = ordered[0]
    validate_workflow_record(WORKFLOW_EVENT_SCHEMA, first)
    initial_version = (
        str(dict(first.get("data") or {}).get("from_definition_version") or "")
        if first.get("type") == "workflow.definition.migrated"
        else str(first["definition_version"])
    )
    if initial_version not in compiled_by_version:
        raise WorkflowResolutionError(f"workflow definition is unavailable for replay: {initial_version}")
    current_definition = compiled_by_version[initial_version]
    snapshot = new_instance(current_definition, instance_id, context=context, now=created_at)
    for event in ordered:
        validate_workflow_record(WORKFLOW_EVENT_SCHEMA, event)
        if event["instance_ref"]["id"] != instance_id:
            raise WorkflowResolutionError("workflow event instance_ref does not match replay target")
        expected = int(snapshot["generation"]) + 1
        if int(event["generation"]) != expected:
            raise WorkflowResolutionError(
                f"workflow event generation gap: expected {expected}, got {event['generation']}"
            )
        if event["before_state"] != snapshot["state"]:
            raise WorkflowResolutionError("workflow event before_state does not match replay state")
        if event["type"] == "workflow.definition.migrated":
            data = dict(event["data"])
            if data.get("from_definition_version") != snapshot["definition_version"]:
                raise WorkflowResolutionError("migration event source version does not match replay snapshot")
            target_version = str(data.get("to_definition_version") or "")
            if target_version != event["definition_version"] or target_version not in compiled_by_version:
                raise WorkflowResolutionError("migration event target definition is unavailable")
            current_definition = compiled_by_version[target_version]
            if event["after_state"] not in current_definition.states:
                raise WorkflowResolutionError("migration event target state is not in target definition")
            migrated_context = copy.deepcopy(dict(snapshot.get("context") or {}))
            for field in data.get("context_remove") or []:
                migrated_context.pop(str(field), None)
            migrated_context.update(copy.deepcopy(dict(data.get("context_set") or {})))
            snapshot["context"] = migrated_context
            snapshot["definition_version"] = target_version
        else:
            if event["definition_version"] != snapshot["definition_version"]:
                raise WorkflowResolutionError("workflow event definition version does not match replay snapshot")
            transition = next(
                (item for item in current_definition.transitions if item.transition_id == event["transition_id"]),
                None,
            )
            if transition is None or transition.target != event["after_state"]:
                raise WorkflowResolutionError("workflow event transition does not match definition")
        snapshot["state"] = str(event["after_state"])
        snapshot["generation"] = int(event["generation"])
        snapshot["updated_at"] = str(event["created_at"])
        snapshot["history"] = [
            *snapshot["history"],
            {
                "generation": int(event["generation"]),
                "command": str(event["command_id"]),
                "transition_id": str(event["transition_id"]),
                "actor": str(event["actor_ref"]["id"]),
                "from": str(event["before_state"]),
                "to": str(event["after_state"]),
                "input_digest": str(event["input_digest"]),
                "at": str(event["created_at"]),
            },
        ][-_MAX_LEDGER:]
        snapshot["idempotency"] = [
            *snapshot["idempotency"],
            {
                "key": str(event["idempotency_key"]),
                "payload_digest": str(event["payload_digest"]),
                "transition_id": str(event["transition_id"]),
                "generation": int(event["generation"]),
                "at": str(event["created_at"]),
            },
        ][-_MAX_LEDGER:]
    _validate(WORKFLOW_INSTANCE_SCHEMA, snapshot)
    return snapshot


def definition_review_report(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
) -> dict[str, Any]:
    compiled = definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)
    adjacency: dict[str, set[str]] = {state: set() for state in compiled.states}
    for transition in compiled.transitions:
        for source in transition.sources:
            adjacency[source].add(transition.target)
    reachable = {compiled.initial_state}
    pending = [compiled.initial_state]
    while pending:
        source = pending.pop()
        for target in sorted(adjacency[source]):
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    cycle_edges = 0
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(state: str) -> None:
        nonlocal cycle_edges
        visiting.add(state)
        for target in adjacency[state]:
            if target in visiting:
                cycle_edges += 1
            elif target not in visited:
                visit(target)
        visiting.remove(state)
        visited.add(state)

    visit(compiled.initial_state)
    return {
        "schema": "adaos.workflow.definition_review.v1",
        "workflow_type": compiled.workflow_type,
        "definition_version": compiled.definition_version,
        "state_count": len(compiled.states),
        "transition_count": len(compiled.transitions),
        "command_count": len(compiled.commands),
        "reachable_states": sorted(reachable),
        "unreachable_states": sorted(set(compiled.states) - reachable),
        "terminal_states": sorted(
            state_id for state_id, state in compiled.states.items() if state.get("terminal")
        ),
        "waiting_states": sorted(
            state_id for state_id, state in compiled.states.items() if state.get("waiting")
        ),
        "cycle_edge_count": cycle_edges,
        "unused_commands": sorted(
            set(compiled.commands) - {transition.command for transition in compiled.transitions}
        ),
        "conflict_scopes": sorted(
            {str(transition.descriptor["concurrency"]["conflict_scope"]) for transition in compiled.transitions}
        ),
    }


def export_statechart(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
) -> dict[str, Any]:
    compiled = definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)
    return {
        "schema": "adaos.workflow.statechart_projection.v1",
        "workflow_type": compiled.workflow_type,
        "definition_version": compiled.definition_version,
        "initial_state": compiled.initial_state,
        "states": [copy.deepcopy(compiled.states[state_id]) for state_id in sorted(compiled.states)],
        "edges": [
            {
                "transition_id": transition.transition_id,
                "source": list(transition.sources),
                "target": transition.target,
                "command": transition.command,
            }
            for transition in compiled.transitions
        ],
        "authoritative": False,
    }


def generate_conformance_cases(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Generate deterministic state/transition coverage cases for adapters."""

    compiled = definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)
    cases: list[dict[str, Any]] = []
    for state_id in sorted(compiled.states):
        state = compiled.states[state_id]
        cases.append(
            {
                "case_id": f"state:{state_id}:explain",
                "kind": "state_explanation",
                "state": state_id,
                "expected_terminal": bool(state.get("terminal")),
                "expected_waiting": bool(state.get("waiting")),
                "expected_explanation": state.get("wait_explanation") or state.get("description") or state.get("label"),
            }
        )
    for transition in compiled.transitions:
        for source in transition.sources:
            cases.append(
                {
                    "case_id": f"transition:{transition.transition_id}:{source}",
                    "kind": "transition_admission",
                    "state": source,
                    "command": transition.command,
                    "target": transition.target,
                    "transition_id": transition.transition_id,
                    "expected_generation_guard": bool(
                        transition.descriptor["concurrency"]["requires_generation"]
                    ),
                    "expected_rejection_key": f"workflow.{compiled.workflow_type}.{transition.transition_id}.rejected",
                }
            )
    return cases


def workflow_contract_snapshot() -> dict[str, Any]:
    return {
        "schema_version": "adaos.workflow.contract.v1",
        "records": {
            "WorkflowDefinition": WORKFLOW_DEFINITION_SCHEMA,
            "TransitionDescriptor": WORKFLOW_TRANSITION_SCHEMA,
            "WorkflowInstance": WORKFLOW_INSTANCE_SCHEMA,
            "WorkflowRef": WORKFLOW_REF_SCHEMA,
            "WorkflowCommand": WORKFLOW_COMMAND_SCHEMA,
            "WorkflowEvent": WORKFLOW_EVENT_SCHEMA,
            "WorkflowDefinitionMigration": WORKFLOW_DEFINITION_MIGRATION_SCHEMA,
            "WorkflowComposition": WORKFLOW_COMPOSITION_SCHEMA,
            "WorkflowDecision": WORKFLOW_DECISION_SCHEMA,
            "WorkflowValidationReport": WORKFLOW_VALIDATION_REPORT_SCHEMA,
            "WorkflowAdapterContract": WORKFLOW_ADAPTER_CONTRACT_SCHEMA,
            "WorkflowRegistryEntry": WORKFLOW_REGISTRY_ENTRY_SCHEMA,
            "WorkflowBinding": WORKFLOW_BINDING_SCHEMA,
            "WorkflowPrincipal": WORKFLOW_PRINCIPAL_SCHEMA,
        },
        "invariants": {
            "resolver": "pure",
            "effects": "described_not_executed",
            "definition_version": "pinned_per_instance",
            "definition_digest": "pinned_per_instance",
            "authority_roles": "declared_and_fail_closed",
            "concurrency": "generation_guarded",
            "idempotency": "payload_bound",
            "definition_migration": "explicit_event",
            "composition": "reference_only_parent_child",
        },
    }
