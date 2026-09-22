from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic_ns

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef, canonical_payload_digest
from adaos.domain.capability_binding_state import CBSBenchmarkTelemetry, CBSCrudProofBundle
from adaos.services.artifact_pipeline import (
    ActivationError,
    ContentAddressedPackageStore,
    PackageCatalog,
    WorkspaceActivationManager,
    build_artifact_package,
    build_project_release,
)
from adaos.services.capability_binding_state import (
    CBSActivationCoordinator,
    CBSBenchmarkTelemetryStore,
    CBSCrudProofStore,
    FencedLocalCrudWriter,
    LegacyCrudProjector,
    LocalIdentityStore,
    PrototypeCrudProjector,
    ResolutionPlanner,
    SemanticResolver,
    StaleWriterError,
    stage_authority_transition,
)
from adaos.services.capability_binding_state.reference_crud import (
    FLOWBOARD_PROTOTYPE_RESOURCE_TYPE,
    FLOWBOARD_RESOURCE_TYPE,
    binding_delivery,
    conformance_evidence,
    flowboard_bundle,
    flowboard_contracts,
    flowboard_prototype_bundle,
)
from adaos.services.resources.local import LocalCrudResourceService
from adaos.services.resources.prototype import PrototypeResourceService


pytestmark = pytest.mark.e2e

FIXED_NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
POLICIES = {
    "reload_policy": {
        "mode": "skip",
        "approved_by": "pytest.cbs-e2e",
        "reason": "the proof has no separately running process",
    },
    "health_policy": {
        "mode": "skip",
        "approved_by": "pytest.cbs-e2e",
        "reason": "provider scenarios are the health proof",
    },
}


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-cbs-crud-proof",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("skills/",),
    )


