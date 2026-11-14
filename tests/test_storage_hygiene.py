from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from types import SimpleNamespace
from pathlib import Path

from typer.testing import CliRunner
import pytest

from adaos.services import storage_hygiene


def test_delete_slice_never_traverses_windows_junction(tmp_path, monkeypatch):
    link = tmp_path / "junction"
    traversed = []
    removed = []
    monkeypatch.setattr(storage_hygiene.os, "lstat", lambda _: SimpleNamespace(
        st_file_attributes=0x400, st_mode=stat.S_IFDIR))
    monkeypatch.setattr(storage_hygiene.os, "scandir", lambda _: traversed.append(True))
    monkeypatch.setattr(storage_hygiene, "_rmdir", lambda path, **_: removed.append(path))
    budget = storage_hygiene._DeleteBudget(max_entries=10, deadline=time.monotonic() + 10)
    storage_hygiene._delete_tree_slice(link, budget=budget)
    assert removed == [link] and not traversed


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction semantics")
def test_quarantine_deletes_real_junction_without_touching_target(tmp_path):
    workspace, base = _workspace(tmp_path)
    target = workspace / "protected-target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    obsolete = base / "trials/old"
    obsolete.mkdir(parents=True)
    link = obsolete / "current"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    plan = storage_hygiene.create_storage_cleanup_plan([{"path": str(obsolete)}],
        workspace_root=workspace, base_dir=base, source="test-junction")
    job = storage_hygiene.apply_storage_cleanup_plan(plan["plan_digest"],
        workspace_root=workspace, base_dir=base, max_entries=100, max_seconds=5)
    assert job["status"] == "complete" and not obsolete.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    base = workspace / ".adaos"
    workspace.mkdir()
    base.mkdir()
    return workspace, base


def test_inspect_storage_counts_only_immediate_entries(tmp_path: Path) -> None:
    workspace, base = _workspace(tmp_path)
    (base / "gc" / "quarantine").mkdir(parents=True)
    deep = workspace / ".tmp" / "run" / "a" / "b"
    deep.mkdir(parents=True)
    for index in range(20):
        (deep / f"{index}.txt").write_text("payload", encoding="utf-8")

    payload = storage_hygiene.inspect_storage(
        workspace_root=workspace,
        base_dir=base,
        max_entries_per_root=10,
    )

    repository_temp = next(item for item in payload["roots"] if item["id"] == "repository_temp")
    assert repository_temp["immediate_entries"] == 1
    assert repository_temp["scan_truncated"] is False
    assert repository_temp["cleanup"] == "children"
    assert any(item["id"] == "adaos_gc" for item in payload["roots"])
    assert payload["unclassified_directories"] == []


def test_manifest_lease_and_pin_control_cleanup_plan(tmp_path: Path) -> None:
    workspace, base = _workspace(tmp_path)
    run = workspace / ".tmp" / "completed-run"
    now = time.time()
    storage_hygiene.write_storage_manifest(
        run,
        owner="test",
        status="complete",
        retain_seconds=0,
        created_at=now - 3600,
    )
    lease = storage_hygiene.acquire_storage_lease(run, owner="test", ttl_seconds=300)

    leased_plan = storage_hygiene.plan_storage_cleanup(
        workspace_root=workspace,
        base_dir=base,
        now=now,
    )
    assert leased_plan["candidate_total"] == 0
    assert leased_plan["skipped_counts"]["active_lease"] == 1

    assert storage_hygiene.release_storage_lease(run, lease_id=lease["lease_id"]) is True
    candidate_plan = storage_hygiene.plan_storage_cleanup(
        workspace_root=workspace,
        base_dir=base,
        now=now,
    )
    assert [item["workspace_path"] for item in candidate_plan["candidates"]] == [".tmp/completed-run"]

    storage_hygiene.pin_storage_path(run, workspace_root=workspace, base_dir=base, reason="evidence")
    pinned_plan = storage_hygiene.plan_storage_cleanup(
        workspace_root=workspace,
        base_dir=base,
        now=now,
    )
    assert pinned_plan["candidate_total"] == 0
    assert pinned_plan["skipped_counts"]["pinned"] == 1

    result = storage_hygiene.unpin_storage_path(run, workspace_root=workspace, base_dir=base)
    assert result["removed"] is True


