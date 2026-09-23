from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from adaos.services.mutation_lock import MutationLockTimeout, mutation_lock


_MOVEFILE_REPLACE_EXISTING = 0x1
_MOVEFILE_WRITE_THROUGH = 0x8


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


def read_atomic_json(path: Path, *, attempts: int = 8) -> Any:
    """Read a replaced document, retrying only transient Windows sharing failures.

    Retry the file open/read, not a task, policy decision or state mutation.
    Corrupt JSON and persistent access failures remain errors, never empty state.
    """
    for attempt in range(max(1, attempts)):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            sharing_failure = os.name == "nt" and (
                isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33}
            )
            if not sharing_failure or attempt + 1 >= attempts:
                raise
            time.sleep(min(0.01 * (2**attempt), 0.25))


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
    "read_atomic_json",
    "sync_directory",
]
