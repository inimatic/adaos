import json
import hashlib
from types import SimpleNamespace

import pytest

from adaos.services.builder.prototype_history import PrototypeHistoryService


def history_service(tmp_path, *, current='008'):
    source = tmp_path / 'scenarios' / 'example'
    revisions = source / 'ui_revisions'
    revisions.mkdir(parents=True)
    for number in range(1, 9):
        revision = f'{number:03d}'
        (revisions / f'{revision}.json').write_text(json.dumps({
            'schema': 'adaos.builder.ui_revision.v1', 'scenario_id': 'example',
            'revision': revision, 'created_at': '2026-10-23T00:00:00Z',
            'after_webui': {'ui': {'version': '0.1.0', 'title': revision}},
            'prompt_files': {'private': 'not archived'},
        }), encoding='utf-8')
    workflow = SimpleNamespace(state_dir=tmp_path / 'state',
        project_root=lambda kind, identity: source,
        lifecycle_state=lambda *args: {'prototype': {'head_revision': current}, 'history': []},
        _project_presentation=lambda *args: {'scenario_id': 'example'})
    return PrototypeHistoryService(workflow), source


def test_history_paginates_all_revisions_and_keeps_old_current_explicit(tmp_path):
    service, _ = history_service(tmp_path, current='001')
    first = service.list('project', 'example', limit=5)
    second = service.list('project', 'example', limit=5, cursor=first['next_cursor'])
    assert [r['revision'] for r in first['items'] + second['items']] == [f'{n:03d}' for n in range(8, 0, -1)]
    assert first['current']['revision'] == '001'
    assert first['current']['current'] is True
    assert first['total'] == 8 and first['has_more']
    assert not second['has_more'] and second['next_cursor'] is None
    assert not service.workflow.state_dir.exists()  # A read never creates a store.


def test_project_history_uses_presentation_workflow_not_catalog_release_state(tmp_path):
    service, _ = history_service(tmp_path)
    service.workflow.lifecycle_state = lambda kind, identity: (
        {'prototype': {'head_revision': '008'}} if kind == 'scenario' and identity == 'example' else {})
    assert service.list('project', 'example')['current_revision'] == '008'


def test_retained_history_survives_loss_of_disposable_revision_files(tmp_path):
    service, source = history_service(tmp_path)
    retained = service.retain('scenario', 'example', '008')
    assert service.retain('project', 'example', '008') == retained
    (source / 'ui_revisions' / '008.json').unlink()
    item = service.list('scenario', 'example')['current']
    assert item['availability'] == 'retained' and item['snapshot_available']
    assert item['source_available'] is False  # Preview is not a DEV restore/current switch.
    assert service.read('scenario', 'example', '008')['after_webui']['ui']['title'] == '008'
    archived = next(service.workflow.state_dir.rglob('snapshots/008.json'))
    assert 'prompt_files' not in json.loads(archived.read_text(encoding='utf-8'))


def test_missing_revision_is_not_substituted_with_a_release(tmp_path):
    service, source = history_service(tmp_path)
    (source / 'ui_revisions' / '008.json').unlink()
    current = service.list('scenario', 'example')['current']
    assert current['revision'] == '008'
    assert current['availability'] == 'missing'
    assert not current['snapshot_available']
    assert service.retain('scenario', 'example', '008')['status'] == 'missing'


def test_retention_rejects_redefinition_and_ignores_mutable_vcs_receipts(tmp_path):
    service, source = history_service(tmp_path)
    service.retain('scenario', 'example', '008')
    file = source / 'ui_revisions' / '008.json'
    value = json.loads(file.read_text(encoding='utf-8'))
    value['vcs_checkpoint'] = {'status': 'later'}
    file.write_text(json.dumps(value), encoding='utf-8')
    service.retain('scenario', 'example', '008')
    value['after_webui']['ui']['title'] = 'different'
    file.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='different content'):
        service.retain('scenario', 'example', '008')


@pytest.mark.parametrize('revision', ['../../008', '008.json', '0.1.29', '-1'])
def test_revision_identity_is_bounded(tmp_path, revision):
    service, _ = history_service(tmp_path)
    with pytest.raises(ValueError):
        service.retain('scenario', 'example', revision)
    with pytest.raises(ValueError):
        service.list('scenario', 'example', cursor=revision)


def test_history_does_not_walk_the_entire_project(tmp_path, monkeypatch):
    service, source = history_service(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('History must not use the generic recursive project file catalog')
    monkeypatch.setattr(type(source), 'rglob', forbidden)
    assert service.list('scenario', 'example')['total'] == 8


def test_history_uses_durable_run_receipts_after_bounded_workflow_history(tmp_path):
    service, source = history_service(tmp_path)
    for path in (source / 'ui_revisions').glob('00[1-7].json'):
        path.unlink()
    change_id = 'change.archived'
    ledger = (service.workflow.state_dir / 'builder/change_runs/scenario/example'
              / f'{hashlib.sha256(change_id.encode()).hexdigest()}.json')
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({
        'schema': 'adaos.builder.change_runs.v1', 'change_id': change_id,
        'runs': [{
            'activity': 'prototype_revision_recorded', 'status': 'succeeded',
            'output_refs': [f'ui_revision:{number:03d}'],
            'completed_at': f'2026-10-2{number}T00:00:00Z',
        } for number in range(1, 8)],
    }), encoding='utf-8')
    service.workflow.lifecycle_state = lambda *args: {
        'prototype': {'head_revision': '008'}, 'history': []}
    result = service.list('scenario', 'example')
    assert [item['revision'] for item in result['items']] == [
        f'{number:03d}' for number in range(8, 0, -1)]
    assert result['missing_revisions'] == [f'{number:03d}' for number in range(7, 0, -1)]
    assert result['items'][-1]['evidence_count'] == 1
    assert result['items'][-1]['change_ids'] == [change_id]


