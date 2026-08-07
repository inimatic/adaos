"""Governed-workflow activity adapter for the generic executor port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from adaos.domain.execution import ExecutionSpec
from adaos.ports.execution import ExecutorProvider


class ExecutionWorkflowBindingError(ValueError):
    """Raised when a workflow activity lacks an immutable execution binding."""


@dataclass(slots=True)
class ExecutionWorkflowActivityAdapter:
    """Submit one durable workflow activity without becoming workflow authority.

    The adapter is used as a ``WorkflowActivityRunner`` handler. It records a
    normal user-visible operation through the existing OperationManager when
    supplied, and returns references to both identities.
    """

    executor: ExecutorProvider
    operation_manager: Any | None = None

    def __call__(self, activity_attempt: Mapping[str, Any]) -> dict[str, Any]:
        binding = dict(activity_attempt.get("effect_binding") or {})
        raw_spec = binding.get("execution_spec")
        if not isinstance(raw_spec, Mapping):
            raise ExecutionWorkflowBindingError("effect binding requires execution_spec")
        spec = ExecutionSpec.from_dict(raw_spec)
        key = str(
            binding.get("execution_idempotency_key")
            or activity_attempt.get("attempt_id")
            or ""
        ).strip()
        if not key:
            raise ExecutionWorkflowBindingError("execution idempotency key is required")
        attempt = self.executor.submit(spec, idempotency_key=key)
        operation_ref = None
        if self.operation_manager is not None:
            operation = self.operation_manager.create_operation(
                kind="execution_attempt",
                target_kind="execution_attempt",
                target_id=attempt.attempt_id,
                initiator={"workflow_attempt_id": activity_attempt.get("attempt_id")},
                scope=[spec.owner_ref],
                message=f"Execution submitted through {attempt.provider_id}",
            )
            operation_ref = {
                "kind": "operation",
                "id": operation.operation_id,
            }
        return {
            "outcome": "succeeded",
            "data": {
                "execution_attempt": attempt.to_dict(),
                "execution_ref": {"kind": "execution_attempt", "id": attempt.attempt_id},
                "operation_ref": operation_ref,
            },
            "evidence_refs": [],
        }

    def reconcile_operation(self, attempt_id: str, *, owner_ref: str, operation_id: str) -> dict[str, Any]:
        attempt = self.executor.reconcile(attempt_id, owner_ref=owner_ref)
        if self.operation_manager is None:
            return attempt.to_dict()
        if attempt.terminal:
            status = "succeeded" if attempt.status == "succeeded" else attempt.status
            self.operation_manager.update_operation(
                operation_id,
                status=status,
                result={"execution_attempt": attempt.to_dict()},
                error=attempt.failure,
                finished=True,
            )
        else:
            self.operation_manager.update_operation(
                operation_id,
                status="running",
                message=f"Execution is {attempt.status}",
            )
        return attempt.to_dict()


__all__ = ["ExecutionWorkflowActivityAdapter", "ExecutionWorkflowBindingError"]
