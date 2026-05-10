from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import threading
import time
from contextlib import contextmanager, asynccontextmanager
from typing import Iterator, AsyncIterator, Awaitable, Optional, TypeVar, Callable, Any

import y_py as Y

from adaos.services.agent_context import get_ctx
from adaos.services.yjs.store import get_ystore_for_webspace, ystore_write_metadata, ystore_write_metadata_sync
from adaos.services.yjs.update_origin import mark_backend_room_update

T = TypeVar("T")
_log = logging.getLogger("adaos.yjs.doc")
_LIVE_MAP_VALUE_CACHE_TTL_S = 10.0
_LIVE_MAP_VALUE_CACHE_MAX = 128
_LIVE_MAP_VALUE_CACHE: dict[tuple[str, str, str], tuple[float, Any]] = {}
_LIVE_MAP_VALUE_SAFE_KEYS = {
    ("ui", "current_scenario"),
}


def _cacheable_live_map_value(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _resolve_yjs_write_owner() -> str:
    try:
        current = getattr(get_ctx(), "skill_ctx", None)
        if current is not None:
            active = current.get()
            name = str(getattr(active, "name", "") or "").strip()
            if name:
                return f"skill:{name}"
    except Exception:
        pass
    return "core"


_SYNC_GET_YDOC_GUARD_LOCK = threading.RLock()
_SYNC_GET_YDOC_ACTIVE_BY_WEBSPACE: dict[str, int] = {}


def _sync_get_ydoc_max_active_per_webspace() -> int:
    raw = str(os.getenv("ADAOS_YJS_SYNC_GET_YDOC_MAX_ACTIVE_PER_WEBSPACE") or "").strip()
    if not raw:
        return 4
    try:
        value = int(raw)
    except ValueError:
        return 4
    return max(0, min(128, value))


def _sync_get_ydoc_owner_label() -> str:
    owner = _resolve_yjs_write_owner()
    thread_name = threading.current_thread().name
    if owner:
        return f"{owner}/{thread_name}"
    return thread_name


def _acquire_sync_get_ydoc_slot(webspace_id: str) -> str | None:
    limit = _sync_get_ydoc_max_active_per_webspace()
    if limit <= 0:
        return None
    key = str(webspace_id or "default")
    with _SYNC_GET_YDOC_GUARD_LOCK:
        active = int(_SYNC_GET_YDOC_ACTIVE_BY_WEBSPACE.get(key, 0) or 0)
        if active >= limit:
            _log.warning(
                "sync get_ydoc rejected by concurrency guard webspace=%s active=%s limit=%s owner=%s",
                key,
                active,
                limit,
                _sync_get_ydoc_owner_label(),
            )
            raise RuntimeError("sync_get_ydoc_overload")
        _SYNC_GET_YDOC_ACTIVE_BY_WEBSPACE[key] = active + 1
    return key


def _release_sync_get_ydoc_slot(key: str | None) -> None:
    if not key:
        return
    with _SYNC_GET_YDOC_GUARD_LOCK:
        active = int(_SYNC_GET_YDOC_ACTIVE_BY_WEBSPACE.get(key, 0) or 0)
        if active <= 1:
            _SYNC_GET_YDOC_ACTIVE_BY_WEBSPACE.pop(key, None)
        else:
            _SYNC_GET_YDOC_ACTIVE_BY_WEBSPACE[key] = active - 1


def _sync_get_ydoc_operation_timeout_s() -> float:
    raw = str(os.getenv("ADAOS_YJS_SYNC_GET_YDOC_OPERATION_TIMEOUT_S") or "").strip()
    if not raw:
        return 8.0
    try:
        value = float(raw)
    except ValueError:
        return 8.0
    return max(0.0, min(300.0, value))


def _record_doc_timing(timings: dict[str, float] | None, key: str, started_at: float, *, prefix: str = "") -> float:
    value = round((time.perf_counter() - started_at) * 1000.0, 3)
    if timings is not None:
        token = f"{prefix}{str(key or '').strip()}" if prefix else str(key or "").strip()
        if token:
            timings[token] = value
    return value


def _set_doc_timing(timings: dict[str, float] | None, key: str, value: float, *, prefix: str = "") -> float:
    if timings is not None:
        token = f"{prefix}{str(key or '').strip()}" if prefix else str(key or "").strip()
        if token:
            timings[token] = round(float(value), 3)
    return round(float(value), 3)


def _run_blocking(coro: Awaitable[T], *, timeout_s: float | None = None) -> T:
    """
    Execute an async SQLiteYStore operation from synchronous code.
    Falls back to asyncio.run when no loop is active.
    """
    async def _await_coro() -> T:
        if timeout_s is not None and timeout_s > 0:
            return await asyncio.wait_for(coro, timeout=timeout_s)
        return await coro

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_coro())
    raise RuntimeError("get_ydoc() cannot be used inside an active event loop; use async_get_ydoc().")


