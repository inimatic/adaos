import pytest

from adaos.services.artifact_pipeline import ActivationError, ArtifactPublicationService, WorkspaceActivationManager
from test_artifact_publication_service import _Remote, _named_scenario, _promote, _scenario, _source, _source_for


def test_recovered_promotion_readmits_current_workspace_without_replaying_channel(tmp_path):
    remote = _Remote(tmp_path / 'remote')
    service = ArtifactPublicationService(state_root=tmp_path / 'state',
        workspace_root=tmp_path / 'workspace', remote=remote)

    def prepare(root, name):
        service.record_push(kind='scenario', artifact_id=name, artifact_dir=root, source_ref=_source_for(name))
        prepared = service.prepare_candidate(kind='scenario', artifact_id=name, artifact_dir=root,
            change_ids=(f'change-{name}',), validation_evidence={'status': 'passed'})
        service.decide_candidate(prepared.candidate.candidate_id, accepted=True)
        return prepared.candidate

    source = _scenario(tmp_path / 'dev')
    initial = prepare(source, 'recipes')
    _promote(service, initial.candidate_id)
    (source / 'scenario.yaml').write_text('id: recipes\nversion: 1.1.0\ntitle: Recipes\n', encoding='utf-8')
    updated = prepare(source, 'recipes')
    with pytest.raises(ActivationError, match='health check failed'):
        _promote(service, updated.candidate_id, reload_runtime=lambda _: {'status': 'completed'},
            health_check=lambda _: False)
    previous_admission = service.load_promotion(updated.candidate_id)['receipts']['workflow_admitted']

    other = _named_scenario(tmp_path / 'dev', 'unrelated', marker='keep me')
    other_candidate = prepare(other, 'unrelated')
    _promote(service, other_candidate.candidate_id)
    other_path = tmp_path / 'workspace/scenarios/unrelated/webui.json'
    other_bytes = other_path.read_bytes()
    channel_writes = remote.channel_writes

    failed_id = WorkspaceActivationManager.operation_id(f'stable:{updated.release_digest}')
    recovery = service.recover_promotion_activation(updated.candidate_id, failed_id)
    assert recovery['status'] == 'recovered'
    promoted = _promote(service, updated.candidate_id)
    receipts = service.load_promotion(updated.candidate_id)['receipts']
    assert receipts['workflow_admission_reconciled']['previous_admission'] == previous_admission['admission']
    assert receipts['workflow_admitted'] != previous_admission
    assert promoted.activation.operation_id == recovery['next_operation_id']
    assert {item.project_id for item in promoted.activation.workspace_lock.slots} == {'recipes', 'unrelated'}
    assert other_path.read_bytes() == other_bytes
    assert remote.channel_writes == channel_writes
