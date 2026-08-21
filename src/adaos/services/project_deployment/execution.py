from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping, Protocol

from adaos.domain.artifact_release import ArtifactPackageRef, canonical_payload_digest
from adaos.domain.project_deployment import (
    ComponentActivation,
    DeploymentComponentResult,
    DeploymentNodeResult,
    DeploymentOperation,
    DeploymentPhaseResult,
    DeploymentPlan,
    DeploymentPlanChange,
    NodeInventoryRecord,
    ProjectDeployment,
    inventory_revision,
    utc_now,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.operational_errors import (
    SENSITIVE_ERROR_MARKERS,
    normalized_error_code,
)
from adaos.services.id_gen import new_id

from .authorization import DeploymentPrincipal
from .store import ProjectDeploymentStore


class ProjectDeploymentExecutionError(RuntimeError):
    pass


class RetryableDeploymentPhaseError(ProjectDeploymentExecutionError):
    pass


class UncertainDeploymentPhaseError(ProjectDeploymentExecutionError):
    def __init__(
        self, message: str, *, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


def component_activation_id(
    desired: ProjectDeployment,
    change: DeploymentPlanChange,
    package: ArtifactPackageRef,
) -> str:
    """Return the activation identity shared by coordinator and target node."""

    activation_seed = canonical_payload_digest(
        {
            "deployment_id": desired.deployment_id,
            "component_ref": change.component_ref,
            "node_id": change.node_id,
            "generation": desired.revision,
            "package_digest": package.digest,
        }
    ).split(":", 1)[1][:32]
    return f"activation.{activation_seed}"


class ComponentDeploymentAdapter(Protocol):
    def execute_phase(
        self,
        *,
        phase: str,
        node: NodeInventoryRecord,
        change: DeploymentPlanChange,
        desired: ProjectDeployment,
        release_plan: ReleasePlan,
        package: ArtifactPackageRef | None,
        current_activation: ComponentActivation | None,
        idempotency_key: str,
        attempt: int,
    ) -> Mapping[str, Any]: ...


def _safe_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "<truncated>"
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:100]:
            key = str(raw_key)
            if any(fragment in key.lower() for fragment in SENSITIVE_ERROR_MARKERS):
                output[key] = "<redacted>"
            else:
                output[key] = _safe_payload(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_safe_payload(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        if any(marker in value.lower() for marker in SENSITIVE_ERROR_MARKERS):
            return "<redacted>"
        return value[:2000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(type(value).__name__)


def _safe_error(exc: BaseException, *, code: str) -> dict[str, Any]:
    return {
        "code": code,
        "type": type(exc).__name__,
        "message": normalized_error_code(exc, fallback=code),
    }


def _component_state(values: Iterable[DeploymentComponentResult]) -> tuple[str, bool]:
    items = tuple(values)
    if any(item.uncertain or item.state == "uncertain" for item in items):
        return "uncertain", True
    failed = [item for item in items if item.state == "failed"]
    succeeded = [item for item in items if item.state in {"succeeded", "rolled_back"}]
    if failed and succeeded:
        return "partial", False
    if failed:
        return "failed", False
    if items and all(item.state in {"succeeded", "rolled_back"} for item in items):
        return "succeeded", False
    return "running", False


@dataclass(slots=True)
class ProjectDeploymentExecutor:
    store: ProjectDeploymentStore
    adapter: ComponentDeploymentAdapter
    sleep: Callable[[float], None] = time.sleep
    max_phase_attempts: int = 2

    def execute(
        self,
        plan: DeploymentPlan,
        *,
        desired: ProjectDeployment,
        release_plan: ReleasePlan,
        inventory: Iterable[NodeInventoryRecord],
        principal: DeploymentPrincipal,
        idempotency_key: str,
        kind: str = "apply",
    ) -> DeploymentOperation:
        inventory_records = tuple(inventory)
        operation = self.accept(
            plan,
            desired=desired,
            release_plan=release_plan,
            inventory=inventory_records,
            principal=principal,
            idempotency_key=idempotency_key,
            kind=kind,
        )
        if operation.state != "accepted":
            return operation
        return self.resume(
            operation.operation_id,
            desired=desired,
            release_plan=release_plan,
            inventory=inventory_records,
            principal=principal,
        )

    def accept(
        self,
        plan: DeploymentPlan,
        *,
        desired: ProjectDeployment,
        release_plan: ReleasePlan,
        inventory: Iterable[NodeInventoryRecord],
        principal: DeploymentPrincipal,
        idempotency_key: str,
        kind: str = "apply",
    ) -> DeploymentOperation:
        """Authorize and durably accept work without executing component phases."""

        self._authorize(plan, principal)
        self._validate_preconditions(
            plan,
            desired=desired,
            release_plan=release_plan,
            inventory=inventory,
        )
        self.store.put_plan(plan)
        now = utc_now()
        operation = self.store.create_operation(
            DeploymentOperation(
                operation_id=f"deploymentop.{new_id()}",
                deployment_id=plan.deployment_id,
                plan_digest=str(plan.plan_digest),
                kind=kind,
                state="accepted",
                expected_revision=plan.expected_revision,
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
        )
        self.store.put_operation_authorization(
            operation.operation_id,
            {
                "schema": "adaos.project.deployment_operation_authorization.v1",
                "operation_id": operation.operation_id,
                "actor_ref": principal.actor_ref,
                "permissions": sorted(principal.permissions),
                "approvals": sorted(principal.approvals),
            },
        )
        if operation.state != "accepted":
            return operation
        self.store.append_audit(
            "deployment.apply.authorized",
            operation_id=operation.operation_id,
            deployment_id=operation.deployment_id,
            actor_ref=principal.actor_ref,
            policy_decision="allow",
            approvals=sorted(principal.approvals),
        )
        return operation

    def resume(
        self,
        operation_id: str,
        *,
        desired: ProjectDeployment,
        release_plan: ReleasePlan,
        inventory: Iterable[NodeInventoryRecord],
        principal: DeploymentPrincipal,
    ) -> DeploymentOperation:
        operation = self.store.get_operation(operation_id)
        if operation.kind == "reconcile":
            principal.require("project.deployment.reconcile")
        plan = self.store.get_plan(operation.plan_digest)
        self._authorize(plan, principal)
        nodes = self._validate_preconditions(
            plan,
            desired=desired,
            release_plan=release_plan,
            inventory=inventory,
        )
        if operation.state in {"accepted", "partial"}:
            operation = self.store.update_operation(
                replace(operation, state="running", updated_at=utc_now()),
                expected_state=operation.state,
            )
        elif operation.state != "running":
            return operation
        return self._run(
            operation,
            plan=plan,
            desired=desired,
            release_plan=release_plan,
            nodes=nodes,
        )

    def _authorize(self, plan: DeploymentPlan, principal: DeploymentPrincipal) -> None:
        try:
            principal.require("project.deployment.apply")
            principal.require_plan_approvals(plan.required_approvals)
        except Exception as exc:
            self.store.append_audit(
                "deployment.apply.denied",
                deployment_id=plan.deployment_id,
                actor_ref=principal.actor_ref,
                policy_decision="deny",
                error=_safe_error(exc, code="authorization_denied"),
            )
            raise

    def _validate_preconditions(
        self,
        plan: DeploymentPlan,
        *,
        desired: ProjectDeployment,
        release_plan: ReleasePlan,
        inventory: Iterable[NodeInventoryRecord],
    ) -> dict[str, NodeInventoryRecord]:
        if plan.status != "ready":
            raise ProjectDeploymentExecutionError(
                "blocked deployment plan cannot be applied"
            )
        current = self.store.get_deployment(desired.deployment_id)
        if current != desired or current.revision != plan.expected_revision:
            raise ProjectDeploymentExecutionError(
                "deployment desired revision changed after planning"
            )
        release = release_plan.release
        release_digest = release.release_digest or release.computed_digest()
        if (
            release_digest != plan.release_digest
            or desired.release_digest != release_digest
        ):
            raise ProjectDeploymentExecutionError(
                "ProjectRelease changed after planning"
            )
        records = tuple(inventory)
        if inventory_revision(records) != plan.inventory_revision:
            raise ProjectDeploymentExecutionError(
                "node inventory changed after planning"
            )
        nodes = {item.node_id: item for item in records}
        missing_nodes = sorted(
            {item.node_id for item in plan.changes}.difference(nodes)
        )
        if missing_nodes:
            raise ProjectDeploymentExecutionError(
                f"planned nodes are missing from inventory: {', '.join(missing_nodes)}"
            )
        return nodes

    def _run(
        self,
        operation: DeploymentOperation,
        *,
        plan: DeploymentPlan,
        desired: ProjectDeployment,
        release_plan: ReleasePlan,
        nodes: Mapping[str, NodeInventoryRecord],
    ) -> DeploymentOperation:
        package_by_ref = {item.key: item for item in release_plan.release.components}
        completed = {
            (node.node_id, component.component_ref)
            for node in operation.node_results
            for component in node.components
            if component.state in {"succeeded", "rolled_back"}
        }
        actionable = [
            change
            for change in plan.changes
            if (change.node_id, change.component_ref) not in completed
        ]
        batch_size = max(1, min(desired.rollout.batch_size, len(actionable) or 1))
        stop = False
        for start in range(0, len(actionable), batch_size):
            for change in actionable[start : start + batch_size]:
                package = (
                    package_by_ref.get(change.component_ref)
                    if change.action in {"install", "update", "noop"}
                    else None
                )
                operation, result = self._execute_change(
                    operation,
                    change=change,
                    desired=desired,
                    release_plan=release_plan,
                    package=package,
                    node=nodes[change.node_id],
                )
                if result.uncertain or result.state == "failed":
                    stop = desired.rollout.stop_on_failure
                    if stop:
                        break
            if stop:
                break
            if start + batch_size < len(actionable) and desired.rollout.pause_seconds:
                self.sleep(float(desired.rollout.pause_seconds))

        component_results = [
            component
            for node in operation.node_results
            for component in node.components
        ]
        final_state, uncertain = _component_state(component_results)
        if final_state == "running":
            final_state = "succeeded" if not actionable else "partial"
        error: dict[str, Any] = {}
        if final_state in {"failed", "partial", "uncertain"}:
            error = {
                "code": "deployment_incomplete",
                "manual_reconciliation": uncertain,
            }
        return self.store.update_operation(
            replace(
                operation,
                state=final_state,
                error=error,
                uncertain=uncertain,
                updated_at=utc_now(),
            ),
            expected_state=operation.state,
        )

    def _execute_change(
        self,
        operation: DeploymentOperation,
        *,
        change: DeploymentPlanChange,
        desired: ProjectDeployment,
        release_plan: ReleasePlan,
        package: ArtifactPackageRef | None,
        node: NodeInventoryRecord,
    ) -> tuple[DeploymentOperation, DeploymentComponentResult]:
        if change.action in {"install", "update", "noop"} and package is None:
            raise ProjectDeploymentExecutionError(
                f"ProjectRelease has no exact package for {change.component_ref}"
            )
        if (
            change.action in {"install", "update", "noop"}
            and package is not None
            and change.target_package_digest != package.digest
        ):
            raise ProjectDeploymentExecutionError(
                f"planned package digest changed for {change.component_ref}"
            )
        current_activation = None
        if change.current_activation_ref:
            current_activation = self.store.get_activation(
                change.current_activation_ref
            )
        existing = self._component_result(
            operation, node_id=node.node_id, component_ref=change.component_ref
        )
        phases = list(existing.phases) if existing is not None else []
        result = DeploymentComponentResult(
            component_ref=change.component_ref,
            action=change.action,
            state="running",
            phases=tuple(phases),
            activation_ref=change.current_activation_ref,
        )
        operation = self._persist_component(operation, node.node_id, result)
        for phase in change.phases:
            prior = next((item for item in result.phases if item.phase == phase), None)
            if prior is not None and prior.state == "succeeded":
                continue
            operation, result = self._execute_phase(
                operation,
                result=result,
                phase=phase,
                node=node,
                change=change,
                desired=desired,
                release_plan=release_plan,
                package=package,
                current_activation=current_activation,
            )
            if result.state in {"failed", "uncertain"}:
                if (
                    result.state == "failed"
                    and change.action != "noop"
                    and desired.rollout.rollback_on_failure
                    and phase not in {"rollback", "remove"}
                ):
                    operation, result = self._execute_phase(
                        operation,
                        result=result,
                        phase="rollback",
                        node=node,
                        change=change,
                        desired=desired,
                        release_plan=release_plan,
                        package=package,
                        current_activation=current_activation,
                        preserve_failure=True,
                    )
                return operation, result

        if change.action == "noop":
            result = replace(
                result,
                state="succeeded",
                activation_ref=(
                    current_activation.activation_id
                    if current_activation is not None
                    else None
                ),
                error={},
                uncertain=False,
            )
            operation = self._persist_component(operation, node.node_id, result)
            return operation, result

        activation_ref = self._commit_activation(
            operation,
            change=change,
            desired=desired,
            package=package,
            current=current_activation,
            phases=result.phases,
        )
        result = replace(
            result,
            state="succeeded",
            activation_ref=activation_ref,
            error={},
            uncertain=False,
        )
        operation = self._persist_component(operation, node.node_id, result)
        return operation, result

    def _execute_phase(
        self,
        operation: DeploymentOperation,
        *,
        result: DeploymentComponentResult,
        phase: str,
        node: NodeInventoryRecord,
        change: DeploymentPlanChange,
        desired: ProjectDeployment,
        release_plan: ReleasePlan,
        package: ArtifactPackageRef | None,
        current_activation: ComponentActivation | None,
        preserve_failure: bool = False,
    ) -> tuple[DeploymentOperation, DeploymentComponentResult]:
        phase_key = (
            f"{operation.idempotency_key}:{node.node_id}:{change.component_ref}:{phase}"
        )
        for attempt in range(1, max(1, self.max_phase_attempts) + 1):
            started = utc_now()
            running = DeploymentPhaseResult(
                phase=phase,
                state="running",
                attempt=attempt,
                idempotency_key=phase_key,
                started_at=started,
            )
            result = self._replace_phase(result, running)
            operation = self._persist_component(operation, node.node_id, result)
            try:
                raw_receipt = self.adapter.execute_phase(
                    phase=phase,
                    node=node,
                    change=change,
                    desired=desired,
                    release_plan=release_plan,
                    package=package,
                    current_activation=current_activation,
                    idempotency_key=phase_key,
                    attempt=attempt,
                )
                receipt = _safe_payload(raw_receipt)
                if not isinstance(receipt, Mapping):
                    raise ProjectDeploymentExecutionError(
                        "deployment adapter phase receipt must be an object"
                    )
                completed = replace(
                    running,
                    state="succeeded",
                    receipt=dict(receipt),
                    finished_at=utc_now(),
                )
                result = self._replace_phase(result, completed)
                if phase == "rollback" and not preserve_failure:
                    result = replace(result, state="rolled_back")
                operation = self._persist_component(operation, node.node_id, result)
                return operation, result
            except UncertainDeploymentPhaseError as exc:
                failed = replace(
                    running,
                    state="uncertain",
                    error={
                        **_safe_error(exc, code="adapter_outcome_uncertain"),
                        "details": _safe_payload(exc.details),
                    },
                    finished_at=utc_now(),
                )
                result = replace(
                    self._replace_phase(result, failed),
                    state="uncertain",
                    error=dict(failed.error),
                    uncertain=True,
                )
                operation = self._persist_component(operation, node.node_id, result)
                return operation, result
            except RetryableDeploymentPhaseError as exc:
                failed = replace(
                    running,
                    state="failed",
                    error=_safe_error(exc, code="adapter_phase_retryable"),
                    finished_at=utc_now(),
                )
                result = self._replace_phase(result, failed)
                operation = self._persist_component(operation, node.node_id, result)
                if attempt < max(1, self.max_phase_attempts):
                    continue
                result = replace(result, state="failed", error=dict(failed.error))
                operation = self._persist_component(operation, node.node_id, result)
                return operation, result
            except Exception as exc:
                failed = replace(
                    running,
                    state="failed",
                    error=_safe_error(exc, code="adapter_phase_failed"),
                    finished_at=utc_now(),
                )
                result = replace(
                    self._replace_phase(result, failed),
                    state="failed",
                    error=dict(failed.error),
                )
                operation = self._persist_component(operation, node.node_id, result)
                return operation, result
        raise AssertionError("phase attempt loop did not return")

    def _commit_activation(
        self,
        operation: DeploymentOperation,
        *,
        change: DeploymentPlanChange,
        desired: ProjectDeployment,
        package: ArtifactPackageRef | None,
        current: ComponentActivation | None,
        phases: tuple[DeploymentPhaseResult, ...],
    ) -> str | None:
        now = utc_now()
        if change.action == "remove":
            if current is None:
                return None
            removed = replace(current, status="removed", updated_at=now)
            self.store.put_activation(removed)
            return removed.activation_id
        if change.action in {"cordon", "drain", "deactivate"}:
            if current is None:
                raise ProjectDeploymentExecutionError(
                    f"{change.action} requires a current component activation"
                )
            status = "draining" if change.action in {"cordon", "drain"} else "inactive"
            observed = replace(current, status=status, updated_at=now)
            self.store.put_activation(observed)
            return observed.activation_id
        if package is None:
            raise ProjectDeploymentExecutionError(
                "activation requires an exact package"
            )
        if current is not None and change.action == "update":
            self.store.put_activation(
                replace(current, status="inactive", updated_at=now)
            )
        receipts = {item.phase: dict(item.receipt) for item in phases if item.receipt}
        health = dict(receipts.get("health") or {})
        activation = ComponentActivation(
            activation_id=component_activation_id(desired, change, package),
            deployment_id=desired.deployment_id,
            component_ref=change.component_ref,
            node_id=change.node_id,
            release_digest=desired.release_digest,
            package_digest=package.digest,
            generation=desired.revision,
            status="active",
            health=health,
            evidence={
                "operation_id": operation.operation_id,
                "plan_digest": operation.plan_digest,
                "phase_receipts": receipts,
            },
            created_at=now,
            updated_at=now,
        )
        self.store.put_activation(activation)
        return activation.activation_id

    @staticmethod
    def _component_result(
        operation: DeploymentOperation, *, node_id: str, component_ref: str
    ) -> DeploymentComponentResult | None:
        for node in operation.node_results:
            if node.node_id != node_id:
                continue
            return next(
                (
                    item
                    for item in node.components
                    if item.component_ref == component_ref
                ),
                None,
            )
        return None

    @staticmethod
    def _replace_phase(
        result: DeploymentComponentResult, phase: DeploymentPhaseResult
    ) -> DeploymentComponentResult:
        phases = [item for item in result.phases if item.phase != phase.phase]
        phases.append(phase)
        order = {item.phase: index for index, item in enumerate(result.phases)}
        phases.sort(key=lambda item: order.get(item.phase, len(order)))
        return replace(result, phases=tuple(phases))

    def _persist_component(
        self,
        operation: DeploymentOperation,
        node_id: str,
        result: DeploymentComponentResult,
    ) -> DeploymentOperation:
        nodes = list(operation.node_results)
        existing_node = next((item for item in nodes if item.node_id == node_id), None)
        components = list(existing_node.components) if existing_node is not None else []
        components = [
            item for item in components if item.component_ref != result.component_ref
        ]
        components.append(result)
        node_state, node_uncertain = _component_state(components)
        node_result = DeploymentNodeResult(
            node_id=node_id,
            state=node_state,
            components=tuple(components),
            error=(
                dict(result.error) if result.state in {"failed", "uncertain"} else {}
            ),
            uncertain=node_uncertain,
        )
        nodes = [item for item in nodes if item.node_id != node_id]
        nodes.append(node_result)
        updated = replace(
            operation,
            node_results=tuple(sorted(nodes, key=lambda item: item.node_id)),
            updated_at=utc_now(),
        )
        return self.store.update_operation(updated, expected_state=operation.state)


__all__ = [
    "ComponentDeploymentAdapter",
    "ProjectDeploymentExecutionError",
    "ProjectDeploymentExecutor",
    "RetryableDeploymentPhaseError",
    "UncertainDeploymentPhaseError",
    "component_activation_id",
]