def test_history_ignores_unproven_or_failed_revision_receipts(tmp_path):
    service, source = history_service(tmp_path)
    for path in (source / 'ui_revisions').glob('00[1-7].json'):
        path.unlink()
    change_id = 'change.failed'
    root = service.workflow.state_dir / 'builder/change_runs/scenario/example'
    root.mkdir(parents=True)
    (root / f'{hashlib.sha256(change_id.encode()).hexdigest()}.json').write_text(json.dumps({
        'schema': 'adaos.builder.change_runs.v1', 'change_id': change_id,
        'runs': [{'activity': 'prototype_revision_recorded', 'status': 'failed',
                  'output_refs': ['ui_revision:007']}],
    }), encoding='utf-8')
    (root / 'wrong-name.json').write_text(json.dumps({
        'schema': 'adaos.builder.change_runs.v1', 'change_id': 'forged',
        'runs': [{'activity': 'prototype_revision_recorded', 'status': 'succeeded',
                  'output_refs': ['ui_revision:006']}],
    }), encoding='utf-8')
    assert [item['revision'] for item in service.list('scenario', 'example')['items']] == ['008']


def test_retained_read_rejects_corrupt_snapshot(tmp_path):
    service, _ = history_service(tmp_path)
    service.retain('scenario', 'example', '008')
    snapshot = next(service.workflow.state_dir.rglob('snapshots/008.json'))
    value = json.loads(snapshot.read_text(encoding='utf-8'))
    value['after_webui']['ui']['title'] = 'tampered'
    snapshot.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='digest mismatch'):
        service.read('scenario', 'example', '008')


@pytest.mark.parametrize('matches', [True, False])
def test_recovery_requires_verified_source_and_existing_acceptance_digest(tmp_path, matches):
    from adaos.services.skill_factory_sources import capture_source_snapshot
    from adaos.services.resources.prototype import prototype_webui_digest
    service, source = history_service(tmp_path)
    webui = {'ui': {'title': 'accepted'}}
    (source / 'webui.json').write_text(json.dumps(webui), encoding='utf-8')
    manifest = capture_source_snapshot(state_dir=service.workflow.state_dir,
        artifacts=[('scenario', 'example', source)], created_at='2026-10-23T00:00:00Z')
    acceptance = {'revision': '008', 'decision': 'accepted', 'acceptance_id': 'acceptance.exact',
                  'webui_digest': prototype_webui_digest(webui) if matches else 'sha256:wrong'}
    state = {'prototype': {'head_revision': '008', 'acceptance': acceptance}}
    service.workflow.lifecycle_state = lambda *args: state
    before = json.dumps(state)
    (source / 'ui_revisions' / '008.json').unlink()
    if matches:
        recovered = service.recover_accepted('scenario', 'example', manifest)
        assert recovered['recovery']['acceptance_id'] == 'acceptance.exact'
        assert service.read('scenario', 'example', '008')['after_webui'] == webui
        assert not (source / 'ui_revisions' / '008.json').exists()
    else:
        with pytest.raises(ValueError, match='does not match accepted'):
            service.recover_accepted('scenario', 'example', manifest)
    assert json.dumps(state) == before


def test_historical_recovery_requires_digest_addressed_acceptance_evidence(tmp_path):
    from adaos.services.skill_factory_sources import capture_source_snapshot
    from adaos.services.resources.prototype import prototype_webui_digest
    service, source = history_service(tmp_path)
    webui = {'ui': {'title': 'historically accepted'}}
    (source / 'webui.json').write_text(json.dumps(webui), encoding='utf-8')
    (source / 'ui_revisions' / '007.json').unlink()
    manifest = capture_source_snapshot(
        state_dir=service.workflow.state_dir,
        artifacts=[('scenario', 'example', source)],
        created_at='2026-10-23T00:00:00Z')
    acceptance = {
        'decision': 'accepted', 'revision': '007',
        'acceptance_id': 'acceptance.historical',
        'webui_digest': prototype_webui_digest(webui),
        'accepted_at': '2026-10-22T00:00:00Z',
    }
    packet = {
        'schema': 'adaos.builder.context_packet.v1',
        'artifacts': {'prototype': {'acceptance': acceptance}},
    }
    raw = json.dumps(packet, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    packet['digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
    packet['built_at'] = '2026-10-23T00:00:00Z'
    root = service.workflow.state_dir / 'builder/context_packets/scenario/example'
    root.mkdir(parents=True)
    (root / f"{packet['digest'][7:]}.json").write_text(
        json.dumps(packet), encoding='utf-8')
    recovered = service.recover_evidenced_acceptance(
        'scenario', 'example', manifest, 'acceptance.historical')
    assert recovered['revision'] == '007'
    assert recovered['recovery']['acceptance_id'] == 'acceptance.historical'
    assert service.read('scenario', 'example', '007')['after_webui'] == webui
    assert not (source / 'ui_revisions' / '007.json').exists()
