from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class UpdateReconciliationOperations:
    active_slot_target_mismatch_status: Any
    attempt_transition_at: Any
    clear_core_update_plan: Any
    clear_orphaned_subsequent_transition_status: Any
    compact_public_runtime_self_heal: Any
    compact_watchdog_required_link: Any
    complete_update_attempt: Any
    fail_root_restart_attempt: Any
    finalize_runtime_boot_status_from_supervisor: Any
    is_root_restart_completed_status: Any
    is_root_restart_pending_attempt: Any
    is_root_promotion_pending_status: Any
    is_terminal_update_status: Any
    read_core_update_status: Any
    read_update_attempt: Any
    reconcile_failed_attempt_after_terminal_success: Any
    reconcile_failed_root_restart_after_runtime_recovery: Any
    reconcile_failed_target_mismatch_after_active_switch: Any
    recover_active_attempt_target_already_active: Any
    revoke_prepare_lease: Any
    rollback_installed_skill_runtimes: Any
    rollback_to_previous_slot: Any
    runtime_ready_for_boot_status_finalize: Any
    status_updated_at: Any
    terminal_status_belongs_to_attempt: Any
    transition_snapshot_current: Any
    update_attempt_contract_version: Any
    update_status_timeout_sec: Any
    update_transition_timed_out: Any
    write_core_update_status: Any


