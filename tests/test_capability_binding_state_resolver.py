from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef, canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationRequirement,
    BindingInstance,
    EvidenceAssessment,
    StateAccessRelation,
    StateSpace,
)
from adaos.services.artifact_pipeline import (
    PackageCatalog,
    build_artifact_package,
    build_project_release,
)
from adaos.services.capability_binding_state import (
    LegacyCrudProjector,
    LocalIdentityStore,
    ResolutionFailure,
    SemanticResolver,
)
from adaos.services.capability_binding_state.reference_crud import (
    FLOWBOARD_RESOURCE_TYPE,
    binding_delivery,
    conformance_evidence,
    flowboard_bundle,
    flowboard_contracts,
)
from adaos.services.resources.local import LocalCrudResourceService


SEMANTIC_REVISION = "sha256:" + "c" * 64


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-cbs-fixtures",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("skills/",),
    )


def _package(root: Path, *, package_id: str, member: str):
    source = root / package_id
    source.mkdir(parents=True)
    (source / "skill.yaml").write_text(
        f"name: {package_id}\nversion: 1.0.0\n", encoding="utf-8"
    )
    path = source / member
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def invoke(payload):\n    return payload\n", encoding="utf-8")
    built = build_artifact_package(source, kind="skill", source_ref=_source())
    plan = build_project_release(
        project_id=package_id,
        version="1.0.0",
        source_ref=_source(),
        components=(built.ref,),
        catalog=PackageCatalog(),
    )
    return built, plan


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _fixture(tmp_path: Path):
    contracts = flowboard_contracts()
    production_package, production_plan = _package(
        tmp_path / "packages",
        package_id="flowboard_local_provider",
        member="handlers/main.py",
    )
    simulation_package, simulation_plan = _package(
        tmp_path / "packages",
        package_id="flowboard_prototype_provider",
        member="runtime/prototype.py",
    )
    production_delivery = binding_delivery(
        contracts.production_binding,
        production_package.ref,
        physical_member="handlers/main.py",
    )
    simulation_delivery = binding_delivery(
        contracts.simulation_binding,
        simulation_package.ref,
        physical_member="runtime/prototype.py",
    )

    resource = LocalCrudResourceService(state_dir=tmp_path / "runtime")
    resource.materialize(flowboard_bundle())
    store = LocalIdentityStore(tmp_path / "identities")
    production = LegacyCrudProjector(resource, store).project(
        FLOWBOARD_RESOURCE_TYPE,
        workspace_ref="workspace:local",
        tenant_ref="tenant:test",
        capability_contract=contracts.capability,
        state_contract=contracts.state,
        binding_definition=contracts.production_binding,
        delivery=production_delivery,
        environment_profile=contracts.profile,
        mode="production",
    )

    simulation_instance = BindingInstance.create(
        binding_instance_ref="binding-instance:preview/change-1/flowboard",
        revision=1,
        predecessor_digest=None,
        workspace_ref="workspace:preview/change-1",
        tenant_ref="tenant:test",
        binding_definition_ref=contracts.simulation_binding.binding_definition_ref,
        binding_definition_digest=contracts.simulation_binding.digest,
        delivery_digest=simulation_delivery.digest,
        environment_profile_ref=contracts.profile.profile_ref,
        environment_profile_digest=contracts.profile.digest,
        mode="simulation",
        local_binding_ref="prototype-sqlite:flowboard",
        authority_epoch=1,
    )
    simulation_space = StateSpace.create(
        state_space_ref="state-space:preview/change-1/flowboard/work-items",
        revision=1,
        predecessor_digest=None,
        state_contract_ref=contracts.state.state_contract_ref,
        state_contract_version=contracts.state.version,
        state_contract_digest=contracts.state.digest,
        workspace_ref="workspace:preview/change-1",
        tenant_ref="tenant:test",
        logical_owner_ref="application:flowboard-preview",
        lifecycle_authority_ref="application:flowboard-preview",
        custodian_binding_instance_ref=simulation_instance.stable_ref,
        mutation_authority_ref="workspace:preview/change-1",
        locator_ref="prototype-resource:change-1/flowboard/work-items",
        generation=1,
        authority_epoch=1,
        portability_class="portable",
        schema_locks=contracts.state.to_dict()["schema_locks"],
    )
    simulation_relation = StateAccessRelation.create(
        relation_ref="state-access:preview/change-1/flowboard/records",
        binding_instance_ref=simulation_instance.stable_ref,
        binding_instance_revision_digest=simulation_instance.digest,
        state_space_ref=simulation_space.stable_ref,
        state_space_revision_digest=simulation_space.digest,
        port_id="records",
        access="writes",
    )
    evidence_pairs = (
        *conformance_evidence(contracts, contracts.production_binding, suffix="production"),
        *conformance_evidence(contracts, contracts.simulation_binding, suffix="simulation"),
    )
    claims = tuple(item[0] for item in evidence_pairs)
    assessments = tuple(item[1] for item in evidence_pairs)
    plans = {
        production_package.ref.digest: production_plan,
        simulation_package.ref.digest: simulation_plan,
    }

    def exact_package(candidate):
        return plans[candidate.delivery.package_digest]

    return {
        "contracts": contracts,
        "deliveries": (production_delivery, simulation_delivery),
        "instances": (production.binding_instance, simulation_instance),
        "spaces": (production.state_space, simulation_space),
        "relations": (*production.relations, simulation_relation),
        "claims": claims,
        "assessments": assessments,
        "package_resolver": exact_package,
        "mutable_root": tmp_path / "runtime",
    }


