from __future__ import annotations

from pathlib import Path

import pytest

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    BindingDefinition,
    BindingDelivery,
    BindingInstance,
    CapabilityContract,
    EnvironmentProfile,
    LocalRevisionObservation,
    StateContract,
    StateLifecycleOperation,
    StateSpace,
)
from adaos.services.capability_binding_state import (
    LegacyCrudProjector,
    LocalIdentityConflict,
    LocalIdentityStore,
    StateAttachmentError,
    build_identity_map,
    calculate_effective_guarantees,
    inspect_state_identity,
    redacted_graph_record,
    validate_state_attachment,
)
from adaos.services.resources.local import LocalCrudResourceService


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
RESOURCE_TYPE = "skill.flowboard_skill.work_items"


def _record_schema() -> dict:
    return {
        "type": "object",
        "required": ["id", "title", "status", "revision"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1},
            "status": {"enum": ["planned", "in_progress", "done"]},
            "revision": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": False,
    }


def _bundle() -> dict:
    schema = _record_schema()
    return {
        "schema": "adaos.resource.local_crud.v1",
        "owner_ref": "skill:flowboard_skill",
        "seed_policy": "if_missing",
        "resource_definition": {
            "schema": "adaos.resource.definition.v1",
            "resource_type": RESOURCE_TYPE,
            "version": "1.0.0",
            "title": "Work items",
            "description": "Durable work items.",
            "scope": {"owner": "skill:flowboard_skill"},
            "authority": {
                "provider": "local_crud",
                "binding": "flowboard_skill",
                "writes": "optimistic",
                "source_of_truth": "local_skill_state",
            },
            "record_schema_ref": f"inline:{RESOURCE_TYPE}",
            "record_schema": schema,
            "query": {"default": "all", "filters": ["id"], "sort": ["title"], "cursor": False, "include": []},
            "operations": [
                {"id": "list", "kind": "list", "risk": "read"},
                {"id": "show", "kind": "show", "risk": "read"},
                {"id": "create", "kind": "create", "risk": "low"},
                {"id": "update", "kind": "update", "risk": "low"},
                {"id": "delete", "kind": "delete", "risk": "medium"},
            ],
            "views": [{"id": "board", "kind": "board", "title": "Board"}],
            "events": {"emits": ["resource.record.updated"]},
            "i18n": {"default_locale": "en", "locales": ["en"]},
            "access": {"role_fixtures": {"owner": {"update": "allowed"}}},
            "privacy": {"sensitivity": "workspace", "retention": "skill_owned", "external_export": "denied"},
            "readiness": {"states": ["ready"]},
        },
        "seed": [{"id": "one", "title": "Plan", "status": "planned", "revision": 1}],
    }


def _contracts(*, profile_serializable: bool = True):
    schema_digest = canonical_payload_digest(_record_schema())
    state = StateContract.create(
        state_contract_ref="state-contract:flowboard.work-items",
        version="1.0.0",
        schema_locks=({"lock_id": f"inline:{RESOURCE_TYPE}", "digest": schema_digest},),
        invariant_refs=("record-id-unique", "revision-monotonic"),
        guarantees={
            "consistency": ["snapshot", "serializable"],
            "durability": ["persistent"],
            "isolation": ["single_writer"],
        },
        lifecycle={"retention": "skill_owned", "destruction": "explicit_authority", "backup_required": True},
        ownership_constraints={"allowed_owner_kinds": ["skill"], "custodian_may_differ": True, "mutation_authority_required": True},
        portability_class="portable",
        migration_compatibility={"strategy": "explicit_edge", "from_versions": ["1.0.0"], "migration_lock_refs": []},
    )
    capability = CapabilityContract.create(
        capability_ref="capability:resource.records.manage",
        version="1.0.0",
        title="Manage records",
        operations=({"operation_id": "update", "input_schema": {"type": "object"}, "output_schema": {"type": "object"}, "errors": ["conflict"]},),
        authority_requirements=("resource.records.write",),
        state_ports=({
            "port_id": "records",
            "contract_ref": state.state_contract_ref,
            "contract_range": "^1.0.0",
            "access": "writes",
            "requirements": {
                "consistency_at_least": "serializable",
                "durability": "persistent",
                "isolation": "single_writer",
                "mutation_authority": "workspace",
            },
        },),
        conformance_refs=("scenario:resource.records.manage.v1",),
    )
    definition = BindingDefinition.create(
        binding_definition_ref="binding-definition:resource.records.local-json",
        version="1.0.0",
        capability_ref=capability.capability_ref,
        capability_version=capability.version,
        entry_protocol="adaos.resource.provider.v1",
        implementation_entrypoint="resource.records.local",
        state_support=({
            "state_contract_ref": state.state_contract_ref,
            "contract_range": "^1.0.0",
            "access_modes": ["reads", "writes"],
            "guarantees": {
                "consistency": ["snapshot", "serializable"],
                "durability": ["persistent"],
                "isolation": ["single_writer"],
            },
        },),
        modes=("production",),
        environment_constraints={"profile_classes": ["local"], "provider_features": ["atomic_replace", "mutation_lock"]},
        authority_requirements=("resource.records.write",),
        conformance_obligations=("capability_conformance", "state_compatibility"),
    )
    profile = EnvironmentProfile.create(
        profile_ref="profile:local/default",
        profile_class="local",
        modes=("simulation", "production"),
        provider_features=("atomic_replace", "mutation_lock"),
        guarantees={
            "consistency": ["snapshot", *( ["serializable"] if profile_serializable else [])],
            "durability": ["persistent"],
            "isolation": ["single_writer"],
        },
        authorities=("resource.records.write", "workspace.activate"),
    )
    return capability, state, definition, profile


