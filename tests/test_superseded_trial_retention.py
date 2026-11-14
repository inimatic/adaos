from contextlib import closing
import json
import os
import sqlite3
import time
from types import SimpleNamespace

import pytest

from adaos.domain.application import RuntimeSelection
from adaos.services.applications.data_lifecycle import LocalApplicationDataLifecycle, OwnedDataComponent
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel
from adaos.services.applications.trial_retention import SupersededTrialRetention, cleanup_superseded_trials
from adaos.services.artifact_pipeline.retention import ArtifactPipelineRetentionManager
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services import storage_hygiene


OLD = "sha256:" + "1" * 64
NEW = "sha256:" + "2" * 64


def _activate(state, workspace, source, source_digest, target, target_digest):
    base = workspace.parent
    root = base / "trials" / target
    source_root = base / "trials" / source
    atomic_write_json(root / ".adaos/data-transition.json", {
        "schema": "adaos.application.local_data_binding.v1", "application_id": "sample",
        "candidate_id": target, "release_digest": target_digest,
        "beta_source": {"runtime_root_ref": "trial:" + source, "release_digest": source_digest},
        "retained_retired_components": [],
    })
    for candidate, digest in [(source, source_digest), (target, target_digest)]:
        atomic_write_json(state / "artifact_pipeline/trial-activations" / (candidate + ".json"), {
            "schema": "adaos.trial.activation.v1", "status": "active",
            "project_ref": {"id": "sample"}, "release_ref": {"project_id": "sample", "digest": digest},
            "candidate_ref": {"candidate_id": candidate, "release_digest": digest},
            "runtime_binding": {"path": str(base / "trials" / candidate)},
        })
    lifecycle = LocalApplicationDataLifecycle(state_root=state, private_root=base,
        application_id="sample", candidate_id=target, release_digest=target_digest,
        stable_digest=None, beta_source_ref=("trial:" + source, source_digest),
        components=(OwnedDataComponent("skill:worker", None, root / "data", workspace / "data", {}, {
            "data_lifecycle": {"schema": "adaos.skill.data_lifecycle.v1", "execution": "native_tools",
                               "databases": [{"path": "entries.sqlite", "migrations": []}]},
        }, source_root / "data"),))
    lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: {"ok": True})
    return lifecycle


@pytest.fixture
def upgraded(tmp_path):
    repo = tmp_path / "repo"
    base = repo / ".adaos"
    state, workspace = base / "state", base / "workspace"
    workspace.mkdir(parents=True)
    old = base / "trials/old"
    (old / "data").mkdir(parents=True)
    with closing(sqlite3.connect(old / "data/entries.sqlite")) as db:
        db.execute("CREATE TABLE entries(id INTEGER PRIMARY KEY, label TEXT)")
        db.execute("INSERT INTO entries VALUES(1, 'private beta data')")
        db.commit()
    channel = ApplicationRuntimeChannel(state, "sample")
    with channel._connection() as db:
        channel._initialize(db, (RuntimeSelection(application_id="sample", webspace_id="desktop",
            source="local_trial", runtime_root_ref="trial:old", release_digest=OLD, revision=1),))
    lifecycle = _activate(state, workspace, "old", OLD, "new", NEW)
    retention = SupersededTrialRetention(state_dir=state, workspace_root=workspace)
    return repo, state, workspace, channel, lifecycle, retention


def test_completed_forward_upgrade_removes_old_trial_without_age_or_last_three_exemption(upgraded):
    repo, state, workspace, channel, lifecycle, retention = upgraded
    old = workspace.parent / "trials/old"
    assert retention.plan()["actions"][0]["path"] == str(old)
    result = cleanup_superseded_trials(SimpleNamespace(paths=SimpleNamespace(
        state_dir=lambda: state, workspace_dir=lambda: workspace)), "sample")
    assert result["ok"] and result["results"][0]["status"] == "complete"
    assert not old.exists()
    assert channel.read()[0].runtime_root_ref == "trial:new"
    with closing(sqlite3.connect(workspace.parent / "trials/new/data/entries.sqlite")) as db:
        assert db.execute("SELECT * FROM entries").fetchall() == [(1, "private beta data")]
    # Reopening/retrying a completed preparation does not need the predecessor.
    assert lifecycle.prepare_beta(webspace_id="desktop", activate=lambda _: pytest.fail("No replay"))["completed"]
    assert not retention.apply()["results"]


