import hashlib
import json
from pathlib import Path

import pytest

from adaos.services.applications.recovery_path_binding import RecoveryPathBinding
from adaos.services.applications.recovery_storage import ApplicationRecoveryStorage
from adaos.services.node_maintenance.migration import _link
from adaos.services.node_maintenance.storage import volume


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def moved(tmp_path):
    root = tmp_path.resolve()
    private, physical = root / 'private', root / 'confirmed'
    physical.mkdir()
    logical = private / 'recovery/applications'
    logical.parent.mkdir(parents=True)
    _link(logical, physical)
    storage = ApplicationRecoveryStorage(private)
    identity = ['app', 'candidate', 'sha256:release']
    point = physical / hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    write(point / '.adaos-recovery.json', dict(schema='adaos.recovery.point.v1',
          adapter='applications.lifecycle', **dict(zip(('application_id', 'candidate_id', 'release_digest'), identity))))
    (point / 'payload.bin').write_bytes(b'unchanged recovery payload')
    job_id = 'a' * 64
    job = {'role': 'application_recovery', 'status': 'completed', 'node_id': 'node',
           'source': str(logical), 'destination': str(root / 'previous-location'),
           'volume_id': volume(physical)['id'], 'content_digest': 'sha256:verified-before-move',
           'bytes': 10, 'copied_bytes': 10, 'verified_bytes': 10,
           'recovery_verification': {'adapter_version': 1}}
    job_path = storage.state / 'migrations' / job_id / 'job.json'
    write(job_path, job)
    write(physical / '.adaos-migration-owner.json', {'job_id': job_id, 'admission_root': str(logical)})
    settings = {'node_id': 'node', 'revision': 7, 'roles': {'application_recovery': {
        'active_path': str(physical), 'compatibility_path': str(logical), 'active_volume_id': job['volume_id']}}}
    write(storage.state / 'storage-settings.json', settings)
    return storage, physical, point, job_path, settings


def plan(moved):
    storage, physical, *_ = moved
    return RecoveryPathBinding(storage.private).plan(confirmed_path=str(physical), expected_revision=7,
        actor_ref='user:local', reason='Owner confirmed intentional drive/path change')


def test_reviewed_binding_restores_stable_paths_without_rewriting_history_or_payloads(moved):
    storage, physical, point, job_path, _ = moved
    historical = job_path.read_bytes()
    with pytest.raises(ValueError, match='committed identity'):
        with storage.access():
            pytest.fail('Unreviewed moved root admitted a writer')
    review = plan(moved)
    assert review['moves_data'] is False and review['rewrites_migration'] is False
    assert review['point_inventory']['registered_points'] == 1
    assert job_path.read_bytes() == historical
    binding = RecoveryPathBinding(storage.private)
    result = binding.apply(review['plan_digest'])
    assert result['previous_path'] != result['confirmed_path'] == str(physical)
    with ApplicationRecoveryStorage(storage.private).access():
        assert storage.path(point / 'payload.bin') == storage.logical_root / point.name / 'payload.bin'
    assert (point / 'payload.bin').read_bytes() == b'unchanged recovery payload'
    assert job_path.read_bytes() == historical
    assert binding.apply(review['plan_digest'])['replayed'] is True
    assert json.loads((storage.state / 'storage-settings.json').read_text())['revision'] == 8


@pytest.mark.parametrize('change', ['volume', 'node', 'bytes', 'adapter', 'marker', 'active_copy'])
def test_rebinding_cannot_bypass_other_placement_guards(moved, change):
    storage, physical, _, job_path, settings = moved
    job = json.loads(job_path.read_text())
    if change == 'volume': job['volume_id'] = 'different-volume'
    elif change == 'node': job['node_id'] = 'different-node'
    elif change == 'bytes': job['verified_bytes'] = 9
    elif change == 'adapter': job['recovery_verification']['adapter_version'] = 0
    elif change == 'marker': write(physical / '.adaos-migration-owner.json', {'job_id': 'b' * 64})
    elif change == 'active_copy': Path(job['destination']).mkdir()
    write(job_path, job)
    with pytest.raises(ValueError): plan(moved)
    assert json.loads((storage.state / 'storage-settings.json').read_text()) == settings


@pytest.mark.parametrize('change', ['settings', 'point', 'migration', 'gate'])
def test_apply_rejects_changes_after_review(moved, change):
    storage, physical, point, job_path, settings = moved
    review = plan(moved)
    if change == 'settings':
        settings['revision'] += 1
        write(storage.state / 'storage-settings.json', settings)
    elif change == 'point':
        write(point / '.adaos-recovery.json', {'schema': 'changed'})
    elif change == 'migration':
        job = json.loads(job_path.read_text()); job['completed_at'] = 'changed'; write(job_path, job)
    elif change == 'gate':
        write(storage._gate()[0], {'status': 'running'})
    with pytest.raises(ValueError): RecoveryPathBinding(storage.private).apply(review['plan_digest'])
    assert 'path_binding_digest' not in json.loads((storage.state / 'storage-settings.json').read_text())['roles']['application_recovery']


def test_runtime_checks_receipt_integrity_and_physical_root_identity(moved):
    storage, physical, _, _, _ = moved
    binding = RecoveryPathBinding(storage.private)
    result = binding.apply(plan(moved)['plan_digest'])
    receipt = binding.root / (result['binding_digest'] + '.json')
    original = receipt.read_bytes()
    receipt.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='committed identity'): storage._placement()
    receipt.write_bytes(original)
    replacement = physical.with_name('retained-original')
    assert replacement.resolve().is_relative_to(physical.parent.resolve())
    physical.rename(replacement)
    physical.mkdir()
    (physical / '.adaos-migration-owner.json').write_bytes((replacement / '.adaos-migration-owner.json').read_bytes())
    with pytest.raises(ValueError, match='committed identity'): storage._placement()


def test_binding_drains_reader_admission_and_rejects_unknown_plan(moved):
    storage, *_ = moved
    binding = RecoveryPathBinding(storage.private)
    review = plan(moved)
    with pytest.raises(ValueError): binding.apply('../wrong')
    with pytest.raises(ValueError): binding.apply('0' * 64)
    connection = storage._connection()
    try:
        connection.execute('BEGIN'); connection.execute('SELECT id FROM access').fetchone()
        with pytest.raises(ValueError, match='in use'): binding.apply(review['plan_digest'])
    finally:
        connection.close()
