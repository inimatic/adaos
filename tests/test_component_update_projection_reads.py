from types import SimpleNamespace

import pytest

from adaos.services.applications.development import ApplicationDevelopmentCoordinator
from adaos.services.component_updates import ComponentUpdateService


def test_publication_projection_skips_retained_build_results_and_invalidates(tmp_path, monkeypatch):
    from adaos.services.artifact_pipeline.storage import atomic_write_json
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    authority = dict(actor_ref='user:owner', subnet_ref='subnet:home', expected_revision=1)
    coordinator.execute('create_trial', 'other', capability='applications.develop', idempotency_key='trial',
        intent={}, callback=lambda: {'large_build_result': [0] * 10000}, **authority)
    published = coordinator.execute('publish_to_registry', 'mail', capability='applications.publish',
        idempotency_key='publish', intent={'release_digest': 'exact-release', 'release_notes': 'Reviewed changes'},
        callback=lambda: {'large_workspace_result': [0] * 10000}, **authority)
    coordinator.summaries()  # Cold projection is prepared once for each authority file.
    original = coordinator._read
    reads = []
    def read(path):
        reads.append(path)
        assert path == coordinator._path(published['operation_id'])
        return original(path)
    monkeypatch.setattr(coordinator, '_read', read)
    first = coordinator.publication_summaries('mail')
    assert len(reads) == 1
    assert first[0]['intent'] == {'release_digest': 'exact-release', 'release_notes': 'Reviewed changes'}
    assert 'result' not in first[0]
    assert coordinator.publication_summaries('mail') == first and len(reads) == 1
    published['status'] = 'unknown'
    atomic_write_json(coordinator._path(published['operation_id']), published)
    assert coordinator.publication_summaries('mail') == []


@pytest.mark.parametrize('candidate_changes', [{}, {'release_digest': 'foreign'},
    {'candidate_id': 'foreign'}, {'package_digest': ''}])
@pytest.mark.parametrize('native', [True, False])
def test_native_notice_reconciliation_never_opens_full_builder_or_journals(tmp_path, monkeypatch, candidate_changes, native):
    from adaos.sdk.builder import applications, workflow
    from adaos.sdk.developer import projects
    from adaos.services.applications import store as stores
    selected = SimpleNamespace(webspace_id='desktop', application_id='sample', source='stable_installation',
        release_digest='exact-release', runtime_root_ref='workspace', revision=3)
    foreign = SimpleNamespace(webspace_id='desktop', application_id='foreign')
    def release(app, _digest):
        assert app == 'sample'
        return SimpleNamespace(accepted_candidate_id='exact-candidate', release_digest='exact-release',
            project_release=SimpleNamespace(version='1.0.0'))
    store = SimpleNamespace(list_runtime_selections=lambda: [selected, foreign], get_release=release,
        get_application=lambda app: SimpleNamespace(publisher_ref='subnet:home', display={'title': app}))
    monkeypatch.setattr(stores, 'ApplicationStore', lambda _: store)
    monkeypatch.setattr(applications, '_local_subnet_ref', lambda: 'subnet:home')
    monkeypatch.setattr(applications, '_primary_scenario', lambda app: app.display['title'])
    monkeypatch.setattr(workflow, 'get_state', lambda *_: pytest.fail('Do not expand the authoring graph'))
    monkeypatch.setattr(ApplicationDevelopmentCoordinator, 'list', lambda *_: pytest.fail('Do not load full journals'))
    candidate = {'candidate_id': 'exact-candidate', 'release_digest': 'exact-release',
        'package_digest': 'exact-package', 'status': 'accepted', **candidate_changes}
    from adaos.services.artifact_pipeline.candidates import CandidateStore
    def load(self, _identifier):
        if native:
            return SimpleNamespace(**candidate)
        raise FileNotFoundError
    monkeypatch.setattr(CandidateStore, 'load', load)
    def legacy(_identifier):
        assert not native, 'Native notice reads must not open the rich promotion inspector'
        return {'candidate': candidate}
    monkeypatch.setattr(projects, 'get_candidate', legacy)
    notices = []
    monkeypatch.setattr(ComponentUpdateService, 'record_aprobation', lambda self, **kw: notices.append(kw) or {'ok': True})
    assert ComponentUpdateService(state_dir=tmp_path).reconcile_local_trials('desktop', application_id='sample') == (0 if candidate_changes else 1)
    if candidate_changes:
        assert notices == []
        return
    assert notices[0]['component_id'] == 'sample'
    assert notices[0]['aprobation']['trial']['candidate_digest'] == 'exact-package'
    assert notices[0]['aprobation']['trial']['status'] == 'published'
