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
    for desktop in ['a','b']:
        index.ensure_workspace(desktop)
    policy = DesktopAccessService(access)
    visitor = SubjectRef('user','visitor')
    policy.share(DesktopPrincipal(access.owner),'b',visitor)
    access.put_session(SessionKey(session_id='visitor-session',key_id='test',subject=visitor,expires_at=time.time()+3600))
    credential=policy.issue_credential(DesktopPrincipal(SubjectRef('session','visitor-session'),'visitor-session'))['access_token']
    app=FastAPI()
    app.include_router(node_api.router,prefix='/api/node')
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
    with pytest.raises(ValueError):
        desktop_applications.resolve_home_application('not-installed')


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


def _visitor_grant(client, owner):
    return next(item for item in client.get('/api/node/yjs/webspaces/b/access', headers=owner).json()['grants']
                if item['subject']['id'] == 'visitor')


def test_participant_presentation_is_readonly_until_explicitly_delegated(desktop_api):
    client, _, owner, guest = desktop_api
    for path, body in [('desktop/pinned-applications', {'pinnedApplications': []}),
                       ('desktop/pinned-widgets', {'pinnedWidgets': []})]:
        assert client.post('/api/node/yjs/webspaces/b/' + path, headers=guest, json=body).status_code == 403
    assert client.patch('/api/node/yjs/webspaces/b/desktop', headers=guest, json={'iconOrder': []}).status_code == 403
    grant = _visitor_grant(client, owner)
    result = client.post('/api/node/yjs/webspaces/b/access/permissions', headers=owner,
                         json={'grant_id': grant['grant_id'], 'permissions': ['desktop.rename']})
    assert result.status_code == 200
    assert client.patch('/api/node/yjs/webspaces/b', headers=guest, json={'title': 'My desk'}).status_code == 200
    assert index.get_workspace('b').title == 'My desk'
    assert client.patch('/api/node/yjs/webspaces/b', headers=guest,
                        json={'title': 'Escalated', 'home_scenario': 'other'}).status_code == 403
    assert index.get_workspace('b').title == 'My desk'
    assert client.patch('/api/node/yjs/webspaces/a', headers=guest, json={'title': 'Private'}).status_code == 403


def test_layout_grant_does_not_authorize_structural_changes_or_escalation(desktop_api, monkeypatch):
    client, policy, owner, guest = desktop_api
    grant = _visitor_grant(client, owner)
    path = '/api/node/yjs/webspaces/b/access/permissions'
    assert client.post(path, headers=guest, json={'grant_id': grant['grant_id'], 'permissions': ['desktop.rename']}).status_code == 403
    assert client.post(path, headers=owner, json={'grant_id': grant['grant_id'], 'permissions': ['subnet.admin']}).status_code == 422
    assert client.post(path, headers=owner, json={'grant_id': grant['grant_id'], 'permissions': ['desktop.layout.reorder']}).status_code == 200
    writes = []
    monkeypatch.setattr(node_api, 'WebDesktopService', lambda: SimpleNamespace(
        set_icon_order_with_live_room=lambda values, target: writes.append((target, values)),
        get_snapshot_async=lambda _: _snapshot()))
    async def _snapshot():
        return SimpleNamespace(to_dict=lambda: {})
    assert client.patch('/api/node/yjs/webspaces/b/desktop', headers=guest, json={'iconOrder': ['one']}).status_code == 200
    assert writes == [('b', ['one'])]
    assert client.patch('/api/node/yjs/webspaces/b/desktop', headers=guest,
                        json={'iconOrder': ['bad'], 'pageSchema': {'widgets': []}}).status_code == 403
    assert writes == [('b', ['one'])]
    policy.set_permissions(DesktopPrincipal(policy.access.owner), 'b', grant['grant_id'], [])
    assert client.patch('/api/node/yjs/webspaces/b/desktop', headers=guest, json={'iconOrder': []}).status_code == 403


def test_endpoint_rename_is_delegated_only_for_the_verified_own_device(desktop_api, monkeypatch):
    from adaos.domain.personalization_access import DeviceKey
    from adaos.services import device_access
    client, policy, owner, guest = desktop_api
    policy.access.store.put_device_key(DeviceKey(user_id='visitor', device_id='browser-own', key_id='key', public_key_ref='public'))
    policy.access.store.update_session('visitor-session', {'device_id': 'browser-own'})
    grant = _visitor_grant(client, owner)
    policy.set_permissions(DesktopPrincipal(policy.access.owner), 'b', grant['grant_id'], ['endpoint.rename'])
    renamed = []
    monkeypatch.setattr(device_access, 'rename_device', lambda ref, name: renamed.append((ref, name)) or {'ok': True})
    base = '/api/node/yjs/webspaces/b/endpoints/'
    assert client.post(base + 'browser-other/name', headers=guest, json={'name': 'Other'}).status_code == 403
    assert client.post(base + 'browser-own/name', headers=guest, json={'name': 'My browser'}).status_code == 200
    assert renamed == [('browser:browser-own', 'My browser')]
    assert not policy.allowed(DesktopPrincipal(SubjectRef('user', 'visitor')), 'b', administer=True)


def test_application_checklist_uses_the_authenticated_participant_and_pin_permission(desktop_api, monkeypatch):
    client, policy, owner, guest = desktop_api
    reads, writes = [], []
    def choices(target, actor):
        reads.append((target, actor.actor.ref()))
        return [{'application_id': 'available', 'title': 'Available', 'pinned': False}]
    def save(target, selected, actor):
        writes.append((target, selected, actor.actor.ref()))
        return []
    monkeypatch.setattr(desktop_applications, 'desktop_application_choices', choices)
    monkeypatch.setattr(desktop_applications, 'set_desktop_application_pins', save)
    path = '/api/node/yjs/webspaces/b/desktop/pinned-applications'
    response = client.get(path, headers=guest)
    assert response.status_code == 200 and response.json()['can_edit'] is False
    assert reads == [('b', 'session:visitor-session')]
    assert client.post(path, headers=guest, json={'application_ids': ['available']}).status_code == 403
    grant = _visitor_grant(client, owner)
    policy.set_permissions(DesktopPrincipal(policy.access.owner), 'b', grant['grant_id'], ['desktop.applications.pin'])
    assert client.get(path, headers=guest).json()['can_edit'] is True
    assert client.post(path, headers=guest, json={'application_ids': ['available']}).status_code == 200
    assert writes == [('b', ['available'], 'session:visitor-session')]
