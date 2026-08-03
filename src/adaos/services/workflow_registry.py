from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    compile_definition,
    validate_workflow_record,
    workflow_definition_digest,
)


WORKFLOW_ADAPTER_CONTRACT_SCHEMA = "adaos.workflow.adapter_contract.v1"
WORKFLOW_REGISTRY_ENTRY_SCHEMA = "adaos.workflow.registry_entry.v1"
WORKFLOW_BINDING_SCHEMA = "adaos.workflow.binding.v1"


class WorkflowAdapterRegistryError(ValueError):
    """Raised when declarative code references cannot be bound immutably."""


def create_adapter_contract(
    adapter_id: str,
    kind: str,
    *,
    implementation: str,
    owner_scope: str = "platform",
    owner_package: str | None = None,
    input_schema: Mapping[str, Any] | None = None,
    output_schema: Mapping[str, Any] | None = None,
    params_schema: Mapping[str, Any] | None = None,
    side_effects: Iterable[str] = ("none",),
    risk_classes: Iterable[str] = ("read",),
    permission_ceiling: Iterable[str] = (),
    sandbox: str = "pure",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": WORKFLOW_ADAPTER_CONTRACT_SCHEMA,
        "adapter_id": str(adapter_id),
        "kind": str(kind),
        "owner": {"scope": str(owner_scope), "package": owner_package},
        "implementation": str(implementation),
        "input_schema": copy.deepcopy(dict(input_schema or {})),
        "output_schema": copy.deepcopy(dict(output_schema or {})),
        "params_schema": copy.deepcopy(dict(params_schema or {})),
        "side_effects": sorted({str(item) for item in side_effects}),
        "risk_classes": sorted({str(item) for item in risk_classes}),
        "permission_ceiling": sorted({str(item) for item in permission_ceiling}),
        "sandbox": str(sandbox),
    }
    payload["contract_digest"] = canonical_payload_digest(payload)
    validate_adapter_contract(payload)
    return payload


def validate_adapter_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    contract = validate_workflow_record(WORKFLOW_ADAPTER_CONTRACT_SCHEMA, value)
    supplied = str(contract.pop("contract_digest"))
    expected = canonical_payload_digest(contract)
    contract["contract_digest"] = supplied
    if supplied != expected:
        raise WorkflowAdapterRegistryError(
            f"adapter {contract.get('adapter_id')} contract digest mismatch"
        )
    owner = dict(contract["owner"])
    if owner["scope"] == "platform" and owner["package"] is not None:
        raise WorkflowAdapterRegistryError("platform adapter owner must not name a package")
    if owner["scope"] != "platform" and not str(owner["package"] or "").strip():
        raise WorkflowAdapterRegistryError("package/dependency adapter owner must name a package")
    if contract["kind"] == "guard":
        if contract["side_effects"] != ["none"] or contract["sandbox"] != "pure":
            raise WorkflowAdapterRegistryError("guard adapters must be pure and side-effect free")
    return contract


def create_registry_entry(
    contract: Mapping[str, Any],
    *,
    status: str = "active",
) -> dict[str, Any]:
    entry = {
        "schema": WORKFLOW_REGISTRY_ENTRY_SCHEMA,
        "contract": validate_adapter_contract(contract),
        "status": str(status),
    }
    validate_workflow_record(WORKFLOW_REGISTRY_ENTRY_SCHEMA, entry)
    return copy.deepcopy(entry)


def _builder_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "confirmed": {"type": "boolean"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "legacy_action": {"type": "string"},
        },
        "additionalProperties": True,
    }


def platform_workflow_adapter_contracts() -> tuple[dict[str, Any], ...]:
    """Stable semantic contracts for adapters implemented by AdaOS core."""

    guards = (
        create_adapter_contract(
            "always",
            "guard",
            implementation="adaos.services.governed_workflow:_guard_always",
            params_schema={"type": "object", "maxProperties": 0},
        ),
        create_adapter_contract(
            "context_equals",
            "guard",
            implementation="adaos.services.governed_workflow:_guard_context_equals",
            params_schema={
                "type": "object",
                "required": ["field", "value"],
                "properties": {"field": {"type": "string"}, "value": {}},
                "additionalProperties": False,
            },
        ),
        create_adapter_contract(
            "instance_context_equals",
            "guard",
            implementation="adaos.services.governed_workflow:_guard_instance_context_equals",
            params_schema={
                "type": "object",
                "required": ["field", "value"],
                "properties": {"field": {"type": "string"}, "value": {}},
                "additionalProperties": False,
            },
        ),
    )
    builder_input = _builder_input_schema()
    activities = tuple(
        create_adapter_contract(
            adapter_id,
            kind,
            implementation=f"adaos.services.builder.workflow:{adapter_id}",
            input_schema=builder_input,
            output_schema={"type": "object"},
            side_effects=side_effects,
            risk_classes=risk_classes,
            sandbox="core",
        )
        for adapter_id, kind, side_effects, risk_classes in (
            ("builder.codex.run", "activity", ("reversible",), ("isolated_write",)),
            ("builder.codex.run.compensate", "compensation", ("reversible",), ("isolated_write",)),
            ("builder.prototype.derive", "activity", ("reversible",), ("isolated_write",)),
            ("builder.prototype.derive.compensate", "compensation", ("reversible",), ("isolated_write",)),
            ("builder.trial.activate", "activity", ("external",), ("trial_activation",)),
            ("builder.publication.publish", "activity", ("external",), ("publication",)),
        )
    )
    return (*guards, *activities)


