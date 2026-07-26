from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping


class MutationLockTimeout(TimeoutError):
    pass


_MUTATION_LOCKS_GUARD = threading.Lock()
_MUTATION_LOCKS: dict[str, threading.RLock] = {}
_MOVEFILE_REPLACE_EXISTING = 0x1
_MOVEFILE_WRITE_THROUGH = 0x8


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _MUTATION_LOCKS_GUARD:
        lock = _MUTATION_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _MUTATION_LOCKS[key] = lock
        return lock


@contextmanager
def mutation_lock(path: Path, *, timeout_s: float = 10.0) -> Iterator[None]:
    """Serialize one bounded local mutation across threads and processes."""

    lock_path = Path(path).expanduser().resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock_for(lock_path)
    with thread_lock:
        started = time.monotonic()
        with lock_path.open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if time.monotonic() - started >= timeout_s:
                            raise MutationLockTimeout(
                                f"timed out waiting for mutation lock {lock_path}"
                            ) from exc
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    with contextlib.suppress(OSError):
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                return

            try:
                import fcntl
            except ImportError:  # pragma: no cover - supported targets provide fcntl
                yield
                return
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() - started >= timeout_s:
                        raise MutationLockTimeout(
                            f"timed out waiting for mutation lock {lock_path}"
                        ) from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def sync_directory(path: Path) -> bool:
    """Best-effort persistence barrier for directory metadata.

    POSIX exposes directory fsync directly. Windows directory handles are not
    portable through ``os.open``; durable renames use MoveFileExW with
    MOVEFILE_WRITE_THROUGH instead.
    """

    if os.name == "nt":
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(Path(path), flags)
    except OSError:
        return False
    try:
        os.fsync(descriptor)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return True


def _replace_once(source: Path, target: Path) -> None:
    if os.name != "nt":
        os.replace(source, target)
        return

    import ctypes
    from ctypes import wintypes

    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file.restype = wintypes.BOOL
    if not move_file(
        str(source),
        str(target),
        _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH,
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def replace_with_retry(source: Path, target: Path, *, attempts: int = 8) -> None:
    """Retry only the filesystem switch, never the enclosing stateful operation."""

    source = Path(source)
    target = Path(target)
    source_parent = source.parent.resolve()
    target_parent = target.parent.resolve()
    for attempt in range(attempts):
        try:
            _replace_once(source, target)
            sync_directory(target_parent)
            if source_parent != target_parent:
                sync_directory(source_parent)
            return
        except OSError as exc:
            retryable = isinstance(exc, PermissionError) or getattr(
                exc, "winerror", None
            ) in {5, 32, 33}
            if not retryable or attempt + 1 >= attempts:
                raise
            time.sleep(min(0.01 * (2**attempt), 0.25))


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "MutationLockTimeout",
    "atomic_write_bytes",
    "atomic_write_json",
    "mutation_lock",
    "replace_with_retry",
    "sync_directory",
]
