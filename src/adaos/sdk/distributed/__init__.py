"""Public SDK for distributed service membership, topology, routes, and operations."""

from __future__ import annotations

from typing import Iterable, Mapping

from adaos.domain.distributed_operations import TopologyPlan, TopologyPlanStep
from adaos.domain.distributed_runtime import (
    Dataset,
    DistributedRoute,
    Partition,
    Replica,
    ServiceDefinition,
    ServiceEndpoint,
    ServiceGroup,
    ServiceInstance,
    TopologyLease,
    TopologyOperation,
    TransferRecord,
)
from adaos.sdk.core._ctx import require_ctx
from adaos.services.distributed_runtime import (
    AuthenticatedTransferSink,
    AuthenticatedTransferSource,
    BoundedTransferController,
    DistributedInspection,
    DistributedPrincipal,
    RetryableTopologyPhaseError,
    TopologyAdapter,
    TopologyStepContext,
    UncertainTopologyPhaseError,
    get_distributed_runtime,
)
from adaos.services.policy.skill_capabilities import require_skill_capability


_APPROVAL_PERMISSIONS = {
    "authority_handoff": "distributed.authority.handoff",
    "replica_remove": "distributed.replica.remove",
    "replica_data_delete": "distributed.data.delete",
}


def _principal(
    required: Iterable[str],
    *,
    approvals: Iterable[str] = (),
    scopes: Iterable[str] = (),
) -> DistributedPrincipal:
    ctx = require_ctx("sdk.distributed")
    current = ctx.skill_ctx.get()
    skill_name = str(getattr(current, "name", "") or "").strip()
    if not skill_name:
        raise RuntimeError("distributed SDK requires an active skill context")
    permissions = {str(item) for item in required}
    approval_set = {str(item) for item in approvals}
    for permission in sorted(permissions):
        require_skill_capability(ctx, permission)
    for approval in sorted(approval_set):
        permission = _APPROVAL_PERMISSIONS.get(approval)
        if permission is None:
            raise ValueError(f"unsupported distributed approval: {approval}")
        require_skill_capability(ctx, permission)
        permissions.add(permission)
    for scope in sorted({str(item) for item in scopes}):
        require_skill_capability(ctx, scope)
        permissions.add(f"scope:{scope}")
    return DistributedPrincipal.create(
        actor_ref=f"skill:{skill_name}",
        permissions=permissions,
        approvals=approval_set,
    )


def define_service(definition: ServiceDefinition) -> ServiceDefinition:
    return get_distributed_runtime().define_service(
        definition, principal=_principal(("distributed.service.manage",))
    )


def define_group(group: ServiceGroup, *, expected_revision: int) -> ServiceGroup:
    return get_distributed_runtime().define_group(
        group,
        expected_revision=expected_revision,
        principal=_principal(("distributed.service.manage",)),
    )


def register(
    instance: ServiceInstance,
    *,
    expected_revision: int = 0,
    lease_seconds: int = 90,
) -> ServiceInstance:
    """Join a service through an exact active Project component activation."""

    return get_distributed_runtime().register_instance(
        instance,
        expected_revision=expected_revision,
        lease_seconds=lease_seconds,
        principal=_principal(("distributed.service.register",)),
    )


def renew(
    instance_id: str,
    *,
    expected_revision: int,
    readiness: bool,
    status: str,
    health: Mapping[str, object],
    pressure: Mapping[str, object],
    lease_seconds: int = 90,
) -> ServiceInstance:
    return get_distributed_runtime().renew_instance(
        instance_id,
        expected_revision=expected_revision,
        readiness=readiness,
        status=status,
        health=health,
        pressure=pressure,
        lease_seconds=lease_seconds,
        principal=_principal(("distributed.service.renew",)),
    )


def drain(instance_id: str, *, expected_revision: int) -> ServiceInstance:
    return get_distributed_runtime().drain_instance(
        instance_id,
        expected_revision=expected_revision,
        principal=_principal(("distributed.service.drain",)),
    )


def invoke(
    instance_id: str,
    operation_id: str,
    arguments: Mapping[str, object] | None = None,
    *,
    request_id: str,
    timeout_seconds: float = 30.0,
) -> object:
    """Invoke one public operation on an admitted distributed service instance."""

    return get_distributed_runtime().invoke_instance(
        instance_id,
        operation_id,
        arguments or {},
        request_id=request_id,
        timeout_seconds=timeout_seconds,
        principal=_principal(("distributed.service.invoke",)),
    )


def define_dataset(dataset: Dataset, *, expected_revision: int) -> Dataset:
    return get_distributed_runtime().define_dataset(
        dataset,
        expected_revision=expected_revision,
        principal=_principal(("distributed.topology.manage",)),
    )


def put_partition(partition: Partition, *, expected_revision: int) -> Partition:
    return get_distributed_runtime().put_partition(
        partition,
        expected_revision=expected_revision,
        principal=_principal(("distributed.topology.manage",)),
    )


def observe_replica(replica: Replica, *, expected_revision: int) -> Replica:
    return get_distributed_runtime().observe_replica(
        replica,
        expected_revision=expected_revision,
        principal=_principal(("distributed.replica.observe",)),
    )