def _delivery(definition: BindingDefinition, *, package_digest: str = DIGEST_A) -> BindingDelivery:
    return BindingDelivery.create(
        binding_definition_ref=definition.binding_definition_ref,
        binding_definition_digest=definition.digest,
        logical_entrypoint="resource.records.local",
        package={"kind": "skill", "id": "flowboard_provider", "version": "1.0.0", "digest": package_digest},
        physical_member="handlers/main.py",
    )


def _projection(tmp_path: Path, *, profile_serializable: bool = True):
    resource = LocalCrudResourceService(state_dir=tmp_path / "runtime")
    resource.materialize(_bundle())
    store = LocalIdentityStore(tmp_path / "identities")
    capability, state, definition, profile = _contracts(profile_serializable=profile_serializable)
    projector = LegacyCrudProjector(resource, store)
    projected = projector.project(
        RESOURCE_TYPE,
        workspace_ref="workspace:local",
        tenant_ref="tenant:test",
        capability_contract=capability,
        state_contract=state,
        binding_definition=definition,
        delivery=_delivery(definition),
        environment_profile=profile,
    )
    return resource, store, projector, projected, capability, state, definition, profile


def test_legacy_projection_is_deterministic_and_does_not_rewrite_records(tmp_path: Path) -> None:
    resource, _store, projector, first, capability, state, definition, profile = _projection(tmp_path)
    registry_before = resource.registry_path.read_bytes()
    second = projector.project(
        RESOURCE_TYPE,
        workspace_ref="workspace:local",
        tenant_ref="tenant:test",
        capability_contract=capability,
        state_contract=state,
        binding_definition=definition,
        delivery=_delivery(definition),
        environment_profile=profile,
    )

    assert second == first
    assert first.binding_instance.revision == 1
    assert first.state_space.revision == 1
    assert first.state_space.generation == 1
    assert resource.registry_path.read_bytes() == registry_before
    assert resource.snapshot(RESOURCE_TYPE)["records"] == _bundle()["seed"]


def test_state_generation_appends_revision_while_package_relocation_preserves_identities(tmp_path: Path) -> None:
    resource, store, projector, first, capability, state, definition, profile = _projection(tmp_path)
    resource.operate(
        RESOURCE_TYPE,
        "update",
        record_id="one",
        payload={"status": "done"},
        expected_revision=1,
    )
    after_write = projector.project(
        RESOURCE_TYPE,
        workspace_ref="workspace:local",
        tenant_ref="tenant:test",
        capability_contract=capability,
        state_contract=state,
        binding_definition=definition,
        delivery=_delivery(definition),
        environment_profile=profile,
    )
    relocated = projector.project(
        RESOURCE_TYPE,
        workspace_ref="workspace:local",
        tenant_ref="tenant:test",
        capability_contract=capability,
        state_contract=state,
        binding_definition=definition,
        delivery=_delivery(definition, package_digest=DIGEST_B),
        environment_profile=profile,
    )

    assert after_write.state_space.stable_ref == first.state_space.stable_ref
    assert after_write.state_space.revision == 2
    assert after_write.state_space.predecessor_digest == first.state_space.digest
    assert relocated.binding_instance.stable_ref == first.binding_instance.stable_ref
    assert relocated.binding_instance.revision == 2
    assert relocated.binding_instance.predecessor_digest == first.binding_instance.digest
    assert relocated.state_space == after_write.state_space
    assert store.latest(first.state_space.stable_ref, StateSpace) == after_write.state_space


