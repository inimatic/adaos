from __future__ import annotations

from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, StableSubscription
from adaos.services.artifact_pipeline import (
    ActivationError,
    ArtifactAttestationAdmission,
    ArtifactAttestationPublisher,
    ArtifactPublicationService,
    ArtifactTrustStore,
    ContentAddressedAttestationStore,
    Ed25519ArtifactSigner,
    PublicationError,
    PublicationStaleError,
    ReleaseAttestationSet,
    ReleasePlan,
    ReleaseRepository,
    WorkspaceActivationManager,
)
from adaos.services.artifact_pipeline import packages as package_module


class _Remote:
    def __init__(self, root: Path) -> None:
        self.releases = ReleaseRepository(root / "releases")
        self.archives: dict[str, bytes] = {}
        self.tree = "f" * 40
        self.channel_writes = 0
        self.fail_after_channel_once = False
        self.attestation_sets: dict[str, ReleaseAttestationSet] = {}
        self.attestation_binding_writes = 0
        self.fail_after_attestation_binding_once = False

    def put_release(self, plan: ReleasePlan, archives: dict[str, bytes]) -> None:
        self.archives.update(archives)
        self.releases.put_release(plan)

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        return self.releases.get_release(project_id, release_digest)

    def put_release_attestation_set(
        self,
        attestation_set: ReleaseAttestationSet,
    ) -> ReleaseAttestationSet:
        sealed = attestation_set.seal()
        self.attestation_binding_writes += 1
        existing = self.attestation_sets.get(sealed.release_digest)
        if existing is not None and existing != sealed:
            raise RuntimeError("immutable attestation set conflict")
        self.attestation_sets[sealed.release_digest] = sealed
        if self.fail_after_attestation_binding_once:
            self.fail_after_attestation_binding_once = False
            raise TimeoutError("attestation binding acknowledgement was lost")
        return sealed

    def get_release_attestation_set(
        self,
        project_id: str,
        release_digest: str,
    ) -> ReleaseAttestationSet:
        result = self.attestation_sets[release_digest]
        assert result.project_id == project_id
        return result

    def set_channel(
        self,
        plan: ReleasePlan,
        channel: str = "stable",
        *,
        expected_release_digest: str | None,
    ):
        self.channel_writes += 1
        self.releases.put_release(plan)
        pointer = self.releases.set_channel(
            plan.release.project_id,
            channel,
            plan.release.release_digest,
            expected_release_digest=expected_release_digest,
        )
        if self.fail_after_channel_once:
            self.fail_after_channel_once = False
            raise TimeoutError("channel outcome was not delivered")
        return pointer

    def get_channel(self, project_id: str, channel: str = "stable"):
        return self.releases.get_channel(project_id, channel)

    def fetch_package(self, package: ArtifactPackageRef) -> bytes:
        return self.archives[package.digest]

    def tree_revision(self, source_ref: ArtifactSourceRef) -> str:
        return self.tree


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("subnets/dev/nodes/node/scenarios/recipes/",),
    )


def _scenario(root: Path) -> Path:
    scenario = root / "recipes"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ntitle: Recipes\n",
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text('{"ui": {}}\n', encoding="utf-8")
    return scenario


def _named_scenario(root: Path, name: str, *, marker: str) -> Path:
    scenario = root / name
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        f"id: {name}\nversion: 1.0.0\ntitle: {name}\n",
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text(
        f'{{"ui": {{"marker": "{marker}"}}}}\n',
        encoding="utf-8",
    )
    return scenario


def _source_for(name: str) -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=(f"subnets/dev/nodes/node/scenarios/{name}/",),
    )


def _skill(root: Path) -> Path:
    skill = root / "shopping_skill"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: shopping_skill\nversion: 2.1.0\n",
        encoding="utf-8",
    )
    (skill / "handlers.py").write_text("def run(): return True\n", encoding="utf-8")
    return skill


def _promote(service: ArtifactPublicationService, candidate_id: str, **kwargs):
    if kwargs.get("reload_runtime") is None:
        kwargs.setdefault(
            "reload_policy",
            {
                "mode": "skip",
                "approved_by": "pytest.artifact_publication",
                "reason": "test Workspace has no live runtime",
            },
        )
    if kwargs.get("health_check") is None:
        kwargs.setdefault(
            "health_policy",
            {
                "mode": "skip",
                "approved_by": "pytest.artifact_publication",
                "reason": "test does not exercise live runtime health",
            },
        )
    return service.promote(candidate_id, **kwargs)


