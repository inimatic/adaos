from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adaos.services import storage_hygiene as hygiene
from adaos.services import workspace_storage as storage


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "--initial-branch=dev2027")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Storage test")
    (root / ".gitignore").write_text(".tmp/\n.adaos/\n.cache/\n", encoding="utf-8")
    (root / "source.txt").write_text("retained", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def complete(path: Path) -> None:
    hygiene.write_storage_manifest(path, owner="pytest", status="complete", retain_seconds=0, created_at=time.time() - 10 * 86400)
    manifest_path = path / hygiene.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["completed_at"] = hygiene._utc_now(time.time() - 10 * 86400)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def worktree(root: Path) -> tuple[Path, Path]:
    task = root / ".tmp/task"
    task.mkdir(parents=True)
    path = task / "release"
    git(root, "worktree", "add", "--detach", str(path), "HEAD")
    complete(task)
    return task, path


def test_generic_cleanup_protects_nested_checkout_even_with_explicit_legacy(project):
    task, path = worktree(project)
    for legacy in (False, True):
        plan = hygiene.plan_storage_cleanup(workspace_root=project, include_legacy=legacy)
        assert not plan["candidates"]
        assert plan["skipped_counts"]["git_checkout_requires_owner"] == 1
    assert path.exists()


def test_generic_apply_rechecks_repository_markers(project):
    task = project / ".tmp/task"
    complete(task)
    plan = hygiene.plan_storage_cleanup(workspace_root=project)
    expected = task.stat().st_mtime_ns
    nested = task / "new-repo"
    nested.mkdir()
    git(nested, "init")
    os.utime(task, ns=(expected, expected))
    result = hygiene.apply_storage_cleanup_plan(plan["plan_digest"], workspace_root=project)
    assert result["items"][0]["reason"] == "git_checkout_requires_owner"
    assert (nested / ".git").exists()


@pytest.mark.parametrize("reason", ["dirty", "untracked", "locked", "unmerged", "active", "lease", "pin", "unmanaged"])
def test_worktree_protection_uses_actual_git_state(project, reason):
    task, path = worktree(project)
    expected = {
        "dirty": "dirty_worktree", "untracked": "dirty_worktree", "locked": "git_worktree_locked",
        "unmerged": "main_reachability_unconfirmed", "active": "owner_not_complete",
        "lease": "active_lease", "pin": "pinned", "unmanaged": "unmanaged_worktree",
    }[reason]
    if reason == "dirty":
        (path / "source.txt").write_text("unsaved work", encoding="utf-8")
    elif reason == "untracked":
        (path / "new.txt").write_text("new work", encoding="utf-8")
    elif reason == "locked":
        git(project, "worktree", "lock", str(path))
    elif reason == "unmerged":
        (path / "source.txt").write_text("unreleased commit", encoding="utf-8")
        git(path, "add", ".")
        git(path, "commit", "-m", "unreleased")
    elif reason == "active":
        hygiene.update_storage_manifest(task, status="active")
    elif reason == "lease":
        hygiene.acquire_storage_lease(task, owner="pytest")
    elif reason == "pin":
        hygiene.pin_storage_path(path / "source.txt", workspace_root=project)
    elif reason == "unmanaged":
        (task / hygiene.MANIFEST_NAME).unlink()
    plan = storage.plan_workspace_worktrees(project)
    assert plan["eligible_count"] == 0
    assert plan["worktrees"][0]["reason"] == expected
    storage.maintain_workspace_storage(project, apply=True)
    assert path.exists()


def test_completed_released_worktree_removed_via_git_then_parent_cleaned(project):
    task, path = worktree(project)
    git(path, "switch", "-c", "completed-feature")
    cache = path / ".cache/generated.bin"
    cache.parent.mkdir()
    cache.write_bytes(b"rebuildable")
    before = storage.maintain_workspace_storage(project)
    assert before["worktree_plan"]["eligible_count"] == 1
    assert path.exists()  # dry-run is the default
    result = storage.maintain_workspace_storage(project, apply=True)
    assert result["worktree_results"] == [{"path": str(path.resolve()), "status": "removed"}]
    assert not task.exists()
    assert str(path).replace("\\", "/") not in git(project, "worktree", "list", "--porcelain")
    assert git(project, "rev-parse", "completed-feature")  # local branch remains recoverable
    assert (project / "source.txt").read_text(encoding="utf-8") == "retained"


def test_recent_completion_does_not_expire_because_task_started_long_ago(project):
    task, path = worktree(project)
    manifest_path = task / hygiene.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["completed_at"] = hygiene._utc_now()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = storage.plan_workspace_worktrees(project)
    assert plan["worktrees"][0]["reason"] == "within_retention"
    assert path.exists()


def test_apply_rechecks_edits_made_after_worktree_planning(project, monkeypatch):
    task, path = worktree(project)
    decision = storage._worktree_decision
    calls = []

    def edit_after_first_check(*args):
        result = decision(*args)
        calls.append(result)
        if len(calls) == 1:
            (path / "source.txt").write_text("parallel edit", encoding="utf-8")
        return result

    monkeypatch.setattr(storage, "_worktree_decision", edit_after_first_check)
    result = storage.maintain_workspace_storage(project, apply=True)
    assert result["worktree_results"][0]["status"] == "dirty_worktree"
    assert path.exists() and task.exists()


def test_interrupted_git_removal_is_recorded_and_never_replayed(project, monkeypatch):
    task, path = worktree(project)
    native_git = storage._git
    attempts = []

    def interrupt(repository, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            attempts.append(args)
            (path / ".git").unlink()  # model a partial native removal
            raise subprocess.TimeoutExpired("git", 30)
        return native_git(repository, *args, **kwargs)

    monkeypatch.setattr(storage, "_git", interrupt)
    result = storage.maintain_workspace_storage(project, apply=True)
    assert result["worktree_results"][0]["status"] == "blocked"
    assert storage._read_intents(project / ".adaos")[str(path.resolve())]["HEAD"]
    storage.maintain_workspace_storage(project, apply=True)
    assert len(attempts) == 1 and path.exists()
    plan = hygiene.plan_storage_cleanup(workspace_root=project)
    assert not plan["candidates"]
    assert plan["skipped_counts"]["unfinished_git_removal_requires_review"] == 1


def test_nested_leases_and_pins_protect_parent_scratch(project):
    task = project / ".tmp/task"
    complete(task)
    nested = task / "active-child"
    hygiene.acquire_storage_lease(nested, owner="pytest")
    assert storage.repository_guard(task) == "nested_active_lease"
    (nested / hygiene.LEASE_NAME).unlink()
    hygiene.pin_storage_path(nested, workspace_root=project)
    plan = hygiene.plan_storage_cleanup(workspace_root=project)
    assert not plan["candidates"]
    assert plan["skipped_counts"]["pinned"] == 1


def test_long_running_task_retains_failure_diagnostics_from_completion(project):
    task = project / ".tmp/task"
    hygiene.write_storage_manifest(task, owner="pytest", status="active", created_at=time.time() - 30 * 86400)
    hygiene.update_storage_manifest(task, status="failed", retain_seconds=3 * 86400)
    plan = hygiene.plan_storage_cleanup(workspace_root=project)
    assert not plan["candidates"]
    assert plan["skipped_counts"]["within_retention"] == 1


def test_budget_gate_fails_closed_on_partial_measurement_and_reservation(project, monkeypatch):
    scratch = project / ".tmp/cache"
    scratch.mkdir(parents=True)
    for index in range(20):
        (scratch / str(index)).write_bytes(b"x" * 100)
    monkeypatch.setattr(storage, "_policy", lambda _: {**storage.DEFAULT_POLICY, "scan_max_entries": 5, "min_free_bytes": 10, "scratch_max_bytes": 10_000})
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: storage.shutil._ntuple_diskusage(100, 50, 50))
    report = storage.inspect_workspace_storage(project, reserve_bytes=45)
    assert report["admission"] == "blocked"
    assert {"scratch_usage_incomplete", "free_space_reserve"}.issubset(report["reasons"])
    assert report["scratch"]["complete"] is False


def test_incomplete_marker_scan_is_not_permission_to_delete(project):
    task = project / ".tmp/task"
    complete(task)
    assert storage.repository_guard(task, max_entries=0) == "repository_scan_incomplete"


def test_workspace_cli_gate_returns_nonzero_on_budget_block(project, monkeypatch):
    from adaos.apps.cli.commands.maintenance import app

    monkeypatch.setattr(storage, "inspect_workspace_storage", lambda *args, **kwargs: {"ok": False, "admission": "blocked"})
    result = CliRunner().invoke(app, ["workspace-check", "--workspace-root", str(project), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["admission"] == "blocked"
