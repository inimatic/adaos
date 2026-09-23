from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef, canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    BindingDefinition,
    BindingDelivery,
    CapabilityBindingStateContractError,
    CapabilityContract,
    EnvironmentProfile,
    EvidenceAssessment,
    EvidenceClaim,
    StateContract,
    validate_state_contract_locks,
)
from adaos.services.artifact_pipeline import (
    ArtifactAttestationVerificationError,
    ArtifactTrustStore,
    Ed25519ArtifactSigner,
    build_artifact_package,
)
from adaos.services.capability_binding_state import (
    PORTABLE_BUNDLE_PREDICATE,
    PortableContractCatalog,
    PortableContractConflict,
    admit_portable_bundle,
    contract_diff,
    contract_reference,
    explicit_compatibility_edge,
    lint_persistent_terminology,
    portable_bundle_digest,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _capability() -> CapabilityContract:
    return CapabilityContract.create(
        capability_ref="capability:resource.records.manage",
        version="1.0.0",
        title="Manage typed resource records",
        operations=(
            {
                "operation_id": "create",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "errors": ["conflict", "invalid_record"],
            },
            {
                "operation_id": "update",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "errors": ["conflict", "not_found"],
            },
        ),
        invariants=("record identity is stable",),
        effects=("writes persistent state",),
        authority_requirements=("resource.records.write",),
        state_ports=(
            {
                "port_id": "records",
                "contract_ref": "state-contract:flowboard.work-items",
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
        compatibility={"backward_compatible_with": []},
    )


def _state_contract() -> StateContract:
    return StateContract.create(
        state_contract_ref="state-contract:flowboard.work-items",
        version="1.0.0",
        schema_locks=({"lock_id": "inline:flowboard.work-items", "digest": DIGEST_A},),
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


def _definition() -> BindingDefinition:
    capability = _capability()
    return BindingDefinition.create(
        binding_definition_ref="binding-definition:resource.records.local-json",
        version="1.0.0",
        capability_ref=capability.capability_ref,
        capability_version=capability.version,
        entry_protocol="adaos.resource.provider.v1",
        implementation_entrypoint="resource.records.local",
        state_support=(
            {
                "state_contract_ref": "state-contract:flowboard.work-items",
                "contract_range": "^1.0.0",
                "access_modes": ["reads", "writes"],
                "guarantees": {
                    "consistency": ["snapshot", "serializable"],
                    "durability": ["persistent"],
                    "isolation": ["single_writer"],
                },
            },
        ),
        modes=("production",),
        environment_constraints={
            "profile_classes": ["local"],
            "provider_features": ["atomic_replace", "mutation_lock"],
        },
        authority_requirements=("resource.records.write",),
        conformance_obligations=(
            "capability_conformance",
            "state_compatibility",
        ),
    )


def _profile() -> EnvironmentProfile:
    return EnvironmentProfile.create(
        profile_ref="profile:local/default",
        profile_class="local",
        modes=("simulation", "production"),
        provider_features=("atomic_replace", "mutation_lock"),
        guarantees={
            "consistency": ["snapshot", "serializable"],
            "durability": ["persistent"],
            "isolation": ["single_writer"],
        },
        authorities=("resource.records.write", "workspace.activate"),
    )


def test_portable_contracts_are_canonical_immutable_and_round_trip() -> None:
    records = (_capability(), _state_contract(), _definition(), _profile())
    for record in records:
        payload = record.to_dict()
        assert payload[record.DIGEST_FIELD] == canonical_payload_digest(
            {key: value for key, value in payload.items() if key != record.DIGEST_FIELD}
        )
        assert type(record).from_mapping(payload) == record
        assert type(record).from_mapping(
            json.loads(json.dumps(payload, ensure_ascii=False))
        ).digest == record.digest

    with pytest.raises(TypeError):
        _capability()._payload["title"] = "mutated"  # type: ignore[index]
    state_ports = _capability()._payload["state_ports"]
    with pytest.raises(TypeError):
        state_ports[0]["access"] = "reads"  # type: ignore[index]


def test_state_contract_references_existing_locks_without_copying_schema_content() -> None:
    contract = _state_contract()
    validate_state_contract_locks(
        contract,
        schema_locks=({"lock_id": "inline:flowboard.work-items", "digest": DIGEST_A},),
    )
    with pytest.raises(CapabilityBindingStateContractError, match="schema locks"):
        validate_state_contract_locks(
            contract,
            schema_locks=({"lock_id": "inline:flowboard.work-items", "digest": DIGEST_B},),
        )


def test_contracts_reject_unknown_fields_versions_secrets_and_physical_entrypoints() -> None:
    payload = _capability().to_dict()
    payload["unknown"] = True
    with pytest.raises(CapabilityBindingStateContractError, match="Additional properties"):
        CapabilityContract.from_mapping(payload)

    payload = _capability().to_dict()
    payload["schema"] = "adaos.capability.contract.v2"
    with pytest.raises(CapabilityBindingStateContractError, match="was expected"):
        CapabilityContract.from_mapping(payload)

    with pytest.raises(CapabilityBindingStateContractError, match="logical id"):
        BindingDefinition.create(
            binding_definition_ref="binding-definition:bad",
            version="1.0.0",
            capability_ref="capability:resource.records.manage",
            capability_version="1.0.0",
            entry_protocol="v1",
            implementation_entrypoint="handlers/main.py",
            state_support=(),
            modes=("production",),
            environment_constraints={"profile_classes": ["local"], "provider_features": []},
            authority_requirements=(),
            conformance_obligations=(),
        )


def test_portable_contract_distinguishes_pagination_from_credential_tokens() -> None:
    accepted = CapabilityContract.create(
        capability_ref="capability:mail.messages.list",
        version="1.0.0",
        title="List mail messages",
        operations=(
            {
                "operation_id": "list_messages",
                "input_schema": {
                    "type": "object",
                    "properties": {"page_token": {"type": "string"}},
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"next_page_token": {"type": "string"}},
                },
                "errors": ["provider_unavailable"],
            },
        ),
    )

    assert accepted.capability_ref == "capability:mail.messages.list"
    payload = accepted.to_dict()
    payload.pop(accepted.DIGEST_FIELD)
    payload["operations"][0]["input_schema"]["properties"]["access_token"] = {
        "type": "string"
    }
    with pytest.raises(
        CapabilityBindingStateContractError, match="prohibited credential field"
    ):
        CapabilityContract.from_mapping(payload)

    with pytest.raises(CapabilityBindingStateContractError, match="absolute path"):
        EvidenceClaim.create(
            claim_ref="evidence-claim:bad-path",
            claim_kind="capability_conformance",
            subjects=({"kind": "capability_contract", "ref": _capability().capability_ref, "digest": _capability().digest},),
            environment={"profile_ref": _profile().profile_ref, "profile_digest": _profile().digest},
            dependencies=(),
            suite_digest=DIGEST_A,
            evidence_digest=DIGEST_B,
            provenance={"issuer": "test", "runner": "C:\\private\\runner.exe"},
            issued_at="2026-09-22T00:00:00+00:00",
            freshness={"max_age_seconds": 3600, "invalidated_by": ["dependency_change"]},
            result="verified",
            redaction={"portable": True, "omitted_fields": []},
            portability_scope="portable",
        )


def test_evidence_requirement_and_assessment_keep_distinct_semantics() -> None:
    capability = _capability()
    definition = _definition()
    profile = _profile()
    claim = EvidenceClaim.create(
        claim_ref="evidence-claim:flowboard-conformance",
        claim_kind="capability_conformance",
        subjects=(
            {"kind": "capability_contract", "ref": capability.capability_ref, "digest": capability.digest},
            {"kind": "binding_definition", "ref": definition.binding_definition_ref, "digest": definition.digest},
        ),
        environment={"profile_ref": profile.profile_ref, "profile_digest": profile.digest},
        dependencies=({"ref": "python", "observed_version": "3.12"},),
        suite_digest=DIGEST_A,
        evidence_digest=DIGEST_B,
        provenance={"issuer": "adaos:test", "runner": "pytest", "run_id": "cbs1"},
        issued_at="2026-09-22T00:00:00+00:00",
        freshness={"max_age_seconds": 86400, "invalidated_by": ["dependency_change", "environment_change"]},
        result="verified",
        redaction={"portable": True, "omitted_fields": ["local_paths"]},
        portability_scope="portable",
    )
    assessment = EvidenceAssessment.create(
        assessment_ref="evidence-assessment:flowboard-conformance/current",
        claim_ref=claim.claim_ref,
        claim_digest=claim.digest,
        evaluated_at="2026-09-22T00:01:00+00:00",
        policy_digest=DIGEST_A,
        status="admissible",
    )
    requirement = ApplicationRequirement.create(
        requirement_ref="requirement:flowboard/manage-work-items",
        capability_ref=capability.capability_ref,
        contract_range="^1.0.0",
        environment_target={
            "profile_ref": profile.profile_ref,
            "allowed_modes": ["simulation", "production"],
        },
        policy_constraints={"locality": "local", "required_authorities": ["resource.records.write"]},
        evidence_threshold={"required_claim_kinds": ["capability_conformance", "state_compatibility"], "allow_stale": False},
    )

    assert claim.to_dict()["result"] == "verified"
    assert assessment.to_dict()["status"] == "admissible"
    assert "package" not in requirement.to_dict()
    assert "provider" not in requirement.to_dict()


def test_terminology_lint_compatibility_and_generated_contract_reference() -> None:
    issues = lint_persistent_terminology(
        {
            "state_ref": "webui-state-is-not-persistent-here",
            "manifest": {"capabilities": ["ambiguous"]},
            "state_space_ref": "state-space:allowed",
        }
    )
    assert [item.code for item in issues] == [
        "persistent_state_ref",
        "ambiguous_capabilities",
    ]
    assert lint_persistent_terminology(
        {"state_space_ref": "state-space:allowed", "provided_capabilities": []}
    ) == ()

    older = _capability()
    newer = CapabilityContract.create(
        capability_ref=older.capability_ref,
        version="1.1.0",
        title="Manage typed resource records",
        operations=older.to_dict()["operations"],
        invariants=older.to_dict()["invariants"],
        effects=older.to_dict()["effects"],
        authority_requirements=older.to_dict()["authority_requirements"],
        dependencies=older.to_dict()["dependencies"],
        state_ports=older.to_dict()["state_ports"],
        conformance_refs=older.to_dict()["conformance_refs"],
        compatibility={"backward_compatible_with": ["1.0.0"]},
    )
    edge = explicit_compatibility_edge(newer, older)
    assert edge["compatible"] is True
    assert edge["basis"] == "backward_compatible_with"
    reference = contract_reference(newer)
    assert reference["ref"] == newer.capability_ref
    assert reference["operations"] == ["create", "update"]
    diff = contract_diff(older, newer)
    assert diff["changed"] is True
    assert {item["field"] for item in diff["changes"]} == {"compatibility", "version"}


def test_portable_bundle_reuses_package_attestation_trust_policy(tmp_path: Path) -> None:
    capability = _capability()
    state = _state_contract()
    definition = _definition()
    profile = _profile()
    claim = EvidenceClaim.create(
        claim_ref="evidence-claim:portable-bundle",
        claim_kind="capability_conformance",
        subjects=(
            {
                "kind": "capability_contract",
                "ref": capability.capability_ref,
                "digest": capability.digest,
            },
            {
                "kind": "binding_definition",
                "ref": definition.binding_definition_ref,
                "digest": definition.digest,
            },
        ),
        environment={"profile_ref": profile.profile_ref, "profile_digest": profile.digest},
        dependencies=(),
        suite_digest=DIGEST_A,
        evidence_digest=DIGEST_B,
        provenance={"issuer": "trust.example", "runner": "pytest", "run_id": "portable"},
        issued_at="2026-09-23T00:00:00+00:00",
        freshness={"max_age_seconds": 3600, "invalidated_by": ["dependency_change"]},
        result="verified",
        redaction={"portable": True, "omitted_fields": ["credentials"]},
        portability_scope="portable",
    )
    records = (capability, state, definition, claim)
    bundle_digest = portable_bundle_digest(records)
    signer = Ed25519ArtifactSigner.generate(issuer="trust.example")
    trust = ArtifactTrustStore(tmp_path / "trust.json")
    trust.add(signer.trusted_key(purposes=("package",)))
    package_digest = "sha256:" + "d" * 64
    attestation = signer.sign(
        subject_kind="package",
        subject_digest=package_digest,
        project_id="flowboard_portable",
        predicate_type=PORTABLE_BUNDLE_PREDICATE,
        predicate_digest=bundle_digest,
        issued_at="2026-09-23T00:00:00+00:00",
    )
    admitted = admit_portable_bundle(
        records,
        delivering_package_digest=package_digest,
        project_id="flowboard_portable",
        trust_domain="trust.example",
        attestation=attestation,
        trust_store=trust,
    )
    assert admitted["admitted"] is True
    assert admitted["bundle_digest"] == bundle_digest

    with pytest.raises(ArtifactAttestationVerificationError, match="issuer"):
        admit_portable_bundle(
            records,
            delivering_package_digest=package_digest,
            project_id="flowboard_portable",
            trust_domain="another.example",
            attestation=attestation,
            trust_store=trust,
        )


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-cbs-fixtures",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("skills/",),
    )


def _package(root: Path, *, package_id: str, member: str):
    package_root = root / package_id
    package_root.mkdir(parents=True)
    (package_root / "skill.yaml").write_text(
        f"name: {package_id}\nversion: 1.0.0\n", encoding="utf-8"
    )
    target = package_root / member
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def invoke(payload):\n    return payload\n", encoding="utf-8")
    (package_root / "binding.definition.json").write_text(
        json.dumps(_definition().to_dict(), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return build_artifact_package(package_root, kind="skill", source_ref=_source())


def test_package_relocation_changes_delivery_not_binding_identity(tmp_path: Path) -> None:
    definition = _definition()
    first = _package(tmp_path, package_id="flowboard_provider_a", member="handlers/main.py")
    second = _package(tmp_path, package_id="flowboard_provider_b", member="runtime/provider.py")

    deliveries = (
        BindingDelivery.create(
            binding_definition_ref=definition.binding_definition_ref,
            binding_definition_digest=definition.digest,
            logical_entrypoint="resource.records.local",
            package={"kind": first.ref.kind, "id": first.ref.artifact_id, "version": first.ref.version, "digest": first.ref.digest},
            physical_member="handlers/main.py",
        ),
        BindingDelivery.create(
            binding_definition_ref=definition.binding_definition_ref,
            binding_definition_digest=definition.digest,
            logical_entrypoint="resource.records.local",
            package={"kind": second.ref.kind, "id": second.ref.artifact_id, "version": second.ref.version, "digest": second.ref.digest},
            physical_member="runtime/provider.py",
        ),
    )

    assert first.ref.digest != second.ref.digest
    assert deliveries[0].digest != deliveries[1].digest
    assert {item.binding_definition_digest for item in deliveries} == {definition.digest}


def test_portable_catalog_is_content_addressed_and_rejects_identity_mutation(tmp_path: Path) -> None:
    catalog = PortableContractCatalog(tmp_path / "catalog")
    capability = _capability()
    path = catalog.put(capability)
    assert path.is_file()
    assert catalog.put(capability) == path
    assert catalog.load(capability.digest, CapabilityContract) == capability

    changed = capability.to_dict()
    changed["title"] = "Different meaning"
    changed["contract_digest"] = canonical_payload_digest(
        {key: value for key, value in changed.items() if key != "contract_digest"}
    )
    with pytest.raises(PortableContractConflict, match="different content"):
        catalog.put(CapabilityContract.from_mapping(changed))
