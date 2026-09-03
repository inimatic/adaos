from __future__ import annotations

import asyncio
import inspect
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from .state import WebspaceTaskState


def _scenario_overlay_busy_timeout_ms() -> int:
    raw = str(os.environ.get("ADAOS_SCENARIO_OVERLAY_BUSY_TIMEOUT_MS") or "").strip()
    try:
        value = int(raw) if raw else 200
    except ValueError:
        value = 200
    return max(25, min(1_000, value))


@dataclass(frozen=True)
class ScenarioSwitchRequest:
    webspace_id: str
    scenario_id: str
    set_home: bool
    wait_for_rebuild: bool
    request_id: str | None
    request_source: str | None
    request_client: str | None


@dataclass(frozen=True)
class ScenarioSwitchDecision:
    action: str
    reason: str | None = None


@dataclass(frozen=True)
class ScenarioSwitchOperations:
    """Explicit runtime dependencies used by one scenario-switch transaction."""

    task_state: WebspaceTaskState
    log: Any
    workspace_index: Any
    describe_operational_state: Callable[..., Any]
    describe_rebuild_state: Callable[..., Any]
    record_timing: Callable[..., Any]
    materialization_scenario_from_rebuild_state: Callable[..., Any]
    read_effective_materialization_scenario: Callable[..., Any]
    scenario_switch_mode: Callable[..., Any]
    copy_timing_map: Callable[..., Any]
    derive_phase_timings: Callable[..., Any]
    finalize_timing_map: Callable[..., Any]
    scenario_exists_for_switch: Callable[..., Any]
    set_rebuild_status: Callable[..., Any]
    set_rebuild_status_if_current: Callable[..., Any]
    sync_webspace_listing_target: Callable[..., Any]
    schedule_scenario_switch_rebuild: Callable[..., Any]
    complete_scenario_switch_rebuild: Callable[..., Any]
    set_map_value_if_changed: Callable[..., Any]
    write_meta: Callable[..., Any]
    async_get_ydoc: Callable[..., Any]
    mutate_live_room: Callable[..., Any]


