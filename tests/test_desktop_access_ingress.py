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
    monkeypatch.setattr(runtime, 'personalization_access_service', lambda: access)
    monkeypatch.setattr(runtime, 'current_user_id', lambda: 'owner')
    monkeypatch.setattr(runtime, 'current_subnet_id', lambda: 'subnet')
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
