from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from adaos.domain.distributed_runtime import (
    Dataset,
    DistributedRoute,
    Partition,
    Replica,
    ServiceGroup,
    ServiceInstance,
    TopologyLease,
    TopologyOperation,
)


OPERATOR_PROJECTION_SCHEMA = "adaos.distributed.operator_projection.v2"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active(lease: TopologyLease, *, now: datetime) -> bool:
    expires = datetime.fromisoformat(lease.valid_until.replace("Z", "+00:00"))
    return lease.status == "active" and expires > now


def _operation_row(operation: TopologyOperation) -> dict:
    phases = tuple(operation.phases)
    current = next(
        (
            item
            for item in reversed(phases)
            if item.state in {"pending", "running", "failed", "uncertain"}
        ),
        phases[-1] if phases else None,
    )
    return {
        "operation_id": operation.operation_id,
        "kind": operation.kind,
        "target_ref": operation.target_ref,
        "state": operation.state,
        "expected_revision": operation.expected_revision,
        "authority_epoch": operation.authority_epoch,
        "phase_count": len(phases),
        "terminal_phase_count": sum(
            item.state in {"succeeded", "failed", "uncertain", "skipped"}
            for item in phases
        ),
        "current_phase": current.phase if current is not None else None,
        "current_phase_state": current.state if current is not None else None,
        "error_code": current.error_code if current is not None else None,
        "created_at": operation.created_at,
        "updated_at": operation.updated_at,
    }


def build_distributed_projection(
    *,
    groups: Iterable[ServiceGroup],
    instances: Iterable[ServiceInstance],
    leases: Iterable[TopologyLease],
    datasets: Iterable[Dataset],
    partitions: Iterable[Partition],
    replicas: Iterable[Replica],
    operations: Iterable[TopologyOperation],
    routes: Iterable[DistributedRoute] = (),
    item_limit: int = 20,
) -> dict:
    """Build a bounded operator projection; detailed inventories stay cursor-backed."""

    now = _now()
    group_values = tuple(groups)
    instance_values = tuple(instances)
    lease_values = tuple(leases)
    dataset_values = tuple(datasets)
    partition_values = tuple(partitions)
    replica_values = tuple(replicas)
    operation_values = tuple(operations)
    route_values = tuple(routes)
    active_lease_ids = {
        item.lease_id for item in lease_values if _active(item, now=now)
    }
    ready_instances = [
        item
        for item in instance_values
        if item.readiness
        and item.status == "ready"
        and item.lease_id in active_lease_ids
    ]
    freshness = [
        item.freshness_seconds
        for item in replica_values
        if item.freshness_seconds is not None
    ]
    pressures = [
        float(value)
        for item in instance_values
        for value in item.pressure.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    recent_operations = sorted(
        operation_values, key=lambda item: item.updated_at, reverse=True
    )[: max(1, min(item_limit, 50))]
    return {
        "schema": OPERATOR_PROJECTION_SCHEMA,
        "summary": {
            "groups": len(group_values),
            "ready_groups": sum(item.status == "ready" for item in group_values),
            "instances": len(instance_values),
            "ready_instances": len(ready_instances),
            "active_leases": len(active_lease_ids),
            "datasets": len(dataset_values),
            "partitions": len(partition_values),
            "replicas": len(replica_values),
            "partial_routes": sum(item.partial for item in route_values),
            "active_operations": sum(
                item.state in {"pending", "running"} for item in operation_values
            ),
        },
        "status": {
            "groups": dict(Counter(item.status for item in group_values)),
            "instances": dict(Counter(item.status for item in instance_values)),
            "datasets": dict(Counter(item.status for item in dataset_values)),
            "partitions": dict(Counter(item.status for item in partition_values)),
            "replicas": dict(Counter(item.lifecycle for item in replica_values)),
            "content": dict(Counter(item.content_state for item in replica_values)),
            "operations": dict(Counter(item.state for item in operation_values)),
        },
        "freshness": {
            "observed_replicas": len(freshness),
            "maximum_seconds": max(freshness, default=None),
        },
        "pressure": {
            "observed_values": len(pressures),
            "maximum": max(pressures, default=None),
        },
        "recent_operations": [_operation_row(item) for item in recent_operations],
        "inventory": {
            "bounded": True,
            "page_limit": 200,
            "detail_transport": "cursor_api",
        },
    }


__all__ = ["OPERATOR_PROJECTION_SCHEMA", "build_distributed_projection"]