@pytest.mark.parametrize("mutation", ["incomplete", "snapshot_missing", "reset_from_stable", "wrong_contract",
                                     "activation_failed", "foreign_binding", "retained_owned_data", "missing_binding"])
def test_incomplete_or_non_preserving_cutover_never_authorizes_collection(upgraded, mutation):
    _, _, workspace, channel, _, retention = upgraded
    binding_path = workspace.parent / "trials/new/.adaos/data-transition.json"
    if mutation in {"foreign_binding", "retained_owned_data", "missing_binding"}:
        binding = json.loads(binding_path.read_text())
        if mutation == "foreign_binding":
            binding["application_id"] = "someone_else"
        elif mutation == "retained_owned_data":
            binding["retained_retired_components"] = ["skill:old_data"]
        if mutation == "missing_binding":
            binding_path.unlink()
        else:
            atomic_write_json(binding_path, binding)
    else:
        with channel._connection() as db:
            row = db.execute("SELECT operation_id,document FROM transitions").fetchone()
            record = json.loads(row[1])
            if mutation == "incomplete":
                record["completed"] = False
            elif mutation == "snapshot_missing":
                record["receipts"].pop("snapshot_migrate_data")
            elif mutation == "reset_from_stable":
                record["receipts"]["snapshot_migrate_data"]["mode"] = "stable_snapshot_forward"
            elif mutation == "wrong_contract":
                record["receipts"]["snapshot_migrate_data"]["contract_digest"] = OLD
            elif mutation == "activation_failed":
                record["receipts"]["activate_verify"]["ok"] = False
            db.execute("UPDATE transitions SET completed=?,document=? WHERE operation_id=?",
                       (int(record["completed"]), json.dumps(record), row[0]))
            db.commit()
    assert not retention.plan()["actions"]
    assert not retention.apply()["results"]
    assert (workspace.parent / "trials/old/data/entries.sqlite").is_file()


def test_apply_rechecks_live_channel_instead_of_replaying_a_stale_plan(upgraded):
    _, _, workspace, channel, _, retention = upgraded
    assert retention.plan()["actions"]
    with channel._connection() as db:
        selected = channel.read()[0]
        restored = selected.advance(expected_revision=selected.revision, source="local_trial",
            runtime_root_ref="trial:old", release_digest=OLD)
        db.execute("UPDATE channel SET document=? WHERE id=1", (channel._encode((restored,)),))
        db.commit()
    assert not retention.apply()["results"]
    assert (workspace.parent / "trials/old").is_dir()


def test_native_execution_lease_defers_collection(upgraded):
    _, _, workspace, channel, _, retention = upgraded
    with channel.execution("trial:new", NEW):
        result = retention.apply()
    assert result["deferred"] and not result["results"]
    assert (workspace.parent / "trials/old").is_dir()
    assert retention.apply()["results"][0]["status"] == "complete"


def test_manual_storage_pin_survives_automatic_retention(upgraded):
    repo, _, workspace, _, _, retention = upgraded
    old = workspace.parent / "trials/old"
    storage_hygiene.pin_storage_path(old, workspace_root=repo, base_dir=workspace.parent, reason="Keep rollback")
    assert retention.apply()["results"][0]["status"] == "skipped"
    assert old.is_dir()


def test_explicit_retention_hold_keeps_inactive_beta(upgraded):
    _, state, workspace, _, _, retention = upgraded
    atomic_write_json(state / "applications/retention/holds/old.json", {
        "status": "active", "release_digest": OLD,
    })
    assert not retention.plan()["actions"]
    assert not retention.apply()["results"]
    assert (workspace.parent / "trials/old").is_dir()