def _resolve(resolver: SemanticResolver, fixture: dict, *, mode: str, **overrides):
    contracts = fixture["contracts"]
    values = {
        "semantic_revision_digest": SEMANTIC_REVISION,
        "target_mode": mode,
        "capability_contracts": (contracts.capability,),
        "state_contracts": (contracts.state,),
        "binding_definitions": (contracts.production_binding, contracts.simulation_binding),
        "deliveries": fixture["deliveries"],
        "environment_profile": contracts.profile,
        "binding_instances": fixture["instances"],
        "state_spaces": fixture["spaces"],
        "relations": fixture["relations"],
        "evidence_claims": fixture["claims"],
        "evidence_assessments": fixture["assessments"],
        "package_resolver": fixture["package_resolver"],
    }
    values.update(overrides)
    return resolver.resolve(contracts.requirement, **values)


def test_one_semantic_revision_resolves_for_semantic_simulation_and_production(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    resolver = SemanticResolver(now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc))
    contracts = fixture["contracts"]
    before = _tree_snapshot(fixture["mutable_root"])

    semantic = resolver.semantic_viability(contracts.requirement, (contracts.capability,))
    simulation = _resolve(resolver, fixture, mode="simulation")
    production = _resolve(resolver, fixture, mode="production")

    assert semantic["viable"] is True
    assert simulation.to_dict()["semantic_revision_digest"] == SEMANTIC_REVISION
    assert production.to_dict()["semantic_revision_digest"] == SEMANTIC_REVISION
    assert simulation.to_dict()["requirement_digest"] == production.to_dict()["requirement_digest"]
    assert simulation.to_dict()["state_attachments"][0]["state_space_ref"].startswith("state-space:preview/")
    assert production.to_dict()["state_attachments"][0]["state_space_ref"].startswith("state-space:workspace-local/")
    assert simulation.to_dict()["delivery"]["package_digest"] != production.to_dict()["delivery"]["package_digest"]
    assert _tree_snapshot(fixture["mutable_root"]) == before