def test_effective_guarantees_admit_and_reject_state_ports(tmp_path: Path) -> None:
    _resource, _store, _projector, projected, capability, state, definition, profile = _projection(tmp_path)
    effective = validate_state_attachment(
        capability_contract=capability,
        port_id="records",
        state_contract=state,
        binding_definition=definition,
        binding_instance=projected.binding_instance,
        state_space=projected.state_space,
        environment_profile=profile,
        relation=projected.relations[0],
    )
    assert effective == {
        "consistency": ("serializable", "snapshot"),
        "durability": ("persistent",),
        "isolation": ("single_writer",),
    }

    weak_profile = _contracts(profile_serializable=False)[3]
    weak_instance = BindingInstance.create(
        binding_instance_ref=projected.binding_instance.stable_ref,
        revision=2,
        predecessor_digest=projected.binding_instance.digest,
        workspace_ref="workspace:local",
        tenant_ref="tenant:test",
        binding_definition_ref=definition.binding_definition_ref,
        binding_definition_digest=definition.digest,
        delivery_digest=projected.binding_instance.to_dict()["delivery_digest"],
        environment_profile_ref=weak_profile.profile_ref,
        environment_profile_digest=weak_profile.digest,
        mode="production",
        local_binding_ref="local-crud:flowboard_skill",
        authority_epoch=1,
    )
    relation = type(projected.relations[0]).create(
        relation_ref=projected.relations[0].stable_ref,
        binding_instance_ref=weak_instance.stable_ref,
        binding_instance_revision_digest=weak_instance.digest,
        state_space_ref=projected.state_space.stable_ref,
        state_space_revision_digest=projected.state_space.digest,
        port_id="records",
        access="writes",
    )
    with pytest.raises(StateAttachmentError, match="consistency_at_least"):
        validate_state_attachment(
            capability_contract=capability,
            port_id="records",
            state_contract=state,
            binding_definition=definition,
            binding_instance=weak_instance,
            state_space=projected.state_space,
            environment_profile=weak_profile,
            relation=relation,
        )


def test_local_revision_store_rejects_stale_predecessor_and_epoch(tmp_path: Path) -> None:
    _resource, store, _projector, projected, _capability, _state, definition, profile = _projection(tmp_path)
    stale = BindingInstance.create(
        binding_instance_ref=projected.binding_instance.stable_ref,
        revision=2,
        predecessor_digest=DIGEST_B,
        workspace_ref="workspace:local",
        tenant_ref="tenant:test",
        binding_definition_ref=definition.binding_definition_ref,
        binding_definition_digest=definition.digest,
        delivery_digest=projected.binding_instance.to_dict()["delivery_digest"],
        environment_profile_ref=profile.profile_ref,
        environment_profile_digest=profile.digest,
        mode="production",
        local_binding_ref="local-crud:flowboard_skill",
        authority_epoch=1,
    )
    with pytest.raises(LocalIdentityConflict, match="predecessor"):
        store.append(stale)