def test_checkpoint_candidate_isolated_trial_and_stable_promotion(tmp_path: Path) -> None:
    dev = _scenario(tmp_path / "dev")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "primary-marker.txt").write_text("unchanged", encoding="utf-8")
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=workspace,
        remote=remote,
    )

    pushed = service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        change_ids=("change-create-recipes",),
        validation_evidence={"suite": "scenario-validation", "status": "passed"},
    )

    assert pushed.package.digest == prepared.plan.packages[0].digest
    assert prepared.candidate.status == "trial"
    trial = prepared.candidate.trials[0]
    assert trial.data_mode == "empty"
    assert trial.isolation_evidence["status"] == "verified"
    assert trial.reload_receipt["status"] == "skipped"
    assert trial.health_receipt["status"] == "passed"
    trial_lock = WorkspaceActivationManager(
        workspace_root=prepared.trial_workspace,
        package_store=service.package_store,
        state_root=service.state_root / "trials" / prepared.candidate.candidate_id / "state",
    ).load_lock()
    assert trial_lock is not None
    assert trial_lock.slots[0].data_mode == "empty"
    assert (prepared.trial_workspace / "scenarios" / "recipes" / "scenario.yaml").is_file()
    assert not (workspace / "scenarios" / "recipes").exists()
    assert (workspace / "primary-marker.txt").read_text(encoding="utf-8") == "unchanged"

    accepted = service.decide_candidate(
        prepared.candidate.candidate_id,
        accepted=True,
        observations=({"user": "owner", "decision": "looks_good"},),
    )
    result = _promote(service, accepted.candidate_id, health_check=lambda lock: True)

    assert result.pointer.release == "recipes@1.0.0"
    assert (workspace / "scenarios" / "recipes" / "scenario.yaml").is_file()
    assert service.subscriptions.load()["recipes"].installed_digest == result.pointer.release_digest
    registry = (workspace / "registry.json").read_text(encoding="utf-8")
    assert '"stable"' in registry


def test_paused_promotion_recovers_failed_activation_with_new_identity(
    tmp_path: Path,
) -> None:
    dev = _scenario(tmp_path / "dev")
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        change_ids=("change-recipes",),
        validation_evidence={"status": "passed"},
    )
    service.decide_candidate(prepared.candidate.candidate_id, accepted=True)

    with pytest.raises(ActivationError, match="health check failed"):
        service.promote(
            prepared.candidate.candidate_id,
            reload_policy={
                "mode": "skip",
                "approved_by": "pytest",
                "reason": "no live runtime",
            },
            health_check=lambda _lock: False,
        )
    failed_operation_id = WorkspaceActivationManager.operation_id(
        f"stable:{prepared.candidate.release_digest}"
    )
    recovery = service.recover_promotion_activation(
        prepared.candidate.candidate_id,
        failed_operation_id,
    )

    assert recovery["status"] == "recovered"
    assert recovery["operation_id"] == failed_operation_id
    promoted = service.promote(
        prepared.candidate.candidate_id,
        reload_policy={
            "mode": "skip",
            "approved_by": "pytest",
            "reason": "no live runtime",
        },
        health_check=lambda _lock: True,
    )
    assert promoted.pointer.release == "recipes@1.0.0"
    assert promoted.activation.operation_id == recovery["next_operation_id"]
    promotion = service.load_promotion(prepared.candidate.candidate_id)
    assert promotion is not None
    assert (
        promotion["receipts"]["activation_recovered"]["operation_id"]
        == failed_operation_id
    )


def test_stable_promotion_retains_other_subscribed_workspace_projects(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=workspace,
        remote=remote,
    )

    promoted = None
    for name, marker in (("recipes", "one"), ("planner", "two")):
        dev = _named_scenario(tmp_path / "dev", name, marker=marker)
        service.record_push(
            kind="scenario",
            artifact_id=name,
            artifact_dir=dev,
            source_ref=_source_for(name),
        )
        prepared = service.prepare_candidate(
            kind="scenario",
            artifact_id=name,
            artifact_dir=dev,
            change_ids=(f"change-{name}",),
            validation_evidence={"suite": "scenario-validation", "status": "passed"},
        )
        service.decide_candidate(prepared.candidate.candidate_id, accepted=True)
        promoted = _promote(service, prepared.candidate.candidate_id)

    assert promoted is not None
    lock = promoted.activation.workspace_lock
    assert {item.project_id for item in lock.slots} == {"recipes", "planner"}
    assert {item.key for item in lock.components} == {
        "scenario:recipes",
        "scenario:planner",
    }
    assert (workspace / "scenarios" / "recipes" / "webui.json").is_file()
    assert (workspace / "scenarios" / "planner" / "webui.json").is_file()
    assert set(service.subscriptions.load()) == {"recipes", "planner"}
    registry = (workspace / "registry.json").read_text(encoding="utf-8")
    assert '"recipes"' in registry
    assert '"planner"' in registry