def route(
    dataset_id: str,
    partition_ids: Iterable[str],
    *,
    auth_scope: str,
    max_staleness_seconds: float | None = None,
    allow_partial: bool = False,
    allow_stale_fallback: bool = False,
    ttl_seconds: int = 60,
) -> DistributedRoute:
    return get_distributed_runtime().resolve_route(
        dataset_id,
        partition_ids,
        auth_scope=auth_scope,
        max_staleness_seconds=max_staleness_seconds,
        allow_partial=allow_partial,
        allow_stale_fallback=allow_stale_fallback,
        ttl_seconds=ttl_seconds,
        principal=_principal(("distributed.route.grant",), scopes=(auth_scope,)),
    )


def plan_replica_change(
    partition_id: str,
    *,
    action: str,
    source_instance_id: str | None,
    target_instance_id: str | None,
    replica_role: str,
) -> TopologyPlan:
    return get_distributed_runtime().plan_replica_change(
        partition_id,
        action=action,
        source_instance_id=source_instance_id,
        target_instance_id=target_instance_id,
        replica_role=replica_role,
        principal=_principal(("distributed.topology.plan",)),
    )


def save_plan(plan: TopologyPlan) -> TopologyPlan:
    return get_distributed_runtime().save_topology_plan(
        plan, principal=_principal(("distributed.topology.plan",))
    )


def get_plan(plan_digest: str) -> TopologyPlan:
    """Read one immutable reviewed topology plan by content digest."""

    return get_distributed_runtime().get_topology_plan(
        plan_digest,
        principal=_principal(("distributed.topology.inspect",)),
    )


def get_operation(operation_id: str) -> TopologyOperation:
    """Read one durable topology operation by its stable identifier."""

    return get_distributed_runtime().get_topology_operation(
        operation_id,
        principal=_principal(("distributed.topology.inspect",)),
    )


def plan_rebalance(
    dataset_id: str,
    *,
    max_steps: int = 16,
    max_parallel: int = 2,
    throughput_bytes_per_second: int = 25 * 1024 * 1024,
) -> dict[str, object]:
    """Create a bounded reviewed rebalance plan with explicit resource estimates."""

    return get_distributed_runtime().plan_rebalance(
        dataset_id,
        max_steps=max_steps,
        max_parallel=max_parallel,
        throughput_bytes_per_second=throughput_bytes_per_second,
        principal=_principal(("distributed.topology.plan",)),
    )


def apply_plan(
    plan_digest: str,
    *,
    idempotency_key: str,
    approvals: Iterable[str] = (),
) -> TopologyOperation:
    return get_distributed_runtime().apply_topology_plan(
        plan_digest,
        idempotency_key=idempotency_key,
        principal=_principal(("distributed.topology.apply",), approvals=approvals),
    )


def handoff_authority(
    partition_id: str,
    target_instance_id: str,
    *,
    expected_partition_revision: int,
    expected_epoch: int,
    operation_id: str,
    lease_seconds: int = 120,
) -> TopologyLease:
    return get_distributed_runtime().handoff_authority(
        partition_id,
        target_instance_id,
        expected_partition_revision=expected_partition_revision,
        expected_epoch=expected_epoch,
        operation_id=operation_id,
        lease_seconds=lease_seconds,
        principal=_principal(
            ("distributed.authority.handoff",), approvals=("authority_handoff",)
        ),
    )


def inspect(
    *, cursors: Mapping[str, str | None] | None = None, limit: int = 50
) -> DistributedInspection:
    return get_distributed_runtime().inspect(
        cursors=cursors,
        limit=limit,
        principal=_principal(("distributed.topology.inspect",)),
    )


def explain_route(dataset_id: str, partition_ids: Iterable[str]) -> dict:
    return get_distributed_runtime().explain_route(
        dataset_id,
        partition_ids,
        principal=_principal(("distributed.topology.inspect",)),
    )


OPERATOR_PROJECTION_SCHEMA = "adaos.distributed.operator_projection.v1"


__all__ = [
    "AuthenticatedTransferSink",
    "AuthenticatedTransferSource",
    "BoundedTransferController",
    "Dataset",
    "DistributedInspection",
    "DistributedRoute",
    "OPERATOR_PROJECTION_SCHEMA",
    "Partition",
    "Replica",
    "RetryableTopologyPhaseError",
    "ServiceDefinition",
    "ServiceEndpoint",
    "ServiceGroup",
    "ServiceInstance",
    "TopologyAdapter",
    "TopologyLease",
    "TopologyOperation",
    "TopologyPlan",
    "TopologyPlanStep",
    "TopologyStepContext",
    "TransferRecord",
    "UncertainTopologyPhaseError",
    "apply_plan",
    "define_dataset",
    "define_group",
    "define_service",
    "drain",
    "explain_route",
    "get_plan",
    "get_operation",
    "handoff_authority",
    "inspect",
    "invoke",
    "observe_replica",
    "plan_replica_change",
    "plan_rebalance",
    "put_partition",
    "register",
    "renew",
    "route",
    "save_plan",
]
