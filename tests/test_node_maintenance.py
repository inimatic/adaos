from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from adaos.services.node_maintenance import NodeMaintenanceService
from adaos.services.node_maintenance import storage


@pytest.fixture
def service(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return NodeMaintenanceService(workspace=workspace, base=workspace / ".adaos", node_id="test-node")


def plan(service, destination, **options):
    return service.locations.plan(role="e2e_evidence", desired_path=str(destination),
        expected_revision=service.locations.settings()["revision"], min_free_bytes=0, **options)


def test_desired_settings_never_create_destination_move_data_or_switch_writers(service, tmp_path):
    source = service.locations.resolve("e2e_evidence")
    source.mkdir(parents=True)
    (source / "retained.bin").write_bytes(b"retained evidence")
    target = tmp_path / "another-volume" / "evidence"
    proposed = plan(service, target)
    receipt = service.locations.apply(proposed["plan_digest"])
    assert receipt["revision"] == 1
    assert receipt["moves_data"] is False
    assert receipt["switches_writers"] is False
    assert service.locations.resolve("e2e_evidence") == source
    assert not target.exists()
    assert (source / "retained.bin").read_bytes() == b"retained evidence"
    assert service.locations.settings()["roles"]["e2e_evidence"]["desired_path"] == str(target)


def test_review_binds_original_editor_input_separately_from_normalized_destination(service, tmp_path):
    destination = tmp_path / 'separate-storage/evidence'
    requested = str(destination).replace('\\', '/')
    reviewed = plan(service, requested)
    assert reviewed['requested_path'] == requested
    assert reviewed['desired_path'] == str(destination.resolve())
    record = storage.read(service.locations.state / 'placement-plans' / (reviewed['plan_digest']+'.json'))
    assert record['requested_path'] == requested
    assert storage.digest(record) == reviewed['plan_digest']


def test_competing_plans_cannot_overwrite_revision_and_retry_is_idempotent(service, tmp_path):
    first, second = plan(service, tmp_path / "one"), plan(service, tmp_path / "two")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.locations.apply, p["plan_digest"]) for p in (first, second)]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except ValueError as exc:
                assert "stale" in str(exc)
    assert len(results) == 1
    again = service.locations.apply(results[0]["plan_digest"])
    assert again["replayed"] is True
    assert service.locations.settings()["revision"] == 1


def test_crash_after_settings_commit_recovers_acknowledgement_without_new_effect(service, tmp_path, monkeypatch):
    proposed = plan(service, tmp_path / "destination")
    original = storage.atomic_write_json
    def interrupt_receipt(path, payload):
        if path.parent.name == "placement-receipts":
            raise OSError("interrupted acknowledgement")
        original(path, payload)
    monkeypatch.setattr(storage, "atomic_write_json", interrupt_receipt)
    with pytest.raises(OSError):
        service.locations.apply(proposed["plan_digest"])
    monkeypatch.setattr(storage, "atomic_write_json", original)
    receipt = service.locations.apply(proposed["plan_digest"])
    assert receipt["replayed"] is True
    assert receipt["revision"] == service.locations.settings()["revision"] == 1


def test_volume_replacement_and_capacity_change_invalidate_plan(service, tmp_path, monkeypatch):
    proposed = plan(service, tmp_path / "destination")
    original = storage.volume
    monkeypatch.setattr(storage, "volume", lambda path: {**original(path), "id": "replacement"})
    with pytest.raises(ValueError, match="volume changed"):
        service.locations.apply(proposed["plan_digest"])
    assert service.locations.settings()["revision"] == 0


@pytest.mark.parametrize("suffix", ["src", ".git", ".tmp/nested", ".adaos/workspace", ".adaos/state/elsewhere", "."])
def test_protected_or_overlapping_destinations_are_rejected(service, suffix):
    with pytest.raises(ValueError, match="overlaps protected"):
        plan(service, service.locations.workspace / suffix)


def test_nested_inventory_resumes_and_never_double_counts_completed_slices(service):
    source = service.locations.resolve("e2e_evidence")
    for index in range(17):
        path = source / ("left" if index % 2 else "right") / str(index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * (index + 1))
    for _ in range(50):
        sample = service.inventory.advance("e2e_evidence", source, max_entries=2, max_seconds=1)
        if sample["complete"]:
            break
    assert sample["complete"]
    assert sample["slices"] > 2
    assert sample["files"] == 17
    assert sample["bytes"] == sum(range(1, 18))
    again = service.inventory.advance("e2e_evidence", source)
    assert again == sample


def test_legacy_recovery_never_becomes_restorable_from_module_presence(service):
    root = service.locations.resolve("application_recovery")
    (root / "unknown-snapshot").mkdir(parents=True)
    result = service.snapshot(section="recovery")
    point = result["points"][0]
    assert point["adapter_installed"] is True
    assert point["status"] == "unclassified"
    assert point["integrity"] == "not_verified"
    assert point["restore_rehearsal"] == "not_run"
    assert point["restorable"] is False