def test_current_subscription_repairs_a_missing_legacy_workspace_slot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=workspace,
        remote=remote,
    )

    plans = {}
    for name, marker in (("recipes", "one"), ("planner", "two")):
        dev = _named_scenario(tmp_path / "dev", name, marker=marker)
        service.record_push(
            kind="scenario",
            artifact_id=name,
            artifact_dir=dev,
            source_ref=_source_for(name),
        )
        prepared = service.prepare_candidate(
            kind="scenario",
            artifact_id=name,
            artifact_dir=dev,
            change_ids=(f"change-{name}",),
            validation_evidence={"suite": "scenario-validation", "status": "passed"},
        )
        plans[name] = prepared.plan
        service.decide_candidate(prepared.candidate.candidate_id, accepted=True)
        _promote(service, prepared.candidate.candidate_id)

    manager = WorkspaceActivationManager(
        workspace_root=workspace,
        package_store=service.package_store,
        state_root=service.state_root / "activation",
    )
    manager.activate(
        plans["planner"],
        idempotency_key="simulate-legacy-primary-replacement",
        slot_id="primary",
        reload_policy={
            "mode": "skip",
            "approved_by": "pytest.legacy_workspace",
            "reason": "simulate the former single-slot activation",
        },
        health_policy={
            "mode": "skip",
            "approved_by": "pytest.legacy_workspace",
            "reason": "simulation has no live runtime",
        },
    )
    assert not (workspace / "scenarios" / "recipes").exists()

    notice = service.check_subscription("recipes")
    assert notice.available is True
    assert notice.activation_allowed is True
    assert notice.reason == "workspace_slot_missing"
    reviewed = service.plan_subscription_update("recipes", notice=notice)
    repaired = service.activate_subscription_update(
        "recipes",
        expected_plan_digest=reviewed.plan_digest,
        reload_policy={
            "mode": "skip",
            "approved_by": "pytest.subscription_repair",
            "reason": "test Workspace has no live runtime",
        },
        health_policy={
            "mode": "skip",
            "approved_by": "pytest.subscription_repair",
            "reason": "test Workspace has no live runtime",
        },
    )

    assert notice.reason == "workspace_slot_missing"
    assert {item.project_id for item in repaired.activation.workspace_lock.slots} == {
        "recipes",
        "planner",
    }
    assert (workspace / "scenarios" / "recipes" / "webui.json").is_file()
    assert (workspace / "scenarios" / "planner" / "webui.json").is_file()


def test_configured_promotion_publishes_exact_attestations_before_channel(
    tmp_path: Path,
) -> None:
    dev = _scenario(tmp_path / "dev")
    workspace = tmp_path / "workspace"
    remote = _Remote(tmp_path / "remote")
    attestation_store = ContentAddressedAttestationStore(tmp_path / "attestations")
    signer = Ed25519ArtifactSigner.generate(issuer="inimatic.release")
    trust_store = ArtifactTrustStore(tmp_path / "trust.json")
    trust_store.add(signer.trusted_key())
    attestation_publisher = ArtifactAttestationPublisher(
        state_root=tmp_path / "state",
        store=attestation_store,
        signer=signer,
    )
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=workspace,
        remote=remote,
        attestation_publisher=attestation_publisher,
        attestation_admission=ArtifactAttestationAdmission(
            store=attestation_store,
            trust_store=trust_store,
        ),
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        change_ids=("change-attested-promotion",),
        validation_evidence={"status": "passed"},
    )
    service.decide_candidate(prepared.candidate.candidate_id, accepted=True)

    promoted = _promote(service, prepared.candidate.candidate_id)
    operation = service.load_promotion(prepared.candidate.candidate_id)

    assert operation is not None
    phases = [event["phase"] for event in operation["events"]]
    assert phases.index("attestations_published") < phases.index("channel_moved")
    assert phases.index("attestations_bound") < phases.index("channel_moved")
    publication = operation["receipts"]["attestations_published"]["publication"]
    assert publication["status"] == "completed"
    assert [item["subject_kind"] for item in publication["attestations"]] == [
        "package",
        "release",
    ]
    assert remote.get_channel("recipes").release_digest == promoted.pointer.release_digest


