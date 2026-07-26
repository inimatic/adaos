from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef, StableSubscription
from adaos.services.artifact_pipeline import (
    ActivationError,
    CandidateError,
    ChannelError,
    ContentAddressedPackageStore,
    PackageCatalog,
    ReleasePlan,
    ReleaseRepository,
    SubscriptionManager,
    SubscriptionStore,
    WorkspaceActivationManager,
    begin_trial,
    build_artifact_package,
    build_project_release,
    candidate_from_release,
    complete_trial,
    promote_candidate,
    record_validation,
)


def _source(token: str) -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="git",
        repository="registry",
        revision=token * 40,
        path_scope=("scenarios/recipes/",),
    )


def _built(root: Path, *, version: str, token: str):
    scenario = root / f"source-{token}"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        f"id: recipes\nversion: {version}\n",
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text(
        json.dumps({"version": version}) + "\n",
        encoding="utf-8",
    )
    return build_artifact_package(scenario, kind="scenario", source_ref=_source(token))


def _plan(built) -> ReleasePlan:
    return build_project_release(
        project_id="recipes",
        version=built.ref.version,
        source_ref=built.ref.source_ref,
        components=(built.ref,),
        catalog=PackageCatalog(),
    )


def _accepted_candidate(base: ReleasePlan, candidate: ReleasePlan, package_digest: str):
    record = candidate_from_release(
        candidate_id=f"recipes-{candidate.release.version.replace('.', '-')}",
        release=candidate.release,
        base_release=base.release,
        package_digest=package_digest,
        change_ids=("change-favorites",),
        source_tree="f" * 40,
        now="2026-07-24T00:00:00Z",
    )
    record = record_validation(record, {"suite": "pytest", "status": "passed"}, now="2026-07-24T00:10:00Z")
    record = begin_trial(
        record,
        trial_id="trial-one",
        audience="owner",
        data_mode="snapshot",
        lock_digest="sha256:" + "e" * 64,
        now="2026-07-24T00:20:00Z",
    )
    return complete_trial(
        record,
        trial_id="trial-one",
        accepted=True,
        now="2026-07-24T01:20:00Z",
    )


class _SourceProvider:
    def __init__(self, tree: str) -> None:
        self.tree = tree

    def tree_revision(self, source_ref: ArtifactSourceRef) -> str:
        return self.tree


def test_promotion_persists_immutable_release_before_moving_channel(tmp_path: Path) -> None:
    stable_built = _built(tmp_path, version="1.0.0", token="1")
    next_built = _built(tmp_path, version="1.1.0", token="2")
    stable = _plan(stable_built)
    next_plan = _plan(next_built)
    repository = ReleaseRepository(tmp_path / "registry-packages")
    repository.put_release(stable)
    repository.set_channel("recipes", "stable", stable.release.release_digest)
    candidate = _accepted_candidate(stable, next_plan, next_built.ref.digest)

    pointer = promote_candidate(
        candidate=candidate,
        plan=next_plan,
        current_stable=stable,
        repository=repository,
        source_provider=_SourceProvider("f" * 40),
    )

    assert pointer.release == "recipes@1.1.0"
    assert repository.get_channel_release("recipes").release == next_plan.release
    assert repository.release_path("recipes", next_plan.release.release_digest).is_file()


def test_release_repository_rejects_same_version_with_different_digest(tmp_path: Path) -> None:
    first = _plan(_built(tmp_path, version="1.0.0", token="1"))
    second = _plan(_built(tmp_path, version="1.0.0", token="2"))
    repository = ReleaseRepository(tmp_path / "registry-packages")

    repository.put_release(first)
    repository.put_release(first)
    with pytest.raises(ChannelError, match="already maps"):
        repository.put_release(second)

    assert repository.get_release("recipes", first.release.release_digest) == first
    assert not repository.release_path("recipes", second.release.release_digest).exists()


