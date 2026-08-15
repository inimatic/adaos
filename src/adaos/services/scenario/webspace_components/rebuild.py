from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RebuildOperations:
    """Explicit collaborators for one semantic rebuild transaction."""

    scenario_workflow_runtime_type: Any
    webspace_scenario_runtime_type: Any
    stale_request_error_type: Any
    builder_revision_detached_direct_live_room_updates_enabled: Callable[..., Any]
    builder_revision_fresh_doc_rebuild_enabled: Callable[..., Any]
    builder_revision_projection_refresh_enabled: Callable[..., Any]
    builder_revision_rebuild_prefers_live_room: Callable[..., Any]
    builder_revision_replace_ystore_snapshot_enabled: Callable[..., Any]
    clone_json_like: Callable[..., Any]
    compact_live_room_refresh_result_for_log: Callable[..., Any]
    copy_materialization_snapshot: Callable[..., Any]
    copy_timing_map: Callable[..., Any]
    defer_live_room_refresh_for_rebuild: Callable[..., Any]
    defer_workflow_sync_for_rebuild: Callable[..., Any]
    derive_phase_timings: Callable[..., Any]
    finalize_timing_map: Callable[..., Any]
    invalidate_resolved_webspace_cache: Callable[..., Any]
    is_control_flow_base_exception: Callable[..., Any]
    log: Any
    pending_materialization_snapshot: Callable[..., Any]
    project_webspace_from_scenario: Callable[..., Any]
    publish_live_room_for_rebuild: Callable[..., Any]
    rebuild_action_applies_live_payload: Callable[..., Any]
    rebuild_action_refreshes_live_room: Callable[..., Any]
    record_timing: Callable[..., Any]
    refresh_live_room_after_rebuild_enabled: Callable[..., Any]
    refresh_projection_rules_for_rebuild: Callable[..., Any]
    resolve_projection_refresh_space: Callable[..., Any]
    resolve_rebuild_scenario_target: Callable[..., Any]
    scenario_switch_inline_listing_sync_enabled: Callable[..., Any]
    scenario_switch_materialization_identity: Callable[..., Any]
    schedule_live_room_refresh: Callable[..., Any]
    schedule_workflow_sync: Callable[..., Any]
    seed_webspace_from_scenario_with_options: Callable[..., Any]
    semantic_rebuild_timeout_s: Callable[..., Any]
    set_rebuild_status: Callable[..., Any]
    set_rebuild_status_if_current: Callable[..., Any]
    sync_webspace_listing_target: Callable[..., Any]
    write_meta: Callable[..., Any]
    workflow_sync_for_rebuild_enabled: Callable[..., Any]
    async_get_ydoc: Callable[..., Any]
    describe_rebuild_state: Callable[..., Any]
    emit: Callable[..., Any]
    get_ctx: Callable[..., Any]
    scenarios_loader: Any


