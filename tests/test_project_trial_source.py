"""Ordinary Trial preparation uses complete retained source, including tests."""
from pathlib import Path
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from adaos.services.artifact_pipeline.project_source_sync import capture_project, SourceSyncError
from adaos.services.artifact_pipeline.project_trial_source import _retained_trial_operation
from adaos.services.artifact_pipeline.source_snapshots import SourceSnapshot
from adaos.services.artifact_pipeline import source_validation
from adaos.services.root.service import RootDeveloperService, RootServiceError
from test_project_source_sync import source


def _retain_accepted(publication, snapshot, *, kind='skill', name='worker', change='automation:accepted'):
    publication.accepted = snapshot
    root = publication.state_root / 'checkpoint-intents' / f'{kind}s' / name
    root.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha256(change.encode()).hexdigest()
    raw = snapshot.archive()
    digest = hashlib.sha256(raw).hexdigest()
    (root / f'{stem}.zip').write_bytes(raw)
    journal = root / f'{stem}.json'
    journal.write_text(json.dumps({'schema': 'adaos.artifact.checkpoint_intent.v1',
        'status': 'completed', 'kind': kind, 'artifact_id': name, 'change_id': change,
        'source_tree': snapshot.tree(), 'archive_sha256': digest,
        'receipt': {'commit': 'a' * 40, 'tree_sha': snapshot.tree(), 'sha256': digest}}), encoding='utf-8')
    return journal


def test_real_environment_identity_survives_persistence_for_restart_reuse():
    identity = source_validation.validation_environment()
    assert json.loads(json.dumps(identity)) == identity


@pytest.fixture
def trial(source, tmp_path, monkeypatch):
    workspace, remote, sync = source
    test_file = workspace / 'skills/worker/tests/test_worker.py'
    test_file.write_text('def test_worker():\n    assert True\n', encoding='utf-8')
    snapshot = capture_project(workspace, 'demo')
    primary = SourceSnapshot(tuple((n[len('skills/worker/'):], b) for n, b in snapshot.entries if n.startswith('skills/worker/')))
    calls = []

    class Publication:
        state_root = tmp_path / 'publication'
        def load_pushed_source(self, kind, name):
            calls.append(('load', kind, name))
            return SimpleNamespace(source_ref=SimpleNamespace(revision='a' * 40),
                source_tree=self.accepted.tree(), change_ids=('automation:accepted',))
        def prepare_project_candidate(self, **kwargs):
            calls.append(('prepare', kwargs))
            assert capture_project(kwargs['source_workspace_root'], 'demo').digest == snapshot.digest
            assert kwargs['source_ref'].path_scope == ('subnets/sn_test/nodes/node_test/projects/demo/source/skills/worker/',)
            assert kwargs['source_ref'].revision == remote.receipts[next(iter(remote.receipts))]['commit']
            assert kwargs['release_source_ref'].forge == 'adaos-root'
            assert kwargs['release_source_ref'].path_scope == ('subnets/sn_test/nodes/node_test/projects/demo/source/',)
            return SimpleNamespace(candidate=SimpleNamespace(to_dict=lambda: {'candidate_id': 'frozen'}),
                plan=SimpleNamespace(release=SimpleNamespace(to_dict=lambda: {'project_id': 'demo'})),
                trial_workspace=tmp_path / 'trial', trial_activation={'status': 'ready'})

    publication = Publication()
    _retain_accepted(publication, primary)
    owner = RootDeveloperService.__new__(RootDeveloperService)
    owner.ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path / 'state'))
    owner.project_source_sync = sync
    owner._load_config = lambda: None
    owner._artifact_publication_service = lambda cfg: publication
    owner._admit_project_candidate_cbs = lambda **kwargs: None
    monkeypatch.setattr(source_validation, 'validation_environment', lambda: {'runtime': 'exact-core'})
    arguments = dict(source_kind='skill', source_name='worker', source_revision='a' * 40,
                     change_ids=('automation:accepted',), idempotency_key='trial-1')
    return owner, arguments, remote, test_file, calls, publication


def test_normal_trial_automatically_retains_restores_and_tests_the_whole_project(trial):
    owner, args, remote, _, calls, _ = trial
    result = owner.prepare_project_candidate('demo', **args)
    assert result['candidate']['candidate_id'] == 'frozen'
    assert result['automation_source_basis']['source_revision'] == 'a' * 40
    assert result['source_checkpoint']['state'] == 'remote_confirmed'
    evidence = calls[-1][1]['validation_evidence']
    assert evidence['source_validation']['status'] == 'passed'
    assert result['source_validation']['reused'] is False
    assert len(evidence['source_validation']['packages']) == 1
    assert remote.writes == 1


