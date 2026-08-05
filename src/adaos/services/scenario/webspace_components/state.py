from __future__ import annotations

import asyncio
from typing import Any


class WebspaceTaskState:
    """Own mutable task, pending-work, and runtime status registries."""

    def __init__(self) -> None:
        self.scenario_switch_rebuild_tasks: dict[str, asyncio.Task[Any]] = {}
        self.webspace_rebuild_status: dict[str, dict[str, Any]] = {}
        self.webspace_recovery_command_cache: dict[str, dict[str, Any]] = {}
        self.skill_runtime_rebuild_tasks: dict[str, asyncio.Task[Any]] = {}
        self.skill_runtime_rebuild_pending: dict[str, dict[str, Any]] = {}
        self.skill_runtime_rebuild_stats: dict[str, dict[str, Any]] = {}
        self.webspace_listing_sync_task: asyncio.Task[Any] | None = None
        self.workflow_sync_tasks: dict[str, asyncio.Task[Any]] = {}
        self.workflow_sync_pending: dict[str, dict[str, Any]] = {}
        self.workflow_sync_stats: dict[str, dict[str, Any]] = {}
        self.live_room_refresh_tasks: dict[str, asyncio.Task[Any]] = {}
        self.live_room_refresh_pending: dict[str, dict[str, Any]] = {}
        self.live_room_refresh_stats: dict[str, dict[str, Any]] = {}
        self.builder_ystore_backup_tasks: dict[str, asyncio.Task[Any]] = {}
        self.member_snapshot_rebuild_at: dict[str, float] = {}
        self.member_snapshot_rebuild_tasks: dict[str, asyncio.Task[Any]] = {}
        self.member_snapshot_rebuild_delayed_tasks: dict[str, asyncio.Task[Any]] = {}
        self.member_snapshot_rebuild_dirty: dict[str, dict[str, Any]] = {}
        self.member_snapshot_rebuild_stats: dict[str, dict[str, Any]] = {}
        self.member_snapshot_rebuild_material_fingerprint: dict[str, str] = {}
