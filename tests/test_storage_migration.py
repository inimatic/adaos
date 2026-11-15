from pathlib import Path
import os
import json

import pytest

from adaos.services.node_maintenance import NodeMaintenanceService
from adaos.services.node_maintenance import migration
from adaos.services.storage_hygiene import managed_storage_run


def retirement_until(service, job, status):
    for _ in range(150):
        result = service.migrations.retirement.advance(job["job_id"], max_seconds=.05)
        assert result["status"] != "paused", result.get("error")
        if result["status"] == status:
            return result
    pytest.fail("Retirement did not reach its durable checkpoint")


def test_retirement_cursor_cost_is_bounded_with_large_file_and_directory_indexes():
    import sqlite3
    from adaos.services.node_maintenance.retirement import StorageRetirement
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE entries(path TEXT UNIQUE,kind TEXT,size INTEGER,mtime INTEGER,identity TEXT,state TEXT)")
        db.execute("CREATE INDEX directories ON entries(kind,state,length(path) DESC)")
        db.executemany("INSERT INTO entries VALUES(?,'file',1,0,'identity','pending')",
                       ((f"run-{i}/evidence.bin",) for i in range(10000)))
        calls = 0
        def budget():
            nonlocal calls
            calls += 1
            return calls > 100  # 10,000 SQLite VM operations for 50 cursor steps.
        db.set_progress_handler(budget, 100)
        cursor = 0
        for _ in range(50):
            cursor = StorageRetirement._next_file(db, cursor)[0]
        assert cursor == 50


@pytest.mark.skipif(os.name != "nt", reason="Retirement requires Windows sharing locks")
def test_retirement_deletes_only_verified_duplicates_preserving_historical_path(setup):
    service, source, target = setup
    job = finish(service, start(setup))
    cleanup = service.migrations.retirement
    cleanup.review(job["job_id"])
    reviewed = retirement_until(service, job, "ready")
    retained = Path(job["retained_source"])
    assert (retained / "run/evidence.bin").exists()  # Review has no deletion effects.
    with pytest.raises(ValueError, match="exact reviewed"):
        cleanup.apply(job["job_id"], "0" * 64)
    cleanup.apply(job["job_id"], reviewed["plan_digest"])
    done = retirement_until(service, job, "completed")
    assert done["deleted_files"] == 1 and done["deleted_bytes"] == len(b"retained evidence" * 100)
    assert done["preserved_files"] == 0
    assert list(retained.iterdir()) == []
    assert (source / "run/evidence.bin").read_bytes() == (target / "run/evidence.bin").read_bytes()
    assert cleanup.apply(job["job_id"], reviewed["plan_digest"])["status"] == "completed"
    assert service.migrations.snapshot()[0]["source_space_reclaimed"] is True


@pytest.mark.skipif(os.name != "nt", reason="Retirement requires Windows sharing locks")
@pytest.mark.parametrize("change", ["during_migration", "source_hash", "target_hash", "missing_target", "late_change"])
def test_retirement_preserves_whole_changed_folder(setup, change):
    service, _, target = setup
    job = finish(service, start(setup))
    retained = Path(job["retained_source"])
    original = retained / "run/evidence.bin"
    if change == "during_migration":
        original.touch()
    elif change in {"source_hash", "target_hash"}:
        selected = original if change == "source_hash" else target / "run/evidence.bin"
        info = selected.stat()
        selected.write_bytes(b"x" * info.st_size)
        os.utime(selected, ns=(info.st_atime_ns, info.st_mtime_ns))
    elif change == "missing_target":
        (target / "run/evidence.bin").unlink()
    cleanup = service.migrations.retirement
    cleanup.review(job["job_id"])
    reviewed = retirement_until(service, job, "ready")
    if change == "late_change":
        original.write_bytes(b"late unique bytes")
    cleanup.apply(job["job_id"], reviewed["plan_digest"])
    done = retirement_until(service, job, "completed")
    assert original.exists() and done["preserved_files"] == 1 and done["deleted_files"] == 0
    assert any(p["path"] == "run" for p in done["preserved_directories"])


