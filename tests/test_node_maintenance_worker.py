import json
import time

import pytest

from adaos.services.node_maintenance import NodeMaintenanceService
from adaos.services.node_maintenance import migration
from adaos.services.node_maintenance.worker import ensure_worker, observation, stop_worker


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "active")
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "1")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = NodeMaintenanceService(workspace=workspace, base=workspace / ".adaos", node_id="worker-test")
    source = service.locations.resolve("e2e_evidence")
    source.mkdir(parents=True)
    (source / "evidence.bin").write_bytes(b"preserve this content" * 100)
    target = tmp_path / "external/evidence"
    plan = service.locations.plan(role="e2e_evidence", desired_path=str(target), expected_revision=0, min_free_bytes=0)
    service.locations.apply(plan["plan_digest"])
    service.advance_inventory("e2e_evidence")
    job = service.migrations.start(service.migrations.plan("e2e_evidence", 1)["plan_digest"])
    try:
        yield service, source, target, job
    finally:
        stop_worker(service)


def wait_until(function, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(.02)
    pytest.fail("Continuing maintenance did not reach the expected checkpoint")


def test_migration_continues_past_old_request_budget_without_client_polling(prepared, monkeypatch):
    service, source, target, job = prepared
    # Accelerate the former 30-second boundary. The worker must ignore a request's
    # lifetime and finish all independently committed phases, including cutover.
    actual_advance = service.migrations.advance
    started = time.monotonic()
    calls = []

    def slow_slice(*args, **kwargs):
        time.sleep(.08)
        calls.append(time.monotonic())
        return actual_advance(*args, **kwargs)

    monkeypatch.setattr(service.migrations, "advance", slow_slice)
    ensure_worker(service)
    wait_until(lambda: service.migrations.snapshot()[0]["status"] == "completed")
    assert len(calls) >= 7 and calls[-1] - started > .5
    assert (source / "evidence.bin").read_bytes() == (target / "evidence.bin").read_bytes()
    assert service.locations.settings()["last_migration"] == job["job_id"]


def test_single_owner_and_restart_resume_the_saved_job(prepared, monkeypatch):
    service, _, _, job = prepared
    actual_advance = service.migrations.advance
    count = []

    def slice_then_stop(*args, **kwargs):
        result = actual_advance(*args, **kwargs)
        count.append(result["phase"])
        worker.stop_event.set()
        return result

    monkeypatch.setattr(service.migrations, "advance", slice_then_stop)
    # Start only after the closure's owner exists.
    from adaos.services.node_maintenance.worker import MaintenanceWorker
    worker = MaintenanceWorker(service)
    worker.thread.start()
    worker.thread.join(timeout=30)
    assert not worker.thread.is_alive() and count
    checkpoint = service.migrations.snapshot()[0]
    assert checkpoint["status"] == "running" and checkpoint["execution_status"] == "interrupted"
    monkeypatch.setattr(service.migrations, "advance", actual_advance)
    first = ensure_worker(service)
    assert ensure_worker(service) is first
    wait_until(lambda: service.migrations.snapshot()[0]["status"] == "completed")
    assert service.locations.settings()["last_migration"] == job["job_id"]


def test_unexpected_failure_pauses_job_and_does_not_retry_without_resume(prepared, monkeypatch):
    service, _, target, _ = prepared
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("injected worker failure")

    monkeypatch.setattr(service.migrations, "advance", fail)
    ensure_worker(service)
    wait_until(lambda: service.migrations.snapshot()[0]["status"] == "paused")
    time.sleep(.15)
    assert len(calls) == 1
    assert "injected worker failure" in service.migrations.snapshot()[0]["error"]
    assert not target.exists()
    assert observation(service.locations.state)["active"]


def test_terminal_or_paused_jobs_are_never_restarted(prepared, monkeypatch):
    service, _, _, job = prepared
    service.migrations.cancel(job["job_id"])
    monkeypatch.setattr(service.migrations, "advance", lambda *_a, **_kw: pytest.fail("Cancelled job restarted"))
    ensure_worker(service)
    wait_until(lambda: observation(service.locations.state)["active"])
    assert service.migrations.snapshot()[0]["execution_status"] == "cancelled"


def test_stale_heartbeat_is_not_running_execution(prepared):
    service, _, _, _ = prepared
    service.locations.state.mkdir(parents=True, exist_ok=True)
    (service.locations.state / "worker.json").write_text(json.dumps({
        "status": "running", "heartbeat_at": "2020-01-01T00:00:00+00:00",
        "task": {"id": service.migrations.snapshot()[0]["job_id"]},
    }), encoding="utf-8")
    assert not observation(service.locations.state)["active"]
    assert service.migrations.snapshot()[0]["execution_status"] == "interrupted"


def test_inventory_finishes_without_another_http_request(prepared):
    service, source, _, job = prepared
    service.migrations.cancel(job["job_id"])
    service.inventory.path("e2e_evidence").unlink()
    for index in range(40):
        (source / f"sample-{index}.txt").write_text("sample", encoding="utf-8")
    first = service.inventory.advance("e2e_evidence", source, max_entries=1)
    assert not first["complete"]
    ensure_worker(service)
    wait_until(lambda: service.inventory.snapshot("e2e_evidence", source)["complete"])
    assert service.inventory.snapshot("e2e_evidence", source)["files"] == 41


def test_cancellation_is_honored_between_worker_slices(prepared, monkeypatch):
    service, source, target, job = prepared
    monkeypatch.setattr(migration, "CHUNK", 7)
    actual_advance = service.migrations.advance

    def cancel_after_scan(*args, **kwargs):
        result = actual_advance(*args, **kwargs)
        service.migrations.cancel(job["job_id"])
        return result

    monkeypatch.setattr(service.migrations, "advance", cancel_after_scan)
    ensure_worker(service)
    wait_until(lambda: service.migrations.snapshot()[0]["status"] == "cancelled")
    assert source.is_dir() and not target.exists()


@pytest.mark.parametrize("role,authorized", [("candidate", "1"), ("active", "0"), ("candidate", "0")])
def test_fenced_core_never_acquires_maintenance_or_advances_jobs(prepared, monkeypatch, role, authorized):
    service, _, target, _ = prepared
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", role)
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", authorized)
    from adaos.services.node_maintenance.worker import MaintenanceWorker
    worker=MaintenanceWorker(service)
    worker.thread.start()
    try:
        worker.thread.join(timeout=5)
        assert not worker.thread.is_alive()
        assert not (service.locations.state/'worker.json').exists()
        assert service.migrations.snapshot()[0]['files']==0
        assert not target.exists()
    finally:
        worker.stop()


def test_revoked_core_yields_after_checkpoint_and_new_authority_resumes(prepared, monkeypatch):
    service, _, target, _ = prepared
    actual_advance=service.migrations.advance

    def retire_after_checkpoint(*args, **kwargs):
        result=actual_advance(*args, **kwargs)
        monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "0")
        return result

    monkeypatch.setattr(service.migrations,"advance",retire_after_checkpoint)
    worker=ensure_worker(service)
    wait_until(lambda:not worker.thread.is_alive())
    checkpoint=service.migrations.snapshot()[0]
    assert checkpoint['status']=='running' and checkpoint['execution_status']=='interrupted'
    assert checkpoint['phase']=='copy' and not target.exists()
    monkeypatch.setattr(service.migrations,"advance",actual_advance)
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "1")
    ensure_worker(service)
    wait_until(lambda:service.migrations.snapshot()[0]['status']=='completed')
