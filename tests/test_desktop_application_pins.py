from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from adaos.domain.application_access import ApplicationAccessGrant, ApplicationPermissionProfile, ApplicationRoleDeclaration
from adaos.domain.personalization_access import SubjectRef
from adaos.services import desktop_applications, personalization_runtime
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.policy.caller import CallerAccessDenied
from adaos.services.io_web import desktop
from adaos.services.io_web.desktop import WebDesktopService
from adaos.services.workspaces import index


@pytest.fixture
def pins(tmp_path, monkeypatch):
    access = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / 'access.json'), owner=SubjectRef('user', 'owner'))
    monkeypatch.setattr(personalization_runtime, 'personalization_access_service', lambda *_: access)
    policy = DesktopAccessService(access)
    owner = DesktopPrincipal(access.owner)
    user = DesktopPrincipal(SubjectRef('user', 'visitor'))
    grant = policy.share(owner, 'desk', user.actor)
    policy.set_permissions(owner, 'desk', grant['grant_id'], ['desktop.applications.pin'])
    index.ensure_workspace('desk')
    index.set_workspace_pinned_applications_overlay('desk', ['scenario:private'])
    profile = ApplicationPermissionProfile.from_legacy_permissions(['workspace.read'])
    role = ApplicationRoleDeclaration(role_id='viewer', title='Viewer', grants=('read',), requires_permissions=('workspace.read',))
    def app(name):
        return SimpleNamespace(application_id=name, slug=name, lifecycle='active', kind='application',
            display={'title': name}, entrypoints=({'entrypoint_id': 'main', 'presentation_ref': 'scenario:' + name},))
    releases = {name: SimpleNamespace(application_id=name, permission_profile=profile, application_roles=(role,),
        project_release=SimpleNamespace(catalog={})) for name in ['allowed', 'private', 'removed']}
    grants = [ApplicationAccessGrant(grant_id='appgrant', subject_ref='user:visitor', application_id='allowed',
        application_roles=('viewer',), permission_ceiling=('workspace.read',), explicit_denies=(), constraints={},
        issuer_ref='user:owner', reviewed_permission_profile_digest=profile.digest)]
    store = SimpleNamespace(list_runtime_selections=lambda: [],
        list_installations=lambda: [SimpleNamespace(application_id=name, installed_release_digest=name,
            status='removed' if name == 'removed' else 'active') for name in releases],
        get_application=app, get_release=lambda _, digest: releases[digest], release_has_component=lambda *_, **__: True,
        list_application_access_grants=lambda application_id, subject_ref: [item for item in grants
            if item.application_id == application_id and item.subject_ref == subject_ref])
    monkeypatch.setattr(desktop_applications, 'get_application_service', lambda _: SimpleNamespace(store=store))
    writes = []
    snapshot = SimpleNamespace(pinned_applications=['scenario:private'],
        installed=desktop.WebDesktopInstalled(apps=['scenario:private'], widgets=[]))
    fake_service = SimpleNamespace(get_snapshot=lambda _: snapshot,
        set_installed_with_live_room=lambda data, host: writes.append(('installed', data, host)),
        set_pinned_applications_with_live_room=lambda data, host: writes.append(('pinned', data, host)))
    monkeypatch.setattr(desktop, 'WebDesktopService', lambda: fake_service)
    return SimpleNamespace(owner=owner, user=user, policy=policy, grant=grant, grants=grants, writes=writes, store=store)


def test_choices_are_installed_and_authorized_for_the_current_subject(pins):
    assert [item['application_id'] for item in desktop_applications.desktop_application_choices('desk', pins.user)] == ['allowed']
    assert [item['application_id'] for item in desktop_applications.desktop_application_choices('desk', pins.owner)] == ['allowed', 'private']


@pytest.mark.parametrize('change', ['revoked', 'expired', 'profile', 'scope', 'denied'])
def test_access_is_rechecked_for_each_choice_read(pins, change):
    original = pins.grants[0]
    changes = {'revoked': {'status': 'revoked'}, 'expired': {'expires_at': (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()},
               'profile': {'reviewed_permission_profile_digest': 'sha256:' + 'a' * 64},
               'scope': {'constraints': {'webspace_id': 'another'}}, 'denied': {'permission_ceiling': (), 'explicit_denies': ('workspace.read',)}}
    pins.grants[0] = replace(original, **changes[change])
    assert desktop_applications.desktop_application_choices('desk', pins.user) == []


def test_checklist_preserves_inaccessible_pins_and_reconciles_only_installed_apps(pins):
    result = desktop_applications.set_desktop_application_pins('desk', ['allowed'], pins.user)
    assert result[0]['pinned']
    assert pins.writes[0][1].apps == ['scenario:private', 'scenario:allowed']
    assert pins.writes[1] == ('pinned', ['scenario:private', 'scenario:allowed'], 'desk')


def test_unavailable_application_cannot_be_submitted_by_forging_a_checkbox(pins):
    with pytest.raises(CallerAccessDenied):
        desktop_applications.set_desktop_application_pins('desk', ['private'], pins.user)
    assert pins.writes == []


def test_revoked_desktop_permission_blocks_pin_writes(pins):
    pins.policy.set_permissions(pins.owner, 'desk', pins.grant['grant_id'], [])
    with pytest.raises(CallerAccessDenied):
        desktop_applications.set_desktop_application_pins('desk', ['allowed'], pins.user)
    assert pins.writes == []


def test_legacy_pin_refs_cannot_bypass_application_access(pins):
    from adaos.services.policy.caller import verified_caller
    with verified_caller(pins.user.actor):
        with pytest.raises(CallerAccessDenied):
            WebDesktopService._persist_overlay_pinned_applications('desk', ['scenario:private', 'scenario:removed'])
        with pytest.raises(CallerAccessDenied):
            WebDesktopService._persist_overlay_pinned_applications('desk', [])
        WebDesktopService._persist_overlay_pinned_applications('desk', ['scenario:private', 'scenario:allowed'])
    assert index.get_workspace('desk').pinned_applications_overlay == ['scenario:private', 'scenario:allowed']


@pytest.mark.parametrize(('admission', 'host', 'kind', 'available'), [
    ('admitted', 'desk', 'temporary_trial', True), ('expiring', 'desk', 'temporary_trial', True),
    ('expired', 'desk', 'temporary_trial', False), ('admitted', 'another', 'temporary_trial', False),
    ('admitted', 'another', 'installed_local_beta', True),
])
def test_checklist_respects_trial_admission_and_desktop(pins, monkeypatch, admission, host, kind, available):
    from adaos.services.applications import lifecycle
    pins.store.list_runtime_selections = lambda: [SimpleNamespace(application_id='allowed', webspace_id=host,
        source='local_trial', release_digest='allowed')]
    monkeypatch.setattr(lifecycle, 'observe_runtime_admission', lambda *_, **__: {'admission': admission, 'admission_kind': kind})
    choices = desktop_applications.desktop_application_choices('desk', pins.user)
    assert bool(choices) == available


def test_checklist_does_not_offer_a_scenario_missing_from_the_selected_release(pins):
    pins.store.release_has_component = lambda *_, **__: False
    assert desktop_applications.desktop_application_choices('desk', pins.user) == []