def _resolve_live_room(webspace_id: str):
    """
    Try to resolve an active YRoom for the given webspace id, if the Y websocket
    server is running in-process. Import is lazy to avoid circular deps.
    """
    try:
        from adaos.services.yjs.gateway import y_server  # pylint: disable=import-outside-toplevel
    except Exception:
        return None
    return y_server.rooms.get(webspace_id)


def _live_room_pipeline_ready(room: Any) -> bool:
    """
    Return True when backend mutations on this room will be broadcast and persisted.

    Live-room fast paths are only safe while the room task group is running.
    Otherwise we could mutate an in-memory doc and accidentally skip the normal
    YStore writeback path.
    """
    if room is None or getattr(room, "ydoc", None) is None:
        return False
    if getattr(room, "_task_group", None) is None:
        return False
    if getattr(room, "ystore", None) is None:
        return False
    started = getattr(room, "started", None)
    if started is None:
        return True
    is_set = getattr(started, "is_set", None)
    if not callable(is_set):
        return True
    try:
        return bool(is_set())
    except Exception:
        return False


def _can_access_live_room_directly(room: Any) -> bool:
    """
    Return True when the caller already runs on the room owner thread/loop.

    Direct room reuse is intentionally conservative: we only touch the live
    YDoc in-place when we know we are already executing in the same runtime
    context that owns the room. Other callers fall back to the isolated
    store-backed YDoc session.
    """
    if not _live_room_pipeline_ready(room):
        return False
    owner_thread = getattr(room, "_thread_id", None)
    current_thread = threading.get_ident()
    if owner_thread is not None and owner_thread != current_thread:
        return False
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    room_loop = getattr(room, "_loop", None)
    if room_loop is not None and current_loop is not None and room_loop is not current_loop:
        return False
    return True


def try_read_live_map_value(webspace_id: str, map_name: str, key: str) -> tuple[bool, Any]:
    """
    Best-effort fast path for reading a value from the in-memory live room.

    The helper only reads directly when the current thread already owns the
    room, so it stays non-blocking and safe for hot-path diagnostics.
    """
    cache_key = (str(webspace_id or ""), str(map_name or ""), str(key or ""))
    now = time.monotonic()
    cached = _LIVE_MAP_VALUE_CACHE.get(cache_key)
    if cached is not None and (now - cached[0]) <= _LIVE_MAP_VALUE_CACHE_TTL_S:
        return True, cached[1]
    safe_key = (cache_key[1], cache_key[2])
    if safe_key not in _LIVE_MAP_VALUE_SAFE_KEYS:
        return False, None

    room = _resolve_live_room(cache_key[0])
    if not _can_access_live_room_directly(room):
        return False, None
    try:
        y_map = room.ydoc.get_map(cache_key[1])
        value = y_map.get(cache_key[2])
        if _cacheable_live_map_value(value):
            if len(_LIVE_MAP_VALUE_CACHE) >= _LIVE_MAP_VALUE_CACHE_MAX:
                _LIVE_MAP_VALUE_CACHE.pop(next(iter(_LIVE_MAP_VALUE_CACHE)), None)
            _LIVE_MAP_VALUE_CACHE[cache_key] = (now, value)
        return True, value
    except Exception:
        return True, None