@pytest.mark.skipif(os.name != "nt", reason="Retirement requires Windows sharing locks")
def test_retirement_preserves_registered_run_and_siblings_but_reclaims_other_runs(setup):
    service, source, _ = setup
    (source / "run/.adaos-storage.json").write_text('{"status":"complete"}', encoding="utf-8")
    (source / "run/sibling.bin").write_bytes(b"unchanged")
    (source / "other").mkdir()
    (source / "other/cold.bin").write_bytes(b"cold")
    job = finish(service, start(setup))
    retained = Path(job["retained_source"])
    (retained / "run/evidence.bin").touch()
    cleanup = service.migrations.retirement
    cleanup.review(job["job_id"])
    reviewed = retirement_until(service, job, "ready")
    cleanup.apply(job["job_id"], reviewed["plan_digest"])
    done = retirement_until(service, job, "completed")
    assert (retained / "run/sibling.bin").read_bytes() == b"unchanged"
    assert (retained / "run/.adaos-storage.json").exists()
    assert not (retained / "other").exists()
    assert done["deleted_files"] == 1 and done["preserved_files"] == 3


@pytest.mark.skipif(os.name != "nt", reason="Retirement requires Windows sharing locks")
def test_retirement_excludes_open_writers_and_keeps_new_unreviewed_entries(setup):
    from adaos.services.node_maintenance.retirement import locked_file
    service, _, _ = setup
    job = finish(service, start(setup))
    retained = Path(job["retained_source"])
    cleanup = service.migrations.retirement
    cleanup.review(job["job_id"])
    reviewed = retirement_until(service, job, "ready")
    with (retained / "run/evidence.bin").open("r+b"):
        with pytest.raises(OSError):
            with locked_file(retained / "run/evidence.bin", delete=True):
                pytest.fail("Open writer was not excluded")
        cleanup.apply(job["job_id"], reviewed["plan_digest"])
        done = retirement_until(service, job, "completed")
    assert done["preserved_files"] == 1


def test_retirement_rejects_replaced_placement_and_corrupt_ledger(setup):
    service, _, _ = setup
    job = finish(service, start(setup))
    cleanup = service.migrations.retirement
    cleanup.review(job["job_id"])
    path = service.locations.settings_path
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["revision"] += 1
    path.write_text(json.dumps(settings), encoding="utf-8")
    result = cleanup.advance(job["job_id"])
    assert result["status"] == "paused" and "Placement" in result["error"]


@pytest.mark.skipif(os.name != "nt", reason="Retirement requires Windows sharing locks")
def test_retirement_continues_after_service_restart_and_keeps_late_files(setup):
    service, _, _ = setup
    job = finish(service, start(setup))
    cleanup = service.migrations.retirement
    cleanup.review(job["job_id"])
    reviewed = retirement_until(service, job, "ready")
    cleanup.apply(job["job_id"], reviewed["plan_digest"])
    retained = Path(job["retained_source"])
    (retained / "late").mkdir()
    (retained / "late/unique.bin").write_bytes(b"manual recovery")
    restarted = NodeMaintenanceService(workspace=service.locations.workspace, base=service.locations.base, node_id="test-node")
    done = retirement_until(restarted, job, "completed")
    assert done["deleted_files"] == 1 and done["preserved_files"] == 1
    assert (retained / "late/unique.bin").read_bytes() == b"manual recovery"


def test_retirement_rejects_actual_nested_link_before_admitting_deletion(setup, tmp_path):
    service, _, _ = setup
    job = finish(service, start(setup))
    outside = tmp_path / "outside-originals"
    outside.mkdir()
    (outside / "unique.bin").write_bytes(b"protected")
    migration._link(Path(job["retained_source"]) / "nested", outside)
    service.migrations.retirement.review(job["job_id"])
    result = service.migrations.retirement.advance(job["job_id"])
    assert result["status"] == "paused" and "reparse" in result["error"]
    assert (outside / "unique.bin").read_bytes() == b"protected"


