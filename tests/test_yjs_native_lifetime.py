import threading

import pytest

from adaos.services.yjs import doc


@pytest.mark.asyncio
async def test_sync_active_loop_rejection_never_allocates_native_document(monkeypatch):
    monkeypatch.setattr(doc.Y, 'YDoc', lambda: pytest.fail('Native allocation before active-loop admission'))
    monkeypatch.setattr(doc, 'get_ystore_for_webspace', lambda _: pytest.fail('Store allocation before active-loop admission'))
    with pytest.raises(RuntimeError, match='active event loop'):
        with doc.get_ydoc('test_native_lifetime'):
            pytest.fail('Rejected context must never yield')


def test_sync_load_failure_releases_native_cell_before_traceback_leaves_owner(monkeypatch):
    released, errors, owner = [], [], []

    class Native:
        def __del__(self):
            released.append(threading.get_ident())

    def fail(coro, **kwargs):
        coro.close()
        raise RuntimeError('controlled load failure')

    monkeypatch.setattr(doc.Y, 'YDoc', Native)
    monkeypatch.setattr(doc, '_resolve_live_room', lambda _: None)
    monkeypatch.setattr(doc, 'get_ystore_for_webspace', lambda _: object())
    monkeypatch.setattr(doc, '_run_blocking', fail)

    def worker():
        owner.append(threading.get_ident())
        try:
            with doc.get_ydoc('test_native_lifetime'):
                raise AssertionError('Load failure must never yield')
        except RuntimeError as error:
            errors.append(error)  # Deliberately move its retained traceback.

    thread = threading.Thread(target=worker)
    thread.start(); thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert released == owner, 'Native final decref must occur on its creator thread'
    traceback = errors[0].__traceback__
    while traceback:
        if traceback.tb_frame.f_code.co_name == 'get_ydoc':
            assert traceback.tb_frame.f_locals['ydoc'] is None
        traceback = traceback.tb_next


@pytest.mark.asyncio
async def test_async_store_start_failure_does_not_allocate_native_document(monkeypatch):
    class Store:
        async def start(self):
            raise RuntimeError('controlled store start failure')

    lock = threading.Lock()
    async def acquire(*args, **kwargs):
        lock.acquire()
        return lock

    monkeypatch.setattr(doc.Y, 'YDoc', lambda: pytest.fail('Native allocation before store start'))
    monkeypatch.setattr(doc, '_resolve_live_room', lambda _: None)
    monkeypatch.setattr(doc, 'get_ystore_for_webspace', lambda _: Store())
    monkeypatch.setattr(doc, '_acquire_async_get_ydoc_session', acquire)
    with pytest.raises(RuntimeError, match='controlled store start failure'):
        async with doc.async_get_ydoc('test_native_lifetime'):
            pytest.fail('Failed context must never yield')
    assert not lock.locked()