def _schedule_room_update(
    webspace_id: str,
    update: Optional[bytes],
    *,
    already_persisted: bool = False,
    source: str = "yjs.doc.room_update",
    owner: str | None = None,
    channel: str | None = None,
) -> None:
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
            if already_persisted:
                blocked, snapshot = _backend_update_breaks_effective_contract(room, update)
                if blocked:
                    _log.warning(
                        "blocked backend YRoom update that would break effective contract webspace=%s bytes=%s source=%s owner=%s channel=%s snapshot=%s",
                        webspace_id,
                        len(update or b""),
                        source,
                        owner,
                        channel,
                        json.dumps(snapshot, ensure_ascii=True, sort_keys=True)[:1000],
                    )
                    return
            if already_persisted:
                mark_backend_room_update(
                    webspace_id,
                    update,
                    source=source,
                    owner=owner,
                    channel=channel,
                    already_persisted=True,
                    governed=True,
                )
            Y.apply_update(room.ydoc, update)
        except Exception:
            pass

    _run_on_room_thread(room, _apply)


def _run_on_room_thread(room, fn: Callable[[], None]) -> bool:
    owner_thread = getattr(room, "_thread_id", None)
    loop = getattr(room, "_loop", None)
    current = threading.get_ident()

    if owner_thread is not None and owner_thread == current:
        fn()
        return True

    if loop and loop.is_running():
        try:
            loop.call_soon_threadsafe(fn)
            return True
        except RuntimeError:
            return False

    if owner_thread is None:
        fn()
        return True

    return False


def _encode_diff(ydoc: Y.YDoc, before: bytes | None) -> bytes | None:
    try:
        if before is not None:
            return Y.encode_state_as_update(ydoc, before)
        return Y.encode_state_as_update(ydoc)
    except Exception:
        return None


def _backend_update_breaks_effective_contract(room: Any, update: bytes) -> tuple[bool, dict[str, Any]]:
    """
    Preflight a detached backend diff before it is applied to a live shared room.

    A detached writer may be working from a stale/partial YStore state. If its
    diff would turn an already materialized/effective room into a non-ready one,
    applying it to the live room makes browsers briefly lose required state
    before the room repair loop can publish a corrective update. Blocking that
    destructive diff at the live-room boundary keeps the browser doc stable
    while still allowing normal narrow backend updates through.
    """
    if not update:
        return False, {}
    try:
        from adaos.services.yjs.gateway_ws import _room_effective_branch_snapshot  # pylint: disable=import-outside-toplevel

        before = _room_effective_branch_snapshot(room.ydoc)
        if not bool(before.get("ready")):
            return False, before
        probe = Y.YDoc()
        current = Y.encode_state_as_update(room.ydoc)
        if current:
            Y.apply_update(probe, current)
        Y.apply_update(probe, update)
        after = _room_effective_branch_snapshot(probe)
        return not bool(after.get("ready")), after
    except Exception as exc:
        return False, {"ready": True, "error": f"{type(exc).__name__}: {exc}"}


def _state_changed(
    ydoc: Y.YDoc,
    before: bytes | None,
    timings: dict[str, float] | None,
    *,
    prefix: str = "",
) -> bool:
    if before is None:
        return True
    stage_started = time.perf_counter()
    try:
        after = Y.encode_state_vector(ydoc)
        _record_doc_timing(timings, "encode_state_vector_after", stage_started, prefix=prefix)
        return after != before
    except Exception:
        _record_doc_timing(timings, "encode_state_vector_after", stage_started, prefix=prefix)
        return True


