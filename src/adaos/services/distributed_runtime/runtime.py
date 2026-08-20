from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Callable, Iterable, Mapping, Protocol, TypeVar

from adaos.domain.distributed_runtime import (
    Dataset,
    DistributedRoute,
    Partition,
    Replica,
    RouteEndpoint,
    ServiceDefinition,
    ServiceGroup,
    ServiceInstance,
    TopologyLease,
    TopologyOperation,
)
from adaos.domain.distributed_operations import TopologyPlan, TopologyPlanStep
from adaos.domain.project_deployment import NodeInventoryRecord
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.project_deployment.store import ProjectDeploymentStore

from .authorization import DistributedPrincipal
from .projections import build_distributed_projection
from .operations import TopologyAdapter, TopologyExecutor
from .service_invocation import ServiceInvocationAdapter
from .store import DistributedRuntimeStore


class DistributedRuntimeError(RuntimeError):
    pass


class StaleAuthorityEpochError(DistributedRuntimeError):
    pass


class DistributedNodeInventoryProvider(Protocol):
    def list_nodes(self, subnet_id: str) -> Iterable[NodeInventoryRecord]: ...


class DistributedReleaseProvider(Protocol):
    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan: ...


@dataclass(frozen=True, slots=True)
class DistributedInspection:
    groups: tuple[ServiceGroup, ...]
    instances: tuple[ServiceInstance, ...]
    leases: tuple[TopologyLease, ...]
    datasets: tuple[Dataset, ...]
    partitions: tuple[Partition, ...]
    replicas: tuple[Replica, ...]
    operations: tuple[TopologyOperation, ...]
    cursors: Mapping[str, str | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "adaos.distributed.inspection.v1",
            "groups": [item.to_dict() for item in self.groups],
            "instances": [item.to_dict() for item in self.instances],
            "leases": [item.to_dict() for item in self.leases],
            "datasets": [item.to_dict() for item in self.datasets],
            "partitions": [item.to_dict() for item in self.partitions],
            "replicas": [item.to_dict() for item in self.replicas],
            "operations": [item.to_dict() for item in self.operations],
            "cursors": dict(self.cursors),
        }


_T = TypeVar("_T")


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _identity(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(item) for item in parts).encode()
    ).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _all_pages(
    loader: Callable[..., tuple[tuple[_T, ...], str | None]], **filters: Any
) -> tuple[_T, ...]:
    values: list[_T] = []
    cursor: str | None = None
    while True:
        page, cursor = loader(cursor=cursor, limit=200, **filters)
        values.extend(page)
        if cursor is None:
            return tuple(values)


