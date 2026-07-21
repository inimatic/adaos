from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adaos.services.runtime_paths import current_state_dir


_TASKS: dict[tuple[int, str], asyncio.Task[dict[str, Any]]] = {}
_LOCK_REGISTRY_GUARD = threading.Lock()
_APPLY_LOCKS: dict[str, threading.Lock] = {}
_STATE_LOCKS: dict[str, threading.RLock] = {}


def _source_lock(registry: dict[str, Any], source_webspace_id: str, factory: Callable[[], Any]) -> Any:
    with _LOCK_REGISTRY_GUARD:
        lock = registry.get(source_webspace_id)
        if lock is None:
            lock = factory()
            registry[source_webspace_id] = lock
        return lock


def _safe_token(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return token or "default"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 5:
                temporary.unlink(missing_ok=True)
                raise
            # Windows scanners/readers can briefly deny replace on a file that
            # has just been observed. Preserve atomicity and retry the rename.
            time.sleep(0.005 * (attempt + 1))


@dataclass(slots=True)
class BuilderPreviewReconciler:
    state_dir: Path | None = None

    @property
    def root(self) -> Path:
        return Path(self.state_dir or current_state_dir()) / "builder" / "workbench" / "runtime"

    def state_path(self, source_webspace_id: str) -> Path:
        return self.root / f"{_safe_token(source_webspace_id)}.json"

    def describe(self, source_webspace_id: str) -> dict[str, Any]:
        source = str(source_webspace_id or "").strip()
        state_lock = _source_lock(_STATE_LOCKS, source, threading.RLock)
        with state_lock:
            current = _read_json(self.state_path(source))
        if current:
            return current
        return {
            "schema": "adaos.builder.preview_runtime.v1",
            "source_webspace_id": source,
            "preview_webspace_id": None,
            "selected_project": None,
            "desired_scenario": None,
            "observed_scenario": None,
            "generation": 0,
            "operation_id": None,
            "status": "idle",
            "requested_at": None,
            "started_at": None,
            "completed_at": None,
            "updated_at": None,
            "error": None,
        }

    def request(
        self,
        *,
        source_webspace_id: str,
        preview_webspace_id: str,
        project_kind: str,
        project_id: str,
        desired_scenario: str,
    ) -> tuple[dict[str, Any], bool]:
        source = str(source_webspace_id or "").strip()
        preview = str(preview_webspace_id or "").strip()
        desired = str(desired_scenario or "").strip()
        if not source or not preview or not desired:
            raise ValueError("source, preview and desired scenario are required")
        state_lock = _source_lock(_STATE_LOCKS, source, threading.RLock)
        with state_lock:
            current = self.describe(source)
            selected_project = {
                "kind": str(project_kind or "scenario").strip().lower() or "scenario",
                "id": str(project_id or desired).strip() or desired,
            }
            same_request = (
                str(current.get("preview_webspace_id") or "").strip() == preview
                and current.get("selected_project") == selected_project
                and str(current.get("desired_scenario") or "").strip() == desired
            )
            if same_request and str(current.get("status") or "") in {"requested", "running", "accepted", "ready"}:
                return current, True

            now = time.time()
            record = {
                **current,
                "schema": "adaos.builder.preview_runtime.v1",
                "source_webspace_id": source,
                "preview_webspace_id": preview,
                "selected_project": selected_project,
                "desired_scenario": desired,
                "generation": int(current.get("generation") or 0) + 1,
                "operation_id": f"preview-{secrets.token_hex(8)}",
                "status": "requested",
                "requested_at": now,
                "started_at": None,
                "completed_at": None,
                "updated_at": now,
                "error": None,
            }
            _write_json(self.state_path(source), record)
            return record, False

    def _update_if_current(
        self,
        source_webspace_id: str,
        generation: int,
        **changes: Any,
    ) -> tuple[dict[str, Any], bool]:
        state_lock = _source_lock(_STATE_LOCKS, source_webspace_id, threading.RLock)
        with state_lock:
            current = self.describe(source_webspace_id)
            if int(current.get("generation") or 0) != int(generation):
                return current, False
            current.update(changes)
            current["updated_at"] = time.time()
            _write_json(self.state_path(source_webspace_id), current)
            return current, True

    @staticmethod
    def _publish_observed(record: Mapping[str, Any]) -> None:
        try:
            from adaos.domain.project_events import BUILDER_PREVIEW_OBSERVED
            from adaos.sdk.data.events import publish

            publish(
                BUILDER_PREVIEW_OBSERVED,
                {
                    "source_webspace_id": record.get("source_webspace_id"),
                    "preview_webspace_id": record.get("preview_webspace_id"),
                    "scenario_id": record.get("observed_scenario"),
                    "generation": record.get("generation"),
                    "operation_id": record.get("operation_id"),
                    "status": record.get("status"),
                },
                source="builder.preview_reconciler",
            )
        except Exception:
            # Reconcile remains usable in offline tools without an event bus.
            return

    async def reconcile(
        self,
        source_webspace_id: str,
        apply: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]],
        *,
        wait: bool,
    ) -> dict[str, Any]:
        source = str(source_webspace_id or "").strip()
        loop = asyncio.get_running_loop()
        key = (id(loop), source)
        existing = _TASKS.get(key)
        if existing is None or existing.done():
            task = loop.create_task(self._run(source, apply), name=f"builder-preview-reconcile:{source}")
            _TASKS[key] = task

            def _cleanup(done: asyncio.Task[Any]) -> None:
                if _TASKS.get(key) is done:
                    _TASKS.pop(key, None)

            task.add_done_callback(_cleanup)
        else:
            task = existing
        if wait:
            return await asyncio.shield(task)
        current = self.describe(source)
        return {**current, "scheduled": True}

    async def _run(
        self,
        source_webspace_id: str,
        apply: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]],
    ) -> dict[str, Any]:
        apply_lock = _source_lock(_APPLY_LOCKS, source_webspace_id, threading.Lock)
        acquired = False
        try:
            while not acquired:
                acquired = apply_lock.acquire(blocking=False)
                if not acquired:
                    await asyncio.sleep(0.025)
            while True:
                requested = self.describe(source_webspace_id)
                generation = int(requested.get("generation") or 0)
                desired = str(requested.get("desired_scenario") or "").strip()
                if not desired:
                    return requested
                if (
                    str(requested.get("status") or "") == "ready"
                    and str(requested.get("observed_scenario") or "").strip() == desired
                ):
                    return requested
                started, current = self._update_if_current(
                    source_webspace_id,
                    generation,
                    status="running",
                    started_at=time.time(),
                    completed_at=None,
                    error=None,
                )
                if not current:
                    continue
                try:
                    result = dict(await apply(started))
                    accepted = bool(result.get("ok", True)) and bool(result.get("accepted", True))
                    if not accepted:
                        raise RuntimeError(str(result.get("error") or "preview_materialization_rejected"))
                except BaseException as exc:
                    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                        raise
                    failed, current = self._update_if_current(
                        source_webspace_id,
                        generation,
                        status="failed",
                        completed_at=time.time(),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    if current:
                        return failed
                    continue
                if bool(result.get("background_rebuild") or result.get("scheduled")):
                    accepted_record, current = self._update_if_current(
                        source_webspace_id,
                        generation,
                        status="accepted",
                        completed_at=time.time(),
                        error=None,
                        result=result,
                    )
                    if current:
                        return accepted_record
                    continue
                ready, current = self._update_if_current(
                    source_webspace_id,
                    generation,
                    status="ready",
                    observed_scenario=desired,
                    completed_at=time.time(),
                    error=None,
                    result=result,
                )
                if current:
                    self._publish_observed(ready)
                    return ready
        finally:
            if acquired:
                apply_lock.release()


__all__ = ["BuilderPreviewReconciler"]