class UpdateReconciliationService:
    def reconcile(
        self,
        operations: UpdateReconciliationOperations,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        if operations.runtime_ready_for_boot_status_finalize(status, runtime):
            finalized_status = operations.finalize_runtime_boot_status_from_supervisor()
            if isinstance(finalized_status, dict):
                status = finalized_status
                payload["status"] = finalized_status
                payload["_served_by"] = "supervisor_runtime_ready_finalize"
        attempt = operations.read_update_attempt()
        mismatch_status = operations.active_slot_target_mismatch_status(status, attempt if isinstance(attempt, dict) else None)
        if isinstance(mismatch_status, dict):
            status = mismatch_status
            payload["status"] = mismatch_status
            payload["_served_by"] = "supervisor_target_mismatch_recovery"
        if not isinstance(attempt, dict):
            return payload

        cleaned_status = operations.clear_orphaned_subsequent_transition_status(status, attempt)
        if cleaned_status != status:
            status = cleaned_status
            payload["status"] = cleaned_status
            payload["_served_by"] = "supervisor_orphaned_subsequent_recovery"
        payload["attempt"] = dict(attempt)
        recovered_status = operations.recover_active_attempt_target_already_active(
            status=status,
            attempt=attempt,
            runtime=runtime,
        )
        if isinstance(recovered_status, dict):
            payload["status"] = recovered_status
            payload["attempt"] = operations.read_update_attempt() or payload["attempt"]
            payload["_served_by"] = "supervisor_active_target_recovery"
            return payload
        recovered_attempt = operations.reconcile_failed_target_mismatch_after_active_switch(
            status=status,
            attempt=attempt,
            runtime=runtime,
        )
        if isinstance(recovered_attempt, dict):
            payload["status"] = operations.read_core_update_status() or status
            payload["attempt"] = recovered_attempt
            payload["_served_by"] = "supervisor_failed_target_mismatch_reconciled"
            return payload
        recovered_attempt = operations.reconcile_failed_root_restart_after_runtime_recovery(
            status=status,
            attempt=attempt,
            runtime=runtime,
        )
        if isinstance(recovered_attempt, dict):
            payload["status"] = operations.read_core_update_status() or status
            payload["attempt"] = recovered_attempt
            payload["_served_by"] = "supervisor_root_restart_timeout_reconciled"
            return payload

        now = time.time()
        timeout_sec = operations.update_status_timeout_sec(status)
        status_age = max(0.0, now - operations.status_updated_at(status)) if operations.status_updated_at(status) > 0.0 else 0.0
        transition_age = max(0.0, now - operations.attempt_transition_at(attempt)) if operations.attempt_transition_at(attempt) > 0.0 else 0.0
        if bool(status.get("active_slot_target_mismatch")):
            payload["attempt"] = operations.complete_update_attempt(
                state="failed",
                status=status,
                reason="active slot target mismatch",
            )
            return payload
        if operations.is_root_restart_pending_attempt(attempt):
            if operations.is_root_restart_completed_status(status):
                payload["attempt"] = operations.complete_update_attempt(
                    state="completed",
                    status=status,
                    reason="root restart completed",
                )
            elif operations.update_transition_timed_out(
                status_age=status_age,
                transition_age=transition_age,
                timeout_sec=timeout_sec,
            ):
                # If a new runtime is already serving status, let it finalize a stale
                # root-promoted marker before declaring the update failed.
                finalized_status = (
                    operations.finalize_runtime_boot_status_from_supervisor()
                    if operations.runtime_ready_for_boot_status_finalize(status, runtime)
                    else None
                )
                if operations.is_root_restart_completed_status(finalized_status):
                    payload["status"] = finalized_status
                    payload["attempt"] = operations.complete_update_attempt(
                        state="completed",
                        status=finalized_status,
                        reason="root restart completed",
                    )
                    payload["_served_by"] = "supervisor_timeout_finalize"
                    return payload
                failed_attempt = operations.fail_root_restart_attempt(
                    status=status,
                    attempt=attempt,
                    timeout_sec=timeout_sec,
                    now=now,
                )
                payload["status"] = operations.read_core_update_status()
                payload["attempt"] = failed_attempt
                payload["_served_by"] = "supervisor_timeout_recovery"
            return payload

        if operations.is_root_promotion_pending_status(status):
            payload["attempt"] = attempt
            return payload

        if str(attempt.get("state") or "").strip().lower() != "active":
            reconciled_attempt = operations.reconcile_failed_attempt_after_terminal_success(
                status=status,
                attempt=attempt,
            )
            if isinstance(reconciled_attempt, dict):
                payload["attempt"] = reconciled_attempt
                payload["_served_by"] = "supervisor_failed_attempt_success_reconciled"
            return payload

        if operations.is_terminal_update_status(status):
            if not operations.terminal_status_belongs_to_attempt(status, attempt):
                payload["_served_by"] = "supervisor_stale_terminal_status_ignored"
                return payload
            if bool(status.get("active_slot_target_mismatch")):
                payload["attempt"] = operations.complete_update_attempt(
                    state="failed",
                    status=status,
                    reason="active slot target mismatch",
                )
                return payload
            payload["attempt"] = operations.complete_update_attempt(state="completed", status=status, reason="terminal core update status")
            return payload

        if not operations.update_transition_timed_out(
            status_age=status_age,
            transition_age=transition_age,
            timeout_sec=timeout_sec,
        ):
            return payload

        if not operations.transition_snapshot_current(status=status, attempt=attempt):
            payload["status"] = operations.read_core_update_status()
            payload["attempt"] = operations.read_update_attempt() or payload["attempt"]
            payload["reconciliation"] = {
                "deferred": True,
                "retryable": True,
                "reason": "transition_snapshot_advanced",
            }
            payload["_served_by"] = "supervisor_stale_timeout_ignored"
            return payload

        action = str(status.get("action") or attempt.get("action") or "update")
        prepare_lease_revocation = operations.revoke_prepare_lease(
            status=status,
            attempt=attempt,
            reason="supervisor.timeout_recovery",
        )
        failed_payload: dict[str, Any] = {
            "state": "failed",
            "phase": str(status.get("phase") or "restart_timeout"),
            "action": action,
            "target_rev": str(status.get("target_rev") or attempt.get("target_rev") or ""),
            "target_version": str(status.get("target_version") or attempt.get("target_version") or ""),
            "reason": str(status.get("reason") or attempt.get("reason") or "supervisor.timeout"),
            "message": f"supervisor timed out waiting for runtime to finish {status.get('state') or 'update transition'}",
            "supervisor_timeout_sec": timeout_sec,
            "supervisor_timeout_at": now,
            "supervisor_previous_status": status,
        }
        if prepare_lease_revocation is not None:
            failed_payload["prepare_lease_revocation"] = prepare_lease_revocation
        if action == "update":
            target_slot = str(status.get("target_slot") or attempt.get("target_slot") or "").strip().upper()
            restored = operations.rollback_to_previous_slot()
            skill_runtime_rollback = operations.rollback_installed_skill_runtimes() if restored else {}
            if restored:
                failed_payload["restored_slot"] = restored
                failed_payload["rollback"] = {"ok": True, "slot": restored}
                failed_payload["message"] += f"; rolled back to slot {restored}"
            if target_slot:
                # Timeout reconciliation has no process handle and cannot prove that
                # the target runtime has exited. Removing its slot here leaves a
                # live process executing from a deleted interpreter/source tree.
                failed_payload["slot_cleanup"] = {
                    "ok": True,
                    "removed": False,
                    "deferred": True,
                    "slot": target_slot,
                    "reason": "runtime_stop_not_confirmed",
                }
            if skill_runtime_rollback:
                failed_payload["skill_runtime_rollback"] = skill_runtime_rollback
                if restored and not bool(skill_runtime_rollback.get("ok")):
                    failed_payload["message"] += " | some skill runtime rollbacks failed"
        if not operations.transition_snapshot_current(status=status, attempt=attempt):
            payload["status"] = operations.read_core_update_status()
            payload["attempt"] = operations.read_update_attempt() or payload["attempt"]
            payload["reconciliation"] = {
                "deferred": True,
                "retryable": False,
                "reason": "transition_advanced_during_timeout_recovery",
                "rollback": failed_payload.get("rollback"),
                "skill_runtime_rollback": failed_payload.get("skill_runtime_rollback"),
            }
            payload["_served_by"] = "supervisor_stale_timeout_write_suppressed"
            return payload
        failed_status = operations.write_core_update_status(failed_payload)
        with contextlib.suppress(Exception):
            operations.clear_core_update_plan()
        payload["status"] = failed_status
        payload["attempt"] = operations.complete_update_attempt(state="failed", status=failed_status, reason="restart/apply timeout")
        payload["_served_by"] = "supervisor_timeout_recovery"
        return payload


    def public_payload(
        self,
        operations: UpdateReconciliationOperations,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        source = dict(payload or {})
        status = source.get("status") if isinstance(source.get("status"), dict) else {}
        runtime = source.get("runtime") if isinstance(source.get("runtime"), dict) else {}
        attempt = source.get("attempt") if isinstance(source.get("attempt"), dict) else {}
        runtime_self_heal = runtime.get("runtime_self_heal") if isinstance(runtime.get("runtime_self_heal"), dict) else {}
        sidecar = runtime.get("sidecar") if isinstance(runtime.get("sidecar"), dict) else {}
        sidecar_process = sidecar.get("process") if isinstance(sidecar.get("process"), dict) else {}
        sidecar_health = sidecar.get("health") if isinstance(sidecar.get("health"), dict) else {}
        sidecar_code = sidecar.get("code") if isinstance(sidecar.get("code"), dict) else {}
        sidecar_policy = sidecar.get("restart_policy") if isinstance(sidecar.get("restart_policy"), dict) else {}
        sidecar_sync = sidecar.get("sync") if isinstance(sidecar.get("sync"), dict) else {}
        public_status = {
            "action": str(status.get("action") or "").strip().lower() or None,
            "state": str(status.get("state") or "").strip().lower() or "unknown",
            "phase": str(status.get("phase") or "").strip().lower() or "",
            "message": str(status.get("message") or "").strip(),
            "target_rev": str(status.get("target_rev") or "").strip(),
            "target_version": str(status.get("target_version") or "").strip(),
            "planned_reason": str(status.get("planned_reason") or "").strip() or None,
            "min_update_period_sec": status.get("min_update_period_sec"),
            "scheduled_for": status.get("scheduled_for"),
            "subsequent_transition": bool(status.get("subsequent_transition")),
            "subsequent_transition_requested_at": status.get("subsequent_transition_requested_at"),
            "candidate_prewarm_state": str(status.get("candidate_prewarm_state") or "").strip() or None,
            "candidate_prewarm_message": str(status.get("candidate_prewarm_message") or "").strip() or None,
            "candidate_prewarm_ready_at": status.get("candidate_prewarm_ready_at"),
            "candidate_prewarm_deferral_count": status.get("candidate_prewarm_deferral_count"),
            "candidate_prewarm_max_deferrals": status.get("candidate_prewarm_max_deferrals"),
            "failure_reason": str(status.get("failure_reason") or "").strip() or None,
            "restart_mode": str(status.get("restart_mode") or "").strip() or None,
            "restart_requested_at": status.get("restart_requested_at"),
            "updated_at": status.get("updated_at"),
        }
        return {
            "ok": True,
            "status": public_status,
            "attempt": {
                "contract_version": str(attempt.get("contract_version") or operations.update_attempt_contract_version),
                "authority": str(attempt.get("authority") or "supervisor"),
                "action": str(attempt.get("action") or "").strip().lower() or None,
                "state": str(attempt.get("state") or "").strip().lower() or None,
                "awaiting_restart": bool(attempt.get("awaiting_restart")),
                "planned_reason": str(attempt.get("planned_reason") or "").strip() or None,
                "scheduled_for": attempt.get("scheduled_for"),
                "subsequent_transition": bool(attempt.get("subsequent_transition")),
                "subsequent_transition_requested_at": attempt.get("subsequent_transition_requested_at"),
                "candidate_prewarm_state": str(attempt.get("candidate_prewarm_state") or "").strip() or None,
                "candidate_prewarm_message": str(attempt.get("candidate_prewarm_message") or "").strip() or None,
                "candidate_prewarm_deferral_count": attempt.get("candidate_prewarm_deferral_count"),
                "candidate_prewarm_max_deferrals": attempt.get("candidate_prewarm_max_deferrals"),
                "completion_reason": str(attempt.get("completion_reason") or "").strip() or None,
                "restart_mode": str(attempt.get("restart_mode") or "").strip() or None,
                "restart_requested_at": attempt.get("restart_requested_at"),
                "updated_at": attempt.get("updated_at"),
            },
            "runtime": {
                "active_slot": str(runtime.get("active_slot") or "").strip() or None,
                "runtime_state": str(runtime.get("runtime_state") or "").strip() or None,
                "runtime_url": str(runtime.get("runtime_url") or "").strip() or None,
                "runtime_port": runtime.get("runtime_port"),
                "runtime_instance_id": str(runtime.get("runtime_instance_id") or "").strip() or None,
                "transition_role": str(runtime.get("transition_role") or "").strip() or None,
                "listener_running": bool(runtime.get("listener_running")),
                "runtime_api_ready": bool(runtime.get("runtime_api_ready")),
                "candidate_slot": str(runtime.get("candidate_slot") or "").strip() or None,
                "candidate_runtime_url": str(runtime.get("candidate_runtime_url") or "").strip() or None,
                "candidate_runtime_port": runtime.get("candidate_runtime_port"),
                "candidate_runtime_instance_id": str(runtime.get("candidate_runtime_instance_id") or "").strip() or None,
                "candidate_transition_role": str(runtime.get("candidate_transition_role") or "").strip() or None,
                "candidate_listener_running": bool(runtime.get("candidate_listener_running")),
                "candidate_runtime_api_ready": bool(runtime.get("candidate_runtime_api_ready")),
                "candidate_runtime_state": str(runtime.get("candidate_runtime_state") or "").strip() or None,
                "transition_mode": str(runtime.get("transition_mode") or "").strip() or None,
                "warm_switch_supported": runtime.get("warm_switch_supported"),
                "warm_switch_allowed": runtime.get("warm_switch_allowed"),
                "warm_switch_reason": str(runtime.get("warm_switch_reason") or "").strip() or None,
                "slot_ports": runtime.get("slot_ports") if isinstance(runtime.get("slot_ports"), dict) else {},
                "required_upstream_link": operations.compact_watchdog_required_link(runtime.get("required_upstream_link")),
                "root_promotion_required": bool(runtime.get("root_promotion_required")),
                "runtime_self_heal": operations.compact_public_runtime_self_heal(runtime_self_heal),
                "sidecar": {
                    "enabled": bool(sidecar.get("enabled")),
                    "role": str(sidecar.get("role") or "").strip() or None,
                    "launch_cwd": str(sidecar.get("launch_cwd") or "").strip() or None,
                    "last_start_reason": str(sidecar.get("last_start_reason") or "").strip() or None,
                    "last_restart_reason": str(sidecar.get("last_restart_reason") or "").strip() or None,
                    "listener_running": bool(sidecar_process.get("listener_running")),
                    "listener_pid": sidecar_process.get("listener_pid"),
                    "managed_pid": sidecar_process.get("managed_pid"),
                    "adopted_listener": bool(sidecar_process.get("adopted_listener")),
                    "last_probe_ok": sidecar_health.get("last_probe_ok"),
                    "last_probe_error": str(sidecar_health.get("last_probe_error") or "").strip() or None,
                    "consecutive_failures": sidecar_health.get("consecutive_failures"),
                    "code_changed": bool(
                        sidecar_code.get("fingerprint")
                        and sidecar_code.get("active_fingerprint")
                        and str(sidecar_code.get("fingerprint")) != str(sidecar_code.get("active_fingerprint"))
                    ),
                    "active_fingerprint": str(sidecar_code.get("active_fingerprint") or "").strip() or None,
                    "pending_fingerprint": str(sidecar_policy.get("pending_code_fingerprint") or "").strip() or None,
                    "restart_backoff_remaining_s": sidecar_policy.get("restart_backoff_remaining_s"),
                    "circuit_open_remaining_s": sidecar_policy.get("circuit_open_remaining_s"),
                    "sync_source_mode": str(sidecar_code.get("sync_source_mode") or "").strip() or None,
                    "sync_source_slot": str(sidecar_code.get("sync_source_slot") or "").strip() or None,
                    "last_sync_at": sidecar_sync.get("last_sync_at"),
                    "last_sync_source_slot": str(sidecar_sync.get("last_sync_source_slot") or "").strip() or None,
                    "last_sync_reason": str(sidecar_sync.get("last_sync_reason") or "").strip() or None,
                    "last_sync_changed_paths": list(sidecar_sync.get("last_sync_changed_paths") or []),
                    "sync_changed": bool(sidecar_sync.get("last_sync_changed_paths")),
                },
            },
            "_served_by": str(source.get("_served_by") or "").strip() or "unknown",
        }