def test_unknown_attestation_binding_is_reconciled_without_second_write(
    tmp_path: Path,
) -> None:
    dev = _scenario(tmp_path / "dev")
    remote = _Remote(tmp_path / "remote")
    attestation_store = ContentAddressedAttestationStore(tmp_path / "attestations")
    publisher = ArtifactAttestationPublisher(
        state_root=tmp_path / "state",
        store=attestation_store,
        signer=Ed25519ArtifactSigner.generate(issuer="inimatic.release"),
    )
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
        attestation_publisher=publisher,
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        change_ids=("change-binding-timeout",),
        validation_evidence={"status": "passed"},
    )
    candidate_id = prepared.candidate.candidate_id
    service.decide_candidate(candidate_id, accepted=True)
    remote.fail_after_attestation_binding_once = True

    with pytest.raises(TimeoutError, match="acknowledgement was lost"):
        _promote(service, candidate_id)
    assert remote.attestation_binding_writes == 1
    with pytest.raises(PublicationError, match="outcome is uncertain"):
        _promote(service, candidate_id)
    assert remote.attestation_binding_writes == 1

    reconciled = service.reconcile_release_attestation_binding(candidate_id)
    promoted = _promote(service, candidate_id)

    assert reconciled.release_digest == promoted.pointer.release_digest
    assert remote.attestation_binding_writes == 1
    operation = service.load_promotion(candidate_id)
    assert operation is not None
    assert operation["attestation_binding"]["completed_via"] == "reconciliation"


def test_candidate_rejects_legacy_workspace_downgrade_before_trial(tmp_path: Path) -> None:
    dev = _scenario(tmp_path / "dev")
    workspace = tmp_path / "workspace"
    installed = _scenario(workspace / "scenarios")
    (installed / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.1\ntitle: Recipes\n",
        encoding="utf-8",
    )
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=workspace,
        remote=remote,
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )

    with pytest.raises(PublicationError, match="newer than installed Workspace version 1.0.1"):
        service.prepare_candidate(
            kind="scenario",
            artifact_id="recipes",
            artifact_dir=dev,
            change_ids=("change-downgrade",),
            validation_evidence={"status": "passed"},
        )

    assert remote.archives == {}


def test_rejected_trial_is_detached_with_durable_rollback_evidence(tmp_path: Path) -> None:
    dev = _scenario(tmp_path / "dev")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=_Remote(tmp_path / "remote"),
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        change_ids=("change-rejected",),
        validation_evidence={"status": "passed"},
    )

    rejected = service.decide_candidate(
        prepared.candidate.candidate_id,
        accepted=False,
        observations=({"decision": "needs_changes"},),
    )

    trial = rejected.trials[0]
    assert rejected.status == "rejected"
    assert trial.rollback_receipt["status"] == "rolled_back"
    assert trial.duration_seconds is not None
    assert not prepared.trial_workspace.exists()
    assert Path(trial.rollback_receipt["archive"]).is_dir()


def test_candidate_rejects_dev_changes_after_checkpoint(tmp_path: Path) -> None:
    dev = _scenario(tmp_path / "dev")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=_Remote(tmp_path / "remote"),
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    (dev / "webui.json").write_text('{"ui": {"changed": true}}\n', encoding="utf-8")

    with pytest.raises(PublicationError, match="changed after"):
        service.prepare_candidate(
            kind="scenario",
            artifact_id="recipes",
            artifact_dir=dev,
            change_ids=("change-after-push",),
            validation_evidence={"status": "passed"},
        )


def test_candidate_reuses_exact_checkpoint_after_build_policy_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = _scenario(tmp_path / "dev")
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    old_policy = "sha256:" + "1" * 64
    new_policy = "sha256:" + "2" * 64
    monkeypatch.setattr(package_module, "PACKAGE_BUILD_POLICY_DIGEST", old_policy)
    pushed = service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )

    monkeypatch.setattr(package_module, "PACKAGE_BUILD_POLICY_DIGEST", new_policy)
    verified = service.verify_pushed_source(pushed, dev)
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        change_ids=("change-policy-only",),
        validation_evidence={"status": "passed"},
    )

    assert pushed.package.build_policy_digest == old_policy
    assert verified.ref == pushed.package
    assert prepared.plan.packages == (pushed.package,)
    assert remote.archives[pushed.package.digest] == verified.archive_bytes


def test_build_policy_change_does_not_hide_dev_content_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = _scenario(tmp_path / "dev")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=_Remote(tmp_path / "remote"),
    )
    monkeypatch.setattr(
        package_module,
        "PACKAGE_BUILD_POLICY_DIGEST",
        "sha256:" + "1" * 64,
    )
    pushed = service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    monkeypatch.setattr(
        package_module,
        "PACKAGE_BUILD_POLICY_DIGEST",
        "sha256:" + "2" * 64,
    )
    (dev / "webui.json").write_text('{"ui": {"changed": true}}\n', encoding="utf-8")

    with pytest.raises(PublicationError, match="changed after"):
        service.verify_pushed_source(pushed, dev)