def _package(
    root: Path,
    *,
    artifact_id: str,
    version: str,
    physical_member: str,
    binding_definition: dict,
):
    package_root = root / artifact_id
    package_root.mkdir(parents=True)
    (package_root / "skill.yaml").write_text(
        f"name: {artifact_id}\nversion: {version}\n", encoding="utf-8"
    )
    member = package_root / physical_member
    member.parent.mkdir(parents=True, exist_ok=True)
    member.write_text("def invoke(payload):\n    return payload\n", encoding="utf-8")
    (package_root / "binding.definition.json").write_text(
        json.dumps(binding_definition, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return build_artifact_package(package_root, kind="skill", source_ref=_source())


def _release(package, *, version: str):
    return build_project_release(
        project_id="flowboard_cbs_proof",
        version=version,
        source_ref=_source(),
        components=(package.ref,),
        catalog=PackageCatalog(),
    )


def _run_contract_scenario(service, resource_type: str) -> None:
    created = service.operate(
        resource_type,
        "create",
        record_id="scenario-record",
        payload={
            "id": "scenario-record",
            "title": "Contract scenario",
            "status": "planned",
            "revision": 1,
        },
    )
    assert created["record"]["revision"] == 1
    updated = service.operate(
        resource_type,
        "update",
        record_id="scenario-record",
        payload={"status": "in_progress"},
        expected_revision=1,
    )
    assert updated["record"]["revision"] == 2
    deleted = service.operate(
        resource_type,
        "delete",
        record_id="scenario-record",
        payload={},
        expected_revision=2,
    )
    assert deleted["deleted"] is True


def _resolve(
    resolver: SemanticResolver,
    *,
    contracts,
    semantic_revision_digest: str,
    mode: str,
    definition,
    delivery,
    projection,
    evidence,
    release_plan,
):
    return resolver.resolve(
        contracts.requirement,
        semantic_revision_digest=semantic_revision_digest,
        target_mode=mode,
        capability_contracts=(contracts.capability,),
        state_contracts=(contracts.state,),
        binding_definitions=(definition,),
        deliveries=(delivery,),
        environment_profile=contracts.profile,
        binding_instances=(projection.binding_instance,),
        state_spaces=(projection.state_space,),
        relations=projection.relations,
        evidence_claims=tuple(item[0] for item in evidence),
        evidence_assessments=tuple(item[1] for item in evidence),
        package_resolver=lambda _candidate: release_plan,
    )


def _records(snapshot: dict) -> tuple[int, str]:
    records = sorted(snapshot["records"], key=lambda item: item["id"])
    return len(records), canonical_payload_digest(records)


def test_cbs_crud_executable_proof_preserves_all_five_invariants(tmp_path: Path) -> None:
    started = monotonic_ns()
    contracts = flowboard_contracts()
    semantic_document = {
        "schema": "adaos.application.semantic_fixture.v1",
        "application_ref": "application:flowboard",
        "requirements": [contracts.requirement.to_dict()],
    }
    semantic_bytes = json.dumps(
        semantic_document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    semantic_revision_digest = canonical_payload_digest(semantic_document)

    built_sim = _package(
        tmp_path / "packages",
        artifact_id="flowboard_preview_provider",
        version="0.1.0",
        physical_member="preview/provider.py",
        binding_definition=contracts.simulation_binding.to_dict(),
    )
    built_a = _package(
        tmp_path / "packages",
        artifact_id="flowboard_local_provider_a",
        version="1.0.0",
        physical_member="provider_a/main.py",
        binding_definition=contracts.production_binding.to_dict(),
    )
    built_b = _package(
        tmp_path / "packages",
        artifact_id="flowboard_local_provider_b",
        version="1.0.1",
        physical_member="relocated/provider.py",
        binding_definition=contracts.production_binding.to_dict(),
    )
    release_sim = _release(built_sim, version="0.1.0")
    release_a = _release(built_a, version="1.0.0")
    release_b = _release(built_b, version="1.0.1")
    delivery_sim = binding_delivery(
        contracts.simulation_binding,
        built_sim.ref,
        physical_member="preview/provider.py",
    )
    delivery_a = binding_delivery(
        contracts.production_binding,
        built_a.ref,
        physical_member="provider_a/main.py",
    )
    delivery_b = binding_delivery(
        contracts.production_binding,
        built_b.ref,
        physical_member="relocated/provider.py",
    )

    prototype = PrototypeResourceService(tmp_path / "prototype-state")
    production = LocalCrudResourceService(tmp_path / "production-state")
    prototype.materialize(flowboard_prototype_bundle())
    production.materialize(flowboard_bundle())
    _run_contract_scenario(prototype, FLOWBOARD_PROTOTYPE_RESOURCE_TYPE)
    _run_contract_scenario(production, FLOWBOARD_RESOURCE_TYPE)

    identities = LocalIdentityStore(tmp_path / "identities")
    simulation = PrototypeCrudProjector(prototype, identities).project(
        FLOWBOARD_PROTOTYPE_RESOURCE_TYPE,
        workspace_ref="workspace:flowboard",
        tenant_ref="tenant:cbs-proof",
        capability_contract=contracts.capability,
        state_contract=contracts.state,
        binding_definition=contracts.simulation_binding,
        delivery=delivery_sim,
        environment_profile=contracts.profile,
    )
    production_a = LegacyCrudProjector(production, identities).project(
        FLOWBOARD_RESOURCE_TYPE,
        workspace_ref="workspace:flowboard",
        tenant_ref="tenant:cbs-proof",
        capability_contract=contracts.capability,
        state_contract=contracts.state,
        binding_definition=contracts.production_binding,
        delivery=delivery_a,
        environment_profile=contracts.profile,
    )
    assert simulation.state_space.stable_ref != production_a.state_space.stable_ref

    evidence_sim = conformance_evidence(
        contracts, contracts.simulation_binding, suffix="cbs5-simulation"
    )
    evidence_production = conformance_evidence(
        contracts, contracts.production_binding, suffix="cbs5-production"
    )
    resolver = SemanticResolver(now=lambda: FIXED_NOW)
    resolution_started = monotonic_ns()
    resolution_sim = _resolve(
        resolver,
        contracts=contracts,
        semantic_revision_digest=semantic_revision_digest,
        mode="simulation",
        definition=contracts.simulation_binding,
        delivery=delivery_sim,
        projection=simulation,
        evidence=evidence_sim,
        release_plan=release_sim,
    )
    resolution_a = _resolve(
        resolver,
        contracts=contracts,
        semantic_revision_digest=semantic_revision_digest,
        mode="production",
        definition=contracts.production_binding,
        delivery=delivery_a,
        projection=production_a,
        evidence=evidence_production,
        release_plan=release_a,
    )

    package_store = ContentAddressedPackageStore(tmp_path / "package-store")
    for built in (built_sim, built_a, built_b):
        package_store.put(built.archive_bytes)
    manager = WorkspaceActivationManager(
        workspace_root=tmp_path / "workspace",
        package_store=package_store,
        state_root=tmp_path / "activation-state",
        delayed_verification_seconds=0,
    )

    def generation(state_space_ref: str) -> int:
        if state_space_ref == simulation.state_space.stable_ref:
            snapshot = prototype.snapshot(FLOWBOARD_PROTOTYPE_RESOURCE_TYPE)
        elif state_space_ref == production_a.state_space.stable_ref:
            snapshot = production.snapshot(FLOWBOARD_RESOURCE_TYPE)
        else:
            raise AssertionError(f"unexpected StateSpace: {state_space_ref}")
        assert snapshot is not None
        return max(1, int(snapshot["generation"]))

    planner = ResolutionPlanner(now=lambda: FIXED_NOW)
    coordinator = CBSActivationCoordinator(
        manager,
        identities,
        now=lambda: FIXED_NOW,
        state_generation_observer=generation,
    )
    activation_started = monotonic_ns()
    plan_sim = planner.build(resolution_sim, current_lock=None)
    activated_sim = coordinator.activate(
        plan_sim,
        resolution=resolution_sim,
        release_plan=release_sim,
        idempotency_key="cbs5-simulation",
        **POLICIES,
    )
    plan_a = planner.build(resolution_a, current_lock=activated_sim.activation.workspace_lock)
    activated_a = coordinator.activate(
        plan_a,
        resolution=resolution_a,
        release_plan=release_a,
        idempotency_key="cbs5-production-a",
        **POLICIES,
    )
    assert resolution_sim.to_dict()["semantic_revision_digest"] == semantic_revision_digest
    assert resolution_a.to_dict()["semantic_revision_digest"] == semantic_revision_digest
    assert semantic_bytes == json.dumps(
        semantic_document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    writer = FencedLocalCrudWriter(production, manager, production_a.state_space.stable_ref)
    writer.operate(
        FLOWBOARD_RESOURCE_TYPE,
        "create",
        writer_epoch=1,
        record_id="persistent-record",
        payload={
            "id": "persistent-record",
            "title": "Survives package relocation",
            "status": "planned",
            "revision": 1,
        },
    )
    writer.operate(
        FLOWBOARD_RESOURCE_TYPE,
        "update",
        writer_epoch=1,
        record_id="persistent-record",
        payload={"status": "in_progress"},
        expected_revision=1,
    )
    before_rebind_snapshot = production.snapshot(FLOWBOARD_RESOURCE_TYPE)
    assert before_rebind_snapshot is not None
    before_rebind_records = _records(before_rebind_snapshot)

    current_a = LegacyCrudProjector(production, identities).project(
        FLOWBOARD_RESOURCE_TYPE,
        workspace_ref="workspace:flowboard",
        tenant_ref="tenant:cbs-proof",
        capability_contract=contracts.capability,
        state_contract=contracts.state,
        binding_definition=contracts.production_binding,
        delivery=delivery_a,
        environment_profile=contracts.profile,
    )
    relocated = LegacyCrudProjector(production, identities).project(
        FLOWBOARD_RESOURCE_TYPE,
        workspace_ref="workspace:flowboard",
        tenant_ref="tenant:cbs-proof",
        capability_contract=contracts.capability,
        state_contract=contracts.state,
        binding_definition=contracts.production_binding,
        delivery=delivery_b,
        environment_profile=contracts.profile,
    )
    staged_b = stage_authority_transition(
        identities,
        binding_instance=relocated.binding_instance,
        state_space=relocated.state_space,
        relations=relocated.relations,
    )
    assert current_a.state_space.stable_ref == staged_b.state_space.stable_ref
    assert staged_b.binding_instance.authority_epoch == 2

    resolution_b = _resolve(
        resolver,
        contracts=contracts,
        semantic_revision_digest=semantic_revision_digest,
        mode="production",
        definition=contracts.production_binding,
        delivery=delivery_b,
        projection=staged_b,
        evidence=evidence_production,
        release_plan=release_b,
    )
    resolution_duration_ms = (monotonic_ns() - resolution_started) // 1_000_000
    plan_b = planner.build(resolution_b, current_lock=activated_a.activation.workspace_lock)
    before_failed_lock = manager.load_lock()
    assert before_failed_lock is not None

    def fail_before_commit(phase: str) -> None:
        if phase == "switch-lock":
            raise RuntimeError("CBS5 injected precommit failure")

    with pytest.raises(ActivationError, match="CBS5 injected precommit failure"):
        coordinator.activate(
            plan_b,
            resolution=resolution_b,
            release_plan=release_b,
            idempotency_key="cbs5-production-b-failed",
            phase_hook=fail_before_commit,
            **POLICIES,
        )
    assert manager.load_lock().to_dict() == before_failed_lock.to_dict()
    assert _records(production.snapshot(FLOWBOARD_RESOURCE_TYPE)) == before_rebind_records
    assert writer.operate(
        FLOWBOARD_RESOURCE_TYPE,
        "show",
        writer_epoch=1,
        record_id="persistent-record",
        payload={},
    )["record"]["status"] == "in_progress"

    activated_b = coordinator.activate(
        plan_b,
        resolution=resolution_b,
        release_plan=release_b,
        idempotency_key="cbs5-production-b",
        **POLICIES,
    )
    activation_duration_ms = (monotonic_ns() - activation_started) // 1_000_000
    after_rebind_snapshot = production.snapshot(FLOWBOARD_RESOURCE_TYPE)
    assert after_rebind_snapshot is not None
    assert _records(after_rebind_snapshot) == before_rebind_records
    with pytest.raises(StaleWriterError):
        writer.operate(
            FLOWBOARD_RESOURCE_TYPE,
            "show",
            writer_epoch=1,
            record_id="persistent-record",
            payload={},
        )
    assert writer.operate(
        FLOWBOARD_RESOURCE_TYPE,
        "update",
        writer_epoch=2,
        record_id="persistent-record",
        payload={"status": "done"},
        expected_revision=2,
    )["record"]["status"] == "done"

    final_lock = activated_b.activation.workspace_lock
    final_payload = final_lock.to_dict()
    assert final_payload["cbs"]["state_spaces"][0]["state_space_ref"] == (
        production_a.state_space.stable_ref
    )
    assert final_payload["cbs"]["state_spaces"][0]["authority_epoch"] == 2
    assert built_a.ref.artifact_id != built_b.ref.artifact_id
    assert delivery_a.digest != delivery_b.digest
    assert resolution_a.to_dict()["binding_definition"] == resolution_b.to_dict()[
        "binding_definition"
    ]
    assert contracts.production_binding.digest == staged_b.binding_instance.to_dict()[
        "binding_definition_digest"
    ]

    semantic_text = semantic_bytes.decode("utf-8")
    for forbidden in (
        "flowboard_local_provider",
        "binding-definition:",
        "binding-instance:",
        "state-space:",
        "provider_a",
        "provider_b",
        "credential",
        "endpoint",
    ):
        assert forbidden not in semantic_text

    invariants = {
        "materialization_independence": True,
        "package_topology_independence": True,
        "state_continuity": True,
        "application_independence": True,
        "atomic_rebinding": True,
    }
    final_digest = final_payload["lock_digest"]
    telemetry = CBSBenchmarkTelemetry.create(
        run_id="cbs5-flowboard-crud-v1",
        semantic_revision_digest=semantic_revision_digest,
        environment_profile_digest=contracts.profile.digest,
        requirement_digest=contracts.requirement.digest,
        contract_digests=(contracts.capability.digest, contracts.state.digest),
        package_digests=(built_sim.ref.digest, built_a.ref.digest, built_b.ref.digest),
        application_resolution_digest=resolution_b.digest,
        resolution_plan_digest=plan_b.digest,
        workspace_lock_digest=final_digest,
        requirement_total=1,
        requirement_resolved=1,
        resolver_candidate_count=3,
        binding_selection_cost={
            "enumerated": 3,
            "package_resolutions": 3,
            "evidence_checks": 6,
        },
        context_tokens={"input": 0, "output": 0},
        residual_implementation={"refs": [], "diff_lines": 0},
        package_reuse={"selected": 3, "reused": 0},
        contract_reuse={"selected": 2, "reused": 2},
        resolution_duration_ms=resolution_duration_ms,
        activation_duration_ms=activation_duration_ms,
        manual_interventions=0,
        e2e_result="passed",
        invariant_results=invariants,
        started_at=FIXED_NOW.isoformat(),
        completed_at=FIXED_NOW.isoformat(),
    )
    telemetry_store = CBSBenchmarkTelemetryStore(tmp_path / "telemetry")
    telemetry_path = telemetry_store.put(telemetry)
    assert telemetry_path.is_file()
    assert telemetry_store.load(telemetry.digest) == telemetry
    all_evidence = (*evidence_sim, *evidence_production)
    proof = CBSCrudProofBundle.create(
        run_id="cbs5-flowboard-crud-v1",
        source_digest=canonical_payload_digest(_source().to_dict()),
        semantic_revision_digest=semantic_revision_digest,
        environment_profile=contracts.profile.to_dict(),
        application_requirement=contracts.requirement.to_dict(),
        contracts=(contracts.capability.to_dict(), contracts.state.to_dict()),
        packages=(built_sim.ref.to_dict(), built_a.ref.to_dict(), built_b.ref.to_dict()),
        binding_deliveries=(
            delivery_sim.to_dict(),
            delivery_a.to_dict(),
            delivery_b.to_dict(),
        ),
        application_resolutions=(
            resolution_sim.to_dict(),
            resolution_a.to_dict(),
            resolution_b.to_dict(),
        ),
        resolution_plans=(plan_sim.to_dict(), plan_a.to_dict(), plan_b.to_dict()),
        workspace_locks=(
            {"stage": "simulation", "lock": activated_sim.activation.workspace_lock.to_dict()},
            {"stage": "production-a", "lock": activated_a.activation.workspace_lock.to_dict()},
            {"stage": "failed-rebind", "lock": before_failed_lock.to_dict()},
            {"stage": "production-b", "lock": final_payload},
        ),
        local_revisions=(
            simulation.binding_instance.to_dict(),
            simulation.state_space.to_dict(),
            production_a.binding_instance.to_dict(),
            production_a.state_space.to_dict(),
            current_a.state_space.to_dict(),
            relocated.binding_instance.to_dict(),
            staged_b.binding_instance.to_dict(),
            staged_b.state_space.to_dict(),
        ),
        state_evidence=(
            {
                "stage": "simulation",
                "state_space_ref": simulation.state_space.stable_ref,
                "source_record_digest": simulation.source_record_digest,
            },
            {
                "stage": "production-before-rebind",
                "state_space_ref": production_a.state_space.stable_ref,
                "record_count": before_rebind_records[0],
                "records_digest": before_rebind_records[1],
                "generation": before_rebind_snapshot["generation"],
            },
            {
                "stage": "production-after-rebind",
                "state_space_ref": staged_b.state_space.stable_ref,
                "record_count": before_rebind_records[0],
                "records_digest": before_rebind_records[1],
                "generation": after_rebind_snapshot["generation"],
            },
        ),
        evidence_claims=tuple(pair[0].to_dict() for pair in all_evidence),
        evidence_assessments=tuple(pair[1].to_dict() for pair in all_evidence),
        telemetry=telemetry.to_dict(),
        trace_ids=(
            activated_sim.activation.operation_id,
            activated_a.activation.operation_id,
            manager.operation_id("cbs5-production-b-failed"),
            activated_b.activation.operation_id,
        ),
        test_output={
            "runner": "pytest",
            "test": "test_cbs_crud_executable_proof_preserves_all_five_invariants",
            "result": "passed",
            "contract_scenarios": ["simulation", "local-production"],
        },
        invariants=invariants,
        created_at=FIXED_NOW.isoformat(),
    )
    proof_store = CBSCrudProofStore(tmp_path / "proof")
    proof_path = proof_store.put(proof)
    assert proof_path.is_file()
    assert proof_store.load(proof.digest) == proof
    assert monotonic_ns() >= started
