"""Node-independent Home Application choices from subnet installation facts."""
from pathlib import Path
from datetime import datetime, timezone

from adaos.services.agent_context import get_ctx
from adaos.services.applications import get_application_service


def home_applications() -> list[dict]:
    ctx = get_ctx()
    state = Path(getattr(ctx, 'authority_state_dir', None) or ctx.paths.state_dir())
    store = get_application_service(state).store
    installed = {item.application_id for item in store.list_installations()
                 if item.status in {'active', 'updating', 'degraded'}}
    items = [{'application_id': 'core:desktop', 'title': 'Desktop', 'scenario_id': 'web_desktop'}]
    for app in store.list_applications():
        if app.application_id not in installed:
            continue
        entries = [entry for entry in app.entrypoints
                   if str(entry.get('presentation_ref', '')).startswith('scenario:')]
        if not entries:
            continue  # Modal-only Applications cannot be a desktop's Home.
        entry = next((entry for entry in entries if entry.get('entrypoint_id') in {'main', 'default', 'primary'}), entries[0])
        items.append({'application_id': app.application_id,
                      'title': str(app.display.get('title') or app.slug),
                      'scenario_id': entry['presentation_ref'].split(':', 1)[1]})
    return items


def resolve_home_application(application_id: str) -> dict:
    for entry in home_applications():
        if entry['application_id'] == application_id:
            return entry
    raise ValueError('home_application_unavailable')


def _application_available(store, release, principal, policy, webspace_id: str) -> bool:
    """Observe existing Application grants; pinning never creates access."""
    from adaos.domain.application_access import evaluate_application_access

    subjects = policy._subjects(principal)
    if policy.access.owner in subjects:
        return True
    profile = release.permission_profile
    roles = release.application_roles
    session = policy.access.store.get_session(principal.session_id or '') or {}
    chain = {'webspace_id': webspace_id, 'session_ref': 'session:' + principal.session_id if principal.session_id else '',
             'device_ref': 'device:' + str(session['device_id']) if session.get('device_id') else ''}
    for subject in subjects:
        for grant in store.list_application_access_grants(release.application_id, subject_ref=subject.ref()):
            if grant.status != 'active' or grant.reviewed_permission_profile_digest != profile.digest:
                continue
            if grant.expires_at and datetime.fromisoformat(grant.expires_at.replace('Z', '+00:00')) <= datetime.now(timezone.utc):
                continue
            capabilities = {capability for role in roles if role.role_id in grant.application_roles for capability in role.grants}
            if not roles:
                capabilities = {'application.open'}
            for capability in capabilities:
                for permission in set(grant.permission_ceiling) - set(grant.explicit_denies):
                    decision = evaluate_application_access(profile=profile, roles=roles, grant=grant,
                        permission_id=permission, app_capability=capability, actor_chain=chain,
                        component_capabilities=profile.flat_permissions)
                    if decision.decision == 'allow':
                        return True
    return False


