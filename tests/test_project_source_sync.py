import base64
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from adaos.domain.artifact_release import canonical_payload_digest, sha256_digest
from adaos.services.artifact_pipeline.project_source_sync import ProjectSourceSync, SourceSyncError, capture_project
from adaos.services.artifact_pipeline.source_snapshots import SourceSnapshot
from adaos.services.development_source_admission import SourceAdmissionError


class Remote:
    def __init__(self):
        self.receipts = {}
        self.writer = None
        self.heads = {}
        self.writes = 0
        self.after_write = lambda: None
        self.available = True

    def status(self, scope):
        if not self.available:
            raise ConnectionError('offline')
        return dict(repository='test/registry', target='main', base='1' * 40,
                    head=self.heads.get(scope['change_id']), writer=self.writer)

    def write(self, request):
        self.writes += 1
        frozen = SourceSnapshot.from_archive(base64.b64decode(request['archive_b64']))
        commit = str(self.writes + 1) * 40
        receipt = {**{k: v for k, v in request.items() if k != 'archive_b64'},
                   'state': 'remote_confirmed', 'commit': commit, 'tree': frozen.tree(),
                   'stored_path': f"subnets/{request['subnet_id']}/nodes/{request['node_id']}/projects/{request['project_id']}/source",
                   'inventory': frozen.inventory, 'archive_b64': request['archive_b64'],
                   'retention_ref': f'refs/heads/source-receipts/{commit}'}
        self.receipts[request['operation_id']] = receipt
        self.writer = commit
        self.heads[request['change_id']] = commit
        self.after_write()
        return receipt

    def read(self, scope, operation_id, revision=None):
        if not self.available:
            raise ConnectionError('offline')
        return self.receipts.get(operation_id)


@pytest.fixture
def source(tmp_path):
    workspace = tmp_path / 'dev'
    project = workspace / 'projects/demo'
    project.mkdir(parents=True)
    (project / 'project.yaml').write_text(yaml.safe_dump({
        'schema': 'adaos.project.v1', 'kind': 'project', 'id': 'demo', 'version': '1.0.0',
        'components': {'owned': [{'ref': 'skill:worker', 'role': 'primary'}],
                       'dependencies': [{'ref': 'skill:external', 'version': '^1'}]}}), encoding='utf-8')
    skill = workspace / 'skills/worker'
    (skill / 'tests').mkdir(parents=True)
    (skill / 'skill.yaml').write_text('name: worker\nversion: 1.0.0\n', encoding='utf-8')
    (skill / 'tests/test_worker.py').write_text('assert True\n', encoding='utf-8')
    (skill / 'assets').mkdir()
    (skill / 'assets/mask.bin').write_bytes(b'\x00\xff\x80\r\n\x00mask')
    (workspace / 'unowned.txt').write_text('private unrelated\n', encoding='utf-8')
    remote = Remote()
    def service():
        return ProjectSourceSync(state_root=tmp_path / 'state', workspace=workspace,
                                 subnet_id='sn_test', node_id='node_test', transport=remote)
    return workspace, remote, service


def test_full_source_capture_keeps_tests_and_excludes_unowned_dependencies(source):
    workspace, _, _ = source
    snapshot = capture_project(workspace, 'demo')
    assert [name for name, _ in snapshot.entries] == [
        'projects/demo/project.yaml', 'skills/worker/assets/mask.bin',
        'skills/worker/skill.yaml', 'skills/worker/tests/test_worker.py']


def _context_artifact(workspace, publication='private'):
    group = workspace / 'skills/worker/artifacts/inputs'
    group.mkdir(parents=True)
    data = b'large immutable authoring input' * 100
    (group / 'raw.bin').write_bytes(data)
    manifest = {'schema': 'adaos.skill.artifact_group.v1', 'schema_version': '1.1.0',
                'skill_ref': 'skill:worker', 'group_id': 'inputs', 'generation': 1,
                'updated_at': '2026-11-18T00:00:00Z', 'items': [{
                    'artifact_id': 'raw', 'path': 'raw.bin', 'media_type': 'application/octet-stream',
                    'role': 'source', 'digest': sha256_digest(data), 'size_bytes': len(data),
                    'origin': {}, 'trust': 'untrusted', 'sensitivity': 'internal',
                    'license': None, 'publication': publication, 'analysis': {}}]}
    manifest['digest'] = canonical_payload_digest(manifest)
    path = group / 'manifest.yaml'
    path.write_text(yaml.safe_dump(manifest), encoding='utf-8')
    return path