def test_package_resolution_precedes_evidence_and_failure_creates_no_resolution(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    resolver = SemanticResolver(now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc))
    calls: list[str] = []

    def fail_package(candidate):
        calls.append(candidate.delivery.digest)
        raise ValueError("forced exact package conflict")

    with pytest.raises(ResolutionFailure) as caught:
        _resolve(
            resolver,
            fixture,
            mode="production",
            package_resolver=fail_package,
            evidence_claims=(),
            evidence_assessments=(),
        )
    assert calls
    assert {item.code for item in caught.value.rejections} == {"package_conflict"}
    assert not list(tmp_path.rglob("*application-resolution*"))


def test_stale_evidence_is_typed_and_does_not_silently_fallback(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    resolver = SemanticResolver(now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc))
    stale = tuple(
        EvidenceAssessment.create(
            assessment_ref=item.to_dict()["assessment_ref"],
            claim_ref=item.to_dict()["claim_ref"],
            claim_digest=item.to_dict()["claim_digest"],
            evaluated_at=item.to_dict()["evaluated_at"],
            policy_digest=item.to_dict()["policy_digest"],
            status="stale",
            reasons=("dependency fingerprint changed",),
        )
        for item in fixture["assessments"]
    )
    with pytest.raises(ResolutionFailure) as caught:
        _resolve(
            resolver,
            fixture,
            mode="production",
            evidence_assessments=stale,
        )
    assert "stale_evidence" in {item.code for item in caught.value.rejections}


def test_production_never_admits_stale_evidence_even_when_requirement_allows_visibility(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    contracts = fixture["contracts"]
    value = contracts.requirement.to_dict()
    requirement = ApplicationRequirement.create(
        requirement_ref=value["requirement_ref"],
        capability_ref=value["capability_ref"],
        contract_range=value["contract_range"],
        environment_target=value["environment_target"],
        policy_constraints=value["policy_constraints"],
        evidence_threshold={
            **value["evidence_threshold"],
            "allow_stale": True,
        },
    )
    stale = tuple(
        EvidenceAssessment.create(
            assessment_ref=item.to_dict()["assessment_ref"],
            claim_ref=item.to_dict()["claim_ref"],
            claim_digest=item.to_dict()["claim_digest"],
            evaluated_at=item.to_dict()["evaluated_at"],
            policy_digest=item.to_dict()["policy_digest"],
            status="stale",
            reasons=("external dependency changed",),
        )
        for item in fixture["assessments"]
    )
    resolver = SemanticResolver(now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc))

    with pytest.raises(ResolutionFailure) as caught:
        resolver.resolve(
            requirement,
            semantic_revision_digest=SEMANTIC_REVISION,
            target_mode="production",
            capability_contracts=(contracts.capability,),
            state_contracts=(contracts.state,),
            binding_definitions=(contracts.production_binding, contracts.simulation_binding),
            deliveries=fixture["deliveries"],
            environment_profile=contracts.profile,
            binding_instances=fixture["instances"],
            state_spaces=fixture["spaces"],
            relations=fixture["relations"],
            evidence_claims=fixture["claims"],
            evidence_assessments=stale,
            package_resolver=fixture["package_resolver"],
        )

    assert "stale_evidence" in {item.code for item in caught.value.rejections}


def test_resolution_pins_exact_package_delivery_state_and_evidence(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    resolver = SemanticResolver(now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc))
    resolution = _resolve(resolver, fixture, mode="production")
    payload = resolution.to_dict()

    assert payload["resolution_digest"] == canonical_payload_digest(
        {key: value for key, value in payload.items() if key != "resolution_digest"}
    )
    assert payload["project_release_digest"].startswith("sha256:")
    assert payload["package_closure"]
    assert payload["binding_instances"][0]["revision_digest"] == fixture["instances"][0].digest
    assert payload["state_attachments"][0]["revision_digest"] == fixture["spaces"][0].digest
    assert {item["status"] for item in payload["evidence"]} == {"admissible"}
    assert json.loads(json.dumps(payload))["resolution_digest"] == resolution.digest
