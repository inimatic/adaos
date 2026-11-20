import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api.desktop_access import connection_principal, require_desktop_caller
from adaos.domain.personalization_access import Membership, ScopeRef, SessionKey, SubjectRef
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal, desktop_event_allowed, require_desktop_command
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.policy.caller import CallerAccessDenied, verified_caller


@pytest.fixture
def authority(tmp_path, monkeypatch):
    import adaos.services.personalization_runtime as runtime
    access = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / 'access.json'), owner=SubjectRef('user', 'owner'))
    policy = DesktopAccessService(access, subnet_id='subnet')
    monkeypatch.setattr(runtime, 'personalization_access_service', lambda _ctx=None: access)
    monkeypatch.setattr(runtime, 'current_user_id', lambda _ctx=None: 'owner')
    monkeypatch.setattr(runtime, 'current_subnet_id', lambda _ctx=None: 'subnet')
    monkeypatch.setenv('ADAOS_TOKEN', 'owner-node-secret')
    from adaos.services.workspaces import index
    monkeypatch.setattr(index, 'get_workspace', lambda target: SimpleNamespace(workspace_id=target))
    return policy


def credential(policy, actor):
    session_id = 'session-' + actor.id
    policy.access.put_session(SessionKey(session_id=session_id, key_id='test', subject=actor, expires_at=time.time() + 3600))
    return policy.issue_credential(DesktopPrincipal(SubjectRef('session', session_id), session_id))['access_token']


@pytest.fixture
def client(authority):
    app = FastAPI()
    # Use the production dependency at the production path shape. Handlers
    # have no effect, making failures attributable to admission itself.
    async def accepted(): return {'ok': True}
    from fastapi import Request
    from adaos.apps.api.auth import require_tool_caller
    async def tool_identity(request: Request):
        actor = request.state.adaos_verified_caller
        scope = request.state.adaos_verified_caller_scope
        return {'actor': actor.ref(), 'scope': scope.ref()}
    app.add_api_route('/api/tools/call', tool_identity, methods=['POST'], dependencies=[Depends(require_tool_caller)])
    for suffix, method in [('', 'GET'), ('', 'PATCH'), ('', 'DELETE'), ('/scenario', 'POST'),
                           ('/go-home', 'POST'), ('/reload', 'POST'), ('/reset', 'POST'),
                           ('/restore', 'POST'), ('/set-home', 'POST'), ('/access', 'POST'),
                           ('/access/revoke', 'POST'), ('/desktop', 'PATCH')]:
        app.add_api_route('/api/node/yjs/webspaces/{webspace_id}' + suffix, accepted,
                          methods=[method], dependencies=[Depends(require_desktop_caller)])
    return TestClient(app)


@pytest.mark.parametrize('role', ['owner', 'co_owner', 'admin'])
def test_http_three_administrators_have_same_system_matrix(authority, client, role):
    actor = SubjectRef('user', role)
    authority.access.put_membership(Membership(subject=actor, scope=ScopeRef('subnet', 'subnet'), role=role))
    headers = {'X-AdaOS-Desktop-Credential': credential(authority, actor)}
    for suffix, method in [('', 'PATCH'), ('', 'DELETE'), ('/reload', 'POST'), ('/reset', 'POST'),
                           ('/restore', 'POST'), ('/set-home', 'POST'), ('/access', 'POST'), ('/access/revoke', 'POST')]:
        assert client.request(method, '/api/node/yjs/webspaces/b' + suffix, headers=headers, json={}).status_code == 200


def test_invitee_cannot_administer_or_address_another_desktop(authority, client):
    actor = SubjectRef('user', 'visitor')
    authority.share(DesktopPrincipal(authority.access.owner), 'b', actor)
    headers = {'X-AdaOS-Desktop-Credential': credential(authority, actor)}
    assert client.get('/api/node/yjs/webspaces/b', headers=headers).status_code == 200
    assert client.get('/api/node/yjs/webspaces/a', headers=headers).status_code == 403
    for suffix, method in [('', 'PATCH'), ('', 'DELETE'), ('/reload', 'POST'), ('/reset', 'POST'),
                           ('/restore', 'POST'), ('/set-home', 'POST'), ('/access', 'POST'), ('/access/revoke', 'POST')]:
        assert client.request(method, '/api/node/yjs/webspaces/b' + suffix, headers=headers,
                              json={'actor': 'owner', 'role': 'owner'}).status_code == 403
    assert client.post('/api/node/yjs/webspaces/b/scenario', headers=headers, json={}).status_code == 200
    assert client.patch('/api/node/yjs/webspaces/b/desktop', headers=headers, json={}).status_code == 200
    assert client.post('/api/node/yjs/webspaces/b/scenario', headers=headers, json={'set_home': True}).status_code == 403