def test_private_builder_inputs_do_not_consume_source_budget_but_marts_and_unlisted_files_remain(source, monkeypatch):
    from adaos.services.artifact_pipeline import source_snapshots
    workspace, _, _ = source
    manifest = _context_artifact(workspace)
    (manifest.parent / 'unlisted.txt').write_text('keep unclassified source', encoding='utf-8')
    (manifest.parent / '.mutation.lock').write_text('1', encoding='utf-8')
    mart = workspace / 'skills/worker/marts/cities.json'
    mart.parent.mkdir()
    mart.write_text('[{"population": 53312}]', encoding='utf-8')
    monkeypatch.setattr(source_snapshots, 'MAX_BYTES', 2000)
    snapshot = dict(capture_project(workspace, 'demo').entries)
    assert 'skills/worker/artifacts/inputs/raw.bin' not in snapshot
    assert 'skills/worker/artifacts/inputs/manifest.yaml' not in snapshot
    assert 'skills/worker/artifacts/inputs/.mutation.lock' not in snapshot
    assert snapshot['skills/worker/marts/cities.json'] == mart.read_bytes()
    assert 'skills/worker/artifacts/inputs/unlisted.txt' in snapshot
    assert (manifest.parent / 'raw.bin').is_file()


@pytest.mark.parametrize('publication', ['project', 'redistributable'])
def test_publishable_artifact_groups_stay_in_complete_source(source, publication):
    workspace, _, _ = source
    _context_artifact(workspace, publication)
    snapshot = dict(capture_project(workspace, 'demo').entries)
    assert 'skills/worker/artifacts/inputs/manifest.yaml' in snapshot
    assert 'skills/worker/artifacts/inputs/raw.bin' in snapshot


def test_private_artifact_manifest_must_be_valid_before_exclusion(source):
    workspace, _, _ = source
    manifest = _context_artifact(workspace)
    value = yaml.safe_load(manifest.read_text())
    value['digest'] = 'sha256:' + '0' * 64
    manifest.write_text(yaml.safe_dump(value), encoding='utf-8')
    with pytest.raises(SourceSyncError, match='manifest is invalid'):
        capture_project(workspace, 'demo')


def test_private_publication_policy_cannot_change_during_capture(source, monkeypatch):
    workspace, _, _ = source
    manifest = _context_artifact(workspace)
    original = SourceSnapshot.capture
    def changed(cls, root, *, skip):
        result = original(root, skip=skip)
        manifest.write_text(manifest.read_text().replace('publication: private', 'publication: project'), encoding='utf-8')
        return result
    monkeypatch.setattr(SourceSnapshot, 'capture', classmethod(changed))
    with pytest.raises(SourceSyncError, match='publication changed'):
        capture_project(workspace, 'demo')


@pytest.mark.parametrize('code,terminal', [
    ('source_component_owner_transfer_required', True),
    ('source_base_revision_changed', True),
    ('source_git_operation_failed', False),
])
def test_only_typed_prewrite_refusal_releases_writer_and_keeps_frozen_evidence(source, code, terminal):
    workspace, remote, factory = source
    real_write = remote.write
    def refused(request):
        error = RuntimeError('bounded source refusal')
        error.error_code = code
        error.status_code = 409
        raise error
    remote.write = refused
    sync = factory()
    with pytest.raises(RuntimeError):
        sync.checkpoint('demo', 'change_one', operation_id='refused')
    path, archive = sync._paths(sync._scope('demo', 'change_one'), 'refused')
    intent = sync._read_json(path)
    assert intent['state'] == ('rejected' if terminal else 'sending')
    assert SourceSnapshot.from_archive(archive.read_bytes()).digest == intent['snapshot_digest']
    remote.write = real_write
    if terminal:
        assert sync.checkpoint('demo', 'change_one', operation_id='new_admitted_request')['operation_state'] == 'remote_confirmed'
    else:
        with pytest.raises(SourceSyncError, match='requires reconciliation'):
            sync.checkpoint('demo', 'change_one', operation_id='new_admitted_request')
        assert remote.writes == 0


def test_edit_during_send_is_local_work_and_retry_never_restores_old_source(source):
    workspace, remote, service = source
    file = workspace / 'skills/worker/tests/test_worker.py'
    remote.after_write = lambda: file.write_text('assert 2 == 2\n', encoding='utf-8')
    result = service().checkpoint('demo', 'change_one', operation_id='op_one')
    assert result['state'] == 'local_changes'
    assert result['local_revision'] != result['confirmed_revision']
    assert result['operation_state'] == 'remote_confirmed'
    service().checkpoint('demo', 'change_one', operation_id='op_one')
    assert file.read_text() == 'assert 2 == 2\n'
    assert remote.writes == 1


