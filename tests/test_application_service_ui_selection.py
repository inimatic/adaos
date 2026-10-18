"""The browser service gateway follows Core's Application channel authority."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from adaos.services.skill import service_supervisor as services
from adaos.services.applications import store as application_store
from adaos.services.applications import runtime_channel, trial_runtime


@pytest.fixture
def selected(monkeypatch,tmp_path):
    selection=SimpleNamespace(application_id='owned-app',runtime_root_ref='trial:candidate',release_digest='digest')
    owner=SimpleNamespace(paths=SimpleNamespace(state_dir=lambda:tmp_path))
    default=SimpleNamespace(_ctx=owner)
    channel=SimpleNamespace(read=lambda:(selection,))
    @contextmanager
    def execution(root,digest,**kwargs):
        assert root==selection.runtime_root_ref and digest==selection.release_digest
        yield
    channel.execution=execution
    store=SimpleNamespace(list_runtime_selections=lambda:[selection],release_has_component=lambda *args,**kwargs:True)
    monkeypatch.setattr(application_store,'ApplicationStore',lambda path:store)
    monkeypatch.setattr(runtime_channel,'ApplicationRuntimeChannel',lambda *args:channel)
    trial_ctx=SimpleNamespace(paths=SimpleNamespace(state_dir=lambda:tmp_path/'trial'))
    calls=[]
    runtime=SimpleNamespace(ready_manager=lambda name:calls.append(name),context=lambda:trial_ctx)
    monkeypatch.setattr(trial_runtime.NativeTrialRuntime,'resolve',lambda ctx,candidate,digest:runtime)
    monkeypatch.setattr(services,'ServiceSkillSupervisor',lambda:SimpleNamespace(_ctx=trial_ctx,_shutdown_requested=False))
    monkeypatch.setattr(services,'_APPLICATION_SUPERVISORS',{})
    return default,selection,store,channel,trial_ctx,calls


def test_selected_trial_service_has_its_own_verified_runtime_context(selected):
    default,selection,store,channel,trial_ctx,calls=selected
    first=services.selected_application_service_supervisor('owned-service',default)
    assert first is not default and first._ctx is trial_ctx
    assert services.selected_application_service_supervisor('owned-service',default) is first
    assert calls==['owned-service','owned-service']


def test_workspace_and_unowned_services_keep_default_supervisor(selected):
    default,selection,store,channel,*_=selected
    selection.runtime_root_ref='workspace'
    assert services.selected_application_service_supervisor('owned-service',default) is default
    store.release_has_component=lambda *args,**kwargs:False
    assert services.selected_application_service_supervisor('shared-service',default) is default


def test_pending_or_retired_runtime_never_falls_back_to_workspace(selected):
    default,selection,store,channel,*_=selected
    @contextmanager
    def unavailable(*args,**kwargs):
        raise runtime_channel.RuntimeChannelConflict('pending or retired')
        yield
    channel.execution=unavailable
    with pytest.raises(runtime_channel.RuntimeChannelConflict):
        services.selected_application_service_supervisor('owned-service',default)
    assert not services._APPLICATION_SUPERVISORS


def test_retirement_stops_only_obsolete_supervisors_of_this_application(selected):
    import asyncio
    default,selection,store,channel,*_=selected
    previous=services.selected_application_service_supervisor('owned-service',default)
    stopped=[]
    async def stop_previous(): stopped.append('owned')
    async def stop_other(): stopped.append('other')
    previous.shutdown=stop_previous
    state=str(default._ctx.paths.state_dir().resolve())
    other=SimpleNamespace(shutdown=stop_other)
    services._APPLICATION_SUPERVISORS[(state,'other-app','trial:other','other-digest')]=other
    selection.runtime_root_ref='workspace'
    active=services.selected_application_service_supervisor('owned-service',default)
    asyncio.run(services.retire_application_service_supervisors('owned-service',active,default))
    assert stopped==['owned']
    assert list(services._APPLICATION_SUPERVISORS.values())==[other]