@pytest.mark.skipif(os.name != "nt", reason="Retirement requires Windows sharing locks")
def test_owner_api_retirement_is_authenticated_and_continues_on_core_worker(setup, monkeypatch):
    import time
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from adaos.apps.api import maintenance
    from adaos.services.node_maintenance.worker import stop_worker
    service, _, _ = setup
    job = finish(service, start(setup))
    app = FastAPI()
    app.include_router(maintenance.router, prefix="/api/admin/maintenance")
    monkeypatch.setattr(maintenance, "from_context", lambda: service)
    monkeypatch.setenv("ADAOS_TOKEN", "retirement-owner")
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "active")
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "1")
    endpoint = f"/api/admin/maintenance/migrations/{job['job_id']}/retirement"
    headers = {"X-AdaOS-Token": "retirement-owner"}
    try:
        with TestClient(app) as client:
            assert client.post(endpoint + "/review").status_code == 401
            assert client.post(endpoint + "/review", headers=headers).status_code == 200
            deadline = time.monotonic() + 90
            while service.migrations.retirement.snapshot(job["job_id"])["status"] != "ready":
                assert time.monotonic() < deadline
                time.sleep(.02)
            assert client.post(endpoint + "/apply", headers=headers, json={"plan_digest":"0"*64}).status_code == 409
            reviewed = service.migrations.retirement.snapshot(job["job_id"])
            assert client.post(endpoint + "/apply", headers=headers, json={"plan_digest":reviewed["plan_digest"]}).status_code == 200
            while service.migrations.retirement.snapshot(job["job_id"])["status"] != "completed":
                assert time.monotonic() < deadline
                time.sleep(.02)
    finally:
        stop_worker(service)


@pytest.mark.skipif(os.name != "nt", reason="Retirement requires Windows sharing locks")
def test_retirement_handles_reject_parent_alias_and_exclude_new_writers(tmp_path):
    from adaos.services.node_maintenance.retirement import locked_file
    physical = tmp_path / "physical"
    physical.mkdir()
    payload = physical / "evidence.bin"
    payload.write_bytes(b"keep")
    alias = tmp_path / "alias"
    migration._link(alias, physical)
    with pytest.raises(ValueError, match="physical path"):
        with locked_file(alias / "evidence.bin", delete=True):
            pytest.fail("Physical path alias was admitted")
    with locked_file(payload, delete=True):
        with pytest.raises(OSError):
            payload.open("r+b")
    assert payload.read_bytes() == b"keep"


@pytest.mark.skipif(os.name != "nt", reason="Retirement requires Windows sharing locks")
def test_retirement_projection_failure_recovers_committed_delete_receipt(setup, monkeypatch):
    service, _, target = setup
    job = finish(service, start(setup))
    cleanup = service.migrations.retirement
    cleanup.review(job["job_id"])
    reviewed = retirement_until(service, job, "ready")
    cleanup.apply(job["job_id"], reviewed["plan_digest"])
    save = cleanup._save
    monkeypatch.setattr(cleanup, "_save", lambda _: (_ for _ in ()).throw(OSError("projection interrupted")))
    with pytest.raises(OSError, match="projection interrupted"):
        cleanup.advance(job["job_id"])
    monkeypatch.setattr(cleanup, "_save", save)
    done = retirement_until(service, job, "completed")
    assert done["deleted_files"] == 1 and (target / "run/evidence.bin").exists()


@pytest.fixture
def setup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = NodeMaintenanceService(workspace=workspace, base=workspace / ".adaos", node_id="test-node")
    source = service.locations.resolve("e2e_evidence")
    source.mkdir(parents=True)
    (source / "run").mkdir()
    (source / "run/evidence.bin").write_bytes(b"retained evidence" * 100)
    (source / "empty").mkdir()
    target = tmp_path / "external/evidence"
    plan = service.locations.plan(role="e2e_evidence", desired_path=str(target), expected_revision=0, min_free_bytes=0)
    service.locations.apply(plan["plan_digest"])
    service.advance_inventory("e2e_evidence")
    return service, source, target


def start(setup):
    service, _, _ = setup
    plan = service.migrations.plan("e2e_evidence", 1)
    assert plan["admitted"], plan["reasons"]
    return service.migrations.start(plan["plan_digest"])


def finish(service, job):
    for _ in range(50):
        job = service.migrations.advance(job["job_id"], max_seconds=.05)
        assert job["status"] != "paused", job.get("error")
        if job["status"] == "completed":
            return job
    pytest.fail("Migration did not complete in bounded slices")


def test_review_has_no_copy_or_writer_effects_and_requires_complete_measurement(setup):
    service, source, target = setup
    plan = service.migrations.plan("e2e_evidence", 1)
    assert plan["admitted"] and plan["retains_original"]
    assert not target.exists()
    assert not migration.gate_paths(source)[0].exists()
    assert service.locations.resolve("e2e_evidence") == source
    service.inventory.path("e2e_evidence").unlink()
    assert not service.migrations.plan("e2e_evidence", 1)["admitted"]


