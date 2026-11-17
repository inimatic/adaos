import json
import subprocess

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import ArtifactPublicationService, PublicationError
from adaos.services.artifact_pipeline.legacy_project_source import qualify_legacy_project_source
from adaos.services.artifact_pipeline.project_source_sync import ProjectSourceSync
from adaos.services.artifact_pipeline.source_checkpoints import SourceCheckpointError
from test_artifact_publication_service import _Remote, _scenario, _skill
from test_project_source_sync import Remote


def legacy_release(tmp_path, *, changed_component=False):
    repo = tmp_path / 'historical'
    _scenario(repo / 'scenarios')
    skill = _skill(repo / 'skills')
    tests = skill / 'tests'
    tests.mkdir(exist_ok=True)
    (tests / 'test_retained.py').write_bytes(b'assert True\n')
    project = repo / 'projects/recipes_project'
    project.mkdir(parents=True)
    (project / 'project.yaml').write_text(json.dumps({
        'schema': 'adaos.project.v1', 'kind': 'project', 'id': 'recipes_project', 'version': '1.0.0',
        'profiles': [], 'components': {'owned': [{'ref': 'scenario:recipes', 'role': 'primary'},
            {'ref': 'skill:shopping_skill', 'role': 'implementation'}], 'dependencies': []},
        'entrypoints': [{'id': 'main', 'presentation': 'scenario:recipes', 'default': True, 'bindings': {}}],
        'catalog': {'title': 'Recipes', 'description': 'Historical release', 'categories': [], 'tags': []},
        'lifecycle': {'uninstall': {'components': 'remove_if_unreferenced', 'runtime_data': 'retain',
            'source_artifacts': 'retain'}}}), encoding='utf-8')
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), '-c', 'core.autocrlf=false', *args],
                              capture_output=True, check=True).stdout.decode().strip()
    git('init')
    git('add', '.')
    git('-c', 'user.email=test@example.invalid', '-c', 'user.name=Test', 'commit', '-m', 'historical')
    commit = git('rev-parse', 'HEAD')
    if changed_component:
        (skill / 'new-runtime-file.txt').write_bytes(b'not in the retained commit\n')
    remote = _Remote(tmp_path / 'publication-remote')
    publication = ArtifactPublicationService(state_root=tmp_path / 'state',
        workspace_root=tmp_path / 'workspace', remote=remote)
    ref = ArtifactSourceRef(forge='github', repository=str(repo), revision=commit,
                            path_scope=('projects/recipes_project/',))
    prepared = publication.prepare_project_candidate(project_id='recipes_project', project_dir=project,
        source_workspace_root=repo, source_ref=ref, source_tree=commit,
        change_ids=('historical',), validation_evidence={'status': 'passed'})
    candidate = publication.decide_candidate(prepared.candidate.candidate_id, accepted=True)
    transport = Remote()
    def sync():
        return ProjectSourceSync(state_root=tmp_path / 'source-state', workspace=repo,
            subnet_id='sn_test', node_id='node_test', transport=transport)
    return repo, publication, remote, candidate, transport, sync


def test_legacy_source_migration_proves_all_packages_and_preserves_candidate_and_current_edits(tmp_path):
    repo, publication, remote, candidate, transport, sync = legacy_release(tmp_path)
    with pytest.raises(PublicationError, match='identify one component'):
        publication.verify_candidate_public_source(candidate.candidate_id)
    later = repo / 'skills/shopping_skill/later.txt'
    later.write_bytes(b'current work must survive\n')
    receipt = qualify_legacy_project_source(publication, sync(), candidate.candidate_id, repository=repo)
    assert publication.candidate_store.load(candidate.candidate_id) == candidate
    assert receipt['source_tree'] == candidate.source_ref.revision
    assert receipt['verified_source_tree'] != receipt['source_tree']
    assert len(receipt['components']) == 2
    remote.tree = receipt['verified_source_tree']
    publication.verify_candidate_public_source(candidate.candidate_id)
    assert qualify_legacy_project_source(publication, sync(), candidate.candidate_id, repository=repo) == receipt
    assert transport.writes == 1
    assert later.read_bytes() == b'current work must survive\n'
    remote.tree = 'a' * 40
    with pytest.raises(PublicationError, match='source tree changed'):
        publication.verify_candidate_public_source(candidate.candidate_id)


def test_legacy_source_mismatch_in_supporting_component_rejects_before_remote_write(tmp_path):
    repo, publication, _, candidate, transport, sync = legacy_release(tmp_path, changed_component=True)
    with pytest.raises(SourceCheckpointError, match='shopping_skill'):
        qualify_legacy_project_source(publication, sync(), candidate.candidate_id, repository=repo)
    assert transport.writes == 0
    assert publication.source_checkpoints.receipt(candidate) is None


def test_legacy_source_migration_recovers_lost_reply_in_a_fresh_writer_without_overwriting_dev(tmp_path):
    repo, publication, remote, candidate, transport, sync = legacy_release(tmp_path)
    transport.after_write = lambda: (_ for _ in ()).throw(TimeoutError('lost reply after Git commit'))
    with pytest.raises(TimeoutError):
        qualify_legacy_project_source(publication, sync(), candidate.candidate_id, repository=repo)
    later = repo / 'skills/shopping_skill/later.txt'
    later.write_bytes(b'newer source\n')
    transport.after_write = lambda: None
    receipt = qualify_legacy_project_source(publication, sync(), candidate.candidate_id, repository=repo)
    assert transport.writes == 1
    assert later.read_bytes() == b'newer source\n'
    remote.tree = receipt['verified_source_tree']
    publication.verify_candidate_public_source(candidate.candidate_id)


def test_legacy_source_migration_requires_explicit_original_repository(tmp_path):
    repo, publication, _, candidate, transport, sync = legacy_release(tmp_path)
    with pytest.raises(SourceCheckpointError, match='Explicit repository'):
        qualify_legacy_project_source(publication, sync(), candidate.candidate_id, repository=tmp_path)
    assert transport.writes == 0
