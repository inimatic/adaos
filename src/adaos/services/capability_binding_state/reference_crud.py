"""Executable Flowboard CRUD fixture used by the CBS architectural proof."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from adaos.domain.artifact_release import ArtifactPackageRef, canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    BindingDefinition,
    BindingDelivery,
    CapabilityContract,
    EnvironmentProfile,
    EvidenceAssessment,
    EvidenceClaim,
    StateContract,
)


FLOWBOARD_RESOURCE_TYPE = "skill.flowboard_skill.work_items"
FLOWBOARD_PROTOTYPE_RESOURCE_TYPE = "prototype.flowboard.work_items"
FLOWBOARD_CAPABILITY_REF = "capability:resource.records.manage"
FLOWBOARD_STATE_CONTRACT_REF = "state-contract:flowboard.work-items"
FLOWBOARD_REQUIREMENT_REF = "requirement:flowboard/manage-work-items"


def flowboard_record_schema() -> dict[str, Any]:
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


def flowboard_bundle() -> dict[str, Any]:
    record_schema = flowboard_record_schema()
    return {
        "schema": "adaos.resource.local_crud.v1",
        "owner_ref": "skill:flowboard_skill",
        "seed_policy": "if_missing",
        "resource_definition": {
            "schema": "adaos.resource.definition.v1",
            "resource_type": FLOWBOARD_RESOURCE_TYPE,
            "version": "1.0.0",
            "title": "Work items",
            "description": "Durable work items owned by the Flowboard skill.",
            "scope": {"owner": "skill:flowboard_skill"},
            "authority": {
                "provider": "local_crud",
                "binding": "flowboard_skill",
                "writes": "optimistic",
                "source_of_truth": "local_skill_state",
            },
            "record_schema_ref": f"inline:{FLOWBOARD_RESOURCE_TYPE}",
            "record_schema": record_schema,
            "query": {
                "default": "all",
                "filters": ["id", "status", "search"],
                "sort": ["title"],
                "cursor": False,
                "include": [],
            },
            "operations": [
                {"id": "list", "kind": "list", "risk": "read"},
                {"id": "show", "kind": "show", "risk": "read"},
                {"id": "create", "kind": "create", "risk": "low"},
                {"id": "update", "kind": "update", "risk": "low"},
                {"id": "delete", "kind": "delete", "risk": "medium"},
            ],
            "views": [
                {"id": "board", "kind": "board", "title": "Board"},
                {"id": "form", "kind": "form", "title": "Form"},
            ],
            "events": {
                "emits": [
                    "resource.record.created",
                    "resource.record.updated",
                    "resource.record.deleted",
                ]
            },
            "i18n": {"default_locale": "en", "locales": ["en", "ru"]},
            "access": {
                "role_fixtures": {
                    "owner": {"create": "allowed", "update": "allowed", "delete": "allowed"},
                    "member": {"create": "allowed", "update": "allowed", "delete": "allowed"},
                    "guest": {"create": "denied", "update": "denied", "delete": "denied"},
                }
            },
            "privacy": {
                "sensitivity": "workspace",
                "retention": "skill_owned",
                "external_export": "denied",
            },
            "readiness": {"states": ["ready", "empty", "validation_error"]},
        },
        "seed": [
            {"id": "one", "title": "Plan release", "status": "planned", "revision": 1},
            {"id": "two", "title": "Ship release", "status": "done", "revision": 1},
        ],
    }


def flowboard_prototype_bundle() -> dict[str, Any]:
    """Materialize the same contract scenario in disposable Builder Preview state."""

    record_schema = flowboard_record_schema()

    def activity(operation: str) -> dict[str, Any]:
        input_schema: dict[str, Any] = {"type": "object"}
        output_schema: dict[str, Any] = record_schema
        if operation == "list":
            input_schema = {"type": "object", "additionalProperties": False}
            output_schema = {"type": "array", "items": record_schema}
        elif operation == "show":
            input_schema = {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
                "additionalProperties": False,
            }
            output_schema = {"oneOf": [record_schema, {"type": "null"}]}
        elif operation == "create":
            input_schema = {
                "type": "object",
                "required": ["record"],
                "properties": {"record": record_schema},
                "additionalProperties": False,
            }
        elif operation == "update":
            input_schema = {
                "type": "object",
                "required": ["id", "patch"],
                "properties": {
                    "id": {"type": "string"},
                    "patch": {"type": "object"},
                },
                "additionalProperties": False,
            }
        elif operation == "delete":
            input_schema = {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
                "additionalProperties": False,
            }
        return {
            "activity_id": operation,
            "operation": "get" if operation == "show" else operation,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "side_effect_class": (
                "read_only" if operation in {"list", "show"} else "local_reversible"
            ),
            "implementation_status": "prototype_only",
            "implementation_ref": None,
        }

    definition = flowboard_bundle()["resource_definition"]
    definition = {
        **definition,
        "resource_type": FLOWBOARD_PROTOTYPE_RESOURCE_TYPE,
        "version": "0.0.0-prototype",
        "scope": {"owner": "project:flowboard"},
        "authority": {
            "provider": "prototype",
            "binding": "flowboard.work_items",
            "writes": "local_reversible",
            "source_of_truth": "builder_preview",
        },
        # The semantic record lock is intentionally shared; the StateSpace is not.
        "record_schema_ref": f"inline:{FLOWBOARD_RESOURCE_TYPE}",
        "privacy": {
            "sensitivity": "synthetic",
            "retention": "preview",
            "external_export": "denied",
        },
    }
    data_definition = {
        "schema": "adaos.builder.prototype_data.v1",
        "source_id": "flowboard.work_items",
        "mode": "local_crud",
        "record_schema": record_schema,
        "seed": flowboard_bundle()["seed"],
        "activities": [
            activity(operation)
            for operation in ("list", "show", "create", "update", "delete")
        ],
    }
    return {
        "schema": "adaos.builder.prototype_resource.v1",
        "project_ref": "project:flowboard",
        "change_id": "change-cbs-crud-proof",
        "revision": "semantic-revision-cbs-crud-v1",
        "webui_digest": canonical_payload_digest(
            {"fixture": "flowboard", "revision": "semantic-revision-cbs-crud-v1"}
        ),
        "resource_definition": definition,
        "data_definition": data_definition,
    }


@dataclass(frozen=True, slots=True)
class FlowboardContracts:
    capability: CapabilityContract
    state: StateContract
    production_binding: BindingDefinition
    simulation_binding: BindingDefinition
    sandbox_binding: BindingDefinition
    profile: EnvironmentProfile
    requirement: ApplicationRequirement


def flowboard_contracts() -> FlowboardContracts:
    state = StateContract.create(
        state_contract_ref=FLOWBOARD_STATE_CONTRACT_REF,
        version="1.0.0",
        schema_locks=(
            {
                "lock_id": f"inline:{FLOWBOARD_RESOURCE_TYPE}",
                "digest": canonical_payload_digest(flowboard_record_schema()),
            },
        ),
        invariant_refs=("record-id-unique", "revision-monotonic"),
        guarantees={
            "consistency": ["snapshot", "serializable"],
            "durability": ["persistent"],
            "isolation": ["single_writer"],
        },
        lifecycle={
            "retention": "skill_owned",
            "destruction": "explicit_authority",
            "backup_required": True,
        },
        ownership_constraints={
            "allowed_owner_kinds": ["skill", "application"],
            "custodian_may_differ": True,
            "mutation_authority_required": True,
        },
        portability_class="portable",
        migration_compatibility={
            "strategy": "explicit_edge",
            "from_versions": ["1.0.0"],
            "migration_lock_refs": [],
        },
    )
    operation_schema = {"type": "object"}
    capability = CapabilityContract.create(
        capability_ref=FLOWBOARD_CAPABILITY_REF,
        version="1.0.0",
        title="Manage typed resource records",
        operations=tuple(
            {
                "operation_id": operation,
                "input_schema": operation_schema,
                "output_schema": operation_schema,
                "errors": ["conflict", "not_found", "invalid_record"],
            }
            for operation in ("list", "show", "create", "update", "delete")
        ),
        invariants=("record identity is stable", "record revisions are monotonic"),
        effects=("writes persistent state",),
        authority_requirements=("resource.records.write",),
        state_ports=(
            {
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
            },
        ),
        conformance_refs=("scenario:resource.records.manage.v1",),
    )

    def binding(
        *,
        ref: str,
        entrypoint: str,
        modes: tuple[str, ...],
        features: tuple[str, ...],
    ) -> BindingDefinition:
        return BindingDefinition.create(
            binding_definition_ref=ref,
            version="1.0.0",
            capability_ref=capability.capability_ref,
            capability_version=capability.version,
            entry_protocol="adaos.resource.provider.v1",
            implementation_entrypoint=entrypoint,
            state_support=(
                {
                    "state_contract_ref": state.state_contract_ref,
                    "contract_range": "^1.0.0",
                    "access_modes": ["reads", "writes"],
                    "guarantees": {
                        "consistency": ["snapshot", "serializable"],
                        "durability": ["persistent"],
                        "isolation": ["single_writer"],
                    },
                },
            ),
            modes=modes,
            environment_constraints={
                "profile_classes": ["local"],
                "provider_features": list(features),
            },
            authority_requirements=("resource.records.write",),
            conformance_obligations=("capability_conformance", "state_compatibility"),
        )

    production = binding(
        ref="binding-definition:resource.records.local-json",
        entrypoint="resource.records.local",
        modes=("production",),
        features=("atomic_replace", "mutation_lock"),
    )
    simulation = binding(
        ref="binding-definition:resource.records.prototype-sqlite",
        entrypoint="resource.records.prototype",
        modes=("simulation",),
        features=("sqlite_transaction",),
    )
    sandbox = binding(
        ref="binding-definition:resource.records.sandbox-sqlite",
        entrypoint="resource.records.sandbox",
        modes=("sandbox",),
        features=("sqlite_transaction", "restricted_effects"),
    )
    profile = EnvironmentProfile.create(
        profile_ref="profile:local/default",
        profile_class="local",
        modes=("simulation", "sandbox", "production"),
        provider_features=(
            "atomic_replace",
            "mutation_lock",
            "sqlite_transaction",
            "restricted_effects",
        ),
        guarantees={
            "consistency": ["snapshot", "serializable"],
            "durability": ["persistent"],
            "isolation": ["single_writer"],
        },
        authorities=("resource.records.write", "workspace.activate"),
    )
    requirement = ApplicationRequirement.create(
        requirement_ref=FLOWBOARD_REQUIREMENT_REF,
        capability_ref=capability.capability_ref,
        contract_range="^1.0.0",
        environment_target={
            "profile_ref": profile.profile_ref,
            "allowed_modes": ["simulation", "sandbox", "production"],
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
    return FlowboardContracts(
        capability,
        state,
        production,
        simulation,
        sandbox,
        profile,
        requirement,
    )


def binding_delivery(
    definition: BindingDefinition,
    package: ArtifactPackageRef,
    *,
    physical_member: str,
) -> BindingDelivery:
    return BindingDelivery.create(
        binding_definition_ref=definition.binding_definition_ref,
        binding_definition_digest=definition.digest,
        logical_entrypoint=definition.to_dict()["implementation_entrypoint"],
        package={
            "kind": package.kind,
            "id": package.artifact_id,
            "version": package.version,
            "digest": package.digest,
        },
        physical_member=physical_member,
    )


def conformance_evidence(
    contracts: FlowboardContracts,
    definition: BindingDefinition,
    *,
    suffix: str,
    issued_at: str = "2026-09-22T00:00:00+00:00",
) -> tuple[tuple[EvidenceClaim, EvidenceAssessment], ...]:
    subjects = {
        "capability": {
            "kind": "capability_contract",
            "ref": contracts.capability.capability_ref,
            "digest": contracts.capability.digest,
        },
        "binding": {
            "kind": "binding_definition",
            "ref": definition.binding_definition_ref,
            "digest": definition.digest,
        },
        "state": {
            "kind": "state_contract",
            "ref": contracts.state.state_contract_ref,
            "digest": contracts.state.digest,
        },
    }
    result: list[tuple[EvidenceClaim, EvidenceAssessment]] = []
    for kind, selected in (
        ("capability_conformance", (subjects["capability"], subjects["binding"])),
        ("state_compatibility", (subjects["binding"], subjects["state"])),
    ):
        claim = EvidenceClaim.create(
            claim_ref=f"evidence-claim:flowboard/{suffix}/{kind}",
            claim_kind=kind,
            subjects=selected,
            environment={
                "profile_ref": contracts.profile.profile_ref,
                "profile_digest": contracts.profile.digest,
            },
            dependencies=({"ref": "adaos-core", "observed_version": "0.1"},),
            suite_digest=canonical_payload_digest({"suite": kind, "fixture": "flowboard"}),
            evidence_digest=canonical_payload_digest({"result": "passed", "kind": kind, "suffix": suffix}),
            provenance={"issuer": "adaos:cbs", "runner": "pytest", "run_id": suffix},
            issued_at=issued_at,
            freshness={"max_age_seconds": 86400, "invalidated_by": ["dependency_change", "environment_change"]},
            result="verified",
            redaction={"portable": True, "omitted_fields": ["local_paths", "credentials"]},
            portability_scope="portable",
        )
        assessment = EvidenceAssessment.create(
            assessment_ref=f"evidence-assessment:flowboard/{suffix}/{kind}",
            claim_ref=claim.claim_ref,
            claim_digest=claim.digest,
            evaluated_at=issued_at,
            policy_digest=canonical_payload_digest({"policy": "cbs-crud-v1"}),
            status="admissible",
        )
        result.append((claim, assessment))
    return tuple(result)


def package_mapping(package: ArtifactPackageRef) -> Mapping[str, str]:
    return {
        "kind": package.kind,
        "id": package.artifact_id,
        "version": package.version,
        "digest": package.digest,
    }


__all__ = [
    "FLOWBOARD_CAPABILITY_REF",
    "FLOWBOARD_PROTOTYPE_RESOURCE_TYPE",
    "FLOWBOARD_REQUIREMENT_REF",
    "FLOWBOARD_RESOURCE_TYPE",
    "FLOWBOARD_STATE_CONTRACT_REF",
    "FlowboardContracts",
    "binding_delivery",
    "conformance_evidence",
    "flowboard_bundle",
    "flowboard_prototype_bundle",
    "flowboard_contracts",
    "flowboard_record_schema",
    "package_mapping",
]
