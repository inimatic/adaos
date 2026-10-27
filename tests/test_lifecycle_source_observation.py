"""Source observations batch metadata and retain real ownership ambiguity."""
from types import SimpleNamespace

from adaos.services.applications.lifecycle import ApplicationLifecycleService
from adaos.services.applications.store import ApplicationStore


def test_project_observation_does_not_enumerate_unrelated_runtime_or_installations(tmp_path):
    def forbidden():
        raise AssertionError('A Project association does not need runtime admission discovery')
    store = SimpleNamespace(list_applications=lambda: (
        SimpleNamespace(application_id='owner', legacy_project_id='project'),),
        list_runtime_selections=forbidden, list_installations=forbidden)
    service = ApplicationLifecycleService(SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path)), store=store)
    service.snapshot = lambda application_id, **kwargs: {'owner': application_id}
    assert service.for_source('project', 'project') == {'owner': 'owner'}


def test_component_observation_reads_installations_once_not_for_each_definition(tmp_path):
    calls = []
    store = SimpleNamespace(
        list_applications=lambda: tuple(SimpleNamespace(application_id=f'app-{index}', entrypoints=()) for index in range(200)),
        list_runtime_selections=lambda: (),
        list_installations=lambda: calls.append(True) or (
            SimpleNamespace(application_id='app-42', status='active', installed_release_digest='exact'),),
        release_owns_source=lambda application_id, digest, ref:
            (application_id, digest, ref) == ('app-42', 'exact', 'skill:owned'),
    )
    service = ApplicationLifecycleService(SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path)), store=store)
    service.snapshot = lambda application_id, **kwargs: {'owner': application_id}
    assert service.for_source('skill', 'owned') == {'owner': 'app-42'}
    assert calls == [True]


def test_two_real_source_owners_stay_ambiguous(tmp_path):
    store = SimpleNamespace(list_applications=lambda: tuple(
        SimpleNamespace(application_id=identity, entrypoints=({'presentation_ref':'scenario:shared'},))
        for identity in ('first', 'second')),
        list_runtime_selections=lambda: (), list_installations=lambda: ())
    service = ApplicationLifecycleService(SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path)), store=store)
    assert service.for_source('scenario', 'shared')['reason'] == 'ApplicationOwnershipAmbiguous'


def test_verified_ownership_projection_excludes_shared_members(tmp_path, monkeypatch):
    store = ApplicationStore(tmp_path)
    release = SimpleNamespace(project_release=SimpleNamespace(composition_lock=SimpleNamespace(members=(
        SimpleNamespace(ref='skill:owned', lifecycle='owned'),
        SimpleNamespace(ref='skill:dependency', lifecycle='shared'),))))
    monkeypatch.setattr(store, '_verified_release', lambda *_args: release)
    assert store.release_owns_source('app', 'exact', 'skill:owned')
    assert not store.release_owns_source('app', 'exact', 'skill:dependency')
