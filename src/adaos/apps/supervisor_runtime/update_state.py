from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping


class UpdateStateMachine:
    """Classify supervisor update states without performing persistence or I/O."""

    TERMINAL_STATES = frozenset(
        {"failed", "validated", "succeeded", "rolled_back", "expired", "cancelled", "idle"}
    )

    def __init__(self) -> None:
        self.task: asyncio.Task[Any] | None = None
        self.cancel_mode: str | None = None
        self._write_status: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self._write_attempt: Callable[[dict[str, Any]], Any] | None = None

    def bind_persistence(
        self,
        *,
        write_status: Callable[[dict[str, Any]], dict[str, Any]],
        write_attempt: Callable[[dict[str, Any]], Any],
    ) -> None:
        self._write_status = write_status
        self._write_attempt = write_attempt

    def persist_transition(
        self,
        *,
        status_payload: dict[str, Any],
        attempt_payload: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """Persist a status and its causally-linked attempt through one owner."""
        if self._write_status is None or self._write_attempt is None:
            raise RuntimeError("update persistence is not bound")
        status = self._write_status(dict(status_payload))
        attempt = attempt_payload(status) if callable(attempt_payload) else dict(attempt_payload)
        self._write_attempt(attempt)
        return status

    def task_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def start_task(
        self,
        task_name: str,
        worker: Callable[[], Awaitable[Any]],
    ) -> asyncio.Task[Any]:
        if self.task_running():
            raise RuntimeError("supervisor update task is already running")
        self.cancel_mode = None
        self.task = asyncio.create_task(worker(), name=task_name)
        return self.task

    async def cancel_task(self, *, mode: str) -> bool:
        task = self.task
        if task is None or task.done():
            self.task = None
            self.cancel_mode = None
            return False
        self.cancel_mode = str(mode or "cancelled")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.task = None
        self.cancel_mode = None
        return True

    def release_finished_task(self, task: asyncio.Task[Any] | None = None) -> None:
        current = self.task
        if current is None:
            self.cancel_mode = None
            return
        if task is not None and current is not task:
            return
        if current.done():
            self.task = None
            self.cancel_mode = None

    @staticmethod
    def state(payload: Mapping[str, Any] | None) -> str:
        return str((payload or {}).get("state") or "").strip().lower()

    @staticmethod
    def phase(payload: Mapping[str, Any] | None) -> str:
        return str((payload or {}).get("phase") or "").strip().lower()

    def is_root_restart_pending_attempt(self, payload: Mapping[str, Any] | None) -> bool:
        return self.state(payload) == "awaiting_root_restart"

    def is_root_restart_completed_status(self, payload: Mapping[str, Any] | None) -> bool:
        if not isinstance(payload, Mapping):
            return False
        return (
            self.state(payload) == "succeeded"
            and self.phase(payload) == "validate"
            and float(payload.get("root_restart_completed_at") or 0.0) > 0.0
        )

    def is_root_promotion_pending_status(self, payload: Mapping[str, Any] | None) -> bool:
        return self.state(payload) == "validated" and self.phase(payload) == "root_promotion_pending"

    def is_root_restart_pending_status(self, payload: Mapping[str, Any] | None) -> bool:
        return self.state(payload) == "succeeded" and self.phase(payload) == "root_promoted"

    def is_terminal(self, payload: Mapping[str, Any] | None) -> bool:
        if not isinstance(payload, Mapping):
            return False
        if self.is_root_promotion_pending_status(payload) or self.is_root_restart_pending_status(payload):
            return False
        return self.state(payload) in self.TERMINAL_STATES

    def transition_in_progress(
        self,
        status: Mapping[str, Any] | None,
        attempt: Mapping[str, Any] | None,
    ) -> bool:
        state = self.state(status)
        phase = self.phase(status)
        attempt_state = self.state(attempt)
        if attempt_state in {"active", "awaiting_root_restart"}:
            return True
        if state in {"preparing", "countdown", "draining", "stopping", "restarting", "applying"}:
            return True
        return (state, phase) in {
            ("validated", "root_promotion_pending"),
            ("succeeded", "root_promoted"),
        }

    @staticmethod
    def transition_timed_out(*, status_age: float, transition_age: float, timeout_sec: float) -> bool:
        ages = [age for age in (status_age, transition_age) if age > 0.0]
        return bool(ages) and min(ages) >= float(timeout_sec)

    @staticmethod
    def root_promotion_owner_instance(payload: Mapping[str, Any] | None) -> str:
        data = payload if isinstance(payload, Mapping) else {}
        return str(
            data.get("root_promotion_supervisor_instance_id")
            or data.get("restart_requested_by_instance_id")
            or ""
        ).strip()

    def crossed_supervisor_generation(
        self,
        payload: Mapping[str, Any] | None,
        *,
        current_instance_id: str,
    ) -> bool:
        owner = self.root_promotion_owner_instance(payload)
        return not owner or owner != current_instance_id

    def runtime_ready_for_boot_finalize(
        self,
        status: Mapping[str, Any] | None,
        runtime: Mapping[str, Any] | None,
        *,
        current_instance_id: str,
    ) -> bool:
        if not isinstance(status, Mapping) or not isinstance(runtime, Mapping):
            return False
        state = self.state(status)
        phase = self.phase(status)
        if (state, phase) in {("succeeded", "validate"), ("validated", "root_promotion_pending")}:
            return False
        if state == "succeeded" and phase == "root_promoted" and not self.crossed_supervisor_generation(
            status,
            current_instance_id=current_instance_id,
        ):
            return False
        finalizable = state in {"restarting", "applying", "validated"} or (
            state == "succeeded" and phase in {"", "apply", "launch", "shutdown", "root_promoted"}
        )
        if not finalizable:
            return False
        runtime_state = self.state({"state": runtime.get("runtime_state")})
        runtime_ready = runtime_state == "ready" or (
            bool(runtime.get("listener_running")) and bool(runtime.get("runtime_api_ready"))
        )
        if not runtime_ready:
            return False
        target_slot = str(status.get("target_slot") or "").strip().upper()
        manifest = status.get("manifest")
        if not target_slot and isinstance(manifest, Mapping):
            target_slot = str(manifest.get("slot") or "").strip().upper()
        active_runtime_slot = str(runtime.get("active_slot") or "").strip().upper()
        return not (target_slot and active_runtime_slot and target_slot != active_runtime_slot)

    @staticmethod
    def target_version_matches(left: Any, right: Any) -> bool:
        a = str(left or "").strip()
        b = str(right or "").strip()
        if not a or not b:
            return False
        return a == b or (len(a) >= 7 and len(b) >= 7 and (a.startswith(b) or b.startswith(a)))

    @staticmethod
    def looks_like_git_sha(value: Any) -> bool:
        text = str(value or "").strip()
        return 7 <= len(text) <= 40 and all(ch in "0123456789abcdefABCDEF" for ch in text)

    def transition_request_has_resolved_target(self, request: Mapping[str, Any] | None) -> bool:
        req = request if isinstance(request, Mapping) else {}
        if str(req.get("action") or "update").strip().lower() != "update":
            return True
        return bool(str(req.get("target_rev") or "").strip()) or self.looks_like_git_sha(
            req.get("target_version")
        )

    def manifest_matches_target_version(
        self,
        manifest: Mapping[str, Any] | None,
        target_version: Any,
    ) -> bool:
        expected = str(target_version or "").strip()
        if not expected:
            return True
        data = manifest if isinstance(manifest, Mapping) else {}
        return any(
            self.target_version_matches(expected, data.get(key))
            for key in ("target_version", "build_version", "git_commit", "git_short_commit")
        )


class UpdateAttemptStore:
    """Normalize and persist the supervisor-owned update-attempt contract."""

    CONTRACT_VERSION = "1"

    @staticmethod
    def epoch(value: Any) -> float:
        try:
            return float(value or 0.0)
        except Exception:
            return 0.0

    def normalize(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        source = dict(payload)
        epoch = self.epoch
        normalized = {
            "contract_version": str(
                source.get("contract_version") or self.CONTRACT_VERSION
            ),
            "authority": str(source.get("authority") or "supervisor"),
            "state": str(source.get("state") or "").strip().lower() or None,
            "action": str(source.get("action") or "").strip().lower() or None,
            "requested_at": epoch(source.get("requested_at")) or None,
            "transitioned_at": epoch(source.get("transitioned_at")) or None,
            "scheduled_for": epoch(source.get("scheduled_for")) or None,
            "updated_at": epoch(source.get("updated_at")) or None,
            "completed_at": epoch(source.get("completed_at")) or None,
            "countdown_sec": epoch(source.get("countdown_sec")) or None,
            "drain_timeout_sec": epoch(source.get("drain_timeout_sec")) or None,
            "signal_delay_sec": epoch(source.get("signal_delay_sec")) or None,
            "target_rev": str(source.get("target_rev") or "").strip() or None,
            "target_version": str(source.get("target_version") or "").strip() or None,
            "reason": str(source.get("reason") or "").strip() or None,
            "planned_reason": str(source.get("planned_reason") or "").strip() or None,
            "completion_reason": str(source.get("completion_reason") or "").strip()
            or None,
            "accepted": bool(source.get("accepted")),
            "awaiting_restart": bool(source.get("awaiting_restart")),
            "restart_required": bool(source.get("restart_required")),
            "restart_mode": str(source.get("restart_mode") or "").strip() or None,
            "restart_requested_at": epoch(source.get("restart_requested_at")) or None,
            "restart_requested_by_instance_id": str(
                source.get("restart_requested_by_instance_id") or ""
            ).strip()
            or None,
            "restart_requested_by_pid": int(epoch(source.get("restart_requested_by_pid")))
            or None,
            "restart_requested_by_started_at": epoch(
                source.get("restart_requested_by_started_at")
            )
            or None,
            "root_promotion_supervisor_instance_id": str(
                source.get("root_promotion_supervisor_instance_id") or ""
            ).strip()
            or None,
            "root_promotion_supervisor_pid": int(
                epoch(source.get("root_promotion_supervisor_pid"))
            )
            or None,
            "root_promotion_supervisor_started_at": epoch(
                source.get("root_promotion_supervisor_started_at")
            )
            or None,
            "min_update_period_sec": epoch(source.get("min_update_period_sec")) or None,
            "subsequent_transition": bool(source.get("subsequent_transition")),
            "subsequent_transition_requested_at": epoch(
                source.get("subsequent_transition_requested_at")
            )
            or None,
            "candidate_prewarm_state": str(
                source.get("candidate_prewarm_state") or ""
            ).strip()
            or None,
            "candidate_prewarm_message": str(
                source.get("candidate_prewarm_message") or ""
            ).strip()
            or None,
            "candidate_prewarm_ready_at": epoch(
                source.get("candidate_prewarm_ready_at")
            )
            or None,
            "candidate_prewarm_deferral_count": max(
                0, int(epoch(source.get("candidate_prewarm_deferral_count")))
            ),
            "candidate_prewarm_max_deferrals": max(
                0, int(epoch(source.get("candidate_prewarm_max_deferrals")))
            ),
            "prepare_lease_path": str(source.get("prepare_lease_path") or "").strip()
            or None,
            "prepare_lease_token": str(source.get("prepare_lease_token") or "").strip()
            or None,
            "prepare_timeout_sec": epoch(source.get("prepare_timeout_sec")) or None,
            "subsequent_transition_request": dict(
                source.get("subsequent_transition_request") or {}
            )
            if isinstance(source.get("subsequent_transition_request"), dict)
            else None,
            "last_status": dict(source.get("last_status") or {})
            if isinstance(source.get("last_status"), dict)
            else {},
        }
        if normalized["updated_at"] is None:
            normalized["updated_at"] = time.time()
        return normalized

    def read(
        self,
        path: Path,
        *,
        read_json: Callable[[Path], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        return self.normalize(read_json(path))

    def write(
        self,
        path: Path,
        payload: dict[str, Any],
        *,
        write_json: Callable[[Path, dict[str, Any]], None],
    ) -> dict[str, Any]:
        normalized = self.normalize(payload)
        if not isinstance(normalized, dict):
            raise ValueError("update attempt payload must be a dict")
        write_json(path, normalized)
        return normalized

    @classmethod
    def status_updated_at(cls, payload: Mapping[str, Any]) -> float:
        for key in ("updated_at", "validated_at", "finished_at", "started_at"):
            value = cls.epoch(payload.get(key))
            if value > 0.0:
                return value
        return 0.0

    @classmethod
    def transition_at(cls, payload: Mapping[str, Any]) -> float:
        for key in (
            "transitioned_at",
            "scheduled_for",
            "requested_at",
            "updated_at",
            "created_at",
        ):
            value = cls.epoch(payload.get(key))
            if value > 0.0:
                return value
        return 0.0
