from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

from adaos.domain.distributed_runtime import (
    Dataset,
    DistributedRoute,
    Partition,
    Replica,
    ServiceDefinition,
    ServiceGroup,
    ServiceInstance,
    TopologyLease,
    TopologyOperation,
    TransferRecord,
    utc_now,
)
from adaos.domain.distributed_operations import TopologyPlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.runtime_paths import current_state_dir


class DistributedStoreError(RuntimeError):
    pass


class DistributedConflictError(DistributedStoreError):
    def __init__(self, *, resource: str, expected: int, observed: int) -> None:
        super().__init__(
            f"{resource} revision conflict: expected {expected}, observed {observed}"
        )
        self.resource = resource
        self.expected = expected
        self.observed = observed


_T = TypeVar("_T")
_OPERATION_TRANSITIONS = {
    "pending": {"running", "failed"},
    "running": {"running", "succeeded", "failed", "uncertain"},
    "succeeded": set(),
    "failed": set(),
    "uncertain": set(),
}
_TRANSFER_TRANSITIONS = {
    "preparing": {"transferring", "failed", "uncertain"},
    "transferring": {"transferring", "verifying", "failed", "uncertain"},
    "verifying": {"complete", "failed", "uncertain"},
    "complete": set(),
    "failed": set(),
    "uncertain": set(),
}


