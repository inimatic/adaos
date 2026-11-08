import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import node_api
from adaos.domain.personalization_access import SessionKey, SubjectRef
from adaos.services import desktop_applications, personalization_runtime
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.workspaces import index


@pytest.fixture
def desktop_api(tmp_path, monkeypatch):
    access = PersonalizationAccessService(PersonalizationAccessStore(tmp_path/'access.json'), owner=SubjectRef('user','owner'))
    monkeypatch.setattr(personalization_runtime,'personalization_access_service',lambda *_:access)
    monkeypatch.setattr(personalization_runtime,'current_user_id',lambda:'owner')
    monkeypatch.setattr(node_api,'load_config',lambda:SimpleNamespace(role='hub'))
    monkeypatch.setenv('ADAOS_TOKEN','test-owner')
    from adaos.apps.api import personalization
    monkeypatch.setattr(personalization,'_public_invite_view',lambda value,*_:dict(value,claim_url='https://fixture.test/invite'))
    for desktop in ['a','b']: index.ensure_workspace(desktop)
    policy = DesktopAccessService(access)
    visitor = SubjectRef('user','visitor')
    policy.share(DesktopPrincipal(access.owner),'b',visitor)
    access.put_session(SessionKey(session_id='visitor-session',key_id='test',subject=visitor,expires_at=time.time()+3600))
    credential=policy.issue_credential(DesktopPrincipal(SubjectRef('session','visitor-session'),'visitor-session'))['access_token']
    app=FastAPI();app.include_router(node_api.router,prefix='/api/node')
    with TestClient(app) as client:
        yield client,policy,{'X-AdaOS-Token':'test-owner'},{'X-AdaOS-Desktop-Credential':credential}


def test_participant_cannot_read_or_mutate_desktop_administration(desktop_api):
    client,_,owner,guest=desktop_api
    for path in ['/yjs/home-applications?webspace_id=b','/yjs/webspaces/b/access']:
        assert client.get('/api/node'+path,headers=guest).status_code==403
        assert client.get('/api/node'+path,headers=owner).status_code==200
    assert client.post('/api/node/yjs/webspaces/b/access/invites',json={},headers=guest).status_code==403
    assert client.post('/api/node/yjs/webspaces/b/access/invites/revoke',json={'invite_id':'x'},headers=guest).status_code==403


def test_desktop_access_inventory_and_revoke_stay_within_target_scope(desktop_api):
    client,policy,owner,_=desktop_api
    policy.share(DesktopPrincipal(policy.access.owner),'a',SubjectRef('user','private'))
    view=client.get('/api/node/yjs/webspaces/b/access',headers=owner).json()
    assert all(item['scope']=={'kind':'webspace','id':'b'} for item in view['grants'])
    response=client.post('/api/node/yjs/webspaces/b/access/invites',json={},headers=owner)
    assert response.status_code==200
    invite=response.json()['invite']
    assert invite['scope']=={'kind':'webspace','id':'b'}
    assert client.post('/api/node/yjs/webspaces/a/access/invites/revoke',json={'invite_id':invite['invite_id']},headers=owner).status_code==403
    listed=client.get('/api/node/yjs/webspaces/b/access',headers=owner).json()['invites']
    assert any(item['invite_id']==invite['invite_id'] for item in listed)
    assert client.post('/api/node/yjs/webspaces/b/access/invites/revoke',json={'invite_id':invite['invite_id']},headers=owner).status_code==200
    assert policy.access.store.get_invite(invite['invite_id'])['status']=='revoked'


def test_home_choices_use_subnet_installation_identity_not_node_inventory(monkeypatch):
    apps=[SimpleNamespace(application_id=key,slug=key,display={'title':key},entrypoints=[{'entrypoint_id':'main','presentation_ref':'scenario:'+key}])
          for key in ['installed','removed','not-installed']]
    store=SimpleNamespace(list_applications=lambda:apps,
      list_installations=lambda:[SimpleNamespace(application_id='installed',status='active'),SimpleNamespace(application_id='removed',status='removed')],
      list_runtime_selections=lambda:pytest.fail('Home must not scan runtime selections for unrelated desktops'))
    monkeypatch.setattr(desktop_applications,'get_application_service',lambda _:SimpleNamespace(store=store))
    items=desktop_applications.home_applications()
    assert [item['application_id'] for item in items]==['core:desktop','installed']
    assert all('node_id' not in item for item in items)
    with pytest.raises(ValueError): desktop_applications.resolve_home_application('not-installed')


def test_creation_cannot_reuse_a_deleted_identity(desktop_api):
    client,_,owner,_=desktop_api
    index.delete_workspace('b')
    response=client.post('/api/node/yjs/webspaces',headers=owner,json={'id':'b','title':'Replacement'})
    assert response.status_code==409
    assert response.json()['detail']=='desktop_identity_retired'
    assert index.get_workspace('b') is None


def test_home_change_does_not_navigate_the_shared_document(desktop_api,monkeypatch):
    client,_,owner,_=desktop_api
    from adaos.services.scenario import webspace_runtime
    index.set_workspace_current_scenario_overlay('b','current-application')
    choice={'application_id':'home-app','title':'Home','scenario_id':'home-entry'}
    monkeypatch.setattr(desktop_applications,'home_applications',lambda *_:[choice])
    monkeypatch.setattr(webspace_runtime,'_scenario_exists_for_source_mode',lambda value,**_:bool(value) if value else None)
    async def unexpected(*_,**__): raise AssertionError('Home metadata must not navigate participants')
    monkeypatch.setattr(webspace_runtime,'switch_webspace_scenario',unexpected)
    response=client.patch('/api/node/yjs/webspaces/b',headers=owner,json={'home_application_id':'home-app','title':'Renamed'})
    assert response.status_code==200
    row=index.get_workspace('b')
    assert row.title=='Renamed' and row.effective_home_scenario=='home-entry'
    assert row.current_scenario_overlay=='current-application'
    assert row.home_scenario_ref_overlay['application_id']=='home-app'
    assert client.patch('/api/node/yjs/webspaces/b',headers=owner,json={'home_application_id':'unknown'}).status_code==422
