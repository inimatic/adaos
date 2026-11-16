from types import SimpleNamespace

import pytest
import yaml

from test_sdk_project_composition import project_space, _scenario, _skill
from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.sdk.developer import compositions
from adaos.services.artifact_pipeline import publication
from adaos.services.artifact_pipeline.project_build import review_project_composition


@pytest.fixture
def stale_project(project_space):
    source = _scenario(project_space['scenarios'], 'retouch')
    _skill(project_space['skills'], 'shared_worker')
    compositions.create_for_existing_component('retouch', kind='scenario', component_id='retouch')
    compositions.ensure_dependency('retouch', 'skill:shared_worker', version='==0.1.6')
    active = SimpleNamespace(components=(SimpleNamespace(key='skill:shared_worker', version='0.1.7'),))
    return project_space, source, active


@pytest.mark.parametrize('referenced', [False, True])
def test_composition_review_reports_drift_without_changing_source(stale_project, referenced):
    roots, source, active = stale_project
    if referenced:
        path = source / 'scenario.yaml'
        manifest = yaml.safe_load(path.read_text())
        manifest['depends'] = ['skill:shared_worker']
        path.write_text(yaml.safe_dump(manifest), encoding='utf-8')
    project = roots['projects'] / 'retouch'
    before = (project / 'project.yaml').read_bytes()
    report = review_project_composition(project_dir=project, workspace_root=project.parent.parent,
        active_workspace_lock=active)
    assert report['requires_review'] and report['read_only']
    assert report['findings'] == [{'ref':'skill:shared_worker', 'declared_version':'==0.1.6',
        'active_version':'0.1.7', 'source_required':referenced,
        'code':'DeclaredDependencyVersionDrift',
        'review_action':'review_version' if referenced else 'review_removal'}]
    assert (project / 'project.yaml').read_bytes() == before
    assert report == review_project_composition(project_dir=project, workspace_root=project.parent.parent,
        active_workspace_lock=active)
    project_def = compositions.get('retouch')
    replacement = {**project_def, 'components':{**project_def['components'], 'dependencies':[]}}
    compositions.replace('retouch', replacement, expected_manifest_digest=project_def['manifest_digest'])
    reviewed = review_project_composition(project_dir=project, workspace_root=project.parent.parent,
        active_workspace_lock=active)
    assert not reviewed['requires_review']
    assert reviewed['review_digest'] != report['review_digest']


def test_candidate_preparation_checks_composition_before_build_or_remote_write(stale_project, monkeypatch):
    roots, _, active = stale_project
    service = object.__new__(publication.ArtifactPublicationService)
    service.workspace_root = roots['projects'].parent
    monkeypatch.setattr(publication, 'load_workspace_lock', lambda *_: active)
    monkeypatch.setattr(publication, 'build_workspace_project_release', lambda **_: pytest.fail('Built a stale composition'))
    with pytest.raises(publication.PublicationError, match='composition requires reconciliation.*0.1.6.*0.1.7'):
        service.prepare_project_candidate(project_id='retouch', project_dir=roots['projects']/'retouch',
            source_workspace_root=roots['projects'].parent,
            source_ref=ArtifactSourceRef(forge='github', repository='example/source', revision='a'*40,
                path_scope=('scenarios/retouch/',)), change_ids=('repair',), validation_evidence={'status':'passed'})


def test_ensure_dependency_never_reports_success_for_a_different_pin(stale_project):
    _, _, _ = stale_project
    before = compositions.get('retouch')
    with pytest.raises(compositions.ProjectCompositionError, match='already declares'):
        compositions.ensure_dependency('retouch', 'skill:shared_worker', version='==0.1.7')
    assert compositions.get('retouch')['manifest_digest'] == before['manifest_digest']
    assert compositions.ensure_dependency('retouch','skill:shared_worker', version='==0.1.6')['idempotent']