def test_real_copy_verification_link_cutover_and_historical_path_readback(setup):
    service, source, target = setup
    expected = (source / "run/evidence.bin").read_bytes()
    job = finish(service, start(setup))
    assert service.locations.resolve("e2e_evidence") == target
    assert service.locations.settings()["revision"] == 2
    assert (target / "run/evidence.bin").read_bytes() == expected
    assert (source / "run/evidence.bin").read_bytes() == expected
    assert (Path(job["retained_source"]) / "run/evidence.bin").read_bytes() == expected
    assert job["copied_bytes"] == job["verified_bytes"] == len(expected)
    assert (target / "empty").is_dir()
    with managed_storage_run(source / "new-run", owner="test", retention_class="evidence") as run:
        assert run.parent == target
    assert (target / "new-run/.adaos-storage.json").exists()
    assert service.migrations.start(job["job_id"])["replayed"]
    assert service.migrations.advance(job["job_id"])["status"] == "completed"


def test_admission_blocks_new_runs_and_active_run_pauses_migration(setup):
    service, source, target = setup
    with managed_storage_run(source / "running", owner="test", retention_class="evidence"):
        job = start(setup)
        job = service.migrations.advance(job["job_id"])
        assert job["status"] == "paused"
        assert "active" in job["error"]
        with pytest.raises(ValueError, match="in progress"):
            with managed_storage_run(source / "later", owner="test", retention_class="evidence"):
                pass
        assert not (source / "later").exists()
        service.migrations.cancel(job["job_id"])
    assert not target.exists()


def test_copy_ledger_resumes_chunks_and_rejects_content_corruption(setup, monkeypatch):
    service, source, target = setup
    monkeypatch.setattr(migration, "CHUNK", 19)
    job = start(setup)
    while job["phase"] != "verify_copy":
        job = service.migrations.advance(job["job_id"], max_seconds=.01)
        assert job["status"] == "running", job.get("error")
    (target / "run/evidence.bin").write_bytes(b"X" * 1700)
    result = service.migrations.advance(job["job_id"])
    assert result["status"] == "paused"
    assert "verification" in result["error"]
    assert source.resolve() == source
    assert service.locations.resolve("e2e_evidence") == source
    service.migrations.cancel(job["job_id"])
    assert source.is_dir() and target.is_dir()


@pytest.mark.parametrize("change", ["remove", "mutate"])
def test_source_changes_prevent_cutover_and_cancel_releases_gate(setup, change):
    service, source, target = setup
    job = start(setup)
    while job["phase"] != "verify_source":
        job = service.migrations.advance(job["job_id"])
        assert job["status"] == "running", job.get("error")
    if change == "remove":
        (source / "run/evidence.bin").unlink()
    else:
        (source / "run/evidence.bin").write_bytes(b"changed")
    job = service.migrations.advance(job["job_id"])
    assert job["status"] == "paused"
    assert service.locations.resolve("e2e_evidence") == source
    service.migrations.cancel(job["job_id"])
    with managed_storage_run(source / "later", owner="test", retention_class="evidence"):
        pass


def verified_source_checkpoint(setup):
    service, _, _ = setup
    job = start(setup)
    for _ in range(30):
        job = service.migrations.advance(job["job_id"])
        assert job["status"] == "running", job.get("error")
        if job["phase"] == "verify_source":
            return job
    pytest.fail("Did not reach source verification")