def test_promotion_rechecks_persisted_public_source_tree(tmp_path: Path) -> None:
    dev = _scenario(tmp_path / "dev")
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        change_ids=("change-source-tree",),
        validation_evidence={"status": "passed"},
    )
    service.decide_candidate(prepared.candidate.candidate_id, accepted=True)
    remote.tree = "0" * 40

    with pytest.raises(PublicationError, match="public source tree changed"):
        _promote(service, prepared.candidate.candidate_id)

    with pytest.raises(FileNotFoundError):
        remote.get_channel("recipes", "stable")


def test_scenario_candidate_locks_and_materializes_stable_skill_dependency(
    tmp_path: Path,
) -> None:
    remote = _Remote(tmp_path / "remote")
    skill_dir = _skill(tmp_path / "dev")
    skill_service = ArtifactPublicationService(
        state_root=tmp_path / "skill-state",
        workspace_root=tmp_path / "skill-workspace",
        remote=remote,
    )
    skill_service.record_push(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        source_ref=_source(),
    )
    skill_candidate = skill_service.prepare_candidate(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        change_ids=("change-skill",),
        validation_evidence={"status": "passed"},
    )
    skill_service.decide_candidate(skill_candidate.candidate.candidate_id, accepted=True)
    _promote(skill_service, skill_candidate.candidate.candidate_id)

    scenario_dir = _scenario(tmp_path / "dev")
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ndepends:\n  - shopping_skill\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    service = ArtifactPublicationService(
        state_root=tmp_path / "scenario-state",
        workspace_root=workspace,
        remote=remote,
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        source_ref=_source(),
    )

    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        change_ids=("change-recipes",),
        validation_evidence={"status": "passed"},
    )

    assert [(item.kind, item.artifact_id, item.version) for item in prepared.plan.packages] == [
        ("scenario", "recipes", "1.0.0"),
        ("skill", "shopping_skill", "2.1.0"),
    ]
    assert (
        prepared.trial_workspace / "skills" / "shopping_skill" / "skill.yaml"
    ).is_file()

    service.decide_candidate(prepared.candidate.candidate_id, accepted=True)
    _promote(service, prepared.candidate.candidate_id)
    registry = (workspace / "registry.json").read_text(encoding="utf-8")
    assert '"shopping_skill"' in registry
    assert '"package_lock"' in registry


def test_scenario_candidate_includes_companion_skill_from_same_change_set(
    tmp_path: Path,
) -> None:
    remote = _Remote(tmp_path / "remote")
    dev_root = tmp_path / "dev"
    scenario_dir = _scenario(dev_root / "scenarios")
    skill_dir = _skill(dev_root / "skills")
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ndepends:\n  - shopping_skill\n",
        encoding="utf-8",
    )
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    change_id = "change-recipe-editor"
    service.record_push(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        source_ref=_source(),
        change_ids=(change_id,),
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        source_ref=_source(),
        change_ids=(change_id,),
    )

    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        change_ids=(change_id,),
        validation_evidence={"status": "passed"},
    )

    assert [(item.kind, item.artifact_id, item.version) for item in prepared.plan.packages] == [
        ("scenario", "recipes", "1.0.0"),
        ("skill", "shopping_skill", "2.1.0"),
    ]
    assert prepared.plan.packages[1].source_ref == _source()
    assert (
        prepared.trial_workspace / "skills" / "shopping_skill" / "skill.yaml"
    ).is_file()


def test_scenario_candidate_migrates_installed_dependency_without_dev_copy(
    tmp_path: Path,
) -> None:
    remote = _Remote(tmp_path / "remote")
    dev_root = tmp_path / "dev"
    scenario_dir = _scenario(dev_root / "scenarios")
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ndepends:\n  - shopping_skill\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    installed_skill = _skill(workspace / "skills")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=workspace,
        remote=remote,
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        source_ref=_source(),
        change_ids=("change-recipes",),
    )

    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        change_ids=("change-recipes",),
        validation_evidence={"status": "passed"},
    )

    dependency = next(
        item for item in prepared.plan.packages if item.key == "skill:shopping_skill"
    )
    assert dependency.version == "2.1.0"
    assert dependency.source_ref.forge == "workspace-migration"
    assert dependency.source_ref.repository == "installed-workspace"
    assert dependency.source_ref.revision.startswith("sha256:")
    assert not (dev_root / "skills" / "shopping_skill").exists()
    assert installed_skill.is_dir()
    assert (
        prepared.trial_workspace / "skills" / "shopping_skill" / "skill.yaml"
    ).is_file()


