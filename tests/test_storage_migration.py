from pathlib import Path

import pytest

from adaos.services.node_maintenance import NodeMaintenanceService
from adaos.services.node_maintenance import migration
from adaos.services.storage_hygiene import managed_storage_run


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


@pytest.mark.parametrize("change", ["add", "remove", "mutate"])
def test_source_changes_prevent_cutover_and_cancel_releases_gate(setup, change):
    service, source, target = setup
    job = start(setup)
    while job["phase"] != "verify_source":
        job = service.migrations.advance(job["job_id"])
        assert job["status"] == "running", job.get("error")
    if change == "add":
        (source / "unexpected").write_text("late writer", encoding="utf-8")
    elif change == "remove":
        (source / "run/evidence.bin").unlink()
    else:
        (source / "run/evidence.bin").write_bytes(b"changed")
    job = service.migrations.advance(job["job_id"])
    assert job["status"] == "paused"
    assert service.locations.resolve("e2e_evidence") == source
    service.migrations.cancel(job["job_id"])
    with managed_storage_run(source / "later", owner="test", retention_class="evidence"):
        pass


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
