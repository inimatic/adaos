from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Any, Callable, Iterable, Mapping, Protocol

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.project_deployment import (
    ComponentActivation,
    DeploymentOperation,
    DeploymentPlan,
    DeploymentPlanChange,
    NodeInventoryRecord,
    ProjectDeployment,
    inventory_revision,
    utc_now,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import MutationLockTimeout, mutation_lock

from .authorization import DeploymentPrincipal
from .execution import ComponentDeploymentAdapter, ProjectDeploymentExecutor
from .planner import ProjectDeploymentPlanner
from .projections import build_project_deployment_projection
from .store import ProjectDeploymentStore


class ProjectReleaseProvider(Protocol):
    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan: ...


class NodeInventoryProvider(Protocol):
    def list_nodes(self, subnet_id: str) -> Iterable[NodeInventoryRecord]: ...


@dataclass(frozen=True, slots=True)
class DeploymentInspection:
    desired: ProjectDeployment
    activations: tuple[ComponentActivation, ...]
    operations: tuple[DeploymentOperation, ...]
    activation_cursor: str | None = None
    operation_cursor: str | None = None

    def to_dict(self) -> dict:
        return {
            "schema": "adaos.project.deployment_inspection.v1",
            "desired": self.desired.to_dict(),
            "activations": [item.to_dict() for item in self.activations],
            "operations": [item.to_dict() for item in self.operations],
            "activation_cursor": self.activation_cursor,
            "operation_cursor": self.operation_cursor,
        }


@dataclass(slots=True)
class ProjectDeploymentRuntime:
    store: ProjectDeploymentStore
    releases: ProjectReleaseProvider
    inventory: NodeInventoryProvider
    adapter: ComponentDeploymentAdapter
    local_node_id: str | None = None
    projection_publisher: Callable[[Mapping[str, Any]], Any] | None = None
    _worker: ThreadPoolExecutor = field(init=False, repr=False)
    _worker_lock: RLock = field(init=False, repr=False)
    _futures: dict[str, Future[DeploymentOperation]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._worker = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="adaos-project-deployment",
        )
        self._worker_lock = RLock()
        self._futures = {}

    def define(
        self,
        desired: ProjectDeployment,
        *,
        expected_revision: int,
        principal: DeploymentPrincipal,
        reason: str,
    ) -> ProjectDeployment:
        principal.require("project.deployment.manage")
        revision = self.store.save_deployment(
            desired,
            expected_revision=expected_revision,
            actor_ref=principal.actor_ref,
            reason=reason,
        )
        self._publish_projection()
        return revision.desired

    def plan(
        self, deployment_id: str, *, principal: DeploymentPrincipal
    ) -> DeploymentPlan:
        principal.require("project.deployment.inspect")
        desired = self.store.get_deployment(deployment_id)
        release_plan = self._release(desired)
        inventory = tuple(self.inventory.list_nodes(desired.subnet_id))
        activations = self._all_activations(deployment_id)
        plan = ProjectDeploymentPlanner().plan(
            desired,
            release_plan=release_plan,
            inventory=inventory,
            activations=activations,
            local_node_id=self.local_node_id,
        )
        saved = self.store.put_plan(plan)
        self._publish_projection()
        return saved

    def apply(
        self,
        plan_digest: str,
        *,
        principal: DeploymentPrincipal,
        idempotency_key: str,
    ) -> DeploymentOperation:
        plan = self.store.get_plan(plan_digest)
        desired = self.store.get_deployment(plan.deployment_id)
        operation = ProjectDeploymentExecutor(
            store=self.store, adapter=self.adapter
        ).execute(
            plan,
            desired=desired,
            release_plan=self._release(desired),
            inventory=tuple(self.inventory.list_nodes(desired.subnet_id)),
            principal=principal,
            idempotency_key=idempotency_key,
            kind="apply",
        )
        self._publish_projection()
        return operation

    def submit(
        self,
        plan_digest: str,
        *,
        principal: DeploymentPrincipal,
        idempotency_key: str,
    ) -> DeploymentOperation:
        """Durably accept a reviewed plan and execute it outside the caller RPC."""

        plan = self.store.get_plan(plan_digest)
        desired = self.store.get_deployment(plan.deployment_id)
        operation = ProjectDeploymentExecutor(
            store=self.store, adapter=self.adapter
        ).accept(
            plan,
            desired=desired,
            release_plan=self._release(desired),
            inventory=tuple(self.inventory.list_nodes(desired.subnet_id)),
            principal=principal,
            idempotency_key=idempotency_key,
            kind="apply",
        )
        if operation.state in {"accepted", "running"}:
            self._schedule(operation.operation_id)
        self._publish_projection()
        return self.store.get_operation(operation.operation_id)

    def get_operation(
        self,
        operation_id: str,
        *,
        principal: DeploymentPrincipal,
    ) -> DeploymentOperation:
        principal.require("project.deployment.inspect")
        return self.store.get_operation(operation_id)

    def recover_incomplete(self, *, limit: int = 100) -> tuple[str, ...]:
        scheduled: list[str] = []
        for operation in self.store.list_incomplete_operations(limit=limit):
            try:
                self.store.get_operation_authorization(operation.operation_id)
            except FileNotFoundError:
                self.store.append_audit(
                    "deployment.operation.recovery.skipped",
                    operation_id=operation.operation_id,
                    reason="authorization_record_missing",
                )
                continue
            self._schedule(operation.operation_id)
            scheduled.append(operation.operation_id)
        return tuple(scheduled)

    def shutdown(self, *, wait: bool = False) -> None:
        self._worker.shutdown(wait=wait, cancel_futures=False)

    def _schedule(self, operation_id: str) -> None:
        with self._worker_lock:
            previous = self._futures.get(operation_id)
            if previous is not None and not previous.done():
                return
            future = self._worker.submit(self._resume_submitted, operation_id)
            self._futures[operation_id] = future
            future.add_done_callback(
                lambda completed, key=operation_id: self._submitted_done(key, completed)
            )

    def _resume_submitted(self, operation_id: str) -> DeploymentOperation:
        try:
            with mutation_lock(
                self.store.operation_execution_lock_path(operation_id),
                timeout_s=0.1,
            ):
                return self._resume_submitted_locked(operation_id)
        except MutationLockTimeout:
            return self.store.get_operation(operation_id)

    def _resume_submitted_locked(self, operation_id: str) -> DeploymentOperation:
        operation = self.store.get_operation(operation_id)
        authorization = self.store.get_operation_authorization(operation_id)
        principal = DeploymentPrincipal.create(
            actor_ref=str(authorization.get("actor_ref") or ""),
            permissions=tuple(authorization.get("permissions") or ()),
            approvals=tuple(authorization.get("approvals") or ()),
        )
        desired = self.store.get_deployment(operation.deployment_id)
        try:
            result = ProjectDeploymentExecutor(
                store=self.store, adapter=self.adapter
            ).resume(
                operation_id,
                desired=desired,
                release_plan=self._release(desired),
                inventory=tuple(self.inventory.list_nodes(desired.subnet_id)),
                principal=principal,
            )
        except Exception as exc:
            current = self.store.get_operation(operation_id)
            if current.state in {"accepted", "running"}:
                result = self.store.update_operation(
                    replace(
                        current,
                        state="failed",
                        error={
                            "code": "deployment_worker_failed",
                            "type": type(exc).__name__,
                            "message": str(exc)[:1000],
                        },
                        updated_at=utc_now(),
                    ),
                    expected_state=current.state,
                )
            else:
                result = current
        self._publish_projection()
        return result

    def _submitted_done(
        self,
        operation_id: str,
        future: Future[DeploymentOperation],
    ) -> None:
        del future
        with self._worker_lock:
            self._futures.pop(operation_id, None)

    def reconcile(
        self,
        deployment_id: str,
        *,
        principal: DeploymentPrincipal,
        idempotency_key: str,
    ) -> DeploymentOperation:
        principal.require("project.deployment.reconcile")
        plan = self.plan(deployment_id, principal=principal)
        desired = self.store.get_deployment(deployment_id)
        operation = ProjectDeploymentExecutor(
            store=self.store, adapter=self.adapter
        ).execute(
            plan,
            desired=desired,
            release_plan=self._release(desired),
            inventory=tuple(self.inventory.list_nodes(desired.subnet_id)),
            principal=principal,
            idempotency_key=idempotency_key,
            kind="reconcile",
        )
        self._publish_projection()
        return operation

    def inspect(
        self,
        deployment_id: str,
        *,
        principal: DeploymentPrincipal,
        activation_cursor: str | None = None,
        operation_cursor: str | None = None,
        limit: int = 50,
    ) -> DeploymentInspection:
        principal.require("project.deployment.inspect")
        desired = self.store.get_deployment(deployment_id)
        activations, next_activation = self.store.list_activations(
            deployment_id=deployment_id,
            cursor=activation_cursor,
            limit=limit,
        )
        operations, next_operation = self.store.list_operations(
            deployment_id=deployment_id,
            cursor=operation_cursor,
            limit=limit,
        )
        return DeploymentInspection(
            desired=desired,
            activations=activations,
            operations=operations,
            activation_cursor=next_activation,
            operation_cursor=next_operation,
        )

    def list_deployments(
        self,
        *,
        principal: DeploymentPrincipal,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProjectDeployment, ...], str | None]:
        principal.require("project.deployment.inspect")
        return self.store.list_deployments(cursor=cursor, limit=limit)

    def recommend_nodes(
        self,
        deployment_id: str,
        component_ref: str,
        *,
        principal: DeploymentPrincipal,
        limit: int = 20,
    ) -> dict[str, object]:
        principal.require("project.deployment.inspect")
        desired = self.store.get_deployment(deployment_id)
        placement = next(
            (
                item
                for item in desired.placements
                if item.component_ref == str(component_ref)
            ),
            None,
        )
        if placement is None:
            raise KeyError(f"component placement not found: {component_ref}")
        return ProjectDeploymentPlanner().recommend_nodes(
            desired,
            placement,
            inventory=tuple(self.inventory.list_nodes(desired.subnet_id)),
            activations=self._all_activations(deployment_id),
            limit=limit,
        )

    def drain(
        self,
        activation_id: str,
        *,
        principal: DeploymentPrincipal,
        idempotency_key: str,
    ) -> DeploymentOperation:
        return self._activation_operation(
            activation_id,
            action="drain",
            phases=("cordon", "drain"),
            approval="component_drain",
            principal=principal,
            idempotency_key=idempotency_key,
        )

    def remove(
        self,
        activation_id: str,
        *,
        principal: DeploymentPrincipal,
        idempotency_key: str,
    ) -> DeploymentOperation:
        return self._activation_operation(
            activation_id,
            action="remove",
            phases=("cordon", "drain", "deactivate", "remove"),
            approval="component_remove",
            principal=principal,
            idempotency_key=idempotency_key,
        )

    def _activation_operation(
        self,
        activation_id: str,
        *,
        action: str,
        phases: tuple[str, ...],
        approval: str,
        principal: DeploymentPrincipal,
        idempotency_key: str,
    ) -> DeploymentOperation:
        activation = self.store.get_activation(activation_id)
        desired = self.store.get_deployment(activation.deployment_id)
        inventory = tuple(self.inventory.list_nodes(desired.subnet_id))
        inventory_digest = inventory_revision(inventory)
        seed = canonical_payload_digest(
            {
                "deployment_id": desired.deployment_id,
                "revision": desired.revision,
                "activation_id": activation_id,
                "action": action,
                "inventory_revision": inventory_digest,
            }
        ).split(":", 1)[1][:24]
        plan = DeploymentPlan(
            plan_id=f"plan.{seed}",
            deployment_id=desired.deployment_id,
            expected_revision=desired.revision,
            release_digest=desired.release_digest,
            inventory_revision=inventory_digest,
            changes=(
                DeploymentPlanChange(
                    action=action,
                    component_ref=activation.component_ref,
                    node_id=activation.node_id,
                    current_activation_ref=activation.activation_id,
                    reason=f"operator_requested_{action}",
                    phases=phases,
                    availability_impact="reduced_capacity",
                ),
            ),
            required_approvals=(approval,),
            created_at=desired.updated_at,
        )
        self.store.put_plan(plan)
        operation = ProjectDeploymentExecutor(
            store=self.store, adapter=self.adapter
        ).execute(
            plan,
            desired=desired,
            release_plan=self._release(desired),
            inventory=inventory,
            principal=principal,
            idempotency_key=idempotency_key,
            kind=action,
        )
        self._publish_projection()
        return operation

    def _release(self, desired: ProjectDeployment) -> ReleasePlan:
        project_id = desired.project_ref.split(":", 1)[1]
        return self.releases.get_release(project_id, desired.release_digest)

    def _all_activations(self, deployment_id: str) -> tuple[ComponentActivation, ...]:
        records: list[ComponentActivation] = []
        cursor: str | None = None
        while True:
            page, cursor = self.store.list_activations(
                deployment_id=deployment_id,
                cursor=cursor,
                limit=200,
            )
            records.extend(page)
            if cursor is None:
                return tuple(records)

    def _publish_projection(self) -> None:
        publisher = self.projection_publisher
        if publisher is None:
            return
        try:
            publisher(build_project_deployment_projection(self.store))
        except Exception as exc:
            self.store.append_audit(
                "deployment.projection.failed",
                error={"type": type(exc).__name__, "message": str(exc)[:500]},
            )


_RUNTIME: ProjectDeploymentRuntime | None = None


def register_project_deployment_runtime(
    runtime: ProjectDeploymentRuntime | None,
) -> ProjectDeploymentRuntime | None:
    global _RUNTIME
    previous = _RUNTIME
    _RUNTIME = runtime
    return previous


def get_project_deployment_runtime() -> ProjectDeploymentRuntime:
    if _RUNTIME is None:
        raise RuntimeError("project deployment runtime is not configured")
    return _RUNTIME


__all__ = [
    "DeploymentInspection",
    "NodeInventoryProvider",
    "ProjectDeploymentRuntime",
    "ProjectReleaseProvider",
    "get_project_deployment_runtime",
    "register_project_deployment_runtime",
]