def test_normal_trial_recovery_does_not_recapture_new_dev_or_read_new_pushed_index(trial):
    owner, args, remote, live, calls, publication = trial
    def lost_reply():
        live.write_text('def test_worker():\n    assert False\n', encoding='utf-8')
        raise TimeoutError('lost reply after push')
    remote.after_write = lost_reply
    with pytest.raises(TimeoutError):
        owner.prepare_project_candidate('demo', **args)
    publication.load_pushed_source = lambda *a: pytest.fail('read newer mutable checkpoint')
    result = owner.prepare_project_candidate('demo', **args)
    assert result['candidate']['candidate_id'] == 'frozen'
    assert 'assert False' in live.read_text()
    assert remote.writes == 1
    assert len([c for c in calls if c[0] == 'prepare']) == 1


def test_normal_trial_reuses_completed_source_checks_after_restart(trial):
    owner, args, remote, _, calls, _ = trial
    owner.prepare_project_candidate('demo', **args)
    result = owner.prepare_project_candidate('demo', **args)
    assert result['source_validation']['reused'] is True
    inputs = [row[1]['validation_evidence'] for row in calls if row[0] == 'prepare']
    assert inputs[0] == inputs[1]
    assert remote.writes == 1


def test_permission_callback_on_retry_is_not_serialized_into_frozen_source_identity(trial):
    owner,args,remote,_,calls,_ = trial
    owner.prepare_project_candidate('demo',**args)
    callback=lambda plan:{'approved':True,'reason':'retained same-scope approval'}
    result=owner.prepare_project_candidate('demo',**{
        **args,'permission_decision':callback,'idempotency_key':'trial-new-approval',
    })
    assert result['candidate']['candidate_id'] == 'frozen' and remote.writes == 1
    attempts=[row[1] for row in calls if row[0]=='prepare']
    assert attempts[-1]['permission_decision'] is callback
    assert attempts[0]['release_validation_evidence'] == attempts[-1]['release_validation_evidence']


def test_legacy_trial_source_prefers_the_release_already_frozen_for_this_checkpoint(trial):
    owner, args, _, _, _, publication = trial
    sync = owner.project_source_sync()
    scope = sync._scope('demo', 'trial-same-checkpoint')
    basis = {'project_id':'demo', **{k:args[k] for k in ('source_kind','source_name','source_revision')},
             'change_ids':list(args['change_ids'])}
    snapshot_digest = 'sha256:' + '1' * 64
    for sequence, operation in enumerate(('trial-early', 'trial-frozen', 'trial-later'), start=1):
        path, _ = sync._paths(scope, operation)
        path.parent.mkdir(parents=True)
        path.with_name('trial-basis.json').write_text(json.dumps(basis), encoding='utf-8')
        path.write_text(json.dumps({
            'state':'remote_confirmed', 'request':scope, 'sequence':sequence,
            'snapshot_digest':snapshot_digest, 'commit':str(sequence) * 40,
        }), encoding='utf-8')
    frozen = SimpleNamespace(validation_evidence=({'source_custody':{
        'operation_id':'trial-frozen','snapshot_digest':snapshot_digest,'commit':'2' * 40,
    }},))
    publication.release_cache = SimpleNamespace(release_digests_by_version=lambda _: {'0.1.8':'release'},
                                                get_release=lambda *_: SimpleNamespace(release=frozen))
    owner._artifact_publication_service = lambda cfg, **kwargs: publication
    assert _retained_trial_operation(owner, sync, scope, basis) == 'trial-frozen'


def test_normal_trial_rejects_changed_source_before_remote_write(trial):
    owner, args, remote, live, calls, _ = trial
    live.write_text('def test_worker():\n    assert 2 == 2\n', encoding='utf-8')
    with pytest.raises(SourceSyncError, match='changed after Automation'):
        owner.prepare_project_candidate('demo', **args)
    assert remote.writes == 0
    assert not any(c[0] == 'prepare' for c in calls)


def test_normal_trial_compares_the_same_source_boundary_on_both_sides(trial):
    owner, args, remote, live, _, publication = trial
    extras = (('prompt_state.json', b'{"accepted": true}'),
              ('builder.draft.json', b'{"draft": 1}'),
              ('ui_revisions/001.json', b'{"revision": 1}'),
              ('.mutation.lock', b'old lock'))
    _retain_accepted(publication, SourceSnapshot(publication.accepted.entries + extras))
    root = live.parents[1]
    for name, _ in extras:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'new Builder metadata')
    result = owner.prepare_project_candidate('demo', **args)
    assert result['candidate']['candidate_id'] == 'frozen'
    assert remote.writes == 1