def test_follow_up_candidate_reuses_dependency_from_stable_project_release(
    tmp_path: Path,
) -> None:
    remote = _Remote(tmp_path / "remote")
    dev_root = tmp_path / "dev"
    scenario_dir = _scenario(dev_root / "scenarios")
    skill_dir = _skill(dev_root / "skills")
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ndepends:\n  - shopping_skill\n",
        encoding="utf-8",
    )
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    service.record_push(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        source_ref=_source(),
        change_ids=("initial-project-release",),
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        source_ref=_source(),
        change_ids=("initial-project-release",),
    )
    initial = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        change_ids=("initial-project-release",),
        validation_evidence={"status": "passed"},
    )
    service.decide_candidate(initial.candidate.candidate_id, accepted=True)
    _promote(service, initial.candidate.candidate_id)

    # The companion component has no independent shopping_skill/stable
    # channel: it is owned by the stable recipes release set.
    with pytest.raises(FileNotFoundError):
        remote.get_channel("shopping_skill", "stable")

    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.1\ndepends:\n  - shopping_skill\n",
        encoding="utf-8",
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        source_ref=_source(),
        change_ids=("scenario-follow-up",),
    )

    follow_up = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        change_ids=("scenario-follow-up",),
        validation_evidence={"status": "passed"},
    )

    components = {
        (item.kind, item.artifact_id): item.version
        for item in follow_up.plan.packages
    }
    assert components == {
        ("scenario", "recipes"): "1.0.1",
        ("skill", "shopping_skill"): "2.1.0",
    }
    assert (
        follow_up.trial_workspace / "skills" / "shopping_skill" / "skill.yaml"
    ).is_file()


def test_scenario_candidate_includes_dependency_from_an_earlier_change_set_member(
    tmp_path: Path,
) -> None:
    remote = _Remote(tmp_path / "remote")
    dev_root = tmp_path / "dev"
    scenario_dir = _scenario(dev_root / "scenarios")
    skill_dir = _skill(dev_root / "skills")
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ndepends:\n  - shopping_skill\n",
        encoding="utf-8",
    )
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    service.record_push(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        source_ref=_source(),
        change_ids=("change-skill",),
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        source_ref=_source(),
        change_ids=("change-scenario",),
    )

    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        change_ids=("change-skill", "change-scenario"),
        validation_evidence={"status": "passed"},
    )

    skill_package = next(item for item in prepared.plan.packages if item.kind == "skill")
    assert skill_package.version == "2.1.0"
    assert skill_package.source_ref == _source()


def test_scenario_candidate_does_not_mix_unrelated_dev_dependency(
    tmp_path: Path,
) -> None:
    remote = _Remote(tmp_path / "remote")
    dev_root = tmp_path / "dev"
    skill_dir = _skill(dev_root / "skills")
    skill_service = ArtifactPublicationService(
        state_root=tmp_path / "stable-state",
        workspace_root=tmp_path / "stable-workspace",
        remote=remote,
    )
    skill_service.record_push(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        source_ref=_source(),
    )
    stable_candidate = skill_service.prepare_candidate(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        change_ids=("stable-skill",),
        validation_evidence={"status": "passed"},
    )
    skill_service.decide_candidate(stable_candidate.candidate.candidate_id, accepted=True)
    _promote(skill_service, stable_candidate.candidate.candidate_id)

    (skill_dir / "skill.yaml").write_text(
        "name: shopping_skill\nversion: 3.0.0\n",
        encoding="utf-8",
    )
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    service.record_push(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        source_ref=_source(),
        change_ids=("unrelated-change",),
    )
    scenario_dir = _scenario(dev_root / "scenarios")
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ndepends:\n  - shopping_skill\n",
        encoding="utf-8",
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        source_ref=_source(),
        change_ids=("scenario-change",),
    )

    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        change_ids=("scenario-change",),
        validation_evidence={"status": "passed"},
    )

    skill_package = next(item for item in prepared.plan.packages if item.kind == "skill")
    assert skill_package.version == "2.1.0"