def test_bad_desktop_credential_never_falls_back_to_owner_node_token(authority, client):
    headers = {'X-AdaOS-Token': 'owner-node-secret', 'X-AdaOS-Desktop-Credential': 'adaos-desktop-v1.fake.secret'}
    assert client.get('/api/node/yjs/webspaces/a', headers=headers).status_code == 403
    headers = {'X-AdaOS-Token': 'owner-node-secret', 'X-AdaOS-Route-Authenticated': '1',
               'X-AdaOS-Route-Principal': 'unverified'}
    assert client.get('/api/node/yjs/webspaces/a', headers=headers).status_code == 401


def test_raw_ws_p2p_commands_cannot_wrap_system_events_or_spoof_target(authority):
    visitor = DesktopPrincipal(SubjectRef('user', 'visitor'))
    authority.share(DesktopPrincipal(authority.access.owner), 'b', visitor.actor)
    require_desktop_command('desktop.scenario.set', {'webspace_id': 'b'}, 'a', visitor)
    require_desktop_command('desktop.navigation.set', {'webspace_id': 'b'}, 'a', visitor)
    attacks = [
        ('desktop.webspace.delete', {'webspace_id': 'b', 'role': 'owner'}),
        ('desktop.scenario.set', {'webspace_id': 'b', 'persist_home': True}),
        ('desktop.navigation.set', {'webspace_id': 'a'}),
        ('desktop.webspace.use', {'webspace_id': 'b', 'id': 'a'}),
        ('skill.event.publish', {'webspace_id': 'b', 'event_type': 'desktop.webspace.delete', 'payload': {'id': 'b'}}),
        ('skill.event.publish', {'webspace_id': 'b', 'event_type': 'desktop.scenario.set', 'payload': {'webspace_id': 'a'}}),
        ('skill.event.publish', {'webspace_id': 'b', 'event_type': 'sys.ystore.reset'}),
        ('desktop.scenario.set', {'webspace_id': 'b', '_meta': {'webspace_id': 'a'}}),
    ]
    for kind, payload in attacks:
        with pytest.raises(CallerAccessDenied):
            require_desktop_command(kind, payload, 'b', visitor)
    assert not desktop_event_allowed(visitor, {'kind': 'desktop.changed', 'payload': {'webspace_id': 'a'}})
    assert not desktop_event_allowed(visitor, {'kind': 'node.catalog', 'payload': {'items': [{'id': 'a'}]}})
    assert desktop_event_allowed(visitor, {'kind': 'desktop.changed', 'payload': {'webspace_id': 'b'}})
    from adaos.services.desktop_access import filter_desktop_topics
    assert filter_desktop_topics(visitor, ['webio.stream.a.private', 'webio.yjs.b.shared']) == ['webio.yjs.b.shared']
    assert not desktop_event_allowed(visitor, {'kind': 'webio.stream.a.private', 'payload': {'webspace_id': 'b'}})


def test_sdk_catalog_and_system_methods_require_verified_scope(authority, monkeypatch):
    from adaos.sdk.web import webspace
    monkeypatch.setattr(webspace, 'WebspaceService', lambda: SimpleNamespace(list=lambda **_: [SimpleNamespace(id='a'), SimpleNamespace(id='b')]))
    visitor = SubjectRef('user', 'visitor')
    authority.share(DesktopPrincipal(authority.access.owner), 'b', visitor)
    with verified_caller(visitor):
        assert [item.id for item in webspace.webspace_list()] == ['b']
        with pytest.raises(CallerAccessDenied):
            asyncio.run(webspace.webspace_delete('b'))
    with verified_caller(None), pytest.raises(CallerAccessDenied):
        webspace.webspace_list()


