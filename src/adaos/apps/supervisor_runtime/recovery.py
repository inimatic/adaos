from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeRecoveryFacts:
    process_running: bool
    stopping: bool
    desired_running: bool
    update_state: str
    update_phase: str
    current_slot: str | None
    managed_executable: str | None
    managed_cwd: str | None
    expected_executable: str | None
    expected_cwd: str | None
    managed_matches_active_slot: bool | None
    runtime_host: str
    runtime_port: int
    runtime_url: str
    listener_running: bool
    runtime_api_ready: bool
    now: float
    unhealthy_kind: str | None
    unhealthy_since: float | None
    last_start_at: float | None
    listener_startup_grace_sec: float
    listener_restart_timeout_sec: float
    api_restart_timeout_sec: float


@dataclass(frozen=True)
class RuntimeRecoveryEvaluation:
    unhealthy_kind: str | None
    unhealthy_since: float | None
    decision: dict[str, Any] | None


@dataclass(frozen=True)
class RuntimeRecoveryOperations:
    hub_root_watchdog_cooldown_sec: Any
    hub_root_watchdog_enabled: Any
    hub_root_watchdog_reset_degraded_route_enabled: Any
    member_hub_watchdog_cooldown_sec: Any
    member_hub_watchdog_enabled: Any


class RuntimeRecoveryPolicy:
    """Decide whether an unhealthy managed runtime should be restarted."""

    def __init__(self) -> None:
        self.unhealthy_since: float | None = None
        self.unhealthy_kind: str | None = None
        self.last_decision: dict[str, Any] | None = None
        self.last_evidence: dict[str, Any] | None = None

    def clear_unhealthy_window(self) -> None:
        self.unhealthy_since = None
        self.unhealthy_kind = None

    def record_evaluation(self, evaluation: RuntimeRecoveryEvaluation) -> dict[str, Any] | None:
        self.unhealthy_kind = evaluation.unhealthy_kind
        self.unhealthy_since = evaluation.unhealthy_since
        if evaluation.decision is not None:
            self.last_decision = dict(evaluation.decision)
        return evaluation.decision

    def record_evidence(self, evidence: dict[str, Any] | None) -> None:
        self.last_evidence = dict(evidence) if isinstance(evidence, dict) else None

    @staticmethod
    def evaluate(facts: RuntimeRecoveryFacts) -> RuntimeRecoveryEvaluation:
        if not facts.process_running or facts.stopping or not facts.desired_running:
            return RuntimeRecoveryEvaluation(None, None, None)

        if facts.managed_matches_active_slot is False:
            mismatch_detail = (
                facts.expected_executable or facts.expected_cwd or facts.current_slot or "active slot"
            )
            return RuntimeRecoveryEvaluation(
                None,
                None,
                {
                    "reason": "supervisor.runtime.slot_mismatch",
                    "message": (
                        f"active runtime process does not match the active slot {facts.current_slot or '-'}"
                        f"; expected {mismatch_detail} and will be restarted"
                    ),
                    "active_slot": facts.current_slot,
                    "managed_executable": facts.managed_executable,
                    "managed_cwd": facts.managed_cwd,
                    "expected_managed_executable": facts.expected_executable,
                    "expected_managed_cwd": facts.expected_cwd,
                },
            )

        if facts.update_state == "applying" and facts.update_phase == "apply":
            return RuntimeRecoveryEvaluation(None, None, None)
        if facts.listener_running and facts.runtime_api_ready:
            return RuntimeRecoveryEvaluation(None, None, None)

        unhealthy_kind = "api_unready" if facts.listener_running else "listener_lost"
        if facts.unhealthy_kind != unhealthy_kind:
            return RuntimeRecoveryEvaluation(unhealthy_kind, facts.now, None)

        unhealthy_since = float(facts.unhealthy_since or facts.now)
        if facts.last_start_at is not None:
            unhealthy_since = max(unhealthy_since, float(facts.last_start_at))
        if unhealthy_kind == "listener_lost" and facts.last_start_at is not None:
            runtime_age = max(0.0, facts.now - float(facts.last_start_at))
            if runtime_age < facts.listener_startup_grace_sec:
                return RuntimeRecoveryEvaluation(unhealthy_kind, facts.unhealthy_since, None)
        timeout_sec = (
            facts.api_restart_timeout_sec
            if unhealthy_kind == "api_unready"
            else facts.listener_restart_timeout_sec
        )
        if (facts.now - unhealthy_since) < timeout_sec:
            return RuntimeRecoveryEvaluation(unhealthy_kind, facts.unhealthy_since, None)

        target = (
            facts.runtime_url
            if unhealthy_kind == "api_unready"
            else f"http://{facts.runtime_host}:{facts.runtime_port}"
        )
        return RuntimeRecoveryEvaluation(
            unhealthy_kind,
            facts.unhealthy_since,
            {
                "reason": f"supervisor.runtime.{unhealthy_kind}",
                "message": (
                    f"active runtime stayed {unhealthy_kind.replace('_', ' ')} for {timeout_sec:.0f}s"
                    f" at {target}; restarting"
                ),
                "runtime_port": facts.runtime_port,
                "runtime_url": facts.runtime_url,
                "listener_running": facts.listener_running,
                "runtime_api_ready": facts.runtime_api_ready,
                "timeout_sec": timeout_sec,
            },
        )

    def hub_root_watchdog_decision(
        self,
        manager: Any,
        operations: RuntimeRecoveryOperations,
        runtime: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not operations.hub_root_watchdog_enabled():
            manager._hub_root_watchdog_last_state = "disabled"
            manager._hub_root_watchdog_last_reason = "watchdog disabled"
            return None
        if manager._stopping or not manager._desired_running:
            return None
        current_time = time.time() if now is None else float(now)
        node = runtime.get("node") if isinstance(runtime.get("node"), dict) else {}
        role = str(node.get("role") or manager._sidecar_role() or "").strip().lower()
        if role != "hub":
            manager._hub_root_watchdog_last_state = "not_applicable"
            manager._hub_root_watchdog_last_reason = f"role={role or '-'}"
            return None

        channel_state = manager._hub_root_channel_state(runtime)
        root_status = str(channel_state.get("root_control_status") or "")
        route_status = str(channel_state.get("route_status") or "")
        hub_root_status = str(channel_state.get("hub_root_status") or "")
        hub_root_state = str(channel_state.get("hub_root_state") or "")
        hub_root_browser_status = str(channel_state.get("hub_root_browser_status") or "")
        hub_root_browser_state = str(channel_state.get("hub_root_browser_state") or "")
        required_link = manager._required_upstream_link_snapshot(runtime=runtime, role=role)
        sidecar_enabled = bool(required_link.get("sidecar_enabled"))
        transport_owner = str(required_link.get("current_owner") or "").strip().lower() or ("sidecar" if sidecar_enabled else "runtime")
        root_down = manager._hub_root_channel_down(channel_state)
        route_degraded = manager._hub_root_route_degraded(channel_state)
        route_degraded_reset_enabled = operations.hub_root_watchdog_reset_degraded_route_enabled()
        root_probe = (
            manager._hub_root_root_probe_last_result
            if isinstance(manager._hub_root_root_probe_last_result, dict)
            else {}
        )
        root_probe_state = str(root_probe.get("state") or "").strip().lower()
        action = (
            "runtime_route_reset"
            if route_degraded and not root_down
            else "sidecar_restart"
            if transport_owner == "sidecar"
            else "runtime_reconnect"
        )

        if (
            root_down
            and root_probe_state == "ready"
            and not manager._root_probe_reports_hub_root_unready(root_probe)
        ):
            age = root_probe.get("age_sec")
            age_text = f"{float(age):.1f}s" if isinstance(age, (int, float)) else "-"
            manager._hub_root_watchdog_last_state = "root_perspective_ready"
            manager._hub_root_watchdog_last_reason = (
                f"runtime reports hub-root down, but root has a fresh hub control report (age={age_text})"
            )
            return None

        if not root_down and not route_degraded:
            state = (
                root_status
                or hub_root_status
                or hub_root_state
                or route_status
                or hub_root_browser_status
                or hub_root_browser_state
                or "unknown"
            )
            manager._hub_root_watchdog_last_state = state
            manager._hub_root_watchdog_last_reason = "hub-root and browser route are not down"
            return None

        if route_degraded and not root_down and not route_degraded_reset_enabled:
            state = hub_root_browser_status or hub_root_browser_state or route_status or "route_degraded"
            manager._hub_root_watchdog_last_state = state
            manager._hub_root_watchdog_last_reason = "browser route degraded; preserving active runtime-owned tunnels"
            return None

        cooldown = operations.hub_root_watchdog_cooldown_sec()
        last_reconnect = manager._hub_root_watchdog_last_reconnect_at
        if last_reconnect is not None and (current_time - float(last_reconnect)) < cooldown:
            manager._hub_root_watchdog_last_state = "cooldown"
            manager._hub_root_watchdog_last_reason = f"hub-root down but reconnect cooldown is active ({cooldown:.0f}s)"
            return None

        reason = (
            f"root_control={root_status or '-'} "
            f"hub_root={hub_root_status or hub_root_state or '-'} "
            f"route={route_status or '-'} "
            f"hub_root_browser={hub_root_browser_status or hub_root_browser_state or '-'}"
        )
        return {
            "reason": "supervisor.hub_root.watchdog_reconnect",
            "message": f"hub-root route watchdog requesting {action} ({reason})",
            "action": action,
            "transport_owner": transport_owner,
            "root_control_status": root_status or None,
            "route_status": route_status or None,
            "hub_root_status": hub_root_status or None,
            "hub_root_state": hub_root_state or None,
            "hub_root_browser_status": hub_root_browser_status or None,
            "hub_root_browser_state": hub_root_browser_state or None,
            "last_event": channel_state.get("last_event"),
            "last_summary": channel_state.get("last_summary"),
            "channel_before": channel_state,
            "required_upstream_link": required_link,
            "root_perspective_probe": dict(root_probe),
        }


    def member_hub_watchdog_decision(
        self,
        manager: Any,
        operations: RuntimeRecoveryOperations,
        runtime: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not operations.member_hub_watchdog_enabled():
            manager._member_hub_watchdog_last_state = "disabled"
            manager._member_hub_watchdog_last_reason = "watchdog disabled"
            return None
        if manager._stopping or not manager._desired_running:
            return None
        current_time = time.time() if now is None else float(now)
        node = runtime.get("node") if isinstance(runtime.get("node"), dict) else {}
        role = str(node.get("role") or manager._sidecar_role() or "").strip().lower()
        if role != "member":
            manager._member_hub_watchdog_last_state = "not_applicable"
            manager._member_hub_watchdog_last_reason = f"role={role or '-'}"
            return None

        channel_state = manager._member_hub_channel_state(runtime)
        required_link = manager._required_upstream_link_snapshot(runtime=runtime, role=role)
        transition_state = str(channel_state.get("transition_state") or "").strip().lower()
        if transition_state in {"waiting_restart", "restarting", "paused_for_update"}:
            manager._member_hub_watchdog_last_state = transition_state
            manager._member_hub_watchdog_last_reason = (
                str(channel_state.get("transition_reason") or "").strip() or transition_state
            )
            return None

        if bool(channel_state.get("connected")):
            manager._member_hub_watchdog_last_state = "ready"
            manager._member_hub_watchdog_last_reason = "member-hub link is connected"
            return None

        cooldown = operations.member_hub_watchdog_cooldown_sec()
        last_reconnect = manager._member_hub_watchdog_last_reconnect_at
        if last_reconnect is not None and (current_time - float(last_reconnect)) < cooldown:
            manager._member_hub_watchdog_last_state = "cooldown"
            manager._member_hub_watchdog_last_reason = (
                f"member-hub down but reconnect cooldown is active ({cooldown:.0f}s)"
            )
            return None

        route_status = str(channel_state.get("route_status") or "").strip().lower() or "-"
        member_state = str(channel_state.get("member_state") or "").strip().lower() or "-"
        transport_owner = str(required_link.get("current_owner") or "").strip().lower() or "runtime"
        continuity_mode = str(required_link.get("continuity_mode") or "").strip().lower() or "runtime_bound"
        handoff_state = str(required_link.get("handoff_state") or "").strip().lower() or "unknown"
        handoff_ready = bool(required_link.get("handoff_ready"))
        recovery_policy = (
            dict(required_link.get("recovery_policy"))
            if isinstance(required_link.get("recovery_policy"), dict)
            else {}
        )
        reason = (
            f"route={route_status} "
            f"member_state={member_state} "
            f"assessment={str(channel_state.get('assessment_state') or '-')} "
            f"hub_url={str(channel_state.get('hub_url') or '-')} "
            f"owner={transport_owner} "
            f"handoff={handoff_state} "
            f"continuity={continuity_mode}"
        )
        return {
            "reason": "supervisor.member_hub.watchdog_reconnect",
            "message": f"member-hub watchdog requesting runtime_reconnect ({reason})",
            "action": "runtime_reconnect",
            "transport_owner": transport_owner,
            "continuity_mode": continuity_mode,
            "handoff_state": handoff_state,
            "handoff_ready": handoff_ready,
            "recovery_policy": recovery_policy,
            "route_status": channel_state.get("route_status"),
            "hub_member_status": channel_state.get("hub_member_status"),
            "member_state": channel_state.get("member_state"),
            "assessment_state": channel_state.get("assessment_state"),
            "assessment_reason": channel_state.get("assessment_reason"),
            "transition_state": channel_state.get("transition_state"),
            "transition_reason": channel_state.get("transition_reason"),
            "last_error": channel_state.get("last_error"),
            "last_close_reason": channel_state.get("last_close_reason"),
            "channel_before": channel_state,
            "required_upstream_link": required_link,
        }

