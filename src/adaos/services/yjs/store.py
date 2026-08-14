from __future__ import annotations

import logging
import os
import time
import contextlib
import contextvars
import asyncio
import inspect
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Tuple

import anyio
import y_py as Y
from anyio import Event, TASK_STATUS_IGNORED
from anyio.abc import TaskStatus
from ypy_websocket.ystore import BaseYStore, YDocNotFound

from adaos.services.agent_context import get_ctx
from adaos.sdk.core.decorators import subscribe

_log = logging.getLogger("adaos.yjs.ystore")

_SUPPRESS_NOTIFY: contextvars.ContextVar[bool] = contextvars.ContextVar("adaos_ystore_suppress_notify", default=False)
_WRITE_META: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("adaos_ystore_write_meta", default=None)
_GLOBAL_WRITE_LISTENERS: list[Callable[[str, bytes], Any]] = []


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except Exception:
        value = int(default)
    return max(int(minimum), value)


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)) or str(default))
    except Exception:
        value = float(default)
    return max(float(minimum), value)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


_YSTORE_APPLY_YIELD_BYTES = _env_int("ADAOS_YSTORE_APPLY_YIELD_BYTES", 512 * 1024, minimum=0)
_YSTORE_APPLY_YIELD_MS = _env_float("ADAOS_YSTORE_APPLY_YIELD_MS", 25.0, minimum=0.0)
_YSTORE_APPLY_SLOW_UPDATE_MS = _env_float("ADAOS_YSTORE_APPLY_SLOW_UPDATE_MS", 250.0, minimum=0.0)
_YSTORE_SNAPSHOT_PREFLIGHT = _env_flag("ADAOS_YSTORE_SNAPSHOT_PREFLIGHT", True)
_YSTORE_SNAPSHOT_PREFLIGHT_TIMEOUT_S = _env_float("ADAOS_YSTORE_SNAPSHOT_PREFLIGHT_TIMEOUT_S", 5.0, minimum=0.25)
_YSTORE_SNAPSHOT_SUFFIX = ".ysnap"


def _is_fatal_base_exception(exc: BaseException) -> bool:
    return isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit))


def add_ystore_write_listener(cb: Callable[..., Any]) -> Callable[[], None]:
    """
    Register a global listener called on every YStore write:
      cb(webspace_id: str, update: bytes[, metadata: dict]) -> Any

    Returns a function that removes the listener.
    """
    _GLOBAL_WRITE_LISTENERS.append(cb)

    def _remove() -> None:
        try:
            _GLOBAL_WRITE_LISTENERS.remove(cb)
        except ValueError:
            return

    return _remove


@contextlib.asynccontextmanager
async def suppress_ystore_write_notifications():
    token = _SUPPRESS_NOTIFY.set(True)
    try:
        yield
    finally:
        try:
            _SUPPRESS_NOTIFY.reset(token)
        except Exception:
            pass


@contextlib.asynccontextmanager
async def ystore_write_metadata(
    *,
    root_names: list[str] | tuple[str, ...] | None = None,
    source: str | None = None,
    owner: str | None = None,
    channel: str | None = None,
    governed: bool | None = None,
):
    payload = dict(_WRITE_META.get() or {})
    names = [str(name or "").strip() for name in (root_names or ()) if str(name or "").strip()]
    if names:
        payload["root_names"] = names
    if source is not None:
        payload["source"] = str(source or "").strip() or None
    if owner is not None:
        payload["owner"] = str(owner or "").strip() or None
    if channel is not None:
        payload["channel"] = str(channel or "").strip() or None
    if governed is not None:
        payload["governed"] = bool(governed)
    token = _WRITE_META.set(payload)
    try:
        yield
    finally:
        try:
            _WRITE_META.reset(token)
        except Exception:
            pass


@contextlib.contextmanager
def ystore_write_metadata_sync(
    *,
    root_names: list[str] | tuple[str, ...] | None = None,
    source: str | None = None,
    owner: str | None = None,
    channel: str | None = None,
    governed: bool | None = None,
):
    payload = dict(_WRITE_META.get() or {})
    names = [str(name or "").strip() for name in (root_names or ()) if str(name or "").strip()]
    if names:
        payload["root_names"] = names
    if source is not None:
        payload["source"] = str(source or "").strip() or None
    if owner is not None:
        payload["owner"] = str(owner or "").strip() or None
    if channel is not None:
        payload["channel"] = str(channel or "").strip() or None
    if governed is not None:
        payload["governed"] = bool(governed)
    token = _WRITE_META.set(payload)
    try:
        yield
    finally:
        try:
            _WRITE_META.reset(token)
        except Exception:
            pass


