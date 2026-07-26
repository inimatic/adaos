from __future__ import annotations

from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, StableSubscription
from adaos.services.artifact_pipeline import (
    ActivationError,
    ArtifactPublicationService,
    PublicationError,
    PublicationStaleError,
    ReleasePlan,
    ReleaseRepository,
    WorkspaceActivationManager,
)


class _Remote:
    def __init__(self, root: Path) -> None:
        self.releases = ReleaseRepository(root / "releases")
        self.archives: dict[str, bytes] = {}
        self.tree = "f" * 40
        self.channel_writes = 0
        self.fail_after_channel_once = False

    def put_release(self, plan: ReleasePlan, archives: dict[str, bytes]) -> None:
        self.archives.update(archives)
        self.releases.put_release(plan)

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        return self.releases.get_release(project_id, release_digest)

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


def _skill(root: Path) -> Path:
    skill = root / "shopping_skill"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: shopping_skill\nversion: 2.1.0\n",
        encoding="utf-8",
    )
    (skill / "handlers.py").write_text("def run(): return True\n", encoding="utf-8")
    return skill


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
    assert (prepared.trial_workspace / "scenarios" / "recipes" / "scenario.yaml").is_file()
    assert not (workspace / "scenarios" / "recipes").exists()
    assert (workspace / "primary-marker.txt").read_text(encoding="utf-8") == "unchanged"

    accepted = service.decide_candidate(
        prepared.candidate.candidate_id,
        accepted=True,
        observations=({"user": "owner", "decision": "looks_good"},),
    )
    result = service.promote(accepted.candidate_id, health_check=lambda lock: True)

    assert result.pointer.release == "recipes@1.0.0"
    assert (workspace / "scenarios" / "recipes" / "scenario.yaml").is_file()
    assert service.subscriptions.load()["recipes"].installed_digest == result.pointer.release_digest
    registry = (workspace / "registry.json").read_text(encoding="utf-8")
    assert '"stable"' in registry


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
        service.promote(prepared.candidate.candidate_id)

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
    skill_service.promote(skill_candidate.candidate.candidate_id)

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
    service.promote(prepared.candidate.candidate_id)
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
    skill_service.promote(stable_candidate.candidate.candidate_id)

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
    service.promote(baseline.candidate.candidate_id)

    feature = checkpoint_candidate("1.1.0", "change-favorites", "favorites")
    service.decide_candidate(feature.candidate.candidate_id, accepted=True)

    moved = checkpoint_candidate("1.0.1", "change-mainline", "mainline")
    service.decide_candidate(moved.candidate.candidate_id, accepted=True)
    service.promote(moved.candidate.candidate_id)

    with pytest.raises(PublicationStaleError) as stale_error:
        service.promote(feature.candidate.candidate_id)

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
    promoted = service.promote(rebased.candidate.candidate_id)
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
        return publisher.promote(prepared.candidate.candidate_id)

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

    with pytest.raises(ActivationError, match="health check failed"):
        subscriber.activate_subscription_update(
            "recipes",
            health_check=lambda _lock: False,
        )
    unchanged = subscriber.subscriptions.load()["recipes"]
    assert unchanged.installed_digest == first.pointer.release_digest
    assert (subscriber.workspace_root / "scenarios" / "recipes" / "webui.json").read_text(
        encoding="utf-8"
    ).strip() == '{"marker": "first"}'

    updated = subscriber.activate_subscription_update(
        "recipes",
        idempotency_key="subscription-retry-after-health-fix",
        health_check=lambda _lock: True,
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
        service.promote(prepared.candidate.candidate_id, health_check=lambda _lock: True)

    paused = service.load_promotion(prepared.candidate.candidate_id)
    assert paused is not None
    assert paused["status"] == "paused"
    assert "admitted" in paused["receipts"]
    assert "channel_moved" not in paused["receipts"]
    assert remote.get_channel("recipes").release_digest == prepared.candidate.release_digest

    promoted = service.promote(
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
        service.promote(prepared.candidate.candidate_id, health_check=lambda _lock: True)

    paused = service.load_promotion(prepared.candidate.candidate_id)
    assert paused is not None
    assert "channel_moved" in paused["receipts"]
    assert "workspace_activated" in paused["receipts"]
    assert "projection_recorded" not in paused["receipts"]
    activation_operation = paused["receipts"]["workspace_activated"]["operation_id"]
    monkeypatch.setattr(service, "_record_workspace_projection", original_projection)

    promoted = service.promote(prepared.candidate.candidate_id)

    assert remote.channel_writes == 1
    completed = service.load_promotion(prepared.candidate.candidate_id)
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["receipts"]["workspace_activated"]["operation_id"] == activation_operation
    assert promoted.activation.idempotent_replay is True
