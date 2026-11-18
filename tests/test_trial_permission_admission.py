"""A denied Trial cannot publish a release or lock consent into source identity."""
import pytest
import yaml

from adaos.services.artifact_pipeline import ArtifactPublicationService
from adaos.services.artifact_pipeline.publication import TrialPermissionReviewRequired
from adaos.sdk.developer.compositions import _new_project_definition
from test_artifact_publication_service import _Remote, _scenario, _skill, _source
from test_project_trial_source import trial
from test_project_source_sync import source


@pytest.mark.parametrize('decision', [None, False, {'approved': False}])
def test_denied_trial_permissions_cannot_publish_and_approved_retry_reuses_version(tmp_path, decision):
    workspace = tmp_path / 'dev'
    _scenario(workspace / 'scenarios')
    skill = _skill(workspace / 'skills')
    manifest = skill / 'skill.yaml'
    definition = yaml.safe_load(manifest.read_text(encoding='utf-8'))
    definition['permissions'] = ['workspace.read']
    manifest.write_text(yaml.safe_dump(definition), encoding='utf-8')
    project = workspace / 'projects/demo'
    project.mkdir(parents=True)
    definition = _new_project_definition('demo', primary_ref='scenario:recipes', title='Permission trial',
        entrypoints=({'id': 'main', 'presentation': 'scenario:recipes', 'default': True, 'bindings': {}},))
    definition['components']['owned'].append({'ref': 'skill:shopping_skill', 'role': 'implementation'})
    definition['permission_profile'] = {'schema': 'adaos.application.permission_profile.v1',
        'required': [{'id': 'workspace.read', 'approval_policy': 'grant_on_install',
                      'purpose': 'Read this test workspace.'}], 'optional': []}
    (project / 'project.yaml').write_text(yaml.safe_dump(definition), encoding='utf-8')
    remote = _Remote(tmp_path / 'remote')
    service = ArtifactPublicationService(state_root=tmp_path / 'state', workspace_root=tmp_path / 'active', remote=remote)
    arguments = dict(project_id='demo', project_dir=project, source_workspace_root=workspace,
        source_ref=_source(), source_tree='f' * 40, change_ids=('permission-trial',),
        validation_evidence={'status': 'passed'})
    with pytest.raises(TrialPermissionReviewRequired) as rejected:
        service.prepare_project_candidate(**arguments, permission_decision=decision)
    assert 'workspace.read' in rejected.value.requirements
    assert remote.archives == {}
    assert remote.channel_writes == 0
    assert remote.releases.release_digests_by_version('demo') == {}
    assert not (service.workspace_root / '.adaos/workspace.lock.json').exists()
    approved = service.prepare_project_candidate(**arguments, permission_decision={'approved': True, 'actor': 'user:test'})
    assert len(remote.releases.release_digests_by_version('demo')) == 1
    assert approved.candidate.version == definition['version']
    assert approved.trial_activation['status'] == 'active'


def test_permission_review_retry_keeps_frozen_source_and_release_qualification(trial, monkeypatch):
    owner, args, forge, _, calls, publication = trial
    from adaos.services.artifact_pipeline.project_source_sync import capture_project
    snapshot = capture_project(owner.project_source_sync().workspace, 'demo')
    args = {'change_id': 'permission-review', 'operation_id': 'frozen-source',
        'validation_evidence': {'status': 'passed', 'source_snapshot_digest': snapshot.digest},
        'idempotency_key': 'trial-before-review'}
    original = publication.prepare_project_candidate
    def reject(**kwargs):
        if not (kwargs.get('permission_decision') or {}).get('approved'):
            raise TrialPermissionReviewRequired(['data.read'])
        return original(**kwargs)
    monkeypatch.setattr(publication, 'prepare_project_candidate', reject)
    with pytest.raises(TrialPermissionReviewRequired):
        owner.prepare_project_candidate_from_source_checkpoint('demo', **args)
    result = owner.prepare_project_candidate_from_source_checkpoint('demo', **{**args, 'idempotency_key': 'approved-trial',
        'permission_decision': {'approved': True, 'actor': 'user:test'}})
    assert result['candidate']['candidate_id'] == 'frozen'
    assert result['source_validation']['reused'] is True
    assert forge.writes == 1


def test_permission_review_is_a_known_rejection_not_an_unknown_external_outcome():
    from adaos.services.applications.development import _publication_preflight_rejection
    rejected = _publication_preflight_rejection(TrialPermissionReviewRequired(['data.read']))
    assert rejected['error']['code'] == 'trial_permission_approval_required'
    assert rejected['error']['requirements'] == ['data.read']