def test_lost_reply_recovers_on_fresh_status_after_restart_and_preserves_edit(source):
    workspace, remote, service = source
    def lost_reply():
        raise TimeoutError('reply lost after push')
    remote.after_write = lost_reply
    with pytest.raises(TimeoutError):
        service().checkpoint('demo', 'change_one', operation_id='op_one')
    file = workspace / 'skills/worker/tests/test_worker.py'
    file.write_text('assert 3 == 3\n', encoding='utf-8')
    status = service().status('demo', 'change_one')
    assert status['state'] == 'local_changes'
    assert status['confirmed']['operation_id'] == 'op_one'
    assert status['remote_availability'] == 'verified'
    remote.after_write = lambda: None
    service().checkpoint('demo', 'change_one', operation_id='op_one')
    assert remote.writes == 1
    assert file.read_text() == 'assert 3 == 3\n'


def test_unavailable_remote_retains_previous_confirmation(source):
    _, remote, service = source
    receipt = service().checkpoint('demo', 'change_one', operation_id='op_one')['confirmed']
    remote.available = False
    status = service().status('demo', 'change_one')
    assert status['state'] == 'reconciling'
    assert status['confirmed'] == receipt
    assert status['remote_availability'] == 'unverified'


def test_missing_root_protocol_is_classified_without_returning_proxy_html(source, monkeypatch):
    from adaos.services.root.client import RootHttpError
    _, remote, service = source
    def unavailable(scope):
        raise RootHttpError('<html>Cannot GET /v1/projects/source/status</html>', status_code=404)
    monkeypatch.setattr(remote, 'status', unavailable)
    status = service().status('demo', 'change_one')
    assert status['remote_diagnostic']['code'] == 'source_endpoint_unavailable'
    assert status['remote_diagnostic']['http_status'] == 404
    assert '<html>' not in json.dumps(status)
    assert status['state'] == 'reconciling'


@pytest.mark.parametrize('remote_available', [True, False])
@pytest.mark.parametrize('local_fault', ['ownership', 'manifest', 'missing_source'])
def test_local_source_fault_never_hides_remote_status_or_retained_receipt(source, remote_available, local_fault):
    workspace, remote, service = source
    receipt = service().checkpoint('demo', 'change_one', operation_id='op_one')['confirmed']
    if local_fault == 'ownership':
        other = workspace / 'projects/other'
        other.mkdir()
        definition = yaml.safe_load((workspace / 'projects/demo/project.yaml').read_text())
        definition['id'] = 'other'
        (other / 'project.yaml').write_text(yaml.safe_dump(definition), encoding='utf-8')
    elif local_fault == 'manifest':
        (workspace / 'projects/demo/project.yaml').write_text('not-a-project\n', encoding='utf-8')
    else:
        (workspace / 'skills/worker').rename(workspace / 'skills/moved_worker')
    remote.available = remote_available
    status = service().status('demo', 'change_one')
    assert status['local_revision'] is None
    assert status['local_availability'] == 'unverified'
    assert status['local_diagnostic']['detail']
    assert status['confirmed'] == receipt
    assert status['remote_availability'] == ('verified' if remote_available else 'unverified')
    assert status['state'] == ('local_unverified' if remote_available else 'reconciling')
    if not remote_available:
        assert status['diagnostic'] == 'offline'
    remote.available = True
    with pytest.raises((SourceAdmissionError, SourceSyncError)):
        service().checkpoint('demo', 'change_one', operation_id='op_next')
    assert remote.writes == 1


def test_replaying_older_operation_does_not_rewind_last_confirmation(source):
    workspace, _, service = source
    service().checkpoint('demo', 'change_one', operation_id='op_one')
    (workspace / 'skills/worker/tests/test_worker.py').write_text('assert 4 == 4\n', encoding='utf-8')
    latest = service().checkpoint('demo', 'change_one', operation_id='op_two')['confirmed']
    replay = service().checkpoint('demo', 'change_one', operation_id='op_one')
    assert replay['confirmed'] == latest
    assert replay['state'] == 'synchronized'


