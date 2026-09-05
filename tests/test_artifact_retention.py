from __future__ import annotations

import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import (
    ArtifactPipelineRetentionManager,
    ArtifactRetentionPolicy,
    ContentAddressedPackageStore,
    PackageCatalog,
    WorkspaceActivationManager,
    build_artifact_package,
    build_project_release,
)


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/recipes/",),
    )


def _package(root: Path, *, version: str, marker: str):
    scenario = root / f"source-{marker}"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        f"id: recipes\nversion: {version}\ntitle: Recipes\n",
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text(
        json.dumps({"marker": marker}) + "\n",
        encoding="utf-8",
    )
    return build_artifact_package(
        scenario,
        kind="scenario",
        source_ref=_source(),
    )


def _plan(package):
    return build_project_release(
        project_id="recipes",
        version=package.ref.version,
        source_ref=_source(),
        components=(package.ref,),
        catalog=PackageCatalog(),
    )


def _old(path: Path, *, now: float) -> None:
    old = now - 10_000
    os.utime(path, (old, old))


def _policy() -> ArtifactRetentionPolicy:
    return ArtifactRetentionPolicy(
        orphan_grace_seconds=1,
        package_retention_seconds=1,
        record_retention_seconds=1,
        lock_history_retention_seconds=1,
        keep_lock_histories=2,
    )