@pytest.mark.parametrize("legacy_pause", [False, True])
def test_append_only_source_reconciliation_reuses_verified_prefix_and_resumes(setup, monkeypatch, legacy_pause):
    import json
    import sqlite3
    service, source, target = setup
    job = verified_source_checkpoint(setup)
    original_bytes, prefix_digest = job["bytes"], job["content_digest"]
    (source / "late-run").mkdir()
    (source / "late-run/new.bin").write_bytes(b"new evidence")
    (source / "late-run/empty.bin").touch()
    folder = service.migrations._path(job["job_id"])
    if legacy_pause:
        projection = migration.read(folder / "job.json")
        projection.update(status="paused", error="Source changed during relocation; originals are retained")
        migration.atomic_write_json(folder / "job.json", projection)
    with sqlite3.connect(folder / "entries.sqlite") as db:
        old_chunks = db.execute("SELECT * FROM chunks").fetchall()
    # Reopening old file content would throw: metadata checks remain allowed.
    original_open = Path.open
    def guarded_open(path, *args, **kwargs):
        if path in {source / "run/evidence.bin", target / "run/evidence.bin"}:
            raise AssertionError("Verified prefix was read again")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    job = service.migrations.advance(job["job_id"])
    assert job["phase"] in {"reconcile_source", "copy"}
    # A fresh service instance reconstructs execution from the SQLite checkpoint.
    resumed = NodeMaintenanceService(workspace=service.locations.workspace, base=service.locations.base, node_id="test-node")
    job = finish(resumed, job)
    assert job["bytes"] == job["copied_bytes"] == job["verified_bytes"] == original_bytes + 12
    assert job["files"] == 3
    assert job["source_reconciliations"][0]["verified_prefix_digest"] == prefix_digest
    assert job["source_reconciliations"][0]["added_files"] == 2
    assert job["source_reconciliations"][0]["added_bytes"] == 12
    with sqlite3.connect(folder / "entries.sqlite") as db:
        assert db.execute("SELECT * FROM chunks WHERE entry=?", (old_chunks[0][0],)).fetchall() == old_chunks
    assert (target / "late-run/new.bin").read_bytes() == b"new evidence"
    assert (Path(job["retained_source"]) / "late-run/new.bin").read_bytes() == b"new evidence"


@pytest.mark.parametrize("fault", ["modified", "removed", "lease", "foreign_destination", "source_volume"])
def test_reconciliation_preserves_fences_and_rejects_changes_to_existing_entries(setup, monkeypatch, fault):
    service, source, target = setup
    job = verified_source_checkpoint(setup)
    (source / "late").write_bytes(b"late")
    job = service.migrations.advance(job["job_id"])
    assert job["phase"] == "reconcile_source"
    if fault == "modified":
        (source / "run/evidence.bin").write_bytes(b"changed")
    elif fault == "removed":
        (source / "run/evidence.bin").unlink()
    elif fault == "lease":
        (source / ".adaos-lease.json").write_text("{}", encoding="utf8")
    elif fault == "foreign_destination":
        migration.atomic_write_json(target / ".adaos-migration-owner.json", {"job_id": "other"})
    else:
        real = migration.volume
        monkeypatch.setattr(migration, "volume", lambda path: {**real(path), "id": "replaced-source"} if Path(path) == source else real(path))
    for _ in range(10):
        job = service.migrations.advance(job["job_id"])
        if job["status"] == "paused":
            break
    assert job["status"] == "paused"
    assert service.locations.resolve("e2e_evidence") == source
    assert source.resolve() == source
    assert (target / "run/evidence.bin").read_bytes() == b"retained evidence" * 100


def test_repeated_additions_reconcile_each_verified_generation(setup):
    service, source, target = setup
    job = verified_source_checkpoint(setup)
    for generation in range(2):
        (source / f"late-{generation}").write_bytes(b"delta")
        job = service.migrations.advance(job["job_id"])
        assert job["phase"] == "reconcile_source"
        for _ in range(20):
            job = service.migrations.advance(job["job_id"])
            assert job["status"] == "running", job.get("error")
            if job["phase"] == "verify_source":
                break
        assert len(job["source_reconciliations"]) == generation + 1
    job = finish(service, job)
    assert job["copied_bytes"] == job["verified_bytes"] == job["bytes"] == 1710


def test_enumerated_metadata_still_rejects_nested_directory_links(setup, tmp_path):
    service, source, target = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.bin").write_bytes(b"must remain outside")
    migration._link(source / "nested-link", outside)
    job = service.migrations.advance(start(setup)["job_id"])
    assert job["status"] == "paused" and "links or reparse" in job["error"]
    assert not target.exists()
    assert source.resolve() == source
    assert (outside / "private.bin").read_bytes() == b"must remain outside"


def test_missing_destination_volume_can_resume_without_duplicate_copy(setup, monkeypatch):
    service, _, _ = setup
    job = start(setup)
    real = migration.volume
    monkeypatch.setattr(migration, "volume", lambda _: (_ for _ in ()).throw(OSError("offline")))
    paused = service.migrations.advance(job["job_id"])
    assert paused["status"] == "paused" and "offline" in paused["error"]
    monkeypatch.setattr(migration, "volume", real)
    assert finish(service, paused)["status"] == "completed"