def test_unknown_previous_write_blocks_new_operation_until_reconciled(source):
    _, remote, service = source
    def no_write(request):
        raise TimeoutError('unknown outcome')
    remote.write = no_write
    with pytest.raises(TimeoutError):
        service().checkpoint('demo', 'change_one', operation_id='op_one')
    with pytest.raises(SourceSyncError, match='Previous source outcome'):
        service().checkpoint('demo', 'change_two', operation_id='op_two')
    remote.writer = 'f' * 40
    assert service().status('demo', 'change_one')['state'] == 'diverged'


def test_clean_restore_proves_tests_and_runtime_projection(source, tmp_path):
    workspace, _, service = source
    result = service().checkpoint('demo', 'change_one', operation_id='op_one')
    restored = tmp_path / 'restored'
    proof = service().restore('demo', 'change_one', operation_id='op_one', destination=restored)
    assert proof['snapshot_digest'] == result['confirmed_revision']
    assert (restored / 'skills/worker/tests/test_worker.py').read_text() == 'assert True\n'
    from adaos.services.artifact_pipeline.packages import artifact_source_snapshot
    restored_projection = artifact_source_snapshot(restored / 'skills/worker')
    original_projection = artifact_source_snapshot(workspace / 'skills/worker')
    assert {k: v for k, v in restored_projection.items() if k != 'root'} == {k: v for k, v in original_projection.items() if k != 'root'}


def test_wrong_remote_tree_is_not_a_confirmation(source):
    _, remote, service = source
    original = remote.read
    def wrong(*args):
        result = original(*args)
        return {**result, 'tree': 'f' * 40} if result else None
    remote.read = wrong
    with pytest.raises(SourceSyncError, match='proof differs'):
        service().checkpoint('demo', 'change_one', operation_id='op_one')


@pytest.mark.skipif(not os.getenv('ADAOS_SOURCE_SYNC_MODULE'), reason='requires explicitly built Root source module')
def test_core_root_git_process_boundary_and_clean_restore(source, tmp_path):
    workspace, _, _ = source
    remote = tmp_path / 'remote.git'
    checkout = tmp_path / 'root'
    def git(cwd, *args):
        return subprocess.check_output(['git', *args], cwd=cwd, text=True, stderr=subprocess.PIPE).strip()
    git(tmp_path, 'init', '--bare', '--initial-branch=main', str(remote))
    git(tmp_path, 'clone', str(remote), str(checkout))
    git(checkout, 'config', 'user.name', 'Core Root Source Qualification')
    git(checkout, 'config', 'user.email', 'test@localhost')
    (checkout / 'README').write_text('seed\n', encoding='utf-8')
    git(checkout, 'add', 'README')
    git(checkout, 'commit', '-m', 'seed')
    git(checkout, 'push', 'origin', 'main')
    bridge = Path(__file__).resolve().parents[1] / 'src/adaos/integrations/adaos-backend/backend/source-sync-bridge.mjs'

    class RootTransport:
        writes = 0
        lose_reply = True
        def call(self, action, **payload):
            output = subprocess.check_output(['node', str(bridge)], input=json.dumps({
                'action': action, 'cwd': str(checkout), **payload}).encode(), stderr=subprocess.PIPE, timeout=90)
            return json.loads(output)
        def status(self, scope): return self.call('status', scope=scope)
        def read(self, scope, operation_id, revision=None):
            return self.call('read', scope=scope, operation_id=operation_id, revision=revision)
        def write(self, request):
            self.writes += 1
            result = self.call('write', request=request)
            if self.lose_reply:
                raise TimeoutError('connection lost after Root process pushed')
            return result

    transport = RootTransport()
    def core():
        return ProjectSourceSync(state_root=tmp_path / 'real-state', workspace=workspace,
            subnet_id='sn_test', node_id='node_test', transport=transport)
    with pytest.raises(TimeoutError):
        core().checkpoint('demo', 'change_one', operation_id='op_one')
    current = workspace / 'skills/worker/tests/test_worker.py'
    current.write_text('assert 5 == 5\n', encoding='utf-8')
    observed = core().status('demo', 'change_one')
    assert observed['remote_availability'] == 'verified'
    assert observed['state'] == 'local_changes'
    core().checkpoint('demo', 'change_one', operation_id='op_one')
    assert transport.writes == 1
    destination = tmp_path / 'clean-restore'
    proof = core().restore('demo', 'change_one', operation_id='op_one', destination=destination)
    assert proof['snapshot_digest'] == observed['confirmed_revision']
    assert (destination / 'skills/worker/tests/test_worker.py').read_text() == 'assert True\n'
    assert (destination / 'skills/worker/assets/mask.bin').read_bytes() == b'\x00\xff\x80\r\n\x00mask'
    assert current.read_text() == 'assert 5 == 5\n'
    assert git(remote, 'rev-parse', 'main') != proof['commit']
    assert git(remote, 'rev-parse', observed['confirmed']['retention_ref']) == proof['commit']


