import json

import pytest

from test_application_service import _application, _release, service
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.applications.store import ApplicationStore, ApplicationStoreError
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.capability_binding_state.registry_projection import (
    SemanticRegistryProjection, SemanticRegistryProjectionError, _empty_index,
)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    projection = SemanticRegistryProjection(registry_root=tmp_path/'registry', state_dir=tmp_path/'state')
    app = _application()
    stable = _release(version='1.0.0')
    beta = _release(version='1.1.0')
    index = _empty_index()
    # CBS compilation is qualified separately. These sealed semantic records
    # isolate the channel transport and real ApplicationStore hydration here.
    for release in (stable, beta):
        semantic = {'project_id': app.legacy_project_id, 'project_release_digest': release.release_digest}
        semantic['projection_digest'] = canonical_payload_digest(semantic)
        relative = 'semantic/applications/recipes/' + release.release_digest[7:] + '.json'
        atomic_write_json(tmp_path/'registry'/relative, semantic)
        index['application_releases']['project:recipes@'+release.release_digest] = {
            'path': relative, 'projection_digest': semantic['projection_digest']}
    index.pop('index_digest')
    index['index_digest'] = canonical_payload_digest(index)
    atomic_write_json(tmp_path/'registry/semantic/index.json', index)
    from adaos.services.applications.cbs import ApplicationCBSService
    monkeypatch.setattr(ApplicationCBSService, 'import_semantic_requirement_set', lambda self, **kwargs: {
        'application_ref': 'application:app_recipes', 'project_release_digest': kwargs['semantic_release']['project_release_digest'],
        'compilation_digest': 'sha256:'+'c'*64, 'requirement_set_digest': 'sha256:'+'d'*64})
    return projection, app, stable, beta


def test_public_beta_round_trip_preserves_stable_and_creates_no_installation(catalog, tmp_path):
    projection, app, stable, beta = catalog
    projection.prepare_public_application(app, stable)
    stable_record = projection.application_catalog_path.read_bytes()
    before = json.loads(stable_record)['applications'][app.application_id]
    projection.prepare_public_application(app, beta, channel='prerelease')
    entry = json.loads(projection.application_catalog_path.read_bytes())['applications'][app.application_id]
    assert {key:entry[key] for key in before} == before
    assert entry['prerelease']['prerelease_release_digest'] == beta.release_digest
    store = ApplicationStore(tmp_path/'consumer')
    result = projection.import_public_applications(application_store=store, local_publisher_ref='subnet:receiver')
    assert result['application_count'] == 1
    assert store.get_channels(app.application_id)['channels'] == {'stable':stable.release_digest,'prerelease':beta.release_digest}
    assert store.get_release(app.application_id,beta.release_digest) == beta
    before = store.get_channels(app.application_id)
    projection.import_public_applications(application_store=store, local_publisher_ref='subnet:receiver')
    assert store.get_channels(app.application_id) == before
    assert not store.list_installations()
    assert not store.list_runtime_selections()


def test_finalize_same_release_uses_a_separate_immutable_catalog_record(catalog, tmp_path):
    projection, app, stable, beta = catalog
    projection.prepare_public_application(app,stable)
    projection.prepare_public_application(app,beta,channel='prerelease')
    beta_path = projection._public_application_release_path(app.application_id,beta.release_digest,channel='prerelease')
    beta_bytes = beta_path.read_bytes()
    store = ApplicationStore(tmp_path/'consumer')
    projection.import_public_applications(application_store=store,local_publisher_ref='subnet:receiver')
    projection.prepare_public_application(app,beta)
    assert beta_path.read_bytes() == beta_bytes
    projection.import_public_applications(application_store=store,local_publisher_ref='subnet:receiver')
    assert store.get_channels(app.application_id)['channels'] == {'stable':beta.release_digest}


def test_beta_requires_a_stable_baseline_before_catalog_or_store_writes(catalog, tmp_path):
    projection, app, _, beta = catalog
    with pytest.raises(SemanticRegistryProjectionError,match='Stable baseline'):
        projection.prepare_public_application(app,beta,channel='prerelease')
    assert not projection.application_catalog_path.exists()
    store = ApplicationStore(tmp_path/'consumer')
    with pytest.raises(ApplicationStoreError,match='Stable baseline'):
        store.import_public_registry_release(app,beta,prerelease_release_digest=beta.release_digest)
    assert not store.list_applications()


def test_resealed_beta_pointer_cannot_claim_another_release(catalog, tmp_path):
    projection, app, stable, beta = catalog
    projection.prepare_public_application(app,stable)
    projection.prepare_public_application(app,beta,channel='prerelease')
    value = json.loads(projection.application_catalog_path.read_bytes())
    value['applications'][app.application_id]['prerelease']['prerelease_release_digest'] = stable.release_digest
    value.pop('index_digest');value['index_digest'] = canonical_payload_digest(value)
    atomic_write_json(projection.application_catalog_path,value)
    store = ApplicationStore(tmp_path/'consumer')
    with pytest.raises(SemanticRegistryProjectionError,match='metadata mismatch'):
        projection.import_public_applications(application_store=store,local_publisher_ref='subnet:receiver')
    assert store.get_channels(app.application_id)['channels'] == {'stable':stable.release_digest}


def test_prerelease_receipt_cannot_satisfy_later_stable_source_publication(service):
    from adaos.services.applications.source_projection import GitStableSourcePublisher, StableSourceProjectionService
    baseline = service.register_release(_release())
    service.move_channel(baseline.application_id,'stable',baseline.release_digest,
        publisher_ref=baseline.publisher_ref,expected_release_digest=None)
    release = service.register_release(_release(version='1.1.0'))
    service.move_channel(release.application_id,'prerelease',release.release_digest,
        publisher_ref=release.publisher_ref,expected_release_digest=None)
    calls=[]
    def publish(candidate_id, **kwargs):
        calls.append(kwargs)
        return {'candidate_id':candidate_id,'release_digest':release.release_digest,
            'publication':{'branch':'main','commit':'a'*40}}
    projection = StableSourceProjectionService(service,publisher=GitStableSourcePublisher(
        publish,repository='registry',remote='registry',branch='main'))
    beta=projection.publish(release.application_id,release.release_digest,publisher_ref=release.publisher_ref,
        release_notes='',channel='prerelease')
    assert calls[0]['channel']=='prerelease'
    service.move_channel(release.application_id,'stable',release.release_digest,
        publisher_ref=release.publisher_ref,expected_release_digest=baseline.release_digest)
    stable=projection.publish(release.application_id,release.release_digest,publisher_ref=release.publisher_ref,release_notes='')
    assert len(calls)==2 and 'channel' not in calls[1]
    assert beta['channel']=='prerelease' and stable['channel']=='stable'
