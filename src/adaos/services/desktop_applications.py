"""Node-independent Home Application choices from subnet installation facts."""
from pathlib import Path

from adaos.services.agent_context import get_ctx
from adaos.services.applications import get_application_service


def home_applications(desktop_id: str | None = None) -> list[dict]:
    ctx = get_ctx()
    state = Path(getattr(ctx, 'authority_state_dir', None) or ctx.paths.state_dir())
    store = get_application_service(state).store
    installed = {item.application_id for item in store.list_installations()
                 if item.status in {'active', 'updating', 'degraded'}}
    if desktop_id:
        installed.update(item.application_id for item in store.list_runtime_selections()
                         if item.webspace_id == desktop_id)
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


def resolve_home_application(application_id: str, desktop_id: str | None = None) -> dict:
    for entry in home_applications(desktop_id):
        if entry['application_id'] == application_id:
            return entry
    raise ValueError('home_application_unavailable')