def desktop_application_choices(webspace_id: str, principal) -> list[dict]:
    from adaos.services.applications.lifecycle import observe_runtime_admission
    from adaos.services.applications.presentation import primary_scenario_entrypoint
    from adaos.services.desktop_access import DesktopAccessService
    from adaos.services.workspaces.index import get_workspace

    policy = DesktopAccessService()
    policy.require(principal, webspace_id)
    ctx = get_ctx()
    state = Path(getattr(ctx, 'authority_state_dir', None) or ctx.paths.state_dir())
    store = get_application_service(state).store
    row = get_workspace(webspace_id)
    if row is None:
        raise ValueError('desktop_not_found')
    pins = set(row.pinned_applications_overlay if row.has_pinned_applications_overlay
               else (row.installed_overlay or {}).get('apps', []))
    selections = store.list_runtime_selections()
    items = []
    for installation in store.list_installations():
        if installation.status not in {'active', 'updating', 'degraded'}:
            continue
        app = store.get_application(installation.application_id)
        if app.lifecycle != 'active' or app.kind != 'application':
            continue
        selected = next((item for item in selections if item.application_id == app.application_id
                         and item.webspace_id == webspace_id), None)
        if selected is None:
            selected = next((item for item in selections if item.application_id == app.application_id
                             and item.source == 'local_trial'), None)
        if selected is not None and selected.source == 'local_trial':
            admission = observe_runtime_admission(state, selected, store=store)
            if admission.get('admission') not in {'admitted', 'expiring'} or (
                    selected.webspace_id != webspace_id and admission.get('admission_kind') != 'installed_local_beta'):
                continue
        digest = selected.release_digest if selected is not None else installation.installed_release_digest
        release = store.get_release(app.application_id, digest)
        if not _application_available(store, release, principal, policy, webspace_id):
            continue
        primary = primary_scenario_entrypoint(app.entrypoints)
        if primary is None:
            primary = next((entry for entry in app.entrypoints
                            if ':' not in str(entry.get('presentation_ref') or '')), None)
        if primary is None:
            continue
        reference = primary['presentation_ref']
        if reference.startswith('scenario:') and not store.release_has_component(
                app.application_id, digest, kind='scenario', artifact_id=reference.split(':', 1)[1]):
            continue
        aliases = {entry['presentation_ref'] for entry in app.entrypoints}
        items.append({'application_id': app.application_id, 'title': str(app.display.get('title') or app.slug),
                      'icon': (release.project_release.catalog or {}).get('icon') or app.display.get('icon') or 'apps-outline',
                      'application_ref': reference, 'aliases': sorted(aliases), 'pinned': bool(pins & aliases)})
    return sorted(items, key=lambda item: (item['title'].casefold(), item['application_id']))


def set_desktop_application_pins(webspace_id: str, application_ids: list[str], principal) -> list[dict]:
    from adaos.services.desktop_access import DesktopAccessService
    from adaos.services.io_web.desktop import WebDesktopService, WebDesktopInstalled
    from adaos.services.policy.caller import CallerAccessDenied, verified_caller

    DesktopAccessService().require(principal, webspace_id, capability='desktop.applications.pin')
    choices = desktop_application_choices(webspace_id, principal)
    requested = set(application_ids)
    if requested - {item['application_id'] for item in choices}:
        raise CallerAccessDenied('desktop_application_unavailable')
    service = WebDesktopService()
    snapshot = service.get_snapshot(webspace_id)
    editable = {alias for item in choices for alias in item['aliases']}
    pins = [item for item in snapshot.pinned_applications if item not in editable]
    selected = [item['application_ref'] for item in choices if item['application_id'] in requested]
    installed = list(dict.fromkeys([*snapshot.installed.apps, *selected]))
    # All identities and access were checked above. This is a presentation
    # reconciliation of already installed Applications, not an installation.
    with verified_caller(None):
        service.set_installed_with_live_room(WebDesktopInstalled(
            apps=installed, widgets=snapshot.installed.widgets,
            removed_apps=[item for item in snapshot.installed.removed_apps if item not in selected],
            removed_widgets=snapshot.installed.removed_widgets), webspace_id)
        service.set_pinned_applications_with_live_room([*pins, *selected], webspace_id)
    return [{**item, 'pinned': item['application_id'] in requested} for item in choices]


def authorize_application_pin_refs(webspace_id: str, items: list[str]) -> None:
    """Keep legacy HTTP/SDK pin writes within the same delegated choices."""
    from adaos.services.desktop_access import DesktopAccessService, require_sdk_desktop
    from adaos.services.policy.caller import CallerAccessDenied, current_caller
    from adaos.services.workspaces.index import get_workspace

    if current_caller() is None:
        return
    principal = require_sdk_desktop(webspace_id, capability='desktop.applications.pin')
    if DesktopAccessService().allowed(principal, webspace_id, administer=True):
        return
    choices = desktop_application_choices(webspace_id, principal)
    editable = {alias for choice in choices for alias in choice['aliases']}
    row = get_workspace(webspace_id)
    current = set(row.pinned_applications_overlay if row.has_pinned_applications_overlay
                  else (row.installed_overlay or {}).get('apps', []))
    requested = set(items)
    if (requested ^ current) - editable:
        raise CallerAccessDenied('desktop_application_unavailable')
