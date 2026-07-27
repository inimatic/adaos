from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adaos.domain.artifact_release import ArtifactSourceRef, StableSubscription
import adaos.services.artifact_pipeline.reconciliation as reconciliation_module
from adaos.apps.cli.commands import maintenance as maintenance_cli
from adaos.services.artifact_pipeline import (
    ContentAddressedPackageStore,
    PackageCatalog,
    RegistryReconciliationError,
    ReleaseRepository,
    SubscriptionStore,
    WorkspaceActivationManager,
    WorkspaceRegistryReconciler,
    build_artifact_package,
    build_project_release,
)
from adaos.services.workspace_registry import (
    load_workspace_registry,
    upsert_workspace_registry_entry,
    write_workspace_registry,
)


def _source(token: str) -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="git",
        repository="registry",
        revision=token * 40,
        path_scope=("scenarios/recipes/",),
    )


def _release(tmp_path: Path, *, version: str, token: str):
    source = tmp_path / f"source-{token}"
    source.mkdir(parents=True)
    (source / "scenario.yaml").write_text(
        f"id: recipes\nversion: {version}\n",
        encoding="utf-8",
    )
    (source / "webui.json").write_text(
        json.dumps({"version": version}) + "\n",
        encoding="utf-8",
    )
    built = build_artifact_package(source, kind="scenario", source_ref=_source(token))
    plan = build_project_release(
        project_id="recipes",
        version=version,
        source_ref=built.ref.source_ref,
        components=(built.ref,),
        catalog=PackageCatalog(),
    )
    return built, plan


