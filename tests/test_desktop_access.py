import time

import pytest

from adaos.domain.personalization_access import Grant, GrantConstraint, Membership, ScopeRef, SessionKey, SubjectRef
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.policy.caller import CallerAccessDenied


@pytest.fixture
def policy(tmp_path):
    access = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / 'access.json'),
                                        owner=SubjectRef('user', 'owner'))
    return DesktopAccessService(access, subnet_id='subnet-a')


def principal(name):
    return DesktopPrincipal(SubjectRef('user', name))


@pytest.mark.parametrize('role', ['owner', 'co_owner', 'admin'])
def test_builtin_administrators_manage_desktops_and_sharing(policy, role):
    actor = principal(role + '-user')
    policy.access.put_membership(Membership(subject=actor.actor, scope=ScopeRef('subnet', 'subnet-a'), role=role))
    policy.require(actor, 'desktop-a', administer=True)
    grant = policy.share(actor, 'desktop-a', principal('visitor').actor)
    policy.require(principal('visitor'), 'desktop-a')
    policy.revoke(actor, 'desktop-a', grant['grant_id'])
    assert not policy.allowed(principal('visitor'), 'desktop-a')


def test_shared_participant_gets_only_admitted_catalog_and_no_system_rights(policy):
    visitor = principal('visitor')
    policy.share(principal('owner'), 'desktop-b', visitor.actor)
    assert policy.filter_catalog(visitor, [{'id': 'desktop-a', 'title': 'private'}, {'id': 'desktop-b'}]) == [{'id': 'desktop-b', 'can_administer': False}]
    policy.require(visitor, 'desktop-b')
    with pytest.raises(CallerAccessDenied):
        policy.require(visitor, 'desktop-a')
    with pytest.raises(CallerAccessDenied):
        policy.require(visitor, 'desktop-b', administer=True)
    with pytest.raises(CallerAccessDenied):
        policy.share(visitor, 'desktop-b', principal('other').actor)


def test_owner_limits_narrow_broad_membership_and_survive_store_reopen(policy):
    visitor = principal('visitor')
    policy.access.put_membership(Membership(subject=visitor.actor, scope=ScopeRef('subnet', 'subnet-a'), role='member'))
    assert policy.allowed(visitor, 'desktop-a')
    policy.set_allowed_desktops(principal('owner'), visitor.actor, ['desktop-b'])
    reopened = DesktopAccessService(PersonalizationAccessService(PersonalizationAccessStore(policy.access.store.path),
                                    owner=policy.access.owner), subnet_id='subnet-a')
    assert not reopened.allowed(visitor, 'desktop-a')
    assert reopened.allowed(visitor, 'desktop-b')


def test_revocation_invalidates_linked_membership_instead_of_leaving_another_authority(policy):
    visitor = principal('visitor')
    grant = policy.share(principal('owner'), 'desktop-b', visitor.actor)
    policy.access.put_membership(Membership(subject=visitor.actor, scope=ScopeRef('webspace', 'desktop-b'),
                                          role='admin', grant_id='membership:' + grant['grant_id']))
    assert policy.allowed(visitor, 'desktop-b')
    assert not policy.allowed(visitor, 'desktop-b', administer=True)
    policy.revoke(principal('owner'), 'desktop-b', grant['grant_id'])
    assert not policy.allowed(visitor, 'desktop-b')


def test_other_subnet_and_expired_grants_cannot_admit_or_administer(policy):
    visitor = principal('visitor')
    policy.access.put_membership(Membership(subject=visitor.actor, scope=ScopeRef('subnet', 'other-subnet'), role='owner'))
    policy.access.put_grant(Grant(grant_id='expired', subject=visitor.actor, scope=ScopeRef('webspace', 'desktop-a'),
                                 role='admin', constraints=GrantConstraint(expires_at=time.time() - 1)))
    assert not policy.allowed(visitor, 'desktop-a')
    assert not policy.allowed(visitor, 'desktop-a', administer=True)


def test_desktop_invite_never_inherits_system_role_and_credential_revokes_with_session(policy):
    access = policy.access
    access.create_targeted_invite_link(invite_id='invite-b', scope=ScopeRef('webspace', 'desktop-b'), role='admin',
                                      issued_by=access.owner, profile_hint=None, expires_at=time.time() + 3600)
    visitor = principal('visitor')
    access.claim_invite('invite-b', accepted_by=visitor.actor, session_id='session-b')
    session = DesktopPrincipal(SubjectRef('session', 'session-b'), 'session-b')
    policy.require(session, 'desktop-b')
    assert not policy.allowed(session, 'desktop-b', administer=True)
    assert access.evaluate(actor=visitor.actor, action='memberships.grant', scope=ScopeRef('webspace', 'desktop-b')).decision == 'deny'
    credential = policy.issue_credential(session)
    assert policy.authenticate(credential['access_token']).actor == session.actor
    access.revoke_session('session-b', actor=access.owner)
    with pytest.raises(CallerAccessDenied):
        policy.authenticate(credential['access_token'])


def test_credential_cannot_be_replaced_by_session_id_or_another_secret(policy):
    policy.access.put_session(SessionKey(session_id='session-b', key_id='key', subject=principal('visitor').actor))
    session = DesktopPrincipal(SubjectRef('session', 'session-b'), 'session-b')
    token = policy.issue_credential(session)['access_token']
    with pytest.raises(CallerAccessDenied):
        policy.authenticate('session-b')
    with pytest.raises(CallerAccessDenied):
        policy.authenticate(token[:-1] + ('A' if token[-1] != 'A' else 'B'))
