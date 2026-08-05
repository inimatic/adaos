from __future__ import annotations

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