@dataclass(slots=True)
class WorkflowAdapterRegistry:
    contracts: Iterable[Mapping[str, Any]] = ()
    _contracts: dict[tuple[str, str], dict[str, Any]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        for contract in self.contracts:
            self.register(contract)

    def register(self, value: Mapping[str, Any]) -> None:
        contract = validate_adapter_contract(value)
        key = (str(contract["kind"]), str(contract["adapter_id"]))
        previous = self._contracts.get(key)
        if previous is not None and previous != contract:
            raise WorkflowAdapterRegistryError(
                f"mutable adapter registration rejected: {key[0]}:{key[1]}"
            )
        self._contracts[key] = copy.deepcopy(contract)

    def get(self, kind: str, adapter_id: str) -> dict[str, Any] | None:
        value = self._contracts.get((str(kind), str(adapter_id)))
        return copy.deepcopy(value) if value is not None else None

    def bind(
        self,
        definition: CompiledWorkflowDefinition | Mapping[str, Any],
        *,
        expected_locks: Iterable[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        compiled = (
            definition
            if isinstance(definition, CompiledWorkflowDefinition)
            else compile_definition(definition)
        )
        selected: dict[tuple[str, str], dict[str, Any]] = {}
        for transition in compiled.transitions:
            for guard in transition.descriptor["guards"]:
                self._admit_usage(
                    selected,
                    "guard",
                    str(guard["id"]),
                    transition,
                    params=dict(guard["params"]),
                )
            effect = dict(transition.descriptor["effect"])
            if effect.get("activity"):
                self._admit_usage(
                    selected,
                    "activity",
                    str(effect["activity"]),
                    transition,
                    params=dict(effect.get("activity_params") or {}),
                )
            if effect.get("compensation"):
                self._admit_usage(
                    selected,
                    "compensation",
                    str(effect["compensation"]),
                    transition,
                    params=dict(effect.get("compensation_params") or {}),
                )
        adapters = [
            {
                "adapter_id": contract["adapter_id"],
                "kind": contract["kind"],
                "contract_digest": contract["contract_digest"],
                "owner": copy.deepcopy(contract["owner"]),
            }
            for _key, contract in sorted(selected.items())
        ]
        if expected_locks is not None:
            expected = sorted(
                (
                    str(item.get("kind")),
                    str(item.get("adapter_id")),
                    str(item.get("contract_digest")),
                )
                for item in expected_locks
            )
            actual = sorted(
                (item["kind"], item["adapter_id"], item["contract_digest"])
                for item in adapters
            )
            if expected != actual:
                raise WorkflowAdapterRegistryError(
                    "workflow adapter locks do not match the active registry"
                )
        registry_digest = canonical_payload_digest(adapters)
        unsigned = {
            "schema": WORKFLOW_BINDING_SCHEMA,
            "workflow_type": compiled.workflow_type,
            "definition_version": compiled.definition_version,
            "definition_digest": workflow_definition_digest(compiled),
            "registry_digest": registry_digest,
            "adapters": adapters,
        }
        binding = {**unsigned, "binding_digest": canonical_payload_digest(unsigned)}
        validate_workflow_record(WORKFLOW_BINDING_SCHEMA, binding)
        return binding

    def _admit_usage(
        self,
        selected: dict[tuple[str, str], dict[str, Any]],
        kind: str,
        adapter_id: str,
        transition: Any,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        contract = self._contracts.get((kind, adapter_id))
        if contract is None:
            raise WorkflowAdapterRegistryError(
                f"workflow {kind} adapter is not registered: {adapter_id}"
            )
        descriptor = transition.descriptor
        errors = sorted(
            Draft202012Validator(contract["params_schema"]).iter_errors(dict(params or {})),
            key=lambda item: list(item.absolute_path),
        )
        if errors:
            raise WorkflowAdapterRegistryError(
                f"{kind} {adapter_id} params violate its registered contract: {errors[0].message}"
            )
        if kind != "guard":
            input_schema = descriptor["trigger"]["input_schema"]
            if canonical_payload_digest(input_schema) != canonical_payload_digest(
                contract["input_schema"]
            ):
                raise WorkflowAdapterRegistryError(
                    f"{kind} {adapter_id} input schema differs from its registered contract"
                )
            output_schema = descriptor["effect"]["output_schema"]
            if canonical_payload_digest(output_schema) != canonical_payload_digest(
                contract["output_schema"]
            ):
                raise WorkflowAdapterRegistryError(
                    f"{kind} {adapter_id} output schema differs from its registered contract"
                )
            risk = descriptor["risk"]
            if risk["side_effect"] not in contract["side_effects"]:
                raise WorkflowAdapterRegistryError(
                    f"{kind} {adapter_id} broadens registered side effects"
                )
            if risk["class"] not in contract["risk_classes"]:
                raise WorkflowAdapterRegistryError(
                    f"{kind} {adapter_id} broadens registered risk class"
                )
        permissions = set(descriptor["authority"]["permissions"])
        if not permissions.issubset(set(contract["permission_ceiling"])):
            raise WorkflowAdapterRegistryError(
                f"{kind} {adapter_id} broadens registered permission ceiling"
            )
        selected[(kind, adapter_id)] = contract


def platform_workflow_adapter_registry() -> WorkflowAdapterRegistry:
    return WorkflowAdapterRegistry(platform_workflow_adapter_contracts())


__all__ = [
    "WORKFLOW_ADAPTER_CONTRACT_SCHEMA",
    "WORKFLOW_BINDING_SCHEMA",
    "WORKFLOW_REGISTRY_ENTRY_SCHEMA",
    "WorkflowAdapterRegistry",
    "WorkflowAdapterRegistryError",
    "create_adapter_contract",
    "create_registry_entry",
    "platform_workflow_adapter_contracts",
    "platform_workflow_adapter_registry",
    "validate_adapter_contract",
]