def _listener_accepts_meta(cb: Callable[..., Any]) -> bool:
    try:
        sig = inspect.signature(cb)
    except Exception:
        return False
    params = list(sig.parameters.values())
    if any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params):
        return True
    positional = [
        param
        for param in params
        if param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    return len(positional) >= 3


def _notify_write_listeners(webspace_id: str, update: bytes) -> None:
    if _SUPPRESS_NOTIFY.get():
        return
    if not _GLOBAL_WRITE_LISTENERS:
        return
    # Best-effort, never block the writer.
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except Exception:
        loop = None
    meta = dict(_WRITE_META.get() or {})
    for cb in list(_GLOBAL_WRITE_LISTENERS):
        try:
            if meta and _listener_accepts_meta(cb):
                res = cb(webspace_id, update, meta)
            else:
                res = cb(webspace_id, update)
            if loop is not None:
                try:
                    import asyncio

                    if asyncio.iscoroutine(res):
                        loop.create_task(res)
                except Exception:
                    pass
        except Exception:
            continue


def current_ystore_write_metadata() -> dict[str, Any]:
    return dict(_WRITE_META.get() or {})


def _encode_snapshot_update(updates: List[Tuple[bytes, bytes, float]]) -> bytes:
    """
    Heavy snapshot encoding performed in a worker thread.
    """
    if not updates:
        return b""
    if len(updates) == 1:
        return bytes(updates[0][0] or b"")

    ydoc = None
    try:
        ydoc = Y.YDoc()
        for update, _meta, _ts in updates:
            Y.apply_update(ydoc, update)  # type: ignore[arg-type]
        return Y.encode_state_as_update(ydoc)  # type: ignore[arg-type]
    except BaseException as exc:
        if _is_fatal_base_exception(exc):
            raise
        raise RuntimeError(f"yjs_snapshot_encode_failed:{type(exc).__name__}:{exc}") from None
    finally:
        ydoc = None


def _encode_snapshot_artifacts(updates: List[Tuple[bytes, bytes, float]]) -> tuple[bytes, bytes]:
    """
    Encode a compacted snapshot together with its state vector in one pass.
    """
    if not updates:
        return b"", b""
    ydoc = None
    try:
        ydoc = Y.YDoc()
        for update, _meta, _ts in updates:
            Y.apply_update(ydoc, update)  # type: ignore[arg-type]
        return (
            Y.encode_state_as_update(ydoc),  # type: ignore[arg-type]
            Y.encode_state_vector(ydoc),  # type: ignore[arg-type]
        )
    except BaseException as exc:
        if _is_fatal_base_exception(exc):
            raise
        raise RuntimeError(f"yjs_snapshot_artifacts_failed:{type(exc).__name__}:{exc}") from None
    finally:
        ydoc = None


def _decode_state_vector_from_snapshot(snapshot: bytes) -> bytes:
    """
    Recover a state vector from one compacted snapshot update.
    """
    if not snapshot:
        return b""
    ydoc = None
    try:
        ydoc = Y.YDoc()
        Y.apply_update(ydoc, snapshot)  # type: ignore[arg-type]
        return Y.encode_state_vector(ydoc)  # type: ignore[arg-type]
    except BaseException as exc:
        if _is_fatal_base_exception(exc):
            raise
        raise RuntimeError(f"yjs_state_vector_decode_failed:{type(exc).__name__}:{exc}") from None
    finally:
        ydoc = None


def _preflight_snapshot_file(path: Path) -> tuple[bool, str]:
    if not _YSTORE_SNAPSHOT_PREFLIGHT:
        return True, "disabled"
    if not path.exists():
        return True, "missing"
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "import y_py as Y\n"
        "data = Path(sys.argv[1]).read_bytes()\n"
        "doc = Y.YDoc()\n"
        "Y.apply_update(doc, data)\n"
        "Y.encode_state_vector(doc)\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=float(_YSTORE_SNAPSHOT_PREFLIGHT_TIMEOUT_S),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        _log.warning("YStore snapshot preflight failed open path=%s: %s", path, exc, exc_info=True)
        return True, f"preflight_error:{type(exc).__name__}"
    if result.returncode == 0:
        return True, "ok"
    stderr = (result.stderr or b"")[:500].decode("utf-8", errors="replace").strip()
    return False, f"returncode={result.returncode} stderr={stderr}"


def _quarantine_corrupt_snapshot(path: Path, reason: str) -> Path | None:
    try:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        target = path.with_name(f"{path.name}.corrupt.{stamp}")
        suffix = 0
        while target.exists():
            suffix += 1
            target = path.with_name(f"{path.name}.corrupt.{stamp}.{suffix}")
        path.replace(target)
        _log.warning("YStore corrupt snapshot quarantined path=%s target=%s reason=%s", path, target, reason)
        return target
    except Exception:
        _log.warning("failed to quarantine corrupt YStore snapshot path=%s reason=%s", path, reason, exc_info=True)
        return None


def _persist_snapshot(path: Path, snapshot: bytes) -> int:
    """
    Heavy snapshot writing performed in a worker thread.
    """
    if not snapshot:
        try:
            path.unlink()
        except FileNotFoundError:
            return 0
        except Exception as exc:
            _log.warning("failed to remove stale YStore snapshot %s: %s", path, exc, exc_info=True)
        return 0
    tmp = Path(str(path) + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(snapshot)
        tmp.replace(path)
        _log.debug("YStore snapshot written for webspace=%s path=%s", path.name.removesuffix(_YSTORE_SNAPSHOT_SUFFIX), path)
        return len(snapshot)
    except Exception as exc:
        _log.warning("failed to write YStore snapshot %s: %s", path, exc, exc_info=True)
        return 0


def ystores_root() -> Path:
    """
    Root directory for Yjs store snapshots, ensuring it exists.

    Even though the live store is in-memory, we keep periodic snapshots here
    so that webspaces can be restored across restarts.
    """
    ctx = get_ctx()
    root = ctx.paths.state_dir() / "ystores"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ystore_path_for_webspace(webspace_id: str) -> Path:
    """
    Map a webspace id to a filesystem path for its raw Yjs snapshot.
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in webspace_id)
    return ystores_root() / f"{safe}{_YSTORE_SNAPSHOT_SUFFIX}"


def ystore_snapshot_exists(webspace_id: str) -> bool:
    try:
        return ystore_path_for_webspace(str(webspace_id or "")).exists()
    except Exception:
        return False


class AdaosMemoryYStore(BaseYStore):
    """
    In-memory YStore with optional periodic snapshots to disk.

    - All Y updates are kept in-memory in the current process.
    - `read()` replays the in-memory log or, on first access, a persisted
      snapshot from disk (if present).
    - `backup_to_disk()` compresses the current log into a single
      `Y.encode_state_as_update(ydoc)` blob and writes it atomically.
    - Hot-path callers may append incremental diff updates directly via
      `write_update()` instead of re-encoding the full document on every flush.
    """

    def __init__(self, path: str, *, document_ttl: float | None = None):
        # BaseYStore expects these attributes; its __init__ is abstract/no-op.
        self.path = path
        self.metadata_callback = None
        self.document_ttl = document_ttl
        self.max_updates = _env_int("ADAOS_YSTORE_MAX_UPDATES", 33, minimum=8)
        self.replay_window = min(
            self.max_updates - 1,
            _env_int("ADAOS_YSTORE_REPLAY_WINDOW", 32, minimum=0),
        )
        self.max_replay_bytes = _env_int("ADAOS_YSTORE_MAX_REPLAY_BYTES", 8 * 1024 * 1024, minimum=0)
        default_compact_target = int(self.max_replay_bytes // 2) if self.max_replay_bytes > 0 else 0
        self.compact_target_replay_bytes = _env_int(
            "ADAOS_YSTORE_COMPACT_TARGET_REPLAY_BYTES",
            default_compact_target,
            minimum=0,
        )
        self.compact_target_replay_entries = _env_int(
            "ADAOS_YSTORE_COMPACT_TARGET_REPLAY_ENTRIES",
            max(0, int(self.replay_window) // 2),
            minimum=0,
        )
        self.auto_backup_after_compact = _env_flag("ADAOS_YSTORE_AUTOBACKUP_AFTER_COMPACT", True)
        self.auto_backup_cooldown_sec = _env_float("ADAOS_YSTORE_AUTOBACKUP_COOLDOWN_SEC", 30.0, minimum=0.0)
        self.auto_backup_debounce_sec = _env_float("ADAOS_YSTORE_AUTOBACKUP_DEBOUNCE_SEC", 0.5, minimum=0.0)
        self.auto_backup_large_update_bytes = _env_int(
            "ADAOS_YSTORE_AUTOBACKUP_LARGE_UPDATE_BYTES",
            1024 * 1024,
            minimum=0,
        )
        self.auto_backup_large_update_debounce_sec = _env_float(
            "ADAOS_YSTORE_AUTOBACKUP_LARGE_UPDATE_DEBOUNCE_SEC",
            30.0,
            minimum=0.0,
        )
        self.auto_backup_replay_pressure_bytes = _env_int(
            "ADAOS_YSTORE_AUTOBACKUP_REPLAY_PRESSURE_BYTES",
            1024 * 1024,
            minimum=0,
        )
        default_replay_pressure_entries = min(
            max(4, int(self.replay_window) // 2),
            int(self.replay_window),
        )
        self.auto_backup_replay_pressure_entries = _env_int(
            "ADAOS_YSTORE_AUTOBACKUP_REPLAY_PRESSURE_ENTRIES",
            default_replay_pressure_entries,
            minimum=0,
        )
        self.auto_backup_replay_pressure_debounce_sec = _env_float(
            "ADAOS_YSTORE_AUTOBACKUP_REPLAY_PRESSURE_DEBOUNCE_SEC",
            1.0,
            minimum=0.0,
        )
        self.snapshot_compaction_failure_backoff_sec = _env_float(
            "ADAOS_YSTORE_SNAPSHOT_COMPACTION_FAILURE_BACKOFF_SEC",
            30.0,
            minimum=0.0,
        )
        self.snapshot_compaction_failure_backoff_max_sec = _env_float(
            "ADAOS_YSTORE_SNAPSHOT_COMPACTION_FAILURE_BACKOFF_MAX_SEC",
            300.0,
            minimum=0.0,
        )
        self._lock = threading.RLock()
        self._updates: List[Tuple[bytes, bytes, float]] = []
        self._base_snapshot_present = False
        self._loaded_from_disk = False
        self._started: Event | None = Event()
        self._starting: bool = False
        self._task_group = None
        self._running: bool = False
        self._write_total = 0
        self._compact_total = 0
        self._backup_total = 0
        self._backup_fast_path_total = 0
        self._backup_skipped_total = 0
        self._backup_failed_total = 0
        self._backup_by_kind: dict[str, int] = {}
        self._backup_written_by_kind: dict[str, int] = {}
        self._backup_skipped_by_kind: dict[str, int] = {}
        self._backup_failed_by_kind: dict[str, int] = {}
        self._auto_backup_total = 0
        self._diff_write_total = 0
        self._snapshot_write_total = 0
        self._write_skipped_total = 0
        self._apply_total = 0
        self._applied_update_total = 0
        self._applied_update_bytes = 0
        self._last_write_at = 0.0
        self._last_compact_at = 0.0
        self._last_compact_reason = ""
        self._last_backup_at = 0.0
        self._last_auto_backup_at = 0.0
        self._last_auto_backup_reason = ""
        self._auto_backup_retry_reason = ""
        self._last_backup_kind = ""
        self._last_backup_mode = ""
        self._last_backup_skip_reason = ""
        self._last_backup_written_bytes = 0
        self._last_backup_error = ""
        self._last_backup_failed_at = 0.0
        self._last_apply_at = 0.0
        self._last_loaded_from_disk_at = 0.0
        self._last_update_bytes = 0
        self._last_snapshot_bytes = 0
        self._last_apply_update_total = 0
        self._last_apply_bytes = 0
        self._apply_yield_total = 0
        self._last_apply_yield_total = 0
        self._last_apply_slow_update_ms = 0.0
        self._last_apply_slow_update_bytes = 0
        self._auto_backup_inflight = False
        self._snapshot_compaction_failure_streak = 0
        self._snapshot_compaction_retry_not_before = 0.0
        self._snapshot_compaction_suppressed_total = 0
        self._last_snapshot_compaction_error = ""
        self._last_snapshot_compaction_failed_at = 0.0
        self._generation = 0
        self._persisted_generation = -1
        self._persisted_snapshot_bytes = 0
        self._base_state_vector: bytes | None = None
        self._state_vector_fast_path_total = 0
        self._state_vector_compute_total = 0
        self._state_vector_cache_miss_total = 0
        self._last_apply_mode = ""
        self._disk_snapshot_prepend_total = 0
        self._disk_snapshot_skip_nonempty_total = 0
        self._last_disk_load_mode = ""
        self._last_disk_snapshot_bytes = 0

    async def replace_snapshot_update(
        self,
        snapshot: bytes,
        *,
        state_vector: bytes | None = None,
        backup_kind: str = "replace_snapshot",
        persist_snapshot: bool = True,
        notify: bool = True,
    ) -> dict[str, Any]:
        """
        Replace the in-memory/runtime store with a single base snapshot.

        This is intentionally different from appending a "full" Yjs update:
        applying an update produced from a fresh doc on top of old CRDT history
        does not delete keys that no longer exist. Callers that rebuild a whole
        materialized view from authoritative sources need replacement semantics.
        """
        payload = bytes(snapshot or b"")
        state_vector_payload = bytes(state_vector or b"") or None
        kind_token = str(backup_kind or "").strip() or "replace_snapshot"
        path = ystore_path_for_webspace(self.path)
        persist_started = time.perf_counter()
        written_bytes = await anyio.to_thread.run_sync(_persist_snapshot, path, payload) if persist_snapshot else 0
        persist_ms = round((time.perf_counter() - persist_started) * 1000.0, 3)
        metadata = await self.get_metadata()
        now = time.time()
        with self._lock:
            previous_entries = len(self._updates)
            previous_bytes = sum(len(update) for update, _meta, _ts in self._updates)
            self._updates = [(payload, metadata, now)] if payload else []
            self._base_snapshot_present = bool(payload)
            self._base_state_vector = state_vector_payload
            self._loaded_from_disk = True
            self._generation += 1
            if persist_snapshot:
                self._persisted_generation = int(self._generation)
                self._persisted_snapshot_bytes = int(written_bytes or len(payload))
            self._write_total += 1
            self._snapshot_write_total += 1
            self._last_write_at = now
            self._last_update_bytes = len(payload)
            self._last_snapshot_bytes = len(payload)
            self._compact_total += 1
            self._last_compact_at = now
            self._last_compact_reason = kind_token
            self._backup_total += 1
            self._backup_by_kind[kind_token] = int(self._backup_by_kind.get(kind_token) or 0) + 1
            if written_bytes:
                self._backup_written_by_kind[kind_token] = int(self._backup_written_by_kind.get(kind_token) or 0) + 1
            else:
                self._backup_skipped_total += 1
                self._backup_skipped_by_kind[kind_token] = int(self._backup_skipped_by_kind.get(kind_token) or 0) + 1
            self._last_backup_kind = kind_token
            self._last_backup_mode = "replace_snapshot" if persist_snapshot else "replace_snapshot:deferred_disk"
            self._last_backup_skip_reason = "" if written_bytes else ("deferred_disk" if not persist_snapshot else "empty_snapshot")
            self._last_backup_written_bytes = int(written_bytes)
            self._last_backup_error = ""
            self._last_backup_at = now
            self._last_disk_load_mode = "replace_snapshot"
            if persist_snapshot:
                self._last_disk_snapshot_bytes = int(written_bytes or len(payload))
        notify_ms = 0.0
        if notify:
            notify_started = time.perf_counter()
            try:
                _notify_write_listeners(self.path, payload)
            except Exception:
                pass
            notify_ms = round((time.perf_counter() - notify_started) * 1000.0, 3)
        return {
            "ok": True,
            "webspace_id": self.path,
            "backup_kind": kind_token,
            "snapshot_bytes": len(payload),
            "state_vector_bytes": len(state_vector_payload or b""),
            "written_bytes": int(written_bytes),
            "persist_snapshot": bool(persist_snapshot),
            "persist_ms": persist_ms,
            "notify": bool(notify),
            "notify_ms": notify_ms,
            "previous_entries": int(previous_entries),
            "previous_bytes": int(previous_bytes),
            "update_log_entries": 1 if payload else 0,
        }

    async def start(self, *, task_status: TaskStatus[None] = TASK_STATUS_IGNORED):
        """
        For the in-memory store, start/stop are lightweight and idempotent.
        """
        if self._running:
            task_status.started()
            return
        self._running = True
        started = getattr(self, "started", None) or self._started
        if started is not None:
            started.set()
        task_status.started()

    def stop(self) -> None:
        self._running = False

    def _clear_runtime_state_locked(self) -> tuple[int, int]:
        released_entries = len(self._updates)
        released_bytes = sum(len(update) for update, _meta, _ts in self._updates)
        self._updates.clear()
        self._base_snapshot_present = False
        self._loaded_from_disk = False
        self._running = False
        self._auto_backup_inflight = False
        self._auto_backup_retry_reason = ""
        self._snapshot_compaction_failure_streak = 0
        self._snapshot_compaction_retry_not_before = 0.0
        self._generation = 0
        self._persisted_generation = -1
        self._persisted_snapshot_bytes = 0
        self._base_state_vector = None
        self._last_apply_update_total = 0
        self._last_apply_bytes = 0
        self._last_apply_yield_total = 0
        self._last_apply_slow_update_ms = 0.0
        self._last_apply_slow_update_bytes = 0
        self._last_apply_mode = ""
        self._last_loaded_from_disk_at = 0.0
        return released_entries, released_bytes

    async def evict_runtime_state(self) -> dict[str, int]:
        with self._lock:
            released_entries, released_bytes = self._clear_runtime_state_locked()
        return {
            "released_update_entries": int(released_entries),
            "released_update_bytes": int(released_bytes),
        }

    async def discard_corrupt_state(self, *, delete_snapshot: bool = True) -> dict[str, Any]:
        """
        Drop replay state after a Y.apply_update panic.

        Continuing to append new updates after a corrupt base snapshot keeps the
        store unrecoverable: every later reader replays the bad base before the
        fresh repair update. This helper keeps the store object in cache but
        removes the unusable runtime and persisted base so the next write starts
        a clean history.
        """
        base_snapshot = b""
        base_metadata = b""
        base_timestamp = time.time()
        base_state_vector: bytes | None = None
        preserve_base = False
        with self._lock:
            # If replay reached the first entry, the durable base itself was
            # accepted and only a later runtime diff is corrupt. Preserve that
            # last known-good checkpoint instead of deleting the complete
            # webspace. The caller will replay it into a fresh YDoc.
            preserve_base = bool(
                self._base_snapshot_present
                and self._updates
                and int(self._last_apply_update_total or 0) >= 1
            )
            if preserve_base:
                base_snapshot, base_metadata, base_timestamp = self._updates[0]
                base_snapshot = bytes(base_snapshot or b"")
                base_metadata = bytes(base_metadata or b"")
                base_state_vector = bytes(self._base_state_vector or b"") or None
            released_entries, released_bytes = self._clear_runtime_state_locked()
            if preserve_base and base_snapshot:
                self._updates = [(base_snapshot, base_metadata, base_timestamp)]
                self._base_snapshot_present = True
                self._loaded_from_disk = True
                self._generation = 1
                self._base_state_vector = base_state_vector
                self._last_disk_load_mode = "recovered_base_after_corrupt_tail"
                self._last_disk_snapshot_bytes = len(base_snapshot)
                released_entries = max(0, released_entries - 1)
                released_bytes = max(0, released_bytes - len(base_snapshot))
        released = {
            "released_update_entries": int(released_entries),
            "released_update_bytes": int(released_bytes),
        }
        removed_snapshot = False
        persisted_base_bytes = 0
        path = ystore_path_for_webspace(self.path)
        if preserve_base and base_snapshot:
            persisted_base_bytes = await anyio.to_thread.run_sync(
                _persist_snapshot,
                path,
                base_snapshot,
            )
            if base_state_vector is None:
                try:
                    base_state_vector = await anyio.to_thread.run_sync(
                        _decode_state_vector_from_snapshot,
                        base_snapshot,
                    )
                except Exception:
                    base_state_vector = None
            with self._lock:
                if self._base_snapshot_present and self._updates and self._updates[0][0] == base_snapshot:
                    self._base_state_vector = bytes(base_state_vector or b"") or None
                    self._persisted_generation = int(self._generation)
                    self._persisted_snapshot_bytes = int(persisted_base_bytes or len(base_snapshot))
        elif delete_snapshot:
            try:
                if path.exists():
                    path.unlink()
                    removed_snapshot = True
            except Exception:
                _log.warning("failed to remove corrupt YStore snapshot for webspace=%s", self.path, exc_info=True)
        with self._lock:
            self._loaded_from_disk = True
            if not preserve_base:
                self._last_disk_load_mode = "discarded_corrupt_state"
                self._last_disk_snapshot_bytes = 0
                self._persisted_snapshot_bytes = 0
                self._persisted_generation = 0
                self._base_state_vector = None
        return {
            "ok": True,
            "webspace_id": self.path,
            "snapshot_deleted": removed_snapshot,
            "base_snapshot_preserved": bool(preserve_base and base_snapshot),
            "base_snapshot_bytes": len(base_snapshot) if preserve_base else 0,
            **released,
        }

    async def write(self, data: bytes) -> None:  # type: ignore[override]
        """
        Append an update to the in-memory log, with optional TTL-based squashing.
        """
        await self.write_update(data)

    async def write_update(
        self,
        data: bytes,
        *,
        update_kind: str = "raw",
        notify: bool = True,
        state_vector: bytes | None = None,
    ) -> bool:
        """
        Append one already-encoded Yjs update to the in-memory log.

        `update_kind` is diagnostic only and lets runtime snapshots distinguish
        full-state writes from incremental diff writes.
        """
        payload = bytes(data or b"")
        now = time.time()
        if not payload:
            with self._lock:
                self._write_skipped_total += 1
            return False

        metadata = await self.get_metadata()
        governance_meta = dict(_WRITE_META.get() or {})
        if not bool(governance_meta.get("governed")):
            try:
                from adaos.services.yjs.governance import govern_primary_doc_write

                root_names = governance_meta.get("root_names")
                if not isinstance(root_names, (list, tuple)):
                    root_names = []
                allowed = await govern_primary_doc_write(
                    webspace_id=self.path,
                    owner=str(governance_meta.get("owner") or "").strip() or None,
                    root_names=[str(item or "").strip() for item in root_names if str(item or "").strip()],
                    path=",".join(str(item or "").strip() for item in root_names if str(item or "").strip()) or "primary_shared_doc",
                    source=str(governance_meta.get("source") or "ystore.write_update"),
                    channel=str(governance_meta.get("channel") or update_kind or "ystore.write_update"),
                    update_bytes=len(payload),
                )
                if not allowed:
                    with self._lock:
                        self._write_skipped_total += 1
                    return False
            except Exception:
                _log.debug("failed to apply YStore primary-doc governance webspace=%s", self.path, exc_info=True)
        auto_backup_reason: str | None = None
        auto_backup_debounce_override: float | None = None
        with self._lock:
            was_empty = not self._updates
            self._write_total += 1
            if update_kind == "diff":
                self._diff_write_total += 1
            elif update_kind == "snapshot":
                self._snapshot_write_total += 1
                if not self._updates:
                    self._base_snapshot_present = True
                    self._base_state_vector = bytes(state_vector or b"") or None
            self._last_write_at = now
            self._last_update_bytes = len(payload)
            if update_kind != "snapshot" or not was_empty:
                self._base_state_vector = None
            compaction_failed = False
            if self.document_ttl is not None and self._updates:
                last_ts = self._updates[-1][2]
                if now - last_ts > self.document_ttl:
                    if self._snapshot_compaction_backoff_remaining_locked(now) > 0.0:
                        self._snapshot_compaction_suppressed_total += 1
                    else:
                        try:
                            self._compact_updates_locked(now=now, keep_tail=0, reason="document_ttl")
                        except BaseException as exc:
                            if _is_fatal_base_exception(exc):
                                raise
                            compaction_failed = True
                            self._record_snapshot_compaction_failure_locked(exc, now=now)
                            _log.warning(
                                "YStore TTL compaction failed; retaining replay log webspace=%s error=%s",
                                self.path,
                                self._last_snapshot_compaction_error,
                                exc_info=True,
                            )

            self._updates.append((payload, metadata, now))
            compact_reason = self._replay_compaction_reason_locked()
            if compact_reason:
                if compaction_failed or self._snapshot_compaction_backoff_remaining_locked(now) > 0.0:
                    self._snapshot_compaction_suppressed_total += 1
                else:
                    try:
                        self._compact_updates_locked(now=now, keep_tail=self.replay_window, reason=compact_reason)
                    except BaseException as exc:
                        if _is_fatal_base_exception(exc):
                            raise
                        compaction_failed = True
                        self._record_snapshot_compaction_failure_locked(exc, now=now)
                        _log.warning(
                            "YStore replay compaction failed; retaining replay log webspace=%s reason=%s error=%s",
                            self.path,
                            compact_reason,
                            self._last_snapshot_compaction_error,
                            exc_info=True,
                        )
                if (
                    not compaction_failed
                    and self.auto_backup_after_compact
                    and not self._auto_backup_inflight
                    and self._snapshot_compaction_backoff_remaining_locked(now) <= 0.0
                    and (self._last_auto_backup_at <= 0.0 or now - self._last_auto_backup_at >= self.auto_backup_cooldown_sec)
                ):
                    self._auto_backup_inflight = True
                    auto_backup_reason = compact_reason
            if (
                auto_backup_reason is None
                and not compaction_failed
                and self.auto_backup_after_compact
                and self.auto_backup_large_update_bytes > 0
                and len(payload) >= int(self.auto_backup_large_update_bytes)
                and not self._auto_backup_inflight
                and self._snapshot_compaction_backoff_remaining_locked(now) <= 0.0
            ):
                cooldown_remaining = (
                    0.0
                    if self._last_auto_backup_at <= 0.0
                    else max(0.0, float(self.auto_backup_cooldown_sec) - (now - self._last_auto_backup_at))
                )
                self._auto_backup_inflight = True
                auto_backup_reason = "large_update"
                auto_backup_debounce_override = float(self.auto_backup_large_update_debounce_sec) + cooldown_remaining
            if (
                auto_backup_reason is None
                and not compaction_failed
                and self.auto_backup_after_compact
                and not self._auto_backup_inflight
                and self._snapshot_compaction_backoff_remaining_locked(now) <= 0.0
            ):
                pressure_reason = self._replay_pressure_reason_locked()
                if pressure_reason:
                    cooldown_remaining = (
                        0.0
                        if self._last_auto_backup_at <= 0.0
                        else max(0.0, float(self.auto_backup_cooldown_sec) - (now - self._last_auto_backup_at))
                    )
                    self._auto_backup_inflight = True
                    auto_backup_reason = pressure_reason
                    auto_backup_debounce_override = float(self.auto_backup_replay_pressure_debounce_sec) + cooldown_remaining
            self._generation += 1
        if notify:
            try:
                _notify_write_listeners(self.path, payload)
            except Exception:
                pass
        if auto_backup_reason:
            if not self._schedule_auto_backup(reason=auto_backup_reason, debounce_sec=auto_backup_debounce_override):
                with self._lock:
                    self._auto_backup_inflight = False
        return True

    async def encode_state_as_update(self, ydoc: Y.YDoc) -> None:  # type: ignore[override]
        update = Y.encode_state_as_update(ydoc)  # type: ignore[arg-type]
        state_vector = Y.encode_state_vector(ydoc)  # type: ignore[arg-type]
        await self.write_update(update, update_kind="snapshot", state_vector=state_vector)

    async def apply_updates(self, ydoc: Y.YDoc) -> None:  # type: ignore[override]
        await self._load_from_disk_if_needed()
        with self._lock:
            if not self._updates:
                raise YDocNotFound
            updates = list(self._updates)
            apply_mode = "base_snapshot" if len(self._updates) == 1 and self._base_snapshot_present else "replay_log"

        now = time.time()
        applied_total = 0
        applied_bytes = 0
        yielded_total = 0
        budget_bytes = int(_YSTORE_APPLY_YIELD_BYTES)
        budget_s = float(_YSTORE_APPLY_YIELD_MS) / 1000.0
        slow_update_s = float(_YSTORE_APPLY_SLOW_UPDATE_MS) / 1000.0
        bytes_since_yield = 0
        last_yield = time.perf_counter()
        slow_update_ms = 0.0
        slow_update_bytes = 0
        try:
            for update, _metadata, _ts in updates:
                update_started = time.perf_counter()
                Y.apply_update(ydoc, update)  # type: ignore[arg-type]
                update_elapsed = time.perf_counter() - update_started
                applied_total += 1
                update_bytes = len(update)
                applied_bytes += update_bytes
                bytes_since_yield += update_bytes
                if (
                    slow_update_s > 0.0
                    and update_elapsed >= slow_update_s
                    and update_elapsed * 1000.0 > slow_update_ms
                ):
                    slow_update_ms = update_elapsed * 1000.0
                    slow_update_bytes = update_bytes
                if applied_total < len(updates) and (
                    (budget_bytes > 0 and bytes_since_yield >= budget_bytes)
                    or (budget_s > 0.0 and time.perf_counter() - last_yield >= budget_s)
                ):
                    yielded_total += 1
                    bytes_since_yield = 0
                    last_yield = time.perf_counter()
                    await asyncio.sleep(0)
        finally:
            with self._lock:
                self._apply_total += 1
                self._applied_update_total += applied_total
                self._applied_update_bytes += applied_bytes
                self._last_apply_at = now
                self._last_apply_update_total = applied_total
                self._last_apply_bytes = applied_bytes
                self._apply_yield_total += yielded_total
                self._last_apply_yield_total = yielded_total
                self._last_apply_slow_update_ms = round(slow_update_ms, 3)
                self._last_apply_slow_update_bytes = int(slow_update_bytes)
                self._last_apply_mode = apply_mode
        if slow_update_ms > 0.0:
            _log.warning(
                "YStore apply_update blocked event loop webspace=%s update_bytes=%d elapsed_ms=%.1f updates=%d",
                self.path,
                slow_update_bytes,
                slow_update_ms,
                len(updates),
            )

    async def current_state_vector(self) -> bytes | None:
        """
        Return the full-document state vector when the store is already
        compacted to a single base snapshot.

        This lets detached YDoc sessions skip one extra encode pass on entry.
        """
        await self._load_from_disk_if_needed()
        snapshot = b""
        with self._lock:
            if len(self._updates) != 1 or not self._base_snapshot_present:
                self._state_vector_cache_miss_total += 1
                return None
            if self._base_state_vector is not None:
                self._state_vector_fast_path_total += 1
                return bytes(self._base_state_vector)
            snapshot = bytes(self._updates[0][0] or b"")

        try:
            state_vector = await anyio.to_thread.run_sync(_decode_state_vector_from_snapshot, snapshot)
        except Exception:
            with self._lock:
                self._state_vector_cache_miss_total += 1
            return None

        with self._lock:
            if len(self._updates) == 1 and self._base_snapshot_present:
                self._base_state_vector = bytes(state_vector or b"") or None
                self._state_vector_compute_total += 1
                self._state_vector_fast_path_total += 1
                return bytes(self._base_state_vector or b"") or None
            self._state_vector_cache_miss_total += 1
        return None

    def _replay_window_bytes_locked(self, updates: List[Tuple[bytes, bytes, float]] | None = None) -> int:
        snapshot = list(updates if updates is not None else self._updates)
        if not snapshot:
            return 0
        start_idx = 1 if self._base_snapshot_present and len(snapshot) > 0 else 0
        return sum(len(update) for update, _meta, _ts in snapshot[start_idx:])

    def _snapshot_compaction_backoff_remaining_locked(self, now: float) -> float:
        return max(0.0, float(self._snapshot_compaction_retry_not_before) - float(now))

    def _record_snapshot_compaction_failure_locked(self, exc: BaseException, *, now: float) -> None:
        self._snapshot_compaction_failure_streak += 1
        base_delay = float(self.snapshot_compaction_failure_backoff_sec)
        max_delay = max(base_delay, float(self.snapshot_compaction_failure_backoff_max_sec))
        delay = min(max_delay, base_delay * (2 ** min(8, self._snapshot_compaction_failure_streak - 1)))
        self._snapshot_compaction_retry_not_before = max(
            float(self._snapshot_compaction_retry_not_before),
            float(now) + delay,
        )
        self._last_snapshot_compaction_error = f"{type(exc).__name__}: {exc}"[:1000]
        self._last_snapshot_compaction_failed_at = float(now)
        self._auto_backup_retry_reason = ""

    def _record_snapshot_compaction_success_locked(self) -> None:
        self._snapshot_compaction_failure_streak = 0
        self._snapshot_compaction_retry_not_before = 0.0

    def _replay_pressure_reason_locked(self) -> str | None:
        replay_entries = max(
            0,
            len(self._updates) - (1 if self._base_snapshot_present and self._updates else 0),
        )
        replay_bytes = self._replay_window_bytes_locked()
        pressure_by_entries = (
            self.auto_backup_replay_pressure_entries > 0
            and replay_entries >= int(self.auto_backup_replay_pressure_entries)
        )
        pressure_by_bytes = (
            self.auto_backup_replay_pressure_bytes > 0
            and replay_bytes >= int(self.auto_backup_replay_pressure_bytes)
        )
        if replay_entries > 0 and (pressure_by_entries or pressure_by_bytes):
            return "replay_pressure"
        return None

    def _replay_compaction_reason_locked(self) -> str | None:
        total = len(self._updates)
        if total <= 1:
            return None
        if total > self.max_updates:
            return "entry_limit"
        if self.max_replay_bytes > 0 and self._replay_window_bytes_locked() > self.max_replay_bytes:
            return "byte_limit"
        return None

    def _base_snapshot_bytes_locked(self, updates: List[Tuple[bytes, bytes, float]] | None = None) -> bytes:
        snapshot = list(updates if updates is not None else self._updates)
        if not snapshot or not self._base_snapshot_present:
            return b""
        return bytes(snapshot[0][0] or b"")

    def _compact_updates_locked(
        self,
        *,
        now: float,
        keep_tail: int | None = None,
        reason: str | None = None,
    ) -> None:
        updates = list(self._updates)
        if not updates:
            return
        total = len(updates)
        tail_count = self.replay_window if keep_tail is None else int(keep_tail)
        tail_count = max(0, min(tail_count, max(0, total - 1)))
        reason_token = str(reason or "").strip()
        if reason_token == "entry_limit" and int(self.compact_target_replay_entries or 0) >= 0:
            tail_count = min(tail_count, int(self.compact_target_replay_entries or 0))
        tail_byte_limit = int(self.max_replay_bytes) if self.max_replay_bytes > 0 else 0
        if reason_token == "byte_limit" and int(self.compact_target_replay_bytes or 0) > 0:
            tail_byte_limit = min(tail_byte_limit, int(self.compact_target_replay_bytes))
        keep_from = total
        kept_total = 0
        kept_bytes = 0
        while keep_from > 0 and kept_total < tail_count:
            candidate_index = keep_from - 1
            if candidate_index <= 0:
                break
            candidate_update = updates[candidate_index][0]
            candidate_size = len(candidate_update)
            if tail_byte_limit > 0 and kept_total > 0 and kept_bytes + candidate_size > tail_byte_limit:
                break
            keep_from = candidate_index
            kept_total += 1
            kept_bytes += candidate_size
        prefix_count = max(1, keep_from)
        prefix = updates[:prefix_count]
        tail = updates[prefix_count:]
        snapshot_state_vector: bytes | None = None
        if prefix_count == 1 and self._base_snapshot_present:
            snapshot = self._base_snapshot_bytes_locked(prefix)
            snapshot_state_vector = bytes(self._base_state_vector or b"") or None
        else:
            ydoc = Y.YDoc()
            for update, _meta, _ts in prefix:
                Y.apply_update(ydoc, update)  # type: ignore[arg-type]
            snapshot = Y.encode_state_as_update(ydoc)  # type: ignore[arg-type]
            snapshot_state_vector = Y.encode_state_vector(ydoc)  # type: ignore[arg-type]
        metadata = prefix[-1][1] if prefix else b""
        self._updates = [(snapshot, metadata, now), *tail]
        self._base_snapshot_present = True
        self._base_state_vector = snapshot_state_vector if not tail else None
        self._compact_total += 1
        self._last_compact_at = now
        self._last_compact_reason = str(reason or "manual").strip() or "manual"
        self._last_snapshot_bytes = len(snapshot)

    async def _load_from_disk_if_needed(self) -> None:
        if self._loaded_from_disk:
            return
        path = ystore_path_for_webspace(self.path)
        if not path.exists():
            self._loaded_from_disk = True
            return

        preflight_ok, preflight_reason = _preflight_snapshot_file(path)
        if not preflight_ok:
            quarantined = _quarantine_corrupt_snapshot(path, preflight_reason)
            with self._lock:
                self._loaded_from_disk = True
                self._last_disk_load_mode = "corrupt_snapshot_quarantined"
                self._last_disk_snapshot_bytes = 0
                self._persisted_snapshot_bytes = 0
                self._persisted_generation = 0
            _log.warning(
                "YStore corrupt snapshot skipped webspace=%s path=%s quarantine=%s reason=%s",
                self.path,
                path,
                quarantined,
                preflight_reason,
            )
            return

        try:
            data = path.read_bytes()
        except Exception as exc:  # pragma: no cover - IO errors are logged only
            _log.warning("failed to read YStore snapshot %s: %s", path, exc, exc_info=True)
            self._loaded_from_disk = True
            return

        try:
            state_vector = await anyio.to_thread.run_sync(_decode_state_vector_from_snapshot, data)
        except Exception:
            state_vector = b""

        metadata = await self.get_metadata()
        now = time.time()
        with self._lock:
            if not self._updates:
                self._updates.append((data, metadata, now))
                self._base_snapshot_present = True
                self._base_state_vector = bytes(state_vector or b"") or None
                if self._base_state_vector is not None:
                    self._state_vector_compute_total += 1
                self._last_loaded_from_disk_at = now
                self._last_snapshot_bytes = len(data)
                self._persisted_generation = int(self._generation)
                self._persisted_snapshot_bytes = len(data)
                self._last_disk_load_mode = "base_snapshot"
                self._last_disk_snapshot_bytes = len(data)
            elif not self._base_snapshot_present:
                # A hot-path writer may append runtime diffs before the first
                # reader opens the store. Those diffs are intended to layer on
                # top of the persisted document, not replace it. If we mark the
                # store as loaded while non-empty updates exist, the disk
                # snapshot is silently ignored and the live YRoom starts from a
                # partial document (for example, only early projection writes).
                #
                # Prepend the persisted snapshot as the base update and keep the
                # already captured runtime diffs as replay tail. Yjs updates are
                # CRDT updates, so this preserves the early writes while
                # restoring the durable baseline.
                self._updates.insert(0, (data, metadata, now))
                self._base_snapshot_present = True
                self._base_state_vector = None
                self._last_loaded_from_disk_at = now
                self._last_snapshot_bytes = len(data)
                self._persisted_generation = int(self._generation)
                self._persisted_snapshot_bytes = len(data)
                self._disk_snapshot_prepend_total += 1
                self._last_disk_load_mode = "prepended_before_runtime_updates"
                self._last_disk_snapshot_bytes = len(data)
                _log.warning(
                    "YStore disk snapshot prepended before existing runtime updates webspace=%s updates=%d bytes=%d",
                    self.path,
                    max(0, len(self._updates) - 1),
                    len(data),
                )
            else:
                self._disk_snapshot_skip_nonempty_total += 1
                self._last_disk_load_mode = "skipped_existing_base_snapshot"
                self._last_disk_snapshot_bytes = len(data)
        self._loaded_from_disk = True

    def _schedule_auto_backup(self, *, reason: str, debounce_sec: float | None = None) -> bool:
        async def _runner() -> None:
            failed = False
            try:
                delay = self.auto_backup_debounce_sec if debounce_sec is None else max(0.0, float(debounce_sec))
                if delay > 0:
                    await asyncio.sleep(delay)
                await self.backup_to_disk(compact_runtime=True, backup_kind=f"auto_after_compact:{reason}")
            except Exception as exc:
                failed = True
                _log.warning(
                    "auto YStore backup failed for webspace=%s reason=%s: %s",
                    self.path,
                    reason,
                    exc,
                    exc_info=True,
                )
            finally:
                retry_reason = ""
                with self._lock:
                    self._auto_backup_inflight = False
                    retry_reason = "" if failed else self._auto_backup_retry_reason
                    self._auto_backup_retry_reason = ""
                    if retry_reason:
                        self._auto_backup_inflight = True
                if retry_reason:
                    scheduled = self._schedule_auto_backup(
                        reason=retry_reason,
                        debounce_sec=float(self.auto_backup_replay_pressure_debounce_sec),
                    )
                    if not scheduled:
                        with self._lock:
                            self._auto_backup_inflight = False

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        loop.create_task(_runner())
        return True

    async def request_runtime_compaction(self, *, reason: str = "manual", min_quiet_sec: float = 0.0) -> bool:
        token = str(reason or "").strip().lower().replace(" ", "_") or "manual"
        now = time.time()
        with self._lock:
            has_replay_tail = len(self._updates) > 1 or self._replay_window_bytes_locked() > 0
            if not has_replay_tail or self._auto_backup_inflight:
                return False
            quiet_sec = max(0.0, float(min_quiet_sec or 0.0))
            if quiet_sec > 0.0 and self._last_write_at > 0.0 and now - self._last_write_at < quiet_sec:
                return False
            self._auto_backup_inflight = True
        if self._schedule_auto_backup(reason=f"idle_{token}"):
            return True
        with self._lock:
            self._auto_backup_inflight = False
        return False

    async def read(self) -> AsyncIterator[tuple[bytes, bytes]]:  # type: ignore[override]
        """
        Async iterator over stored updates (update, metadata).
        """
        await self._load_from_disk_if_needed()
        with self._lock:
            if not self._updates:
                raise YDocNotFound
            snapshot = list(self._updates)

        for update, metadata, _ts in snapshot:
            yield update, metadata

    async def backup_to_disk(
        self,
        *,
        compact_runtime: bool = True,
        backup_kind: str = "manual",
    ) -> None:
        """
        Persist the current YDoc state as a single update snapshot.
        """
        backup_kind_token = str(backup_kind or "").strip() or "manual"
        with self._lock:
            updates = list(self._updates)
            generation = int(self._generation)
            metadata = updates[-1][1] if updates else b""
            cached_state_vector = bytes(self._base_state_vector or b"") or None
        path = ystore_path_for_webspace(self.path)
        snapshot_exists = path.exists()
        snapshot = b""
        snapshot_state_vector: bytes | None = None
        backup_mode = "empty"
        used_fast_path = False
        written_bytes = 0
        try:
            if updates:
                if len(updates) == 1 and bool(self._base_snapshot_present):
                    snapshot = self._base_snapshot_bytes_locked(updates)
                    snapshot_state_vector = cached_state_vector
                    backup_mode = "runtime_base_snapshot"
                    used_fast_path = True
                    if snapshot and snapshot_state_vector is None:
                        snapshot_state_vector = await anyio.to_thread.run_sync(_decode_state_vector_from_snapshot, snapshot)
                else:
                    backup_mode = "encoded_runtime_log"
                    snapshot, snapshot_state_vector = await anyio.to_thread.run_sync(_encode_snapshot_artifacts, updates)

            skip_write = bool(not snapshot and not snapshot_exists)
            skip_reason = "empty_no_snapshot" if skip_write else ""
            if not skip_write:
                with self._lock:
                    persisted_generation = int(self._persisted_generation)
                    persisted_snapshot_bytes = int(self._persisted_snapshot_bytes)
                up_to_date = bool(
                    snapshot
                    and snapshot_exists
                    and generation >= 0
                    and generation == persisted_generation
                    and len(snapshot) == persisted_snapshot_bytes
                )
                if up_to_date:
                    skip_write = True
                    skip_reason = "persisted_generation_current"
                else:
                    written_bytes = await anyio.to_thread.run_sync(_persist_snapshot, path, snapshot)
        except BaseException as exc:
            if _is_fatal_base_exception(exc):
                raise
            now = time.time()
            error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._backup_total += 1
                self._backup_failed_total += 1
                self._backup_by_kind[backup_kind_token] = int(self._backup_by_kind.get(backup_kind_token) or 0) + 1
                self._backup_failed_by_kind[backup_kind_token] = (
                    int(self._backup_failed_by_kind.get(backup_kind_token) or 0) + 1
                )
                self._last_backup_kind = backup_kind_token
                self._last_backup_mode = f"{backup_mode}:failed"
                self._last_backup_skip_reason = "error"
                self._last_backup_written_bytes = 0
                self._last_backup_error = error[:1000]
                self._last_backup_failed_at = now
                self._last_backup_at = now
                self._record_snapshot_compaction_failure_locked(exc, now=now)
                if backup_kind_token.startswith("auto_after_compact:"):
                    self._last_auto_backup_reason = str(backup_kind_token.partition(":")[2] or "").strip()
            _log.warning(
                "YStore backup failed webspace=%s kind=%s mode=%s generation=%d updates=%d error=%s",
                self.path,
                backup_kind_token,
                backup_mode,
                generation,
                len(updates),
                error,
                exc_info=True,
            )
            raise RuntimeError(f"yjs_backup_failed:{backup_kind_token}:{error}") from None
        now = time.time()
        with self._lock:
            self._record_snapshot_compaction_success_locked()
            self._backup_total += 1
            self._backup_by_kind[backup_kind_token] = int(self._backup_by_kind.get(backup_kind_token) or 0) + 1
            if used_fast_path:
                self._backup_fast_path_total += 1
            if skip_write:
                self._backup_skipped_total += 1
                self._backup_skipped_by_kind[backup_kind_token] = (
                    int(self._backup_skipped_by_kind.get(backup_kind_token) or 0) + 1
                )
            elif written_bytes:
                self._backup_written_by_kind[backup_kind_token] = (
                    int(self._backup_written_by_kind.get(backup_kind_token) or 0) + 1
                )
            self._last_backup_kind = backup_kind_token
            self._last_backup_mode = f"{backup_mode}:skipped" if skip_write else backup_mode
            self._last_backup_skip_reason = skip_reason
            self._last_backup_written_bytes = int(written_bytes)
            self._last_backup_error = ""
            self._last_backup_at = now
            if written_bytes:
                self._last_snapshot_bytes = int(written_bytes)
            if backup_kind_token.startswith("auto_after_compact:"):
                self._auto_backup_total += 1
                self._last_auto_backup_at = now
                self._last_auto_backup_reason = str(backup_kind_token.partition(":")[2] or "").strip()
            compacted_runtime = False
            compacted_tail_entries = 0
            if (
                compact_runtime
                and (written_bytes or skip_write)
                and snapshot
            ):
                current_updates = list(self._updates)
                appended_tail: list[tuple[bytes, bytes, float]] = []
                prefix_matches = self._generation == generation
                if not prefix_matches and len(current_updates) >= len(updates):
                    prefix_matches = all(
                        bytes(current_updates[idx][0] or b"") == bytes(update[0] or b"")
                        and bytes(current_updates[idx][1] or b"") == bytes(update[1] or b"")
                        for idx, update in enumerate(updates)
                    )
                    if prefix_matches:
                        appended_tail = list(current_updates[len(updates) :])
                already_compacted = bool(
                    not appended_tail
                    and len(current_updates) == 1
                    and self._base_snapshot_present
                    and bytes(current_updates[0][0] or b"") == bytes(snapshot)
                )
                if prefix_matches and not already_compacted:
                    self._updates = [(bytes(snapshot), metadata, now), *appended_tail]
                    self._base_snapshot_present = True
                    self._base_state_vector = (bytes(snapshot_state_vector or b"") or None) if not appended_tail else None
                    self._compact_total += 1
                    self._last_compact_at = now
                    self._last_compact_reason = "backup_compaction" if not appended_tail else "backup_prefix_compaction"
                    self._generation += 1
                    compacted_runtime = True
                    compacted_tail_entries = len(appended_tail)
            if compacted_runtime:
                self._persisted_generation = generation if compacted_tail_entries else int(self._generation)
                self._persisted_snapshot_bytes = len(snapshot)
            elif skip_write:
                self._persisted_generation = generation
                self._persisted_snapshot_bytes = len(snapshot)
            elif written_bytes and self._generation == generation:
                self._persisted_generation = generation
                self._persisted_snapshot_bytes = int(written_bytes)
            if (
                snapshot
                and snapshot_state_vector
                and self._generation == generation
                and len(self._updates) == 1
                and self._base_snapshot_present
                and bytes(self._updates[0][0] or b"") == bytes(snapshot)
            ):
                self._base_state_vector = bytes(snapshot_state_vector)
            if backup_kind_token.startswith("auto_after_compact:") and not compacted_runtime and self._last_auto_backup_reason:
                # Keep the last auto-backup reason observable even when concurrent
                # writes made runtime-side collapse unsafe for this round.
                self._last_auto_backup_reason = self._last_auto_backup_reason
            if backup_kind_token.startswith("auto_after_compact:"):
                retry_reason = self._replay_pressure_reason_locked()
                if retry_reason:
                    self._auto_backup_retry_reason = retry_reason

    def runtime_snapshot(self, *, now_ts: float | None = None) -> dict[str, Any]:
        now = time.time() if now_ts is None else float(now_ts)
        snapshot_path = ystore_path_for_webspace(self.path)
        snapshot_exists = snapshot_path.exists()
        try:
            snapshot_size = snapshot_path.stat().st_size if snapshot_exists else 0
        except Exception:
            snapshot_size = 0
        updates = list(self._updates)
        update_log_entries = len(updates)
        update_log_bytes = sum(len(update) for update, _meta, _ts in updates)
        base_snapshot_present = bool(updates) and bool(self._base_snapshot_present)
        replay_window_entries = max(0, update_log_entries - (1 if base_snapshot_present else 0))
        replay_window_bytes = self._replay_window_bytes_locked(updates)
        runtime_compaction_eligible = bool(update_log_entries > 1 or replay_window_bytes > 0)
        persisted_up_to_date = bool(
            (update_log_entries <= 0 and not snapshot_exists)
            or (snapshot_exists and int(self._persisted_generation) == int(self._generation))
        )
        if update_log_entries <= 0:
            log_mode = "empty"
        elif base_snapshot_present:
            log_mode = "snapshot_plus_diff"
        else:
            log_mode = "append_only"

        def _top_counts(data: dict[str, int]) -> dict[str, int]:
            return {
                str(key): int(value)
                for key, value in sorted(
                    data.items(),
                    key=lambda item: (-int(item[1] or 0), str(item[0])),
                )[:12]
                if int(value or 0) > 0
            }

        return {
            "webspace_id": self.path,
            "log_mode": log_mode,
            "update_log_entries": update_log_entries,
            "update_log_bytes": int(update_log_bytes),
            "base_snapshot_present": bool(base_snapshot_present),
            "replay_window_entries": replay_window_entries,
            "replay_window_limit": int(self.replay_window),
            "compact_target_replay_entries": int(self.compact_target_replay_entries),
            "replay_window_bytes": int(replay_window_bytes),
            "replay_window_byte_limit": int(self.max_replay_bytes),
            "runtime_compaction_eligible": runtime_compaction_eligible,
            "max_update_log_entries": int(self.max_updates),
            "loaded_from_disk": bool(self._loaded_from_disk),
            "running": bool(self._running),
            "write_total": int(self._write_total),
            "compact_total": int(self._compact_total),
            "backup_total": int(self._backup_total),
            "backup_fast_path_total": int(self._backup_fast_path_total),
            "backup_skipped_total": int(self._backup_skipped_total),
            "backup_failed_total": int(self._backup_failed_total),
            "backup_by_kind": _top_counts(dict(self._backup_by_kind)),
            "backup_written_by_kind": _top_counts(dict(self._backup_written_by_kind)),
            "backup_skipped_by_kind": _top_counts(dict(self._backup_skipped_by_kind)),
            "backup_failed_by_kind": _top_counts(dict(self._backup_failed_by_kind)),
            "auto_backup_total": int(self._auto_backup_total),
            "diff_write_total": int(self._diff_write_total),
            "snapshot_write_total": int(self._snapshot_write_total),
            "write_skipped_total": int(self._write_skipped_total),
            "apply_total": int(self._apply_total),
            "applied_update_total": int(self._applied_update_total),
            "applied_update_bytes": int(self._applied_update_bytes),
            "apply_yield_total": int(self._apply_yield_total),
            "auto_backup_after_compact": bool(self.auto_backup_after_compact),
            "auto_backup_cooldown_sec": float(self.auto_backup_cooldown_sec),
            "auto_backup_debounce_sec": float(self.auto_backup_debounce_sec),
            "auto_backup_large_update_bytes": int(self.auto_backup_large_update_bytes),
            "auto_backup_large_update_debounce_sec": float(self.auto_backup_large_update_debounce_sec),
            "auto_backup_replay_pressure_bytes": int(self.auto_backup_replay_pressure_bytes),
            "auto_backup_replay_pressure_entries": int(self.auto_backup_replay_pressure_entries),
            "auto_backup_replay_pressure_debounce_sec": float(self.auto_backup_replay_pressure_debounce_sec),
            "auto_backup_inflight": bool(self._auto_backup_inflight),
            "auto_backup_retry_pending": bool(self._auto_backup_retry_reason),
            "auto_backup_retry_reason": self._auto_backup_retry_reason or None,
            "snapshot_compaction_failure_backoff_sec": float(self.snapshot_compaction_failure_backoff_sec),
            "snapshot_compaction_failure_backoff_max_sec": float(self.snapshot_compaction_failure_backoff_max_sec),
            "snapshot_compaction_failure_streak": int(self._snapshot_compaction_failure_streak),
            "snapshot_compaction_retry_not_before": self._snapshot_compaction_retry_not_before or None,
            "snapshot_compaction_backoff_remaining_s": round(
                self._snapshot_compaction_backoff_remaining_locked(now),
                3,
            ),
            "snapshot_compaction_suppressed_total": int(self._snapshot_compaction_suppressed_total),
            "snapshot_file_exists": bool(snapshot_exists),
            "snapshot_file_size": int(snapshot_size),
            "persisted_generation": int(self._persisted_generation) if self._persisted_generation >= 0 else None,
            "persisted_snapshot_bytes": int(self._persisted_snapshot_bytes),
            "persisted_up_to_date": persisted_up_to_date,
            "cached_state_vector_bytes": len(self._base_state_vector or b""),
            "state_vector_fast_path_total": int(self._state_vector_fast_path_total),
            "state_vector_compute_total": int(self._state_vector_compute_total),
            "state_vector_cache_miss_total": int(self._state_vector_cache_miss_total),
            "disk_snapshot_prepend_total": int(self._disk_snapshot_prepend_total),
            "disk_snapshot_skip_nonempty_total": int(self._disk_snapshot_skip_nonempty_total),
            "last_disk_load_mode": self._last_disk_load_mode or None,
            "last_disk_snapshot_bytes": int(self._last_disk_snapshot_bytes),
            "last_update_bytes": int(self._last_update_bytes),
            "last_snapshot_bytes": int(self._last_snapshot_bytes),
            "last_backup_kind": self._last_backup_kind or None,
            "last_backup_mode": self._last_backup_mode or None,
            "last_backup_skip_reason": self._last_backup_skip_reason or None,
            "last_backup_written_bytes": int(self._last_backup_written_bytes),
            "last_backup_error": self._last_backup_error or None,
            "last_backup_failed_at": self._last_backup_failed_at or None,
            "last_backup_failed_ago_s": round(max(0.0, now - self._last_backup_failed_at), 3)
            if self._last_backup_failed_at
            else None,
            "last_snapshot_compaction_error": self._last_snapshot_compaction_error or None,
            "last_snapshot_compaction_failed_at": self._last_snapshot_compaction_failed_at or None,
            "last_snapshot_compaction_failed_ago_s": round(
                max(0.0, now - self._last_snapshot_compaction_failed_at),
                3,
            )
            if self._last_snapshot_compaction_failed_at
            else None,
            "last_apply_update_total": int(self._last_apply_update_total),
            "last_apply_bytes": int(self._last_apply_bytes),
            "last_apply_yield_total": int(self._last_apply_yield_total),
            "last_apply_slow_update_ms": float(self._last_apply_slow_update_ms),
            "last_apply_slow_update_bytes": int(self._last_apply_slow_update_bytes),
            "last_apply_mode": self._last_apply_mode or None,
            "last_write_at": self._last_write_at or None,
            "last_write_ago_s": round(max(0.0, now - self._last_write_at), 3) if self._last_write_at else None,
            "last_compact_at": self._last_compact_at or None,
            "last_compact_reason": self._last_compact_reason or None,
            "last_compact_ago_s": round(max(0.0, now - self._last_compact_at), 3) if self._last_compact_at else None,
            "last_backup_at": self._last_backup_at or None,
            "last_backup_ago_s": round(max(0.0, now - self._last_backup_at), 3) if self._last_backup_at else None,
            "last_auto_backup_at": self._last_auto_backup_at or None,
            "last_auto_backup_reason": self._last_auto_backup_reason or None,
            "last_auto_backup_ago_s": round(max(0.0, now - self._last_auto_backup_at), 3)
            if self._last_auto_backup_at
            else None,
            "last_apply_at": self._last_apply_at or None,
            "last_apply_ago_s": round(max(0.0, now - self._last_apply_at), 3) if self._last_apply_at else None,
            "last_loaded_from_disk_at": self._last_loaded_from_disk_at or None,
            "last_loaded_from_disk_ago_s": round(max(0.0, now - self._last_loaded_from_disk_at), 3)
            if self._last_loaded_from_disk_at
            else None,
        }


_YSTORE_CACHE: Dict[str, AdaosMemoryYStore] = {}


def get_ystore_for_webspace(webspace_id: str) -> AdaosMemoryYStore:
    """
        Return a cached in-memory YStore for the given webspace.

        All callers (web_desktop_skill, async_get_ydoc, y_gateway) share the same
        instance to avoid \"YStore already running\" races.
    """
    store = _YSTORE_CACHE.get(webspace_id)
    if store is None:
        store = AdaosMemoryYStore(webspace_id)
        _YSTORE_CACHE[webspace_id] = store
    return store


def ystore_runtime_snapshot(*, webspace_id: str | None = None, now_ts: float | None = None) -> dict[str, Any]:
    now = time.time() if now_ts is None else float(now_ts)
    if webspace_id:
        store = get_ystore_for_webspace(str(webspace_id))
        return {
            "webspace_id": str(webspace_id),
            "webspace_total": 1,
            "webspaces": {
                str(webspace_id): store.runtime_snapshot(now_ts=now),
            },
        }

    webspaces: dict[str, Any] = {}
    active_total = 0
    for ws_id, store in sorted(_YSTORE_CACHE.items()):
        item = store.runtime_snapshot(now_ts=now)
        webspaces[str(ws_id)] = item
        if int(item.get("update_log_entries") or 0) > 0 or bool(item.get("snapshot_file_exists")):
            active_total += 1
    return {
        "webspace_total": len(webspaces),
        "active_webspace_total": active_total,
        "webspaces": webspaces,
    }


def reset_ystore_for_webspace(webspace_id: str) -> None:
    """
    Drop any in-memory Y updates for the given webspace so that future access
    starts from a clean YDoc. Used when corrupted updates cause Y.apply_update
    panics for a webspace that is being deleted or re-seeded.
    """
    store = _YSTORE_CACHE.pop(webspace_id, None)
    if store is not None:
        try:
            store.stop()
        except Exception:
            pass
        try:
            store._clear_runtime_state_locked()  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        path = ystore_path_for_webspace(webspace_id)
        if path.exists():
            path.unlink()
    except Exception:
        _log.warning("failed to remove YStore snapshot for webspace=%s", webspace_id, exc_info=True)


async def restore_ystore_for_webspace(webspace_id: str) -> dict[str, Any]:
    """
    Recreate the in-memory YStore for a webspace from its last persisted
    snapshot, without reseeding from scenario sources.
    """
    key = str(webspace_id or "").strip() or "default"
    path = ystore_path_for_webspace(key)
    snapshot_exists = path.exists()
    if not snapshot_exists:
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": key,
            "error": "snapshot_missing",
            "snapshot_path": str(path),
        }

    store = _YSTORE_CACHE.pop(key, None)
    if store is not None:
        try:
            store.stop()
        except Exception:
            pass
        try:
            store._clear_runtime_state_locked()  # type: ignore[attr-defined]
        except Exception:
            pass

    restored = AdaosMemoryYStore(key)
    _YSTORE_CACHE[key] = restored
    try:
        await restored._load_from_disk_if_needed()  # type: ignore[attr-defined]
    except Exception as exc:
        _log.warning("failed to restore YStore snapshot for webspace=%s: %s", key, exc, exc_info=True)
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": key,
            "error": f"restore_failed:{type(exc).__name__}",
            "snapshot_path": str(path),
        }

    return {
        "ok": True,
        "accepted": True,
        "webspace_id": key,
        "snapshot_path": str(path),
        "runtime": restored.runtime_snapshot(),
    }


async def evict_ystore_for_webspace(
    webspace_id: str,
    *,
    store: AdaosMemoryYStore | None = None,
    persist_snapshot: bool = True,
    compact_runtime: bool = True,
    backup_kind: str = "evict",
    delete_snapshot: bool = False,
) -> dict[str, Any]:
    key = str(webspace_id or "").strip() or "default"
    cached = _YSTORE_CACHE.pop(key, None)
    extra_target = cached if cached is not None and cached is not store else None
    target = store or cached
    if target is None:
        removed_snapshot = False
        if delete_snapshot:
            try:
                path = ystore_path_for_webspace(key)
                if path.exists():
                    path.unlink()
                    removed_snapshot = True
            except Exception:
                _log.warning("failed to remove YStore snapshot for webspace=%s", key, exc_info=True)
        return {
            "ok": True,
            "webspace_id": key,
            "ystore_found": False,
            "persisted": False,
            "snapshot_deleted": removed_snapshot,
            "released_update_entries": 0,
            "released_update_bytes": 0,
        }

    persisted = False
    backup_skipped = False
    backup_error: str | None = None
    if persist_snapshot:
        try:
            await target.backup_to_disk(
                compact_runtime=compact_runtime,
                backup_kind=backup_kind,
            )
            snapshot = target.runtime_snapshot()
            persisted = bool(snapshot.get("snapshot_file_exists"))
            backup_skipped = bool(snapshot.get("persisted_up_to_date"))
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            backup_error = f"{type(exc).__name__}: {exc}"
            _log.warning("failed to persist YStore before eviction webspace=%s", key, exc_info=True)

    try:
        result = target.stop()
        if inspect.isawaitable(result):
            await result
    except Exception:
        _log.debug("failed to stop YStore before eviction webspace=%s", key, exc_info=True)

    released = {"released_update_entries": 0, "released_update_bytes": 0}
    try:
        released = await target.evict_runtime_state()
    except Exception:
        _log.warning("failed to clear YStore runtime state webspace=%s", key, exc_info=True)

    if extra_target is not None:
        try:
            result = extra_target.stop()
            if inspect.isawaitable(result):
                await result
        except Exception:
            _log.debug("failed to stop cached YStore during eviction webspace=%s", key, exc_info=True)
        try:
            await extra_target.evict_runtime_state()
        except Exception:
            _log.warning("failed to clear cached YStore runtime state webspace=%s", key, exc_info=True)

    removed_snapshot = False
    if delete_snapshot:
        try:
            path = ystore_path_for_webspace(key)
            if path.exists():
                path.unlink()
                removed_snapshot = True
        except Exception:
            _log.warning("failed to remove YStore snapshot for webspace=%s", key, exc_info=True)

    return {
        "ok": backup_error is None,
        "webspace_id": key,
        "ystore_found": True,
        "persisted": persisted,
        "backup_skipped": backup_skipped,
        "backup_error": backup_error,
        "snapshot_deleted": removed_snapshot,
        **released,
    }


@subscribe("sys.ystore.backup")
async def _on_ystore_backup(payload: dict) -> None:
    """
    System handler: persist in-memory YStore snapshot for a webspace.

    This is triggered by the scheduler via `sys.ystore.backup` events.
    """
    if not isinstance(payload, dict):
        return
    webspace_id = str(payload.get("webspace_id") or payload.get("workspace_id") or "default")
    try:
        store = get_ystore_for_webspace(webspace_id)
        await store.backup_to_disk()
    except Exception as exc:  # pragma: no cover - defensive logging
        _log.warning("YStore backup failed for webspace=%s: %s", webspace_id, exc, exc_info=True)