def test_idle_attachment_is_closed_after_revocation(authority):
    from adaos.services.desktop_access import watch_desktop_access
    visitor = DesktopPrincipal(SubjectRef('user', 'visitor'))
    owner = DesktopPrincipal(authority.access.owner)
    grant = authority.share(owner, 'b', visitor.actor)
    async def run():
        closed = asyncio.Event()
        async def close(): closed.set()
        watcher = asyncio.create_task(watch_desktop_access(visitor, 'b', close))
        authority.revoke(owner, 'b', grant['grant_id'])
        await asyncio.wait_for(closed.wait(), 3)
        await watcher
    asyncio.run(run())


def test_public_desktop_claim_cannot_reuse_an_existing_privileged_session(authority, monkeypatch):
    from adaos.apps.api import personalization
    privileged = credential(authority, authority.access.owner)
    previous = authority.access.store.get_session('session-owner')
    invite = authority.invite(DesktopPrincipal(authority.access.owner), 'b', max_sessions=3)
    monkeypatch.setattr(personalization, '_access', lambda _: authority.access)
    body = personalization.InviteClaimRequest(subject_kind='user', subject_id='owner',
                                               session_id='session-owner', device_id='claimed-device')
    result = personalization.claim_invite(invite['invite_id'], body, ctx=SimpleNamespace())
    assert result['session_id'] != 'session-owner'
    assert authority.access.store.get_session('session-owner') == previous
    assert authority.authenticate(privileged).actor.id == 'session-owner'
    participant = authority.authenticate(result['desktop_credential']['access_token'])
    assert participant.actor.kind == 'session'
    authority.require(participant, 'b')
    assert not authority.allowed(participant, 'a')
    assert not authority.allowed(participant, 'b', administer=True)


@pytest.mark.parametrize('kind', ['device_pairing_link', 'admin_recovery_link'])
def test_device_invite_returns_new_authenticated_session_and_revocation_cuts_it(authority, monkeypatch, kind):
    from adaos.apps.api import personalization
    previous_token = credential(authority, authority.access.owner)
    previous = authority.access.store.get_session('session-owner')
    args = dict(invite_id='browser-invite', subject=authority.access.owner,
                scope=ScopeRef('subnet', 'subnet'), issued_by=authority.access.owner,
                expires_at=time.time() + 600)
    if kind == 'device_pairing_link':
        authority.access.create_device_pairing_link(**args, role='admin')
    else:
        authority.access.create_admin_recovery_link(**args, recovery_id='recover-browser')
    monkeypatch.setattr(personalization, '_access', lambda _: authority.access)
    monkeypatch.setattr(personalization, '_sync_browser_device_link', lambda *args, **kwargs: None)
    result = personalization.claim_invite('browser-invite', personalization.InviteClaimRequest(
        subject_id='owner', session_id='session-owner', device_id='new-browser'), ctx=SimpleNamespace())

    assert result['session_id'] != 'session-owner'
    assert result['device_id'] == 'new-browser'
    assert authority.access.store.get_session('session-owner') == previous
    assert authority.authenticate(previous_token).session_id == 'session-owner'
    principal = authority.authenticate(result['desktop_credential']['access_token'])
    assert principal.session_id == result['session_id']
    assert authority.access.store.get_session(principal.session_id)['subject'] == {'kind': 'user', 'id': 'owner'}
    authority.require(principal, 'b', administer=True)
    authority.access.revoke_device('new-browser', actor=authority.access.owner)
    with pytest.raises(CallerAccessDenied):
        authority.authenticate(result['desktop_credential']['access_token'])


def test_pairing_credential_preserves_existing_member_rights_despite_invite_display_role(authority, monkeypatch):
    from adaos.apps.api import personalization
    subject = SubjectRef('user', 'member')
    authority.access.put_membership(Membership(subject=subject, scope=ScopeRef('subnet', 'subnet'), role='member'))
    authority.access.create_device_pairing_link(invite_id='member-browser', subject=subject,
        scope=ScopeRef('subnet', 'subnet'), role='admin', issued_by=authority.access.owner,
        expires_at=time.time() + 600)
    monkeypatch.setattr(personalization, '_access', lambda _: authority.access)
    monkeypatch.setattr(personalization, '_sync_browser_device_link', lambda *args, **kwargs: None)
    result = personalization.claim_invite('member-browser', personalization.InviteClaimRequest(
        subject_id='member', device_id='member-device'), ctx=SimpleNamespace())
    principal = authority.authenticate(result['desktop_credential']['access_token'])
    authority.require(principal, 'b')
    assert not authority.allowed(principal, 'b', administer=True)


