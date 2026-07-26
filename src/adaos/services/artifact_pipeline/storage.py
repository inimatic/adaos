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


def replace_with_retry(source: Path, target: Path, *, attempts: int = 8) -> None:
    """Retry only the filesystem switch, never the enclosing stateful operation."""

    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
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
]