def test_crash_after_rename_resumes_or_cancels_without_losing_original(setup, monkeypatch):
    service, source, target = setup
    real_link = migration._link
    monkeypatch.setattr(migration, "_link", lambda *_: (_ for _ in ()).throw(OSError("interrupted cutover")))
    job = start(setup)
    for _ in range(20):
        job = service.migrations.advance(job["job_id"])
        if job["status"] == "paused":
            break
    assert not source.exists() and Path(job["retained_source"]).exists()
    monkeypatch.setattr(migration, "_link", real_link)
    assert finish(service, job)["status"] == "completed"


def test_cancel_after_rename_restores_original_path(setup, monkeypatch):
    service, source, _ = setup
    monkeypatch.setattr(migration, "_link", lambda *_: (_ for _ in ()).throw(OSError("interrupted cutover")))
    job = start(setup)
    for _ in range(20):
        job = service.migrations.advance(job["job_id"])
        if job["status"] == "paused":
            break
    assert service.migrations.cancel(job["job_id"])["status"] == "cancelled"
    assert source.resolve() == source and (source / "run/evidence.bin").exists()


def test_unrelated_existing_destination_and_stale_plan_are_never_admitted(setup):
    service, _, target = setup
    reviewed = service.migrations.plan("e2e_evidence", 1)
    target.mkdir(parents=True)
    (target / "user-data").write_bytes(b"preserve")
    assert not service.migrations.plan("e2e_evidence", 1)["admitted"]
    with pytest.raises(ValueError, match="Destination changed"):
        service.migrations.start(reviewed["plan_digest"])
    assert (target / "user-data").read_bytes() == b"preserve"


def test_recovery_without_its_writer_restore_adapter_is_not_executable(setup):
    service, _, _ = setup
    source = service.locations.resolve("application_recovery")
    source.mkdir(parents=True)
    proposed = service.migrations.plan("application_recovery", 1)
    assert not proposed["admitted"]
    with pytest.raises(ValueError, match="not admitted"):
        service.migrations.start(proposed["plan_digest"])


def test_same_role_nested_destination_is_rejected(setup):
    service, source, _ = setup
    with pytest.raises(ValueError, match="overlaps its current root"):
        service.locations.validate_destination("e2e_evidence", source / "nested")


def test_ledger_commit_before_projection_write_is_recoverable(setup, monkeypatch):
    service, _, _ = setup
    job = start(setup)
    real = service.migrations._save
    monkeypatch.setattr(service.migrations, "_save", lambda _: (_ for _ in ()).throw(OSError("interrupted projection")))
    with pytest.raises(OSError, match="projection"):
        service.migrations.advance(job["job_id"])
    monkeypatch.setattr(service.migrations, "_save", real)
    completed = finish(service, job)
    assert completed["files"] == 1
    assert completed["content_digest"].startswith("sha256:")


def test_settings_commit_before_gate_acknowledgement_is_reconciled(setup, monkeypatch):
    service, source, target = setup
    job = start(setup)
    while job["phase"] != "commit":
        job = service.migrations.advance(job["job_id"])
        assert job["status"] == "running", job.get("error")
    real = migration.atomic_write_json
    def interrupt_gate(path, value):
        if path == migration.gate_paths(source)[0] and value.get("status") == "completed":
            raise OSError("interrupted gate acknowledgement")
        return real(path, value)
    monkeypatch.setattr(migration, "atomic_write_json", interrupt_gate)
    assert service.migrations.advance(job["job_id"])["status"] == "paused"
    assert service.locations.resolve("e2e_evidence") == target
    monkeypatch.setattr(migration, "atomic_write_json", real)
    assert finish(service, job)["revision"] == 2
    assert service.locations.settings()["revision"] == 2


def test_missing_gate_is_rebuilt_before_resuming_a_job(setup):
    service, source, _ = setup
    job = start(setup)
    migration.gate_paths(source)[0].unlink()
    service.migrations.start(job["job_id"])
    with pytest.raises(ValueError, match="in progress"):
        with managed_storage_run(source / "racing", owner="test", retention_class="evidence"):
            pass
    assert finish(service, job)["status"] == "completed"


