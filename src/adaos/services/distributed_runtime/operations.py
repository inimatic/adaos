from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Protocol

from adaos.domain.distributed_operations import TopologyPlan, TopologyPlanStep
from adaos.domain.distributed_runtime import (
    Dataset,
    TopologyOperation,
    TopologyPhaseResult,
    utc_now,
)
from adaos.services.artifact_pipeline.storage import (
    MutationLockTimeout,
    mutation_lock,
)

from .authorization import DistributedPrincipal
from .store import DistributedRuntimeStore


class TopologyExecutionError(RuntimeError):
    pass


class RetryableTopologyPhaseError(TopologyExecutionError):
    pass


class UncertainTopologyPhaseError(TopologyExecutionError):
    pass


@dataclass(frozen=True, slots=True)
class TopologyStepContext:
    operation_id: str
    plan_digest: str
    step: TopologyPlanStep
    phase: str
    authority_epoch: int
    idempotency_key: str
    attempt: int


class TopologyAdapter(Protocol):
    def inspect(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def reserve(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def prepare(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def snapshot(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def stream_deltas(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def catch_up(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def verify(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def activate_read(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def promote(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def demote(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def drain(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def remove(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def route(self, context: TopologyStepContext) -> Mapping[str, Any]: ...

    def release(self, context: TopologyStepContext) -> Mapping[str, Any]: ...


_APPROVAL_PERMISSIONS = {
    "authority_handoff": "distributed.authority.handoff",
    "replica_remove": "distributed.replica.remove",
    "replica_data_delete": "distributed.data.delete",
}
_SECRET_WORDS = {"authorization", "cookie", "password", "secret", "token"}


def _adapter_error_code(exc: Exception, phase: str) -> str:
    candidate = str(exc).strip().lower()
    if (
        candidate
        and len(candidate) <= 160
        and all(
            char.isascii() and (char.isalnum() or char in "._:-") for char in candidate
        )
    ):
        return candidate
    return f"adapter_phase_failed:{phase}"


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]"
            if any(word in str(key).lower() for word in _SECRET_WORDS)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


@dataclass(slots=True)
class TopologyExecutor:
    store: DistributedRuntimeStore
    adapter: TopologyAdapter
    pressure_probe: Callable[[TopologyPlanStep], float] | None = None
    authority_handoff: (
        Callable[[TopologyPlanStep, TopologyOperation, DistributedPrincipal, int], int]
        | None
    ) = None
    sleep: Callable[[float], None] = time.sleep
    max_attempts: int = 2
    pressure_limit: float = 0.9

    def execute(
        self,
        plan: TopologyPlan,
        *,
        principal: DistributedPrincipal,
        idempotency_key: str,
    ) -> TopologyOperation:
        lock_name = f"{str(plan.plan_digest).split(':', 1)[-1]}.lock"
        lock_path = self.store.root / "operation_execution" / lock_name
        execution_lock = mutation_lock(lock_path, timeout_s=0.1)
        try:
            execution_lock.__enter__()
        except MutationLockTimeout as exc:
            previous = self.store.get_operation_by_idempotency(idempotency_key)
            if previous is not None:
                return previous
            raise TopologyExecutionError("topology_operation_execution_busy") from exc
        try:
            return self._execute_locked(
                plan,
                principal=principal,
                idempotency_key=idempotency_key,
            )
        finally:
            execution_lock.__exit__(None, None, None)

    def _execute_locked(
        self,
        plan: TopologyPlan,
        *,
        principal: DistributedPrincipal,
        idempotency_key: str,
    ) -> TopologyOperation:
        principal.require("distributed.topology.apply")
        self._require_approvals(plan, principal)
        if plan.status != "ready":
            raise TopologyExecutionError("topology_plan_not_ready")
        previous = self.store.get_operation_by_idempotency(idempotency_key)
        if previous is not None and previous.state in {
            "succeeded",
            "failed",
            "uncertain",
        }:
            return previous
        if previous is None:
            self._validate_plan_state(plan)
            operation_id = (
                f"topology-{str(plan.plan_digest).split(':', 1)[1][:20]}"
            )
            created_at = utc_now()
            operation = TopologyOperation(
                operation_id=operation_id,
                kind=plan.kind,
                target_ref=plan.target_ref,
                state="pending",
                expected_revision=plan.expected_observed_revision,
                authority_epoch=plan.authority_epoch,
                idempotency_key=idempotency_key,
                phases=(),
                created_at=created_at,
                updated_at=created_at,
            )
            self.store.put_operation(operation)
            completed: list[TopologyPhaseResult] = []
            authority_epoch = plan.authority_epoch
        else:
            self._validate_resume_identity(plan, previous)
            terminal_phase = next(
                (
                    item
                    for item in previous.phases
                    if item.state in {"failed", "uncertain"}
                ),
                None,
            )
            if terminal_phase is not None:
                operation = replace(
                    previous,
                    state=terminal_phase.state,
                    updated_at=utc_now(),
                )
                self.store.put_operation(operation)
                return operation
            operation = previous
            completed = list(previous.phases)
            authority_epoch = previous.authority_epoch
            self.store.append_audit(
                "topology.operation.resumed",
                operation_id=operation.operation_id,
                plan_digest=plan.plan_digest,
                completed_phases=len(completed),
                actor_ref=principal.actor_ref,
            )
        operation = replace(operation, state="running", updated_at=utc_now())
        self.store.put_operation(operation)
        try:
            for step in plan.steps:
                self._validate_retention(step)
                for phase in step.phases:
                    phase_name = f"{step.step_id}.{phase}"
                    phase_key = (
                        f"{operation.idempotency_key}:{step.step_id}:{phase}"
                    )
                    if any(
                        item.phase == phase_name
                        and item.idempotency_key == phase_key
                        and item.state == "succeeded"
                        for item in completed
                    ):
                        continue
                    if phase == "promote":
                        if self.authority_handoff is None:
                            raise TopologyExecutionError(
                                "authority_handoff_committer_not_configured"
                            )
                        if authority_epoch == plan.authority_epoch:
                            authority_epoch = self.authority_handoff(
                                step,
                                operation,
                                principal,
                                authority_epoch,
                            )
                            operation = replace(
                                operation,
                                authority_epoch=authority_epoch,
                                updated_at=utc_now(),
                            )
                            self.store.put_operation(operation)
                    result = self._run_phase(
                        operation=operation,
                        plan=plan,
                        step=step,
                        phase=phase,
                        authority_epoch=authority_epoch,
                    )
                    completed.append(result)
                    operation = replace(
                        operation,
                        phases=tuple(completed),
                        updated_at=utc_now(),
                    )
                    self.store.put_operation(operation)
            operation = replace(
                operation,
                state="succeeded",
                authority_epoch=authority_epoch,
                phases=tuple(completed),
                updated_at=utc_now(),
            )
            self.store.put_operation(operation)
            self.store.append_audit(
                "topology.plan.applied",
                plan_digest=plan.plan_digest,
                operation_id=operation.operation_id,
                actor_ref=principal.actor_ref,
            )
            return operation
        except UncertainTopologyPhaseError:
            persisted = self.store.get_operation(operation.operation_id)
            operation = replace(
                operation,
                state="uncertain",
                phases=persisted.phases,
                updated_at=utc_now(),
            )
            self.store.put_operation(operation)
            self.store.append_audit(
                "topology.operation.manual_reconciliation_required",
                operation_id=operation.operation_id,
                actor_ref=principal.actor_ref,
            )
            return operation
        except Exception:
            persisted = self.store.get_operation(operation.operation_id)
            completed = list(persisted.phases)
            completed.extend(
                self._truthful_release(
                    operation,
                    plan,
                    completed,
                    authority_epoch=authority_epoch,
                )
            )
            operation = replace(
                operation,
                state="failed",
                phases=tuple(completed),
                updated_at=utc_now(),
            )
            self.store.put_operation(operation)
            return operation

    def _run_phase(
        self,
        *,
        operation: TopologyOperation,
        plan: TopologyPlan,
        step: TopologyPlanStep,
        phase: str,
        authority_epoch: int | None = None,
    ) -> TopologyPhaseResult:
        max_attempts = max(1, min(int(self.max_attempts), 5))
        for attempt in range(1, max_attempts + 1):
            started_at = utc_now()
            phase_key = f"{operation.idempotency_key}:{step.step_id}:{phase}"
            context = TopologyStepContext(
                operation_id=operation.operation_id,
                plan_digest=str(plan.plan_digest),
                step=step,
                phase=phase,
                authority_epoch=(
                    plan.authority_epoch if authority_epoch is None else authority_epoch
                ),
                idempotency_key=phase_key,
                attempt=attempt,
            )
            if (
                self.pressure_probe is not None
                and self.pressure_probe(step) > self.pressure_limit
            ):
                if attempt < max_attempts:
                    self.sleep(min(float(attempt), 5.0))
                    continue
                raise RetryableTopologyPhaseError("resource_pressure")
            try:
                method = getattr(self.adapter, phase)
                receipt = _redact(dict(method(context)))
                return TopologyPhaseResult(
                    phase=f"{step.step_id}.{phase}",
                    state="succeeded",
                    attempt=attempt,
                    idempotency_key=phase_key,
                    receipt=receipt,
                    started_at=started_at,
                    finished_at=utc_now(),
                )
            except UncertainTopologyPhaseError as exc:
                result = TopologyPhaseResult(
                    phase=f"{step.step_id}.{phase}",
                    state="uncertain",
                    attempt=attempt,
                    idempotency_key=phase_key,
                    receipt={},
                    started_at=started_at,
                    finished_at=utc_now(),
                    error_code=str(exc) or "uncertain_adapter_outcome",
                )
                self._append_terminal_phase(operation, result)
                raise
            except RetryableTopologyPhaseError as exc:
                if attempt < max_attempts:
                    self.sleep(min(float(attempt), 5.0))
                    continue
                self._append_terminal_phase(
                    operation,
                    TopologyPhaseResult(
                        phase=f"{step.step_id}.{phase}",
                        state="failed",
                        attempt=attempt,
                        idempotency_key=phase_key,
                        receipt={},
                        started_at=started_at,
                        finished_at=utc_now(),
                        error_code=str(exc) or "retry_exhausted",
                    ),
                )
                raise TopologyExecutionError(str(exc) or "retry_exhausted") from exc
            except Exception as exc:
                error_code = _adapter_error_code(exc, phase)
                self._append_terminal_phase(
                    operation,
                    TopologyPhaseResult(
                        phase=f"{step.step_id}.{phase}",
                        state="failed",
                        attempt=attempt,
                        idempotency_key=phase_key,
                        receipt={},
                        started_at=started_at,
                        finished_at=utc_now(),
                        error_code=error_code,
                    ),
                )
                raise TopologyExecutionError(error_code) from exc
        raise TopologyExecutionError("adapter_phase_retry_exhausted")

    def _append_terminal_phase(
        self, operation: TopologyOperation, result: TopologyPhaseResult
    ) -> None:
        self.store.put_operation(
            replace(
                operation,
                phases=(*operation.phases, result),
                updated_at=utc_now(),
            )
        )

    def _truthful_release(
        self,
        operation: TopologyOperation,
        plan: TopologyPlan,
        completed: list[TopologyPhaseResult],
        *,
        authority_epoch: int,
    ) -> list[TopologyPhaseResult]:
        results: list[TopologyPhaseResult] = []
        completed_steps = {item.phase.split(".", 1)[0] for item in completed}
        for step in reversed(plan.steps):
            if step.step_id not in completed_steps or step.action in {
                "remove",
                "handoff",
            }:
                continue
            try:
                results.append(
                    self._run_phase(
                        operation=operation,
                        plan=plan,
                        step=step,
                        phase="release",
                        authority_epoch=authority_epoch,
                    )
                )
            except Exception:
                continue
        return results

    def _validate_plan_state(self, plan: TopologyPlan) -> None:
        for step in plan.steps:
            partition = self.store.get_partition(step.partition_id)
            dataset = self.store.get_dataset(partition.dataset_id)
            if dataset.desired_revision != plan.expected_desired_revision:
                raise TopologyExecutionError("topology_desired_revision_changed")
            expected_partition_revision = int(
                step.adapter_options.get(
                    "expected_partition_revision", plan.expected_observed_revision
                )
            )
            if partition.revision != expected_partition_revision:
                raise TopologyExecutionError("topology_observed_revision_changed")
            if (
                plan.kind == "handoff"
                and partition.authority_epoch != plan.authority_epoch
            ):
                raise TopologyExecutionError("topology_authority_epoch_changed")

    @staticmethod
    def _validate_resume_identity(
        plan: TopologyPlan, operation: TopologyOperation
    ) -> None:
        expected_operation_id = (
            f"topology-{str(plan.plan_digest).split(':', 1)[1][:20]}"
        )
        if (
            operation.operation_id != expected_operation_id
            or operation.kind != plan.kind
            or operation.target_ref != plan.target_ref
            or operation.expected_revision != plan.expected_observed_revision
        ):
            raise TopologyExecutionError("topology_operation_plan_mismatch")

    def _validate_retention(self, step: TopologyPlanStep) -> None:
        partition = self.store.get_partition(step.partition_id)
        dataset: Dataset = self.store.get_dataset(partition.dataset_id)
        if step.retention != dataset.removal_retention:
            raise TopologyExecutionError("topology_plan_retention_mismatch")

    @staticmethod
    def _require_approvals(plan: TopologyPlan, principal: DistributedPrincipal) -> None:
        for approval in plan.required_approvals:
            permission = _APPROVAL_PERMISSIONS.get(approval)
            if permission is None:
                raise TopologyExecutionError(
                    f"unsupported_topology_approval:{approval}"
                )
            principal.require_approval(approval, permission=permission)


__all__ = [
    "RetryableTopologyPhaseError",
    "TopologyAdapter",
    "TopologyExecutionError",
    "TopologyExecutor",
    "TopologyStepContext",
    "UncertainTopologyPhaseError",
]
