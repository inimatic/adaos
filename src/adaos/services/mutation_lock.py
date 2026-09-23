"""Dependency-neutral bounded local mutation lock shared by service layers."""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class MutationLockTimeout(TimeoutError):
    pass


_MUTATION_LOCKS_GUARD = threading.Lock()
_MUTATION_LOCKS: dict[str, threading.RLock] = {}
_MUTATION_LOCK_DEPTH = threading.local()


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
        depths = getattr(_MUTATION_LOCK_DEPTH, "depths", None)
        if depths is None:
            depths = {}
            _MUTATION_LOCK_DEPTH.depths = depths
        key = str(lock_path)
        if int(depths.get(key) or 0) > 0:
            depths[key] = int(depths[key]) + 1
            try:
                yield
            finally:
                remaining = int(depths.get(key) or 1) - 1
                if remaining > 0:
                    depths[key] = remaining
                else:
                    depths.pop(key, None)
            return

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
                    depths[key] = 1
                    yield
                finally:
                    depths.pop(key, None)
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
                depths[key] = 1
                yield
            finally:
                depths.pop(key, None)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = ["MutationLockTimeout", "mutation_lock"]