class WebspaceRebuildService:
    """Own the semantic rebuild transaction and its publish/sync lifecycle."""

    async def rebuild(
        self,
        operations: RebuildOperations,
        webspace_id: str,
        *,
        action: str = "rebuild",
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
        source_of_truth: str = "current_runtime",
        reseed_from_scenario: bool = False,
        event_payload: dict[str, Any] | None = None,
        request_id: str | None = None,
        switch_mode: str | None = None,
        switch_timings_ms: Mapping[str, Any] | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
        scenario_content_override: Mapping[str, Any] | None = None,
        skill_source_mode: str | None = None,
    ) -> dict[str, Any]:
        """
        Single semantic rebuild primitive for the current runtime.

        Phase 3 keeps the existing storage and frontend contracts intact, but
        routes reload/reset/restore-style operations through one backend-owned
        materialization step so reconcile behaviour is explicit.
        """
        webspace_id = str(webspace_id or "").strip()
        if not webspace_id:
            raise ValueError("webspace_id is required")

        rebuild_started = time.perf_counter()
        timings_ms: Dict[str, float] = {}
        requested_action = str(action or "").strip().lower() or "rebuild"
        target_scenario = str(scenario_id or "").strip() or None
        resolved_scenario_resolution = str(scenario_resolution or "").strip() or None
        status_started_at = time.time()
        if not target_scenario or not resolved_scenario_resolution:
            stage_started = time.perf_counter()
            _state, resolved_target_scenario, resolved_target_resolution = await operations.resolve_rebuild_scenario_target(
                webspace_id,
                target_scenario,
                prefer_manifest_home_before_current=requested_action in {"reload", "reset"},
            )
            if not target_scenario:
                target_scenario = resolved_target_scenario
            if not resolved_scenario_resolution:
                resolved_scenario_resolution = resolved_target_resolution
            operations.record_timing(timings_ms, "resolve_rebuild_target", stage_started)

        previous_status = operations.describe_rebuild_state(webspace_id)
        effective_switch_timings = operations.copy_timing_map(switch_timings_ms) or operations.copy_timing_map(previous_status.get("switch_timings_ms"))
        effective_switch_mode = str(switch_mode or previous_status.get("switch_mode") or "").strip() or None
        if requested_action == "scenario_switch_rebuild":
            effective_switch_mode = "pointer_only"
        effective_materialization_identity = dict(materialization_identity) if isinstance(materialization_identity, Mapping) else None
        if effective_materialization_identity is None and requested_action == "scenario_switch_rebuild" and target_scenario:
            stage_started = time.perf_counter()
            try:
                source_mode_for_identity = operations.resolve_projection_refresh_space(webspace_id)
                effective_materialization_identity = operations.scenario_switch_materialization_identity(
                    webspace_id=webspace_id,
                    scenario_id=target_scenario,
                    source_mode=source_mode_for_identity,
                )
            except Exception:
                effective_materialization_identity = None
                operations.log.debug(
                    "failed to build scenario switch materialization identity webspace=%s scenario=%s",
                    webspace_id,
                    target_scenario,
                    exc_info=True,
                )
            operations.record_timing(timings_ms, "resolve_materialization_identity", stage_started)
        running_materialization = operations.pending_materialization_snapshot(
            webspace_id,
            scenario_id=target_scenario,
            snapshot_source="rebuild:running",
            rebuild_state=previous_status,
        )
        operations.set_rebuild_status(
            webspace_id,
            status="running",
            pending=True,
            background=bool(previous_status.get("background")),
            request_id=request_id,
            action=requested_action,
            source_of_truth=source_of_truth,
            scenario_id=target_scenario,
            scenario_resolution=resolved_scenario_resolution,
            switch_mode=effective_switch_mode,
            requested_at=previous_status.get("requested_at") or status_started_at,
            started_at=status_started_at,
            finished_at=None,
            error=None,
            projection_refresh=None,
            registry_summary=None,
            resolver=None,
            apply_summary=None,
            timings_ms=None,
            switch_timings_ms=effective_switch_timings,
            semantic_rebuild_timings_ms=None,
            phase_timings_ms=None,
            materialization=running_materialization,
        )

        reset_room_result: dict[str, Any] | None = None
        ystore_reset = False
        fresh_doc_rebuild = False
        scenario_switch_payload_rebuild = requested_action == "scenario_switch_rebuild"

        async def _write_reseed_pointer() -> None:
            try:
                async with operations.write_meta(
                    root_names=["ui"],
                    source="webspace_runtime.reseed_pointer",
                ):
                    async with operations.async_get_ydoc(webspace_id) as ydoc:
                        ui_map = ydoc.get_map("ui")
                        with ydoc.begin_transaction() as txn:
                            ui_map.set(txn, "current_scenario", target_scenario)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                operations.log.warning(
                    "failed to write reseed current_scenario pointer webspace=%s scenario=%s",
                    webspace_id,
                    target_scenario,
                    exc_info=True,
                )

        def _note_authoritative_selector(reason: str) -> None:
            if not target_scenario:
                return
            try:
                from adaos.services.yjs.gateway import note_authoritative_current_scenario  # pylint: disable=import-outside-toplevel

                note_authoritative_current_scenario(
                    webspace_id,
                    target_scenario,
                    reason=reason,
                )
            except Exception:
                operations.log.debug(
                    "failed to publish authoritative current_scenario lease webspace=%s scenario=%s",
                    webspace_id,
                    target_scenario,
                    exc_info=True,
                )

        if scenario_switch_payload_rebuild:
            if not target_scenario:
                raise ValueError("scenario_id is required for scenario switch rebuild")

            timings_ms["scenario_switch_transport_preserved"] = 0.0

            if operations.scenario_switch_inline_listing_sync_enabled():
                stage_started = time.perf_counter()
                await operations.sync_webspace_listing_target(webspace_id)
                operations.record_timing(timings_ms, "scenario_switch_sync_listing", stage_started)
            else:
                timings_ms["scenario_switch_sync_listing_deferred"] = 0.0

        should_invalidate_loader_cache = bool(
            target_scenario
            and (
                reseed_from_scenario
                or requested_action == "builder_revision_apply"
                or str(source_of_truth or "").strip().lower() == "builder_revision"
            )
        )
        if should_invalidate_loader_cache:
            stage_started = time.perf_counter()
            try:
                operations.scenarios_loader.invalidate_cache(scenario_id=target_scenario, space="workspace")
                operations.scenarios_loader.invalidate_cache(scenario_id=target_scenario, space="dev")
            except Exception:
                pass
            operations.record_timing(timings_ms, "invalidate_loader_cache", stage_started)
            stage_started = time.perf_counter()
            operations.invalidate_resolved_webspace_cache(
                scenario_id=target_scenario,
                reason=requested_action,
            )
            operations.record_timing(timings_ms, "invalidate_resolver_cache", stage_started)

        if reseed_from_scenario:
            if not target_scenario:
                raise ValueError("scenario_id is required when reseed_from_scenario is enabled")
            _note_authoritative_selector(f"{requested_action}:reseed")
            if requested_action != "reset":
                stage_started = time.perf_counter()
                await _write_reseed_pointer()
                operations.record_timing(timings_ms, "reseed_pointer", stage_started)

            if requested_action == "reset":
                stage_started = time.perf_counter()
                try:
                    from adaos.services.yjs.gateway import reset_live_webspace_room  # pylint: disable=import-outside-toplevel
                    from adaos.services.yjs.store import reset_ystore_for_webspace_async  # pylint: disable=import-outside-toplevel

                    try:
                        reset_room_result = await reset_live_webspace_room(
                            webspace_id,
                            close_reason="webspace_reset",
                            persist_ystore_snapshot=False,
                        )
                    except Exception:
                        pass
                    try:
                        await reset_ystore_for_webspace_async(webspace_id)
                        ystore_reset = True
                    except Exception:
                        pass
                except Exception:
                    operations.log.warning("failed to reset ystore for webspace=%s", webspace_id, exc_info=True)
                operations.record_timing(timings_ms, "reset_runtime_state", stage_started)

                stage_started = time.perf_counter()
                await operations.seed_webspace_from_scenario_with_options(
                    webspace_id,
                    target_scenario,
                )
                operations.record_timing(timings_ms, "seed_from_scenario", stage_started)

                stage_started = time.perf_counter()
                await _write_reseed_pointer()
                operations.record_timing(timings_ms, "reseed_pointer_after_reset", stage_started)
            else:
                stage_started = time.perf_counter()
                await operations.project_webspace_from_scenario(
                    webspace_id,
                    target_scenario,
                    emit_event=False,
                )
                operations.record_timing(timings_ms, "project_scenario_payload", stage_started)

            stage_started = time.perf_counter()
            await operations.sync_webspace_listing_target(webspace_id)
            operations.record_timing(timings_ms, "sync_listing", stage_started)

        ctx = operations.get_ctx()
        stage_started = time.perf_counter()
        if requested_action == "builder_revision_apply" and not operations.builder_revision_projection_refresh_enabled():
            target_space = operations.resolve_projection_refresh_space(webspace_id)
            projection_refresh = {
                "attempted": False,
                "scenario_id": target_scenario,
                "scenario_resolution": resolved_scenario_resolution,
                "space": target_space,
                "rules_loaded": 0,
                "source": "skipped",
                "reason": "builder_revision_apply_reuses_existing_projection_rules",
            }
            operations.record_timing(timings_ms, "projection_refresh_skipped", stage_started)
        else:
            operations.log.info(
                "starting projection refresh webspace=%s action=%s scenario=%s resolution=%s",
                webspace_id,
                requested_action,
                target_scenario,
                resolved_scenario_resolution,
            )
            projection_refresh = await operations.refresh_projection_rules_for_rebuild(
                ctx,
                webspace_id,
                scenario_id=target_scenario,
                scenario_resolution=resolved_scenario_resolution,
            )
            operations.record_timing(timings_ms, "projection_refresh", stage_started)
            operations.log.info(
                "finished projection refresh webspace=%s action=%s scenario=%s result=%s elapsed_ms=%.3f",
                webspace_id,
                requested_action,
                target_scenario,
                json.dumps(operations.clone_json_like(projection_refresh), ensure_ascii=True, sort_keys=True)[:1000],
                float(timings_ms.get("projection_refresh") or 0.0),
            )
        runtime = operations.webspace_scenario_runtime_type(ctx)
        live_room_update_requested = operations.publish_live_room_for_rebuild(requested_action)
        prefer_live_room = (
            operations.builder_revision_rebuild_prefers_live_room()
            if requested_action == "builder_revision_apply"
            else bool(live_room_update_requested)
        )
        publish_live_room = bool(live_room_update_requested)
        if requested_action == "builder_revision_apply" and not prefer_live_room:
            publish_live_room = operations.builder_revision_detached_direct_live_room_updates_enabled()
        payload_only_rebuild = scenario_switch_payload_rebuild or bool(scenario_content_override)
        try:
            stage_started = time.perf_counter()
            rebuild_timeout_s = operations.semantic_rebuild_timeout_s(requested_action)
            initial_scenario_id = (
                target_scenario
                if scenario_switch_payload_rebuild or requested_action == "builder_revision_apply"
                else None
            )
            builder_fresh_doc_rebuild = (
                requested_action == "builder_revision_apply" and operations.builder_revision_fresh_doc_rebuild_enabled()
            )
            if builder_fresh_doc_rebuild:
                fresh_doc_rebuild = True
            rebuild_kwargs = {
                "publish_live_room": publish_live_room,
                "prefer_live_room": prefer_live_room,
                "initial_scenario_id": initial_scenario_id,
                "materialization_identity": effective_materialization_identity,
            }
            if builder_fresh_doc_rebuild:
                rebuild_kwargs["fresh_doc"] = True
                rebuild_kwargs["replace_ystore_snapshot"] = operations.builder_revision_replace_ystore_snapshot_enabled()
            if str(request_id or "").strip():
                rebuild_kwargs["request_id"] = request_id
            operations.log.info(
                "starting semantic rebuild core webspace=%s action=%s scenario=%s live_room_requested=%s publish_live_room=%s prefer_live_room=%s payload_only=%s timeout_s=%s materialization_key=%s",
                webspace_id,
                requested_action,
                target_scenario,
                bool(live_room_update_requested),
                bool(publish_live_room),
                bool(prefer_live_room),
                bool(payload_only_rebuild),
                rebuild_timeout_s,
                (
                    effective_materialization_identity.get("key_hash")
                    if isinstance(effective_materialization_identity, Mapping)
                    else "-"
                ),
            )
            if payload_only_rebuild:
                payload_rebuild_kwargs: dict[str, Any] = {
                    "scenario_id": target_scenario,
                    "materialization_identity": effective_materialization_identity,
                    # A scenario switch resolves plain effective branches. Keep it
                    # off the event loop, but do not pay for a second runtime.
                    "isolate_process": False,
                }
                if scenario_content_override:
                    payload_rebuild_kwargs["scenario_content_override"] = scenario_content_override
                if str(skill_source_mode or "").strip():
                    payload_rebuild_kwargs["skill_source_mode"] = str(skill_source_mode).strip()
                if str(request_id or "").strip():
                    payload_rebuild_kwargs["request_id"] = request_id
                rebuild_coro = runtime.resolve_materialized_payload_async(webspace_id, **payload_rebuild_kwargs)
            else:
                rebuild_coro = runtime.rebuild_webspace_async(webspace_id, **rebuild_kwargs)
            if rebuild_timeout_s is not None:
                timeout_cm = getattr(asyncio, "timeout", None)
                if callable(timeout_cm):
                    async with timeout_cm(rebuild_timeout_s):
                        entry = await rebuild_coro
                else:
                    entry = await asyncio.wait_for(rebuild_coro, timeout=rebuild_timeout_s)
            else:
                entry = await rebuild_coro
            operations.record_timing(timings_ms, "semantic_rebuild", stage_started)
            operations.log.info(
                "finished semantic rebuild core webspace=%s action=%s scenario=%s live_room_requested=%s publish_live_room=%s prefer_live_room=%s payload_only=%s semantic_ms=%.3f ydoc_timings=%s semantic_timings=%s",
                webspace_id,
                requested_action,
                target_scenario,
                bool(live_room_update_requested),
                bool(publish_live_room),
                bool(prefer_live_room),
                bool(payload_only_rebuild),
                float(timings_ms.get("semantic_rebuild") or 0.0),
                operations.copy_timing_map(getattr(runtime, "_last_rebuild_ydoc_timings_ms", None)),
                operations.copy_timing_map(getattr(runtime, "_last_rebuild_timings_ms", None)),
            )
        except operations.stale_request_error_type:
            finalized_timings = operations.finalize_timing_map(timings_ms, started_at=rebuild_started)
            semantic_timings = operations.copy_timing_map(getattr(runtime, "_last_rebuild_timings_ms", None))
            ydoc_timings = operations.copy_timing_map(getattr(runtime, "_last_rebuild_ydoc_timings_ms", None))
            resolver_debug = dict(getattr(runtime, "_last_resolver_debug", None) or {})
            apply_summary = dict(getattr(runtime, "_last_apply_summary", None) or {})
            phase_timings = operations.derive_phase_timings(
                switch_timings_ms=effective_switch_timings,
                rebuild_timings_ms=finalized_timings,
                semantic_rebuild_timings_ms=semantic_timings,
                switch_mode=effective_switch_mode,
            )
            operations.set_rebuild_status_if_current(
                webspace_id,
                request_id,
                status="cancelled",
                pending=False,
                finished_at=time.time(),
                error="stale_rebuild_superseded",
                switch_mode=effective_switch_mode,
                scenario_resolution=resolved_scenario_resolution,
                projection_refresh=projection_refresh,
                resolver=resolver_debug or None,
                apply_summary=apply_summary or None,
                timings_ms=finalized_timings,
                switch_timings_ms=effective_switch_timings,
                semantic_rebuild_timings_ms=semantic_timings,
                ydoc_timings_ms=ydoc_timings,
                phase_timings_ms=phase_timings,
            )
            operations.log.info(
                "stale semantic rebuild skipped apply webspace=%s action=%s scenario=%s request_id=%s",
                webspace_id,
                requested_action,
                target_scenario,
                request_id,
            )
            return {
                "ok": False,
                "accepted": False,
                "action": requested_action,
                "source_of_truth": source_of_truth,
                "webspace_id": webspace_id,
                "scenario_id": target_scenario,
                "scenario_resolution": resolved_scenario_resolution,
                "request_id": request_id,
                "switch_mode": effective_switch_mode,
                "projection_refresh": projection_refresh,
                "resolver": resolver_debug or None,
                "apply_summary": apply_summary or None,
                "timings_ms": finalized_timings,
                "switch_timings_ms": effective_switch_timings,
                "semantic_rebuild_timings_ms": semantic_timings,
                "ydoc_timings_ms": ydoc_timings,
                "phase_timings_ms": phase_timings,
                "error": "stale_rebuild_superseded",
            }
        except BaseException as exc:
            if operations.is_control_flow_base_exception(exc):
                raise
            error_token = "webspace_rebuild_timeout" if isinstance(exc, asyncio.TimeoutError) else "webspace_rebuild_failed"
            error_detail = f"{type(exc).__name__}: {exc}"[:1000]
            finalized_timings = operations.finalize_timing_map(timings_ms, started_at=rebuild_started)
            semantic_timings = operations.copy_timing_map(getattr(runtime, "_last_rebuild_timings_ms", None))
            ydoc_timings = operations.copy_timing_map(getattr(runtime, "_last_rebuild_ydoc_timings_ms", None))
            resolver_debug = dict(getattr(runtime, "_last_resolver_debug", None) or {})
            apply_summary = dict(getattr(runtime, "_last_apply_summary", None) or {})
            phase_timings = operations.derive_phase_timings(
                switch_timings_ms=effective_switch_timings,
                rebuild_timings_ms=finalized_timings,
                semantic_rebuild_timings_ms=semantic_timings,
                switch_mode=effective_switch_mode,
            )
            operations.set_rebuild_status_if_current(
                webspace_id,
                request_id,
                status="failed",
                pending=False,
                finished_at=time.time(),
                error=error_token,
                switch_mode=effective_switch_mode,
                scenario_resolution=resolved_scenario_resolution,
                projection_refresh=projection_refresh,
                resolver=resolver_debug or None,
                apply_summary=apply_summary or None,
                timings_ms=finalized_timings,
                switch_timings_ms=effective_switch_timings,
                semantic_rebuild_timings_ms=semantic_timings,
                ydoc_timings_ms=ydoc_timings,
                phase_timings_ms=phase_timings,
            )
            operations.log.warning(
                "failed to rebuild webspace from sources webspace=%s action=%s scenario=%s error=%s detail=%s timings_ms=%s semantic_timings_ms=%s",
                webspace_id,
                requested_action,
                target_scenario,
                error_token,
                error_detail,
                finalized_timings,
                semantic_timings,
                exc_info=True,
            )
            return {
                "ok": False,
                "accepted": False,
                "action": requested_action,
                "source_of_truth": source_of_truth,
                "webspace_id": webspace_id,
                "scenario_id": target_scenario,
                "scenario_resolution": resolved_scenario_resolution,
                "request_id": request_id,
                "switch_mode": effective_switch_mode,
                "projection_refresh": projection_refresh,
                "resolver": resolver_debug or None,
                "apply_summary": apply_summary or None,
                "timings_ms": finalized_timings,
                "switch_timings_ms": effective_switch_timings,
                "semantic_rebuild_timings_ms": semantic_timings,
                "ydoc_timings_ms": ydoc_timings,
                "phase_timings_ms": phase_timings,
                "error": error_token,
                "error_detail": error_detail,
            }

        semantic_timings = operations.copy_timing_map(getattr(runtime, "_last_rebuild_timings_ms", None))
        ydoc_timings = operations.copy_timing_map(getattr(runtime, "_last_rebuild_ydoc_timings_ms", None))
        resolver_debug = dict(getattr(runtime, "_last_resolver_debug", None) or {})
        apply_summary = dict(getattr(runtime, "_last_apply_summary", None) or {})
        worker_diagnostics = dict(getattr(runtime, "_last_worker_diagnostics", None) or {})
        raw_materialized_payload = getattr(runtime, "_last_materialized_payload", None)
        materialized_payload = (
            dict(raw_materialized_payload)
            if isinstance(raw_materialized_payload, Mapping)
            else None
        )
        live_room_refresh_result: dict[str, Any] | None = None

        should_refresh_live_room = (
            not publish_live_room
            and (
                scenario_switch_payload_rebuild
                or operations.refresh_live_room_after_rebuild_enabled()
            )
            and operations.rebuild_action_refreshes_live_room(requested_action)
        )
        force_full_state_update = bool(
            fresh_doc_rebuild
            and (
                ystore_reset
                or (requested_action == "builder_revision_apply" and payload_only_rebuild)
            )
        )
        if should_refresh_live_room:
            if operations.defer_live_room_refresh_for_rebuild(requested_action):
                persist_repair = not (
                    requested_action == "builder_revision_apply"
                    and builder_fresh_doc_rebuild
                    and operations.builder_revision_replace_ystore_snapshot_enabled()
                    and not payload_only_rebuild
                )
                deferred_refresh_kwargs: dict[str, Any] = {
                    "persist_repair": persist_repair,
                    "force_full_state_update": bool(force_full_state_update and persist_repair),
                }
                if materialized_payload:
                    deferred_refresh_kwargs["materialized_payload"] = materialized_payload
                    deferred_refresh_kwargs["materialization_identity"] = effective_materialization_identity
                live_room_refresh_result = operations.schedule_live_room_refresh(
                    webspace_id=webspace_id,
                    reason=f"semantic_rebuild:{requested_action}",
                    **deferred_refresh_kwargs,
                )
                timings_ms["live_room_refresh_deferred"] = 0.0
            else:
                stage_started = time.perf_counter()
                try:
                    operations.log.info(
                        "starting live-room refresh after semantic rebuild webspace=%s action=%s",
                        webspace_id,
                        requested_action,
                    )
                    if operations.rebuild_action_applies_live_payload(requested_action):
                        persist_repair = not (
                            requested_action == "builder_revision_apply"
                            and builder_fresh_doc_rebuild
                            and operations.builder_revision_replace_ystore_snapshot_enabled()
                            and not payload_only_rebuild
                        )
                        refresh_kwargs: dict[str, Any] = {
                            "reason": f"semantic_rebuild:{requested_action}",
                            "persist_repair": persist_repair,
                        }
                        if materialized_payload:
                            from adaos.services.yjs.gateway import apply_materialized_payload_to_live_room  # pylint: disable=import-outside-toplevel

                            refresh_kwargs["force_full_state_update"] = bool(
                                force_full_state_update and persist_repair
                            )
                            live_room_refresh_result = await apply_materialized_payload_to_live_room(
                                webspace_id,
                                materialized_payload=materialized_payload,
                                **refresh_kwargs,
                                materialization_identity=effective_materialization_identity,
                            )
                        else:
                            from adaos.services.yjs.gateway import reconcile_live_webspace_effective_branches  # pylint: disable=import-outside-toplevel

                            live_room_refresh_result = await reconcile_live_webspace_effective_branches(
                                webspace_id,
                                **refresh_kwargs,
                            )
                    else:
                        from adaos.services.yjs.gateway import reset_live_webspace_room  # pylint: disable=import-outside-toplevel

                        live_room_refresh_result = await reset_live_webspace_room(
                            webspace_id,
                            close_reason=f"semantic_rebuild:{requested_action}",
                        )
                    if not isinstance(live_room_refresh_result, Mapping):
                        live_room_refresh_result = {
                            "ok": live_room_refresh_result is not None,
                            "warning": "live_room_refresh_returned_non_mapping",
                            "result_type": type(live_room_refresh_result).__name__,
                        }
                    operations.log.info(
                        "finished live-room refresh after semantic rebuild webspace=%s action=%s summary=%s",
                        webspace_id,
                        requested_action,
                        json.dumps(
                            operations.compact_live_room_refresh_result_for_log(live_room_refresh_result),
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                    )
                except BaseException as exc:
                    if operations.is_control_flow_base_exception(exc):
                        raise
                    live_room_refresh_result = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    operations.log.warning(
                        "failed to refresh live YRoom after detached semantic rebuild webspace=%s action=%s",
                        webspace_id,
                        requested_action,
                        exc_info=True,
                    )
                operations.record_timing(timings_ms, "live_room_refresh", stage_started)

        if isinstance(live_room_refresh_result, Mapping):
            refresh_payload = live_room_refresh_result.get("materialized_payload")
            refresh_apply_summary = (
                refresh_payload.get("apply_summary")
                if isinstance(refresh_payload, Mapping) and isinstance(refresh_payload.get("apply_summary"), Mapping)
                else None
            )
            if isinstance(refresh_apply_summary, Mapping):
                apply_summary = dict(refresh_apply_summary)

        if not target_scenario or not resolved_scenario_resolution:
            stage_started = time.perf_counter()
            try:
                state_after, resolved_target_scenario, resolved_target_resolution = await operations.resolve_rebuild_scenario_target(
                    webspace_id,
                    target_scenario,
                    prefer_manifest_home_before_current=requested_action in {"reload", "reset"},
                )
                if not target_scenario:
                    target_scenario = resolved_target_scenario
                if not resolved_scenario_resolution:
                    resolved_scenario_resolution = resolved_target_resolution
            except Exception:
                target_scenario = target_scenario or None
                resolved_scenario_resolution = resolved_scenario_resolution or None
            operations.record_timing(timings_ms, "resolve_active_scenario", stage_started)

        workflow_sync_action = requested_action in {"scenario_switch_rebuild", "restore", "reload", "reset"}
        should_sync_workflow = operations.workflow_sync_for_rebuild_enabled(requested_action)
        workflow_sync_result: dict[str, Any] | None = None
        if target_scenario and should_sync_workflow:
            if operations.defer_workflow_sync_for_rebuild(requested_action):
                workflow_sync_result = operations.schedule_workflow_sync(
                    ctx,
                    webspace_id=webspace_id,
                    scenario_id=target_scenario,
                    reason=f"semantic_rebuild:{requested_action}",
                )
                timings_ms["workflow_sync_deferred"] = 0.0
            else:
                stage_started = time.perf_counter()
                try:
                    wf = operations.scenario_workflow_runtime_type(ctx)
                    await wf.sync_workflow_for_webspace(target_scenario, webspace_id)
                    workflow_sync_result = {
                        "scheduled": False,
                        "deferred": False,
                        "scenario_id": target_scenario,
                    }
                except BaseException as exc:
                    if operations.is_control_flow_base_exception(exc):
                        raise
                    workflow_sync_result = {
                        "scheduled": False,
                        "deferred": False,
                        "error": f"workflow_sync_failed:{type(exc).__name__}",
                        "scenario_id": target_scenario,
                    }
                    operations.log.warning(
                        "failed to sync workflow during semantic rebuild webspace=%s scenario=%s action=%s",
                        webspace_id,
                        target_scenario,
                        requested_action,
                        exc_info=True,
                    )
                operations.record_timing(timings_ms, "workflow_sync", stage_started)
        elif workflow_sync_action and target_scenario:
            workflow_sync_result = {
                "scheduled": False,
                "deferred": False,
                "skipped": True,
                "reason": "workflow_sync_disabled_for_scenario_switch"
                if requested_action == "scenario_switch_rebuild"
                else "workflow_sync_disabled",
                "scenario_id": target_scenario,
            }
            timings_ms["workflow_sync_skipped"] = 0.0
        elif workflow_sync_action:
            workflow_sync_result = {
                "scheduled": False,
                "deferred": False,
                "skipped": True,
                "reason": "scenario_unresolved",
            }

        event_topic = None
        if requested_action in {"reload", "reset"}:
            event_topic = "desktop.webspace.reloaded"
        elif requested_action == "restore":
            event_topic = "desktop.webspace.restored"
        if event_topic:
            stage_started = time.perf_counter()
            try:
                payload: dict[str, Any] = {
                    "webspace_id": webspace_id,
                    "action": requested_action,
                }
                if target_scenario:
                    payload["scenario_id"] = target_scenario
                if isinstance(event_payload, dict):
                    payload.update(event_payload)
                payload["webspace_id"] = webspace_id
                payload["action"] = requested_action
                if target_scenario:
                    payload["scenario_id"] = target_scenario
                payload["_event_type"] = event_topic
                payload.pop("recreate_room", None)
                operations.emit(ctx.bus, event_topic, payload, "scenario.webspace_runtime")
            except Exception:
                operations.log.debug("failed to operations.emit %s for webspace=%s", event_topic, webspace_id, exc_info=True)
            operations.record_timing(timings_ms, "event_emit", stage_started)

        finalized_timings = operations.finalize_timing_map(timings_ms, started_at=rebuild_started)
        phase_timings = operations.derive_phase_timings(
            switch_timings_ms=effective_switch_timings,
            rebuild_timings_ms=finalized_timings,
            semantic_rebuild_timings_ms=semantic_timings,
            switch_mode=effective_switch_mode,
        )
        final_rebuild_state = operations.describe_rebuild_state(webspace_id)
        final_materialization = operations.copy_materialization_snapshot(
            final_rebuild_state.get("materialization") if isinstance(final_rebuild_state, Mapping) else None
        )
        result = {
            "ok": True,
            "accepted": True,
            "action": requested_action,
            "source_of_truth": source_of_truth,
            "webspace_id": webspace_id,
            "scenario_id": target_scenario,
            "scenario_resolution": resolved_scenario_resolution,
            "request_id": request_id,
            "switch_mode": effective_switch_mode,
            "projection_refresh": projection_refresh,
            "registry_summary": {
                "scenario_id": str(getattr(entry, "scenario_id", target_scenario) or ""),
                "apps": len(getattr(entry, "apps", []) or []),
                "widgets": len(getattr(entry, "widgets", []) or []),
            },
            "resolver": resolver_debug or None,
            "apply_summary": apply_summary or None,
            "timings_ms": finalized_timings,
            "switch_timings_ms": effective_switch_timings,
            "semantic_rebuild_timings_ms": semantic_timings,
            "ydoc_timings_ms": ydoc_timings,
            "materialization_worker": worker_diagnostics or None,
            "phase_timings_ms": phase_timings,
            "materialization": final_materialization,
            "materialization_identity": effective_materialization_identity,
            "live_room_update_requested": bool(live_room_update_requested),
            "live_room_publish": bool(publish_live_room),
            "live_room_refresh": live_room_refresh_result,
            "workflow_sync": workflow_sync_result,
            "fresh_doc_rebuild": bool(fresh_doc_rebuild),
            "atomic_payload_rebuild": bool(scenario_switch_payload_rebuild),
            "force_full_state_update": bool(force_full_state_update),
            "payload_only_rebuild": bool(payload_only_rebuild),
        }
        if requested_action == "reset" or reset_room_result is not None:
            result["reset_room"] = reset_room_result or {
                "webspace_id": webspace_id,
                "room_dropped": False,
            }
            result["ystore_reset"] = bool(ystore_reset)
        operations.set_rebuild_status_if_current(
            webspace_id,
            request_id,
            status="ready",
            pending=False,
            finished_at=time.time(),
            error=None,
            switch_mode=effective_switch_mode,
            scenario_id=target_scenario,
            scenario_resolution=resolved_scenario_resolution,
            projection_refresh=projection_refresh,
            registry_summary=result.get("registry_summary"),
            resolver=resolver_debug or None,
            apply_summary=apply_summary or None,
            timings_ms=finalized_timings,
            switch_timings_ms=effective_switch_timings,
            semantic_rebuild_timings_ms=semantic_timings,
            ydoc_timings_ms=ydoc_timings,
            phase_timings_ms=phase_timings,
            materialization=final_materialization,
            materialized_payload=materialized_payload,
            live_room_update_requested=bool(live_room_update_requested),
            live_room_publish=bool(publish_live_room),
            live_room_refresh=live_room_refresh_result,
        )
        operations.log.info(
            "semantic rebuild completed webspace=%s action=%s scenario=%s timings_ms=%s semantic_timings_ms=%s",
            webspace_id,
            requested_action,
            target_scenario,
            finalized_timings,
            semantic_timings,
        )
        return result
