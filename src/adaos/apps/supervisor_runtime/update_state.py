from __future__ import annotations

from typing import Any, Mapping


class UpdateStateMachine:
    """Classify supervisor update states without performing persistence or I/O."""

    TERMINAL_STATES = frozenset(
        {"failed", "validated", "succeeded", "rolled_back", "expired", "cancelled", "idle"}
    )

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