@dataclass(slots=True)
class DistributedRuntime:
    store: DistributedRuntimeStore
    deployment_store: ProjectDeploymentStore
    releases: DistributedReleaseProvider
    inventory: DistributedNodeInventoryProvider
    topology_adapter: TopologyAdapter | None = None
    service_invoker: ServiceInvocationAdapter | None = None
    projection_publisher: Callable[[Mapping[str, Any]], Any] | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def define_service(
        self, definition: ServiceDefinition, *, principal: DistributedPrincipal
    ) -> ServiceDefinition:
        principal.require("distributed.service.manage")
        result = self.store.put_definition(definition)
        self._publish_projection()
        return result

    def invoke_instance(
        self,
        instance_id: str,
        operation_id: str,
        arguments: Mapping[str, Any],
        *,
        request_id: str,
        timeout_seconds: float,
        principal: DistributedPrincipal,
    ) -> Any:
        principal.require("distributed.service.invoke")
        if self.service_invoker is None:
            raise DistributedRuntimeError("service_invoker_not_configured")
        instance = self.store.get_instance(instance_id)
        self._require_active_membership(instance)
        if not instance.readiness or instance.status != "ready":
            raise DistributedRuntimeError("service_instance_not_ready")
        result = self.service_invoker.invoke(
            instance=instance,
            operation_id=str(operation_id),
            arguments=dict(arguments),
            request_id=str(request_id),
            timeout_seconds=max(1.0, min(float(timeout_seconds), 600.0)),
            actor_ref=principal.actor_ref,
        )
        self.store.append_audit(
            "service.instance.invoked",
            instance_id=instance.instance_id,
            operation_id=str(operation_id),
            request_id=str(request_id),
            actor_ref=principal.actor_ref,
        )
        return result

    def save_topology_plan(
        self, plan: TopologyPlan, *, principal: DistributedPrincipal
    ) -> TopologyPlan:
        principal.require("distributed.topology.plan")
        for step in plan.steps:
            partition = self.store.get_partition(step.partition_id)
            dataset = self.store.get_dataset(partition.dataset_id)
            if dataset.data_class == "external" and step.retention != "retain":
                raise DistributedRuntimeError("external_data_retention_must_be_retain")
        result = self.store.put_plan(plan)
        self.store.append_audit(
            "topology.plan.reviewed",
            plan_digest=result.plan_digest,
            actor_ref=principal.actor_ref,
        )
        return result

    def get_topology_plan(
        self, plan_digest: str, *, principal: DistributedPrincipal
    ) -> TopologyPlan:
        principal.require("distributed.topology.inspect")
        return self.store.get_plan(str(plan_digest))

    def plan_replica_change(
        self,
        partition_id: str,
        *,
        action: str,
        source_instance_id: str | None,
        target_instance_id: str | None,
        replica_role: str,
        principal: DistributedPrincipal,
    ) -> TopologyPlan:
        principal.require("distributed.topology.plan")
        partition = self.store.get_partition(partition_id)
        dataset = self.store.get_dataset(partition.dataset_id)
        replicas = _all_pages(self.store.list_replicas, partition_id=partition_id)
        expected_bytes = max(
            (
                item.byte_count
                for item in replicas
                if item.instance_id == source_instance_id
                and item.byte_count is not None
            ),
            default=None,
        )
        phases_by_action = {
            "create": (
                "inspect",
                "reserve",
                "prepare",
                "snapshot",
                "verify",
                "activate_read",
                "route",
            ),
            "move": (
                "inspect",
                "reserve",
                "prepare",
                "snapshot",
                "stream_deltas",
                "catch_up",
                "verify",
                "activate_read",
                "route",
                "drain",
                "remove",
                "release",
            ),
            "rebuild": (
                "inspect",
                "reserve",
                "prepare",
                "snapshot",
                "verify",
                "activate_read",
                "route",
            ),
            "handoff": (
                "inspect",
                "prepare",
                "stream_deltas",
                "catch_up",
                "verify",
                "promote",
                "route",
                "demote",
            ),
            "drain": ("inspect", "drain", "route"),
            "remove": ("inspect", "drain", "remove", "route"),
            "repair": (
                "inspect",
                "prepare",
                "snapshot",
                "verify",
                "activate_read",
                "route",
            ),
        }
        if action not in phases_by_action:
            raise DistributedRuntimeError("unsupported_topology_change")
        approvals: tuple[str, ...] = ()
        if action == "handoff":
            approvals = ("authority_handoff",)
        elif action == "remove":
            approvals = ("replica_remove",)
        step = TopologyPlanStep(
            step_id=f"{action}-{partition_id}",
            action=action,
            partition_id=partition_id,
            source_instance_id=source_instance_id,
            target_instance_id=target_instance_id,
            replica_role=replica_role,
            phases=phases_by_action[action],
            expected_bytes=expected_bytes,
            temporary_bytes=expected_bytes or 0,
            availability_impact="reduced_capacity"
            if action in {"move", "drain", "remove"}
            else "none",
            retention="retain" if dataset.data_class == "external" else "rebuild",
        )
        plan = TopologyPlan(
            plan_id=_identity(
                "plan",
                action,
                partition_id,
                partition.revision,
                partition.authority_epoch,
            ),
            kind="replicate" if action in {"create", "move", "rebuild"} else action,
            target_ref=f"partition:{partition_id}",
            expected_desired_revision=dataset.desired_revision,
            expected_observed_revision=partition.revision,
            authority_epoch=partition.authority_epoch,
            steps=(step,),
            required_approvals=approvals,
            warnings=("estimated byte count is unavailable",)
            if expected_bytes is None
            else (),
        )
        return self.save_topology_plan(plan, principal=principal)

    def plan_rebalance(
        self,
        dataset_id: str,
        *,
        max_steps: int = 16,
        max_parallel: int = 2,
        throughput_bytes_per_second: int = 25 * 1024 * 1024,
        principal: DistributedPrincipal,
    ) -> dict[str, Any]:
        """Build a bounded, reviewed replica plan; never mutate topology directly."""

        principal.require("distributed.topology.plan")
        dataset = self.store.get_dataset(dataset_id)
        partitions = _all_pages(self.store.list_partitions, dataset_id=dataset_id)
        groups = [
            item
            for item in _all_pages(self.store.list_groups)
            if dataset_id in item.linked_datasets
        ]
        group_ids = {item.group_id for item in groups}
        instances: list[ServiceInstance] = []
        for instance in _all_pages(self.store.list_instances):
            if (
                instance.group_id not in group_ids
                or instance.status != "ready"
                or not instance.readiness
            ):
                continue
            try:
                self._require_active_membership(instance)
            except DistributedRuntimeError:
                continue
            instances.append(instance)
        instances.sort(
            key=lambda item: (
                str(item.pressure.get("level") or "normal") != "normal",
                float(item.pressure.get("score") or 0),
                item.instance_id,
            )
        )
        bounded_steps = max(1, min(int(max_steps), 32))
        parallel = max(1, min(int(max_parallel), 4))
        throughput = max(
            1, min(int(throughput_bytes_per_second), 10 * 1024 * 1024 * 1024)
        )
        role = {
            "derived_projection": "derived",
            "read_through_cache": "cache",
        }.get(dataset.consistency_profile, "follower")
        steps: list[TopologyPlanStep] = []
        warnings: list[str] = []
        total_bytes = 0
        temporary_bytes = 0
        replica_load: dict[str, int] = {item.instance_id: 0 for item in instances}
        replicas_by_partition: dict[str, tuple[Replica, ...]] = {}
        for partition in partitions:
            replicas = tuple(
                item
                for item in _all_pages(
                    self.store.list_replicas, partition_id=partition.partition_id
                )
                if item.lifecycle not in {"removed", "failed"}
            )
            replicas_by_partition[partition.partition_id] = replicas
            for replica in replicas:
                replica_load[replica.instance_id] = (
                    replica_load.get(replica.instance_id, 0) + 1
                )

        for partition in sorted(partitions, key=lambda item: item.partition_id):
            if len(steps) >= bounded_steps:
                break
            replicas = list(replicas_by_partition[partition.partition_id])
            desired_count = partition.desired_replicas
            source = next(
                (
                    item
                    for item in replicas
                    if item.lifecycle == "ready"
                    and item.content_state not in {"unknown", "unavailable"}
                ),
                None,
            )
            expected_bytes = (
                source.byte_count
                if source is not None and source.byte_count is not None
                else int(partition.selector.get("estimated_bytes") or 0) or None
            )
            occupied = {item.instance_id for item in replicas}
            while len(replicas) < desired_count and len(steps) < bounded_steps:
                targets = [
                    item for item in instances if item.instance_id not in occupied
                ]
                targets.sort(
                    key=lambda item: (
                        replica_load.get(item.instance_id, 0),
                        item.instance_id,
                    )
                )
                if not targets:
                    warnings.append(
                        f"partition:{partition.partition_id}:no_eligible_target"
                    )
                    break
                target = targets[0]
                estimate = expected_bytes or 0
                step = TopologyPlanStep(
                    step_id=f"rebalance-create-{partition.partition_id}-{target.instance_id}",
                    action="create",
                    partition_id=partition.partition_id,
                    source_instance_id=source.instance_id
                    if source is not None
                    else None,
                    target_instance_id=target.instance_id,
                    replica_role=role,
                    phases=(
                        "inspect",
                        "reserve",
                        "prepare",
                        "snapshot",
                        "stream_deltas",
                        "catch_up",
                        "verify",
                        "activate_read",
                        "route",
                    ),
                    expected_bytes=expected_bytes,
                    temporary_bytes=estimate,
                    retention="retain"
                    if dataset.data_class == "external"
                    else "rebuild",
                    adapter_options={
                        "max_parallel": parallel,
                        "expected_partition_revision": partition.revision,
                        "estimated_seconds": None
                        if expected_bytes is None
                        else max(1, (expected_bytes + throughput - 1) // throughput),
                    },
                )
                steps.append(step)
                occupied.add(target.instance_id)
                replica_load[target.instance_id] = (
                    replica_load.get(target.instance_id, 0) + 1
                )
                replicas.append(
                    replace(
                        source,
                        replica_id=f"planned:{step.step_id}",
                        instance_id=target.instance_id,
                        node_id=target.node_id,
                        role=role,
                    )
                    if source is not None
                    else None
                )
                total_bytes += estimate
                temporary_bytes += estimate
            removable = [
                item
                for item in replicas
                if item is not None and item.role != "authority"
            ]
            removable.sort(
                key=lambda item: (
                    item.lifecycle == "ready",
                    -(item.freshness_seconds or 0),
                    item.replica_id,
                )
            )
            while (
                len([item for item in replicas if item is not None]) > desired_count
                and removable
                and len(steps) < bounded_steps
            ):
                candidate = removable.pop(0)
                steps.append(
                    TopologyPlanStep(
                        step_id=f"rebalance-remove-{partition.partition_id}-{candidate.instance_id}",
                        action="remove",
                        partition_id=partition.partition_id,
                        source_instance_id=candidate.instance_id,
                        target_instance_id=None,
                        replica_role=candidate.role,
                        phases=("inspect", "drain", "remove", "route", "release"),
                        expected_bytes=candidate.byte_count,
                        availability_impact="reduced_capacity",
                        retention="retain"
                        if dataset.data_class == "external"
                        else "rebuild",
                        adapter_options={
                            "max_parallel": parallel,
                            "expected_partition_revision": partition.revision,
                            "estimated_seconds": 1,
                        },
                    )
                )
                replicas.remove(candidate)

        estimated_seconds = (
            0
            if total_bytes == 0
            else max(
                1, (total_bytes + throughput * parallel - 1) // (throughput * parallel)
            )
        )
        if any(step.expected_bytes is None for step in steps):
            warnings.append("one_or_more_step_byte_estimates_unavailable")
        plan: TopologyPlan | None = None
        if steps:
            observed_revision = dataset.observed_revision
            authority_epoch = max(
                (item.authority_epoch for item in partitions), default=0
            )
            plan = self.save_topology_plan(
                TopologyPlan(
                    plan_id=_identity(
                        "plan",
                        "rebalance",
                        dataset_id,
                        dataset.desired_revision,
                        observed_revision,
                        *[item.step_id for item in steps],
                    ),
                    kind="reconcile",
                    target_ref=f"dataset:{dataset_id}",
                    expected_desired_revision=dataset.desired_revision,
                    expected_observed_revision=observed_revision,
                    authority_epoch=authority_epoch,
                    steps=tuple(steps),
                    required_approvals=("replica_remove",)
                    if any(item.action == "remove" for item in steps)
                    else (),
                    warnings=tuple(dict.fromkeys(warnings)),
                ),
                principal=principal,
            )
        return {
            "schema": "adaos.distributed.rebalance_plan.v1",
            "dataset_id": dataset_id,
            "dry_run": True,
            "status": "ready" if plan is not None else "noop",
            "plan": plan.to_dict() if plan is not None else None,
            "estimates": {
                "bytes": total_bytes,
                "temporary_bytes": temporary_bytes,
                "seconds": estimated_seconds,
                "throughput_bytes_per_second": throughput,
                "max_parallel": parallel,
            },
            "warnings": list(dict.fromkeys(warnings)),
            "truncated": len(steps) >= bounded_steps,
        }

    def apply_topology_plan(
        self,
        plan_digest: str,
        *,
        idempotency_key: str,
        principal: DistributedPrincipal,
    ) -> TopologyOperation:
        if self.topology_adapter is None:
            raise DistributedRuntimeError("topology_adapter_not_configured")
        plan = self.store.get_plan(plan_digest)
        operation = TopologyExecutor(
            store=self.store,
            adapter=self.topology_adapter,
            authority_handoff=lambda step,
            operation,
            actor,
            epoch: self.handoff_authority(
                step.partition_id,
                str(step.target_instance_id or ""),
                expected_partition_revision=self.store.get_partition(
                    step.partition_id
                ).revision,
                expected_epoch=epoch,
                operation_id=operation.operation_id,
                principal=actor,
            ).epoch,
        ).execute(
            plan,
            principal=principal,
            idempotency_key=idempotency_key,
        )
        self._publish_projection()
        return operation

    def define_group(
        self,
        group: ServiceGroup,
        *,
        expected_revision: int,
        principal: DistributedPrincipal,
    ) -> ServiceGroup:
        principal.require("distributed.service.manage")
        definition = self.store.get_definition(
            group.definition_id, group.definition_version
        )
        try:
            previous_group = self.store.get_group(group.group_id)
        except FileNotFoundError:
            previous_group = None
        if previous_group is not None:
            if previous_group.definition_id != group.definition_id:
                raise DistributedRuntimeError("service_group_definition_changed")
            if previous_group.definition_version != group.definition_version:
                previous_definition = self.store.get_definition(
                    previous_group.definition_id,
                    previous_group.definition_version,
                )
                compatible = (
                    previous_definition.protocol_version == definition.protocol_version
                    and set(previous_definition.provided_contracts).issubset(
                        definition.provided_contracts
                    )
                    and set(previous_definition.adapter_contracts).issubset(
                        definition.adapter_contracts
                    )
                )
                if not compatible:
                    raise DistributedRuntimeError(
                        "incompatible_service_definition_upgrade"
                    )
                if group.desired_generation <= previous_group.desired_generation:
                    raise DistributedRuntimeError(
                        "service_definition_upgrade_requires_new_generation"
                    )
        result = self.store.save_group(
            group, expected_revision=expected_revision, actor_ref=principal.actor_ref
        )
        self._publish_projection()
        return result

    def register_instance(
        self,
        instance: ServiceInstance,
        *,
        expected_revision: int,
        lease_seconds: int = 90,
        principal: DistributedPrincipal,
    ) -> ServiceInstance:
        principal.require("distributed.service.register")
        group = self.store.get_group(instance.group_id)
        definition = self.store.get_definition(
            group.definition_id, group.definition_version
        )
        activation = self.deployment_store.get_activation(instance.activation_id)
        if activation.status != "active":
            raise DistributedRuntimeError("component_activation_not_active")
        if (
            activation.node_id != instance.node_id
            or activation.release_digest != instance.release_digest
            or activation.component_ref != instance.component_ref
            or activation.generation != instance.runtime_generation
        ):
            raise DistributedRuntimeError("component_activation_identity_mismatch")
        if (
            definition.release_digest != instance.release_digest
            or instance.component_ref not in definition.compatible_components
            or definition.protocol_version != instance.protocol_version
            or group.desired_generation != instance.topology_generation
        ):
            raise DistributedRuntimeError("service_registration_incompatible")
        deployment = self.deployment_store.get_deployment(activation.deployment_id)
        if deployment.release_digest != instance.release_digest:
            raise DistributedRuntimeError("deployment_release_mismatch")
        release = self.releases.get_release(
            deployment.project_ref.split(":", 1)[-1], deployment.release_digest
        )
        component_parts = instance.component_ref.split(":", 1)
        if not any(
            package.kind == component_parts[0]
            and package.artifact_id == component_parts[1]
            for package in release.packages
        ):
            raise DistributedRuntimeError("component_missing_from_project_release")
        nodes = {
            item.node_id: item
            for item in self.inventory.list_nodes(deployment.subnet_id)
        }
        node = nodes.get(instance.node_id)
        if node is None or node.trust_state != "trusted" or not node.online:
            raise DistributedRuntimeError("service_node_not_trusted_or_online")
        if not set(definition.required_capabilities).issubset(node.capabilities):
            raise DistributedRuntimeError("service_node_capability_mismatch")
        if node.protocols.get("distributed_runtime") != definition.protocol_version:
            raise DistributedRuntimeError("service_node_protocol_mismatch")
        self._admit_placement(group, instance, node=node)

        try:
            previous_instance = self.store.get_instance(instance.instance_id)
        except FileNotFoundError:
            previous_instance = None
        if previous_instance is not None:
            previous_lease = self.store.get_lease(previous_instance.lease_id)
            if previous_lease.status == "active":
                self.store.put_lease(replace(previous_lease, status="released"))

        now = self.clock()
        duration = max(30, min(int(lease_seconds), 600))
        renew_by = now + timedelta(seconds=max(10, duration * 2 // 3))
        valid_until = now + timedelta(seconds=duration)
        lease_id = _identity(
            "membership", instance.instance_id, instance.runtime_generation, _iso(now)
        )
        lease = TopologyLease(
            lease_id=lease_id,
            scope_ref=f"service_group:{group.group_id}",
            owner_instance_id=instance.instance_id,
            kind="membership",
            epoch=0,
            topology_generation=group.desired_generation,
            operation_ref=None,
            issued_at=_iso(now),
            renew_by=_iso(renew_by),
            valid_until=_iso(valid_until),
        )
        self.store.put_lease(lease)
        registered = replace(
            instance,
            lease_id=lease_id,
            observed_at=_iso(now),
            revision=expected_revision + 1,
        )
        result = self.store.put_instance(
            registered, expected_revision=expected_revision
        )
        self.store.append_audit(
            "service.instance.registered",
            instance_id=result.instance_id,
            activation_id=result.activation_id,
            actor_ref=principal.actor_ref,
        )
        self._publish_projection()
        return result

    def renew_instance(
        self,
        instance_id: str,
        *,
        expected_revision: int,
        readiness: bool,
        status: str,
        health: Mapping[str, Any],
        pressure: Mapping[str, Any],
        lease_seconds: int = 90,
        principal: DistributedPrincipal,
    ) -> ServiceInstance:
        principal.require("distributed.service.renew")
        instance = self.store.get_instance(instance_id)
        if instance.revision != expected_revision:
            raise DistributedRuntimeError("service_instance_revision_conflict")
        lease = self.store.get_lease(instance.lease_id)
        now = self.clock()
        if lease.status != "active" or _utc(lease.valid_until) <= now:
            raise DistributedRuntimeError("membership_lease_expired")
        duration = max(30, min(int(lease_seconds), 600))
        renewed = replace(
            lease,
            renew_by=_iso(now + timedelta(seconds=max(10, duration * 2 // 3))),
            valid_until=_iso(now + timedelta(seconds=duration)),
        )
        self.store.put_lease(renewed)
        observed = replace(
            instance,
            readiness=readiness,
            status=status,
            health=dict(health),
            pressure=dict(pressure),
            observed_at=_iso(now),
            revision=expected_revision + 1,
        )
        result = self.store.put_instance(observed, expected_revision=expected_revision)
        self._publish_projection()
        return result

    def expire_leases(self, *, principal: DistributedPrincipal) -> tuple[str, ...]:
        principal.require("distributed.service.reconcile")
        now = self.clock()
        expired: list[str] = []
        for lease in _all_pages(self.store.list_leases):
            if lease.status != "active" or _utc(lease.valid_until) > now:
                continue
            self.store.put_lease(replace(lease, status="expired"))
            expired.append(lease.lease_id)
            if lease.kind == "membership":
                try:
                    instance = self.store.get_instance(lease.owner_instance_id)
                except FileNotFoundError:
                    continue
                if instance.lease_id == lease.lease_id and instance.status != "expired":
                    self.store.put_instance(
                        replace(
                            instance,
                            status="expired",
                            readiness=False,
                            observed_at=_iso(now),
                            revision=instance.revision + 1,
                        ),
                        expected_revision=instance.revision,
                    )
        if expired:
            self.store.append_audit(
                "topology.leases.expired",
                lease_ids=expired,
                actor_ref=principal.actor_ref,
            )
            self._publish_projection()
        return tuple(expired)

    def drain_instance(
        self,
        instance_id: str,
        *,
        expected_revision: int,
        principal: DistributedPrincipal,
    ) -> ServiceInstance:
        principal.require("distributed.service.drain")
        instance = self.store.get_instance(instance_id)
        drained = replace(
            instance,
            status="draining",
            readiness=False,
            observed_at=_iso(self.clock()),
            revision=expected_revision + 1,
        )
        result = self.store.put_instance(drained, expected_revision=expected_revision)
        self.store.append_audit(
            "service.instance.draining",
            instance_id=instance_id,
            actor_ref=principal.actor_ref,
        )
        self._publish_projection()
        return result

    def define_dataset(
        self,
        dataset: Dataset,
        *,
        expected_revision: int,
        principal: DistributedPrincipal,
    ) -> Dataset:
        principal.require("distributed.topology.manage")
        if (
            dataset.data_class == "external"
            and dataset.retention.get("on_remove") != "retain"
        ):
            raise DistributedRuntimeError("external_data_retention_must_be_retain")
        result = self.store.save_dataset(
            dataset, expected_revision=expected_revision, actor_ref=principal.actor_ref
        )
        self._publish_projection()
        return result

    def put_partition(
        self,
        partition: Partition,
        *,
        expected_revision: int,
        principal: DistributedPrincipal,
    ) -> Partition:
        principal.require("distributed.topology.manage")
        self.store.get_dataset(partition.dataset_id)
        result = self.store.put_partition(
            partition, expected_revision=expected_revision
        )
        self._publish_projection()
        return result

    def observe_replica(
        self,
        replica: Replica,
        *,
        expected_revision: int,
        principal: DistributedPrincipal,
    ) -> Replica:
        principal.require("distributed.replica.observe")
        partition = self.store.get_partition(replica.partition_id)
        dataset = self.store.get_dataset(partition.dataset_id)
        instance = self.store.get_instance(replica.instance_id)
        if replica.node_id != instance.node_id:
            raise DistributedRuntimeError("replica_node_identity_mismatch")
        self._require_active_membership(instance)
        group = self.store.get_group(instance.group_id)
        if dataset.dataset_id not in group.linked_datasets:
            raise DistributedRuntimeError("dataset_not_linked_to_service_group")
        if dataset.consistency_profile == "external_authority":
            if replica.role not in {"authority", "follower"} or not replica.source_ref:
                raise DistributedRuntimeError("external_authority_replica_invalid")
        if (
            dataset.consistency_profile == "derived_projection"
            and replica.role != "derived"
        ):
            raise DistributedRuntimeError("derived_projection_replica_invalid")
        if replica.role == "authority":
            if replica.authority_epoch != partition.authority_epoch:
                raise StaleAuthorityEpochError("replica_authority_epoch_mismatch")
            self.assert_authority(
                scope_ref=f"partition:{partition.partition_id}",
                instance_id=instance.instance_id,
                epoch=replica.authority_epoch,
            )
        result = self.store.put_replica(replica, expected_revision=expected_revision)
        self._publish_projection()
        return result

    def handoff_authority(
        self,
        partition_id: str,
        target_instance_id: str,
        *,
        expected_partition_revision: int,
        expected_epoch: int,
        operation_id: str,
        lease_seconds: int = 120,
        principal: DistributedPrincipal,
    ) -> TopologyLease:
        principal.require_approval(
            "authority_handoff", permission="distributed.authority.handoff"
        )
        partition = self.store.get_partition(partition_id)
        if partition.revision != expected_partition_revision:
            raise DistributedRuntimeError("partition_revision_conflict")
        instance = self.store.get_instance(target_instance_id)
        self._require_active_membership(instance)
        if not instance.readiness or instance.status != "ready":
            raise DistributedRuntimeError("authority_target_not_ready")
        if expected_epoch > 0:
            candidates = [
                item
                for item in _all_pages(
                    self.store.list_replicas, partition_id=partition.partition_id
                )
                if item.instance_id == target_instance_id
                and item.lifecycle == "ready"
                and item.content_state not in {"unknown", "unavailable"}
            ]
            if not candidates or any(
                partition.checkpoint is not None
                and item.checkpoint != partition.checkpoint
                for item in candidates
            ):
                raise DistributedRuntimeError("authority_target_not_caught_up")
        now = self.clock()
        duration = max(30, min(int(lease_seconds), 600))
        lease_id = _identity(
            "authority", partition_id, expected_epoch + 1, target_instance_id
        )
        lease = TopologyLease(
            lease_id=lease_id,
            scope_ref=f"partition:{partition_id}",
            owner_instance_id=target_instance_id,
            kind="authority",
            epoch=expected_epoch + 1,
            topology_generation=partition.topology_generation,
            operation_ref=operation_id,
            issued_at=_iso(now),
            renew_by=_iso(now + timedelta(seconds=duration * 2 // 3)),
            valid_until=_iso(now + timedelta(seconds=duration)),
            previous_lease_id=partition.authority_lease_id,
        )
        granted = self.store.handoff_authority(
            lease, expected_epoch=expected_epoch, actor_ref=principal.actor_ref
        )
        self.store.put_partition(
            replace(
                partition,
                authority_lease_id=granted.lease_id,
                authority_epoch=granted.epoch,
                status="moving",
                revision=partition.revision + 1,
            ),
            expected_revision=partition.revision,
        )
        self._publish_projection()
        return granted

    def assert_authority(self, *, scope_ref: str, instance_id: str, epoch: int) -> None:
        now = self.clock()
        leases = _all_pages(self.store.list_leases, scope_ref=scope_ref)
        current = max(
            (item for item in leases if item.kind == "authority"),
            key=lambda item: item.epoch,
            default=None,
        )
        if (
            current is None
            or current.status != "active"
            or _utc(current.valid_until) <= now
            or current.owner_instance_id != instance_id
            or current.epoch != epoch
        ):
            raise StaleAuthorityEpochError("stale_or_unowned_authority_epoch")

    def resolve_route(
        self,
        dataset_id: str,
        partition_ids: Iterable[str],
        *,
        auth_scope: str,
        max_staleness_seconds: float | None = None,
        allow_partial: bool = False,
        allow_stale_fallback: bool = False,
        ttl_seconds: int = 60,
        principal: DistributedPrincipal,
    ) -> DistributedRoute:
        principal.require("distributed.route.grant")
        principal.require(f"scope:{auth_scope}")
        dataset = self.store.get_dataset(dataset_id)
        requested = tuple(
            dict.fromkeys(str(item) for item in partition_ids if str(item))
        )
        if not requested:
            raise DistributedRuntimeError("route_requires_partitions")
        now = self.clock()
        endpoints: list[RouteEndpoint] = []
        unavailable: list[str] = []
        topology_generation = 1
        topology_revision = 1
        stale_used = False
        for partition_id in requested:
            try:
                partition = self.store.get_partition(partition_id)
            except FileNotFoundError:
                unavailable.append(partition_id)
                continue
            if partition.dataset_id != dataset_id or partition.status in {
                "unavailable",
                "removed",
            }:
                unavailable.append(partition_id)
                continue
            topology_generation = max(
                topology_generation, partition.topology_generation
            )
            topology_revision = max(topology_revision, partition.revision)
            eligible: list[tuple[Replica, ServiceInstance]] = []
            for replica in _all_pages(
                self.store.list_replicas, partition_id=partition_id
            ):
                if replica.lifecycle not in {"ready", "stale"}:
                    continue
                if replica.content_state in {"unknown", "unavailable"}:
                    continue
                if replica.lifecycle == "stale" and not allow_stale_fallback:
                    continue
                if (
                    max_staleness_seconds is not None
                    and replica.freshness_seconds is not None
                    and replica.freshness_seconds > max_staleness_seconds
                    and not allow_stale_fallback
                ):
                    continue
                try:
                    instance = self.store.get_instance(replica.instance_id)
                    self._require_active_membership(instance)
                except (FileNotFoundError, DistributedRuntimeError):
                    continue
                if (
                    not instance.readiness
                    or instance.status != "ready"
                    or not instance.endpoints
                ):
                    continue
                if replica.role == "authority":
                    try:
                        self.assert_authority(
                            scope_ref=f"partition:{partition_id}",
                            instance_id=instance.instance_id,
                            epoch=replica.authority_epoch,
                        )
                    except StaleAuthorityEpochError:
                        continue
                eligible.append((replica, instance))
            if not eligible:
                unavailable.append(partition_id)
                continue
            eligible.sort(
                key=lambda pair: (
                    pair[0].lifecycle == "stale",
                    pair[0].freshness_seconds is None,
                    pair[0].freshness_seconds or 0,
                    pair[0].replica_id,
                )
            )
            for priority, (replica, instance) in enumerate(eligible):
                stale_used = stale_used or replica.lifecycle == "stale"
                endpoints.append(
                    RouteEndpoint(
                        endpoint_ref=instance.endpoints[0].address_ref,
                        replica_id=replica.replica_id,
                        partition_id=partition_id,
                        role=replica.role,
                        priority=priority,
                        authority_epoch=replica.authority_epoch,
                        checkpoint=replica.checkpoint,
                        freshness_seconds=replica.freshness_seconds,
                        observed_at=replica.observed_at,
                    )
                )
        if unavailable and not allow_partial:
            raise DistributedRuntimeError(
                "route_partitions_unavailable:" + ",".join(sorted(unavailable))
            )
        ttl = max(5, min(int(ttl_seconds), 300))
        route = DistributedRoute(
            route_id=_identity(
                "route",
                dataset_id,
                *requested,
                topology_revision,
                auth_scope,
                _iso(now),
            ),
            dataset_id=dataset_id,
            partition_ids=requested,
            endpoints=tuple(endpoints),
            consistency_profile=dataset.consistency_profile,
            topology_generation=topology_generation,
            topology_revision=topology_revision,
            partial=bool(unavailable),
            unavailable_partitions=tuple(unavailable),
            fallback="stale"
            if stale_used
            else ("coordinator" if unavailable else "none"),
            auth_scope=auth_scope,
            created_at=_iso(now),
            expires_at=_iso(now + timedelta(seconds=ttl)),
        )
        result = self.store.put_route(route)
        self.store.append_audit(
            "route.authorization.granted",
            route_id=result.route_id,
            auth_scope=auth_scope,
            actor_ref=principal.actor_ref,
        )
        self._publish_projection()
        return result

    def inspect(
        self,
        *,
        principal: DistributedPrincipal,
        cursors: Mapping[str, str | None] | None = None,
        limit: int = 50,
    ) -> DistributedInspection:
        principal.require("distributed.topology.inspect")
        cursors = dict(cursors or {})
        groups, group_cursor = self.store.list_groups(
            cursor=cursors.get("groups"), limit=limit
        )
        instances, instance_cursor = self.store.list_instances(
            cursor=cursors.get("instances"), limit=limit
        )
        leases, lease_cursor = self.store.list_leases(
            cursor=cursors.get("leases"), limit=limit
        )
        datasets, dataset_cursor = self.store.list_datasets(
            cursor=cursors.get("datasets"), limit=limit
        )
        partitions, partition_cursor = self.store.list_partitions(
            cursor=cursors.get("partitions"), limit=limit
        )
        replicas, replica_cursor = self.store.list_replicas(
            cursor=cursors.get("replicas"), limit=limit
        )
        operations, operation_cursor = self.store.list_operations(
            cursor=cursors.get("operations"), limit=limit
        )
        return DistributedInspection(
            groups=groups,
            instances=instances,
            leases=leases,
            datasets=datasets,
            partitions=partitions,
            replicas=replicas,
            operations=operations,
            cursors={
                "groups": group_cursor,
                "instances": instance_cursor,
                "leases": lease_cursor,
                "datasets": dataset_cursor,
                "partitions": partition_cursor,
                "replicas": replica_cursor,
                "operations": operation_cursor,
            },
        )

    def explain_route(
        self,
        dataset_id: str,
        partition_ids: Iterable[str],
        *,
        principal: DistributedPrincipal,
    ) -> dict[str, Any]:
        principal.require("distributed.topology.inspect")
        requested = tuple(dict.fromkeys(str(item) for item in partition_ids))
        explanation: list[dict[str, Any]] = []
        for partition_id in requested:
            try:
                partition = self.store.get_partition(partition_id)
            except FileNotFoundError:
                explanation.append(
                    {
                        "partition_id": partition_id,
                        "eligible": False,
                        "reason": "missing",
                    }
                )
                continue
            replicas = _all_pages(self.store.list_replicas, partition_id=partition_id)
            explanation.append(
                {
                    "partition_id": partition_id,
                    "eligible": partition.dataset_id == dataset_id and bool(replicas),
                    "partition_status": partition.status,
                    "replicas": [
                        {
                            "replica_id": item.replica_id,
                            "lifecycle": item.lifecycle,
                            "content_state": item.content_state,
                            "freshness_seconds": item.freshness_seconds,
                            "authority_epoch": item.authority_epoch,
                        }
                        for item in replicas[:20]
                    ],
                    "truncated": len(replicas) > 20,
                }
            )
        return {
            "schema": "adaos.distributed.route_explain.v1",
            "dataset_id": dataset_id,
            "partitions": explanation,
        }

    def _require_active_membership(self, instance: ServiceInstance) -> TopologyLease:
        lease = self.store.get_lease(instance.lease_id)
        if (
            lease.kind != "membership"
            or lease.owner_instance_id != instance.instance_id
            or lease.status != "active"
            or _utc(lease.valid_until) <= self.clock()
        ):
            raise DistributedRuntimeError("instance_membership_not_active")
        return lease

    def _admit_placement(
        self,
        group: ServiceGroup,
        candidate: ServiceInstance,
        *,
        node: NodeInventoryRecord,
    ) -> None:
        existing = _all_pages(self.store.list_instances, group_id=group.group_id)
        active = [item for item in existing if self._occupies_capacity(item)]
        if not any(item.instance_id == candidate.instance_id for item in active):
            if len(active) >= group.desired_instances:
                raise DistributedRuntimeError("service_group_capacity_exhausted")
        max_per_node = int(group.placement.get("max_instances_per_node", 1))
        same_node = [
            item
            for item in active
            if item.node_id == candidate.node_id
            and item.instance_id != candidate.instance_id
        ]
        if len(same_node) >= max_per_node:
            raise DistributedRuntimeError("service_group_node_capacity_exhausted")
        anti_affinity_label = str(
            group.placement.get("anti_affinity_label") or ""
        ).strip()
        if anti_affinity_label:
            candidate_value = node.labels.get(anti_affinity_label)
            if candidate_value:
                deployment = self.deployment_store.get_deployment(
                    self.deployment_store.get_activation(
                        candidate.activation_id
                    ).deployment_id
                )
                nodes = {
                    item.node_id: item
                    for item in self.inventory.list_nodes(deployment.subnet_id)
                }
                if any(
                    nodes.get(item.node_id) is not None
                    and nodes[item.node_id].labels.get(anti_affinity_label)
                    == candidate_value
                    for item in active
                    if item.instance_id != candidate.instance_id
                ):
                    raise DistributedRuntimeError(
                        "service_group_anti_affinity_conflict"
                    )

    def _occupies_capacity(self, instance: ServiceInstance) -> bool:
        if instance.status in {"draining", "expired", "failed"}:
            return False
        try:
            lease = self.store.get_lease(instance.lease_id)
        except FileNotFoundError:
            return False
        return (
            lease.kind == "membership"
            and lease.owner_instance_id == instance.instance_id
            and lease.status == "active"
            and _utc(lease.valid_until) > self.clock()
        )

    def _publish_projection(self) -> None:
        if self.projection_publisher is None:
            return
        projection = build_distributed_projection(
            groups=_all_pages(self.store.list_groups),
            instances=_all_pages(self.store.list_instances),
            leases=_all_pages(self.store.list_leases),
            datasets=_all_pages(self.store.list_datasets),
            partitions=_all_pages(self.store.list_partitions),
            replicas=_all_pages(self.store.list_replicas),
            operations=_all_pages(self.store.list_operations),
            routes=_all_pages(self.store.list_routes),
        )
        self.projection_publisher(projection)


_runtime_lock = RLock()
_runtime: DistributedRuntime | None = None


def register_distributed_runtime(runtime: DistributedRuntime | None) -> None:
    global _runtime
    with _runtime_lock:
        _runtime = runtime


def get_distributed_runtime() -> DistributedRuntime:
    with _runtime_lock:
        if _runtime is None:
            raise RuntimeError("distributed runtime is not configured")
        return _runtime


__all__ = [
    "DistributedInspection",
    "DistributedNodeInventoryProvider",
    "DistributedReleaseProvider",
    "DistributedRuntime",
    "DistributedRuntimeError",
    "StaleAuthorityEpochError",
    "get_distributed_runtime",
    "register_distributed_runtime",
]
