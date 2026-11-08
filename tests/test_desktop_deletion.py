import asyncio
import sqlite3

import pytest

from adaos.services.agent_context import get_ctx
from adaos.services.workspaces import index
from adaos.services.scenario import webspace_runtime


def test_deleted_identity_cannot_be_recreated_by_late_materialization():
    index.ensure_workspace('retired')
    index.delete_workspace('retired')
    webspace_runtime._resolve_projection_refresh_space('retired')
    with pytest.raises(index.WorkspaceDeletedError):
        index.set_workspace_current_scenario_overlay('retired', 'web_desktop')
    assert index.get_workspace('retired') is None
    assert 'retired' not in [row.workspace_id for row in index.list_workspaces()]


def test_database_fences_an_insert_from_a_stale_writer():
    row = index.ensure_workspace('retired')
    index.delete_workspace('retired')
    with get_ctx().sql.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match='workspace_deleted'):
            connection.execute('INSERT INTO y_workspaces(workspace_id,path,created_at) VALUES (?,?,?)',
                               ('retired', row.path, row.created_at))


def test_late_snapshot_backup_cannot_restore_deleted_data():
    from adaos.services.yjs.store import _persist_desktop_snapshot, ystore_path_for_webspace
    index.ensure_workspace('retired')
    path = ystore_path_for_webspace('retired')
    assert _persist_desktop_snapshot(path, b'old data', 'retired') > 0
    index.delete_workspace('retired')
    assert _persist_desktop_snapshot(path, b'late data', 'retired') == 0
    assert not path.exists()


def test_delete_closes_only_document_without_snapshot_or_prewarm(monkeypatch):
    index.ensure_workspace('retired')
    from adaos.services.yjs import gateway, gateway_ws, store
    calls = []
    async def reset(desktop, **kwargs): calls.append((desktop, kwargs))
    async def clear(desktop): calls.append(('store', desktop))
    monkeypatch.setattr(gateway, 'reset_live_webspace_room', reset)
    monkeypatch.setattr(store, 'reset_ystore_for_webspace_async', clear)
    monkeypatch.setattr(gateway_ws, '_publish_runtime_event', lambda kind, payload, **_: calls.append((kind, payload)))
    assert asyncio.run(webspace_runtime.WebspaceService().delete('retired'))
    assert calls[0] == ('desktop.deleted', {'webspace_id': 'retired'})
    assert calls[1] == ('retired', {'close_reason': 'desktop_deleted', 'persist_ystore_snapshot': False,
                                  'prewarm_after_reset': False})
    assert index.get_workspace('retired') is None


def test_command_ack_waits_for_core_deletion_to_complete(monkeypatch):
    from adaos.services.yjs.gateway_ws import process_events_command
    from adaos.services.personalization_runtime import personalization_access_service
    from adaos.services.desktop_access import DesktopPrincipal
    index.ensure_workspace('retired')
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        responses = []
        async def delete(self, desktop):
            assert desktop == 'retired'
            entered.set()
            await release.wait()
            return True
        async def respond(value): responses.append(value)
        monkeypatch.setattr(webspace_runtime.WebspaceService, 'delete', delete)
        principal = DesktopPrincipal(personalization_access_service().owner)
        task = asyncio.create_task(process_events_command(kind='desktop.webspace.delete',cmd_id='delete-completion-proof',
            payload={'id':'retired'},device_id='test',webspace_id='retired',client_label='test',
            send_response=respond,desktop_principal=principal))
        await asyncio.wait_for(entered.wait(), 5)
        assert responses == []
        release.set()
        await task
        assert responses[-1]['ok'] is True and responses[-1]['data']['deleted'] is True
    asyncio.run(run())
