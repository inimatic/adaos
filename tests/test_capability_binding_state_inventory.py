from __future__ import annotations

import json
from pathlib import Path

from adaos.domain.artifact_release import (
    WORKSPACE_LOCK_SCHEMA,
    canonical_json_bytes,
    canonical_payload_digest,
)
from adaos.services.resources.local import (
    LOCAL_RESOURCE_SCHEMA,
    LOCAL_RESOURCE_STATE_SCHEMA,
)
from adaos.services.resources.prototype import (
    PROTOTYPE_RESOURCE_SCHEMA,
    PROTOTYPE_RESOURCE_STATE_SCHEMA,
)


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = (
    ROOT
    / "docs"
    / "architecture"
    / "capability-binding-state-cbs0-inventory.json"
)


def _inventory() -> dict:
    value = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _source_paths(value: object) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"source_paths", "authoritative_sources"}:
                assert isinstance(item, list)
                paths.update(str(path) for path in item)
            else:
                paths.update(_source_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(_source_paths(item))
    return paths


def test_cbs0_inventory_is_pinned_and_all_source_paths_exist() -> None:
    inventory = _inventory()

    assert inventory["schema"] == (
        "adaos.architecture.capability_binding_state_inventory.v1"
    )
    assert inventory["inventory_id"] == "cbs0-flowboard-work-items-20260921"
    assert inventory["observed_at"] == "2026-09-21"
    assert len(inventory["source"]["revision"]) == 40
    assert inventory["source"]["runtime_mutated"] is False

    paths = _source_paths(inventory)
    assert paths
    missing = sorted(path for path in paths if not (ROOT / path).is_file())
    assert missing == []


def test_cbs0_capability_usages_are_classified_without_semantic_aliases() -> None:
    inventory = _inventory()
    usages = inventory["capability_usages"]
    classifications = {item["classification"] for item in usages}

    assert classifications == {
        "actor_authorization",
        "placement_feature",
        "planning_diagnostic",
        "provider_feature",
        "requested_sdk_authority",
        "runtime_service_advertisement",
        "ui_affordance",
    }
    assert len({item["usage_id"] for item in usages}) == len(usages)
    skill_usage = next(
        item for item in usages if item["usage_id"] == "skill-runtime-authority"
    )
    assert "never means a provided CapabilityContract" in skill_usage["collision_rule"]


def test_cbs0_target_mapping_and_namespaces_are_complete_and_disjoint() -> None:
    inventory = _inventory()
    mappings = {item["target"]: item for item in inventory["target_mappings"]}

    assert set(mappings) == {
        "ApplicationRequirement",
        "CapabilityContract",
        "StateContract",
        "BindingDefinition",
        "PackageRelease",
        "BindingInstance",
        "StateSpace",
        "EvidenceClaim",
        "ApplicationResolution",
        "ResolutionPlan",
        "WorkspaceLock",
        "SemanticGraph",
    }
    assert mappings["WorkspaceLock"]["status"] == "existing_authority"
    assert mappings["CapabilityContract"]["status"] == "missing"
    assert mappings["StateSpace"]["status"] == "missing"

    namespaces = inventory["reserved_namespaces"]
    schemas = namespaces["schema_names"]
    prefixes = namespaces["reference_prefixes"]
    assert namespaces["status"] == "reserved_not_implemented"
    assert len({item["name"] for item in schemas}) == len(schemas)
    assert len({item["prefix"] for item in prefixes}) == len(prefixes)
    assert "state_ref" not in {item["prefix"] for item in prefixes}
    assert {item["value"] for item in namespaces["prohibited_aliases"]} == {
        "binding_id",
        "skill.yaml:capabilities",
        "state_ref",
    }


def test_cbs0_canonical_digest_vector_uses_existing_artifact_convention() -> None:
    vector = _inventory()["canonicalization"]["test_vector"]
    payload = vector["payload"]

    assert canonical_json_bytes(payload).decode("utf-8") == vector["canonical_json"]
    assert canonical_payload_digest(payload) == vector["digest"]

    reordered = {
        "nested": {"a": 1, "z": 2},
        "items": ["b", "a"],
        "name": "flowboard",
        "schema": "adaos.cbs0.digest_vector.v1",
    }
    assert canonical_payload_digest(reordered) == vector["digest"]


def test_cbs0_fixture_matches_current_resource_implementations() -> None:
    fixture = _inventory()["selected_fixture"]
    production = fixture["production_materialization"]
    simulation = fixture["simulation_materialization"]

    assert fixture["current_resource_type"] == "skill.flowboard_skill.work_items"
    assert fixture["current_owner_ref"] == "skill:flowboard_skill"
    assert production["declaration_schema"] == LOCAL_RESOURCE_SCHEMA
    assert production["state_schema"] == LOCAL_RESOURCE_STATE_SCHEMA
    assert production["storage_format"] == "atomic_json_registry"
    assert production["state_locator_template"].endswith("/registry.json")
    assert simulation["declaration_schema"] == PROTOTYPE_RESOURCE_SCHEMA
    assert simulation["state_schema"] == PROTOTYPE_RESOURCE_STATE_SCHEMA
    assert simulation["storage_format"] == "sqlite_resource_states"
    assert simulation["identity_rule"] == (
        "must not reuse the production state_space_ref"
    )


def test_cbs0_preserves_workspace_lock_authority() -> None:
    inventory = _inventory()
    workspace = next(
        item
        for item in inventory["package_and_authority_surfaces"]
        if item["surface_id"] == "workspace-lock"
    )

    assert WORKSPACE_LOCK_SCHEMA == "adaos.workspace.lock.v1"
    assert "sole active authority" in workspace["target_role"]
