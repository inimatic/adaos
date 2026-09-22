from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from adaos.domain.artifact_release import (
    ArtifactSourceRef,
    WORKSPACE_LOCK_SCHEMA,
    WORKSPACE_LOCK_V2_SCHEMA,
)
from adaos.services.artifact_pipeline import (
    ActivationConflictError,
    ActivationError,
    ContentAddressedPackageStore,
    PackageCatalog,
    WorkspaceActivationManager,
    build_artifact_package,
    build_project_release,
)
from adaos.services.capability_binding_state import (
    CBSActivationCoordinator,
    FencedLocalCrudWriter,
    LegacyCrudProjector,
    LocalIdentityStore,
    ResolutionPlanError,
    ResolutionPlanner,
    SemanticResolver,
    StaleWriterError,
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
FIXED_NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-cbs-fixtures",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("skills/",),
    )


def _setup(tmp_path: Path):
    contracts = flowboard_contracts()
    package_root = tmp_path / "source"
    package_root.mkdir()
    (package_root / "skill.yaml").write_text(
        "name: flowboard_local_provider\nversion: 1.0.0\n", encoding="utf-8"
    )
    (package_root / "handlers").mkdir()
    (package_root / "handlers" / "main.py").write_text(
        "def invoke(payload):\n    return payload\n", encoding="utf-8"
    )
    built = build_artifact_package(package_root, kind="skill", source_ref=_source())
    release_plan = build_project_release(
        project_id="flowboard_local_provider",
        version="1.0.0",
        source_ref=_source(),
        components=(built.ref,),
        catalog=PackageCatalog(),
    )
    delivery = binding_delivery(
        contracts.production_binding,
        built.ref,
        physical_member="handlers/main.py",
    )
    resource = LocalCrudResourceService(state_dir=tmp_path / "state")
    resource.materialize(flowboard_bundle())
    identity_store = LocalIdentityStore(tmp_path / "identities")
    projected = LegacyCrudProjector(resource, identity_store).project(
        FLOWBOARD_RESOURCE_TYPE,
        workspace_ref="workspace:local",
        tenant_ref="tenant:test",
        capability_contract=contracts.capability,
        state_contract=contracts.state,
        binding_definition=contracts.production_binding,
        delivery=delivery,
        environment_profile=contracts.profile,
    )
    evidence_pairs = conformance_evidence(
        contracts, contracts.production_binding, suffix="activation"
    )
    resolver = SemanticResolver(now=lambda: FIXED_NOW)
    resolution = resolver.resolve(
        contracts.requirement,
        semantic_revision_digest=SEMANTIC_REVISION,
        target_mode="production",
        capability_contracts=(contracts.capability,),
        state_contracts=(contracts.state,),
        binding_definitions=(contracts.production_binding,),
        deliveries=(delivery,),
        environment_profile=contracts.profile,
        binding_instances=(projected.binding_instance,),
        state_spaces=(projected.state_space,),
        relations=projected.relations,
        evidence_claims=tuple(item[0] for item in evidence_pairs),
        evidence_assessments=tuple(item[1] for item in evidence_pairs),
        package_resolver=lambda _candidate: release_plan,
    )
    package_store = ContentAddressedPackageStore(tmp_path / "package-store")
    package_store.put(built.archive_bytes)
    manager = WorkspaceActivationManager(
        workspace_root=tmp_path / "workspace",
        package_store=package_store,
        state_root=tmp_path / "activation-state",
        delayed_verification_seconds=0,
    )

    def generation(state_space_ref: str) -> int:
        assert state_space_ref == projected.state_space.stable_ref
        snapshot = resource.snapshot(FLOWBOARD_RESOURCE_TYPE)
        assert snapshot is not None
        return int(snapshot["generation"])

    coordinator = CBSActivationCoordinator(
        manager,
        identity_store,
        now=lambda: FIXED_NOW,
        state_generation_observer=generation,
    )
    planner = ResolutionPlanner(now=lambda: FIXED_NOW)
    return {
        "contracts": contracts,
        "resource": resource,
        "identity_store": identity_store,
        "projected": projected,
        "resolution": resolution,
        "release_plan": release_plan,
        "manager": manager,
        "coordinator": coordinator,
        "planner": planner,
    }


