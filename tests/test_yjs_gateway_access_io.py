import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from adaos.services import desktop_access
from adaos.services.yjs import gateway_ws


@pytest.mark.asyncio
@pytest.mark.parametrize('attachment', [False, True])
async def test_desktop_access_construction_and_check_do_not_block_room_loop(monkeypatch, attachment):
    owner = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    calls = []
    principal = object()

    class SlowService:
        def __init__(self):
            assert threading.get_ident() != owner
            entered.set()
            assert release.wait(2)

        def require(self, actor, space):
            assert threading.get_ident() != owner
            calls.append(('require', actor, space))

        def require_attachment(self, actor, space):
            assert threading.get_ident() != owner
            calls.append(('require_attachment', actor, space))

    monkeypatch.setattr(desktop_access, 'DesktopAccessService', SlowService)
    task = asyncio.create_task(gateway_ws._require_gateway_desktop_access(principal, 'dev2', attachment=attachment))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await task
    assert calls == [('require_attachment' if attachment else 'require', principal, 'dev2')]


@pytest.mark.asyncio
@pytest.mark.parametrize('attachment', [False, True])
async def test_desktop_access_revocation_is_not_swallowed(monkeypatch, attachment):
    class Denied:
        def require(self, *args):
            raise PermissionError('desktop_access_revoked')

        require_attachment = require

    monkeypatch.setattr(desktop_access, 'DesktopAccessService', Denied)
    with pytest.raises(PermissionError, match='desktop_access_revoked'):
        await gateway_ws._require_gateway_desktop_access(object(), 'dev2', attachment=attachment)


def test_explicit_root_development_admission_does_not_read_workspace_catalog(monkeypatch):
    monkeypatch.setattr(gateway_ws, 'get_workspace', lambda _: pytest.fail('unnecessary filesystem read'))
    assert gateway_ws._development_webspace_access_denied('dev2', routed=True, development_access=True) is False
    assert gateway_ws._development_webspace_access_denied('dev2', routed=False, development_access=False) is False


@pytest.mark.asyncio
@pytest.mark.parametrize('desktop_id', [None, 'dev2'])
async def test_idle_desktop_revocation_watcher_constructs_policy_off_loop(monkeypatch, desktop_id):
    owner = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    calls = []
    close = AsyncMock()
    principal = object()

    class Revoked:
        def __init__(self):
            assert threading.get_ident() != owner
            entered.set()
            assert release.wait(2)

        def require(self, actor, space, *, administer=False):
            assert threading.get_ident() != owner
            calls.append((actor, space, administer))
            raise PermissionError('desktop_access_revoked')

        require_attachment = require

    monkeypatch.setattr(desktop_access, 'DesktopAccessService', Revoked)
    task = asyncio.create_task(desktop_access.watch_desktop_access(principal, desktop_id, close, administer=True))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await task
    assert calls == [(principal, desktop_id, desktop_id is None)]
    close.assert_awaited_once()