def test_late_predecessor_writes_are_not_silently_discarded(upgraded):
    _, _, workspace, _, _, retention = upgraded
    late = workspace.parent / "trials/old/skills/.runtime/worker/v0.1/data/late.json"
    atomic_write_json(late, {"late": "private work"})
    os.utime(late, (time.time() + 5, time.time() + 5))
    assert not retention.plan()["actions"]
    assert "changed after cutover" in retention.plan()["deferred"][0]["detail"]
    assert not retention.apply()["results"] and late.is_file()


def test_explicitly_retired_component_state_stays_in_its_previous_owner(upgraded):
    _, _, workspace, _, _, retention = upgraded
    atomic_write_json(workspace.parent / "trials/new/.adaos/releases" / (NEW[7:] + ".json"), {
        "composition_lock": {"lifecycle": {"upgrade": {"retired_components": [
            {"ref": "skill:retired_beta_only", "data": "retain"},
        ]}}},
    })
    assert not retention.apply()["results"]
    assert (workspace.parent / "trials/old").is_dir()


def test_windows_busy_tree_is_reported_as_deferred_failure_without_changing_selection(upgraded, monkeypatch):
    from adaos.services.applications import trial_retention

    _, _, workspace, channel, _, retention = upgraded
    old = workspace.parent / "trials/old"
    monkeypatch.setattr(trial_retention, "apply_storage_cleanup_plan", lambda *_, **__: {
        "job_id": "busy", "items": [{"source_path": str(old), "candidate_id": "old", "status": "error",
                                     "error": "Windows sharing violation"}],
    })
    result = retention.apply()
    assert result["ok"] is False and result["results"][0]["status"] == "error"
    assert old.is_dir() and channel.read()[0].runtime_root_ref == "trial:new"


def test_latest_trial_can_collect_a_chain_and_keep_durable_migration_evidence(upgraded):
    _, state, workspace, _, _, retention = upgraded
    latest = "sha256:" + "3" * 64
    _activate(state, workspace, "new", NEW, "latest", latest)
    assert {item["candidate_id"] for item in retention.plan()["actions"]} == {"old", "new"}
    result = retention.apply()
    assert {item["candidate_id"] for item in result["results"] if item["status"] == "complete"} == {"old", "new"}
    assert (workspace.parent / "trials/latest/data/entries.sqlite").is_file()
    assert len(list((state / "artifact_pipeline/trial-supersessions").glob("*.json"))) == 2


def test_maintenance_reuses_fenced_supersession_collection(upgraded):
    _, state, workspace, _, _, _ = upgraded
    manager = ArtifactPipelineRetentionManager(state_root=state / "artifact_pipeline", workspace_root=workspace)
    planned = manager._terminal_trial_actions(now=0)
    assert [item["candidate_id"] for item in planned] == ["old"]
    results, job = manager._apply_plan_with_quarantine({"actions": planned})
    assert results[0]["status"] == "complete"
    assert job["superseded_trial_jobs"]
    assert not (workspace.parent / "trials/old").exists()


def test_maintenance_does_not_claim_success_when_windows_defers_a_tree(upgraded, monkeypatch):
    from adaos.services.applications import trial_retention

    _, state, workspace, _, _, _ = upgraded
    old = workspace.parent / "trials/old"
    monkeypatch.setattr(trial_retention, "apply_storage_cleanup_plan", lambda *_, **__: {
        "job_id": "busy", "items": [{"source_path": str(old), "candidate_id": "old", "status": "error",
                                     "error": "Windows sharing violation"}],
    })
    result = ArtifactPipelineRetentionManager(
        state_root=state / "artifact_pipeline", workspace_root=workspace).run(dry_run=False)
    assert result["ok"] is False and result["deleted_count"] == 0
    assert old.is_dir()
