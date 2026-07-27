from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adaos.apps.cli.commands import maintenance as maintenance_cli
from adaos.domain.artifact_release import ArtifactSourceRef, StableSubscription
from adaos.services.artifact_pipeline import (
    CandidateStore,
    ContentAddressedPackageStore,
    PackageCatalog,
    ReleaseRepository,
    RemoteRegistryRecoveryError,
    RemoteRegistryRecoveryManager,
    SubscriptionStore,
    WorkspaceActivationManager,
    begin_trial,
    build_artifact_package,
    build_project_release,
    candidate_from_release,
    complete_trial,
    record_validation,
    verify_artifact_package,
)


class _LostResponse(RuntimeError):
    pass


class _RecoveryRemote:
    def __init__(self, root: Path, *, source_tree: str) -> None:
        self.repository = ReleaseRepository(root / "releases")
        self.packages: dict[str, bytes] = {}
        self.source_tree = source_tree
        self.fail_channel_response_once = False

    def get_channel(self, project_id: str, channel: str = "stable"):
        return self.repository.get_channel(project_id, channel)

    def get_release(self, project_id: str, release_digest: str):
        return self.repository.get_release(project_id, release_digest)

    def fetch_package(self, package):
        try:
            archive = self.packages[package.digest]
        except KeyError as exc:
            raise FileNotFoundError(f"package not found: {package.digest}") from exc
        verified = verify_artifact_package(archive, expected_digest=package.digest)
        if verified.ref != package:
            raise ValueError("remote package ref mismatch")
        return archive

    def put_package(self, package, archive_bytes: bytes) -> None:
        verified = verify_artifact_package(archive_bytes, expected_digest=package.digest)
        if verified.ref != package:
            raise ValueError("uploaded package ref mismatch")
        previous = self.packages.get(package.digest)
        if previous is not None and previous != archive_bytes:
            raise ValueError("immutable remote package conflict")
        self.packages[package.digest] = archive_bytes

    def put_release_record(self, plan) -> None:
        self.repository.put_release(plan)

    def set_channel(
        self,
        plan,
        channel: str = "stable",
        *,
        expected_release_digest: str | None,
    ):
        pointer = self.repository.set_channel(
            plan.release.project_id,
            channel,
            str(plan.release.release_digest),
            expected_release_digest=expected_release_digest,
        )
        if self.fail_channel_response_once:
            self.fail_channel_response_once = False
            raise _LostResponse("simulated response loss after channel CAS")
        return pointer

    def tree_revision(self, source_ref) -> str:
        return self.source_tree