class WebspaceScenarioSwitchingService:
    """Own request normalization and transition policy for scenario switches."""

    @staticmethod
    def mode() -> str:
        return "pointer_only"

    @staticmethod
    def normalize_request(
        webspace_id: Any,
        scenario_id: Any,
        *,
        set_home: bool | None,
        wait_for_rebuild: bool,
        request_id: str | None,
        request_source: str | None,
        request_client: str | None,
    ) -> ScenarioSwitchRequest:
        normalized_webspace_id = str(webspace_id or "").strip()
        normalized_scenario_id = str(scenario_id or "").strip()
        if not normalized_webspace_id:
            raise ValueError("webspace_id is required")
        if not normalized_scenario_id:
            raise ValueError("scenario_id is required")
        return ScenarioSwitchRequest(
            webspace_id=normalized_webspace_id,
            scenario_id=normalized_scenario_id,
            set_home=bool(set_home) if set_home is not None else False,
            wait_for_rebuild=bool(wait_for_rebuild),
            request_id=str(request_id or "").strip() or None,
            request_source=str(request_source or "").strip() or None,
            request_client=str(request_client or "").strip() or None,
        )

    @staticmethod
    def decide(
        *,
        current_scenario: Any,
        target_scenario: str,
        rebuild_state: Mapping[str, Any],
        materialization_matches_target: bool,
    ) -> ScenarioSwitchDecision:
        current_matches = str(current_scenario or "").strip() == target_scenario
        rebuild_matches = str(rebuild_state.get("scenario_id") or "").strip() == target_scenario
        pending = bool(rebuild_state.get("pending"))
        status = str(rebuild_state.get("status") or "").strip().lower()
        if current_matches and not pending and status == "ready" and rebuild_matches and materialization_matches_target:
            return ScenarioSwitchDecision(action="skip", reason="already_current_ready")
        if current_matches and pending and rebuild_matches:
            return ScenarioSwitchDecision(action="join", reason="already_pending_rebuild")
        return ScenarioSwitchDecision(action="switch")

    @staticmethod
    def loader_space(row: Any) -> str:
        try:
            return str(row.effective_source_mode or "").strip() or "workspace"
        except Exception:
            return "workspace"

    @staticmethod
    async def _notify(
        callback: Callable[..., Any] | None,
        *args: Any,
    ) -> None:
        if callback is None:
            return
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    def schedule_rebuild(
        self,
        *,
        task_state: WebspaceTaskState,
        webspace_id: str,
        scenario_id: str,
        operation: Callable[[], Awaitable[Any]],
        on_cancel: Callable[[], Any] | None = None,
        on_error: Callable[[Exception], Any] | None = None,
    ) -> asyncio.Task[Any]:
        """Own replacement, completion cleanup, and failure boundaries."""

        async def _runner() -> None:
            try:
                await operation()
            except asyncio.CancelledError:
                await self._notify(on_cancel)
                raise
            except Exception as exc:
                await self._notify(on_error, exc)
            finally:
                task_state.pop_task(
                    task_state.SCENARIO_SWITCH,
                    webspace_id,
                    expected=task,
                )

        task = asyncio.create_task(
            _runner(),
            name=f"webspace-scenario-switch:{webspace_id}:{scenario_id}",
        )
        task_state.put_task(
            task_state.SCENARIO_SWITCH,
            webspace_id,
            task,
            cancel_existing=True,
        )
        return task

    @staticmethod
    async def await_existing_rebuild(
        task_state: WebspaceTaskState,
        webspace_id: str,
    ) -> bool:
        task = task_state.active_task(task_state.SCENARIO_SWITCH, webspace_id)
        if task is None:
            return False
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        return True

    @staticmethod
    async def _workspace_row(operations: ScenarioSwitchOperations, webspace_id: str) -> Any:
        def _read_or_create() -> Any:
            return operations.workspace_index.get_workspace(webspace_id) or operations.workspace_index.ensure_workspace(
                webspace_id
            )

        return await asyncio.to_thread(_read_or_create)

    @staticmethod
    async def _set_home_scenario(
        operations: ScenarioSwitchOperations,
        webspace_id: str,
        scenario_id: str,
    ) -> Any:
        return await asyncio.to_thread(
            operations.workspace_index.set_workspace_manifest,
            webspace_id,
            home_scenario=scenario_id,
        )


    async def switch(
        self,
        operations: ScenarioSwitchOperations,
        webspace_id: str,
        scenario_id: str,
        *,
        set_home: bool | None = None,
        wait_for_rebuild: bool = True,
        request_id: str | None = None,
        request_source: str | None = None,
        request_client: str | None = None,
    ) -> dict[str, Any]:
        request = self.normalize_request(
            webspace_id,
            scenario_id,
            set_home=set_home,
            wait_for_rebuild=wait_for_rebuild,
            request_id=request_id,
            request_source=request_source,
            request_client=request_client,
        )
        webspace_id = request.webspace_id
        scenario_id = request.scenario_id
        wait_for_rebuild = request.wait_for_rebuild
        request_id = request.request_id
        request_source = request.request_source
        request_client = request.request_client
        overlay_persistence: dict[str, Any] = {"state": "ready", "pending": False}

        switch_started = time.perf_counter()
        timings_ms: dict[str, float] = {}
        stage_started = time.perf_counter()
        state_before = await operations.describe_operational_state(webspace_id)
        operations.record_timing(timings_ms, "describe_state_before", stage_started)

        stage_started = time.perf_counter()
        row = await self._workspace_row(operations, webspace_id)
        resolved_set_home = request.set_home
        operations.record_timing(timings_ms, "resolve_manifest_policy", stage_started)
        stage_started = time.perf_counter()
        rebuild_state_before = operations.describe_rebuild_state(webspace_id)
        operations.record_timing(timings_ms, "describe_rebuild_before", stage_started)
        materialized_scenario_before: str | None = None
        materialization_matches_target = True
        if str(state_before.current_scenario or "").strip() == scenario_id:
            stage_started = time.perf_counter()
            materialized_scenario_before = operations.materialization_scenario_from_rebuild_state(rebuild_state_before)
            if materialized_scenario_before is None:
                materialized_scenario_before = await operations.read_effective_materialization_scenario(webspace_id)
            operations.record_timing(timings_ms, "read_materialization_scenario_before", stage_started)
            materialization_matches_target = (
                materialized_scenario_before is None
                or str(materialized_scenario_before or "").strip() == scenario_id
            )
            if materialized_scenario_before and not materialization_matches_target:
                operations.log.warning(
                    "desktop.scenario.set forcing rebuild for materialization mismatch webspace=%s current_scenario=%s materialized_scenario=%s target_scenario=%s",
                    webspace_id,
                    state_before.current_scenario,
                    materialized_scenario_before,
                    scenario_id,
                )

        operations.log.info(
            "desktop.scenario.set webspace=%s scenario=%s requested_set_home=%s resolved_set_home=%s request_source=%s request_id=%s request_client=%s",
            webspace_id,
            scenario_id,
            set_home,
            resolved_set_home,
            str(request_source or "").strip() or "-",
            str(request_id or "").strip() or "-",
            str(request_client or "").strip() or "-",
        )
        switch_mode = operations.scenario_switch_mode()
        atomic_selector_commit = True
        selector_commit_mode = "materialization_transaction"
        loader_space = self.loader_space(row)
        def _build_switch_skip_result(*, skip_reason: str, rebuild_state: Mapping[str, Any], background_rebuild: bool) -> dict[str, Any]:
            phase_timings = operations.copy_timing_map(rebuild_state.get("phase_timings_ms"))
            if not phase_timings:
                phase_timings = operations.derive_phase_timings(
                    switch_timings_ms=finalized_timings,
                    rebuild_timings_ms=operations.copy_timing_map(rebuild_state.get("timings_ms")),
                    semantic_rebuild_timings_ms=operations.copy_timing_map(rebuild_state.get("semantic_rebuild_timings_ms")),
                    switch_mode="noop",
                )
            return {
                "ok": True,
                "accepted": True,
                "webspace_id": webspace_id,
                "scenario_id": scenario_id,
                "kind": row.effective_kind,
                "source_mode": row.effective_source_mode,
                "current_scenario_before": state_before.current_scenario,
                "home_scenario_before": state_before.effective_home_scenario,
                "home_scenario": row.effective_home_scenario,
                "set_home": resolved_set_home,
                "background_rebuild": background_rebuild,
                "scenario_switch_mode": switch_mode,
                "selector_commit_mode": "unchanged",
                "switch_skipped": True,
                "skip_reason": skip_reason,
                "timings_ms": finalized_timings,
                "rebuild_timings_ms": operations.copy_timing_map(rebuild_state.get("timings_ms")),
                "semantic_rebuild_timings_ms": operations.copy_timing_map(rebuild_state.get("semantic_rebuild_timings_ms")),
                "resolver": dict(rebuild_state.get("resolver") or {})
                if isinstance(rebuild_state.get("resolver"), Mapping)
                else None,
                "apply_summary": dict(rebuild_state.get("apply_summary") or {})
                if isinstance(rebuild_state.get("apply_summary"), Mapping)
                else None,
                "phase_timings_ms": phase_timings,
            }

        switch_decision = self.decide(
            current_scenario=state_before.current_scenario,
            target_scenario=scenario_id,
            rebuild_state=rebuild_state_before,
            materialization_matches_target=materialization_matches_target,
        )
        if switch_decision.action == "skip":
            if resolved_set_home and row.effective_home_scenario != scenario_id:
                stage_started = time.perf_counter()
                row = await self._set_home_scenario(operations, webspace_id, scenario_id)
                operations.record_timing(timings_ms, "persist_home_scenario", stage_started)

                stage_started = time.perf_counter()
                await operations.sync_webspace_listing_target(webspace_id)
                operations.record_timing(timings_ms, "sync_listing", stage_started)

            finalized_timings = operations.finalize_timing_map(timings_ms, started_at=switch_started)
            operations.log.info(
                "desktop.scenario.set skipped webspace=%s scenario=%s mode=%s timings_ms=%s",
                webspace_id,
                scenario_id,
                switch_mode,
                finalized_timings,
            )
            return _build_switch_skip_result(
                skip_reason=str(switch_decision.reason or "already_current_ready"),
                rebuild_state=rebuild_state_before,
                background_rebuild=False,
            )

        if switch_decision.action == "join":
            if resolved_set_home and row.effective_home_scenario != scenario_id:
                stage_started = time.perf_counter()
                row = await self._set_home_scenario(operations, webspace_id, scenario_id)
                operations.record_timing(timings_ms, "persist_home_scenario", stage_started)

                stage_started = time.perf_counter()
                await operations.sync_webspace_listing_target(webspace_id)
                operations.record_timing(timings_ms, "sync_listing", stage_started)

            if wait_for_rebuild:
                stage_started = time.perf_counter()
                if await self.await_existing_rebuild(operations.task_state, webspace_id):
                    operations.record_timing(timings_ms, "wait_existing_rebuild", stage_started)
                    rebuild_state_before = operations.describe_rebuild_state(webspace_id)

            finalized_timings = operations.finalize_timing_map(timings_ms, started_at=switch_started)
            operations.log.info(
                "desktop.scenario.set deduplicated webspace=%s scenario=%s mode=%s pending=%s timings_ms=%s",
                webspace_id,
                scenario_id,
                switch_mode,
                bool(rebuild_state_before.get("pending")),
                finalized_timings,
            )
            return _build_switch_skip_result(
                skip_reason=str(switch_decision.reason or "already_pending_rebuild"),
                rebuild_state=rebuild_state_before,
                background_rebuild=bool(rebuild_state_before.get("pending") or (not wait_for_rebuild and rebuild_state_before.get("background"))),
            )

        stage_started = time.perf_counter()
        scenario_exists = await asyncio.to_thread(
            operations.scenario_exists_for_switch,
            scenario_id,
            space=loader_space,
        )
        operations.record_timing(timings_ms, "validate_scenario", stage_started)
        if not scenario_exists:
            finalized_timings = operations.finalize_timing_map(timings_ms, started_at=switch_started)
            operations.set_rebuild_status(
                webspace_id,
                status="failed",
                pending=False,
                background=not wait_for_rebuild,
                action="scenario_switch_rebuild",
                source_of_truth="scenario_switch",
                scenario_id=scenario_id,
                scenario_resolution="explicit",
                switch_mode=switch_mode,
                requested_at=time.time(),
                finished_at=time.time(),
                error="scenario_not_found",
                projection_refresh=None,
                registry_summary=None,
                resolver=None,
                apply_summary=None,
                timings_ms=finalized_timings,
                phase_timings_ms=operations.derive_phase_timings(
                    switch_timings_ms=finalized_timings,
                    switch_mode=switch_mode,
                ),
            )
            return {
                "ok": False,
                "accepted": False,
                "error": "scenario_not_found",
                "webspace_id": webspace_id,
                "scenario_id": scenario_id,
                "scenario_switch_mode": switch_mode,
                "timings_ms": finalized_timings,
                "phase_timings_ms": operations.derive_phase_timings(
                    switch_timings_ms=finalized_timings,
                    switch_mode=switch_mode,
                ),
            }

        try:
            if atomic_selector_commit:
                timings_ms["defer_switch_pointer"] = 0.0
            else:
                stage_started = time.perf_counter()

                def _mutator(doc: Any, txn: Any) -> None:
                    ui_map = doc.get_map("ui")
                    operations.set_map_value_if_changed(ui_map, txn, "current_scenario", scenario_id)

                live_applied = operations.mutate_live_room(
                    webspace_id,
                    _mutator,
                    root_names=["ui"],
                    source="webspace_runtime.switch_pointer",
                    owner="core:webspace_runtime",
                    channel="core.webspace_runtime.live_room",
                )
                if live_applied:
                    operations.record_timing(timings_ms, "write_switch_pointer", stage_started)
                else:
                    stage_started = time.perf_counter()
                    async with operations.write_meta(
                        root_names=["ui"],
                        source="webspace_runtime.switch_pointer",
                    ):
                        async with operations.async_get_ydoc(webspace_id) as ydoc:
                            operations.record_timing(timings_ms, "open_doc", stage_started)
                            ui_map = ydoc.get_map("ui")
                            stage_started = time.perf_counter()
                            with ydoc.begin_transaction() as txn:
                                operations.set_map_value_if_changed(ui_map, txn, "current_scenario", scenario_id)
                            operations.record_timing(timings_ms, "write_switch_pointer", stage_started)

            stage_started = time.perf_counter()
            try:
                await asyncio.to_thread(
                    operations.workspace_index.set_workspace_current_scenario_overlay,
                    webspace_id,
                    scenario_id,
                    busy_timeout_ms=_scenario_overlay_busy_timeout_ms(),
                )
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                overlay_persistence = await asyncio.to_thread(
                    operations.workspace_index.defer_workspace_current_scenario_overlay,
                    webspace_id,
                    scenario_id,
                    reason="scenario_switch.sqlite_locked",
                )
                overlay_persistence["state"] = "deferred"
                overlay_persistence["error"] = f"{type(exc).__name__}: {exc}"
                operations.log.warning(
                    "scenario switch overlay persistence deferred webspace=%s scenario=%s",
                    webspace_id,
                    scenario_id,
                )
            operations.record_timing(timings_ms, "persist_current_scenario", stage_started)
        except Exception:
            finalized_timings = operations.finalize_timing_map(timings_ms, started_at=switch_started)
            operations.set_rebuild_status(
                webspace_id,
                status="failed",
                pending=False,
                background=not wait_for_rebuild,
                action="scenario_switch_rebuild",
                source_of_truth="scenario_switch",
                scenario_id=scenario_id,
                scenario_resolution="explicit",
                switch_mode=switch_mode,
                requested_at=time.time(),
                finished_at=time.time(),
                error="scenario_switch_failed",
                projection_refresh=None,
                registry_summary=None,
                resolver=None,
                apply_summary=None,
                timings_ms=finalized_timings,
                phase_timings_ms=operations.derive_phase_timings(
                    switch_timings_ms=finalized_timings,
                    switch_mode=switch_mode,
                ),
            )
            operations.log.warning(
                "failed to switch scenario for webspace=%s scenario=%s timings_ms=%s",
                webspace_id,
                scenario_id,
                finalized_timings,
                exc_info=True,
            )
            return {
                "ok": False,
                "accepted": False,
                "error": "scenario_switch_failed",
                "webspace_id": webspace_id,
                "scenario_id": scenario_id,
                "scenario_switch_mode": switch_mode,
                "timings_ms": finalized_timings,
                "phase_timings_ms": operations.derive_phase_timings(
                    switch_timings_ms=finalized_timings,
                    switch_mode=switch_mode,
                ),
            }

        try:
            from adaos.services.yjs.gateway_ws import (  # pylint: disable=import-outside-toplevel
                note_authoritative_current_scenario,
            )

            if not atomic_selector_commit:
                note_authoritative_current_scenario(
                    webspace_id,
                    scenario_id,
                    reason="scenario_switch",
                )
        except Exception:
            operations.log.debug("failed to publish authoritative current_scenario lease", exc_info=True)

        stage_started = time.perf_counter()
        row = await self._workspace_row(operations, webspace_id)
        operations.record_timing(timings_ms, "refresh_manifest_row", stage_started)
        if resolved_set_home:
            stage_started = time.perf_counter()
            row = await self._set_home_scenario(operations, webspace_id, scenario_id)
            operations.record_timing(timings_ms, "persist_home_scenario", stage_started)

            stage_started = time.perf_counter()
            await operations.sync_webspace_listing_target(webspace_id)
            operations.record_timing(timings_ms, "sync_listing", stage_started)

        if not wait_for_rebuild:
            scheduled_switch_timings = operations.finalize_timing_map(dict(timings_ms), started_at=switch_started)
            stage_started = time.perf_counter()
            operations.schedule_scenario_switch_rebuild(
                webspace_id,
                scenario_id=scenario_id,
                scenario_resolution="explicit",
                switch_mode=switch_mode,
                switch_timings_ms=scheduled_switch_timings,
                request_id=request_id,
                request_source=request_source,
                request_client=request_client,
            )
            operations.record_timing(timings_ms, "schedule_background_rebuild", stage_started)
            finalized_timings = operations.finalize_timing_map(timings_ms, started_at=switch_started)
            current_status = operations.describe_rebuild_state(webspace_id)
            operations.set_rebuild_status_if_current(
                webspace_id,
                str(current_status.get("request_id") or "").strip() or None,
                switch_timings_ms=finalized_timings,
                phase_timings_ms=operations.derive_phase_timings(
                    switch_timings_ms=finalized_timings,
                    switch_mode=switch_mode,
                ),
            )
            operations.log.info(
                "desktop.scenario.set accepted webspace=%s scenario=%s mode=%s background=%s timings_ms=%s",
                webspace_id,
                scenario_id,
                switch_mode,
                True,
                finalized_timings,
            )
            return {
                "ok": True,
                "accepted": True,
                "webspace_id": webspace_id,
                "scenario_id": scenario_id,
                "request_id": str(request_id or "").strip() or str(current_status.get("request_id") or "").strip() or None,
                "request_source": str(request_source or "").strip() or None,
                "request_client": str(request_client or "").strip() or None,
                "kind": row.effective_kind,
                "source_mode": row.effective_source_mode,
                "current_scenario_before": state_before.current_scenario,
                "home_scenario_before": state_before.effective_home_scenario,
                "home_scenario": row.effective_home_scenario,
                "set_home": resolved_set_home,
                "background_rebuild": True,
                "scenario_switch_mode": switch_mode,
                "selector_commit_mode": selector_commit_mode,
                "overlay_persistence": dict(overlay_persistence),
                "timings_ms": finalized_timings,
                "phase_timings_ms": operations.derive_phase_timings(
                    switch_timings_ms=finalized_timings,
                    switch_mode=switch_mode,
                ),
            }

        stage_started = time.perf_counter()
        rebuild_result = await operations.complete_scenario_switch_rebuild(
            webspace_id,
            scenario_id=scenario_id,
            scenario_resolution="explicit",
            switch_mode=switch_mode,
            switch_timings_ms=operations.finalize_timing_map(dict(timings_ms), started_at=switch_started),
        )
        operations.record_timing(timings_ms, "wait_rebuild", stage_started)
        if not bool(rebuild_result.get("accepted")):
            final_switch_timings = operations.finalize_timing_map(timings_ms, started_at=switch_started)
            rebuild_result["switch_timings_ms"] = final_switch_timings
            rebuild_result["phase_timings_ms"] = operations.derive_phase_timings(
                switch_timings_ms=final_switch_timings,
                rebuild_timings_ms=rebuild_result.get("timings_ms"),
                semantic_rebuild_timings_ms=rebuild_result.get("semantic_rebuild_timings_ms"),
                switch_mode=switch_mode,
            )
            return rebuild_result

        finalized_timings = operations.finalize_timing_map(timings_ms, started_at=switch_started)
        phase_timings = operations.derive_phase_timings(
            switch_timings_ms=finalized_timings,
            rebuild_timings_ms=rebuild_result.get("timings_ms"),
            semantic_rebuild_timings_ms=rebuild_result.get("semantic_rebuild_timings_ms"),
            switch_mode=switch_mode,
        )
        operations.log.info(
            "desktop.scenario.set completed webspace=%s scenario=%s mode=%s background=%s timings_ms=%s rebuild_timings_ms=%s",
            webspace_id,
            scenario_id,
            switch_mode,
            False,
            finalized_timings,
            rebuild_result.get("timings_ms"),
        )
        return {
            "ok": True,
            "accepted": True,
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
            "kind": row.effective_kind,
            "source_mode": row.effective_source_mode,
            "current_scenario_before": state_before.current_scenario,
            "home_scenario_before": state_before.effective_home_scenario,
            "home_scenario": row.effective_home_scenario,
            "set_home": resolved_set_home,
            "background_rebuild": False,
            "scenario_switch_mode": switch_mode,
            "selector_commit_mode": selector_commit_mode,
            "overlay_persistence": dict(overlay_persistence),
            "timings_ms": finalized_timings,
            "rebuild_timings_ms": operations.copy_timing_map(rebuild_result.get("timings_ms")),
            "semantic_rebuild_timings_ms": operations.copy_timing_map(rebuild_result.get("semantic_rebuild_timings_ms")),
            "live_room_publish": rebuild_result.get("live_room_publish"),
            "live_room_refresh": rebuild_result.get("live_room_refresh"),
            "fresh_doc_rebuild": rebuild_result.get("fresh_doc_rebuild"),
            "resolver": dict(rebuild_result.get("resolver") or {})
            if isinstance(rebuild_result.get("resolver"), Mapping)
            else None,
            "apply_summary": dict(rebuild_result.get("apply_summary") or {})
            if isinstance(rebuild_result.get("apply_summary"), Mapping)
            else None,
            "phase_timings_ms": phase_timings,
        }