def test_observations_lifecycle_operations_and_graph_projection_are_separate(tmp_path: Path) -> None:
    _resource, store, _projector, projected, *_ = _projection(tmp_path)
    observation = LocalRevisionObservation.create(
        observation_ref="observation:flowboard/state/ready",
        subject_kind="state_space",
        subject_ref=projected.state_space.stable_ref,
        subject_revision_digest=projected.state_space.digest,
        observation_kind="readiness",
        status="ready",
        observed_at="2026-09-22T00:00:00+00:00",
        details={"private_account": "owner@example.test", "locator": "private"},
    )
    operation = StateLifecycleOperation.create(
        operation_ref="state-operation:flowboard/adopt/1",
        operation="adopt",
        state_space_ref=projected.state_space.stable_ref,
        state_space_revision_digest=projected.state_space.digest,
        authority_ref="skill:flowboard_skill",
        requested_at="2026-09-22T00:00:00+00:00",
        reason="explicit legacy adoption",
    )
    store.put_fact(observation)
    store.put_fact(operation)

    state_graph = redacted_graph_record(projected.state_space)
    observation_graph = redacted_graph_record(observation)
    serialized = str((state_graph, observation_graph)).lower()
    assert "locator_ref" not in state_graph
    assert "tenant_ref" not in state_graph
    assert "logical_owner_ref" not in state_graph
    assert "owner@example.test" not in serialized
    assert "details" not in observation_graph
    assert operation.to_dict()["operation"] == "adopt"
    assert projected.state_space.to_dict().get("lifecycle_operation") is None


def test_operator_inspector_reports_backup_capacity_and_identity_history(tmp_path: Path) -> None:
    _resource, store, _projector, projected, capability, state, definition, profile = _projection(
        tmp_path
    )
    capacity = LocalRevisionObservation.create(
        observation_ref="observation:flowboard/state/capacity",
        subject_kind="state_space",
        subject_ref=projected.state_space.stable_ref,
        subject_revision_digest=projected.state_space.digest,
        observation_kind="capacity",
        status="healthy",
        observed_at="2026-09-23T10:00:00+00:00",
        details={"used_bytes": 4096, "available_bytes": 8192},
    )
    backup = LocalRevisionObservation.create(
        observation_ref="observation:flowboard/state/backup",
        subject_kind="state_space",
        subject_ref=projected.state_space.stable_ref,
        subject_revision_digest=projected.state_space.digest,
        observation_kind="backup",
        status="ready",
        observed_at="2026-09-23T10:01:00+00:00",
        details={
            "backup_ref": "backup:flowboard/2026-09-23",
            "backup_digest": DIGEST_B,
            "restore_tested": True,
        },
    )
    store.put_fact(capacity)
    store.put_fact(backup)
    observations = store.observations(
        subject_ref=projected.state_space.stable_ref,
        subject_revision_digest=projected.state_space.digest,
    )
    inspection = inspect_state_identity(
        projected.state_space,
        binding_instance=projected.binding_instance,
        state_contract=state,
        observations=observations,
    )
    assert inspection["logical_owner_ref"] == "skill:flowboard_skill"
    assert inspection["observations"]["backup"]["details"]["restore_tested"] is True
    assert inspection["observations"]["capacity"]["details"]["used_bytes"] == 4096
    guarantees = calculate_effective_guarantees(
        state_contract=state,
        binding_definition=definition,
        binding_instance=projected.binding_instance,
        state_space=projected.state_space,
        environment_profile=profile,
        observations=observations,
    )
    assert guarantees["operational"] == (
        "backup:ready",
        "backup:restore_tested",
        "capacity:healthy",
        "capacity:observed",
    )
    assert store.revisions(projected.state_space.stable_ref, StateSpace) == (
        projected.state_space,
    )

    requirement = ApplicationRequirement.create(
        requirement_ref="requirement:flowboard/manage-work-items",
        capability_ref=capability.capability_ref,
        contract_range="^1.0.0",
        environment_target={
            "profile_ref": profile.profile_ref,
            "allowed_modes": ["production"],
        },
        policy_constraints={
            "locality": "local",
            "privacy": "workspace",
            "required_authorities": ["resource.records.write"],
        },
        evidence_threshold={
            "required_claim_kinds": ["capability_conformance", "state_compatibility"],
            "allow_stale": False,
        },
    )
    identity_map = build_identity_map(
        requirement=requirement,
        capability_contract=capability,
        state_contracts=(state,),
        binding_definition=definition,
        delivery=_delivery(definition),
        binding_instances=(projected.binding_instance,),
        state_spaces=(projected.state_space,),
    )
    assert identity_map["binding_instances"][0]["ref"] == projected.binding_instance.stable_ref
    assert identity_map["state_spaces"][0]["ref"] == projected.state_space.stable_ref
    assert identity_map["map_digest"].startswith("sha256:")
