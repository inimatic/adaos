from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True, slots=True)
class WebspaceMaterializationOperations:
    build_materialization_snapshot_from_resolved: Any
    clone_json_like: Any
    coerce_dict: Any
    copy_timing_map: Any
    default_materialization_required_branches: Any
    describe_webspace_rebuild_state: Any
    effective_branch_paths: Any
    finalize_timing_map: Any
    get_cached_materialized_worker_result: Any
    get_ystore_for_webspace: Any
    materialization_worker_enabled: Any
    materialized_payload_inputs: Any
    normalize_materialization_required_branches: Any
    open_readonly_operational_ydoc: Any
    open_rebuild_ydoc_session: Any
    raise_if_rebuild_request_superseded: Any
    record_timing: Any
    remember_materialized_worker_result: Any
    resolved_outputs_from_cache_payload: Any
    run_materialization_cpu: Any
    run_materialization_worker: Any
    scenario_materialization_contract: Any
    schedule_builder_ystore_snapshot_backup: Any
    set_map_value_if_changed: Any
    set_webspace_rebuild_status_if_current: Any
    ystore_write_metadata: Any


class WebspaceMaterializationService:
    async def resolve_payload(
        self,
        runtime: Any,
        operations: WebspaceMaterializationOperations,
        webspace_id: str,
        *,
        request_id: str | None = None,
        scenario_id: str | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
        isolate_process: bool | None = None,
        skill_decls_snapshot: Any = None,
        skill_decls_fingerprint: str | None = None,
        scenario_content_override: Mapping[str, Any] | None = None,
        skill_source_mode: str | None = None,
    ) -> Any:
        """Resolve a materialized payload without mutating an intermediate YDoc."""
        materialize_started = time.perf_counter()
        timings: Dict[str, float] = {}
        ydoc_timings: Dict[str, float] = {"payload_only": 0.0}
        runtime._last_resolver_debug = None
        runtime._last_collect_inputs_timings_ms = None
        runtime._last_apply_summary = None
        runtime._last_apply_phase_timings_ms = None
        runtime._last_materialized_payload = None
        runtime._last_rebuild_ydoc_timings_ms = None
        runtime._last_rebuild_snapshot_update = None
        runtime._last_rebuild_state_vector = None
        runtime._last_worker_diagnostics = None

        use_process_worker = (
            operations.materialization_worker_enabled()
            and isolate_process is not False
            and not scenario_content_override
            and not skill_source_mode
        )
        if use_process_worker:
            stage_started = time.perf_counter()
            worker_result = operations.get_cached_materialized_worker_result(
                materialization_identity,
                cache_mode="payload_only",
                require_snapshot=False,
            )
            if worker_result is not None:
                operations.record_timing(ydoc_timings, "materialization_cache_lookup", stage_started)
                ydoc_timings["materialization_cache_hit"] = 0.0
            else:
                prepared_skill_decls = skill_decls_snapshot
                prepared_skill_fingerprint = str(skill_decls_fingerprint or "").strip()
                if prepared_skill_decls is None:
                    prepare_started = time.perf_counter()
                    prepared_skill_decls, prepared_skill_fingerprint = await operations.run_materialization_cpu(
                        runtime._prepare_materialization_skill_decls_sync,
                        webspace_id,
                        skill_source_mode,
                    )
                    operations.record_timing(ydoc_timings, "prepare_skill_decls", prepare_started)
                worker_result = await operations.run_materialization_worker(
                    webspace_id,
                    mode="payload_only",
                    request_id=request_id,
                    scenario_id=scenario_id,
                    materialization_identity=materialization_identity,
                    skill_decls_snapshot=prepared_skill_decls,
                    skill_decls_fingerprint=prepared_skill_fingerprint,
                )
                operations.record_timing(ydoc_timings, "payload_worker", stage_started)
                ydoc_timings["materialization_cache_miss"] = 0.0
                operations.remember_materialized_worker_result(
                    materialization_identity,
                    worker_result,
                    cache_mode="payload_only",
                    require_snapshot=False,
                )
            operations.raise_if_rebuild_request_superseded(webspace_id, request_id)
            payload = worker_result.get("materialized_payload")
            if not isinstance(payload, Mapping):
                raise RuntimeError("materialization_worker_missing_payload")
            runtime._last_materialized_payload = dict(payload)
            runtime._last_rebuild_timings_ms = operations.copy_timing_map(
                worker_result.get("rebuild_timings_ms")
            )
            runtime._last_resolver_debug = dict(worker_result.get("resolver_debug") or {})
            runtime._last_apply_summary = dict(worker_result.get("apply_summary") or {})
            runtime._last_apply_phase_timings_ms = operations.copy_timing_map(
                worker_result.get("apply_phase_timings_ms")
            )
            runtime._last_worker_diagnostics = {
                "mode": "payload_only",
                "elapsed_ms": worker_result.get("worker_parent_elapsed_ms"),
                "child_elapsed_ms": worker_result.get("worker_elapsed_ms"),
                "init_ms": worker_result.get("worker_init_ms"),
                "materialize_ms": worker_result.get("worker_materialize_ms"),
                "peak_rss_bytes": worker_result.get("worker_peak_rss_bytes"),
                "result_bytes": worker_result.get("worker_result_bytes"),
                "materialization_cache": dict(worker_result.get("materialization_cache") or {})
                if isinstance(worker_result.get("materialization_cache"), Mapping)
                else None,
            }
            worker_ydoc_timings = operations.copy_timing_map(worker_result.get("ydoc_timings_ms")) or {}
            worker_ydoc_timings["worker_process"] = round(
                float(worker_result.get("worker_parent_elapsed_ms") or 0.0),
                3,
            )
            runtime._last_rebuild_ydoc_timings_ms = operations.finalize_timing_map(
                worker_ydoc_timings,
                started_at=materialize_started,
            )
            resolved = operations.resolved_outputs_from_cache_payload(payload)
            inputs = operations.materialized_payload_inputs(
                webspace_id,
                payload,
                resolved,
                materialization_identity=materialization_identity,
            )
            materialization_contract = operations.coerce_dict(inputs.metadata.get("materialization") or {})
            materialization_snapshot = operations.build_materialization_snapshot_from_resolved(
                webspace_id=webspace_id,
                resolved=resolved,
                compatibility_presence=inputs.compatibility_cache_presence,
                rebuild_state=operations.describe_webspace_rebuild_state(webspace_id),
                required_branches=operations.normalize_materialization_required_branches(materialization_contract)
                or list(operations.default_materialization_required_branches),
                snapshot_source="semantic_rebuild:payload_worker",
                phase_name="complete",
                stale=False,
            )
            if str(request_id or "").strip():
                operations.set_webspace_rebuild_status_if_current(
                    webspace_id,
                    request_id,
                    materialization=materialization_snapshot,
                )
            return resolved.to_registry_entry()

        prepared_skill_decls = skill_decls_snapshot
        prepared_skill_fingerprint = str(skill_decls_fingerprint or "").strip()
        if prepared_skill_decls is None:
            stage_started = time.perf_counter()
            prepared_skill_decls, prepared_skill_fingerprint = await operations.run_materialization_cpu(
                runtime._prepare_materialization_skill_decls_sync,
                webspace_id,
                skill_source_mode,
            )
            operations.record_timing(timings, "prepare_skill_decls", stage_started)

        operational_doc_started = time.perf_counter()
        operational_doc_close_started = operational_doc_started
        async with operations.open_readonly_operational_ydoc(webspace_id) as ydoc:
            operations.record_timing(timings, "open_operational_doc", operational_doc_started)
            operations.raise_if_rebuild_request_superseded(webspace_id, request_id)
            stage_started = time.perf_counter()
            inputs = runtime._collect_resolver_inputs_in_doc(
                ydoc,
                webspace_id,
                materialization_identity=materialization_identity,
                scenario_id_override=scenario_id,
                skill_decls_override=prepared_skill_decls,
                skill_decls_fingerprint_override=prepared_skill_fingerprint,
                scenario_content_override=scenario_content_override,
            )
            operations.record_timing(timings, "collect_inputs", stage_started)
            collect_phase_timings = operations.copy_timing_map(runtime._last_collect_inputs_timings_ms) or {}
            timings.update(collect_phase_timings)

            resolved, payload, worker_timings = await operations.run_materialization_cpu(
                runtime._resolve_materialized_payload_from_inputs_sync,
                inputs,
            )
            timings.update(worker_timings)
            operational_doc_close_started = time.perf_counter()
        operations.record_timing(timings, "close_operational_doc", operational_doc_close_started)

        operations.raise_if_rebuild_request_superseded(webspace_id, request_id)
        runtime._last_materialized_payload = payload
        materialization_contract = operations.coerce_dict(inputs.metadata.get("materialization") or {})
        if not materialization_contract:
            materialization_contract = operations.scenario_materialization_contract(
                resolved.scenario_id,
                source_mode=resolved.source_mode,
            )
        materialization_snapshot = operations.build_materialization_snapshot_from_resolved(
            webspace_id=webspace_id,
            resolved=resolved,
            compatibility_presence=inputs.compatibility_cache_presence,
            rebuild_state=operations.describe_webspace_rebuild_state(webspace_id),
            required_branches=operations.normalize_materialization_required_branches(materialization_contract)
            or list(operations.default_materialization_required_branches),
            snapshot_source="semantic_rebuild:payload_only",
            phase_name="complete",
            stale=False,
        )
        if str(request_id or "").strip():
            operations.set_webspace_rebuild_status_if_current(
                webspace_id,
                request_id,
                materialization=materialization_snapshot,
            )

        stage_started = time.perf_counter()
        entry = resolved.to_registry_entry()
        operations.record_timing(timings, "to_registry_entry", stage_started)
        runtime._last_rebuild_timings_ms = operations.finalize_timing_map(timings, started_at=materialize_started)
        runtime._last_apply_summary = {
            "branch_count": len(operations.effective_branch_paths),
            "changed_branches": 0,
            "unchanged_branches": 0,
            "failed_branches": 0,
            "payload_only": True,
        }
        runtime._last_apply_phase_timings_ms = None
        runtime._last_rebuild_ydoc_timings_ms = operations.finalize_timing_map(ydoc_timings, started_at=materialize_started)
        return entry


    async def rebuild(
        self,
        runtime: Any,
        operations: WebspaceMaterializationOperations,
        webspace_id: str,
        *,
        request_id: str | None = None,
        publish_live_room: bool = True,
        prefer_live_room: bool | None = None,
        initial_scenario_id: str | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
        fresh_doc: bool = False,
        replace_ystore_snapshot: bool = False,
    ) -> Any:
        """
        Async counterpart of :meth:`compute_registry_for_webspace` for use
        inside running event loops.
        """
        rebuild_started = time.perf_counter()
        ydoc_timings: Dict[str, float] = {}
        runtime._last_rebuild_ydoc_timings_ms = None
        runtime._last_rebuild_snapshot_update = None
        runtime._last_rebuild_state_vector = None
        runtime._last_materialized_payload = None
        runtime._last_worker_diagnostics = None
        use_live_room = bool(publish_live_room) if prefer_live_room is None else bool(prefer_live_room)
        try:
            if fresh_doc:
                ystore = operations.get_ystore_for_webspace(webspace_id) if replace_ystore_snapshot else None
                if ystore is not None:
                    stage_started = time.perf_counter()
                    await ystore.start()
                    operations.record_timing(ydoc_timings, "ystore_start", stage_started)
                else:
                    ydoc_timings["ystore_start"] = 0.0
                try:
                    stage_started = time.perf_counter()
                    worker_result = operations.get_cached_materialized_worker_result(materialization_identity)
                    if worker_result is not None:
                        operations.record_timing(ydoc_timings, "materialization_cache_lookup", stage_started)
                        ydoc_timings["fresh_doc_worker"] = 0.0
                        ydoc_timings["materialization_cache_hit"] = 0.0
                    else:
                        if operations.materialization_worker_enabled():
                            prepare_started = time.perf_counter()
                            prepared_skill_decls, prepared_skill_fingerprint = await operations.run_materialization_cpu(
                                runtime._prepare_materialization_skill_decls_sync,
                                webspace_id,
                            )
                            operations.record_timing(ydoc_timings, "prepare_skill_decls", prepare_started)
                            worker_result = await operations.run_materialization_worker(
                                webspace_id,
                                mode="fresh_doc",
                                request_id=request_id,
                                scenario_id=initial_scenario_id,
                                materialization_identity=materialization_identity,
                                skill_decls_snapshot=prepared_skill_decls,
                                skill_decls_fingerprint=prepared_skill_fingerprint,
                            )
                        else:
                            worker_result = await operations.run_materialization_cpu(
                                runtime._rebuild_fresh_doc_snapshot_sync,
                                webspace_id,
                                request_id=request_id,
                                initial_scenario_id=initial_scenario_id,
                                materialization_identity=materialization_identity,
                            )
                        operations.record_timing(ydoc_timings, "fresh_doc_worker", stage_started)
                        ydoc_timings["materialization_cache_miss"] = 0.0
                        operations.remember_materialized_worker_result(materialization_identity, worker_result)
                    operations.raise_if_rebuild_request_superseded(webspace_id, request_id)
                    entry = worker_result["entry"]
                    snapshot_update = bytes(worker_result.get("snapshot_update") or b"")
                    state_vector = bytes(worker_result.get("state_vector") or b"")
                    runtime._last_rebuild_snapshot_update = snapshot_update
                    runtime._last_rebuild_state_vector = state_vector
                    materialized_payload = worker_result.get("materialized_payload")
                    runtime._last_materialized_payload = (
                        operations.clone_json_like(materialized_payload)
                        if isinstance(materialized_payload, Mapping)
                        else None
                    )
                    worker_ydoc_timings = operations.copy_timing_map(worker_result.get("ydoc_timings_ms")) or {}
                    for timing_key, timing_value in worker_ydoc_timings.items():
                        if timing_key == "total":
                            continue
                        ydoc_timings[timing_key] = timing_value
                    if worker_result.get("worker_parent_elapsed_ms") is not None:
                        ydoc_timings["worker_process"] = round(
                            float(worker_result.get("worker_parent_elapsed_ms") or 0.0),
                            3,
                        )
                        runtime._last_worker_diagnostics = {
                            "mode": "fresh_doc",
                            "elapsed_ms": worker_result.get("worker_parent_elapsed_ms"),
                            "child_elapsed_ms": worker_result.get("worker_elapsed_ms"),
                            "init_ms": worker_result.get("worker_init_ms"),
                            "materialize_ms": worker_result.get("worker_materialize_ms"),
                            "peak_rss_bytes": worker_result.get("worker_peak_rss_bytes"),
                            "result_bytes": worker_result.get("worker_result_bytes"),
                        }
                    runtime._last_rebuild_timings_ms = operations.copy_timing_map(worker_result.get("rebuild_timings_ms"))
                    runtime._last_resolver_debug = dict(worker_result.get("resolver_debug") or {})
                    runtime._last_apply_summary = dict(worker_result.get("apply_summary") or {})
                    runtime._last_apply_phase_timings_ms = operations.copy_timing_map(
                        worker_result.get("apply_phase_timings_ms")
                    )
                    if ystore is not None:
                        stage_started = time.perf_counter()
                        async with operations.ystore_write_metadata(
                            root_names=["ui", "data", "registry", "runtime"],
                            source="webspace_runtime.rebuild_async.replace_snapshot",
                            owner="core:webspace_runtime",
                            channel="core.webspace_runtime.snapshot_replace",
                            governed=True,
                        ):
                            replace_result = await ystore.replace_snapshot_update(
                                snapshot_update,
                                state_vector=state_vector,
                                backup_kind="builder_revision_apply_snapshot_replace",
                                persist_snapshot=False,
                                notify=False,
                            )
                        operations.record_timing(ydoc_timings, "ystore_replace_snapshot", stage_started)
                        backup_schedule = operations.schedule_builder_ystore_snapshot_backup(
                            webspace_id,
                            reason="builder_revision_apply_snapshot_replace_deferred",
                        )
                        ydoc_timings["ystore_backup_deferred"] = 0.0 if backup_schedule.get("scheduled") else -1.0
                        if isinstance(replace_result, Mapping):
                            try:
                                ydoc_timings["ystore_replace_persist"] = round(
                                    float(replace_result.get("persist_ms") or 0.0),
                                    3,
                                )
                            except Exception:
                                pass
                            try:
                                ydoc_timings["ystore_replace_notify"] = round(
                                    float(replace_result.get("notify_ms") or 0.0),
                                    3,
                                )
                            except Exception:
                                pass
                    else:
                        ydoc_timings["ystore_replace_snapshot"] = 0.0
                    ydoc_timings["encode_diff"] = 0.0
                    ydoc_timings["ystore_write_update"] = 0.0
                    ydoc_timings["room_update"] = 0.0
                    return entry
                finally:
                    if ystore is not None:
                        stage_started = time.perf_counter()
                        try:
                            ystore.stop()
                        except Exception:
                            pass
                        operations.record_timing(ydoc_timings, "ystore_stop", stage_started)
            async with operations.open_rebuild_ydoc_session(
                webspace_id,
                timings=ydoc_timings,
                publish_live_room=publish_live_room,
                prefer_live_room=use_live_room,
            ) as ydoc:
                seed_scenario = str(initial_scenario_id or "").strip()
                if seed_scenario:
                    stage_started = time.perf_counter()
                    ui_map = ydoc.get_map("ui")
                    with ydoc.begin_transaction() as txn:
                        operations.set_map_value_if_changed(ui_map, txn, "current_scenario", seed_scenario)
                    operations.record_timing(ydoc_timings, "seed_initial_scenario", stage_started)
                stage_started = time.perf_counter()
                entry = runtime._rebuild_in_doc(
                    ydoc,
                    webspace_id,
                    expected_request_id=request_id,
                    materialization_identity=materialization_identity,
                )
                operations.record_timing(ydoc_timings, "in_doc_rebuild", stage_started)
                return entry
        finally:
            runtime._last_rebuild_ydoc_timings_ms = operations.finalize_timing_map(ydoc_timings, started_at=rebuild_started)

