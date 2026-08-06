from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WebspaceTaskSchedulingOperations:
    clone_json_like: Any
    complete_scenario_switch_rebuild: Any
    copy_timing_map: Any
    derive_phase_timings: Any
    elapsed_ms: Any
    is_control_flow_base_exception: Any
    live_room_refresh_debounce_s: Any
    live_room_refresh_stats: Any
    logger: Any
    mark_member_snapshot_rebuild_dirty: Any
    member_snapshot_rebuild_request_id: Any
    member_snapshot_rebuild_stats: Any
    pending_materialization_snapshot: Any
    rebuild_webspace_from_sources: Any
    scenario_switch_background_route_yield_s: Any
    scenario_switching: Any
    scenario_workflow_runtime_type: Any
    schedule_member_snapshot_rebuild: Any
    seed_member_snapshot_ydoc_defaults: Any
    set_webspace_rebuild_status: Any
    set_webspace_rebuild_status_if_current: Any
    task_state: Any
    workflow_sync_debounce_s: Any
    workflow_sync_stats: Any


class WebspaceTaskSchedulingService:
    def schedule_live_room_refresh(
        self,
        operations: WebspaceTaskSchedulingOperations,
        *,
        webspace_id: str,
        reason: str,
        persist_repair: bool | None = None,
        force_full_state_update: bool = False,
        materialized_payload: Mapping[str, Any] | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = str(webspace_id or "").strip()
        if not key:
            return {"scheduled": False, "reason": "missing_webspace"}

        stats = operations.live_room_refresh_stats(key)
        stats["requested_total"] = int(stats.get("requested_total") or 0) + 1
        stats["last_reason"] = str(reason or "").strip()
        stats["last_requested_at"] = time.time()

        request = {
            "webspace_id": key,
            "reason": str(reason or "").strip() or "live_room_refresh",
        }
        if persist_repair is not None:
            request["persist_repair"] = bool(persist_repair)
        if force_full_state_update:
            request["force_full_state_update"] = True
        if isinstance(materialized_payload, Mapping) and materialized_payload:
            request["materialized_payload"] = operations.clone_json_like(materialized_payload)
        if isinstance(materialization_identity, Mapping) and materialization_identity:
            request["materialization_identity"] = operations.clone_json_like(materialization_identity)
        current = operations.task_state.active_task(operations.task_state.LIVE_ROOM_REFRESH, key)
        if current is not None:
            operations.task_state.put_record(operations.task_state.LIVE_ROOM_REFRESH_PENDING, key, request)
            stats["coalesced_total"] = int(stats.get("coalesced_total") or 0) + 1
            return {
                "scheduled": True,
                "deferred": True,
                "coalesced": True,
                "task": current.get_name(),
            }

        async def _runner(initial: dict[str, Any]) -> None:
            current_request = dict(initial)
            try:
                while True:
                    delay = operations.live_room_refresh_debounce_s()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    pending_before_start = operations.task_state.pop_record(
                        operations.task_state.LIVE_ROOM_REFRESH_PENDING,
                        key,
                    )
                    if pending_before_start:
                        current_request = dict(pending_before_start)
                    active_reason = str(current_request.get("reason") or "").strip() or "live_room_refresh"
                    started = time.perf_counter()
                    try:
                        refresh_kwargs: dict[str, Any] = {"reason": active_reason}
                        if "persist_repair" in current_request:
                            refresh_kwargs["persist_repair"] = bool(current_request.get("persist_repair"))
                        request_payload = current_request.get("materialized_payload")
                        request_identity = current_request.get("materialization_identity")
                        if isinstance(request_payload, Mapping) and request_payload:
                            from adaos.services.yjs.gateway import apply_materialized_payload_to_live_room  # pylint: disable=import-outside-toplevel

                            if bool(current_request.get("force_full_state_update")):
                                refresh_kwargs["force_full_state_update"] = True
                            await apply_materialized_payload_to_live_room(
                                key,
                                materialized_payload=request_payload,
                                **refresh_kwargs,
                                materialization_identity=(
                                    request_identity
                                    if isinstance(request_identity, Mapping) and request_identity
                                    else None
                                ),
                            )
                        else:
                            from adaos.services.yjs.gateway import reconcile_live_webspace_effective_branches  # pylint: disable=import-outside-toplevel

                            await reconcile_live_webspace_effective_branches(
                                key,
                                **refresh_kwargs,
                            )
                        stats = operations.live_room_refresh_stats(key)
                        stats["completed_total"] = int(stats.get("completed_total") or 0) + 1
                        stats["last_completed_at"] = time.time()
                        operations.logger.info(
                            "deferred live-room refresh completed webspace=%s reason=%s duration_ms=%.3f",
                            key,
                            active_reason,
                            operations.elapsed_ms(started),
                        )
                    except Exception:
                        stats = operations.live_room_refresh_stats(key)
                        stats["failed_total"] = int(stats.get("failed_total") or 0) + 1
                        operations.logger.warning(
                            "deferred live-room refresh failed webspace=%s reason=%s",
                            key,
                            active_reason,
                            exc_info=True,
                        )
                    next_request = operations.task_state.pop_record(
                        operations.task_state.LIVE_ROOM_REFRESH_PENDING,
                        key,
                    )
                    if not next_request:
                        break
                    current_request = dict(next_request)
            finally:
                operations.task_state.pop_task(operations.task_state.LIVE_ROOM_REFRESH, key, expected=task)

        task = asyncio.create_task(
            _runner(request),
            name=f"live-room-refresh:{key}"[:120],
        )
        operations.task_state.put_task(operations.task_state.LIVE_ROOM_REFRESH, key, task)
        stats["scheduled_total"] = int(stats.get("scheduled_total") or 0) + 1
        return {
            "scheduled": True,
            "deferred": True,
            "coalesced": False,
            "task": task.get_name(),
        }


    def schedule_workflow_sync(
        self,
        operations: WebspaceTaskSchedulingOperations,
        ctx: Any,
        *,
        webspace_id: str,
        scenario_id: str,
        reason: str,
    ) -> dict[str, Any]:
        key = str(webspace_id or "").strip()
        scenario_token = str(scenario_id or "").strip()
        if not key or not scenario_token:
            return {"scheduled": False, "reason": "missing_target"}

        stats = operations.workflow_sync_stats(key)
        stats["requested_total"] = int(stats.get("requested_total") or 0) + 1
        stats["last_reason"] = str(reason or "").strip()
        stats["last_scenario_id"] = scenario_token
        stats["last_requested_at"] = time.time()

        request = {
            "webspace_id": key,
            "scenario_id": scenario_token,
            "reason": str(reason or "").strip() or "workflow_sync",
        }
        current = operations.task_state.active_task(operations.task_state.WORKFLOW_SYNC, key)
        if current is not None:
            operations.task_state.put_record(operations.task_state.WORKFLOW_SYNC_PENDING, key, request)
            stats["coalesced_total"] = int(stats.get("coalesced_total") or 0) + 1
            return {
                "scheduled": True,
                "deferred": True,
                "coalesced": True,
                "task": current.get_name(),
                "scenario_id": scenario_token,
            }

        async def _runner(initial: dict[str, Any]) -> None:
            current_request = dict(initial)
            try:
                while True:
                    delay = operations.workflow_sync_debounce_s()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    pending_before_start = operations.task_state.pop_record(
                        operations.task_state.WORKFLOW_SYNC_PENDING,
                        key,
                    )
                    if pending_before_start:
                        current_request = dict(pending_before_start)
                    started = time.perf_counter()
                    active_scenario = str(current_request.get("scenario_id") or "").strip()
                    active_reason = str(current_request.get("reason") or "").strip() or "workflow_sync"
                    try:
                        wf = operations.scenario_workflow_runtime_type(ctx)
                        await wf.sync_workflow_for_webspace(active_scenario, key)
                        stats = operations.workflow_sync_stats(key)
                        stats["completed_total"] = int(stats.get("completed_total") or 0) + 1
                        stats["last_completed_at"] = time.time()
                        operations.logger.info(
                            "deferred workflow sync completed webspace=%s scenario=%s reason=%s duration_ms=%.3f",
                            key,
                            active_scenario,
                            active_reason,
                            operations.elapsed_ms(started),
                        )
                    except BaseException as exc:
                        if operations.is_control_flow_base_exception(exc):
                            raise
                        stats = operations.workflow_sync_stats(key)
                        stats["failed_total"] = int(stats.get("failed_total") or 0) + 1
                        operations.logger.warning(
                            "deferred workflow sync failed webspace=%s scenario=%s reason=%s",
                            key,
                            active_scenario,
                            active_reason,
                            exc_info=True,
                        )
                    next_request = operations.task_state.pop_record(
                        operations.task_state.WORKFLOW_SYNC_PENDING,
                        key,
                    )
                    if not next_request:
                        break
                    current_request = dict(next_request)
            finally:
                operations.task_state.pop_task(operations.task_state.WORKFLOW_SYNC, key, expected=task)

        task = asyncio.create_task(
            _runner(request),
            name=f"workflow-sync:{key}:{scenario_token}"[:120],
        )
        operations.task_state.put_task(operations.task_state.WORKFLOW_SYNC, key, task)
        stats["scheduled_total"] = int(stats.get("scheduled_total") or 0) + 1
        return {
            "scheduled": True,
            "deferred": True,
            "coalesced": False,
            "task": task.get_name(),
            "scenario_id": scenario_token,
        }


    def schedule_member_snapshot_rebuild(
        self,
        operations: WebspaceTaskSchedulingOperations,
        *,
        webspace_id: str,
        node_id: str,
        reason: str = "subnet.member.snapshot.changed",
        request_id: str | None = None,
    ) -> None:
        task_key = f"{str(node_id or '').strip()}\0{str(webspace_id or '').strip()}"
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        delayed = operations.task_state.pop_task(operations.task_state.MEMBER_SNAPSHOT_DELAYED, task_key)
        if delayed is not None and delayed is not current_task and not delayed.done():
            delayed.cancel()
        existing = operations.task_state.active_task(operations.task_state.MEMBER_SNAPSHOT, task_key)
        if existing:
            operations.mark_member_snapshot_rebuild_dirty(task_key=task_key, reason=reason, mode="task_running", request_id=request_id)
            return
        stats = operations.member_snapshot_rebuild_stats(task_key)
        effective_request_id = str(request_id or "").strip() or operations.member_snapshot_rebuild_request_id(
            webspace_id=webspace_id,
            node_id=node_id,
        )
        stats["scheduled_total"] = int(stats.get("scheduled_total") or 0) + 1
        stats["last_reason"] = str(reason or "").strip() or str(stats.get("last_reason") or "") or "subnet.member.snapshot.changed"
        stats["last_scheduled_at"] = time.time()
        stats["last_request_id"] = effective_request_id
        stats["current_request_id"] = effective_request_id

        async def _runner() -> None:
            try:
                try:
                    await operations.seed_member_snapshot_ydoc_defaults(webspace_id=webspace_id, node_id=node_id)
                except Exception:
                    operations.logger.debug(
                        "failed to seed member snapshot defaults webspace=%s node_id=%s",
                        webspace_id,
                        node_id,
                        exc_info=True,
                    )
                operations.logger.info(
                    "starting member snapshot rebuild webspace=%s node_id=%s request_id=%s reason=%s requested_total=%s scheduled_total=%s",
                    webspace_id,
                    node_id,
                    effective_request_id,
                    str(stats.get("last_reason") or reason or "").strip() or "subnet.member.snapshot.changed",
                    int(stats.get("requested_total") or 0),
                    int(stats.get("scheduled_total") or 0),
                )
                result = await operations.rebuild_webspace_from_sources(
                    webspace_id,
                    action="subnet_member_snapshot_sync",
                    source_of_truth="member_runtime_snapshot",
                    request_id=effective_request_id,
                )
                stats["completed_total"] = int(stats.get("completed_total") or 0) + 1
                stats["last_completed_at"] = time.time()
                stats["last_completed_request_id"] = effective_request_id
                dirty = operations.task_state.get_record(operations.task_state.MEMBER_SNAPSHOT_DIRTY, task_key) or {}
                operations.logger.info(
                    "completed member snapshot rebuild webspace=%s node_id=%s request_id=%s accepted=%s error=%s requested_total=%s scheduled_total=%s rerun_total=%s coalesced_running_total=%s coalesced_interval_total=%s delayed_total=%s dirty_pending=%s",
                    webspace_id,
                    node_id,
                    effective_request_id,
                    bool(result.get("accepted")),
                    str(result.get("error") or "").strip() or None,
                    int(stats.get("requested_total") or 0),
                    int(stats.get("scheduled_total") or 0),
                    int(stats.get("rerun_total") or 0),
                    int(stats.get("coalesced_running_total") or 0),
                    int(stats.get("coalesced_interval_total") or 0),
                    int(stats.get("delayed_total") or 0),
                    int(dirty.get("count") or 0),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                operations.logger.warning(
                    "member snapshot rebuild failed webspace=%s node_id=%s",
                    webspace_id,
                    node_id,
                    exc_info=True,
                )
            finally:
                operations.task_state.pop_task(operations.task_state.MEMBER_SNAPSHOT, task_key, expected=task)
                if str(stats.get("current_request_id") or "").strip() == effective_request_id:
                    stats["current_request_id"] = ""
                dirty = operations.task_state.pop_record(operations.task_state.MEMBER_SNAPSHOT_DIRTY, task_key)
                if dirty:
                    stats["rerun_total"] = int(stats.get("rerun_total") or 0) + 1
                    operations.task_state.put_record(
                        operations.task_state.MEMBER_SNAPSHOT_LAST_AT,
                        task_key,
                        time.monotonic(),
                    )
                    rerun_reason = str(dirty.get("last_reason") or reason or "").strip() or "subnet.member.snapshot.changed"
                    operations.schedule_member_snapshot_rebuild(
                        webspace_id=webspace_id,
                        node_id=node_id,
                        reason=f"{rerun_reason}:coalesced",
                        request_id=str(dirty.get("last_request_id") or "").strip() or None,
                    )

        task = asyncio.create_task(
            _runner(),
            name=f"member-snapshot-rebuild:{webspace_id}:{node_id}",
        )
        operations.task_state.put_task(operations.task_state.MEMBER_SNAPSHOT, task_key, task)


    def schedule_scenario_switch_rebuild(
        self,
        operations: WebspaceTaskSchedulingOperations,
        webspace_id: str,
        *,
        scenario_id: str,
        scenario_resolution: str | None,
        switch_mode: str | None = None,
        switch_timings_ms: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        request_source: str | None = None,
        request_client: str | None = None,
    ) -> None:
        switch_mode = "pointer_only"
        request_id = str(request_id or "").strip() or secrets.token_hex(8)
        initial_phase_timings = operations.derive_phase_timings(
            switch_timings_ms=switch_timings_ms,
            rebuild_timings_ms=None,
            switch_mode=switch_mode,
        )
        initial_materialization = operations.pending_materialization_snapshot(
            webspace_id,
            scenario_id=scenario_id,
            snapshot_source="rebuild:scheduled",
        )
        operations.set_webspace_rebuild_status(
            webspace_id,
            status="scheduled",
            pending=True,
            background=True,
            request_id=request_id,
            request_source=str(request_source or "").strip() or None,
            request_client=str(request_client or "").strip() or None,
            action="scenario_switch_rebuild",
            source_of_truth="scenario_switch",
            scenario_id=scenario_id,
            scenario_resolution=scenario_resolution,
            switch_mode=str(switch_mode or "") or None,
            requested_at=time.time(),
            started_at=None,
            finished_at=None,
            error=None,
            projection_refresh=None,
            registry_summary=None,
            resolver=None,
            apply_summary=None,
            timings_ms=None,
            switch_timings_ms=operations.copy_timing_map(switch_timings_ms),
            semantic_rebuild_timings_ms=None,
            phase_timings_ms=initial_phase_timings,
            materialization=initial_materialization,
        )
        async def _operation() -> None:
            route_yield_s = operations.scenario_switch_background_route_yield_s()
            if route_yield_s > 0:
                await asyncio.sleep(route_yield_s)
            operations.set_webspace_rebuild_status_if_current(
                webspace_id,
                request_id,
                status="running",
                pending=True,
                background=True,
                switch_mode=str(switch_mode or "") or None,
                started_at=time.time(),
                finished_at=None,
                error=None,
                projection_refresh=None,
                registry_summary=None,
                resolver=None,
                apply_summary=None,
                timings_ms=None,
                semantic_rebuild_timings_ms=None,
                materialization=operations.pending_materialization_snapshot(
                    webspace_id,
                    scenario_id=scenario_id,
                    snapshot_source="rebuild:running",
                ),
            )
            result = await operations.complete_scenario_switch_rebuild(
                webspace_id,
                scenario_id=scenario_id,
                scenario_resolution=scenario_resolution,
                request_id=request_id,
                switch_mode=switch_mode,
                switch_timings_ms=None,
            )
            if bool(result.get("accepted")) or str(result.get("error") or "").strip() == "stale_rebuild_superseded":
                return
            operations.set_webspace_rebuild_status_if_current(
                webspace_id,
                request_id,
                status="failed",
                pending=False,
                background=True,
                finished_at=time.time(),
                error=str(result.get("error") or "scenario_switch_rebuild_failed"),
                switch_mode=str(switch_mode or "") or None,
                projection_refresh=result.get("projection_refresh"),
                resolver=result.get("resolver"),
                apply_summary=result.get("apply_summary"),
                timings_ms=operations.copy_timing_map(result.get("timings_ms")),
                switch_timings_ms=operations.copy_timing_map(result.get("switch_timings_ms") or switch_timings_ms),
                semantic_rebuild_timings_ms=operations.copy_timing_map(result.get("semantic_rebuild_timings_ms")),
                phase_timings_ms=operations.copy_timing_map(result.get("phase_timings_ms")),
            )
            operations.logger.warning(
                "background scenario switch rebuild rejected webspace=%s scenario=%s error=%s",
                webspace_id,
                scenario_id,
                result.get("error"),
            )

        def _on_cancel() -> None:
            operations.set_webspace_rebuild_status_if_current(
                webspace_id,
                request_id,
                status="cancelled",
                pending=False,
                background=True,
                finished_at=time.time(),
                error="cancelled",
            )

        def _on_error(exc: Exception) -> None:
            operations.set_webspace_rebuild_status_if_current(
                webspace_id,
                request_id,
                status="failed",
                pending=False,
                background=True,
                finished_at=time.time(),
                error=f"background_scenario_switch_rebuild_failed:{type(exc).__name__}",
            )
            operations.logger.warning(
                "background scenario switch rebuild failed webspace=%s scenario=%s",
                webspace_id,
                scenario_id,
                exc_info=True,
            )

        operations.scenario_switching.schedule_rebuild(
            task_state=operations.task_state,
            webspace_id=webspace_id,
            scenario_id=scenario_id,
            operation=_operation,
            on_cancel=_on_cancel,
            on_error=_on_error,
        )