def test_moved_base_creates_reapply_plan_and_requires_new_trial(tmp_path: Path) -> None:
    dev = _scenario(tmp_path / "dev")
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )

    def checkpoint_candidate(version: str, change_id: str, marker: str):
        (dev / "scenario.yaml").write_text(
            f"id: recipes\nversion: {version}\ntitle: Recipes\n",
            encoding="utf-8",
        )
        (dev / "webui.json").write_text(f'{{"marker": "{marker}"}}\n', encoding="utf-8")
        service.record_push(
            kind="scenario",
            artifact_id="recipes",
            artifact_dir=dev,
            source_ref=_source(),
            change_ids=(change_id,),
        )
        return service.prepare_candidate(
            kind="scenario",
            artifact_id="recipes",
            artifact_dir=dev,
            change_ids=(change_id,),
            validation_evidence={"status": "passed", "marker": marker},
        )

    baseline = checkpoint_candidate("1.0.0", "baseline", "baseline")
    service.decide_candidate(baseline.candidate.candidate_id, accepted=True)
    _promote(service, baseline.candidate.candidate_id)

    feature = checkpoint_candidate("1.1.0", "change-favorites", "favorites")
    service.decide_candidate(feature.candidate.candidate_id, accepted=True)

    moved = checkpoint_candidate("1.0.1", "change-mainline", "mainline")
    service.decide_candidate(moved.candidate.candidate_id, accepted=True)
    _promote(service, moved.candidate.candidate_id)

    with pytest.raises(PublicationStaleError) as stale_error:
        _promote(service, feature.candidate.candidate_id)

    rebase_plan = stale_error.value.plan
    assert rebase_plan.change_ids == ("change-favorites",)
    assert rebase_plan.target_base_release == "recipes@1.0.1"
    assert service.candidate_store.load(feature.candidate.candidate_id).status == "stale"
    assert service.load_rebase_plan(feature.candidate.candidate_id) == rebase_plan

    (dev / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.1.1\ntitle: Recipes\n",
        encoding="utf-8",
    )
    (dev / "webui.json").write_text(
        '{"marker": "mainline+favorites"}\n',
        encoding="utf-8",
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
        change_ids=("change-favorites",),
    )
    rebased = service.prepare_rebased_candidate(
        feature.candidate.candidate_id,
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        validation_evidence={"status": "passed", "suite": "rebased-contracts"},
    )

    assert rebased.candidate.status == "trial"
    assert rebased.candidate.base_release == "recipes@1.0.1"
    assert rebased.candidate.change_ids == ("change-favorites",)
    assert rebased.candidate.digest != feature.candidate.digest
    service.decide_candidate(rebased.candidate.candidate_id, accepted=True)
    promoted = _promote(service, rebased.candidate.candidate_id)
    assert promoted.pointer.release == "recipes@1.1.1"


def test_remote_stable_subscription_updates_from_packages_after_success_only(tmp_path: Path) -> None:
    remote = _Remote(tmp_path / "remote")
    publisher_dev = _scenario(tmp_path / "publisher-dev")
    publisher = ArtifactPublicationService(
        state_root=tmp_path / "publisher-state",
        workspace_root=tmp_path / "publisher-workspace",
        remote=remote,
    )

    def publish(version: str, marker: str):
        (publisher_dev / "scenario.yaml").write_text(
            f"id: recipes\nversion: {version}\ntitle: Recipes\n",
            encoding="utf-8",
        )
        (publisher_dev / "webui.json").write_text(
            f'{{"marker": "{marker}"}}\n',
            encoding="utf-8",
        )
        publisher.record_push(
            kind="scenario",
            artifact_id="recipes",
            artifact_dir=publisher_dev,
            source_ref=_source(),
            change_ids=(f"publish-{version}",),
        )
        prepared = publisher.prepare_candidate(
            kind="scenario",
            artifact_id="recipes",
            artifact_dir=publisher_dev,
            change_ids=(f"publish-{version}",),
            validation_evidence={"status": "passed"},
        )
        publisher.decide_candidate(prepared.candidate.candidate_id, accepted=True)
        return _promote(publisher, prepared.candidate.candidate_id)

    first = publish("1.0.0", "first")
    subscriber = ArtifactPublicationService(
        state_root=tmp_path / "subscriber-state",
        workspace_root=tmp_path / "subscriber-workspace",
        remote=remote,
    )
    WorkspaceActivationManager(
        workspace_root=subscriber.workspace_root,
        package_store=subscriber.package_store,
        state_root=subscriber.state_root / "activation",
    ).activate(
        first.plan,
        idempotency_key="initial-install",
        fetch_package=remote.fetch_package,
        reload_policy={
            "mode": "skip",
            "approved_by": "pytest",
            "reason": "subscriber fixture has no live runtime",
        },
        health_policy={
            "mode": "skip",
            "approved_by": "pytest",
            "reason": "initial subscriber fixture",
        },
    )
    subscriber.subscriptions.save(
        StableSubscription(
            project_id="recipes",
            installed_release=first.pointer.release,
            installed_digest=first.pointer.release_digest,
        )
    )

    second = publish("1.1.0", "second")
    notice = subscriber.check_subscription("recipes")
    assert notice.available is True
    assert notice.pointer.release_digest == second.pointer.release_digest
    update_plan = subscriber.plan_subscription_update("recipes")
    update_payload = update_plan.to_dict()
    assert update_payload["plan_digest"].startswith("sha256:")
    assert update_payload["activation"]["component_changes"] == {
        "added": [],
        "changed": ["scenario:recipes"],
        "retained": [],
        "removed": [],
    }
    assert update_payload["activation"]["permissions"]["introduced"] == []
    assert update_payload["activation"]["rollback"]["available"] is True

    with pytest.raises(PublicationError, match="plan changed"):
        subscriber.activate_subscription_update(
            "recipes",
            expected_plan_digest="sha256:" + "0" * 64,
        )

    with pytest.raises(ActivationError, match="health check failed"):
        subscriber.activate_subscription_update(
            "recipes",
            health_check=lambda _lock: False,
            reload_policy={
                "mode": "skip",
                "approved_by": "pytest",
                "reason": "subscriber fixture has no live runtime",
            },
        )
    unchanged = subscriber.subscriptions.load()["recipes"]
    assert unchanged.installed_digest == first.pointer.release_digest
    assert (subscriber.workspace_root / "scenarios" / "recipes" / "webui.json").read_text(
        encoding="utf-8"
    ).strip() == '{"marker": "first"}'

    updated = subscriber.activate_subscription_update(
        "recipes",
        idempotency_key="subscription-retry-after-health-fix",
        expected_plan_digest=update_plan.plan_digest,
        health_check=lambda _lock: True,
        reload_policy={
            "mode": "skip",
            "approved_by": "pytest",
            "reason": "subscriber fixture has no live runtime",
        },
    )
    assert updated.subscription.installed_digest == second.pointer.release_digest
    assert (subscriber.workspace_root / "scenarios" / "recipes" / "webui.json").read_text(
        encoding="utf-8"
    ).strip() == '{"marker": "second"}'