def test_retention_dry_run_and_apply_preserve_active_and_running_state(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "artifact_pipeline"
    workspace_root = tmp_path / "workspace"
    store = ContentAddressedPackageStore(state_root / "packages")
    activation = WorkspaceActivationManager(
        workspace_root=workspace_root,
        package_store=store,
        state_root=state_root / "activation",
        delayed_verification_seconds=3600,
    )
    active = _package(tmp_path, version="1.0.0", marker="active")
    orphan = _package(tmp_path, version="2.0.0", marker="orphan")
    store.put(active.archive_bytes)
    store.put(orphan.archive_bytes)
    activation.activate(
        _plan(active),
        idempotency_key="retention-active",
        reload_policy={
            "mode": "skip",
            "approved_by": "pytest.retention",
            "reason": "no attached runtime",
        },
        health_policy={
            "mode": "skip",
            "approved_by": "pytest.retention",
            "reason": "no attached runtime",
        },
    )

    now = time.time()
    active_path = store.package_path(active.ref.digest)
    orphan_path = store.package_path(orphan.ref.digest)
    _old(active_path, now=now)
    _old(orphan_path, now=now)

    orphan_stage = activation.staging_root / ("f" * 32)
    orphan_stage.mkdir(parents=True)
    (orphan_stage / "partial.bin").write_bytes(b"orphan")
    _old(orphan_stage, now=now)

    running_id = "e" * 32
    running_stage = activation.staging_root / running_id
    running_stage.mkdir(parents=True)
    (running_stage / "partial.bin").write_bytes(b"running")
    activation.operations_root.mkdir(parents=True, exist_ok=True)
    (activation.operations_root / f"{running_id}.json").write_text(
        json.dumps(
            {
                "schema": "adaos.artifact.activation_operation.v1",
                "operation_id": running_id,
                "status": "running",
                "desired_lock": activation.load_lock().to_dict(),
            }
        ),
        encoding="utf-8",
    )
    _old(running_stage, now=now)

    retention = ArtifactPipelineRetentionManager(
        state_root=state_root,
        workspace_root=workspace_root,
        policy=_policy(),
    )
    planned = retention.run(dry_run=True, now=now)
    by_reason = {item["reason"]: item for item in planned["actions"]}

    assert planned["dry_run"] is True
    assert by_reason["unreferenced_package"]["path"] == str(orphan_path.resolve())
    assert by_reason["orphan_staging"]["path"] == str(orphan_stage.resolve())
    assert str(active_path.resolve()) not in {item["path"] for item in planned["actions"]}
    assert str(running_stage.resolve()) not in {item["path"] for item in planned["actions"]}
    assert orphan_path.exists()
    assert orphan_stage.exists()

    applied = retention.run(dry_run=False, now=now)

    assert applied["deleted_count"] >= 2
    assert not orphan_path.exists()
    assert not orphan_stage.exists()
    assert active_path.exists()
    assert running_stage.exists()
    assert activation.load_lock() is not None


def test_nonterminal_candidate_protects_its_package_digest(tmp_path: Path) -> None:
    state_root = tmp_path / "state" / "artifact_pipeline"
    store = ContentAddressedPackageStore(state_root / "packages")
    package = _package(tmp_path, version="1.0.0", marker="candidate")
    store.put(package.archive_bytes)
    now = time.time()
    package_path = store.package_path(package.ref.digest)
    _old(package_path, now=now)
    candidates = state_root / "candidates"
    candidates.mkdir(parents=True)
    candidate_path = candidates / "recipes.json"
    candidate_path.write_text(
        json.dumps(
            {
                "schema": "adaos.artifact.candidate.v1",
                "status": "prepared",
                "package_digest": package.ref.digest,
            }
        ),
        encoding="utf-8",
    )
    _old(candidate_path, now=now)

    retention = ArtifactPipelineRetentionManager(
        state_root=state_root,
        workspace_root=tmp_path / "workspace",
        policy=_policy(),
    )
    plan = retention.run(dry_run=True, now=now)

    assert package.ref.digest in plan["protected_package_digests"]
    assert str(package_path.resolve()) not in {item["path"] for item in plan["actions"]}


def test_expired_terminal_candidate_no_longer_pins_package(tmp_path: Path) -> None:
    state_root = tmp_path / "state" / "artifact_pipeline"
    store = ContentAddressedPackageStore(state_root / "packages")
    package = _package(tmp_path, version="1.0.0", marker="expired")
    store.put(package.archive_bytes)
    now = time.time()
    package_path = store.package_path(package.ref.digest)
    _old(package_path, now=now)
    candidates = state_root / "candidates"
    candidates.mkdir(parents=True)
    candidate_path = candidates / "recipes.json"
    candidate_path.write_text(
        json.dumps(
            {
                "schema": "adaos.artifact.candidate.v1",
                "status": "rejected",
                "package_digest": package.ref.digest,
            }
        ),
        encoding="utf-8",
    )
    _old(candidate_path, now=now)

    retention = ArtifactPipelineRetentionManager(
        state_root=state_root,
        workspace_root=tmp_path / "workspace",
        policy=_policy(),
    )
    plan = retention.run(dry_run=True, now=now)

    assert package.ref.digest not in plan["protected_package_digests"]
    assert any(
        item["reason"] == "unreferenced_package"
        and item["path"] == str(package_path.resolve())
        for item in plan["actions"]
    )


def test_uncertain_failed_operation_preserves_recovery_tree(tmp_path: Path) -> None:
    state_root = tmp_path / "state" / "artifact_pipeline"
    retention = ArtifactPipelineRetentionManager(
        state_root=state_root,
        workspace_root=tmp_path / "workspace",
        policy=_policy(),
    )
    operation_id = "d" * 32
    recovery_tree = retention.activation.backups_root / operation_id
    recovery_tree.mkdir(parents=True)
    (recovery_tree / "checkpoint.bin").write_bytes(b"required")
    retention.activation.operations_root.mkdir(parents=True, exist_ok=True)
    operation_path = retention.activation.operations_root / f"{operation_id}.json"
    operation_path.write_text(
        json.dumps(
            {
                "schema": "adaos.artifact.activation_operation.v1",
                "operation_id": operation_id,
                "status": "failed",
                "migration_execution": {"status": "uncertain"},
                "rollback_error": "manual reconciliation required",
            }
        ),
        encoding="utf-8",
    )
    now = time.time()
    _old(recovery_tree, now=now)
    _old(operation_path, now=now)

    plan = retention.run(dry_run=True, now=now)

    targets = {item["path"] for item in plan["actions"]}
    assert str(recovery_tree.resolve()) not in targets
    assert str(operation_path.resolve()) not in targets


def test_corrupt_operation_record_fails_closed_for_staging_cleanup(tmp_path: Path) -> None:
    state_root = tmp_path / "state" / "artifact_pipeline"
    retention = ArtifactPipelineRetentionManager(
        state_root=state_root,
        workspace_root=tmp_path / "workspace",
        policy=_policy(),
    )
    operation_id = "c" * 32
    stage = retention.activation.staging_root / operation_id
    stage.mkdir(parents=True)
    (stage / "partial.bin").write_bytes(b"keep")
    retention.activation.operations_root.mkdir(parents=True, exist_ok=True)
    operation_path = retention.activation.operations_root / f"{operation_id}.json"
    operation_path.write_text("{not-json", encoding="utf-8")
    now = time.time()
    _old(stage, now=now)
    _old(operation_path, now=now)

    plan = retention.run(dry_run=True, now=now)

    assert str(stage.resolve()) not in {item["path"] for item in plan["actions"]}


def test_rolled_back_history_is_audited_but_does_not_pin_packages(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "artifact_pipeline"
    retention = ArtifactPipelineRetentionManager(
        state_root=state_root,
        workspace_root=tmp_path / "workspace",
        policy=_policy(),
    )
    history_id = f"00000001-{'a' * 64}"
    history = retention.activation.lock_history_root / f"{history_id}.json"
    status = history.with_suffix(".status")
    history.parent.mkdir(parents=True)
    history.write_text(
        json.dumps({"components": [{"digest": f"sha256:{'b' * 64}"}]}),
        encoding="utf-8",
    )
    status.write_text(
        json.dumps(
            {
                "schema": "adaos.artifact.lock_history_status.v1",
                "history_id": history_id,
                "status": "rolled_back",
            }
        ),
        encoding="utf-8",
    )
    corrupt_id = f"00000002-{'c' * 64}"
    corrupt_history = history.parent / f"{corrupt_id}.json"
    corrupt_status = corrupt_history.with_suffix(".status")
    corrupt_history.write_text(json.dumps({"components": []}), encoding="utf-8")
    corrupt_status.write_text("{not-json", encoding="utf-8")
    now = time.time()
    _old(history, now=now)
    _old(status, now=now)
    _old(corrupt_history, now=now)
    _old(corrupt_status, now=now)

    records, actions = retention._history_records(now=now)

    assert records == [(corrupt_history, {"components": []})]
    assert {item["reason"] for item in actions} == {
        "expired_rolled_back_history",
        "expired_rolled_back_history_status",
    }


def test_retention_removes_only_terminal_promoted_trial_workspace(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "artifact_pipeline"
    workspace_root = tmp_path / "workspace"
    candidate_id = "recipes-1-0-0-deadbeef"
    trial = workspace_root.parent / "trials" / candidate_id
    trial_state = state_root / "trials" / candidate_id
    (trial / ".adaos").mkdir(parents=True)
    (trial / ".adaos" / "workspace.lock.json").write_text("{}\n", encoding="utf-8")
    trial_state.mkdir(parents=True)
    (trial_state / "operation.json").write_text("{}\n", encoding="utf-8")
    activations = state_root / "trial-activations"
    promotions = state_root / "promotions"
    activations.mkdir(parents=True)
    promotions.mkdir(parents=True)
    activation_path = activations / f"{candidate_id}.json"
    activation_path.write_text(
        json.dumps(
            {
                "schema": "adaos.trial.activation.v1",
                "status": "completed",
                "candidate_ref": {"candidate_id": candidate_id},
            }
        ),
        encoding="utf-8",
    )
    promotion_path = promotions / f"{candidate_id}.json"
    promotion_path.write_text(
        json.dumps(
            {
                "schema": "adaos.artifact.promotion_operation.v1",
                "candidate_id": candidate_id,
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    now = time.time()
    for path in (trial, trial_state, activation_path, promotion_path):
        _old(path, now=now)
    retention = ArtifactPipelineRetentionManager(
        state_root=state_root,
        workspace_root=workspace_root,
        policy=_policy(),
    )

    plan = retention.run(dry_run=True, now=now)

    terminal_targets = {
        item["path"]
        for item in plan["actions"]
        if item["reason"] == "expired_promoted_trial"
    }
    assert terminal_targets == {str(trial.resolve()), str(trial_state.resolve())}

    retention.run(dry_run=False, now=now)
    assert not trial.exists()
    assert not trial_state.exists()


def test_retention_preserves_active_and_unproven_trial_workspaces(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state" / "artifact_pipeline"
    workspace_root = tmp_path / "workspace"
    activations = state_root / "trial-activations"
    activations.mkdir(parents=True)
    now = time.time()
    for candidate_id, status in (
        ("active-candidate", "active"),
        ("completed-without-promotion", "completed"),
    ):
        trial = workspace_root.parent / "trials" / candidate_id
        trial.mkdir(parents=True)
        activation_path = activations / f"{candidate_id}.json"
        activation_path.write_text(
            json.dumps(
                {
                    "schema": "adaos.trial.activation.v1",
                    "status": status,
                    "candidate_ref": {"candidate_id": candidate_id},
                }
            ),
            encoding="utf-8",
        )
        _old(trial, now=now)
        _old(activation_path, now=now)
    retention = ArtifactPipelineRetentionManager(
        state_root=state_root,
        workspace_root=workspace_root,
        policy=_policy(),
    )

    plan = retention.run(dry_run=True, now=now)

    targets = {item["path"] for item in plan["actions"]}
    assert str((workspace_root.parent / "trials" / "active-candidate").resolve()) not in targets
    assert (
        str(
            (workspace_root.parent / "trials" / "completed-without-promotion").resolve()
        )
        not in targets
    )


def test_artifact_retention_cli_is_dry_run_by_default(cli_app, tmp_base_dir) -> None:
    result = CliRunner().invoke(
        cli_app,
        ["maintenance", "artifact-retention", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["results"] == []
