from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

from adaos.services.applications.blob_data_transition import BlobDataTransition
from adaos.services.applications.data_lifecycle import _SkillMemoryTransition, _StateFileTransition, StateFileContract
from adaos.services.applications.recovery_storage import ApplicationRecoveryStorage
from adaos.services.applications.sqlite_data_transition import SQLiteDataTransition
from adaos.services.node_maintenance import NodeMaintenanceService
from adaos.services.node_maintenance.migration import _link
from adaos.services.node_maintenance.recovery import register_point


@pytest.fixture
def recovery(tmp_path):
    private = tmp_path / 'private'
    source = private / 'recovery/applications'
    identity = ['sample', 'candidate', 'sha256:' + 'a' * 64]
    point = source / hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    point.mkdir(parents=True)
    register_point(point, application_id=identity[0], candidate_id=identity[1], release_digest=identity[2],
                   operation_id='test-only-native-lifecycle', status='completed')
    service = NodeMaintenanceService(workspace=tmp_path / 'repo', base=private, node_id='test-node')
    target = tmp_path / 'external/recovery'
    plan = service.locations.plan(role='application_recovery', desired_path=str(target), expected_revision=0,
                                  min_free_bytes=8 * 1024**3)
    service.locations.apply(plan['plan_digest'])
    return service, point, target


def review(recovery):
    service, _, _ = recovery
    service.advance_inventory('application_recovery')
    plan = service.migrations.plan('application_recovery', 1)
    assert plan['admitted'], plan['reasons']
    return plan


def complete(service, job):
    for _ in range(100):
        result = service.migrations.advance(job['job_id'], max_seconds=.05)
        assert result['status'] != 'paused', result.get('error')
        if result['status'] == 'completed':
            return result
    pytest.fail('Recovery relocation did not complete its bounded checkpoints')


def sql(path, query):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        result = connection.execute(query).fetchall()
        connection.commit()
        return result


def test_all_native_data_adapters_restore_and_replay_after_relocation(recovery):
    service, point, target = recovery
    private = service.locations.base
    data = private / 'workspace/data'
    sql(data / 'records.sqlite', 'CREATE TABLE records(value TEXT)')
    sql(data / 'records.sqlite', "INSERT INTO records VALUES('synthetic record')")
    database = SQLiteDataTransition(private)
    db_receipt = database.snapshot(data / 'records.sqlite', point / 'records.sqlite', operation_key='db')
    data.mkdir(parents=True, exist_ok=True)
    memory = _SkillMemoryTransition(private)
    (data / 'skill_env.json').write_text('{"synthetic":true}', encoding='utf-8')
    memory_receipt = memory.snapshot(data / 'skill_env.json', point / 'memory.json', operation_key='memory')
    state = _StateFileTransition(private)
    contract = StateFileContract('state.json', 'json', 1024)
    (data / 'state.json').write_text('{"counter":7}', encoding='utf-8')
    state_receipt = state.snapshot(data / 'state.json', point / 'state.json', contract=contract, operation_key='state')
    blobs = BlobDataTransition(private)
    payload = b'synthetic blob'
    digest = hashlib.sha256(payload).hexdigest()
    blob = data / 'files/photos/objects' / digest[:2] / (digest + '.bin')
    blob.parent.mkdir(parents=True)
    blob.write_bytes(payload)
    blob_receipt = blobs.snapshot(data / 'files', point / 'files', operation_key='blobs')

    job = complete(service, service.migrations.start(review(recovery)['plan_digest']))
    assert job['recovery_verification']['registered_points'] == 1
    assert not job['source_space_reclaimed']
    assert (Path(job['retained_source']) / point.name / 'records.sqlite').exists()
    destination = private / 'workspace/restored'
    database.install(point / 'records.sqlite', destination / 'records.sqlite', staged_digest=db_receipt['digest'])
    assert sql(destination / 'records.sqlite', 'SELECT value FROM records') == [('synthetic record',)]
    memory.install(point / 'memory.json', destination / 'skill_env.json', staged_digest=memory_receipt['digest'])
    assert json.loads((destination / 'skill_env.json').read_text()) == {'synthetic': True}
    state.install(point / 'state.json', destination / 'state.json', contract=contract, staged_digest=state_receipt['digest'])
    assert json.loads((destination / 'state.json').read_text()) == {'counter': 7}
    blobs.install(point / 'files', destination / 'files', staged_digest=blob_receipt['digest'])
    assert blobs.inventory(destination / 'files')['digest'] == blob_receipt['digest']
    # Old receipt identities replay unchanged, even when a caller supplies W's
    # physical path instead of its historical compatibility path.
    assert database.snapshot(data / 'records.sqlite', target / point.name / 'records.sqlite', operation_key='db') == db_receipt
    assert memory.snapshot(data / 'skill_env.json', point / 'memory.json', operation_key='memory') == memory_receipt
    assert state.snapshot(data / 'state.json', point / 'state.json', contract=contract, operation_key='state') == state_receipt
    assert blobs.snapshot(data / 'files', point / 'files', operation_key='blobs') == blob_receipt
    database.snapshot(data / 'records.sqlite', point / 'after.sqlite', operation_key='new-after-move')
    assert (target / point.name / 'after.sqlite').is_file()
    assert not (Path(job['retained_source']) / point.name / 'after.sqlite').exists()