@contextmanager
def get_ydoc(
    webspace_id: str,
    *,
    read_only: bool = False,
    timings: dict[str, float] | None = None,
    timing_prefix: str = "",
    load_mark_roots: list[str] | tuple[str, ...] | None = None,
    governed: bool = False,
) -> Iterator[Y.YDoc]:
    """
    Synchronously load a webspace-backed YDoc, applying persisted updates on
    entry and writing the resulting state back on exit.
    """
    _log.debug("get_ydoc enter webspace=%s", webspace_id)
    session_started = time.perf_counter()
    ystore = get_ystore_for_webspace(webspace_id)
    ydoc = Y.YDoc()
    operation_timeout_s = _sync_get_ydoc_operation_timeout_s()

    async def _load() -> bytes | None:
        stage_started = time.perf_counter()
        await ystore.start()
        _record_doc_timing(timings, "ystore_start", stage_started, prefix=timing_prefix)
        try:
            stage_started = time.perf_counter()
            await ystore.apply_updates(ydoc)
            _record_doc_timing(timings, "ystore_apply_updates", stage_started, prefix=timing_prefix)
        except BaseException:
            # Treat corrupted updates as "no state"; start from empty doc.
            _record_doc_timing(timings, "ystore_apply_updates", stage_started, prefix=timing_prefix)
            pass
        if read_only:
            return None
        stage_started = time.perf_counter()
        before = await ystore.current_state_vector()
        if before is not None:
            _set_doc_timing(timings, "encode_state_vector", 0.0, prefix=timing_prefix)
            return before
        try:
            before = Y.encode_state_vector(ydoc)
            _record_doc_timing(timings, "encode_state_vector", stage_started, prefix=timing_prefix)
            return before
        except Exception:
            _record_doc_timing(timings, "encode_state_vector", stage_started, prefix=timing_prefix)
            return None

    sync_slot_key = _acquire_sync_get_ydoc_slot(webspace_id)
    try:
        before = _run_blocking(_load(), timeout_s=operation_timeout_s)
    except BaseException:
        _release_sync_get_ydoc_slot(sync_slot_key)
        raise
    tracked_load_mark_roots = [str(name or "").strip() for name in (load_mark_roots or ()) if str(name or "").strip()]
    try:
        yield ydoc
    finally:
        async def _flush() -> bytes | None:
            update: bytes | None = None
            if not read_only:
                if _state_changed(ydoc, before, timings, prefix=timing_prefix):
                    stage_started = time.perf_counter()
                    update = _encode_diff(ydoc, before)
                    _record_doc_timing(timings, "encode_diff", stage_started, prefix=timing_prefix)
                    owner = _resolve_yjs_write_owner()
                    try:
                        from adaos.services.yjs.governance import govern_primary_doc_write_sync

                        if not governed and not govern_primary_doc_write_sync(
                            webspace_id=webspace_id,
                            owner=owner,
                            root_names=tracked_load_mark_roots,
                            path=",".join(tracked_load_mark_roots) or "primary_shared_doc",
                            source="get_ydoc",
                            channel="yjs.doc.sync",
                            update_bytes=len(update or b""),
                        ):
                            _set_doc_timing(timings, "ystore_write_update", 0.0, prefix=timing_prefix)
                            _set_doc_timing(timings, "room_update", 0.0, prefix=timing_prefix)
                            return None
                    except Exception:
                        _log.debug("failed to apply sync YJS primary-doc governance webspace=%s", webspace_id, exc_info=True)
                    persisted = False
                    try:
                        stage_started = time.perf_counter()
                        async with ystore_write_metadata(
                            root_names=tracked_load_mark_roots,
                            source="get_ydoc",
                            owner=owner,
                            channel="yjs.doc.sync",
                            governed=True,
                        ):
                            if update:
                                await ystore.write_update(update, update_kind="diff")
                            else:
                                await ystore.write_update(b"", update_kind="diff")
                        persisted = True
                        _record_doc_timing(timings, "ystore_write_update", stage_started, prefix=timing_prefix)
                    except Exception:
                        _record_doc_timing(timings, "ystore_write_update", stage_started, prefix=timing_prefix)
                        pass
                    stage_started = time.perf_counter()
                    _schedule_room_update(
                        webspace_id,
                        update,
                        already_persisted=persisted,
                        source="get_ydoc",
                        owner=owner,
                        channel="yjs.doc.sync",
                    )
                    _record_doc_timing(timings, "room_update", stage_started, prefix=timing_prefix)
                else:
                    _set_doc_timing(timings, "encode_diff", 0.0, prefix=timing_prefix)
                    _set_doc_timing(timings, "ystore_write_update", 0.0, prefix=timing_prefix)
                    _set_doc_timing(timings, "room_update", 0.0, prefix=timing_prefix)
            return update

        try:
            _run_blocking(_flush(), timeout_s=operation_timeout_s)
        except Exception as exc:
            _log.warning("get_ydoc flush failed for webspace=%s: %s", webspace_id, exc, exc_info=True)
        finally:
            stage_started = time.perf_counter()
            try:
                stop_result = ystore.stop()
                if inspect.isawaitable(stop_result):
                    _run_blocking(stop_result, timeout_s=operation_timeout_s)
            except Exception:
                pass
            _record_doc_timing(timings, "ystore_stop", stage_started, prefix=timing_prefix)
            _record_doc_timing(timings, "total", session_started, prefix=timing_prefix)
            _release_sync_get_ydoc_slot(sync_slot_key)