def test_device_claim_rolls_back_when_authentication_cannot_be_issued(authority, monkeypatch):
    from adaos.apps.api import personalization
    authority.access.create_device_pairing_link(invite_id='atomic-browser', subject=authority.access.owner,
        scope=ScopeRef('subnet', 'subnet'), role='admin', issued_by=authority.access.owner,
        expires_at=time.time() + 600)
    monkeypatch.setattr(personalization, '_access', lambda _: authority.access)
    def fail(*args, **kwargs): raise RuntimeError('credential storage unavailable')
    monkeypatch.setattr(DesktopAccessService, 'issue_credential', fail)
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        personalization.claim_invite('atomic-browser', personalization.InviteClaimRequest(
            subject_id='owner', device_id='atomic-device'), ctx=SimpleNamespace())
    assert authority.access.store.get_invite('atomic-browser')['status'] == 'pending'
    assert authority.access.store.get_device_key('atomic-device') is None


@pytest.mark.parametrize('kind', ['device_pairing_link','admin_recovery_link'])
def test_lost_claim_response_replays_only_to_the_original_browser(authority, monkeypatch, kind):
    from adaos.apps.api import personalization
    from fastapi import HTTPException
    args=dict(invite_id='retry-browser',subject=authority.access.owner,scope=ScopeRef('subnet','subnet'),
              issued_by=authority.access.owner,expires_at=time.time()+600)
    if kind=='device_pairing_link':authority.access.create_device_pairing_link(**args,role='admin')
    else:authority.access.create_admin_recovery_link(**args,recovery_id='retry-recovery')
    monkeypatch.setattr(personalization,'_access',lambda _:authority.access)
    monkeypatch.setattr(personalization,'_sync_browser_device_link',lambda *args,**kwargs:None)
    body=personalization.InviteClaimRequest(subject_id='owner',device_id='retry-device',claim_secret='a'*43)
    first=personalization.claim_invite('retry-browser',body,ctx=SimpleNamespace())
    repeated=personalization.claim_invite('retry-browser',body,ctx=SimpleNamespace())
    assert repeated['session_id']==first['session_id']
    assert repeated['desktop_credential']==first['desktop_credential']
    assert authority.access.store.get_invite('retry-browser')['claim_count']==1
    assert '_browser_claim' not in repeated['invite']
    assert 'desktop_credential' not in repeated['session']
    with pytest.raises(HTTPException):
        personalization.claim_invite('retry-browser',body.model_copy(update={'claim_secret':'b'*43}),ctx=SimpleNamespace())
    with pytest.raises(HTTPException):
        personalization.claim_invite('retry-browser',body.model_copy(update={'claim_secret':None}),ctx=SimpleNamespace())
    authority.access.revoke_device('retry-device',actor=authority.access.owner)
    with pytest.raises(HTTPException):
        personalization.claim_invite('retry-browser',body,ctx=SimpleNamespace())


def test_profile_header_uses_verified_session_identity_and_cannot_read_owner_cache(authority, monkeypatch):
    from adaos.apps.api import personalization
    from adaos.services.agent_context import get_ctx
    from adaos.services.user.profile import UserProfileService
    monkeypatch.setattr(personalization, '_access', lambda _: authority.access)
    monkeypatch.setattr(UserProfileService, 'current_user_id', lambda _: 'owner')
    profile = UserProfileService(get_ctx(), access=authority.access)
    monkeypatch.setattr(personalization, '_profile', lambda _: profile)
    owner_header = {'display_name': 'Private Owner', 'avatar_ref': '/private-owner-avatar'}
    monkeypatch.setattr(personalization.personalization_runtime, 'current_user_header_settings', lambda _: dict(owner_header))
    authority.access.put_membership(Membership(subject=SubjectRef('user','member'), scope=ScopeRef('subnet','subnet'), role='member'))
    profile.update_profile({'display_name':'Actual Member'}, user_id='member', actor=SubjectRef('user','member'))
    app = FastAPI();app.include_router(personalization.router,prefix='/api');browser=TestClient(app)
    bearer = credential(authority, SubjectRef('user','member'))
    response = browser.get('/api/personalization/current-user/header-settings', headers={
        'X-AdaOS-Desktop-Credential':bearer,'X-AdaOS-Token':'owner-node-secret'})
    assert response.status_code == 200
    settings = response.json()['settings']
    assert settings['display_name'] == 'Actual Member'
    assert settings.get('avatar_ref') != '/private-owner-avatar'
    assert settings['role_status']['value'] == 'member'
    assert settings['identity_source'] == 'authenticated_session'
    authority.access.revoke_session('session-member', actor=authority.access.owner)
    assert browser.get('/api/personalization/current-user/header-settings', headers={
        'X-AdaOS-Desktop-Credential':bearer,'X-AdaOS-Token':'owner-node-secret'}).status_code == 401


