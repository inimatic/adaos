from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir
from adaos.services.skill.setup_plan import SetupExecutionRequest, execute_via_skill_manager


SETUP_OPERATION_SCHEMA = "adaos.skill.setup_operation.v1"
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / "skill.setup_operation.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


class SetupOperationError(RuntimeError):
    pass


class SetupOperationService:
    """Durable, approval-gated setup execution without implicit command retry."""

    def __init__(self, *, state_dir: Path | None = None) -> None:
        self.state_dir = Path(state_dir or current_state_dir())

    @property
    def root(self) -> Path:
        path = self.state_dir / "skill_setup_operations"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.root / ".operations.lock"

    def create(
        self,
        *,
        skill_id: str,
        release_digest: str,
        plan_digest: str,
        webspace_id: str,
        manager: Any,
        dry_run: bool = False,
        pending_action_publisher: Callable[..., Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        status = manager.runtime_status(skill_id)
        if not bool(status.get("ready", True)) or bool(status.get("deactivated")):
            raise SetupOperationError("setup can be requested only after successful skill activation")
        probe = SetupExecutionRequest(
            skill_id=skill_id,
            release_digest=release_digest,
            plan_digest=plan_digest,
            approval_id="pending",
            approved_by="pending",
            webspace_id=webspace_id,
            dry_run=dry_run,
        )
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            for existing in self.list():
                if existing.get("idempotency_key") == probe.idempotency_key:
                    return {"ok": True, "duplicate": True, "operation": existing}
            operation_id = f"setupop.{new_id()}"
            now = _now()
            operation = {
                "schema": SETUP_OPERATION_SCHEMA,
                "operation_id": operation_id,
                "idempotency_key": probe.idempotency_key,
                "status": "approval_pending",
                "skill_id": skill_id,
                "release_digest": release_digest,
                "plan_digest": plan_digest,
                "webspace_id": webspace_id,
                "dry_run": bool(dry_run),
                "pending_action_id": f"pa.{operation_id}",
                "approval": None,
                "attempts": 0,
                "result": None,
                "error": None,
                "recovery": None,
                "logs": [{"at": now, "event": "setup.requested"}],
                "created_at": now,
                "updated_at": now,
            }
            self._write(operation)
        publisher = pending_action_publisher
        if publisher is None:
            from adaos.services.pending_actions import publish_pending_action

            publisher = publish_pending_action
        try:
            publisher(
                webspace_id=webspace_id,
                action_id=operation["pending_action_id"],
                kind="skill.setup.approval",
                title="Approve skill setup",
                summary=f"Run the versioned setup plan for {skill_id} after activation.",
                domain_ref={"type": "skill_setup_operation", "id": operation_id},
                allowed_actions=[
                    {"id": "approve", "label": "Approve", "terminal": True},
                    {"id": "refuse", "label": "Refuse", "terminal": True},
                ],
                response_topic="skill.setup.approval.response",
                metadata={
                    "operation_id": operation_id,
                    "release_digest": release_digest,
                    "plan_digest": plan_digest,
                    "dry_run": bool(dry_run),
                },
            )
        except Exception as exc:
            self._append_log(operation_id, "pending_action.publish_failed", error=f"{type(exc).__name__}: {exc}")
        return {"ok": True, "duplicate": False, "operation": self.get(operation_id)}

    def approve_and_execute(
        self,
        operation_id: str,
        *,
        approval_id: str,
        approved_by: str,
        manager: Any,
    ) -> dict[str, Any]:
        operation = self.get(operation_id)
        if operation["status"] == "completed":
            return {"ok": True, "duplicate": True, "operation": operation}
        if operation["status"] not in {"approval_pending", "failed", "input_required"}:
            raise SetupOperationError(f"setup operation cannot execute from {operation['status']}")
        if not approval_id or not approved_by:
            raise SetupOperationError("approval identity is required")
        request = SetupExecutionRequest(
            skill_id=operation["skill_id"],
            release_digest=operation["release_digest"],
            plan_digest=operation["plan_digest"],
            approval_id=approval_id,
            approved_by=approved_by,
            webspace_id=operation["webspace_id"],
            dry_run=bool(operation["dry_run"]),
        )
        operation["status"] = "running"
        operation["approval"] = {
            "approval_id": approval_id,
            "actor_id": approved_by,
            "approved_at": _now(),
        }
        operation["attempts"] = int(operation.get("attempts") or 0) + 1
        operation["error"] = None
        operation["recovery"] = None
        self._log(operation, "setup.started")
        self._write_locked(operation)
        try:
            result = execute_via_skill_manager(request, manager=manager)
        except Exception as exc:
            operation = self.get(operation_id)
            operation["status"] = "failed"
            operation["error"] = {"type": type(exc).__name__, "message": str(exc), "partial_failure": True}
            operation["recovery"] = {
                "mode": "explicit_retry_or_rollback",
                "retry_safe": True,
                "automatic_retry": False,
            }
            self._log(operation, "setup.failed")
            self._write_locked(operation)
            return {"ok": False, "operation": operation}
        operation = self.get(operation_id)
        operation["status"] = "completed"
        operation["result"] = _clone(result)
        self._log(operation, "setup.completed")
        self._write_locked(operation)
        return {"ok": True, "duplicate": False, "operation": operation}

    def recover_interrupted(self) -> list[dict[str, Any]]:
        """Record unknown outcomes without repeating a state-changing setup tool."""

        recovered: list[dict[str, Any]] = []
        for operation in self.list():
            if operation.get("status") != "running":
                continue
            operation["status"] = "input_required"
            operation["recovery"] = {
                "mode": "verify_then_explicit_retry",
                "reason": "process_restarted_with_unknown_setup_outcome",
                "automatic_retry": False,
            }
            self._log(operation, "setup.outcome_unknown")
            self._write_locked(operation)
            recovered.append(operation)
        return recovered

    def cancel(self, operation_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        operation = self.get(operation_id)
        if operation["status"] in {"completed", "cancelled"}:
            return operation
        operation["status"] = "cancelled"
        operation["recovery"] = {"actor": actor, "reason": reason, "automatic_retry": False}
        self._log(operation, "setup.cancelled")
        self._write_locked(operation)
        return operation

    def get(self, operation_id: str) -> dict[str, Any]:
        path = self.root / f"{str(operation_id)}.json"
        if not path.is_file():
            raise KeyError(operation_id)
        value = json.loads(path.read_text(encoding="utf-8"))
        self._validate(value)
        return value

    def list(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("setupop.*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            self._validate(value)
            out.append(value)
        return out

    def _write_locked(self, operation: Mapping[str, Any]) -> None:
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            self._write(operation)

    def _write(self, operation: Mapping[str, Any]) -> None:
        value = dict(operation)
        value["updated_at"] = _now()
        self._validate(value)
        atomic_write_json(self.root / f"{value['operation_id']}.json", value)

    def _append_log(self, operation_id: str, event: str, *, error: str | None = None) -> None:
        operation = self.get(operation_id)
        self._log(operation, event, error=error)
        self._write_locked(operation)

    @staticmethod
    def _log(operation: dict[str, Any], event: str, *, error: str | None = None) -> None:
        entry = {"at": _now(), "event": event}
        if error:
            entry["error"] = error
        operation.setdefault("logs", []).append(entry)
        operation["logs"] = operation["logs"][-200:]

    @staticmethod
    def _validate(operation: Mapping[str, Any]) -> None:
        errors = sorted(Draft202012Validator(_schema()).iter_errors(operation), key=lambda item: list(item.path))
        if errors:
            raise SetupOperationError(f"invalid setup operation: {errors[0].message}")


__all__ = ["SETUP_OPERATION_SCHEMA", "SetupOperationError", "SetupOperationService"]
