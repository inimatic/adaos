"""Persistent skill-local JSON store backed by the runtime skill env file."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.core.errors import SdkRuntimeNotInitialized

__all__ = [
    "get_env",
    "set_env",
    "delete_env",
    "read_env",
    "write_env",
    "skill_env_path",
    "skill_data_root",
    "async_get_env",
    "async_set_env",
    "async_delete_env",
    "async_read_env",
    "async_write_env",
    "skill_env_io_guard_snapshot",
    "reset_skill_env_io_guard_runtime",
]


_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_IO_GUARD_LOCK = threading.Lock()
_IO_GUARD_RUNTIME: dict[str, Any] = {
    "schema": "adaos.skill_env_io_guard.v1",
    "rejected_total": 0,
    "rejected_by_operation": {},
    "read_total": 0,
    "write_total": 0,
    "write_bytes_total": 0,
    "write_skipped_total": 0,
    "legacy_merge_total": 0,
    "last_rejected_at": None,
    "last_operation": None,
    "last_skill": None,
    "last_thread_id": None,
    "last_write_at": None,
    "last_write_bytes": 0,
}


def _current_skill_name() -> str | None:
    _ctx, current = _current_ctx_and_skill()
    token = str(getattr(current, "name", "") or "").strip() if current is not None else ""
    return token or None


def _reject_blocking_io_on_event_loop(operation: str, *, async_alternative: str) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return

    now = time.time()
    skill = _current_skill_name()
    with _IO_GUARD_LOCK:
        by_operation = dict(_IO_GUARD_RUNTIME.get("rejected_by_operation") or {})
        by_operation[operation] = int(by_operation.get(operation) or 0) + 1
        _IO_GUARD_RUNTIME.update(
            {
                "rejected_total": int(_IO_GUARD_RUNTIME.get("rejected_total") or 0) + 1,
                "rejected_by_operation": by_operation,
                "last_rejected_at": now,
                "last_operation": operation,
                "last_skill": skill,
                "last_thread_id": threading.get_ident(),
            }
        )
    raise RuntimeError(
        f"{operation} performs blocking skill-env file I/O and cannot run on an asyncio event loop; "
        f"use {async_alternative} instead"
    )


def skill_env_io_guard_snapshot() -> dict[str, Any]:
    with _IO_GUARD_LOCK:
        snapshot = dict(_IO_GUARD_RUNTIME)
        snapshot["rejected_by_operation"] = dict(_IO_GUARD_RUNTIME.get("rejected_by_operation") or {})
    return snapshot


def _record_skill_env_io(
    operation: str,
    *,
    write_bytes: int = 0,
    skipped: bool = False,
    legacy_merge: bool = False,
) -> None:
    now = time.time()
    with _IO_GUARD_LOCK:
        if operation == "read":
            _IO_GUARD_RUNTIME["read_total"] = int(_IO_GUARD_RUNTIME.get("read_total") or 0) + 1
        if write_bytes > 0:
            _IO_GUARD_RUNTIME["write_total"] = int(_IO_GUARD_RUNTIME.get("write_total") or 0) + 1
            _IO_GUARD_RUNTIME["write_bytes_total"] = int(
                _IO_GUARD_RUNTIME.get("write_bytes_total") or 0
            ) + int(write_bytes)
            _IO_GUARD_RUNTIME["last_write_at"] = now
            _IO_GUARD_RUNTIME["last_write_bytes"] = int(write_bytes)
        if skipped:
            _IO_GUARD_RUNTIME["write_skipped_total"] = int(
                _IO_GUARD_RUNTIME.get("write_skipped_total") or 0
            ) + 1
        if legacy_merge:
            _IO_GUARD_RUNTIME["legacy_merge_total"] = int(
                _IO_GUARD_RUNTIME.get("legacy_merge_total") or 0
            ) + 1


def reset_skill_env_io_guard_runtime() -> None:
    with _IO_GUARD_LOCK:
        _IO_GUARD_RUNTIME.update(
            {
                "rejected_total": 0,
                "rejected_by_operation": {},
                "read_total": 0,
                "write_total": 0,
                "write_bytes_total": 0,
                "write_skipped_total": 0,
                "legacy_merge_total": 0,
                "last_rejected_at": None,
                "last_operation": None,
                "last_skill": None,
                "last_thread_id": None,
                "last_write_at": None,
                "last_write_bytes": 0,
            }
        )


def _path_lock_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except Exception:
        return str(path)


def _path_lock(path: Path) -> threading.RLock:
    key = _path_lock_key(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _current_skill_dir() -> Path:
    ctx = require_ctx("sdk.data.skill_env")
    current = ctx.skill_ctx.get()
    if current is None or getattr(current, "path", None) is None:
        raise SdkRuntimeNotInitialized("sdk.data.skill_env", "current skill is not set")
    return Path(current.path)


def _current_ctx_and_skill():
    try:
        ctx = require_ctx("sdk.data.skill_env")
    except Exception:
        return None, None
    current = ctx.skill_ctx.get()
    if current is None or getattr(current, "path", None) is None:
        return ctx, None
    return ctx, current


def _is_runtime_bucket_name(value: str) -> bool:
    token = str(value or "").strip()
    if not token.startswith("v"):
        return False
    major, sep, minor = token[1:].partition(".")
    return bool(sep and major.isdigit() and minor.isdigit())


def _runtime_env_path_from_skill_dir(skill_dir: Path) -> Path | None:
    resolved = skill_dir.expanduser().resolve()
    parts = resolved.parts
    try:
        idx = parts.index(".runtime")
    except ValueError:
        return None
    if len(parts) <= idx + 1:
        return None
    if len(parts) > idx + 2 and _is_runtime_bucket_name(str(parts[idx + 2])):
        runtime_root = Path(*parts[: idx + 3])
    else:
        runtime_root = Path(*parts[: idx + 2]) / "v0.0"
    return runtime_root / "data" / "db" / "skill_env.json"


def _runtime_bucket_name(version: str | None) -> str:
    parts = [0, 0]
    raw_parts = str(version or "0.0.0").strip().lstrip("vV").split(".")
    for idx, token in enumerate(raw_parts[:2]):
        digits = "".join(ch for ch in token if ch.isdigit())
        if digits:
            try:
                parts[idx] = int(digits)
            except ValueError:
                parts[idx] = 0
    return f"v{parts[0]}.{parts[1]}"


def _runtime_env_path_from_root(root: Path, skill_name: str) -> Path:
    runtime_root = root / ".runtime" / skill_name
    marker = runtime_root / "current_version"
    version = None
    if marker.exists():
        version = marker.read_text(encoding="utf-8").strip() or None
    return runtime_root / _runtime_bucket_name(version) / "data" / "db" / "skill_env.json"


def _runtime_env_path_from_ctx() -> Path | None:
    ctx, current = _current_ctx_and_skill()
    if ctx is None or current is None:
        return None

    current_dir = Path(current.path)
    direct = _runtime_env_path_from_skill_dir(current_dir)
    if direct is not None:
        return direct

    current_name = str(getattr(current, "name", "") or "").strip()
    if not current_name:
        return None

    def _resolve_root(attr_name: str) -> Path | None:
        attr = getattr(ctx.paths, attr_name, None)
        if attr is None:
            return None
        root = Path(attr() if callable(attr) else attr)
        return root

    def _is_under(path: Path, root: Path | None) -> bool:
        if root is None:
            return False
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except Exception:
            return False

    dev_root = _resolve_root("dev_skills_dir")
    if _is_under(current_dir, dev_root):
        return _runtime_env_path_from_root(dev_root, current_name)  # type: ignore[arg-type]

    workspace_root = _resolve_root("skills_dir")
    if _is_under(current_dir, workspace_root):
        return _runtime_env_path_from_root(workspace_root, current_name)  # type: ignore[arg-type]

    # Repo-workspace and other source fallbacks should still persist state into
    # the local runtime store, not back into the git-tracked source tree.
    if workspace_root is not None:
        return _runtime_env_path_from_root(workspace_root, current_name)
    if dev_root is not None:
        return _runtime_env_path_from_root(dev_root, current_name)
    return None


def skill_env_path() -> Path:
    _reject_blocking_io_on_event_loop("skill_env_path", async_alternative="an async skill-env operation")
    path = _runtime_env_path_from_ctx()
    if path is None:
        override = os.getenv("ADAOS_SKILL_ENV_PATH") or os.getenv("ADAOS_SKILL_MEMORY_PATH")
        if override:
            path = Path(override)
        else:
            current_dir = _current_skill_dir()
            path = _runtime_env_path_from_skill_dir(current_dir) or (current_dir / ".skill_env.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def skill_data_root() -> Path:
    """Return the current skill's owner-scoped mutable data directory.

    The runtime injects ``ADAOS_SKILL_INTERNAL_DATA_ROOT`` for isolated tool
    and Development-session execution.  Normal in-process calls resolve the
    same boundary from the current skill context through ``skill_env_path``.
    Skills must use this helper instead of reconstructing ``.runtime`` paths
    from ``ADAOS_BASE_DIR``: DEV slots, installed slots and compatibility
    buckets deliberately have different physical layouts.
    """

    explicit = str(os.getenv("ADAOS_SKILL_INTERNAL_DATA_ROOT") or "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root
    # The canonical env store is always ``<data-root>/db/skill_env.json``.
    return skill_env_path().parent.parent


def _legacy_paths(target: Path) -> list[Path]:
    candidates: list[Path] = []
    current_dir: Path | None = None
    try:
        current_dir = _current_skill_dir()
    except Exception:
        current_dir = None

    local_legacy = target.with_name(".skill_memory.json")
    if local_legacy != target:
        candidates.append(local_legacy)
    if target.parent.name == "db":
        for legacy in (
            target.parents[1] / ".skill_memory.json",
            target.parents[1] / ".skill_env.json",
            target.parent / ".skill_env.json",
            target.parents[1] / "files" / ".skill_env.json",
        ):
            if legacy != target and legacy not in candidates:
                candidates.append(legacy)
    if current_dir is not None:
        for legacy in (current_dir / ".skill_memory.json", current_dir / ".skill_env.json"):
            if legacy != target and legacy not in candidates:
                candidates.append(legacy)
    return candidates


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _write_json_object(path: Path, payload: Mapping[str, Any]) -> None:
    with _path_lock(path):
        _write_json_object_unlocked(path, payload)


def _write_json_object_unlocked(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    raw = json.dumps(dict(payload), ensure_ascii=False, indent=2)
    tmp.write_text(raw, encoding="utf-8")
    delay_s = 0.01
    try:
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                _record_skill_env_io("write", write_bytes=len(raw.encode("utf-8")))
                return
            except PermissionError:
                if attempt >= 7:
                    raise
                time.sleep(delay_s)
                delay_s = min(delay_s * 2.0, 0.25)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def read_env() -> dict[str, Any]:
    _reject_blocking_io_on_event_loop("read_env", async_alternative="async_read_env()")
    target = skill_env_path()
    with _path_lock(target):
        current = _read_json_object(target) if target.exists() else {}
        merged = dict(current)
        for legacy in _legacy_paths(target):
            if not legacy.exists() or not legacy.is_file():
                continue
            payload = _read_json_object(legacy)
            if not payload:
                continue
            merged = _deep_merge(payload, merged)
        if merged and merged != current:
            _write_json_object_unlocked(target, merged)
            _record_skill_env_io("merge", legacy_merge=True)
        _record_skill_env_io("read")
        return merged


def write_env(payload: Mapping[str, Any]) -> None:
    _reject_blocking_io_on_event_loop("write_env", async_alternative="async_write_env()")
    target = skill_env_path()
    with _path_lock(target):
        current = _read_json_object(target) if target.exists() else {}
        if current == dict(payload):
            _record_skill_env_io("write", skipped=True)
            return
        _write_json_object_unlocked(target, payload)


def get_env(key: str, default: Any | None = None) -> Any:
    return read_env().get(key, default)


def set_env(key: str, value: Any) -> None:
    _reject_blocking_io_on_event_loop("set_env", async_alternative="async_set_env()")
    target = skill_env_path()
    with _path_lock(target):
        payload = read_env()
        marker = object()
        if payload.get(key, marker) == value:
            _record_skill_env_io("set", skipped=True)
            return
        payload[key] = value
        _write_json_object_unlocked(target, payload)


def delete_env(key: str) -> None:
    _reject_blocking_io_on_event_loop("delete_env", async_alternative="async_delete_env()")
    target = skill_env_path()
    with _path_lock(target):
        payload = read_env()
        if key in payload:
            payload.pop(key, None)
            _write_json_object_unlocked(target, payload)


async def async_read_env() -> dict[str, Any]:
    return await asyncio.to_thread(read_env)


async def async_write_env(payload: Mapping[str, Any]) -> None:
    await asyncio.to_thread(write_env, payload)


async def async_get_env(key: str, default: Any | None = None) -> Any:
    return await asyncio.to_thread(get_env, key, default)


async def async_set_env(key: str, value: Any) -> None:
    await asyncio.to_thread(set_env, key, value)


async def async_delete_env(key: str) -> None:
    await asyncio.to_thread(delete_env, key)