def test_promotion_reconciles_unknown_channel_outcome_without_second_write(
    tmp_path: Path,
) -> None:
    dev = _scenario(tmp_path / "dev")
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        change_ids=("change-unknown-channel-outcome",),
        validation_evidence={"status": "passed"},
    )
    service.decide_candidate(prepared.candidate.candidate_id, accepted=True)
    remote.fail_after_channel_once = True

    with pytest.raises(TimeoutError, match="outcome was not delivered"):
        _promote(service, prepared.candidate.candidate_id, health_check=lambda _lock: True)

    paused = service.load_promotion(prepared.candidate.candidate_id)
    assert paused is not None
    assert paused["status"] == "paused"
    assert "admitted" in paused["receipts"]
    assert "channel_moved" not in paused["receipts"]
    assert remote.get_channel("recipes").release_digest == prepared.candidate.release_digest

    promoted = _promote(service,
        prepared.candidate.candidate_id,
        health_check=lambda _lock: True,
    )

    assert promoted.pointer.release_digest == prepared.candidate.release_digest
    assert remote.channel_writes == 1
    completed = service.load_promotion(prepared.candidate.candidate_id)
    assert completed is not None
    assert completed["status"] == "completed"


def test_promotion_continues_after_projection_failure_without_reactivation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = _scenario(tmp_path / "dev")
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        change_ids=("change-projection-resume",),
        validation_evidence={"status": "passed"},
    )
    service.decide_candidate(prepared.candidate.candidate_id, accepted=True)
    original_projection = service._record_workspace_projection
    monkeypatch.setattr(
        service,
        "_record_workspace_projection",
        lambda _plan: (_ for _ in ()).throw(RuntimeError("projection storage unavailable")),
    )

    with pytest.raises(RuntimeError, match="projection storage unavailable"):
        _promote(service, prepared.candidate.candidate_id, health_check=lambda _lock: True)

    paused = service.load_promotion(prepared.candidate.candidate_id)
    assert paused is not None
    assert "channel_moved" in paused["receipts"]
    assert "workspace_activated" in paused["receipts"]
    assert "projection_recorded" not in paused["receipts"]
    activation_operation = paused["receipts"]["workspace_activated"]["operation_id"]
    monkeypatch.setattr(service, "_record_workspace_projection", original_projection)

    promoted = _promote(service, prepared.candidate.candidate_id)

    assert remote.channel_writes == 1
    completed = service.load_promotion(prepared.candidate.candidate_id)
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["receipts"]["workspace_activated"]["operation_id"] == activation_operation
    assert promoted.activation.idempotent_replay is True
