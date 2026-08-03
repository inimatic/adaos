from __future__ import annotations

import copy

import pytest

from adaos.services.builder.governed import builder_change_definition
from adaos.services.workflow_registry import (
    WorkflowAdapterRegistry,
    WorkflowAdapterRegistryError,
    create_adapter_contract,
    create_registry_entry,
    platform_workflow_adapter_contracts,
    platform_workflow_adapter_registry,
)


def test_builder_definition_binds_to_exact_platform_contracts() -> None:
    binding = platform_workflow_adapter_registry().bind(builder_change_definition())

    assert binding["workflow_type"] == "builder.change"
    assert binding["binding_digest"].startswith("sha256:")
    assert {item["adapter_id"] for item in binding["adapters"]} == {
        "always",
        "builder.codex.run",
        "builder.codex.run.compensate",
        "builder.prototype.derive",
        "builder.prototype.derive.compensate",
        "builder.trial.activate",
        "builder.publication.publish",
    }


def test_registry_rejects_unknown_and_permission_broadening_adapters() -> None:
    definition = builder_change_definition()
    transition = next(item for item in definition["transitions"] if item["effect"]["activity"])
    transition["effect"]["activity"] = "unknown.activity"
    with pytest.raises(WorkflowAdapterRegistryError, match="not registered"):
        platform_workflow_adapter_registry().bind(definition)

    definition = builder_change_definition()
    transition = next(item for item in definition["transitions"] if item["effect"]["activity"])
    transition["authority"]["permissions"] = ["host.admin"]
    with pytest.raises(WorkflowAdapterRegistryError, match="permission ceiling"):
        platform_workflow_adapter_registry().bind(definition)


def test_registry_rejects_mutable_contract_and_forged_digest() -> None:
    contract = create_adapter_contract(
        "demo.guard",
        "guard",
        implementation="demo:guard",
        params_schema={"type": "object"},
    )
    registry = WorkflowAdapterRegistry([contract])
    changed = copy.deepcopy(contract)
    changed["implementation"] = "demo:changed"
    with pytest.raises(WorkflowAdapterRegistryError, match="digest mismatch"):
        registry.register(changed)

    replacement = create_adapter_contract(
        "demo.guard",
        "guard",
        implementation="demo:changed",
        params_schema={"type": "object"},
    )
    with pytest.raises(WorkflowAdapterRegistryError, match="mutable"):
        registry.register(replacement)


def test_expected_binding_locks_fail_closed_on_registry_change() -> None:
    definition = builder_change_definition()
    registry = platform_workflow_adapter_registry()
    binding = registry.bind(definition)
    expected = copy.deepcopy(binding["adapters"])
    expected[0]["contract_digest"] = "sha256:" + "0" * 64

    with pytest.raises(WorkflowAdapterRegistryError, match="do not match"):
        registry.bind(definition, expected_locks=expected)


def test_platform_contract_set_has_unique_kind_and_identity() -> None:
    contracts = platform_workflow_adapter_contracts()
    keys = {(item["kind"], item["adapter_id"]) for item in contracts}
    assert len(keys) == len(contracts)


def test_registry_entry_and_activity_params_are_schema_bound() -> None:
    definition = builder_change_definition()
    transition = next(item for item in definition["transitions"] if item["effect"]["activity"])
    transition["effect"]["activity"] = "demo.activity"
    transition["effect"]["compensation"] = None
    transition["effect"]["activity_params"] = {"mode": "trial"}
    transition["effect"]["output_schema"] = {
        "type": "object",
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
        "additionalProperties": False,
    }
    contract = create_adapter_contract(
        "demo.activity",
        "activity",
        implementation="demo:activity",
        input_schema=transition["trigger"]["input_schema"],
        output_schema=transition["effect"]["output_schema"],
        params_schema={
            "type": "object",
            "required": ["mode"],
            "properties": {"mode": {"const": "trial"}},
            "additionalProperties": False,
        },
        side_effects=(transition["risk"]["side_effect"],),
        risk_classes=(transition["risk"]["class"],),
        permission_ceiling=transition["authority"]["permissions"],
        sandbox="isolated",
    )
    entry = create_registry_entry(contract)
    registry = WorkflowAdapterRegistry([*platform_workflow_adapter_contracts(), entry["contract"]])

    binding = registry.bind(definition)

    assert entry["schema"] == "adaos.workflow.registry_entry.v1"
    assert any(item["adapter_id"] == "demo.activity" for item in binding["adapters"])

    missing_params = copy.deepcopy(definition)
    missing_transition = next(
        item
        for item in missing_params["transitions"]
        if item["transition_id"] == transition["transition_id"]
    )
    missing_transition["effect"]["activity_params"] = {}
    with pytest.raises(WorkflowAdapterRegistryError, match="params violate"):
        registry.bind(missing_params)

    mismatched_output = create_adapter_contract(
        "demo.output_mismatch",
        "activity",
        implementation="demo:activity",
        input_schema=transition["trigger"]["input_schema"],
        output_schema={"type": "object"},
        params_schema={
            "type": "object",
            "required": ["mode"],
            "properties": {"mode": {"const": "trial"}},
            "additionalProperties": False,
        },
        side_effects=(transition["risk"]["side_effect"],),
        risk_classes=(transition["risk"]["class"],),
        permission_ceiling=transition["authority"]["permissions"],
        sandbox="isolated",
    )
    output_definition = copy.deepcopy(definition)
    output_transition = next(
        item
        for item in output_definition["transitions"]
        if item["transition_id"] == transition["transition_id"]
    )
    output_transition["effect"]["activity"] = "demo.output_mismatch"
    output_registry = WorkflowAdapterRegistry(
        [*platform_workflow_adapter_contracts(), mismatched_output]
    )
    with pytest.raises(WorkflowAdapterRegistryError, match="output schema"):
        output_registry.bind(output_definition)