def test_managed_storage_run_records_success_and_failure(tmp_path: Path) -> None:
    success = tmp_path / "success"
    with storage_hygiene.managed_storage_run(
        success,
        owner="e2e",
        retention_class="evidence",
        success_retain_seconds=60,
    ):
        assert (success / storage_hygiene.LEASE_NAME).exists()
    success_manifest = json.loads((success / storage_hygiene.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert success_manifest["status"] == "complete"
    assert success_manifest["retain_seconds"] == 60
    assert not (success / storage_hygiene.LEASE_NAME).exists()

    failed = tmp_path / "failed"
    try:
        with storage_hygiene.managed_storage_run(
            failed,
            owner="e2e",
            retention_class="evidence",
            failure_retain_seconds=120,
        ):
            raise RuntimeError("expected")
    except RuntimeError:
        pass
    failed_manifest = json.loads((failed / storage_hygiene.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert failed_manifest["status"] == "failed"
    assert failed_manifest["retain_seconds"] == 120


def test_legacy_repository_temp_requires_explicit_inclusion(tmp_path: Path) -> None:
    workspace, base = _workspace(tmp_path)
    legacy = workspace / ".tmp" / "legacy"
    legacy.mkdir(parents=True)
    old = time.time() - 10 * 86400
    os.utime(legacy, (old, old))

    safe_plan = storage_hygiene.plan_storage_cleanup(workspace_root=workspace, base_dir=base)
    assert safe_plan["candidate_total"] == 0
    assert safe_plan["skipped_counts"]["legacy_missing_manifest"] == 1

    legacy_plan = storage_hygiene.plan_storage_cleanup(
        workspace_root=workspace,
        base_dir=base,
        include_legacy=True,
    )
    assert legacy_plan["candidate_total"] == 1
    assert legacy_plan["candidates"][0]["legacy"] is True

    evidence = workspace / "e2e" / "artifacts" / "legacy-evidence"
    evidence.mkdir(parents=True)
    os.utime(evidence, (old, old))
    protected_plan = storage_hygiene.plan_storage_cleanup(
        workspace_root=workspace,
        base_dir=base,
        include_legacy=True,
    )
    assert "e2e/artifacts/legacy-evidence" not in {
        item["workspace_path"] for item in protected_plan["candidates"]
    }
    assert protected_plan["skipped_counts"]["legacy_evidence_requires_adoption"] == 1

    adopted = storage_hygiene.adopt_legacy_storage_path(
        evidence,
        workspace_root=workspace,
        base_dir=base,
        pinned=True,
    )
    assert adopted["manifest"]["metadata"]["adopted_legacy"] is True
    assert adopted["manifest"]["pinned"] is True


def test_existing_file_can_be_adopted_and_leased(tmp_path: Path) -> None:
    workspace, base = _workspace(tmp_path)
    legacy_file = workspace / ".tmp" / "legacy.log"
    legacy_file.parent.mkdir()
    legacy_file.write_text("payload", encoding="utf-8")

    adopted = storage_hygiene.adopt_legacy_storage_path(
        legacy_file,
        workspace_root=workspace,
        base_dir=base,
        retain_seconds=60,
    )
    lease = storage_hygiene.acquire_storage_lease(legacy_file, owner="test", ttl_seconds=60)

    assert legacy_file.is_file()
    assert adopted["manifest"]["status"] == "complete"
    assert legacy_file.with_name(f"{legacy_file.name}.{storage_hygiene.MANIFEST_NAME}").is_file()
    assert legacy_file.with_name(f"{legacy_file.name}.{storage_hygiene.LEASE_NAME}").is_file()
    assert storage_hygiene.release_storage_lease(legacy_file, lease_id=lease["lease_id"])


def test_apply_skips_source_changed_or_reactivated_after_plan(tmp_path: Path) -> None:
    workspace, base = _workspace(tmp_path)
    changed = workspace / ".tmp" / "changed"
    reactivated = workspace / ".tmp" / "reactivated"
    old = time.time() - 3600
    for path in (changed, reactivated):
        storage_hygiene.write_storage_manifest(
            path,
            owner="test",
            status="complete",
            retain_seconds=0,
            created_at=old,
        )
    plan = storage_hygiene.plan_storage_cleanup(workspace_root=workspace, base_dir=base)
    assert plan["candidate_total"] == 2

    (changed / "late-write.txt").write_text("new", encoding="utf-8")
    storage_hygiene.update_storage_manifest(reactivated, status="active")
    job = storage_hygiene.apply_storage_cleanup_plan(
        plan["plan_digest"],
        workspace_root=workspace,
        base_dir=base,
    )

    assert changed.exists()
    assert reactivated.exists()
    statuses = {item["workspace_path"]: (item["status"], item.get("reason")) for item in job["items"]}
    assert statuses[".tmp/changed"] == ("skipped", "changed_after_plan")
    assert statuses[".tmp/reactivated"] == ("skipped", "active_status_after_plan:active")


def test_apply_quarantines_and_resumes_bounded_readonly_deletion(tmp_path: Path) -> None:
    workspace, base = _workspace(tmp_path)
    run = workspace / ".tmp" / "readonly-run"
    nested = run / "readonly" / "nested"
    nested.mkdir(parents=True)
    for index in range(9):
        path = nested / f"{index}.txt"
        path.write_text("payload", encoding="utf-8")
        os.chmod(path, stat.S_IREAD)
    os.chmod(nested, stat.S_IREAD)
    storage_hygiene.write_storage_manifest(
        run,
        owner="test",
        status="complete",
        retain_seconds=0,
        created_at=time.time() - 3600,
    )
    plan = storage_hygiene.plan_storage_cleanup(workspace_root=workspace, base_dir=base)
    assert plan["candidate_total"] == 1

    job = storage_hygiene.apply_storage_cleanup_plan(
        plan["plan_digest"],
        workspace_root=workspace,
        base_dir=base,
        max_seconds=1,
        max_entries=2,
    )

    assert run.exists() is False
    assert job["status"] == "running"
    inventory = storage_hygiene.list_storage_cleanup_jobs(workspace_root=workspace, base_dir=base)
    assert inventory["active_count"] == 1
    resumed = storage_hygiene.resume_pending_storage_jobs(
        workspace_root=workspace,
        base_dir=base,
        max_jobs=1,
        max_seconds=1,
        max_entries=2,
    )
    assert resumed["resumed_count"] == 1
    for _ in range(20):
        job = storage_hygiene.resume_storage_cleanup(
            job["job_id"],
            workspace_root=workspace,
            base_dir=base,
            max_seconds=1,
            max_entries=2,
        )
        if job["status"] == "complete":
            break
    assert job["status"] == "complete"
    assert job["items"][0]["deleted_entries"] >= 12
    assert Path(job["items"][0]["quarantine_path"]).exists() is False


def test_cleanup_plan_digest_rejects_tampering(tmp_path: Path) -> None:
    workspace, base = _workspace(tmp_path)
    cache = workspace / ".ruff_cache"
    cache.mkdir()
    old = time.time() - 10 * 86400
    os.utime(cache, (old, old))
    plan = storage_hygiene.plan_storage_cleanup(workspace_root=workspace, base_dir=base)
    plan_path = Path(plan["plan_path"])
    stored = json.loads(plan_path.read_text(encoding="utf-8"))
    stored["candidates"][0]["path"] = str(workspace)
    plan_path.write_text(json.dumps(stored), encoding="utf-8")

    try:
        storage_hygiene.apply_storage_cleanup_plan(
            plan["plan_digest"],
            workspace_root=workspace,
            base_dir=base,
        )
    except ValueError as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("tampered cleanup plan was accepted")


def test_maintenance_inspect_cli_returns_catalog(cli_app, tmp_path: Path) -> None:
    workspace, base = _workspace(tmp_path)
    result = CliRunner().invoke(
        cli_app,
        [
            "maintenance",
            "inspect",
            "--workspace-root",
            str(workspace),
            "--base-dir",
            str(base),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["catalog_version"] == storage_hygiene.CATALOG_VERSION
    assert any(item["id"] == "adaos_trials" for item in payload["roots"])