@asynccontextmanager
async def async_get_ydoc(
    webspace_id: str,
    *,
    read_only: bool = False,
    prefer_live_room: bool = False,
    publish_live_room: bool = True,
    timings: dict[str, float] | None = None,
    timing_prefix: str = "",
    load_mark_roots: list[str] | tuple[str, ...] | None = None,
    governed: bool = False,
) -> AsyncIterator[Y.YDoc]:
    """
    Async counterpart of :func:`get_ydoc` for use inside running event loops.
    """
    # Debug log omitted to reduce noise in dev logs.
    session_started = time.perf_counter()
    ystore = get_ystore_for_webspace(webspace_id)
    room = _resolve_live_room(webspace_id) if prefer_live_room else None
    use_live_room = _can_access_live_room_directly(room)
    owner_for_session = _resolve_yjs_write_owner() if use_live_room and not read_only else ""
    if use_live_room and not read_only and not governed:
        try:
            from adaos.services.yjs.governance import govern_primary_doc_write

            if not await govern_primary_doc_write(
                webspace_id=webspace_id,
                owner=owner_for_session,
                root_names=[str(name or "").strip() for name in (load_mark_roots or ()) if str(name or "").strip()],
                path=",".join(str(name or "").strip() for name in (load_mark_roots or ()) if str(name or "").strip()) or "primary_shared_doc",
                source="async_get_ydoc.live_room",
                channel="yjs.doc.async.live_room",
            ):
                use_live_room = False
        except Exception:
            _log.debug("failed to apply live-room admission YJS primary-doc governance webspace=%s", webspace_id, exc_info=True)
    ydoc = room.ydoc if use_live_room else Y.YDoc()
    if use_live_room:
        _set_doc_timing(timings, "ystore_start", 0.0, prefix=timing_prefix)
        _set_doc_timing(timings, "ystore_apply_updates", 0.0, prefix=timing_prefix)
    else:
        stage_started = time.perf_counter()
        await ystore.start()
        _record_doc_timing(timings, "ystore_start", stage_started, prefix=timing_prefix)
    try:
        if not use_live_room:
            try:
                stage_started = time.perf_counter()
                await ystore.apply_updates(ydoc)
                _record_doc_timing(timings, "ystore_apply_updates", stage_started, prefix=timing_prefix)
            except BaseException:
                # Treat corrupted updates as "no state"; start from empty doc.
                _record_doc_timing(timings, "ystore_apply_updates", stage_started, prefix=timing_prefix)
                pass
        before = None
        tracked_load_mark_roots = [str(name or "").strip() for name in (load_mark_roots or ()) if str(name or "").strip()]
        if not read_only:
            stage_started = time.perf_counter()
            if use_live_room:
                try:
                    before = Y.encode_state_vector(ydoc)
                    _record_doc_timing(timings, "encode_state_vector", stage_started, prefix=timing_prefix)
                except Exception:
                    _record_doc_timing(timings, "encode_state_vector", stage_started, prefix=timing_prefix)
                    before = None
            else:
                before = await ystore.current_state_vector()
                if before is not None:
                    _set_doc_timing(timings, "encode_state_vector", 0.0, prefix=timing_prefix)
                else:
                    try:
                        before = Y.encode_state_vector(ydoc)
                        _record_doc_timing(timings, "encode_state_vector", stage_started, prefix=timing_prefix)
                    except Exception:
                        _record_doc_timing(timings, "encode_state_vector", stage_started, prefix=timing_prefix)
                        before = None
        yield ydoc
        if not read_only:
            if _state_changed(ydoc, before, timings, prefix=timing_prefix):
                if use_live_room:
                    # Active YRoom instances already fan backend mutations into
                    # websocket broadcast and YStore persistence.
                    stage_started = time.perf_counter()
                    update = _encode_diff(ydoc, before)
                    _record_doc_timing(timings, "encode_diff", stage_started, prefix=timing_prefix)
                    if update:
                        mark_backend_room_update(
                            webspace_id,
                            update,
                            source="async_get_ydoc.live_room",
                            owner=owner_for_session or _resolve_yjs_write_owner(),
                            channel="yjs.doc.async.live_room",
                            root_names=tracked_load_mark_roots,
                            already_persisted=False,
                            governed=True,
                        )
                    _set_doc_timing(timings, "ystore_write_update", 0.0, prefix=timing_prefix)
                    _set_doc_timing(timings, "room_update", 0.0, prefix=timing_prefix)
                else:
                    stage_started = time.perf_counter()
                    update = _encode_diff(ydoc, before)
                    _record_doc_timing(timings, "encode_diff", stage_started, prefix=timing_prefix)
                    owner = _resolve_yjs_write_owner()
                    try:
                        from adaos.services.yjs.governance import govern_primary_doc_write

                        if not governed and not await govern_primary_doc_write(
                            webspace_id=webspace_id,
                            owner=owner,
                            root_names=tracked_load_mark_roots,
                            path=",".join(tracked_load_mark_roots) or "primary_shared_doc",
                            source="async_get_ydoc",
                            channel="yjs.doc.async",
                            update_bytes=len(update or b""),
                        ):
                            _set_doc_timing(timings, "ystore_write_update", 0.0, prefix=timing_prefix)
                            _set_doc_timing(timings, "room_update", 0.0, prefix=timing_prefix)
                            return
                    except Exception:
                        _log.debug("failed to apply async YJS primary-doc governance webspace=%s", webspace_id, exc_info=True)
                    persisted = False
                    try:
                        stage_started = time.perf_counter()
                        async with ystore_write_metadata(
                            root_names=tracked_load_mark_roots,
                            source="async_get_ydoc",
                            owner=owner,
                            channel="yjs.doc.async",
                            governed=True,
                        ):
                            if update:
                                await ystore.write_update(update, update_kind="diff")
                            else:
                                await ystore.write_update(b"", update_kind="diff")
                        persisted = True
                        _record_doc_timing(timings, "ystore_write_update", stage_started, prefix=timing_prefix)
                    except Exception as exc:
                        _record_doc_timing(timings, "ystore_write_update", stage_started, prefix=timing_prefix)
                        _log.warning("async_get_ydoc write_update failed for webspace=%s: %s", webspace_id, exc, exc_info=True)
                    if publish_live_room:
                        stage_started = time.perf_counter()
                        _schedule_room_update(
                            webspace_id,
                            update,
                            already_persisted=persisted,
                            source="async_get_ydoc",
                            owner=owner,
                            channel="yjs.doc.async",
                        )
                        _record_doc_timing(timings, "room_update", stage_started, prefix=timing_prefix)
                    else:
                        _set_doc_timing(timings, "room_update", 0.0, prefix=timing_prefix)
            else:
                _set_doc_timing(timings, "encode_diff", 0.0, prefix=timing_prefix)
                _set_doc_timing(timings, "ystore_write_update", 0.0, prefix=timing_prefix)
                _set_doc_timing(timings, "room_update", 0.0, prefix=timing_prefix)
    finally:
        if use_live_room:
            _set_doc_timing(timings, "ystore_stop", 0.0, prefix=timing_prefix)
        else:
            stage_started = time.perf_counter()
            try:
                ystore.stop()
            except Exception:
                pass
            _record_doc_timing(timings, "ystore_stop", stage_started, prefix=timing_prefix)
        _record_doc_timing(timings, "total", session_started, prefix=timing_prefix)