@pytest.mark.parametrize('name', ['tests/test_worker.py', 'skill.yaml', 'assets/mask.bin'])
def test_source_normalization_still_rejects_modified_tests_code_and_resources(trial, name):
    owner, args, remote, live, calls, publication = trial
    _retain_accepted(publication, SourceSnapshot(publication.accepted.entries +
                                               (('prompt_state.json', b'old'),)))
    (live.parents[1] / name).write_bytes(b'changed accepted source')
    with pytest.raises(SourceSyncError, match='changed after Automation'):
        owner.prepare_project_candidate('demo', **args)
    assert remote.writes == 0
    assert not any(c[0] == 'prepare' for c in calls)


@pytest.mark.parametrize('damage', ['missing', 'archive', 'archive_tree', 'commit', 'tree', 'identity', 'status'])
def test_normalization_requires_the_bound_immutable_accepted_archive(trial, damage):
    owner, args, remote, _, calls, publication = trial
    journal = _retain_accepted(publication, SourceSnapshot(publication.accepted.entries +
                                                         (('prompt_state.json', b'old'),)))
    if damage == 'missing':
        journal.unlink()
    elif damage == 'archive':
        journal.with_suffix('.zip').write_bytes(b'changed archive')
    else:
        value = json.loads(journal.read_text(encoding='utf-8'))
        if damage == 'archive_tree':
            archive = SourceSnapshot(publication.accepted.entries + (('extra.py', b'changed source'),)).archive()
            journal.with_suffix('.zip').write_bytes(archive)
            value['archive_sha256'] = value['receipt']['sha256'] = hashlib.sha256(archive).hexdigest()
        elif damage == 'commit':
            value['receipt']['commit'] = 'b' * 40
        elif damage == 'tree':
            value['receipt']['tree_sha'] = 'b' * 40
        elif damage == 'identity':
            value['artifact_id'] = 'another'
        elif damage == 'status':
            value['status'] = 'uncertain'
        journal.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(SourceSyncError, match='Accepted component'):
        owner.prepare_project_candidate('demo', **args)
    assert remote.writes == 0
    assert not any(c[0] == 'prepare' for c in calls)


def test_normal_trial_key_cannot_be_reused_for_another_accepted_basis(trial):
    owner, args, remote, _, _, _ = trial
    owner.prepare_project_candidate('demo', **args)
    with pytest.raises(SourceSyncError, match='another source basis'):
        owner.prepare_project_candidate('demo', **{**args, 'source_revision': 'b' * 40})
    assert remote.writes == 1


@pytest.mark.parametrize('change', ['environment', 'missing_log', 'changed_log'])
def test_source_check_reuse_rejects_changed_environment_or_missing_evidence(trial, monkeypatch, change):
    owner, args, _, _, calls, publication = trial
    owner.prepare_project_candidate('demo', **args)
    if change == 'environment':
        monkeypatch.setattr(source_validation, 'validation_environment', lambda: {'runtime': 'new-core'})
    else:
        logs = list((publication.state_root / 'source-validation').glob('*/*.log'))
        assert len(logs) == 1
        if change == 'missing_log':
            logs[0].unlink()
        else:
            logs[0].write_text('changed', encoding='utf-8')
    result = owner.prepare_project_candidate('demo', **args)
    assert result['source_validation']['reused'] is False
    inputs = [row[1]['validation_evidence'] for row in calls if row[0] == 'prepare']
    assert inputs[0] == inputs[1]


def test_failed_frozen_tests_cannot_reach_candidate_or_be_reused(trial, monkeypatch):
    owner, args, remote, _, calls, _ = trial
    def fail(*a, log_path, **k):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text('failed', encoding='utf-8')
        return {'pytest': SimpleNamespace(name='pytest', status='failed', detail='failure')}
    # TestResult is a dataclass, as in the real runner.
    from adaos.services.skill.tests_runner import TestResult
    def failed(*a, log_path, **k):
        fail(log_path=log_path)
        return {'pytest': TestResult('pytest', 'failed')}
    monkeypatch.setattr(source_validation, 'run_tests', failed)
    with pytest.raises(RootServiceError, match=r'Builder Dev Ticket dticket\.') as error:
        owner.prepare_project_candidate('demo', **args)
    assert isinstance(error.value.__cause__, source_validation.SourceValidationTestsFailed)
    assert remote.writes == 1
    assert not any(c[0] == 'prepare' for c in calls)


