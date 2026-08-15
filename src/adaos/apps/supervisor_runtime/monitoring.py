from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SupervisorMonitoringOperations:
    active_slot: Any
    logger: Any
    realtime_sidecar_enabled: Any
    realtime_sidecar_listener_snapshot: Any
    restart_realtime_sidecar_subprocess: Any
    sidecar_code_change_debounce_sec: Any


class SupervisorMonitoringService:
    async def run_iteration_loop(
        self,
        manager: Any,
        operations: SupervisorMonitoringOperations,
    ) -> None:
        while True:
            await asyncio.sleep(1.0)
            manager._monitor_last_iteration_at = time.time()
            reconnect_hub_root_after_sidecar_restart = False
            sidecar_proc = manager._sidecar_proc
            if sidecar_proc is not None and sidecar_proc.poll() is not None:
                manager._sidecar_last_restart_reason = "supervisor.sidecar.exited"
                manager._process_supervisor.track_sidecar(None)
                manager._persist_runtime_state()
            if operations.realtime_sidecar_enabled(role=manager._sidecar_role()) and not manager._stopping:
                sync_result = manager._sync_sidecar_controlled_files_from_validated_slot()
                if bool(sync_result.get("changed")):
                    manager._persist_runtime_state()
                sidecar_snapshot = operations.realtime_sidecar_listener_snapshot(manager._sidecar_proc, role=manager._sidecar_role())
                code_state = manager._sidecar_code_state()
                current_fingerprint = str(code_state.get("fingerprint") or "").strip() or None
                code_changed = bool(
                    current_fingerprint
                    and manager._sidecar_code_fingerprint
                    and current_fingerprint != manager._sidecar_code_fingerprint
                )
                code_change_ready = False
                if code_changed:
                    if current_fingerprint != manager._sidecar_code_change_pending_fingerprint:
                        manager._sidecar_code_change_pending_fingerprint = current_fingerprint
                        manager._sidecar_code_change_pending_since = time.time()
                    elif manager._sidecar_code_change_pending_since is not None:
                        code_change_ready = (
                            time.time() - float(manager._sidecar_code_change_pending_since)
                            >= operations.sidecar_code_change_debounce_sec()
                        )
                else:
                    manager._sidecar_code_change_pending_fingerprint = None
                    manager._sidecar_code_change_pending_since = None
                sidecar_ready = await manager._probe_sidecar_health()
                should_restart_sidecar = False
                restart_reason = None
                if manager._sidecar_proc is None and not bool(sidecar_snapshot.get("listener_running")):
                    should_restart_sidecar = True
                    restart_reason = "supervisor.sidecar.missing"
                elif sidecar_ready is False and manager._sidecar_consecutive_probe_failures >= 2:
                    should_restart_sidecar = True
                    restart_reason = "supervisor.sidecar.unhealthy"
                elif code_changed and code_change_ready:
                    upgrade_allowed, upgrade_blocked_reason = await manager._sidecar_code_upgrade_restart_allowed()
                    if upgrade_allowed:
                        should_restart_sidecar = True
                        restart_reason = "supervisor.sidecar.code_upgrade"
                    elif manager._sidecar_last_restart_reason != upgrade_blocked_reason:
                        manager._sidecar_last_restart_reason = upgrade_blocked_reason
                        manager._persist_runtime_state()
                if should_restart_sidecar:
                    allowed, blocked_reason = manager._sidecar_restart_allowed()
                    if not allowed:
                        manager._sidecar_last_restart_reason = blocked_reason
                        manager._persist_runtime_state()
                        should_restart_sidecar = False
                        await manager._maybe_resume_or_continue_transition()
                        candidate_proc = manager._candidate_proc
                        if candidate_proc is not None:
                            candidate_rc = candidate_proc.poll()
                            if candidate_rc is not None:
                                manager._candidate_last_stop_reason = manager._candidate_last_stop_reason or "supervisor.candidate.exited"
                                manager._process_supervisor.track_candidate(None)
                                manager._candidate_slot = None
                                manager._candidate_runtime_instance_id = None
                                manager._candidate_transition_role = None
                                manager._candidate_runtime_cwd = None
                                manager._persist_runtime_state()
                        proc = manager._proc
                        if proc is None:
                            manager._runtime_unhealthy_since = None
                            manager._runtime_unhealthy_kind = None
                            if manager._desired_running and not manager._stopping:
                                async with manager._lock:
                                    if manager._proc is None and manager._desired_running and not manager._stopping:
                                        await manager._spawn_runtime_locked(
                                            reason="supervisor.monitor.ensure_running",
                                            adopt_existing=True,
                                        )
                            continue
                    if should_restart_sidecar:
                        try:
                            async with manager._lock:
                                if manager._stopping:
                                    pass
                                elif manager._sidecar_proc is None and restart_reason == "supervisor.sidecar.missing":
                                    manager._sidecar_last_restart_reason = restart_reason
                                    await manager._spawn_sidecar_locked(reason=restart_reason)
                                    reconnect_hub_root_after_sidecar_restart = True
                                else:
                                    manager._sidecar_last_restart_reason = str(restart_reason or "supervisor.sidecar.restart")
                                    new_proc, restart_result = await operations.restart_realtime_sidecar_subprocess(
                                        proc=manager._sidecar_proc,
                                        role=manager._sidecar_role(),
                                        repo_root=str(manager._sidecar_repo_root() or "").strip() or None,
                                    )
                                    manager._process_supervisor.track_sidecar(new_proc)
                                    manager._sidecar_launch_cwd = str(code_state.get("repo_root") or manager._sidecar_launch_cwd or "") or None
                                    manager._sidecar_code_fingerprint = current_fingerprint
                                    manager._sidecar_code_fingerprint_updated_at = time.time() if current_fingerprint else None
                                    manager._sidecar_code_change_pending_fingerprint = None
                                    manager._sidecar_code_change_pending_since = None
                                    manager._sidecar_last_start_reason = str(restart_reason or "supervisor.sidecar.restart")
                                    manager._sidecar_last_restart_reason = str(restart_reason or restart_result.get("reason") or "restarted")
                                    manager._sidecar_last_probe_at = None
                                    manager._sidecar_last_probe_ok = None
                                    manager._sidecar_last_probe_error = None
                                    manager._sidecar_consecutive_probe_failures = 0
                                    manager._record_sidecar_restart_attempt(reason=manager._sidecar_last_restart_reason)
                                    manager._persist_runtime_state()
                                    reconnect_hub_root_after_sidecar_restart = True
                        except Exception:
                            operations.logger.warning("failed to restart adaos-realtime sidecar", exc_info=True)
                if reconnect_hub_root_after_sidecar_restart:
                    reconnect_result = await manager._reconnect_hub_root_after_sidecar_restart()
                    if isinstance(reconnect_result, dict) and not bool(reconnect_result.get("ok")):
                        operations.logger.warning(
                            "failed to reconnect hub-root after sidecar restart: %s",
                            reconnect_result.get("error") or reconnect_result,
                        )
            await manager._maybe_resume_or_continue_transition()
            candidate_proc = manager._candidate_proc
            if candidate_proc is not None:
                candidate_rc = candidate_proc.poll()
                if candidate_rc is not None:
                    manager._candidate_last_stop_reason = manager._candidate_last_stop_reason or "supervisor.candidate.exited"
                    manager._process_supervisor.track_candidate(None)
                    manager._candidate_slot = None
                    manager._candidate_runtime_instance_id = None
                    manager._candidate_transition_role = None
                    manager._candidate_runtime_cwd = None
                    manager._persist_runtime_state()
            proc = manager._proc
            if proc is None:
                manager._runtime_unhealthy_since = None
                manager._runtime_unhealthy_kind = None
                if manager._desired_running and not manager._stopping:
                    async with manager._lock:
                        if manager._proc is None and manager._desired_running and not manager._stopping:
                            await manager._spawn_runtime_locked(
                                reason="supervisor.monitor.ensure_running",
                                adopt_existing=True,
                            )
                continue
            rc = proc.poll()
            if rc is None:
                with contextlib.suppress(Exception):
                    manager._sample_memory_telemetry()
                critical_memory_decision = manager._memory_critical_restart_decision()
                if critical_memory_decision is not None:
                    with contextlib.suppress(Exception):
                        critical_memory_decision["pre_restart_evidence"] = manager._capture_runtime_stop_evidence(
                            reason=str(critical_memory_decision.get("reason") or "supervisor.memory.critical_pressure"),
                            stage="memory_critical_restart",
                            decision=dict(critical_memory_decision),
                        )
                    manager._last_error = str(
                        critical_memory_decision.get("message") or "runtime restart requested due to critical memory pressure"
                    )
                    manager._memory_critical_restart_last_at = time.time()
                    manager._persist_runtime_state()
                    try:
                        await manager.restart_runtime(
                            reason=str(critical_memory_decision.get("reason") or "supervisor.memory.critical_pressure")
                        )
                    except Exception:
                        operations.logger.warning("failed to self-heal critical memory pressure", exc_info=True)
                    continue
                try:
                    await manager._maybe_apply_memory_profile_mode()
                except Exception as exc:
                    operations.logger.warning("failed to apply requested memory profile mode", exc_info=True)
                    if str(manager._memory_active_session_id or "").strip() and manager._desired_memory_profile_mode() != "normal":
                        manager._fail_active_memory_session(
                            reason=f"requested_profile_mode_apply_error.{self._desired_memory_profile_mode()}",
                            stage="profile_apply_error",
                            details={
                                "error": f"{type(exc).__name__}: {exc}",
                                "active_slot": str(operations.active_slot() or "").strip().upper() or None,
                                "runtime_instance_id": manager._managed_runtime_instance_id,
                                "transition_role": manager._managed_transition_role,
                            },
                        )
                try:
                    manager._expire_stuck_requested_memory_profile()
                except Exception:
                    operations.logger.warning("failed to expire stuck requested memory profile session", exc_info=True)
                finalize_profile = manager._should_finalize_active_memory_profile()
                if finalize_profile is not None:
                    try:
                        finalize_trigger_source = str(finalize_profile.get("trigger_source") or "").strip().lower() or None
                        manager.stop_memory_profile(
                            str(finalize_profile.get("session_id") or ""),
                            reason=str(finalize_profile.get("reason") or "supervisor.memory.profile_window_complete"),
                        )
                        if finalize_trigger_source:
                            manager._memory_profile_current_trigger_source = finalize_trigger_source
                        allowed, block_reason = manager._memory_profile_restart_guard(desired_mode="normal")
                        if not allowed:
                            manager._record_memory_auto_profile_block(block_reason)
                            manager._persist_runtime_state()
                            continue
                        await manager.restart_runtime(
                            reason=f"supervisor.memory.complete_profile_mode.{str(finalize_profile.get('profile_mode') or 'profile')}"
                        )
                    except Exception:
                        operations.logger.warning("failed to finalize active memory profile session", exc_info=True)
                    continue
                try:
                    await manager._maybe_maintain_required_upstream_link()
                except Exception:
                    operations.logger.warning("required-upstream-link supervisor watchdog failed", exc_info=True)
                if await manager._maybe_self_heal_runtime():
                    continue
                continue
            manager._last_exit_code = int(rc)
            manager._last_exit_at = time.time()
            manager._last_stop_reason = manager._last_stop_reason or "supervisor.runtime.exited"
            if manager._memory_profile_mode != "normal" and not manager._stopping and manager._desired_running:
                manager._fail_active_memory_session(
                    reason="runtime_exited_during_profile_mode",
                    exit_code=int(rc),
                )
            manager._process_supervisor.track_active(None)
            manager._managed_runtime_instance_id = None
            manager._managed_transition_role = None
            manager._managed_slot = None
            manager._managed_runtime_port = None
            manager._managed_runtime_base_url = None
            manager._managed_runtime_cwd = None
            manager._runtime_unhealthy_since = None
            manager._runtime_unhealthy_kind = None
            manager._memory_profile_mode = "normal"
            manager._memory_profile_current_trigger_source = None
            manager._persist_runtime_state()
            if manager._stopping or not manager._desired_running:
                continue
            async with manager._lock:
                if manager._proc is None and manager._desired_running and not manager._stopping:
                    await asyncio.sleep(1.0)
                    await manager._spawn_runtime_locked(
                        reason="supervisor.monitor.respawn_after_exit",
                        adopt_existing=True,
                    )