def _policies() -> dict:
    return {
        "reload_policy": {
            "mode": "skip",
            "approved_by": "pytest.cbs",
            "reason": "no live runtime in contract test",
        },
        "health_policy": {
            "mode": "skip",
            "approved_by": "pytest.cbs",
            "reason": "no live runtime in contract test",
        },
    }


def test_cbs_activation_commits_one_v2_workspace_authority_and_pins_journal(tmp_path: Path) -> None:
    fixture = _setup(tmp_path)
    plan = fixture["planner"].build(fixture["resolution"], current_lock=None)
    result = fixture["coordinator"].activate(
        plan,
        resolution=fixture["resolution"],
        release_plan=fixture["release_plan"],
        idempotency_key="cbs-initial-activation",
        **_policies(),
    )

    lock = result.activation.workspace_lock
    payload = lock.to_dict()
    assert payload["schema"] == WORKSPACE_LOCK_V2_SCHEMA
    assert payload["cbs"]["application_resolution_digest"] == fixture["resolution"].digest
    assert payload["cbs"]["resolution_plan_digest"] == plan.digest
    assert payload["cbs"]["state_spaces"][0]["state_space_ref"] == fixture["projected"].state_space.stable_ref
    operation = json.loads(
        fixture["manager"].operation_path(result.activation.operation_id).read_text(encoding="utf-8")
    )
    assert operation["application_resolution_digest"] == fixture["resolution"].digest
    assert operation["resolution_plan_digest"] == plan.digest
    assert fixture["manager"].load_lock() == lock

    with pytest.raises(ActivationError, match="cannot be downgraded"):
        fixture["manager"].activate(
            fixture["release_plan"],
            idempotency_key="legacy-downgrade-forbidden",
            **_policies(),
        )


def test_precommit_failure_preserves_old_lock_generation_and_writer(tmp_path: Path) -> None:
    fixture = _setup(tmp_path)
    first_plan = fixture["planner"].build(fixture["resolution"], current_lock=None)
    fixture["coordinator"].activate(
        first_plan,
        resolution=fixture["resolution"],
        release_plan=fixture["release_plan"],
        idempotency_key="cbs-base",
        **_policies(),
    )
    before_lock = fixture["manager"].load_lock()
    assert before_lock is not None
    before_payload = before_lock.to_dict()
    before_generation = fixture["resource"].snapshot(FLOWBOARD_RESOURCE_TYPE)["generation"]
    next_plan = fixture["planner"].build(
        fixture["resolution"], current_lock=before_lock, ttl=timedelta(minutes=20)
    )

    def fail_before_switch(phase: str) -> None:
        if phase == "switch-lock":
            raise RuntimeError("injected precommit failure")

    with pytest.raises(ActivationError, match="injected precommit failure"):
        fixture["coordinator"].activate(
            next_plan,
            resolution=fixture["resolution"],
            release_plan=fixture["release_plan"],
            idempotency_key="cbs-precommit-failure",
            phase_hook=fail_before_switch,
            **_policies(),
        )

    assert fixture["manager"].load_lock().to_dict() == before_payload
    assert fixture["resource"].snapshot(FLOWBOARD_RESOURCE_TYPE)["generation"] == before_generation
    writer = FencedLocalCrudWriter(
        fixture["resource"],
        fixture["manager"],
        fixture["projected"].state_space.stable_ref,
    )
    written = writer.operate(
        FLOWBOARD_RESOURCE_TYPE,
        "update",
        writer_epoch=1,
        record_id="one",
        payload={"status": "in_progress"},
        expected_revision=1,
    )
    assert written["record"]["status"] == "in_progress"