def test_completed_packages_survive_later_timeout_with_sufficient_inherited_budget(source, tmp_path, monkeypatch):
    from adaos.services.skill.tests_runner import TestResult
    workspace, _, _ = source
    path = workspace / 'projects/demo/project.yaml'
    definition = yaml.safe_load(path.read_text(encoding='utf-8'))
    definition['components']['owned'].append({'ref': 'skill:second', 'role': 'supporting'})
    path.write_text(yaml.safe_dump(definition), encoding='utf-8')
    second = workspace / 'skills/second/tests'
    second.mkdir(parents=True)
    (second / 'test_second.py').write_text('def test_second(): pass\n', encoding='utf-8')
    frozen = capture_project(workspace, 'demo')
    monkeypatch.setattr(source_validation, 'validation_environment', lambda: {'core': 'same'})
    calls = []
    def run(component, *, log_path, timeout_seconds, **kwargs):
        calls.append((component.name, timeout_seconds))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text('retained test output', encoding='utf-8')
        if len(calls) == 2:
            raise subprocess.TimeoutExpired('pytest', timeout_seconds)
        return {'pytest': TestResult('pytest', 'passed')}
    monkeypatch.setattr(source_validation, 'run_tests', run)
    args = dict(workspace=workspace, project_id='demo', snapshot_digest=frozen.digest,
                state_root=tmp_path / 'checks', timeout_seconds=600)
    with pytest.raises(source_validation.SourceValidationTimeout):
        source_validation.validate_restored_project(**args)
    receipt = source_validation.validate_restored_project(**args)
    assert receipt['status'] == 'passed'
    assert calls == [('worker', 600), ('second', 600), ('second', 600)]
    assert receipt['packages'][0]['reused'] is True


def test_real_publication_replay_keeps_candidate_and_release_after_later_dev_edits(source, tmp_path, monkeypatch):
    from adaos.domain.artifact_release import ArtifactSourceRef
    from adaos.sdk.developer.compositions import _new_project_definition
    from adaos.services.artifact_pipeline import ArtifactPublicationService
    from test_artifact_publication_service import _Remote, _scenario
    workspace, forge, sync = source
    scenario = _scenario(workspace / 'scenarios')
    (scenario / 'tests').mkdir()
    (scenario / 'tests/test_source.py').write_text('def test_source():\n    assert True\n', encoding='utf-8')
    manifest = workspace / 'projects/demo/project.yaml'
    definition = _new_project_definition('demo', primary_ref='scenario:recipes',
        title='Source recovery qualification',
        entrypoints=({'id': 'main', 'presentation': 'scenario:recipes', 'default': True, 'bindings': {}},))
    definition['version'] = '1.0.0'
    manifest.write_text(yaml.safe_dump(definition), encoding='utf-8')
    tree = SourceSnapshot.capture(scenario).tree()
    remote = _Remote(tmp_path / 'package-remote')
    remote.tree = tree
    publication = ArtifactPublicationService(state_root=tmp_path / 'real-publication',
        workspace_root=tmp_path / 'installed', remote=remote)
    ref = ArtifactSourceRef(forge='adaos-root', repository='test/registry', revision='a' * 40,
                            path_scope=('scenarios/recipes/',))
    publication.record_push(kind='scenario', artifact_id='recipes', artifact_dir=scenario,
                            source_ref=ref, source_tree=tree)
    owner = RootDeveloperService.__new__(RootDeveloperService)
    owner.project_source_sync = sync
    owner._load_config = lambda: None
    owner._artifact_publication_service = lambda cfg: publication
    owner._admit_project_candidate_cbs = lambda **k: None
    monkeypatch.setattr(source_validation, 'validation_environment', lambda: {'core': 'qualified'})
    args = dict(source_kind='scenario', source_name='recipes', source_revision='a' * 40,
                change_ids=('accepted',), idempotency_key='real-trial')
    first = owner.prepare_project_candidate('demo', **args)
    (scenario / 'webui.json').write_text('{"newer_local_work": true}', encoding='utf-8')
    second = owner.prepare_project_candidate('demo', **args)
    assert second['candidate'] == first['candidate']
    assert second['release'] == first['release']
    assert second['source_validation']['reused'] is True
    assert forge.writes == 1
    assert (scenario / 'webui.json').read_text() == '{"newer_local_work": true}'