def test_promotion_rejects_source_tree_mismatch_and_stale_base(tmp_path: Path) -> None:
    stable = _plan(_built(tmp_path, version="1.0.0", token="1"))
    next_plan = _plan(_built(tmp_path, version="1.1.0", token="2"))
    moved = _plan(_built(tmp_path, version="1.0.1", token="3"))
    candidate = _accepted_candidate(stable, next_plan, next_plan.packages[0].digest)
    repository = ReleaseRepository(tmp_path / "registry-packages")

    with pytest.raises(ChannelError, match="source tree differs"):
        promote_candidate(
            candidate=candidate,
            plan=next_plan,
            current_stable=stable,
            repository=repository,
            source_provider=_SourceProvider("0" * 40),
        )
    with pytest.raises(CandidateError, match="stale"):
        promote_candidate(
            candidate=candidate,
            plan=next_plan,
            current_stable=moved,
            repository=repository,
            source_provider=_SourceProvider("f" * 40),
        )
    assert not repository.release_path("recipes", next_plan.release.release_digest).exists()


def test_subscription_detects_channel_move_and_advances_only_after_activation(tmp_path: Path) -> None:
    stable_built = _built(tmp_path, version="1.0.0", token="1")
    next_built = _built(tmp_path, version="1.1.0", token="2")
    stable = _plan(stable_built)
    next_plan = _plan(next_built)
    repository = ReleaseRepository(tmp_path / "registry-packages")
    repository.put_release(stable)
    repository.set_channel("recipes", "stable", stable.release.release_digest)
    subscription_store = SubscriptionStore(tmp_path / "workspace" / ".adaos" / "subscriptions.json")
    subscriptions = SubscriptionManager(repository, subscription_store)
    package_store = ContentAddressedPackageStore(tmp_path / "packages")
    package_store.put(stable_built.archive_bytes)
    package_store.put(next_built.archive_bytes)
    activation = WorkspaceActivationManager(
        workspace_root=tmp_path / "workspace",
        package_store=package_store,
        state_root=tmp_path / "state",
    )
    initial_activation = activation.activate(stable, idempotency_key="initial-stable")
    subscription = subscriptions.subscribe_installed(
        project_id="recipes",
        release="recipes@1.0.0",
        release_digest=stable.release.release_digest,
    )
    assert initial_activation.status == "completed"

    repository.put_release(next_plan)
    repository.set_channel("recipes", "stable", next_plan.release.release_digest)
    notice = subscriptions.check(subscription)
    assert notice.available is True
    assert notice.activation_allowed is True
    assert notice.reason == "channel_moved"

    with pytest.raises(ActivationError, match="health check failed"):
        subscriptions.activate_update(
            subscription,
            activation,
            idempotency_key="failed-update",
            health_check=lambda lock: False,
        )
    assert subscription_store.load()["recipes"].installed_digest == stable.release.release_digest

    updated, result = subscriptions.activate_update(
        subscription,
        activation,
        idempotency_key="accepted-update",
        health_check=lambda lock: True,
    )
    assert result.status == "completed"
    assert updated.installed_release == "recipes@1.1.0"
    assert subscription_store.load()["recipes"] == updated
    assert subscriptions.check(updated).available is False


def test_pinned_subscription_reports_but_does_not_activate(tmp_path: Path) -> None:
    plan = _plan(_built(tmp_path, version="1.1.0", token="2"))
    repository = ReleaseRepository(tmp_path / "registry-packages")
    repository.put_release(plan)
    repository.set_channel("recipes", "stable", plan.release.release_digest)
    store = SubscriptionStore(tmp_path / "subscriptions.json")
    manager = SubscriptionManager(repository, store)
    pinned = StableSubscription(
        project_id="recipes",
        policy="pinned",
        installed_release="recipes@1.0.0",
        installed_digest="sha256:" + "a" * 64,
    )

    notice = manager.check(pinned)
    assert notice.available is True
    assert notice.activation_allowed is False
    assert notice.reason == "pinned"