def test_active_recovery_readers_drain_before_migration_and_future_writes_are_fenced(recovery):
    service, point, _ = recovery
    storage = ApplicationRecoveryStorage(service.locations.base)
    entered, release = threading.Event(), threading.Event()
    def reader():
        with storage.access():
            entered.set()
            assert release.wait(10)
    thread = threading.Thread(target=reader)
    thread.start()
    try:
        assert entered.wait(5)
        plan = review(recovery)
        with pytest.raises(ValueError, match='in use'):
            service.migrations.start(plan['plan_digest'])
        assert not service.migrations.snapshot()
    finally:
        release.set()
        thread.join(timeout=5)
    job = service.migrations.start(plan['plan_digest'])
    with pytest.raises(ValueError, match='relocation is in progress'):
        _SkillMemoryTransition(service.locations.base).snapshot(point / 'x', point / 'y', operation_key='blocked')
    service.migrations.cancel(job['job_id'])
    with storage.access():
        assert storage.path(point / 'x') == point / 'x'


def test_recovery_access_is_concurrent_and_nested(recovery):
    service, _, _ = recovery
    storage = ApplicationRecoveryStorage(service.locations.base)
    observed = threading.Event()
    with storage.access(), storage.access():
        def other():
            with storage.access():
                observed.set()
        thread = threading.Thread(target=other)
        thread.start()
        assert observed.wait(3), 'Independent application lifecycle readers were serialized'
        thread.join(timeout=3)


@pytest.mark.parametrize('old_adapter', ['writer', 'coordinator'])
def test_partially_updated_running_core_never_admits_recovery_relocation(recovery, monkeypatch, old_adapter):
    from adaos.services.applications.lifecycle_operations import ApplicationLifecycleOperations
    service, point, target = recovery
    qualified = review(recovery)
    if old_adapter == 'writer':
        monkeypatch.setattr(SQLiteDataTransition, 'snapshot', SQLiteDataTransition.snapshot.__wrapped__)
    else:
        monkeypatch.delattr(ApplicationLifecycleOperations, 'RECOVERY_ADMISSION_VERSION')
    blocked = service.migrations.plan('application_recovery', 1)
    assert not blocked['admitted']
    assert any('running Core has not loaded' in reason for reason in blocked['reasons'])
    with pytest.raises(ValueError, match='adapters changed'):
        service.migrations.start(qualified['plan_digest'])
    assert not target.exists() and point.exists()
    assert not service.migrations.snapshot()