def _token(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DistributedStoreError(
            f"cannot read distributed record {path.name}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise DistributedStoreError(f"distributed record {path.name} is not an object")
    return dict(payload)


def _encode_cursor(after: str) -> str:
    raw = json.dumps({"after": after}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> str | None:
    token = str(cursor or "").strip()
    if not token:
        return None
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(token + padding).decode("utf-8"))
    except Exception as exc:
        raise DistributedStoreError("invalid distributed inventory cursor") from exc
    if not isinstance(payload, Mapping) or set(payload) != {"after"}:
        raise DistributedStoreError("invalid distributed inventory cursor")
    after = str(payload.get("after") or "").strip()
    if not after:
        raise DistributedStoreError("invalid distributed inventory cursor")
    return after


def _page(
    values: Sequence[_T],
    *,
    key: Callable[[_T], str],
    cursor: str | None,
    limit: int,
) -> tuple[tuple[_T, ...], str | None]:
    size = max(1, min(int(limit), 200))
    after = _decode_cursor(cursor)
    ordered = sorted(values, key=key)
    if after is not None:
        ordered = [item for item in ordered if key(item) > after]
    selected = ordered[:size]
    next_cursor = None
    if len(ordered) > len(selected) and selected:
        next_cursor = _encode_cursor(key(selected[-1]))
    return tuple(selected), next_cursor


class DistributedRuntimeStore:
    """Durable bounded state for distributed desired/observed topology."""

    def __init__(self, *, state_dir: Path | None = None) -> None:
        self.state_dir = Path(state_dir or current_state_dir()).expanduser().resolve()

    @property
    def root(self) -> Path:
        path = self.state_dir / "distributed_runtime"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def _path(self, kind: str, identity: str) -> Path:
        return self.root / kind / f"{_token(identity)}.json"

    def _definition_values(self) -> tuple[ServiceDefinition, ...]:
        values: dict[tuple[str, str], ServiceDefinition] = {}
        root = self.root / "definitions"
        if root.is_dir():
            for path in root.glob("*.json"):
                value = ServiceDefinition.from_mapping(_read_mapping(path))
                values[(value.definition_id, value.version)] = value
        return tuple(values.values())

    def _audit(self, event: str, **details: Any) -> None:
        payload = {
            "schema": "adaos.distributed.audit.v1",
            "event": event,
            "at": utc_now(),
            **details,
        }
        path = self.root / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())

    def append_audit(self, event: str, **details: Any) -> None:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            self._audit(event, **details)

    def _get(
        self, kind: str, identity: str, parser: Callable[[Mapping[str, Any]], _T]
    ) -> _T:
        path = self._path(kind, identity)
        if not path.is_file():
            raise FileNotFoundError(f"distributed {kind} record not found: {identity}")
        return parser(_read_mapping(path))

    def _list(
        self,
        kind: str,
        parser: Callable[[Mapping[str, Any]], _T],
        *,
        key: Callable[[_T], str],
        predicate: Callable[[_T], bool] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[_T, ...], str | None]:
        values: list[_T] = []
        root = self.root / kind
        if root.is_dir():
            for path in root.glob("*.json"):
                value = parser(_read_mapping(path))
                if predicate is None or predicate(value):
                    values.append(value)
        return _page(values, key=key, cursor=cursor, limit=limit)

    def put_definition(self, definition: ServiceDefinition) -> ServiceDefinition:
        path = self._path(
            "definitions",
            f"{definition.definition_id}@{definition.version}",
        )
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                previous = ServiceDefinition.from_mapping(_read_mapping(path))
                if previous != definition:
                    raise DistributedStoreError("service definition is immutable")
                return previous
            atomic_write_json(path, definition.to_dict())
            self._audit(
                "service.definition.saved",
                definition_id=definition.definition_id,
                version=definition.version,
                release_digest=definition.release_digest,
            )
        return definition

    def get_definition(
        self, definition_id: str, version: str | None = None
    ) -> ServiceDefinition:
        if version is not None:
            path = self._path("definitions", f"{definition_id}@{version}")
            if path.is_file():
                return ServiceDefinition.from_mapping(_read_mapping(path))
            legacy = self._path("definitions", definition_id)
            if legacy.is_file():
                value = ServiceDefinition.from_mapping(_read_mapping(legacy))
                if value.version == version:
                    return value
            raise FileNotFoundError(
                f"distributed definitions record not found: {definition_id}@{version}"
            )

        selected = [
            item
            for item in self._definition_values()
            if item.definition_id == definition_id
        ]
        if not selected:
            raise FileNotFoundError(
                f"distributed definitions record not found: {definition_id}"
            )
        if len(selected) != 1:
            raise DistributedStoreError("service definition version is required")
        return selected[0]

    def list_definitions(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> tuple[tuple[ServiceDefinition, ...], str | None]:
        return _page(
            self._definition_values(),
            key=lambda item: f"{item.definition_id}@{item.version}",
            cursor=cursor,
            limit=limit,
        )

    def save_group(
        self, group: ServiceGroup, *, expected_revision: int, actor_ref: str
    ) -> ServiceGroup:
        path = self._path("groups", group.group_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            previous = (
                ServiceGroup.from_mapping(_read_mapping(path))
                if path.is_file()
                else None
            )
            observed = previous.desired_revision if previous is not None else 0
            if expected_revision != observed:
                raise DistributedConflictError(
                    resource="service_group",
                    expected=expected_revision,
                    observed=observed,
                )
            if group.desired_revision != observed + 1:
                raise DistributedStoreError(
                    "group desired_revision must advance by one"
                )
            atomic_write_json(path, group.to_dict())
            revision_path = (
                self.root
                / "group_revisions"
                / _token(group.group_id)
                / f"{group.desired_revision:020d}.json"
            )
            if revision_path.exists():
                if _read_mapping(revision_path) != group.to_dict():
                    raise DistributedStoreError(
                        "immutable group revision already exists"
                    )
            else:
                atomic_write_json(revision_path, group.to_dict())
            self._audit(
                "service.group.desired.saved",
                group_id=group.group_id,
                revision=group.desired_revision,
                actor_ref=actor_ref,
            )
        return group

    def get_group(self, group_id: str) -> ServiceGroup:
        return self._get("groups", group_id, ServiceGroup.from_mapping)

    def list_groups(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> tuple[tuple[ServiceGroup, ...], str | None]:
        return self._list(
            "groups",
            ServiceGroup.from_mapping,
            key=lambda item: item.group_id,
            cursor=cursor,
            limit=limit,
        )

    def put_instance(
        self,
        instance: ServiceInstance,
        *,
        expected_revision: int,
        allow_generation_change: bool = False,
    ) -> ServiceInstance:
        path = self._path("instances", instance.instance_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            previous = (
                ServiceInstance.from_mapping(_read_mapping(path))
                if path.is_file()
                else None
            )
            observed = previous.revision if previous is not None else 0
            if expected_revision != observed:
                raise DistributedConflictError(
                    resource="service_instance",
                    expected=expected_revision,
                    observed=observed,
                )
            if instance.revision != observed + 1:
                raise DistributedStoreError("instance revision must advance by one")
            if previous is not None:
                stable_identity = (
                    "instance_id",
                    "group_id",
                    "node_id",
                    "component_ref",
                    "protocol_version",
                )
                if any(
                    getattr(previous, name) != getattr(instance, name)
                    for name in stable_identity
                ):
                    raise DistributedStoreError(
                        "service instance immutable identity changed"
                    )
                activation_changed = (
                    previous.activation_id != instance.activation_id
                    or previous.release_digest != instance.release_digest
                    or previous.runtime_generation != instance.runtime_generation
                )
                topology_changed = (
                    previous.topology_generation != instance.topology_generation
                )
                if activation_changed or topology_changed:
                    if not allow_generation_change:
                        raise DistributedStoreError(
                            "service instance generation identity changed"
                        )
                    if (
                        activation_changed
                        and instance.runtime_generation <= previous.runtime_generation
                    ):
                        raise DistributedStoreError(
                            "service instance runtime generation must advance"
                        )
                    if (
                        topology_changed
                        and instance.topology_generation <= previous.topology_generation
                    ):
                        raise DistributedStoreError(
                            "service instance topology generation must advance"
                        )
            atomic_write_json(path, instance.to_dict())
            self._audit(
                "service.instance.observed",
                instance_id=instance.instance_id,
                group_id=instance.group_id,
                revision=instance.revision,
                status=instance.status,
                readiness=instance.readiness,
            )
        return instance

    def get_instance(self, instance_id: str) -> ServiceInstance:
        return self._get("instances", instance_id, ServiceInstance.from_mapping)

    def list_instances(
        self,
        *,
        group_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ServiceInstance, ...], str | None]:
        return self._list(
            "instances",
            ServiceInstance.from_mapping,
            key=lambda item: item.instance_id,
            predicate=None
            if group_id is None
            else lambda item: item.group_id == group_id,
            cursor=cursor,
            limit=limit,
        )

    def put_lease(self, lease: TopologyLease) -> TopologyLease:
        path = self._path("leases", lease.lease_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                previous = TopologyLease.from_mapping(_read_mapping(path))
                immutable = (
                    "lease_id",
                    "scope_ref",
                    "owner_instance_id",
                    "kind",
                    "epoch",
                    "topology_generation",
                    "operation_ref",
                    "issued_at",
                    "previous_lease_id",
                )
                if any(
                    getattr(previous, name) != getattr(lease, name)
                    for name in immutable
                ):
                    raise DistributedStoreError("lease immutable identity changed")
                if lease.valid_until < previous.valid_until:
                    raise DistributedStoreError("lease renewal cannot shorten validity")
            atomic_write_json(path, lease.to_dict())
            self._audit(
                "topology.lease.saved",
                lease_id=lease.lease_id,
                scope_ref=lease.scope_ref,
                owner_instance_id=lease.owner_instance_id,
                kind=lease.kind,
                epoch=lease.epoch,
                status=lease.status,
            )
        return lease

    def get_lease(self, lease_id: str) -> TopologyLease:
        return self._get("leases", lease_id, TopologyLease.from_mapping)

    def list_leases(
        self,
        *,
        scope_ref: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[TopologyLease, ...], str | None]:
        return self._list(
            "leases",
            TopologyLease.from_mapping,
            key=lambda item: item.lease_id,
            predicate=None
            if scope_ref is None
            else lambda item: item.scope_ref == scope_ref,
            cursor=cursor,
            limit=limit,
        )

    def handoff_authority(
        self,
        lease: TopologyLease,
        *,
        expected_epoch: int,
        actor_ref: str,
    ) -> TopologyLease:
        if lease.kind != "authority" or lease.status != "active":
            raise DistributedStoreError("handoff requires a new active authority lease")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            authorities: list[TopologyLease] = []
            root = self.root / "leases"
            if root.is_dir():
                for path in root.glob("*.json"):
                    candidate = TopologyLease.from_mapping(_read_mapping(path))
                    if (
                        candidate.scope_ref == lease.scope_ref
                        and candidate.kind == "authority"
                    ):
                        authorities.append(candidate)
            latest = max(authorities, key=lambda item: item.epoch, default=None)
            observed_epoch = latest.epoch if latest is not None else 0
            if expected_epoch != observed_epoch:
                raise DistributedConflictError(
                    resource="authority_epoch",
                    expected=expected_epoch,
                    observed=observed_epoch,
                )
            if lease.epoch != observed_epoch + 1:
                raise DistributedStoreError(
                    "authority epoch must advance by exactly one"
                )
            for current in authorities:
                if current.status != "active":
                    continue
                released = replace(current, status="released")
                atomic_write_json(
                    self._path("leases", released.lease_id), released.to_dict()
                )
            path = self._path("leases", lease.lease_id)
            if path.exists():
                raise DistributedStoreError("new authority lease id already exists")
            atomic_write_json(path, lease.to_dict())
            self._audit(
                "topology.authority.handoff",
                scope_ref=lease.scope_ref,
                previous_lease_id=None if latest is None else latest.lease_id,
                lease_id=lease.lease_id,
                owner_instance_id=lease.owner_instance_id,
                epoch=lease.epoch,
                actor_ref=actor_ref,
            )
        return lease

    def save_dataset(
        self, dataset: Dataset, *, expected_revision: int, actor_ref: str
    ) -> Dataset:
        path = self._path("datasets", dataset.dataset_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            previous = (
                Dataset.from_mapping(_read_mapping(path)) if path.is_file() else None
            )
            observed = previous.desired_revision if previous is not None else 0
            if expected_revision != observed:
                raise DistributedConflictError(
                    resource="dataset", expected=expected_revision, observed=observed
                )
            if dataset.desired_revision != observed + 1:
                raise DistributedStoreError(
                    "dataset desired_revision must advance by one"
                )
            atomic_write_json(path, dataset.to_dict())
            revision_path = (
                self.root
                / "dataset_revisions"
                / _token(dataset.dataset_id)
                / f"{dataset.desired_revision:020d}.json"
            )
            if revision_path.exists():
                if _read_mapping(revision_path) != dataset.to_dict():
                    raise DistributedStoreError(
                        "immutable dataset revision already exists"
                    )
            else:
                atomic_write_json(revision_path, dataset.to_dict())
            self._audit(
                "dataset.desired.saved",
                dataset_id=dataset.dataset_id,
                revision=dataset.desired_revision,
                actor_ref=actor_ref,
            )
        return dataset

    def get_dataset(self, dataset_id: str) -> Dataset:
        return self._get("datasets", dataset_id, Dataset.from_mapping)

    def list_datasets(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> tuple[tuple[Dataset, ...], str | None]:
        return self._list(
            "datasets",
            Dataset.from_mapping,
            key=lambda item: item.dataset_id,
            cursor=cursor,
            limit=limit,
        )

    def put_partition(
        self, partition: Partition, *, expected_revision: int
    ) -> Partition:
        path = self._path("partitions", partition.partition_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            previous = (
                Partition.from_mapping(_read_mapping(path)) if path.is_file() else None
            )
            observed = previous.revision if previous is not None else 0
            if expected_revision != observed:
                raise DistributedConflictError(
                    resource="partition", expected=expected_revision, observed=observed
                )
            if partition.revision != observed + 1:
                raise DistributedStoreError("partition revision must advance by one")
            if previous is not None and (
                previous.partition_id != partition.partition_id
                or previous.dataset_id != partition.dataset_id
            ):
                raise DistributedStoreError("partition immutable identity changed")
            atomic_write_json(path, partition.to_dict())
            self._audit(
                "partition.observed",
                partition_id=partition.partition_id,
                dataset_id=partition.dataset_id,
                revision=partition.revision,
                status=partition.status,
            )
        return partition

    def get_partition(self, partition_id: str) -> Partition:
        return self._get("partitions", partition_id, Partition.from_mapping)

    def list_partitions(
        self,
        *,
        dataset_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[Partition, ...], str | None]:
        return self._list(
            "partitions",
            Partition.from_mapping,
            key=lambda item: item.partition_id,
            predicate=None
            if dataset_id is None
            else lambda item: item.dataset_id == dataset_id,
            cursor=cursor,
            limit=limit,
        )

    def put_replica(self, replica: Replica, *, expected_revision: int) -> Replica:
        path = self._path("replicas", replica.replica_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            previous = (
                Replica.from_mapping(_read_mapping(path)) if path.is_file() else None
            )
            observed = previous.revision if previous is not None else 0
            if expected_revision != observed:
                raise DistributedConflictError(
                    resource="replica", expected=expected_revision, observed=observed
                )
            if replica.revision != observed + 1:
                raise DistributedStoreError("replica revision must advance by one")
            if previous is not None:
                immutable = ("replica_id", "partition_id", "instance_id", "node_id")
                if any(
                    getattr(previous, name) != getattr(replica, name)
                    for name in immutable
                ):
                    raise DistributedStoreError("replica immutable identity changed")
            atomic_write_json(path, replica.to_dict())
            self._audit(
                "replica.observed",
                replica_id=replica.replica_id,
                partition_id=replica.partition_id,
                revision=replica.revision,
                lifecycle=replica.lifecycle,
                content_state=replica.content_state,
            )
        return replica

    def get_replica(self, replica_id: str) -> Replica:
        return self._get("replicas", replica_id, Replica.from_mapping)

    def list_replicas(
        self,
        *,
        partition_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[Replica, ...], str | None]:
        return self._list(
            "replicas",
            Replica.from_mapping,
            key=lambda item: item.replica_id,
            predicate=None
            if partition_id is None
            else lambda item: item.partition_id == partition_id,
            cursor=cursor,
            limit=limit,
        )

    def put_route(self, route: DistributedRoute) -> DistributedRoute:
        path = self._path("routes", route.route_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                previous = DistributedRoute.from_mapping(_read_mapping(path))
                if previous != route:
                    raise DistributedStoreError("resolved route is immutable")
                return previous
            atomic_write_json(path, route.to_dict())
            self._audit(
                "route.granted",
                route_id=route.route_id,
                dataset_id=route.dataset_id,
                partial=route.partial,
                expires_at=route.expires_at,
            )
        return route

    def get_route(self, route_id: str) -> DistributedRoute:
        return self._get("routes", route_id, DistributedRoute.from_mapping)

    def list_routes(
        self,
        *,
        dataset_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[DistributedRoute, ...], str | None]:
        return self._list(
            "routes",
            DistributedRoute.from_mapping,
            key=lambda item: item.route_id,
            predicate=None
            if dataset_id is None
            else lambda item: item.dataset_id == dataset_id,
            cursor=cursor,
            limit=limit,
        )

    def put_operation(self, operation: TopologyOperation) -> TopologyOperation:
        path = self._path("operations", operation.operation_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                previous = TopologyOperation.from_mapping(_read_mapping(path))
                immutable = (
                    "operation_id",
                    "kind",
                    "target_ref",
                    "expected_revision",
                    "idempotency_key",
                    "created_at",
                )
                if any(
                    getattr(previous, name) != getattr(operation, name)
                    for name in immutable
                ):
                    raise DistributedStoreError(
                        "topology operation immutable identity changed"
                    )
                if (
                    operation.state != previous.state
                    and operation.state not in _OPERATION_TRANSITIONS[previous.state]
                ):
                    raise DistributedStoreError("invalid topology operation transition")
            atomic_write_json(path, operation.to_dict())
            pointer = (
                self.root
                / "operation_idempotency"
                / f"{_token(operation.idempotency_key)}.json"
            )
            if pointer.is_file():
                previous_pointer = _read_mapping(pointer)
                if previous_pointer.get("operation_id") != operation.operation_id:
                    raise DistributedStoreError(
                        "topology idempotency key already belongs to another operation"
                    )
            else:
                atomic_write_json(pointer, {"operation_id": operation.operation_id})
            self._audit(
                "topology.operation.saved",
                operation_id=operation.operation_id,
                target_ref=operation.target_ref,
                state=operation.state,
            )
        return operation

    def put_plan(self, plan: TopologyPlan) -> TopologyPlan:
        path = self.root / "plans" / f"{str(plan.plan_digest).split(':', 1)[1]}.json"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                previous = TopologyPlan.from_mapping(_read_mapping(path))
                if previous != plan:
                    raise DistributedStoreError("topology plan is immutable")
                return previous
            atomic_write_json(path, plan.to_dict())
            self._audit(
                "topology.plan.saved",
                plan_id=plan.plan_id,
                plan_digest=plan.plan_digest,
                target_ref=plan.target_ref,
                status=plan.status,
            )
        return plan

    def get_plan(self, plan_digest: str) -> TopologyPlan:
        token = str(plan_digest or "")
        if not token.startswith("sha256:"):
            raise DistributedStoreError("topology plan digest is invalid")
        path = self.root / "plans" / f"{token.split(':', 1)[1]}.json"
        if not path.is_file():
            raise FileNotFoundError(f"topology plan not found: {plan_digest}")
        plan = TopologyPlan.from_mapping(_read_mapping(path))
        if plan.plan_digest != plan_digest:
            raise DistributedStoreError("topology plan path identity mismatch")
        return plan

    def get_operation(self, operation_id: str) -> TopologyOperation:
        return self._get("operations", operation_id, TopologyOperation.from_mapping)

    def get_operation_by_idempotency(
        self, idempotency_key: str
    ) -> TopologyOperation | None:
        pointer = (
            self.root / "operation_idempotency" / f"{_token(idempotency_key)}.json"
        )
        if not pointer.is_file():
            return None
        operation_id = str(_read_mapping(pointer).get("operation_id") or "")
        return self.get_operation(operation_id)

    def list_operations(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> tuple[tuple[TopologyOperation, ...], str | None]:
        return self._list(
            "operations",
            TopologyOperation.from_mapping,
            key=lambda item: item.operation_id,
            cursor=cursor,
            limit=limit,
        )

    def put_transfer(self, transfer: TransferRecord) -> TransferRecord:
        path = self._path("transfers", transfer.transfer_id)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if path.is_file():
                previous = TransferRecord.from_mapping(_read_mapping(path))
                immutable = (
                    "transfer_id",
                    "operation_id",
                    "partition_id",
                    "source_instance_id",
                    "target_instance_id",
                    "authority_epoch",
                    "manifest_digest",
                    "started_at",
                )
                if any(
                    getattr(previous, name) != getattr(transfer, name)
                    for name in immutable
                ):
                    raise DistributedStoreError("transfer immutable identity changed")
                if (
                    transfer.state != previous.state
                    and transfer.state not in _TRANSFER_TRANSITIONS[previous.state]
                ):
                    raise DistributedStoreError("invalid transfer transition")
                if (
                    transfer.byte_count < previous.byte_count
                    or transfer.item_count < previous.item_count
                ):
                    raise DistributedStoreError(
                        "transfer progress cannot move backwards"
                    )
            atomic_write_json(path, transfer.to_dict())
            self._audit(
                "topology.transfer.saved",
                transfer_id=transfer.transfer_id,
                operation_id=transfer.operation_id,
                state=transfer.state,
                byte_count=transfer.byte_count,
                item_count=transfer.item_count,
            )
        return transfer

    def get_transfer(self, transfer_id: str) -> TransferRecord:
        return self._get("transfers", transfer_id, TransferRecord.from_mapping)

    def list_transfers(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> tuple[tuple[TransferRecord, ...], str | None]:
        return self._list(
            "transfers",
            TransferRecord.from_mapping,
            key=lambda item: item.transfer_id,
            cursor=cursor,
            limit=limit,
        )


__all__ = [
    "DistributedConflictError",
    "DistributedRuntimeStore",
    "DistributedStoreError",
]