def test_generation_change_and_unproven_irreversible_action_reject_before_activation(tmp_path: Path) -> None:
    fixture = _setup(tmp_path)
    plan = fixture["planner"].build(fixture["resolution"], current_lock=None)
    fixture["resource"].operate(
        FLOWBOARD_RESOURCE_TYPE,
        "update",
        record_id="one",
        payload={"status": "done"},
        expected_revision=1,
    )
    with pytest.raises(ActivationConflictError, match="generation changed"):
        fixture["coordinator"].activate(
            plan,
            resolution=fixture["resolution"],
            release_plan=fixture["release_plan"],
            idempotency_key="cbs-stale-generation",
            **_policies(),
        )
    assert fixture["manager"].load_lock() is None

    irreversible = fixture["planner"].build(
        fixture["resolution"],
        current_lock=None,
        migrations=(
            {
                "migration_ref": "migration:external-delete",
                "classification": "irreversible",
                "proven": False,
            },
        ),
    )
    with pytest.raises(ResolutionPlanError, match="irreversible"):
        fixture["coordinator"]._validate_static(
            irreversible, fixture["resolution"], fixture["release_plan"]
        )


def test_crash_after_lock_switch_is_recovered_without_two_active_authorities(tmp_path: Path) -> None:
    fixture = _setup(tmp_path)
    plan = fixture["planner"].build(fixture["resolution"], current_lock=None)
    operation_id = fixture["manager"].operation_id("cbs-crash-after-switch")

    def crash(phase: str) -> None:
        if phase == "reload":
            raise SystemExit("simulated process death")

    with pytest.raises(SystemExit, match="simulated process death"):
        fixture["coordinator"].activate(
            plan,
            resolution=fixture["resolution"],
            release_plan=fixture["release_plan"],
            idempotency_key="cbs-crash-after-switch",
            phase_hook=crash,
            **_policies(),
        )

    interrupted = fixture["manager"].load_lock()
    assert interrupted is not None
    assert interrupted.to_dict()["schema"] == WORKSPACE_LOCK_V2_SCHEMA
    operation = json.loads(
        fixture["manager"].operation_path(operation_id).read_text(encoding="utf-8")
    )
    assert operation["status"] == "running"
    assert operation["resolution_plan_digest"] == plan.digest

    recovered = fixture["manager"].recover_interrupted(operation_id)
    assert recovered["status"] == "recovered"
    assert fixture["manager"].load_lock() is None
    assert not fixture["manager"].lock_path.exists()


def test_fenced_writer_rejects_missing_and_stale_epochs(tmp_path: Path) -> None:
    fixture = _setup(tmp_path)
    writer = FencedLocalCrudWriter(
        fixture["resource"],
        fixture["manager"],
        fixture["projected"].state_space.stable_ref,
    )
    with pytest.raises(StaleWriterError, match="no CBS writer authority"):
        writer.operate(
            FLOWBOARD_RESOURCE_TYPE,
            "update",
            writer_epoch=1,
            record_id="one",
            payload={"status": "done"},
            expected_revision=1,
        )

    plan = fixture["planner"].build(fixture["resolution"], current_lock=None)
    fixture["coordinator"].activate(
        plan,
        resolution=fixture["resolution"],
        release_plan=fixture["release_plan"],
        idempotency_key="cbs-writer-fence",
        **_policies(),
    )
    with pytest.raises(StaleWriterError, match="not active"):
        writer.operate(
            FLOWBOARD_RESOURCE_TYPE,
            "update",
            writer_epoch=0,
            record_id="one",
            payload={"status": "done"},
            expected_revision=1,
        )
    result = writer.operate(
        FLOWBOARD_RESOURCE_TYPE,
        "update",
        writer_epoch=1,
        record_id="one",
        payload={"status": "done"},
        expected_revision=1,
    )
    assert result["record"]["status"] == "done"
    assert fixture["manager"].load_lock().to_dict()["schema"] != WORKSPACE_LOCK_SCHEMA