def test_recovery_lease_also_blocks_admission_from_another_process(recovery):
    service, _, _ = recovery
    storage = ApplicationRecoveryStorage(service.locations.base)
    code = '''import sys
from pathlib import Path
from adaos.services.applications.recovery_storage import ApplicationRecoveryStorage
try:
    with ApplicationRecoveryStorage(Path(sys.argv[1])).exclusive():
        raise SystemExit('A different process bypassed the recovery reader lease')
except ValueError as exc:
    assert 'in use' in str(exc)
'''
    with storage.access():
        result = subprocess.run([sys.executable, '-c', code, str(service.locations.base)],
            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_recovery_admission_rejects_wal_reader_semantics(recovery):
    service, _, _ = recovery
    storage = ApplicationRecoveryStorage(service.locations.base)
    with storage.access():
        pass
    with closing(sqlite3.connect(storage.state / 'application-recovery-access.sqlite3')) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
    with pytest.raises(ValueError, match='rollback-journal'):
        with storage.access():
            pytest.fail('WAL must not weaken the cross-process drain proof')


def test_cutover_acknowledgement_failure_keeps_recovery_closed_until_resumed(recovery, monkeypatch):
    service, point, target = recovery
    job = service.migrations.start(review(recovery)['plan_digest'])
    save = service.migrations._save
    def interrupted(record):
        if record['status'] == 'completed':
            raise OSError('receipt acknowledgement interrupted')
        save(record)
    monkeypatch.setattr(service.migrations, '_save', interrupted)
    with pytest.raises(OSError, match='receipt acknowledgement'):
        complete(service, job)
    storage = ApplicationRecoveryStorage(service.locations.base)
    with pytest.raises(ValueError, match='committed identity'):
        with storage.access():
            pytest.fail('An unacknowledged relocation was admitted')
    monkeypatch.setattr(service.migrations, '_save', save)
    complete(service, job)
    with storage.access():
        assert storage.path(target / point.name / 'file') == point / 'file'


@pytest.mark.parametrize('drift', ['volume', 'marker', 'nested_link', 'retained_original'])
def test_only_committed_recovery_placement_is_admitted(recovery, drift):
    service, point, target = recovery
    job = complete(service, service.migrations.start(review(recovery)['plan_digest']))
    storage = ApplicationRecoveryStorage(service.locations.base)
    assert storage.path(target / point.name / 'file') == point / 'file'
    probe = point / 'file'
    if drift == 'volume':
        settings = service.locations.settings()
        settings['roles']['application_recovery']['active_volume_id'] = 'replaced-volume'
        service.locations.settings_path.write_text(json.dumps(settings), encoding='utf-8')
    elif drift == 'marker':
        (target / '.adaos-migration-owner.json').write_text('{"job_id":"unknown"}', encoding='utf-8')
    elif drift == 'nested_link':
        outside = service.locations.workspace
        outside.mkdir(parents=True)
        _link(target / point.name / 'nested', outside)
        probe = point / 'nested/file'
    else:
        probe = Path(job['retained_source']) / point.name / 'file'
    with pytest.raises(ValueError):
        storage.path(probe)


def test_bad_recovery_identity_pauses_before_cutover(recovery):
    service, point, target = recovery
    metadata = json.loads((point / '.adaos-recovery.json').read_text())
    metadata['candidate_id'] = 'another-candidate'
    (point / '.adaos-recovery.json').write_text(json.dumps(metadata), encoding='utf-8')
    job = service.migrations.start(review(recovery)['plan_digest'])
    for _ in range(100):
        job = service.migrations.advance(job['job_id'], max_seconds=.05)
        if job['status'] == 'paused':
            break
    assert job['phase'] == 'verify_recovery'
    assert 'identity' in job['error']
    assert service.locations.resolve('application_recovery') == point.parent
    assert point.parent.resolve() != target
    service.migrations.cancel(job['job_id'])
    assert point.exists()


def test_legacy_unclassified_points_are_preserved_without_claiming_restore_readiness(recovery):
    service, point, target = recovery
    legacy = point.parent / 'legacy-point'
    legacy.mkdir()
    (legacy / 'original.bin').write_bytes(b'preserve unclassified evidence')
    job = complete(service, service.migrations.start(review(recovery)['plan_digest']))
    assert job['recovery_verification']['unclassified_points_retained'] == 1
    assert (target / 'legacy-point/original.bin').read_bytes() == b'preserve unclassified evidence'
