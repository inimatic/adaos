from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SupervisorStatusOperations:
    active_slot: Any
    active_slot_manifest: Any
    core_slot_status: Any
    listener_running: Any
    proc_details: Any
    read_core_update_status: Any
    read_jsonl_tail: Any
    read_slot_manifest: Any
    read_update_attempt: Any
    realtime_sidecar_diag_path: Any
    realtime_sidecar_local_url: Any
    resolved_root_promotion_requirement: Any
    runtime_api_ready: Any
    supervisor_base_url: Any
    validate_slot_structure: Any


class SupervisorStatusService:
    def sidecar_runtime_payload(
        self,
        manager: Any,
        operations: SupervisorStatusOperations,
    ) -> dict[str, Any]:
        status = manager._sidecar_status_payload()
        process = status.get("process") if isinstance(status.get("process"), dict) else {}
        role = str(status.get("role") or manager._sidecar_role() or "").strip().lower() or None
        enablement = process.get("enablement_policy") if isinstance(process.get("enablement_policy"), dict) else {}
        enabled = bool(status.get("enabled"))
        route_tunnel_contract = (
            process.get("route_tunnel_contract")
            if isinstance(process.get("route_tunnel_contract"), dict)
            else {}
        )
        diag_path = operations.realtime_sidecar_diag_path()
        record = operations.read_jsonl_tail(diag_path, limit=1)
        last_diag = record[-1] if record else None
        now_ts = time.time()
        diag_age_s = None
        diag_fresh = False
        if isinstance(last_diag, dict) and isinstance(last_diag.get("ts"), (int, float)):
            diag_age_s = round(max(0.0, now_ts - float(last_diag.get("ts"))), 3)
            diag_fresh = float(diag_age_s) <= 10.0
        if isinstance(last_diag, dict) and isinstance(last_diag.get("enablement_policy"), dict):
            enablement_source = str(enablement.get("source") or "").strip().lower()
            enablement_role = str(enablement.get("role") or "").strip().lower()
            if not enablement or enablement_source in {"legacy_runtime", "unavailable"} or not enablement_role:
                enablement = dict(last_diag.get("enablement_policy") or enablement or {})
        if isinstance(last_diag, dict) and isinstance(last_diag.get("route_tunnel_contract"), dict):
            route_tunnel_contract = dict(last_diag.get("route_tunnel_contract") or route_tunnel_contract or {})
        listener_running = bool(process.get("listener_running"))
        managed_alive = bool(process.get("managed_alive"))
        status_text = "disabled"
        summary = "realtime sidecar is disabled"
        session_state = "disabled"
        status_reason = str(enablement.get("reason") or "").strip() or summary
        local_listener_state = "disabled"
        remote_session_state = "disabled"
        transport_ready = False
        if enabled:
            local_listener_state = "ready" if listener_running else "down"
            status_text = "unknown"
            summary = "realtime sidecar is enabled but has no diagnostics yet"
            session_state = "starting"
            status_reason = (
                "sidecar process is running but has not emitted diagnostics yet"
                if managed_alive
                else summary
            )
            remote_session_state = "unknown"
            if isinstance(last_diag, dict):
                last_error = str(last_diag.get("last_error") or "").strip()
                remote_connected_ago_s = last_diag.get("remote_connected_ago_s")
                if not diag_fresh:
                    status_text = "degraded"
                    summary = "sidecar diagnostics are stale"
                    session_state = "stale_diag"
                    status_reason = summary
                    local_listener_state = "stale" if listener_running else "down"
                    remote_session_state = "stale"
                elif last_error:
                    status_text = "degraded"
                    summary = f"sidecar reports transport error: {last_error}"
                    session_state = "remote_connect_failed"
                    status_reason = last_error
                    remote_session_state = "down"
                elif isinstance(remote_connected_ago_s, (int, float)):
                    status_text = "ready"
                    summary = "sidecar remote session is connected"
                    session_state = "remote_ready"
                    status_reason = "remote session is connected"
                    remote_session_state = "ready"
                    transport_ready = True
                else:
                    status_text = "unknown"
                    summary = "sidecar diagnostics do not show an active session"
                    session_state = "starting"
                    status_reason = summary
                    remote_session_state = "unknown"

        def _route_state(kind: str) -> str:
            entry = route_tunnel_contract.get(kind) if isinstance(route_tunnel_contract.get(kind), dict) else {}
            if not enabled:
                return "planned" if entry else "not_owned"
            if bool(entry.get("handoff_ready")):
                return "ready"
            return "planned" if entry else "not_owned"

        def _route_blocker(entry: dict[str, Any]) -> str | None:
            return next((str(item).strip() for item in (entry.get("blockers") or []) if str(item).strip()), None)

        def _handoff_step(step_id: str, title: str, entry: dict[str, Any]) -> dict[str, Any]:
            current_owner = str(entry.get("current_owner") or "").strip().lower()
            planned_owner = str(entry.get("planned_owner") or "").strip().lower()
            handoff_ready = bool(entry.get("handoff_ready"))
            listener_ready = bool(entry.get("listener_ready"))
            blocker = _route_blocker(entry)
            if current_owner == "sidecar" and handoff_ready:
                status_value = "completed"
            elif current_owner == "sidecar" or planned_owner == "sidecar":
                status_value = "in_progress"
            else:
                status_value = "planned"
            return {
                "id": step_id,
                "title": title,
                "status": status_value,
                "active_on_node": current_owner == "sidecar",
                "ready_on_node": current_owner == "sidecar" and handoff_ready,
                "listener_ready": listener_ready,
                "blocker": blocker,
                "delegation_mode": entry.get("delegation_mode"),
                "summary": (
                    "handoff is complete"
                    if status_value == "completed"
                    else (
                        "sidecar local proxy listener is ready, but public ownership cutover is still pending"
                        if listener_ready
                        else blocker or "ownership handoff is not complete yet"
                    )
                ),
            }

        ws_entry = route_tunnel_contract.get("ws") if isinstance(route_tunnel_contract.get("ws"), dict) else {}
        yws_entry = route_tunnel_contract.get("yws") if isinstance(route_tunnel_contract.get("yws"), dict) else {}
        route_ready = _route_state("ws")
        sync_ready = _route_state("yws")
        route_handoff_ready = str(ws_entry.get("current_owner") or "").strip().lower() == "sidecar" and bool(
            ws_entry.get("handoff_ready")
        )
        sync_handoff_ready = str(yws_entry.get("current_owner") or "").strip().lower() == "sidecar" and bool(
            yws_entry.get("handoff_ready")
        )
        scope = {
            "current": (
                ["hub_root_transport"]
                + (["browser_events_ws"] if route_handoff_ready else [])
                + (["browser_yjs_ws"] if sync_handoff_ready else [])
            ),
            "planned_next_boundaries": [
                item
                for item, ready in (
                    ("browser_events_ws", route_handoff_ready),
                    ("browser_yjs_ws", sync_handoff_ready),
                    ("live_media_continuity", False),
                    ("webrtc_signaling", False),
                    ("webrtc_media", False),
                )
                if not ready
            ],
            "deferred_protocol_authority": [
                "yjs_room_state",
                "semantic_channel_authority",
                "webrtc_peer_lifecycle",
            ],
        }
        continuity_blockers: list[str] = []
        for boundary, entry in (("browser_events_ws", ws_entry), ("browser_yjs_ws", yws_entry)):
            blocker = _route_blocker(entry)
            if blocker:
                continuity_blockers.append(f"{boundary}: {blocker}")
        route_tunnel_ready = route_handoff_ready and sync_handoff_ready
        continuity_contract = {
            "required": False,
            "enabled": enabled,
            "member_runtime_update": "allow",
            "hub_runtime_update": "preserve_sidecar",
            "observed_live_topology": None,
            "current_support": "ready" if enabled and route_tunnel_ready else ("planned" if enabled else "disabled"),
            "required_boundaries": [],
            "ready_boundaries": [
                item
                for item, ready in (
                    ("browser_events_ws", route_handoff_ready),
                    ("browser_yjs_ws", sync_handoff_ready),
                )
                if ready
            ],
            "pending_boundaries": [
                item
                for item, ready in (
                    ("browser_events_ws", route_handoff_ready),
                    ("browser_yjs_ws", sync_handoff_ready),
                )
                if not ready
            ],
            "blockers": continuity_blockers,
            "target_behavior": (
                "keep sidecar alive while the hub runtime restarts during live media sessions"
                if enabled
                else "transport sidecar currently isolates only hub_root transport"
            ),
            "reason": "supervisor-side sidecar status does not require runtime reliability API",
        }
        milestones = [
            {
                "id": "hub_root_transport_sidecar",
                "title": "Hub-root transport sidecar",
                "status": "completed",
                "active_on_node": bool(enabled),
                "ready_on_node": bool(transport_ready),
                "summary": "sidecar owns the hub-root transport boundary",
            },
            {
                "id": "supervisor_managed_sidecar",
                "title": "Supervisor-managed sidecar lifecycle",
                "status": "completed",
                "active_on_node": True,
                "ready_on_node": True,
                "summary": "supervisor-managed sidecar lifecycle is implemented",
            },
            _handoff_step("browser_events_ws_handoff", "Browser /ws handoff", ws_entry),
            _handoff_step("browser_yjs_ws_handoff", "Browser /yws handoff", yws_entry),
        ]
        completed_milestones = sum(1 for item in milestones if str(item.get("status") or "") == "completed")
        milestone_total = len(milestones)
        current_milestone = next((item for item in milestones if str(item.get("status") or "") != "completed"), None)
        progress = {
            "target": "first_browser_realtime_tunnel",
            "state": "ready" if milestone_total > 0 and completed_milestones >= milestone_total else "in_progress",
            "completed_milestones": completed_milestones,
            "milestone_total": milestone_total,
            "percent": int((completed_milestones * 100) / milestone_total) if milestone_total else 0,
            "current_milestone": current_milestone.get("id") if isinstance(current_milestone, dict) else None,
            "next_blocker": (
                str(current_milestone.get("blocker") or "").strip() or None
                if isinstance(current_milestone, dict)
                else None
            ),
            "summary": (
                f"{completed_milestones}/{milestone_total} milestones completed "
                "toward first browser realtime sidecar use case"
            ),
            "milestones": milestones,
            "future_targets": ["live_media_continuity", "webrtc_signaling", "webrtc_media"],
        }
        transport_provenance = {
            "local_url": operations.realtime_sidecar_local_url(),
            "diag_path": str(diag_path),
            "session_id": last_diag.get("session_id") if isinstance(last_diag, dict) else None,
            "remote_url": last_diag.get("remote_url") if isinstance(last_diag, dict) else None,
            "loop_policy": last_diag.get("loop_policy") if isinstance(last_diag, dict) else None,
            "loop": last_diag.get("loop") if isinstance(last_diag, dict) else None,
            "active_session": bool(last_diag.get("active_session")) if isinstance(last_diag, dict) else False,
            "local_client_total": int(last_diag.get("local_client_total") or 0) if isinstance(last_diag, dict) else 0,
            "session_open_total": int(last_diag.get("session_open_total") or 0) if isinstance(last_diag, dict) else 0,
            "session_close_total": int(last_diag.get("session_close_total") or 0) if isinstance(last_diag, dict) else 0,
            "remote_connect_total": int(last_diag.get("remote_connect_total") or 0) if isinstance(last_diag, dict) else 0,
            "remote_connect_fail_total": int(last_diag.get("remote_connect_fail_total") or 0) if isinstance(last_diag, dict) else 0,
            "remote_quarantine_total": int(last_diag.get("remote_quarantine_total") or 0) if isinstance(last_diag, dict) else 0,
            "superseded_total": int(last_diag.get("superseded_total") or 0) if isinstance(last_diag, dict) else 0,
            "last_remote_connect_error": last_diag.get("last_remote_connect_error") if isinstance(last_diag, dict) else None,
            "last_remote_connect_error_ago_s": last_diag.get("last_remote_connect_error_ago_s") if isinstance(last_diag, dict) else None,
            "last_remote_disconnect_ago_s": last_diag.get("last_remote_disconnect_ago_s") if isinstance(last_diag, dict) else None,
        }
        return {
            "enabled": enabled,
            "enablement": enablement,
            "phase": "nats_transport_sidecar",
            "transport_owner": "sidecar" if enabled else "runtime",
            "lifecycle_manager": str(route_tunnel_contract.get("lifecycle_manager") or "supervisor"),
            "ownership_boundary": "transport_only",
            "ownership": {
                "owns": ["transport sessions", "transport listeners", "transport relay lifecycle"],
                "must_not_own": ["message semantics", "Yjs document authority", "core update authority"],
            },
            "delegations": {
                "hub_root_transport": bool(enabled),
                "route_tunnel_transport": str(ws_entry.get("current_owner") or "").strip().lower() == "sidecar",
                "sync_transport": str(yws_entry.get("current_owner") or "").strip().lower() == "sidecar",
                "media_transport": False,
            },
            "scope": scope,
            "continuity_contract": continuity_contract,
            "progress": progress,
            "route_tunnel_contract": route_tunnel_contract,
            "status": status_text,
            "summary": summary,
            "session_state": session_state,
            "status_reason": status_reason,
            "local_url": operations.realtime_sidecar_local_url(),
            "diag_path": str(diag_path),
            "diag_age_s": diag_age_s,
            "diag_fresh": diag_fresh,
            "local_listener_state": local_listener_state,
            "remote_session_state": remote_session_state,
            "transport_ready": transport_ready,
            "control_ready": "ready" if transport_ready else ("down" if enabled else "not_applicable"),
            "route_ready": route_ready,
            "sync_ready": sync_ready,
            "media_ready": "not_owned",
            "transport_provenance": transport_provenance,
            "process": process,
            "last_diag": last_diag,
            "role": role,
        }


    def runtime_state_payload(
        self,
        manager: Any,
        operations: SupervisorStatusOperations,
        *,
        runtime_api_timeout: float = 0.75,
    ) -> dict[str, Any]:
        proc = manager._proc
        slot_snapshot = operations.core_slot_status()
        current_slot = str(slot_snapshot.get("active_slot") or operations.active_slot() or "").strip().upper() or None
        previous_slot = str(slot_snapshot.get("previous_slot") or "").strip().upper() or None
        active_manifest = operations.active_slot_manifest()
        update_status = operations.read_core_update_status()
        update_attempt = operations.read_update_attempt()
        root_promotion_required, bootstrap_update = operations.resolved_root_promotion_requirement(active_manifest)
        slot_structure = operations.validate_slot_structure(current_slot) if current_slot else None
        active_runtime_port = manager.slot_runtime_port(current_slot)
        active_runtime_url = manager.slot_runtime_base_url(current_slot)
        managed = operations.proc_details(proc, cwd_hint=manager._managed_runtime_cwd)
        managed_pid = managed["managed_pid"]
        managed_alive = bool(managed["managed_alive"])
        managed_cmdline = managed["managed_cmdline"]
        managed_executable = managed["managed_executable"]
        managed_cwd = managed["managed_cwd"]
        managed_runtime_url = manager._managed_proc_base_url(proc)
        listener_running = bool(managed_alive) and operations.listener_running(manager.runtime_host, active_runtime_port)
        api_ready = listener_running and operations.runtime_api_ready(
            active_runtime_url,
            token=manager.token,
            timeout=runtime_api_timeout,
        )
        runtime_state = "stopped"
        if manager._stopping:
            runtime_state = "stopping"
        elif managed_alive and api_ready:
            runtime_state = "ready"
        elif managed_alive and listener_running:
            runtime_state = "starting"
        elif managed_alive:
            runtime_state = "spawned"
        expected_executable, expected_cwd, managed_matches_active_slot = manager._managed_runtime_slot_expectations(
            manifest=active_manifest,
            managed_executable=managed_executable,
            managed_cwd=managed_cwd,
        )
        warm_switch = manager._warm_switch_state(
            current_slot=current_slot,
            update_status=update_status,
            update_attempt=update_attempt,
            managed_pid=managed_pid,
        )
        candidate_slot = str(manager._candidate_slot or warm_switch.get("candidate_slot") or "").strip().upper() or None
        candidate_manifest = operations.read_slot_manifest(candidate_slot) if candidate_slot else None
        candidate_runtime_port = manager.slot_runtime_port(candidate_slot) if candidate_slot else None
        candidate_runtime_url = manager.slot_runtime_base_url(candidate_slot) if candidate_slot else None
        candidate_managed = operations.proc_details(manager._candidate_proc, cwd_hint=manager._candidate_runtime_cwd)
        candidate_managed_pid = candidate_managed["managed_pid"]
        candidate_managed_alive = bool(candidate_managed["managed_alive"])
        candidate_managed_cmdline = candidate_managed["managed_cmdline"]
        candidate_managed_executable = candidate_managed["managed_executable"]
        candidate_managed_cwd = candidate_managed["managed_cwd"]
        candidate_listener_running = bool(candidate_managed_alive and candidate_runtime_port) and operations.listener_running(
            manager.runtime_host,
            int(candidate_runtime_port or 0),
        )
        candidate_runtime_api_ready = bool(candidate_listener_running and candidate_runtime_url) and operations.runtime_api_ready(
            str(candidate_runtime_url),
            token=manager.token,
            timeout=runtime_api_timeout,
        )
        candidate_runtime_state = None
        if candidate_slot:
            candidate_runtime_state = "stopped"
            if candidate_managed_alive and candidate_runtime_api_ready:
                candidate_runtime_state = "ready"
            elif candidate_managed_alive and candidate_listener_running:
                candidate_runtime_state = "starting"
            elif candidate_managed_alive:
                candidate_runtime_state = "spawned"
        candidate_expected_executable = None
        candidate_expected_cwd = None
        candidate_matches_candidate_slot = None
        if isinstance(candidate_manifest, dict):
            argv = candidate_manifest.get("argv")
            if isinstance(argv, list) and argv:
                candidate_expected_executable = str(argv[0] or "").strip() or None
            candidate_expected_cwd = str(candidate_manifest.get("cwd") or "").strip() or None
        if candidate_slot and (candidate_expected_executable or candidate_expected_cwd):
            candidate_matches_candidate_slot = True
            if (
                candidate_expected_executable
                and str(candidate_managed_executable or "").strip() != candidate_expected_executable
            ):
                candidate_matches_candidate_slot = False
            if candidate_expected_cwd and str(candidate_managed_cwd or "").strip() != candidate_expected_cwd:
                candidate_matches_candidate_slot = False
        candidate_memory_guard = manager._candidate_memory_guard_snapshot(
            {
                "candidate_managed_pid": candidate_managed_pid,
                "candidate_managed_alive": candidate_managed_alive,
            }
        )
        return {
            "ok": True,
            "supervisor_pid": os.getpid(),
            "supervisor_url": operations.supervisor_base_url(),
            "sidecar": manager._sidecar_status_payload(),
            "runtime_url": active_runtime_url,
            "runtime_host": manager.runtime_host,
            "runtime_port": active_runtime_port,
            "managed_slot": manager._managed_slot,
            "managed_runtime_url": managed_runtime_url,
            "managed_runtime_port": manager._managed_runtime_port,
            "runtime_instance_id": manager._managed_runtime_instance_id,
            "transition_role": manager._managed_transition_role if manager._managed_runtime_instance_id else None,
            "active_slot": current_slot,
            "previous_slot": previous_slot,
            "desired_running": bool(manager._desired_running),
            "stopping": bool(manager._stopping),
            "managed_pid": managed_pid,
            "managed_alive": managed_alive,
            "listener_running": listener_running,
            "runtime_api_ready": api_ready,
            "runtime_state": runtime_state,
            "managed_cmdline": managed_cmdline,
            "managed_executable": managed_executable,
            "managed_cwd": managed_cwd,
            "managed_start_reason": manager._managed_start_reason,
            "hub_root_watchdog": manager._hub_root_watchdog_state_payload(),
            "member_hub_watchdog": manager._member_hub_watchdog_state_payload(),
            "required_upstream_link": manager._required_upstream_link_state_payload(),
            "expected_managed_executable": expected_executable,
            "expected_managed_cwd": expected_cwd,
            "managed_matches_active_slot": managed_matches_active_slot,
            **warm_switch,
            "candidate_slot": candidate_slot,
            "candidate_runtime_url": candidate_runtime_url,
            "candidate_runtime_port": candidate_runtime_port,
            "candidate_runtime_instance_id": manager._candidate_runtime_instance_id,
            "candidate_transition_role": (
                manager._candidate_transition_role
                if manager._candidate_runtime_instance_id
                else str(warm_switch.get("candidate_transition_role") or "").strip() or None
            ),
            "candidate_managed_pid": candidate_managed_pid,
            "candidate_managed_alive": candidate_managed_alive,
            "candidate_listener_running": candidate_listener_running,
            "candidate_runtime_api_ready": candidate_runtime_api_ready,
            "candidate_runtime_state": candidate_runtime_state,
            "candidate_managed_cmdline": candidate_managed_cmdline,
            "candidate_managed_executable": candidate_managed_executable,
            "candidate_managed_cwd": candidate_managed_cwd,
            "candidate_start_reason": manager._candidate_start_reason,
            "candidate_expected_managed_executable": candidate_expected_executable,
            "candidate_expected_managed_cwd": candidate_expected_cwd,
            "candidate_matches_candidate_slot": candidate_matches_candidate_slot,
            "candidate_memory_guard": candidate_memory_guard,
            "active_manifest": active_manifest,
            "root_promotion_required": root_promotion_required,
            "bootstrap_update": bootstrap_update,
            "slot_structure": slot_structure,
            "restart_count": int(manager._restart_count),
            "retired_runtime_drain_pending": len(manager._retired_runtime_tasks),
            "preserve_children_on_supervisor_restart": bool(manager._service_restart_pending),
            "last_start_at": manager._last_start_at,
            "last_stop_reason": manager._last_stop_reason,
            "candidate_last_stop_reason": manager._candidate_last_stop_reason,
            "last_exit_at": manager._last_exit_at,
            "last_exit_code": manager._last_exit_code,
            "last_error": manager._last_error,
            "monitor": {
                "running": bool(manager._monitor_task is not None and not manager._monitor_task.done()),
                "loop_started_at": manager._monitor_loop_started_at,
                "last_iteration_at": manager._monitor_last_iteration_at,
                "last_failure_at": manager._monitor_last_failure_at,
                "last_failure": manager._monitor_last_failure,
                "consecutive_failure_total": int(manager._monitor_failure_total),
                "recovery_total": int(manager._monitor_recovery_total),
            },
            "runtime_self_heal": manager._runtime_self_heal_status_payload(),
            "updated_at": time.time(),
        }

