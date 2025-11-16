from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager, asynccontextmanager
from typing import Iterator, AsyncIterator, Awaitable, Optional, TypeVar

import y_py as Y
from ypy_websocket.ystore import SQLiteYStore

from adaos.apps.yjs.y_store import ystore_path_for_webspace

T = TypeVar("T")


def _run_blocking(coro: Awaitable[T]) -> T:
    """
    Execute an async SQLiteYStore operation from synchronous code.
    Falls back to asyncio.run when no loop is active.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("get_ydoc() cannot be used inside an active event loop; use async_get_ydoc().")


def _resolve_live_room(webspace_id: str):
    """
    Try to resolve an active YRoom for the given webspace id, if the Y websocket
    server is running in-process. Import is lazy to avoid circular deps.
    """
    try:
        from adaos.apps.yjs.y_gateway import y_server  # pylint: disable=import-outside-toplevel
    except Exception:
        return None
    return y_server.rooms.get(webspace_id)


def _schedule_room_update(webspace_id: str, update: Optional[bytes]) -> None:
    """
    Apply the given Yjs update to the active room (if any) so connected clients
    receive the change immediately. Falls back silently if no room is active.
    """
    if not update:
        return
    room = _resolve_live_room(webspace_id)
    if not room:
        return

    def _apply() -> None:
        try:
            Y.apply_update(room.ydoc, update)
        except Exception:
            pass

    owner_thread = getattr(room, "_thread_id", None)
    loop = getattr(room, "_loop", None)
    current = threading.get_ident()

    if owner_thread is not None and owner_thread == current:
        _apply()
        return

    if loop and loop.is_running():
        try:
            loop.call_soon_threadsafe(_apply)
        except RuntimeError:
            pass
        return

    # Last resort: best-effort apply synchronously (same-thread requirement may still fail)
    if owner_thread is None:
        _apply()


def _encode_diff(ydoc: Y.YDoc, before: bytes | None) -> bytes | None:
    try:
        if before is not None:
            return Y.encode_state_as_update(ydoc, before)
        return Y.encode_state_as_update(ydoc)
    except Exception:
        return None


@contextmanager
def get_ydoc(webspace_id: str) -> Iterator[Y.YDoc]:
    """
    Synchronously load a webspace-backed YDoc, applying persisted updates on
    entry and writing the resulting state back on exit.
    """
    ystore = SQLiteYStore(str(ystore_path_for_webspace(webspace_id)))
    ydoc = Y.YDoc()

    async def _load() -> bytes | None:
        await ystore.start()
        try:
            await ystore.apply_updates(ydoc)
        except Exception:
            pass
        try:
            return Y.encode_state_vector(ydoc)
        except Exception:
            return None

    before = _run_blocking(_load())
    try:
        yield ydoc
    finally:
        async def _flush() -> bytes | None:
            try:
                await ystore.encode_state_as_update(ydoc)
            except Exception:
                pass
            finally:
                try:
                    await ystore.stop()
                except Exception:
                    pass
            return _encode_diff(ydoc, before)

        try:
            update = _run_blocking(_flush())
        except Exception:
            update = None
        _schedule_room_update(webspace_id, update)


@asynccontextmanager
async def async_get_ydoc(webspace_id: str) -> AsyncIterator[Y.YDoc]:
    """
    Async counterpart of :func:`get_ydoc` for use inside running event loops.
    """
    ystore = SQLiteYStore(str(ystore_path_for_webspace(webspace_id)))
    ydoc = Y.YDoc()
    await ystore.start()
    try:
        try:
            await ystore.apply_updates(ydoc)
        except Exception:
            pass
        try:
            before = Y.encode_state_vector(ydoc)
        except Exception:
            before = None
        yield ydoc
        try:
            await ystore.encode_state_as_update(ydoc)
        except Exception:
            pass
        update = _encode_diff(ydoc, before)
        _schedule_room_update(webspace_id, update)
    finally:
        try:
            await ystore.stop()
        except Exception:
            pass


__all__ = ["get_ydoc", "async_get_ydoc"]