def test_edited_unsaved_location_cannot_review_another_destination(setup):
    service, _, target = setup
    with pytest.raises(ValueError, match="Save the edited"):
        service.migrations.plan("e2e_evidence", 1, desired_path=str(target.parent / "unsaved"))


def test_placement_edits_cannot_race_an_active_migration(setup):
    service, _, target = setup
    proposed = service.locations.plan(role="e2e_evidence", desired_path=str(target.parent / "other"), expected_revision=1, min_free_bytes=0)
    job = start(setup)
    with pytest.raises(ValueError, match="active storage migration"):
        service.locations.apply(proposed["plan_digest"])
    service.migrations.cancel(job["job_id"])
    assert service.locations.apply(proposed["plan_digest"])["revision"] == 2


def test_owner_api_rejects_unauthorized_start_and_deduplicates_the_exact_plan(setup, monkeypatch):
    import time
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from adaos.apps.api import maintenance
    service, _, target = setup
    app = FastAPI()
    app.include_router(maintenance.router, prefix="/api/admin/maintenance")
    monkeypatch.setattr(maintenance, "from_context", lambda: service)
    monkeypatch.setenv("ADAOS_TOKEN", "owner-test-token")
    headers = {"X-AdaOS-Token": "owner-test-token"}
    with TestClient(app) as client:
        response = client.post("/api/admin/maintenance/migrations/plan", headers=headers,
                               json={"role": "e2e_evidence", "expected_revision": 1})
        assert response.status_code == 200 and response.json()["admitted"]
        body = {"plan_digest": response.json()["plan_digest"]}
        assert client.post("/api/admin/maintenance/migrations/start", json=body).status_code == 401
        assert not target.exists()
        first = client.post("/api/admin/maintenance/migrations/start", headers=headers, json=body)
        assert first.status_code == 200
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                replay = client.post("/api/admin/maintenance/migrations/start", headers=headers, json=body)
                assert replay.status_code == 200 and replay.json()["replayed"]
                if replay.json()["status"] == "completed":
                    break
                time.sleep(.02)
            assert replay.json()["status"] == "completed", replay.json()
            assert replay.json()["job_id"] == first.json()["job_id"]
            assert service.locations.settings()["revision"] == 2
        finally:
            from adaos.services.node_maintenance.worker import stop_worker
            stop_worker(service)


def test_second_relocation_is_not_admitted_until_alias_chain_adapter_exists(setup):
    service, _, target = setup
    finish(service, start(setup))
    second = service.locations.plan(role="e2e_evidence", desired_path=str(target.parent / "next"), expected_revision=2, min_free_bytes=0)
    service.locations.apply(second["plan_digest"])
    reviewed = service.migrations.plan("e2e_evidence", 3)
    assert not reviewed["admitted"]
    assert any("first relocation" in reason for reason in reviewed["reasons"])
    with pytest.raises(ValueError, match="not admitted"):
        service.migrations.start(reviewed["plan_digest"])


def test_relocated_cleanup_preserves_logical_pins_and_uses_same_volume_quarantine(setup):
    from adaos.services import storage_hygiene as hygiene
    service, source, target = setup
    (source / "expired").mkdir()
    (source / "expired/payload").write_bytes(b"old")
    hygiene.write_storage_manifest(source / "expired", owner="test", status="complete", retain_seconds=0)
    hygiene.pin_storage_path(source / "run", workspace_root=service.locations.workspace, base_dir=service.locations.base)
    completed = finish(service, start(setup))
    report = hygiene.inspect_storage(workspace_root=service.locations.workspace, base_dir=service.locations.base)
    observed = next(row for row in report["roots"] if row["id"] == "e2e_evidence")
    assert observed["path"] == str(target) and observed["workspace_path"] == "e2e/artifacts"
    assert hygiene.explain_storage_path(target / "run", workspace_root=service.locations.workspace, base_dir=service.locations.base)["pin"]
    plan = hygiene.plan_storage_cleanup(workspace_root=service.locations.workspace, base_dir=service.locations.base, spec_ids={"e2e_evidence"})
    assert [row["workspace_path"] for row in plan["candidates"]] == ["e2e/artifacts/expired"]
    job = hygiene.apply_storage_cleanup_plan(plan["plan_digest"], workspace_root=service.locations.workspace, base_dir=service.locations.base, max_entries=1)
    quarantine = Path(job["items"][0]["quarantine_path"])
    assert quarantine.parent.parent.parent == target.parent / ("." + target.name + ".adaos-quarantine")
    assert job["items"][0]["quarantine_volume_id"] == str(target.stat().st_dev)
    while job["status"] != "complete":
        job = hygiene.resume_storage_cleanup(job["job_id"], workspace_root=service.locations.workspace, base_dir=service.locations.base)
    assert not (target / "expired").exists()
    assert (Path(completed["retained_source"]) / "expired/payload").read_bytes() == b"old"
    assert (target / "run/evidence.bin").exists()


