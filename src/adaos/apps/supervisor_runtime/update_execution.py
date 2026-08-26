from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PREPARE_HEARTBEAT_SEC = 15.0


@dataclass(frozen=True, slots=True)
class SupervisorUpdateExecutionOperations:
    build_attempt_payload: Any
    complete_update_attempt: Any
    revoke_prepare_lease: Any
    warm_switch_cold_fallback_enabled: Any
    warm_switch_defer_sec: Any
    warm_switch_enabled: Any
    warm_switch_max_deferrals: Any
    warm_switch_strict_cutover_enabled: Any
    write_prepare_lease: Any
    write_update_attempt: Any
    activate_slot: Any
    choose_inactive_slot: Any
    clear_core_update_plan: Any
    prepare_pending_update: Any
    remove_inactive_slot: Any
    write_core_update_plan: Any
    write_core_update_status: Any


class SupervisorUpdateExecution:
    async def countdown(
        self,
        manager: Any,
        operations: SupervisorUpdateExecutionOperations,
        *,
        action: str,
        target_rev: str,
        target_version: str,
        reason: str,
        countdown_sec: float,
        drain_timeout_sec: float,
        signal_delay_sec: float,
        prepare_lease_path: str = "",
        prepare_lease_token: str = "",
        prepare_timeout_sec: float | None = None,
    ) -> None:
        started_at = time.time()
        operations.write_core_update_status(
            {
                "state": "countdown",
                "phase": "countdown",
                "action": action,
                "target_rev": target_rev,
                "target_version": target_version,
                "reason": reason,
                "countdown_sec": countdown_sec,
                "drain_timeout_sec": drain_timeout_sec,
                "signal_delay_sec": signal_delay_sec,
                "started_at": started_at,
                "scheduled_for": started_at + countdown_sec,
            }
        )
        try:
            await asyncio.sleep(max(0.0, float(countdown_sec)))
            plan = {
                "state": "pending_restart",
                "action": action,
                "target_rev": target_rev,
                "target_version": target_version,
                "reason": reason,
                "created_at": time.time(),
                "expires_at": time.time() + 1800.0,
            }
            operations.write_core_update_plan(plan)
            operations.write_core_update_status(
                {
                    "state": "restarting",
                    "phase": "shutdown",
                    "action": action,
                    "target_rev": target_rev,
                    "target_version": target_version,
                    "reason": reason,
                    "drain_timeout_sec": drain_timeout_sec,
                    "signal_delay_sec": signal_delay_sec,
                    "message": "countdown completed; pending update written",
                }
            )
            shutdown_request_error: Exception | None = None
            try:
                await manager._request_runtime_shutdown(
                    reason=reason,
                    drain_timeout_sec=drain_timeout_sec,
                    signal_delay_sec=signal_delay_sec,
                )
            except Exception as exc:
                shutdown_request_error = exc
            stop_result = await manager._ensure_runtime_stopped_for_update(
                drain_timeout_sec=drain_timeout_sec,
                signal_delay_sec=signal_delay_sec,
                reason=reason,
            )
            if shutdown_request_error or bool(stop_result.get("forced")):
                pending_exit = bool(stop_result.get("pending_exit"))
                operations.write_core_update_status(
                    {
                        "state": "restarting",
                        "phase": "shutdown",
                        "action": action,
                        "target_rev": target_rev,
                        "target_version": target_version,
                        "reason": reason,
                        "drain_timeout_sec": drain_timeout_sec,
                        "signal_delay_sec": signal_delay_sec,
                        "message": (
                            "runtime remains in kernel shutdown; durable update plan retained until process exit"
                            if pending_exit
                            else
                            "runtime shutdown API was unavailable; supervisor continued with direct process stop"
                            if shutdown_request_error and bool(stop_result.get("forced"))
                            else "runtime shutdown API response was unavailable; runtime still stopped during grace window"
                            if shutdown_request_error
                            else "runtime shutdown exceeded grace period; supervisor forced process stop"
                        ),
                        "forced_shutdown": bool(stop_result.get("forced")),
                        "runtime_exit_pending": pending_exit,
                        "shutdown_request_error_type": (
                            type(shutdown_request_error).__name__ if shutdown_request_error is not None else None
                        ),
                        "shutdown_request_error": str(shutdown_request_error) if shutdown_request_error is not None else None,
                    }
                )
        except asyncio.CancelledError:
            manager._release_skill_runtime_migration_gate(reason="transition_cancelled")
            operations.clear_core_update_plan()
            cancel_mode = str(manager._update_task_cancel_mode or "").strip().lower()
            manager._update_task_cancel_mode = None
            if cancel_mode != "rescheduled":
                status = operations.write_core_update_status(
                    {
                        "state": "cancelled",
                        "phase": "countdown",
                        "action": action,
                        "target_rev": target_rev,
                        "target_version": target_version,
                        "reason": reason,
                        "drain_timeout_sec": drain_timeout_sec,
                        "signal_delay_sec": signal_delay_sec,
                        "message": "core update cancelled",
                    }
                )
                operations.complete_update_attempt(state="cancelled", status=status, reason=reason)
            raise
        except Exception as exc:
            manager._release_skill_runtime_migration_gate(reason=f"transition_failed:{type(exc).__name__}")
            operations.clear_core_update_plan()
            status = operations.write_core_update_status(
                {
                    "state": "failed",
                    "phase": "shutdown",
                    "action": action,
                    "target_rev": target_rev,
                    "target_version": target_version,
                    "reason": reason,
                    "drain_timeout_sec": drain_timeout_sec,
                    "signal_delay_sec": signal_delay_sec,
                    "message": "failed to request runtime shutdown for pending core update",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "updated_at": time.time(),
                }
            )
            operations.complete_update_attempt(
                state="failed",
                status=status,
                reason=f"shutdown request failed: {type(exc).__name__}",
            )
        finally:
            manager._update_task_cancel_mode = None
            if manager._update_task is not None and manager._update_task.done():
                manager._update_task = None


    async def prepare_and_countdown(
        self,
        manager: Any,
        operations: SupervisorUpdateExecutionOperations,
        *,
        action: str,
        target_rev: str,
        target_version: str,
        reason: str,
        countdown_sec: float,
        drain_timeout_sec: float,
        signal_delay_sec: float,
        prepare_lease_path: str = "",
        prepare_lease_token: str = "",
        prepare_timeout_sec: float | None = None,
        candidate_prewarm_deferral_count: int = 0,
    ) -> None:
        cancel_phase = "prepare"
        failure_phase = "prepare"
        target_slot = ""
        manifest: dict[str, Any] | None = None
        candidate_prewarm_state = "skipped"
        candidate_prewarm_message = ""
        candidate_prewarm_ready_at = None
        candidate_memory_guard: dict[str, Any] | None = None
        candidate_launch_state = "skipped"
        candidate_launch_message = ""
        used_candidate_cutover = False
        prepare_elapsed_s = None
        install_elapsed_s = None
        install_installer = None
        venv_seed_source = None
        venv_seeded = False
        prepare_timed_out = False
        try:
            prepare_started_at = time.time()
            prepare_task = asyncio.create_task(
                asyncio.to_thread(
                    operations.prepare_pending_update,
                    {
                        "action": action,
                        "target_rev": target_rev,
                        "target_version": target_version,
                        "reason": reason,
                        "prepare_lease_path": prepare_lease_path,
                        "prepare_lease_token": prepare_lease_token,
                    },
                )
            )
            while True:
                try:
                    prepare_result = await asyncio.wait_for(
                        asyncio.shield(prepare_task),
                        timeout=PREPARE_HEARTBEAT_SEC,
                    )
                    break
                except TimeoutError:
                    if prepare_task.done():
                        prepare_result = await prepare_task
                        break
                    heartbeat_at = time.time()
                    elapsed_s = max(0.0, heartbeat_at - prepare_started_at)
                    timeout_s = max(0.0, float(prepare_timeout_sec or 0.0))
                    if timeout_s and elapsed_s >= timeout_s:
                        prepare_timed_out = True
                        operations.revoke_prepare_lease(
                            status={
                                "prepare_lease_path": prepare_lease_path,
                                "prepare_lease_token": prepare_lease_token,
                            },
                            attempt=None,
                            reason="supervisor.prepare_timeout",
                        )
                        operations.write_core_update_status(
                            {
                                "state": "preparing",
                                "phase": "prepare",
                                "action": action,
                                "target_rev": target_rev,
                                "target_version": target_version,
                                "reason": reason,
                                "message": (
                                    "inactive slot preparation exceeded its timeout; "
                                    "stopping the worker"
                                ),
                                "prepare_elapsed_s": round(elapsed_s, 3),
                                "prepare_heartbeat_at": heartbeat_at,
                                "prepare_timeout_sec": timeout_s,
                                "prepare_timed_out": True,
                                "prepare_lease_path": prepare_lease_path or None,
                                "prepare_lease_token": prepare_lease_token or None,
                            }
                        )
                        prepare_result = await prepare_task
                        break
                    operations.write_core_update_status(
                        {
                            "state": "preparing",
                            "phase": "prepare",
                            "action": action,
                            "target_rev": target_rev,
                            "target_version": target_version,
                            "reason": reason,
                            "message": f"preparing inactive slot; worker active for {elapsed_s:.0f}s",
                            "prepare_elapsed_s": round(elapsed_s, 3),
                            "prepare_heartbeat_at": heartbeat_at,
                            "prepare_timeout_sec": prepare_timeout_sec,
                            "prepare_lease_path": prepare_lease_path or None,
                            "prepare_lease_token": prepare_lease_token or None,
                        }
            )
            if str(prepare_result.get("state") or "").strip().lower() != "prepared":
                prepare_lease_revocation = operations.revoke_prepare_lease(
                    status={
                        "prepare_lease_path": prepare_lease_path,
                        "prepare_lease_token": prepare_lease_token,
                    },
                    attempt=None,
                    reason=(
                        "supervisor.prepare_timeout"
                        if prepare_timed_out
                        else "supervisor.prepare_failed"
                    ),
                )
                status = operations.write_core_update_status(
                    {
                        **dict(prepare_result),
                        "action": action,
                        "target_rev": target_rev,
                        "target_version": target_version,
                        "reason": reason,
                        "prepare_lease_path": prepare_lease_path or None,
                        "prepare_lease_token": prepare_lease_token or None,
                        "prepare_timeout_sec": prepare_timeout_sec,
                        "prepare_timed_out": prepare_timed_out,
                        "prepare_lease_revocation": prepare_lease_revocation,
                    }
                )
                operations.complete_update_attempt(
                    state="failed",
                    status=status,
                    reason=str(prepare_result.get("message") or "prepare failed"),
                )
                manager._release_skill_runtime_migration_gate(reason="prepare_failed")
                return

            prepared_plan = prepare_result.get("plan") if isinstance(prepare_result.get("plan"), dict) else {}
            target_slot = str(
                prepare_result.get("target_slot")
                or prepared_plan.get("target_slot")
                or operations.choose_inactive_slot()
                or ""
            ).strip().upper()
            manifest = prepare_result.get("manifest") if isinstance(prepare_result.get("manifest"), dict) else None
            requested_target_version = target_version
            if isinstance(manifest, dict):
                resolved_target_rev = str(manifest.get("target_rev") or "").strip()
                resolved_target_version = str(
                    manifest.get("target_version")
                    or manifest.get("resolved_target_version")
                    or manifest.get("git_commit")
                    or ""
                ).strip()
                if resolved_target_rev:
                    target_rev = resolved_target_rev
                if resolved_target_version:
                    target_version = resolved_target_version
            if prepare_lease_path:
                with contextlib.suppress(Exception):
                    operations.write_prepare_lease(
                        Path(prepare_lease_path),
                        token=prepare_lease_token,
                        state="completed",
                        reason="prepared",
                        action=action,
                        target_rev=target_rev,
                        target_version=target_version,
                        target_slot=target_slot or None,
                        completed_at=float(prepare_result.get("finished_at") or time.time()),
                    )
            prepare_elapsed_s = prepare_result.get("prepare_elapsed_s")
            install_elapsed_s = prepare_result.get("install_elapsed_s")
            install_installer = str(prepare_result.get("install_installer") or "").strip() or None
            venv_seed_source = str(prepare_result.get("venv_seed_source") or "").strip() or None
            venv_seeded = bool(prepare_result.get("venv_seeded"))
            operations.write_core_update_status(
                {
                    "state": "preparing",
                    "phase": "prewarm",
                    "action": action,
                    "target_rev": target_rev,
                    "target_version": target_version,
                    "requested_target_version": requested_target_version,
                    "reason": reason,
                    "target_slot": target_slot,
                    "prepared_at": float(prepare_result.get("finished_at") or time.time()),
                    "prepare_elapsed_s": prepare_elapsed_s,
                    "install_elapsed_s": install_elapsed_s,
                    "install_installer": install_installer,
                    "venv_seed_source": venv_seed_source,
                    "venv_seeded": venv_seeded,
                    "candidate_prewarm_state": "starting",
                    "candidate_prewarm_started_at": time.time(),
                    "message": (
                        f"slot {target_slot} prepared; starting passive candidate prewarm"
                        if target_slot
                        else "inactive slot prepared; starting passive candidate prewarm"
                    ),
                    "manifest": manifest,
                }
            )
            try:
                candidate_prewarm = await manager._candidate_prewarm(target_slot=target_slot)
            except Exception as exc:
                candidate_prewarm = {
                    "attempted": True,
                    "state": "failed",
                    "message": f"candidate prewarm failed: {type(exc).__name__}: {exc}",
                }
            candidate_prewarm_state = str(candidate_prewarm.get("state") or "").strip().lower() or "skipped"
            candidate_prewarm_message = str(candidate_prewarm.get("message") or "").strip()
            candidate_prewarm_ready_at = candidate_prewarm.get("ready_at")
            candidate_memory_guard = (
                candidate_prewarm.get("candidate_memory_guard")
                if isinstance(candidate_prewarm.get("candidate_memory_guard"), dict)
                else None
            )
            countdown_started_at = time.time()
            status = operations.write_core_update_status(
                {
                    "state": "countdown",
                    "phase": "countdown",
                    "action": action,
                    "target_rev": target_rev,
                    "target_version": target_version,
                    "requested_target_version": requested_target_version,
                    "reason": reason,
                    "countdown_sec": countdown_sec,
                    "drain_timeout_sec": drain_timeout_sec,
                    "signal_delay_sec": signal_delay_sec,
                    "started_at": countdown_started_at,
                    "scheduled_for": countdown_started_at + countdown_sec,
                    "prepared_at": float(prepare_result.get("finished_at") or countdown_started_at),
                    "prepare_elapsed_s": prepare_elapsed_s,
                    "install_elapsed_s": install_elapsed_s,
                    "install_installer": install_installer,
                    "venv_seed_source": venv_seed_source,
                    "venv_seeded": venv_seeded,
                    "target_slot": target_slot,
                    "candidate_prewarm_state": candidate_prewarm_state,
                    "candidate_prewarm_message": candidate_prewarm_message or None,
                    "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                    "candidate_memory_guard": candidate_memory_guard,
                    "candidate_prewarm_deferral_count": candidate_prewarm_deferral_count,
                    "candidate_prewarm_max_deferrals": operations.warm_switch_max_deferrals(),
                    "message": (
                        (
                            f"slot {target_slot} prepared; passive candidate blocked by memory gate; countdown started"
                            if candidate_prewarm_state == "memory_blocked"
                            else
                            f"slot {target_slot} prepared; passive candidate ready; countdown started"
                            if candidate_prewarm_state == "ready"
                            else f"slot {target_slot} prepared; passive candidate warming; countdown started"
                            if candidate_prewarm_state == "starting"
                            else f"slot {target_slot} prepared; passive candidate prewarm failed; countdown started"
                            if candidate_prewarm_state == "failed"
                            else f"slot {target_slot} prepared; countdown started"
                        )
                        if target_slot
                        else "inactive slot prepared; countdown started"
                    ),
                    "manifest": manifest,
                }
            )
            operations.write_update_attempt(
                operations.build_attempt_payload(
                    action=action,
                    request={
                        "action": action,
                        "target_rev": target_rev,
                        "target_version": target_version,
                        "reason": reason,
                        "countdown_sec": countdown_sec,
                        "drain_timeout_sec": drain_timeout_sec,
                        "signal_delay_sec": signal_delay_sec,
                        "candidate_prewarm_state": candidate_prewarm_state,
                        "candidate_prewarm_message": candidate_prewarm_message,
                    },
                    status=status,
                    accepted=True,
                )
            )
            cancel_phase = "countdown"
            await asyncio.sleep(max(0.0, float(countdown_sec)))
            if candidate_prewarm_state == "starting":
                candidate_refresh = await manager._refresh_starting_candidate_prewarm(target_slot=target_slot)
                refreshed_state = str(candidate_refresh.get("state") or "").strip().lower()
                if refreshed_state:
                    candidate_prewarm_state = refreshed_state
                refreshed_message = str(candidate_refresh.get("message") or "").strip()
                if refreshed_message:
                    candidate_prewarm_message = refreshed_message
                if candidate_refresh.get("ready_at") is not None:
                    candidate_prewarm_ready_at = candidate_refresh.get("ready_at")
                if isinstance(candidate_refresh.get("candidate_memory_guard"), dict):
                    candidate_memory_guard = candidate_refresh.get("candidate_memory_guard")

            if (
                operations.warm_switch_enabled()
                and operations.warm_switch_strict_cutover_enabled()
                and not operations.warm_switch_cold_fallback_enabled()
                and candidate_prewarm_state != "ready"
            ):
                blocked_by_memory = candidate_prewarm_state == "memory_blocked"
                cleanup_kwargs: dict[str, Any] = {
                    "reason": (
                        "supervisor.candidate.defer_memory_blocked"
                        if blocked_by_memory
                        else "supervisor.candidate.defer_not_ready"
                    ),
                    "slot": target_slot,
                }
                if blocked_by_memory:
                    cleanup_kwargs["graceful"] = False
                candidate_cleanup = await manager._cleanup_candidate_runtime(**cleanup_kwargs)
                next_deferral_count = max(0, int(candidate_prewarm_deferral_count)) + 1
                max_deferrals = operations.warm_switch_max_deferrals()
                if next_deferral_count > max_deferrals:
                    failure_reason = (
                        "candidate_memory_blocked" if blocked_by_memory else "candidate_not_ready"
                    )
                    terminal_candidate_state = (
                        "failed_memory_blocked" if blocked_by_memory else "failed_not_ready"
                    )
                    operations.clear_core_update_plan()
                    status = operations.write_core_update_status(
                        {
                            "state": "failed",
                            "phase": "prewarm",
                            "action": action,
                            "target_rev": target_rev,
                            "target_version": target_version,
                            "requested_target_version": requested_target_version,
                            "reason": reason,
                            "target_slot": target_slot,
                            "prepared_at": float(prepare_result.get("finished_at") or time.time()),
                            "prepare_elapsed_s": prepare_elapsed_s,
                            "install_elapsed_s": install_elapsed_s,
                            "install_installer": install_installer,
                            "venv_seed_source": venv_seed_source,
                            "venv_seeded": venv_seeded,
                            "failure_reason": failure_reason,
                            "candidate_prewarm_state": terminal_candidate_state,
                            "candidate_prewarm_message": candidate_prewarm_message or None,
                            "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                            "candidate_memory_guard": candidate_memory_guard,
                            "candidate_cleanup": candidate_cleanup,
                            "candidate_prewarm_deferral_count": next_deferral_count,
                            "candidate_prewarm_max_deferrals": max_deferrals,
                            "message": (
                                "core update stopped after the passive candidate repeatedly exceeded the "
                                "warm-switch memory gate"
                                if blocked_by_memory
                                else "core update stopped after the passive candidate repeatedly failed to "
                                "become ready within the warm-switch deadline"
                            ),
                            "manifest": manifest,
                            "finished_at": time.time(),
                        }
                    )
                    operations.complete_update_attempt(
                        state="failed",
                        status=status,
                        reason=f"{failure_reason}: automatic warm-switch deferrals exhausted",
                    )
                    manager._release_skill_runtime_migration_gate(reason="candidate_deferrals_exhausted")
                    return
                scheduled_for = time.time() + operations.warm_switch_defer_sec()
                manager._schedule_planned_transition(
                    {
                        "action": action,
                        "target_rev": target_rev,
                        "target_version": target_version,
                        "reason": reason,
                        "countdown_sec": countdown_sec,
                        "drain_timeout_sec": drain_timeout_sec,
                        "signal_delay_sec": signal_delay_sec,
                        "candidate_prewarm_deferral_count": next_deferral_count,
                    },
                    scheduled_for=scheduled_for,
                    planned_reason="candidate_memory_blocked" if blocked_by_memory else "candidate_not_ready",
                    message=(
                        f"core update deferred; candidate slot {target_slot} exceeded the warm-switch memory gate"
                        if blocked_by_memory and target_slot
                        else "core update deferred; candidate runtime exceeded the warm-switch memory gate"
                        if blocked_by_memory
                        else
                        f"core update deferred; candidate slot {target_slot} is not ready for warm-switch cutover"
                        if target_slot
                        else "core update deferred; candidate runtime is not ready for warm-switch cutover"
                    ),
                    extra_status={
                        "target_slot": target_slot,
                        "prepared_at": float(prepare_result.get("finished_at") or time.time()),
                        "prepare_elapsed_s": prepare_elapsed_s,
                        "install_elapsed_s": install_elapsed_s,
                        "install_installer": install_installer,
                        "venv_seed_source": venv_seed_source,
                        "venv_seeded": venv_seeded,
                        "candidate_prewarm_state": "deferred_not_ready",
                        "candidate_prewarm_message": candidate_prewarm_message or None,
                        "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                        "candidate_memory_guard": candidate_memory_guard,
                        "candidate_prewarm_deferral_count": next_deferral_count,
                        "candidate_prewarm_max_deferrals": max_deferrals,
                        "candidate_cleanup": candidate_cleanup,
                        "manifest": manifest,
                    },
                    extra_attempt={
                        "target_slot": target_slot,
                        "candidate_prewarm_state": "deferred_not_ready",
                        "candidate_prewarm_message": candidate_prewarm_message or None,
                        "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                        "candidate_memory_guard": candidate_memory_guard,
                        "candidate_prewarm_deferral_count": next_deferral_count,
                        "candidate_prewarm_max_deferrals": max_deferrals,
                    },
                )
                manager._release_skill_runtime_migration_gate(reason="candidate_prewarm_deferred")
                return

            plan = {
                "state": "prepared_restart",
                "action": action,
                "target_rev": target_rev,
                "target_version": target_version,
                "reason": reason,
                "target_slot": target_slot,
                "prepared_at": float(prepare_result.get("finished_at") or time.time()),
                "prepare_elapsed_s": prepare_elapsed_s,
                "install_elapsed_s": install_elapsed_s,
                "install_installer": install_installer,
                "venv_seed_source": venv_seed_source,
                "venv_seeded": venv_seeded,
                "created_at": time.time(),
                "expires_at": time.time() + 1800.0,
            }
            operations.write_core_update_plan(plan)
            operations.write_core_update_status(
                {
                    "state": "restarting",
                    "phase": "shutdown",
                    "action": action,
                    "target_rev": target_rev,
                    "target_version": target_version,
                    "reason": reason,
                    "target_slot": target_slot,
                    "drain_timeout_sec": drain_timeout_sec,
                    "signal_delay_sec": signal_delay_sec,
                    "prepared_at": float(prepare_result.get("finished_at") or time.time()),
                    "prepare_elapsed_s": prepare_elapsed_s,
                    "install_elapsed_s": install_elapsed_s,
                    "install_installer": install_installer,
                    "venv_seed_source": venv_seed_source,
                    "venv_seeded": venv_seeded,
                    "candidate_prewarm_state": candidate_prewarm_state,
                    "candidate_prewarm_message": candidate_prewarm_message or None,
                    "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                    "candidate_memory_guard": candidate_memory_guard,
                    "message": "countdown completed; prepared restart written",
                    "manifest": manifest,
                }
            )
            candidate_cleanup: dict[str, Any] | None = None
            if candidate_prewarm_state == "ready":
                failure_phase = "launch"
                active_retirement: dict[str, Any] = {}
                cutover_result: dict[str, Any] = {}
                cold_fallback_enabled = operations.warm_switch_cold_fallback_enabled()
                operations.write_core_update_status(
                    {
                        "state": "restarting",
                        "phase": "cutover",
                        "action": action,
                        "target_rev": target_rev,
                        "target_version": target_version,
                        "reason": reason,
                        "target_slot": target_slot,
                        "prepared_at": float(prepare_result.get("finished_at") or time.time()),
                        "prepare_elapsed_s": prepare_elapsed_s,
                        "install_elapsed_s": install_elapsed_s,
                        "install_installer": install_installer,
                        "venv_seed_source": venv_seed_source,
                        "venv_seeded": venv_seeded,
                        "candidate_prewarm_state": candidate_prewarm_state,
                        "candidate_prewarm_message": candidate_prewarm_message or None,
                        "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                        "candidate_memory_guard": candidate_memory_guard,
                        "message": (
                            f"candidate slot {target_slot} is ready; retiring active transport owner before promotion"
                            if target_slot
                            else "candidate runtime is ready; retiring active transport owner before promotion"
                        ),
                        "manifest": manifest,
                    }
                )
                try:
                    cutover_result = await manager._single_owner_candidate_cutover(
                        slot=target_slot,
                        reason="supervisor.fast_cutover",
                        restore_active_on_failure=not cold_fallback_enabled,
                    )
                    active_retirement = (
                        cutover_result.get("active_retirement")
                        if isinstance(cutover_result.get("active_retirement"), dict)
                        else {}
                    )
                    if not bool(cutover_result.get("ok")):
                        raise RuntimeError(str(cutover_result.get("error") or "atomic candidate cutover failed"))
                except Exception as exc:
                    operations.clear_core_update_plan()
                    latest_guard = manager._candidate_memory_guard_snapshot()
                    blocked_by_memory = not bool(latest_guard.get("allowed"))
                    if blocked_by_memory:
                        candidate_memory_guard = latest_guard
                    candidate_cleanup = (
                        cutover_result.get("candidate_cleanup")
                        if isinstance(cutover_result.get("candidate_cleanup"), dict)
                        else {}
                    )
                    active_restore = (
                        cutover_result.get("active_restore")
                        if isinstance(cutover_result.get("active_restore"), dict)
                        else None
                    )
                    if not cold_fallback_enabled:
                        if not bool((active_restore or {}).get("ok")):
                            raise RuntimeError("failed to restore active runtime after candidate cutover failure") from exc
                        manager._schedule_planned_transition(
                            {
                                "action": action,
                                "target_rev": target_rev,
                                "target_version": target_version,
                                "reason": reason,
                                "countdown_sec": countdown_sec,
                                "drain_timeout_sec": drain_timeout_sec,
                                "signal_delay_sec": signal_delay_sec,
                            },
                            scheduled_for=time.time() + operations.warm_switch_defer_sec(),
                            planned_reason="candidate_memory_blocked" if blocked_by_memory else "candidate_cutover_failed",
                            message=(
                                f"core update deferred; candidate slot {target_slot} exceeded the warm-switch memory gate during atomic cutover"
                                if blocked_by_memory and target_slot
                                else "core update deferred; candidate runtime exceeded the warm-switch memory gate during atomic cutover"
                                if blocked_by_memory
                                else
                                f"core update deferred; candidate slot {target_slot} cutover failed and the previous runtime was restored"
                                if target_slot
                                else "core update deferred; candidate cutover failed and the previous runtime was restored"
                            ),
                            extra_status={
                                "target_slot": target_slot,
                                "prepared_at": float(prepare_result.get("finished_at") or time.time()),
                                "prepare_elapsed_s": prepare_elapsed_s,
                                "install_elapsed_s": install_elapsed_s,
                                "install_installer": install_installer,
                                "venv_seed_source": venv_seed_source,
                                "venv_seeded": venv_seeded,
                                "candidate_prewarm_state": "cutover_deferred",
                                "candidate_prewarm_message": f"{type(exc).__name__}: {exc}",
                                "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                                "candidate_memory_guard": candidate_memory_guard,
                                "candidate_cleanup": candidate_cleanup,
                                "active_retirement": active_retirement,
                                "active_restore": active_restore,
                                "manifest": manifest,
                            },
                            extra_attempt={
                                "target_slot": target_slot,
                                "candidate_prewarm_state": "cutover_deferred",
                                "candidate_prewarm_message": f"{type(exc).__name__}: {exc}",
                                "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                                "candidate_memory_guard": candidate_memory_guard,
                            },
                        )
                        manager._release_skill_runtime_migration_gate(reason="candidate_cutover_deferred")
                        return

                    candidate_prewarm_state = "cutover_fallback"
                    candidate_prewarm_message = f"{type(exc).__name__}: {exc}"
                    operations.write_core_update_status(
                        {
                            "state": "restarting",
                            "phase": "shutdown",
                            "action": action,
                            "target_rev": target_rev,
                            "target_version": target_version,
                            "reason": reason,
                            "target_slot": target_slot,
                            "prepared_at": float(prepare_result.get("finished_at") or time.time()),
                            "prepare_elapsed_s": prepare_elapsed_s,
                            "install_elapsed_s": install_elapsed_s,
                            "install_installer": install_installer,
                            "venv_seed_source": venv_seed_source,
                            "venv_seeded": venv_seeded,
                            "candidate_prewarm_state": candidate_prewarm_state,
                            "candidate_prewarm_message": candidate_prewarm_message,
                            "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                            "candidate_memory_guard": candidate_memory_guard,
                            "candidate_cleanup": candidate_cleanup,
                            "message": (
                                f"candidate slot {target_slot} cutover failed; falling back to cold restart"
                                if target_slot
                                else "candidate cutover failed; falling back to cold restart"
                            ),
                            "manifest": manifest,
                        }
                    )
                else:
                    operations.activate_slot(target_slot)
                    used_candidate_cutover = True
                    candidate_launch_state = "promoted_to_active"
                    candidate_launch_message = (
                        "passive candidate runtime promoted after the previous transport owner stopped"
                    )
                    operations.write_core_update_status(
                        {
                            "state": "restarting",
                            "phase": "launch",
                            "action": action,
                            "target_rev": target_rev,
                            "target_version": target_version,
                            "reason": reason,
                            "target_slot": target_slot,
                            "prepared_at": float(prepare_result.get("finished_at") or time.time()),
                            "prepare_elapsed_s": prepare_elapsed_s,
                            "install_elapsed_s": install_elapsed_s,
                            "install_installer": install_installer,
                            "venv_seed_source": venv_seed_source,
                            "venv_seeded": venv_seeded,
                            "candidate_prewarm_state": candidate_launch_state,
                            "candidate_prewarm_message": candidate_launch_message,
                            "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                            "candidate_memory_guard": candidate_memory_guard,
                            "active_retirement": active_retirement,
                            "message": (
                                f"prepared slot {target_slot} activated via single-owner cutover; awaiting validation"
                                if target_slot
                                else "prepared slot activated via single-owner cutover; awaiting validation"
                            ),
                            "manifest": manifest,
                        }
                    )
                    async with manager._lock:
                        manager._desired_running = True
                        manager._persist_runtime_state()
                    return

            failure_phase = "shutdown"
            shutdown_request_error: Exception | None = None
            try:
                await manager._request_runtime_shutdown(
                    reason=reason,
                    drain_timeout_sec=drain_timeout_sec,
                    signal_delay_sec=signal_delay_sec,
                )
            except Exception as exc:
                shutdown_request_error = exc
            async with manager._lock:
                manager._desired_running = False
                manager._persist_runtime_state()
            stop_result = await manager._ensure_runtime_stopped_for_update(
                drain_timeout_sec=drain_timeout_sec,
                signal_delay_sec=signal_delay_sec,
                reason=reason,
            )
            if shutdown_request_error or bool(stop_result.get("forced")):
                operations.write_core_update_status(
                    {
                        "state": "restarting",
                        "phase": "shutdown",
                        "action": action,
                        "target_rev": target_rev,
                        "target_version": target_version,
                        "reason": reason,
                        "target_slot": target_slot,
                        "drain_timeout_sec": drain_timeout_sec,
                        "signal_delay_sec": signal_delay_sec,
                        "prepare_elapsed_s": prepare_elapsed_s,
                        "install_elapsed_s": install_elapsed_s,
                        "install_installer": install_installer,
                        "venv_seed_source": venv_seed_source,
                        "venv_seeded": venv_seeded,
                        "candidate_prewarm_state": candidate_prewarm_state,
                        "candidate_prewarm_message": candidate_prewarm_message or None,
                        "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                        "candidate_memory_guard": candidate_memory_guard,
                        "message": (
                            "runtime shutdown API was unavailable; supervisor continued with direct process stop"
                            if shutdown_request_error and bool(stop_result.get("forced"))
                            else "runtime shutdown API response was unavailable; runtime still stopped during grace window"
                            if shutdown_request_error
                            else "runtime shutdown exceeded grace period; supervisor forced process stop"
                        ),
                        "forced_shutdown": bool(stop_result.get("forced")),
                        "shutdown_request_error_type": (
                            type(shutdown_request_error).__name__ if shutdown_request_error is not None else None
                        ),
                        "shutdown_request_error": str(shutdown_request_error) if shutdown_request_error is not None else None,
                        "manifest": manifest,
                    }
                )
            operations.activate_slot(target_slot)
            candidate_launch_state = candidate_prewarm_state
            candidate_launch_message = candidate_prewarm_message
            if candidate_prewarm_state == "ready":
                try:
                    await manager._promote_candidate_runtime(
                        slot=target_slot,
                        reason="supervisor.fast_cutover",
                    )
                    used_candidate_cutover = True
                    candidate_launch_state = "promoted_to_active"
                    candidate_launch_message = (
                        "passive candidate runtime promoted to active via warm-switch cutover"
                    )
                except Exception as exc:
                    latest_guard = manager._candidate_memory_guard_snapshot()
                    blocked_by_memory = not bool(latest_guard.get("allowed"))
                    if blocked_by_memory:
                        candidate_memory_guard = latest_guard
                    cleanup_kwargs = {
                        "reason": (
                            "supervisor.candidate.cutover_memory_fallback"
                            if blocked_by_memory
                            else "supervisor.candidate.cutover_fallback"
                        ),
                        "slot": target_slot,
                    }
                    if blocked_by_memory:
                        cleanup_kwargs["graceful"] = False
                    candidate_cleanup = await manager._cleanup_candidate_runtime(**cleanup_kwargs)
                    candidate_launch_state = "cutover_fallback"
                    candidate_launch_message = (
                        f"warm-switch cutover fallback: {type(exc).__name__}: {exc}"
                    )
            elif candidate_prewarm_state not in {"skipped", "cutover_fallback"}:
                blocked_by_memory = candidate_prewarm_state == "memory_blocked"
                cleanup_kwargs = {
                    "reason": (
                        "supervisor.candidate.stop_memory_blocked_before_active_launch"
                        if blocked_by_memory
                        else "supervisor.candidate.stop_before_active_launch"
                    ),
                    "slot": target_slot,
                }
                if blocked_by_memory:
                    cleanup_kwargs["graceful"] = False
                candidate_cleanup = await manager._cleanup_candidate_runtime(**cleanup_kwargs)
                if bool((candidate_cleanup or {}).get("stopped")):
                    if blocked_by_memory:
                        candidate_launch_state = "memory_blocked"
                        candidate_launch_message = (
                            candidate_prewarm_message
                            or "passive candidate runtime stopped by warm-switch memory gate before active launch"
                        )
                    else:
                        candidate_launch_state = "stopped_for_launch"
                        candidate_launch_message = "passive candidate runtime stopped before active launch"
            failure_phase = "launch"
            operations.write_core_update_status(
                {
                    "state": "restarting",
                    "phase": "launch",
                    "action": action,
                    "target_rev": target_rev,
                    "target_version": target_version,
                    "reason": reason,
                    "target_slot": target_slot,
                    "prepared_at": float(prepare_result.get("finished_at") or time.time()),
                    "prepare_elapsed_s": prepare_elapsed_s,
                    "install_elapsed_s": install_elapsed_s,
                    "install_installer": install_installer,
                    "venv_seed_source": venv_seed_source,
                    "venv_seeded": venv_seeded,
                    "candidate_prewarm_state": candidate_launch_state,
                    "candidate_prewarm_message": candidate_launch_message or None,
                    "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                    "candidate_memory_guard": candidate_memory_guard,
                    "candidate_cleanup": candidate_cleanup,
                    "message": (
                        f"prepared slot {target_slot} activated via warm-switch cutover; awaiting validation"
                        if used_candidate_cutover and target_slot
                        else "prepared slot activated via warm-switch cutover; awaiting validation"
                        if used_candidate_cutover
                        else f"prepared slot {target_slot} activated; awaiting runtime launch"
                        if target_slot
                        else "prepared slot activated; awaiting runtime launch"
                    ),
                    "manifest": manifest,
                }
            )
            async with manager._lock:
                manager._desired_running = True
                manager._persist_runtime_state()
        except asyncio.CancelledError:
            manager._release_skill_runtime_migration_gate(reason="prepared_transition_cancelled")
            operations.clear_core_update_plan()
            prepare_lease_revocation = operations.revoke_prepare_lease(
                status={
                    "prepare_lease_path": prepare_lease_path,
                    "prepare_lease_token": prepare_lease_token,
                },
                attempt=None,
                reason="supervisor.cancelled_transition",
            )
            await manager._cleanup_candidate_runtime(
                reason="supervisor.candidate.cancelled_transition",
                slot=target_slot or None,
            )
            cancel_mode = str(manager._update_task_cancel_mode or "").strip().lower()
            manager._update_task_cancel_mode = None
            if cancel_mode != "rescheduled":
                status = operations.write_core_update_status(
                    {
                        "state": "cancelled",
                        "phase": cancel_phase,
                        "action": action,
                        "target_rev": target_rev,
                        "target_version": target_version,
                        "reason": reason,
                        "drain_timeout_sec": drain_timeout_sec,
                        "signal_delay_sec": signal_delay_sec,
                        "candidate_prewarm_state": candidate_prewarm_state,
                        "candidate_prewarm_message": candidate_prewarm_message or None,
                        "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                        "candidate_memory_guard": candidate_memory_guard,
                        "message": "core update cancelled",
                        "prepare_lease_revocation": prepare_lease_revocation,
                    }
                )
                operations.complete_update_attempt(state="cancelled", status=status, reason=reason)
            raise
        except Exception as exc:
            manager._release_skill_runtime_migration_gate(
                reason=f"prepared_transition_failed:{type(exc).__name__}"
            )
            operations.clear_core_update_plan()
            prepare_lease_revocation = operations.revoke_prepare_lease(
                status={
                    "prepare_lease_path": prepare_lease_path,
                    "prepare_lease_token": prepare_lease_token,
                },
                attempt=None,
                reason=f"supervisor.failed_transition:{type(exc).__name__}",
            )
            await manager._cleanup_candidate_runtime(
                reason="supervisor.candidate.failed_transition",
                slot=target_slot or None,
            )
            async with manager._lock:
                manager._desired_running = True
                manager._persist_runtime_state()
            slot_cleanup = (
                operations.remove_inactive_slot(target_slot, reason="supervisor.prepared_transition_failed")
                if target_slot
                else None
            )
            status = operations.write_core_update_status(
                {
                    "state": "failed",
                    "phase": failure_phase,
                    "action": action,
                    "target_rev": target_rev,
                    "target_version": target_version,
                    "reason": reason,
                    "drain_timeout_sec": drain_timeout_sec,
                    "signal_delay_sec": signal_delay_sec,
                    "candidate_prewarm_state": candidate_prewarm_state,
                    "candidate_prewarm_message": candidate_prewarm_message or None,
                    "candidate_prewarm_ready_at": candidate_prewarm_ready_at,
                    "candidate_memory_guard": candidate_memory_guard,
                    "message": "prepared core update transition failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "updated_at": time.time(),
                    "slot_cleanup": slot_cleanup,
                    "prepare_lease_revocation": prepare_lease_revocation,
                }
            )
            operations.complete_update_attempt(
                state="failed",
                status=status,
                reason=f"prepared transition failed: {type(exc).__name__}",
            )
        finally:
            manager._update_task_cancel_mode = None
            if manager._update_task is not None and manager._update_task.done():
                manager._update_task = None