def test_plan_tampering_is_rejected_before_settings_write(service, tmp_path):
    proposed = plan(service, tmp_path / "destination")
    record = service.locations.state / "placement-plans" / (proposed["plan_digest"] + ".json")
    document = json.loads(record.read_text())
    document["desired_path"] = str(tmp_path / "another-destination")
    record.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="Exact placement plan"):
        service.locations.apply(proposed["plan_digest"])
    assert not service.locations.settings_path.exists()


def test_owner_api_refuses_unauthenticated_settings_changes(service, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from adaos.apps.api import maintenance
    app = FastAPI()
    app.include_router(maintenance.router, prefix="/api/admin/maintenance")
    monkeypatch.setattr(maintenance, "from_context", lambda: service)
    monkeypatch.setenv("ADAOS_TOKEN", "test-owner-token")
    with TestClient(app) as client:
        assert client.get("/api/admin/maintenance").status_code == 401
        response = client.get("/api/admin/maintenance", headers={"X-AdaOS-Token": "test-owner-token"})
        assert response.status_code == 200
        assert response.json()["revision"] == 0


def test_new_recovery_point_keeps_owner_identity_without_claiming_rehearsal(service):
    from adaos.services.node_maintenance.recovery import register_point
    root = service.locations.resolve("application_recovery") / "point"
    register_point(root, application_id="app", candidate_id="candidate", release_digest="sha256:release",
                   operation_id="operation", status="completed")
    result = service.snapshot(section="recovery")["points"][0]
    assert result["application_id"] == "app"
    assert result["status"] == "registered"
    assert result["owner_status"] == "completed"
    assert result["restorable"] is False
    assert result["restore_rehearsal"] == "not_run"
    with pytest.raises(ValueError, match="identity changed"):
        register_point(root, application_id="other", candidate_id="candidate", release_digest="sha256:release",
                       operation_id="operation", status="completed")


def test_campaign_plan_is_exact_and_apply_deduplicates_pending_execution(service, tmp_path, monkeypatch):
    from adaos.services import release_validation
    from adaos.services.node_maintenance.validation import plan_campaign, apply_campaign
    registry = release_validation.ReleaseValidationService(state_path=tmp_path / "validation.json")
    registry.register_node(release_validation.TestNode(node_id="test", display_name="Test",
        host="127.0.0.1", identity_file=str(tmp_path / "key")))
    registry.register_suite(release_validation.TestSuite(suite_id="observe", version="1", display_name="Observe"))
    monkeypatch.setattr(release_validation, "get_release_validation_service", lambda: registry)
    proposed = plan_campaign(service, suite_id="observe", node_id="test", target_build="abc123")
    created = apply_campaign(service, proposed["plan_digest"])
    assert created["state"] == "pending"
    replayed = apply_campaign(service, proposed["plan_digest"])
    assert replayed["replayed"] is True
    assert created["campaign_id"] == replayed["campaign_id"]
    assert len(registry.snapshot()["campaigns"]) == 1
    stale = plan_campaign(service, suite_id="observe", node_id="test", target_build="abc123")
    registry.register_suite(release_validation.TestSuite(suite_id="observe", version="2", display_name="Observe"))
    with pytest.raises(ValueError, match="changed"):
        apply_campaign(service, stale["plan_digest"])


def test_testing_index_uses_node_authority_and_does_not_expose_private_requests(service, monkeypatch):
    from adaos.services.node_maintenance.validation import record_run
    from adaos.services import release_validation
    # A Trial worker's process-global registry must not replace the node owner.
    monkeypatch.setattr(release_validation, "get_release_validation_service", lambda: pytest.fail("foreign registry"))
    record_run(service.locations.base / "state", service.locations.workspace / "e2e/artifacts/run",
        kind="builder", status="passed", manifest={"run_id":"run", "suite":{"suite_id":"suite"},
            "environment":{"secret":"private-value"}, "started_at":"now"})
    snapshot = service.snapshot(section="testing")
    assert snapshot["e2e_runs"][0]["status"] == "passed"
    assert "private-value" not in json.dumps(snapshot)
    assert snapshot["e2e_runs"][0]["reusable"] is False


@pytest.mark.parametrize('role,allowed', [('owner',True),('member',False),('child',False),('guest',False)])
def test_sdk_requires_application_settings_capability_before_reading_node(service, monkeypatch, role, allowed):
    from types import SimpleNamespace
    from adaos.sdk import access, maintenance
    from adaos.domain.application_access import ApplicationRoleDeclaration
    grants = ('desktop.settings.write', 'workspace.read') if allowed else ('desktop.open', 'workspace.read')
    declaration = ApplicationRoleDeclaration(role_id=role, title=role, grants=grants)
    monkeypatch.setattr(access, 'application', lambda: {'application_id':'management'})
    monkeypatch.setattr(access, 'require', lambda _: None)
    monkeypatch.setattr(access, '_application_grant', lambda: ({}, SimpleNamespace(
        application_roles=[role], grant_id='grant'), SimpleNamespace(application_roles=[declaration])))
    calls = []
    monkeypatch.setattr(maintenance, 'from_context', lambda: calls.append('read') or service)
    if allowed:
        assert maintenance.snapshot()['node_id'] == 'test-node'
        assert calls == ['read']
    else:
        with pytest.raises(PermissionError):
            maintenance.snapshot()
        assert not calls
