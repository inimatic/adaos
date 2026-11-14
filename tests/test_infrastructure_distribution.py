import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from adaos.services import infrastructure_distribution as service

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'src/adaos/integrations/infra-inimatic/scripts'


@pytest.fixture
def admitted(tmp_path):
    state = tmp_path / 'state'
    path = state / 'private_recovery/bootstrap-delivery/provider.json'
    path.parent.mkdir(parents=True)
    config = tmp_path / 'controller.json'
    config.write_text(json.dumps({'schema': 'inimatic.bootstrap_controller.v1',
        'journal': str(tmp_path / 'journal.db'), 'channel': 'beta', 'zone_id': 'ru', 'source_revision': 'a' * 40,
        'artifacts': {}, 'qualifications': {}, 'allowed_publishers': ['user:operator']}))
    names = ('bootstrap-delivery-controller.py', 'bootstrap-release-regru.py', 'public-site-regru.py', 'infra-bootstrap.py')
    scripts = tmp_path / 'controller'
    scripts.mkdir()
    for name in names:
        (scripts / name).write_text('import json\nprint(json.dumps({"ok": True, "roots": []}))\n')
    provider = {'schema': 'adaos.infrastructure.distribution-admission.v1', 'allowed_skills': ['infra_ops_skill'],
        'allowed_publishers': ['user:operator'], 'python': str(Path(sys.executable).resolve()),
        'controller': str(scripts / names[0]), 'config_file': str(config),
        'controller_files': {name: hashlib.sha256((scripts / name).read_bytes()).hexdigest() for name in names}}
    path.write_text(json.dumps(provider))
    ctx = SimpleNamespace(authority_state_dir=state, paths=SimpleNamespace(state_dir=lambda: tmp_path / 'trial'),
                          skill_ctx=SimpleNamespace(get=lambda: SimpleNamespace(name='infra_ops_skill')))
    service._CACHE.clear()
    return ctx, path, provider


def test_missing_provider_does_not_fall_back_to_root_or_s3_credentials(tmp_path):
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))
    with pytest.raises(service.InfrastructureDistributionError, match='not_configured'):
        service.distribution_snapshot(ctx)


def test_real_controller_projection_lists_whole_planned_set_without_private_paths(admitted):
    ctx, path, provider = admitted
    if not (SCRIPTS / 'bootstrap-delivery-controller.py').is_file():
        pytest.skip('Cross-repository controller source is not checked out')
    provider['controller'] = str(SCRIPTS / 'bootstrap-delivery-controller.py')
    provider['controller_files'] = {name: hashlib.sha256((SCRIPTS / name).read_bytes()).hexdigest()
                                  for name in provider['controller_files']}
    path.write_text(json.dumps(provider))
    snapshot = service.distribution_snapshot(ctx)
    assert snapshot['ok']
    assert len(snapshot['plan']['items']) == 3
    assert not snapshot['plan']['ready']
    assert not snapshot['activation_enabled']
    assert str(path.parent) not in json.dumps(snapshot)
    assert 'credentials_file' not in json.dumps(snapshot)
    assert not ctx.paths.state_dir().exists()


def test_admission_revocation_is_checked_even_when_snapshot_is_cached(admitted):
    ctx, path, provider = admitted
    service.distribution_snapshot(ctx)
    path.write_text(json.dumps({**provider, 'allowed_skills': []}))
    with pytest.raises(PermissionError):
        service.distribution_snapshot(ctx)


def test_changed_controller_dependency_is_rejected_before_execution(admitted):
    ctx, path, provider = admitted
    provider['controller_files']['public-site-regru.py'] = '0' * 64
    path.write_text(json.dumps(provider))
    with pytest.raises(service.InfrastructureDistributionError, match='changed'):
        service.distribution_snapshot(ctx)


def test_projection_rejects_unbounded_collections_and_removes_root_secrets():
    value = service._project({'roots': [{'zone_id': 'ru', 'token': 'PRIVATE',
        'catalogue': {'releases': [{'release_id': 'r', 'secret': 'PRIVATE'}]}}], 'credentials_file': 'PRIVATE'})
    assert 'PRIVATE' not in json.dumps(value)
    with pytest.raises(service.InfrastructureDistributionError):
        service._project({'roots': [None] * 101})


def test_submit_requires_verified_caller_and_invocation(admitted, monkeypatch):
    from adaos.sdk import access
    ctx, _, _ = admitted
    monkeypatch.setattr(access, 'require', lambda permission: None)
    monkeypatch.setattr(access, 'caller', lambda: {'kind': 'user', 'id': 'operator'})
    monkeypatch.setattr(access, 'invocation', lambda: None)
    with pytest.raises(PermissionError, match='invocation'):
        service.publish_distribution(ctx, 'a' * 64)


def test_writer_outside_private_publisher_admission_cannot_dispatch(admitted, monkeypatch):
    from adaos.sdk import access
    ctx, _, _ = admitted
    monkeypatch.setattr(access, 'require', lambda permission: None)
    monkeypatch.setattr(access, 'caller', lambda: {'kind': 'user', 'id': 'reader'})
    monkeypatch.setattr(access, 'invocation', lambda: {'request_id': 'r'})
    monkeypatch.setattr(service, '_invoke', lambda *args: pytest.fail('must not execute controller'))
    with pytest.raises(PermissionError, match='not_admitted'):
        service.publish_distribution(ctx, 'a' * 64)


def test_publish_uses_core_request_identity_and_never_accepts_commands_or_actor_arguments(admitted, monkeypatch):
    from adaos.sdk import access
    ctx, _, _ = admitted
    monkeypatch.setattr(access, 'require', lambda permission: None)
    monkeypatch.setattr(access, 'caller', lambda: {'kind': 'user', 'id': 'operator'})
    monkeypatch.setattr(access, 'invocation', lambda: {'request_id': 'same-transport-retry'})
    calls = []
    monkeypatch.setattr(service, '_invoke', lambda state, config, action, payload: calls.append((action, payload)) or {'ok': True})
    service.publish_distribution(ctx, 'b' * 64)
    service.publish_distribution(ctx, 'b' * 64)
    assert calls[0] == calls[1]
    assert calls[0][1]['actor'] == 'user:operator'
    assert calls[0][1]['request_id'] == hashlib.sha256(b'same-transport-retry').hexdigest()


def test_cached_snapshot_does_not_cache_publisher_permissions(admitted, monkeypatch):
    from adaos.sdk import access
    ctx, _, _ = admitted
    monkeypatch.setattr(access, 'caller', lambda: {'kind': 'user', 'id': 'operator'})
    monkeypatch.setattr(access, 'require', lambda permission: None)
    assert service.distribution_snapshot(ctx)['can_publish']
    def revoked(permission):
        raise PermissionError('revoked')
    monkeypatch.setattr(access, 'require', revoked)
    assert service.distribution_snapshot(ctx)['can_publish'] is False