def test_candidate_hook_uses_original_full_snapshot_while_dev_has_later_edits(source, tmp_path):
    workspace, _, service = source
    (workspace / 'skills/worker/tests/test_worker.py').write_text('def test_worker():\n    assert True\n', encoding='utf-8')
    confirmed = service().checkpoint('demo', 'change_one', operation_id='op_one')
    live = workspace / 'skills/worker/tests/test_worker.py'
    live.write_text('assert 6 == 6\n', encoding='utf-8')
    calls = []
    class Publication:
        state_root = tmp_path / 'publication'
        def prepare_project_candidate(self, **kwargs):
            calls.append(kwargs)
            assert (kwargs['source_workspace_root'] / 'skills/worker/tests/test_worker.py').read_text() == 'def test_worker():\n    assert True\n'
            assert kwargs['source_ref'].path_scope == ('subnets/sn_test/nodes/node_test/projects/demo/source/skills/worker/',)
            assert kwargs['release_source_ref'].path_scope == ('subnets/sn_test/nodes/node_test/projects/demo/source/',)
            return SimpleNamespace(candidate=SimpleNamespace(to_dict=lambda: {'candidate_id': 'frozen'}),
                plan=SimpleNamespace(release=SimpleNamespace(to_dict=lambda: {'project_id': 'demo'})),
                trial_workspace=tmp_path / 'trial', trial_activation={'status': 'ready'})
    from adaos.services.root.service import RootDeveloperService
    owner = RootDeveloperService.__new__(RootDeveloperService)
    owner.project_source_sync = service
    owner._load_config = lambda: None
    owner._artifact_publication_service = lambda cfg: Publication()
    owner._admit_project_candidate_cbs = lambda **kwargs: None
    result = owner.prepare_project_candidate_from_source_checkpoint('demo', change_id='change_one', operation_id='op_one',
        validation_evidence={'status': 'passed', 'source_snapshot_digest': confirmed['confirmed_revision']},
        permission_decision={'allowed': True})
    assert result['candidate']['candidate_id'] == 'frozen'
    assert calls[0]['permission_decision'] == {'allowed': True}
    assert live.read_text() == 'assert 6 == 6\n'
    from adaos.services.root.service import RootServiceError
    with pytest.raises(RootServiceError, match='exact confirmed'):
        owner.prepare_project_candidate_from_source_checkpoint('demo', change_id='change_one', operation_id='op_one',
            validation_evidence={'status': 'passed', 'source_snapshot_digest': 'sha256:' + 'f' * 64})


def test_remote_source_reader_and_release_projection_keep_complete_project_prefix():
    from adaos.domain.artifact_release import ArtifactSourceRef
    from adaos.services.artifact_pipeline.project_build import _source_ref
    from adaos.services.artifact_pipeline.source_checkpoints import RemoteProjectSourceReader
    from adaos.services.artifact_pipeline.remote import RemoteReleaseRepository
    snapshot = SourceSnapshot((('skill.yaml', b'name: worker\nversion: 1.0.0\n'), ('tests/test_worker.py', b'assert True\n')))
    prefix = 'subnets/sn_test/nodes/node_test/projects/demo/source/'
    base = ArtifactSourceRef(forge='adaos-root', repository='test/private', revision='1' * 40, path_scope=(prefix,))
    scoped = _source_ref(base, 'skills/worker/')
    assert scoped.path_scope == (prefix + 'skills/worker/',)
    response = {'repository': 'test/private', 'commit': '1' * 40, 'stored_path': prefix + 'skills/worker',
                'tree_sha': snapshot.tree(), 'archive_b64': base64.b64encode(snapshot.archive()).decode()}
    client = SimpleNamespace(project_source_operation=lambda *a, **kw: response)
    remote = RemoteReleaseRepository(client=client)
    assert remote.tree_revision(scoped) == snapshot.tree()
    reader = RemoteProjectSourceReader(remote)
    assert reader(SimpleNamespace(source_ref=_source_ref(base, 'projects/demo/'),
        verification_source_ref=scoped, source_tree=snapshot.tree())) == dict(snapshot.entries)
    response['stored_path'] = 'another/path'
    with pytest.raises(ValueError, match='binding'):
        remote.tree_revision(scoped)