def _fixture(tmp_path: Path, *, legacy_candidate: bool = False):
    state_root = tmp_path / "state"
    workspace = tmp_path / "workspace"
    source = tmp_path / "source"
    source.mkdir()
    (source / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (source / "webui.json").write_text(
        json.dumps({"version": "1.0.0"}) + "\n",
        encoding="utf-8",
    )
    source_ref = ArtifactSourceRef(
        forge="adaos-root",
        repository="inimatic/adaos-registry",
        revision="1" * 40,
        path_scope=("subnets/test/nodes/node/scenarios/recipes/",),
    )
    built = build_artifact_package(source, kind="scenario", source_ref=source_ref)
    release_plan = build_project_release(
        project_id="recipes",
        version="1.0.0",
        source_ref=source_ref,
        components=(built.ref,),
        catalog=PackageCatalog(),
        validation_evidence=({"status": "passed", "validator": "pytest"},),
    )
    package_store = ContentAddressedPackageStore(state_root / "packages")
    package_store.put(built.archive_bytes)
    ReleaseRepository(state_root / "release-cache").put_release(release_plan)

    active = WorkspaceActivationManager(
        workspace_root=workspace,
        package_store=package_store,
        state_root=state_root / "active",
    ).activate(
        release_plan,
        idempotency_key="active-recipes",
        reload_policy={"mode": "skip", "approved_by": "pytest", "reason": "fixture"},
        health_policy={"mode": "skip", "approved_by": "pytest", "reason": "fixture"},
    )
    SubscriptionStore(workspace / ".adaos" / "subscriptions.json").save(
        StableSubscription(
            project_id="recipes",
            installed_release="recipes@1.0.0",
            installed_digest=str(release_plan.release.release_digest),
        )
    )

    source_tree = "a" * 40
    candidate = candidate_from_release(
        candidate_id="recipes-1-0-0-candidate",
        release=release_plan.release,
        base_release=None,
        package_digest=built.ref.digest,
        change_ids=("change-1",),
        source_tree=source_tree,
        now="2026-07-26T00:00:00+00:00",
    )
    candidate = record_validation(
        candidate,
        {"status": "passed", "validator": "pytest"},
        now="2026-07-26T00:01:00+00:00",
    )
    trial_manager = WorkspaceActivationManager(
        workspace_root=state_root / "trials" / candidate.candidate_id / "workspace",
        package_store=package_store,
        state_root=state_root / "trials" / candidate.candidate_id / "state",
    )
    trial = trial_manager.activate(
        release_plan,
        idempotency_key=f"candidate-trial:{candidate.digest}",
        reload_policy={"mode": "skip", "approved_by": "pytest", "reason": "fixture"},
        health_check=lambda lock: {
            "status": "passed",
            "lock_digest": lock.to_dict()["lock_digest"],
        },
    )
    operation = json.loads(
        trial_manager.operation_path(trial.operation_id).read_text(encoding="utf-8")
    )
    candidate = begin_trial(
        candidate,
        trial_id="trial-recipes",
        audience="owner",
        data_mode="empty",
        lock_digest=trial.workspace_lock.to_dict()["lock_digest"],
        now="2026-07-26T00:02:00+00:00",
        isolation_evidence={"status": "verified", "mode": "empty"},
        reload_receipt=operation["reload_receipt"],
        health_receipt=operation["health_receipt"],
    )
    candidate = complete_trial(
        candidate,
        trial_id="trial-recipes",
        accepted=True,
        now="2026-07-26T00:03:00+00:00",
        rollback_receipt={"status": "not_required"},
    )
    candidate_path = CandidateStore(state_root / "candidates").save(candidate)
    if legacy_candidate:
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        payload["trials"][0]["data_mode"] = "snapshot"
        payload["trials"][0].pop("data_ref", None)
        candidate_path.write_text(json.dumps(payload), encoding="utf-8")

    remote = _RecoveryRemote(tmp_path / "remote", source_tree=source_tree)
    manager = RemoteRegistryRecoveryManager(
        state_root=state_root,
        workspace_root=workspace,
        remote=remote,
    )
    return workspace, state_root, release_plan, built, remote, manager, active


def test_reviewed_recovery_restores_remote_state_without_mutating_workspace(
    tmp_path: Path,
) -> None:
    workspace, _, release, built, remote, manager, _ = _fixture(tmp_path)
    lock_bytes = (workspace / ".adaos" / "workspace.lock.json").read_bytes()
    scenario_bytes = (workspace / "scenarios" / "recipes" / "scenario.yaml").read_bytes()

    plan = manager.plan("recipes", kind="scenario")

    assert plan.allowed is True
    assert plan.action == "restore_remote_registry"
    assert plan.actions == (
        f"put_package:{built.ref.digest}",
        f"put_release:{release.release.release_digest}",
        "create_channel:stable",
    )
    result = manager.apply(
        "recipes",
        kind="scenario",
        reviewed_plan_digest=plan.plan_digest,
    )

    assert result["status"] == "completed"
    assert remote.get_release(
        "recipes", str(release.release.release_digest)
    ).explain() == release.explain()
    assert remote.get_channel("recipes").release_digest == release.release.release_digest
    assert remote.fetch_package(built.ref) == built.archive_bytes
    assert (workspace / ".adaos" / "workspace.lock.json").read_bytes() == lock_bytes
    assert (workspace / "scenarios" / "recipes" / "scenario.yaml").read_bytes() == scenario_bytes
    assert manager.apply(
        "recipes",
        kind="scenario",
        reviewed_plan_digest=plan.plan_digest,
    ) == result


def test_recovery_rejects_changed_local_evidence_after_review(tmp_path: Path) -> None:
    workspace, _, _, _, remote, manager, _ = _fixture(tmp_path)
    plan = manager.plan("recipes", kind="scenario")
    subscriptions = json.loads(
        (workspace / ".adaos" / "subscriptions.json").read_text(encoding="utf-8")
    )
    subscriptions["subscriptions"][0]["policy"] = "pinned"
    (workspace / ".adaos" / "subscriptions.json").write_text(
        json.dumps(subscriptions),
        encoding="utf-8",
    )

    with pytest.raises(RemoteRegistryRecoveryError, match="changed after review"):
        manager.apply(
            "recipes",
            kind="scenario",
            reviewed_plan_digest=plan.plan_digest,
        )

    assert remote.packages == {}
    with pytest.raises(FileNotFoundError):
        remote.get_channel("recipes")


def test_legacy_candidate_requires_current_isolated_revalidation(tmp_path: Path) -> None:
    _, _, _, _, _, manager, _ = _fixture(tmp_path, legacy_candidate=True)

    with pytest.raises(
        RemoteRegistryRecoveryError,
        match="requires explicit isolated revalidation",
    ):
        manager.plan("recipes", kind="scenario")

    receipt = manager.revalidate("recipes", kind="scenario")
    plan = manager.plan("recipes", kind="scenario")

    assert receipt["status"] == "completed"
    assert receipt["legacy_candidate"] is True
    assert plan.legacy_candidate is True
    assert plan.revalidation_receipt_digest is not None
    assert plan.allowed is True


def test_explicit_retry_recovers_lost_channel_response(tmp_path: Path) -> None:
    _, _, release, _, remote, manager, _ = _fixture(tmp_path)
    plan = manager.plan("recipes", kind="scenario")
    remote.fail_channel_response_once = True

    with pytest.raises(RemoteRegistryRecoveryError, match="response loss"):
        manager.apply(
            "recipes",
            kind="scenario",
            reviewed_plan_digest=plan.plan_digest,
        )
    assert remote.get_channel("recipes").release_digest == release.release.release_digest

    recovered = manager.apply(
        "recipes",
        kind="scenario",
        reviewed_plan_digest=plan.plan_digest,
    )

    assert recovered["status"] == "completed"
    assert recovered["receipts"]["channel"]["status"] == "already_present"


def test_maintenance_cli_requires_reviewed_digest_before_remote_recovery(
    monkeypatch,
) -> None:
    calls: list[tuple] = []

    class _Service:
        def plan_artifact_remote_registry_recovery(
            self,
            kind,
            project_id,
            *,
            channel,
        ):
            calls.append(("plan", kind, project_id, channel))
            return {
                "ok": True,
                "action": "restore_remote_registry",
                "plan_digest": "sha256:" + "a" * 64,
            }

        def apply_artifact_remote_registry_recovery(
            self,
            kind,
            project_id,
            *,
            channel,
            reviewed_plan_digest,
        ):
            calls.append(
                ("apply", kind, project_id, channel, reviewed_plan_digest)
            )
            return {"ok": True, "status": "completed"}

        def revalidate_artifact_remote_registry_recovery(
            self,
            kind,
            project_id,
            *,
            channel,
        ):
            calls.append(("revalidate", kind, project_id, channel))
            return {"ok": True, "status": "completed"}

    monkeypatch.setattr(maintenance_cli, "_root_developer_service", _Service)
    runner = CliRunner()
    planned = runner.invoke(
        maintenance_cli.app,
        [
            "artifact-registry-recover",
            "recipes",
            "--kind",
            "scenario",
            "--json",
        ],
    )
    rejected = runner.invoke(
        maintenance_cli.app,
        [
            "artifact-registry-recover",
            "recipes",
            "--kind",
            "scenario",
            "--apply",
        ],
    )
    applied = runner.invoke(
        maintenance_cli.app,
        [
            "artifact-registry-recover",
            "recipes",
            "--kind",
            "scenario",
            "--apply",
            "--reviewed-plan-digest",
            "sha256:" + "a" * 64,
            "--json",
        ],
    )
    revalidation_rejected = runner.invoke(
        maintenance_cli.app,
        [
            "artifact-registry-revalidate",
            "recipes",
            "--kind",
            "scenario",
        ],
    )
    revalidated = runner.invoke(
        maintenance_cli.app,
        [
            "artifact-registry-revalidate",
            "recipes",
            "--kind",
            "scenario",
            "--confirm",
            "--json",
        ],
    )

    assert planned.exit_code == 0, planned.output
    assert json.loads(planned.output)["action"] == "restore_remote_registry"
    assert rejected.exit_code != 0
    assert "reviewed-plan-digest" in rejected.output
    assert applied.exit_code == 0, applied.output
    assert revalidation_rejected.exit_code != 0
    assert "--confirm" in revalidation_rejected.output
    assert revalidated.exit_code == 0, revalidated.output
    assert calls == [
        ("plan", "scenario", "recipes", "stable"),
        ("apply", "scenario", "recipes", "stable", "sha256:" + "a" * 64),
        ("revalidate", "scenario", "recipes", "stable"),
    ]