@asynccontextmanager
async def async_read_ydoc(
    webspace_id: str,
    *,
    prefer_live_room: bool = True,
    timings: dict[str, float] | None = None,
    timing_prefix: str = "",
) -> AsyncIterator[Y.YDoc]:
    async with async_get_ydoc(
        webspace_id,
        read_only=True,
        prefer_live_room=prefer_live_room,
        timings=timings,
        timing_prefix=timing_prefix,
    ) as ydoc:
        yield ydoc


def mutate_live_room(
    webspace_id: str,
    mutator: Callable[[Y.YDoc, Any], None],
    *,
    root_names: list[str] | None = None,
    source: str = "yjs.doc.mutate_live_room",
    owner: str | None = None,
    channel: str = "core.yjs.live_room.sync",
    governed: bool = False,
) -> bool:
    """
    Attempt to mutate the active YDoc directly so connected clients receive the change.
    Returns False if the webspace is not currently hosted in-process.
    """
    room = _resolve_live_room(webspace_id)
    if not _live_room_pipeline_ready(room):
        return False

    def _apply() -> None:
        owner_token = owner or _resolve_yjs_write_owner()
        before: bytes | None = None
        try:
            from adaos.services.yjs.governance import govern_primary_doc_write_sync

            if not governed and not govern_primary_doc_write_sync(
                webspace_id=webspace_id,
                owner=owner_token,
                root_names=list(root_names or []),
                path=",".join(str(item or "").strip() for item in list(root_names or []) if str(item or "").strip()) or "primary_shared_doc",
                source=source,
                channel=channel,
            ):
                return
        except Exception:
            _log.debug("failed to apply live-room YJS primary-doc governance webspace=%s", webspace_id, exc_info=True)
        try:
            try:
                before = Y.encode_state_vector(room.ydoc)
            except Exception:
                before = None
            with ystore_write_metadata_sync(
                root_names=list(root_names or []),
                source=source,
                owner=owner_token,
                channel=channel,
                governed=True,
            ):
                with room.ydoc.begin_transaction() as txn:
                    mutator(room.ydoc, txn)
            update = _encode_diff(room.ydoc, before)
            if update:
                mark_backend_room_update(
                    webspace_id,
                    update,
                    source=source,
                    owner=owner_token,
                    channel=channel,
                    root_names=list(root_names or []),
                    already_persisted=False,
                    governed=True,
                )
        except Exception:
            pass

    return _run_on_room_thread(room, _apply)