def _fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    installed_dir = workspace / "scenarios" / "recipes_alias"
    installed_dir.mkdir(parents=True)
    (installed_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (installed_dir / "webui.json").write_text(
        json.dumps({"version": "1.0.0"}) + "\n",
        encoding="utf-8",
    )
    upsert_workspace_registry_entry(workspace, "scenarios", installed_dir)

    built, plan = _release(tmp_path, version="1.0.0", token="1")
    remote = ReleaseRepository(tmp_path / "remote")
    remote.put_release(plan)
    pointer = remote.set_channel(
        "recipes",
        "stable",
        plan.release.release_digest,
        expected_release_digest=None,
    )
    package_store = ContentAddressedPackageStore(tmp_path / "packages")
    package_store.put(built.archive_bytes)
    activation = WorkspaceActivationManager(
        workspace_root=workspace,
        package_store=package_store,
        state_root=tmp_path / "activation-state",
    )
    result = activation.activate(
        plan,
        idempotency_key="install-recipes",
        reload_policy={"mode": "skip", "approved_by": "pytest", "reason": "fixture"},
        health_policy={"mode": "skip", "approved_by": "pytest", "reason": "fixture"},
    )
    SubscriptionStore(workspace / ".adaos" / "subscriptions.json").save(
        StableSubscription(
            project_id="recipes",
            installed_release=pointer.release,
            installed_digest=pointer.release_digest,
        )
    )
    reconciler = WorkspaceRegistryReconciler(
        state_root=tmp_path / "state",
        workspace_root=workspace,
        remote=remote,
    )
    return workspace, remote, reconciler, result.workspace_lock


def test_reconciliation_projects_only_fresh_remote_channel_and_preserves_activation(
    tmp_path: Path,
) -> None:
    workspace, _, reconciler, lock = _fixture(tmp_path)
    lock_bytes = (workspace / ".adaos" / "workspace.lock.json").read_bytes()
    manifest_bytes = (workspace / "scenarios" / "recipes" / "scenario.yaml").read_bytes()

    plan = reconciler.plan("recipes", kind="scenario")

    assert plan.allowed is True
    assert plan.action == "project_remote_channel"
    assert plan.observed_workspace_lock_digest == lock.to_dict()["lock_digest"]
    result = reconciler.apply(
        "recipes",
        kind="scenario",
        reviewed_plan_digest=plan.plan_digest,
    )

    assert result["status"] == "completed"
    assert result["result"]["status"] == "projected"
    registry = load_workspace_registry(workspace, fallback_to_scan=False)
    entry = registry["scenarios"][0]
    assert entry["name"] == "recipes_alias"
    assert entry["id"] == "recipes"
    assert entry["channels"]["stable"] == plan.target_registry_channel
    assert (workspace / ".adaos" / "workspace.lock.json").read_bytes() == lock_bytes
    assert (workspace / "scenarios" / "recipes" / "scenario.yaml").read_bytes() == manifest_bytes
    assert reconciler.plan("recipes", kind="scenario").action == "noop"
    assert reconciler.apply(
        "recipes",
        kind="scenario",
        reviewed_plan_digest=plan.plan_digest,
    ) == result


def test_reconciliation_rejects_registry_change_after_review(tmp_path: Path) -> None:
    workspace, _, reconciler, _ = _fixture(tmp_path)
    plan = reconciler.plan("recipes", kind="scenario")
    registry = load_workspace_registry(workspace, fallback_to_scan=False)
    registry["scenarios"][0]["operator_note"] = "changed-after-review"
    write_workspace_registry(workspace, registry)

    with pytest.raises(RegistryReconciliationError, match="changed after review"):
        reconciler.apply(
            "recipes",
            kind="scenario",
            reviewed_plan_digest=plan.plan_digest,
        )

    entry = load_workspace_registry(workspace, fallback_to_scan=False)["scenarios"][0]
    assert "channels" not in entry


def test_reconciliation_repairs_source_projection_even_when_channel_matches(
    tmp_path: Path,
) -> None:
    workspace, _, reconciler, _ = _fixture(tmp_path)
    reviewed = reconciler.plan("recipes", kind="scenario")
    reconciler.apply(
        "recipes",
        kind="scenario",
        reviewed_plan_digest=reviewed.plan_digest,
    )
    registry = load_workspace_registry(workspace, fallback_to_scan=False)
    registry["scenarios"][0]["source"]["revision"] = "0" * 40
    write_workspace_registry(workspace, registry)

    repair = reconciler.plan("recipes", kind="scenario")

    assert repair.action == "project_remote_channel"
    result = reconciler.apply(
        "recipes",
        kind="scenario",
        reviewed_plan_digest=repair.plan_digest,
    )
    assert result["status"] == "completed"
    entry = load_workspace_registry(workspace, fallback_to_scan=False)["scenarios"][0]
    assert entry["source"]["revision"] == repair.pointer.source_revision


def test_reconciliation_rejects_remote_channel_move_after_review(tmp_path: Path) -> None:
    workspace, remote, reconciler, _ = _fixture(tmp_path)
    reviewed = reconciler.plan("recipes", kind="scenario")
    _, moved = _release(tmp_path, version="1.1.0", token="2")
    remote.put_release(moved)
    remote.set_channel(
        "recipes",
        "stable",
        moved.release.release_digest,
        expected_release_digest=reviewed.pointer.release_digest,
    )

    with pytest.raises(RegistryReconciliationError, match="changed after review"):
        reconciler.apply(
            "recipes",
            kind="scenario",
            reviewed_plan_digest=reviewed.plan_digest,
        )

    entry = load_workspace_registry(workspace, fallback_to_scan=False)["scenarios"][0]
    assert "channels" not in entry


def test_explicit_retry_recovers_response_lost_after_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, _, reconciler, _ = _fixture(tmp_path)
    plan = reconciler.plan("recipes", kind="scenario")
    original_write = reconciliation_module.atomic_write_json
    writes = 0

    def fail_completion_once(path: Path, payload):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated lost completion receipt")
        original_write(path, payload)

    monkeypatch.setattr(
        reconciliation_module,
        "atomic_write_json",
        fail_completion_once,
    )
    with pytest.raises(OSError, match="lost completion receipt"):
        reconciler.apply(
            "recipes",
            kind="scenario",
            reviewed_plan_digest=plan.plan_digest,
        )
    entry = load_workspace_registry(workspace, fallback_to_scan=False)["scenarios"][0]
    assert entry["channels"]["stable"] == plan.target_registry_channel

    monkeypatch.setattr(
        reconciliation_module,
        "atomic_write_json",
        original_write,
    )
    recovered = reconciler.apply(
        "recipes",
        kind="scenario",
        reviewed_plan_digest=plan.plan_digest,
    )

    assert recovered["status"] == "completed"
    assert recovered["result"]["status"] == "recovered_after_projection"


def test_maintenance_cli_requires_reviewed_digest_before_reconciliation_apply(
    monkeypatch,
) -> None:
    calls: list[tuple] = []

    class _Service:
        def plan_artifact_registry_reconciliation(
            self,
            kind,
            project_id,
            *,
            channel,
        ):
            calls.append(("plan", kind, project_id, channel))
            return {
                "ok": True,
                "action": "project_remote_channel",
                "plan_digest": "sha256:" + "a" * 64,
            }

        def apply_artifact_registry_reconciliation(
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

    monkeypatch.setattr(maintenance_cli, "_root_developer_service", _Service)
    runner = CliRunner()
    planned = runner.invoke(
        maintenance_cli.app,
        [
            "artifact-registry-reconcile",
            "recipes",
            "--kind",
            "scenario",
            "--json",
        ],
    )
    rejected = runner.invoke(
        maintenance_cli.app,
        [
            "artifact-registry-reconcile",
            "recipes",
            "--kind",
            "scenario",
            "--apply",
        ],
    )
    applied = runner.invoke(
        maintenance_cli.app,
        [
            "artifact-registry-reconcile",
            "recipes",
            "--kind",
            "scenario",
            "--apply",
            "--reviewed-plan-digest",
            "sha256:" + "a" * 64,
            "--json",
        ],
    )

    assert planned.exit_code == 0, planned.output
    assert json.loads(planned.output)["action"] == "project_remote_channel"
    assert rejected.exit_code != 0
    assert "reviewed-plan-digest" in rejected.output
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["status"] == "completed"
    assert calls == [
        ("plan", "scenario", "recipes", "stable"),
        ("apply", "scenario", "recipes", "stable", "sha256:" + "a" * 64),
    ]