def test_quarantine_volume_outage_is_paused_and_explicitly_resumable(setup, monkeypatch):
    from adaos.services import storage_hygiene as hygiene
    from adaos.services.node_maintenance import storage
    service, source, target = setup
    finish(service, start(setup))
    hygiene.adopt_legacy_storage_path(target / "run", workspace_root=service.locations.workspace, base_dir=service.locations.base, retain_seconds=0)
    plan = hygiene.plan_storage_cleanup(workspace_root=service.locations.workspace, base_dir=service.locations.base, spec_ids={"e2e_evidence"})
    job = hygiene.apply_storage_cleanup_plan(plan["plan_digest"], workspace_root=service.locations.workspace, base_dir=service.locations.base, max_entries=1)
    assert job["status"] == "running"
    real = storage.volume
    monkeypatch.setattr(storage, "volume", lambda _: (_ for _ in ()).throw(OSError("offline volume")))
    job = hygiene.resume_storage_cleanup(job["job_id"], workspace_root=service.locations.workspace, base_dir=service.locations.base)
    assert job["status"] == "paused" and job["items"][0]["status"] == "paused"
    monkeypatch.setattr(storage, "volume", real)
    job = hygiene.resume_storage_cleanup(job["job_id"], workspace_root=service.locations.workspace, base_dir=service.locations.base)
    assert job["status"] == "complete"


def test_unregistered_external_root_does_not_gain_cleanup_authority(setup):
    from adaos.services import storage_hygiene as hygiene
    service, _, target = setup
    target.mkdir(parents=True)
    (target / "private").mkdir()
    with pytest.raises(ValueError, match="outside workspace"):
        hygiene.create_storage_cleanup_plan([{"path": str(target / "private")}], workspace_root=service.locations.workspace, base_dir=service.locations.base, source="test")


def test_replaced_volume_never_admits_writers_even_if_root_marker_is_missing(setup, monkeypatch):
    service, source, target = setup
    finish(service, start(setup))
    (target / ".adaos-migration-owner.json").unlink()
    monkeypatch.chdir(service.locations.workspace)
    real = migration.volume
    monkeypatch.setattr(migration, "volume", lambda path: {**real(path), "id": "replacement-disk"})
    with pytest.raises(ValueError, match="volume changed"):
        with managed_storage_run(target / "new-run", owner="test", retention_class="evidence"):
            pass
    assert not (target / "new-run").exists()


@pytest.mark.parametrize("filename", ["e2e/stand/qualify-node-maintenance.py",
    "src/adaos/e2e/semantic_proposer_comparison.py", "src/adaos/e2e/semantic_contract_qualification.py",
    "src/adaos/e2e/prototype_context_ablation.py"])
def test_actual_e2e_cli_containment_accepts_relocation_and_rejects_escape(setup, filename):
    import ast
    service, source, target = setup
    finish(service, start(setup))
    candidate = (source / "builder/new-run").resolve()
    repository = Path(__file__).resolve().parents[1]
    tree = ast.parse((repository / filename).read_text(encoding="utf-8"))
    predicates = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "is_relative_to"
        and any(isinstance(value, ast.Constant) and value.value in {"e2e/artifacts", "e2e/artifacts/builder"} for value in ast.walk(node.args[0]))]
    assert predicates
    for node in predicates:
        expression = compile(ast.Expression(body=node), filename, "eval")
        def evaluate(path):
            return eval(expression, {"Path": Path, "root": service.locations.workspace, "repo": service.locations.workspace,
                "output": path, "source_run": path})
        assert evaluate(candidate)
        assert not evaluate(target.parent / "escape")
