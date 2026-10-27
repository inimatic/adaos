import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services import infrastructure_observation as observation


def sample():
    return {'schema': observation.SCHEMA, 'target_id': 'eu', 'observed_at': 1000,
            'checks': [{'id': 'disk', 'state': 'healthy', 'reason': 'measured'}],
            'resources': {'disk_free_bytes': 100, 'ROOT_TOKEN': 'secret'},
            'delivery': {'source_revision': 'a' * 40, 'token': 'secret'},
            'backups': [{'scope': 'root.eu', 'state': 'not_configured', 'copies': [], 'private_key': 'secret'}],
            'services': [], 'secret': 'secret'}


def test_projection_removes_unrecognized_fields_and_secrets():
    projected = observation.project_observation(json.dumps(sample()).encode(), 'eu', now=1001)
    assert projected['fresh']
    assert projected['age_seconds'] == 1
    assert 'secret' not in json.dumps(projected)
    assert projected['backups'][0]['restore_state'] == 'not_observed'


@pytest.mark.parametrize('change', [
    {'target_id': 'ru'}, {'schema': 'wrong'}, {'observed_at': 600},
    {'observed_at': 1100}, {'observed_at': float('nan')}, {'observed_at': True},
    {'checks': []}, {'checks': 'wrong'}, {'backups': [{'scope': 'other', 'state': 'healthy'}]},
])
def test_invalid_or_stale_observation_fails_closed(change):
    value = {**sample(), **change}
    with pytest.raises(observation.InfrastructureObservationError):
        observation.project_observation(json.dumps(value).encode(), 'eu', now=1000)


def test_payload_budget():
    with pytest.raises(observation.InfrastructureObservationError):
        observation.project_observation(b' ' * 65537, 'eu')


def test_no_arbitrary_host_or_key_arguments(tmp_path):
    with pytest.raises(observation.InfrastructureObservationError):
        observation._read_remote({'host': 'example.com;command'}, tmp_path)


def test_provider_absent_does_not_fall_back_to_root_credentials(tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path),
                          skill_ctx=SimpleNamespace(get=lambda: SimpleNamespace(name='infra_ops_skill')))
    with pytest.raises(observation.InfrastructureObservationError, match='provider_not_configured'):
        observation.read_inventory(ctx)


def test_private_admission_rechecked_before_cached_data(tmp_path):
    path = tmp_path / 'private_recovery/infrastructure-observer/observer.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'schema': 'adaos.infrastructure.observer-admission.v1', 'allowed_skills': []}))
    observation._CACHE[str(tmp_path)] = (1000, {'secret': 'should not be returned'})
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path),
                          skill_ctx=SimpleNamespace(get=lambda: SimpleNamespace(name='other_skill')))
    with pytest.raises(PermissionError):
        observation.read_inventory(ctx)


def test_configured_targets_only_and_errors_are_redacted(tmp_path, monkeypatch):
    path = tmp_path / 'private_recovery/infrastructure-observer/observer.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'schema': 'adaos.infrastructure.observer-admission.v1',
        'allowed_skills': ['infra_ops_skill'], 'targets': {key: {} for key in observation.TARGETS}}))
    def fail(*args):
        raise RuntimeError('sensitive data from transport must never escape')
    monkeypatch.setattr(observation, '_read_remote', fail)
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path),
                          skill_ctx=SimpleNamespace(get=lambda: SimpleNamespace(name='infra_ops_skill')))
    value = observation.read_inventory(ctx, use_cache=False)
    assert len(value['targets']) == 3
    assert all(row['state'] == 'unknown' for row in value['targets'])
    assert 'sensitive' not in json.dumps(value)
