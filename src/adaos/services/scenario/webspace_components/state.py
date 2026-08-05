from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any


class WebspaceTaskState:
    """Own mutable task, pending-work, and runtime status registries.

    The runtime facade addresses named groups through this API and never gets
    the backing dictionaries. Task replacement, identity-safe completion, and
    cancellation therefore cannot diverge between scheduling pipelines.
    """

    SCENARIO_SWITCH = "scenario_switch"
    SKILL_RUNTIME = "skill_runtime"
    WEBSPACE_LISTING = "webspace_listing"
    WORKFLOW_SYNC = "workflow_sync"
    LIVE_ROOM_REFRESH = "live_room_refresh"
    BUILDER_YSTORE_BACKUP = "builder_ystore_backup"
    MEMBER_SNAPSHOT = "member_snapshot"
    MEMBER_SNAPSHOT_DELAYED = "member_snapshot_delayed"

    WEBSPACE_REBUILD_STATUS = "webspace_rebuild_status"
    WEBSPACE_RECOVERY_COMMAND = "webspace_recovery_command"
    SKILL_RUNTIME_PENDING = "skill_runtime_pending"
    SKILL_RUNTIME_STATS = "skill_runtime_stats"
    WORKFLOW_SYNC_PENDING = "workflow_sync_pending"
    WORKFLOW_SYNC_STATS = "workflow_sync_stats"
    LIVE_ROOM_REFRESH_PENDING = "live_room_refresh_pending"
    LIVE_ROOM_REFRESH_STATS = "live_room_refresh_stats"
    MEMBER_SNAPSHOT_LAST_AT = "member_snapshot_last_at"
    MEMBER_SNAPSHOT_DIRTY = "member_snapshot_dirty"
    MEMBER_SNAPSHOT_STATS = "member_snapshot_stats"
    MEMBER_SNAPSHOT_MATERIAL_FINGERPRINT = "member_snapshot_material_fingerprint"

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, asyncio.Task[Any]]] = {
            name: {}
            for name in (
                self.SCENARIO_SWITCH,
                self.SKILL_RUNTIME,
                self.WEBSPACE_LISTING,
                self.WORKFLOW_SYNC,
                self.LIVE_ROOM_REFRESH,
                self.BUILDER_YSTORE_BACKUP,
                self.MEMBER_SNAPSHOT,
                self.MEMBER_SNAPSHOT_DELAYED,
            )
        }
        self._records: dict[str, dict[str, Any]] = {
            name: {}
            for name in (
                self.WEBSPACE_REBUILD_STATUS,
                self.WEBSPACE_RECOVERY_COMMAND,
                self.SKILL_RUNTIME_PENDING,
                self.SKILL_RUNTIME_STATS,
                self.WORKFLOW_SYNC_PENDING,
                self.WORKFLOW_SYNC_STATS,
                self.LIVE_ROOM_REFRESH_PENDING,
                self.LIVE_ROOM_REFRESH_STATS,
                self.MEMBER_SNAPSHOT_LAST_AT,
                self.MEMBER_SNAPSHOT_DIRTY,
                self.MEMBER_SNAPSHOT_STATS,
                self.MEMBER_SNAPSHOT_MATERIAL_FINGERPRINT,
            )
        }

    def _task_group(self, group: str) -> dict[str, asyncio.Task[Any]]:
        try:
            return self._tasks[group]
        except KeyError as exc:
            raise ValueError(f"unknown webspace task group: {group}") from exc

    def _record_group(self, group: str) -> dict[str, Any]:
        try:
            return self._records[group]
        except KeyError as exc:
            raise ValueError(f"unknown webspace record group: {group}") from exc

    def get_task(self, group: str, key: str) -> asyncio.Task[Any] | None:
        return self._task_group(group).get(key)

    def active_task(self, group: str, key: str) -> asyncio.Task[Any] | None:
        task = self.get_task(group, key)
        return task if task is not None and not task.done() else None

    def put_task(
        self,
        group: str,
        key: str,
        task: asyncio.Task[Any],
        *,
        cancel_existing: bool = False,
    ) -> asyncio.Task[Any] | None:
        tasks = self._task_group(group)
        existing = tasks.get(key)
        if cancel_existing and existing is not None and existing is not task and not existing.done():
            existing.cancel()
        tasks[key] = task
        return existing

    def pop_task(
        self,
        group: str,
        key: str,
        *,
        expected: asyncio.Task[Any] | None = None,
    ) -> asyncio.Task[Any] | None:
        tasks = self._task_group(group)
        current = tasks.get(key)
        if expected is not None and current is not expected:
            return None
        return tasks.pop(key, None)

    def clear_tasks(self, group: str, *, cancel: bool = False) -> int:
        tasks = self._task_group(group)
        values = tuple(tasks.values())
        tasks.clear()
        if cancel:
            for task in values:
                if not task.done():
                    task.cancel()
        return len(values)

    def task_count(self, group: str) -> int:
        return len(self._task_group(group))

    def get_record(self, group: str, key: str, default: Any = None) -> Any:
        return self._record_group(group).get(key, default)

    def put_record(self, group: str, key: str, value: Any) -> None:
        self._record_group(group)[key] = value

    def pop_record(self, group: str, key: str, default: Any = None) -> Any:
        return self._record_group(group).pop(key, default)

    def has_record(self, group: str, key: str) -> bool:
        return key in self._record_group(group)

    def record_items(self, group: str) -> tuple[tuple[str, Any], ...]:
        return tuple(self._record_group(group).items())

    def record_count(self, group: str) -> int:
        return len(self._record_group(group))

    def clear_records(self, group: str) -> int:
        records = self._record_group(group)
        count = len(records)
        records.clear()
        return count

    def discard_records(self, group: str, keys: Iterable[str]) -> int:
        records = self._record_group(group)
        removed = 0
        for key in keys:
            if key in records:
                records.pop(key, None)
                removed += 1
        return removed