def test_guest_header_has_no_owner_profile_and_bad_bearer_never_falls_back(authority, monkeypatch):
    from adaos.apps.api import personalization
    monkeypatch.setattr(personalization, '_access', lambda _: authority.access)
    def private(*args): raise AssertionError('owner profile must not be read')
    monkeypatch.setattr(personalization.personalization_runtime, 'current_user_header_settings', private)
    app=FastAPI();app.include_router(personalization.router,prefix='/api');browser=TestClient(app)
    guest = SubjectRef('session','guest')
    authority.access.put_session(SessionKey(session_id='guest',key_id='guest',subject=guest,expires_at=time.time()+600))
    token=authority.issue_credential(DesktopPrincipal(guest,'guest'))['access_token']
    response=browser.get('/api/personalization/current-user/header-settings',headers={'X-AdaOS-Desktop-Credential':token})
    assert response.status_code == 200
    assert response.json()['settings']['display_name'] == 'Guest'
    assert response.json()['settings']['role_status']['value'] == 'guest'
    assert browser.get('/api/personalization/current-user/header-settings', headers={
        'X-AdaOS-Desktop-Credential':'adaos-desktop-v1.invalid','X-AdaOS-Token':'owner-node-secret'}).status_code == 401


def test_deleted_desktop_cannot_be_recreated_by_reattaching_with_an_old_grant(authority, monkeypatch):
    from adaos.services.workspaces import index
    visitor = DesktopPrincipal(SubjectRef('user', 'visitor'))
    authority.share(DesktopPrincipal(authority.access.owner), 'b', visitor.actor)
    authority.require_attachment(visitor, 'b')
    monkeypatch.setattr(index, 'get_workspace', lambda _: None)
    with pytest.raises(CallerAccessDenied):
        authority.require_attachment(visitor, 'b')
    with pytest.raises(CallerAccessDenied):
        require_desktop_command('device.register', {'webspace_id':'b'}, 'b', visitor)


def test_desktop_tool_transport_keeps_named_scope_and_never_becomes_owner(authority, client):
    visitor = SubjectRef('user', 'visitor')
    authority.share(DesktopPrincipal(authority.access.owner), 'b', visitor)
    token = credential(authority, visitor)
    headers = {'X-AdaOS-Token': 'owner-node-secret', 'X-AdaOS-Desktop-Credential': token}
    result = client.post('/api/tools/call', headers=headers,
                         json={'tool': 'application:read', 'context': {'webspace_id': 'b'}})
    assert result.status_code == 200
    assert result.json() == {'actor': 'session:session-visitor', 'scope': 'webspace:b'}
    for body in [{}, {'context': {'webspace_id': 'a'}},
                 {'context': {'webspace_id': 'b'}, 'arguments': {'webspace_id': 'a'}}]:
        assert client.post('/api/tools/call', headers=headers, json=body).status_code == 403
    authority.access.revoke_session('session-visitor', actor=authority.access.owner)
    assert client.post('/api/tools/call', headers=headers,
                       json={'context': {'webspace_id': 'b'}}).status_code == 403


def test_sdk_implicit_address_uses_verified_invocation_desktop(authority):
    from adaos.services.desktop_access import resolve_sdk_desktop
    visitor = SubjectRef('user', 'visitor')
    authority.share(DesktopPrincipal(authority.access.owner), 'b', visitor)
    with verified_caller(visitor, ScopeRef('webspace', 'b')):
        assert resolve_sdk_desktop() == 'b'
        with pytest.raises(CallerAccessDenied):
            resolve_sdk_desktop('a')