def apply_update_to_live_room(
    webspace_id: str,
    update: bytes,
    *,
    root_names: list[str] | None = None,
    source: str = "yjs.doc.apply_update_to_live_room",
    owner: str | None = None,
    channel: str = "core.yjs.live_room.update",
) -> bool:
    """
    Apply a raw Yjs update to the active in-process room (if any).
    Returns False if the webspace is not currently hosted in-process.
    """
    if not update:
        return False
    room = _resolve_live_room(webspace_id)
    if not _live_room_pipeline_ready(room):
        return False

    def _apply() -> None:
        try:
            owner_token = owner or _resolve_yjs_write_owner()
            with ystore_write_metadata_sync(
                root_names=list(root_names or []),
                source=source,
                owner=owner_token,
                channel=channel,
            ):
                Y.apply_update(room.ydoc, update)
            mark_backend_room_update(
                webspace_id,
                update,
                source=source,
                owner=owner_token,
                channel=channel,
                root_names=list(root_names or []),
                already_persisted=False,
                governed=False,
            )
        except Exception:
            pass

    return _run_on_room_thread(room, _apply)


__all__ = [
    "get_ydoc",
    "async_get_ydoc",
    "async_read_ydoc",
    "try_read_live_map_value",
    "mutate_live_room",
    "apply_update_to_live_room",
]
