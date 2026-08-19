from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from adaos.domain.artifact_release import canonical_payload_digest


SERVICE_DEFINITION_SCHEMA = "adaos.distributed.service_definition.v1"
SERVICE_GROUP_SCHEMA = "adaos.distributed.service_group.v1"
SERVICE_INSTANCE_SCHEMA = "adaos.distributed.service_instance.v1"
TOPOLOGY_LEASE_SCHEMA = "adaos.distributed.topology_lease.v1"
DATASET_SCHEMA = "adaos.distributed.dataset.v1"
PARTITION_SCHEMA = "adaos.distributed.partition.v1"
REPLICA_SCHEMA = "adaos.distributed.replica.v1"
ROUTE_SCHEMA = "adaos.distributed.route.v1"
TOPOLOGY_OPERATION_SCHEMA = "adaos.distributed.topology_operation.v1"
TRANSFER_SCHEMA = "adaos.distributed.transfer.v1"

CONSISTENCY_PROFILES = {
    "single_authority",
    "multi_writer_crdt",
    "derived_projection",
    "read_through_cache",
    "external_authority",
    "domain_managed",
}
INSTANCE_STATES = {
    "registering",
    "ready",
    "draining",
    "unavailable",
    "failed",
    "expired",
}
REPLICA_LIFECYCLES = {
    "preparing",
    "catching_up",
    "ready",
    "draining",
    "stale",
    "unavailable",
    "failed",
    "removed",
}
REPLICA_ROLES = {"authority", "follower", "derived", "cache"}
CONTENT_STATES = {"unknown", "empty", "non_empty", "unavailable"}


class DistributedContractError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DistributedContractError(f"{field_name} must be an object")
    return {str(key): item for key, item in value.items()}


def _strict(
    value: Mapping[str, Any],
    *,
    schema: str,
    fields: set[str],
    field_name: str,
) -> dict[str, Any]:
    payload = _mapping(value, field_name)
    if payload.get("schema") != schema:
        raise DistributedContractError(f"{field_name} must use {schema}")
    unknown = set(payload).difference(fields)
    missing = fields.difference(payload)
    if unknown or missing:
        problem = "unsupported" if unknown else "missing"
        details = sorted(unknown or missing)
        raise DistributedContractError(
            f"{field_name} contains {problem} fields: {', '.join(details)}"
        )
    return payload


def _token(value: Any, field_name: str, *, max_length: int = 500) -> str:
    token = str(value or "").strip()
    if not token:
        raise DistributedContractError(f"{field_name} is required")
    if len(token) > max_length:
        raise DistributedContractError(f"{field_name} exceeds {max_length} characters")
    return token


def _optional_token(
    value: Any, field_name: str, *, max_length: int = 500
) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    if len(token) > max_length:
        raise DistributedContractError(f"{field_name} exceeds {max_length} characters")
    return token


def _integer(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise DistributedContractError(f"{field_name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise DistributedContractError(f"{field_name} must be an integer") from exc
    if result < minimum:
        raise DistributedContractError(f"{field_name} must be >= {minimum}")
    return result


def _number(value: Any, field_name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool):
        raise DistributedContractError(f"{field_name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DistributedContractError(f"{field_name} must be numeric") from exc
    if result < minimum:
        raise DistributedContractError(f"{field_name} must be >= {minimum}")
    return result


def _timestamp(value: Any, field_name: str) -> str:
    token = _token(value, field_name, max_length=80)
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DistributedContractError(
            f"{field_name} must be an ISO date-time"
        ) from exc
    if parsed.tzinfo is None:
        raise DistributedContractError(f"{field_name} must include a timezone")
    return token


def _optional_timestamp(value: Any, field_name: str) -> str | None:
    return None if value is None else _timestamp(value, field_name)


def _digest(value: Any, field_name: str) -> str:
    token = _token(value, field_name, max_length=71).lower()
    if len(token) != 71 or not token.startswith("sha256:"):
        raise DistributedContractError(f"{field_name} must be a sha256 digest")
    try:
        int(token[7:], 16)
    except ValueError as exc:
        raise DistributedContractError(f"{field_name} must be a sha256 digest") from exc
    return token


def _optional_digest(value: Any, field_name: str) -> str | None:
    return None if value is None else _digest(value, field_name)


def _texts(value: Any, field_name: str, *, max_items: int = 200) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise DistributedContractError(f"{field_name} must be a list")
    if len(value) > max_items:
        raise DistributedContractError(f"{field_name} exceeds {max_items} items")
    return tuple(sorted({_token(item, field_name, max_length=300) for item in value}))


def _ordered_texts(
    value: Any, field_name: str, *, max_items: int = 200
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise DistributedContractError(f"{field_name} must be a list")
    if len(value) > max_items:
        raise DistributedContractError(f"{field_name} exceeds {max_items} items")
    return tuple(
        dict.fromkeys(_token(item, field_name, max_length=300) for item in value)
    )


def _component_ref(value: Any) -> str:
    token = _token(value, "component_ref", max_length=160)
    kind, separator, artifact_id = token.partition(":")
    if separator != ":" or kind not in {"skill", "scenario"} or not artifact_id:
        raise DistributedContractError(
            "component_ref must be skill:<id> or scenario:<id>"
        )
    return token


@dataclass(frozen=True, slots=True)
class ServiceEndpoint:
    endpoint_id: str
    protocol: str
    address_ref: str
    scopes: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "endpoint_id", _token(self.endpoint_id, "endpoint_id"))
        object.__setattr__(
            self, "protocol", _token(self.protocol, "protocol", max_length=80)
        )
        object.__setattr__(self, "address_ref", _token(self.address_ref, "address_ref"))
        object.__setattr__(self, "scopes", _texts(list(self.scopes), "scopes"))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "protocol": self.protocol,
            "address_ref": self.address_ref,
            "scopes": list(self.scopes),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ServiceEndpoint":
        payload = _mapping(value, "ServiceEndpoint")
        fields = {"endpoint_id", "protocol", "address_ref", "scopes", "metadata"}
        if set(payload) != fields:
            raise DistributedContractError("ServiceEndpoint fields are invalid")
        return cls(
            endpoint_id=payload["endpoint_id"],
            protocol=payload["protocol"],
            address_ref=payload["address_ref"],
            scopes=_texts(payload["scopes"], "scopes"),
            metadata=_mapping(payload["metadata"], "metadata"),
        )


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    definition_id: str
    version: str
    release_digest: str
    compatible_components: tuple[str, ...]
    provided_contracts: tuple[str, ...]
    topology_mode: str
    protocol_version: str
    required_capabilities: tuple[str, ...] = ()
    trust_class: str = "trusted"
    adapter_contracts: tuple[str, ...] = ()
    health_protocol: str = "adaos.health.v1"
    drain_protocol: str = "adaos.drain.v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "definition_id", _token(self.definition_id, "definition_id")
        )
        object.__setattr__(
            self, "version", _token(self.version, "version", max_length=80)
        )
        object.__setattr__(
            self, "release_digest", _digest(self.release_digest, "release_digest")
        )
        components = tuple(
            sorted({_component_ref(item) for item in self.compatible_components})
        )
        if not components:
            raise DistributedContractError("compatible_components must not be empty")
        object.__setattr__(self, "compatible_components", components)
        contracts = _texts(list(self.provided_contracts), "provided_contracts")
        if not contracts:
            raise DistributedContractError("provided_contracts must not be empty")
        object.__setattr__(self, "provided_contracts", contracts)
        mode = _token(self.topology_mode, "topology_mode", max_length=30).lower()
        if mode not in {"singleton", "multi_instance"}:
            raise DistributedContractError("topology_mode is invalid")
        object.__setattr__(self, "topology_mode", mode)
        object.__setattr__(
            self, "protocol_version", _token(self.protocol_version, "protocol_version")
        )
        object.__setattr__(
            self,
            "required_capabilities",
            _texts(list(self.required_capabilities), "required_capabilities"),
        )
        trust_class = _token(self.trust_class, "trust_class", max_length=30).lower()
        if trust_class != "trusted":
            raise DistributedContractError("v1 services require trusted nodes")
        object.__setattr__(self, "trust_class", trust_class)
        object.__setattr__(
            self,
            "adapter_contracts",
            _texts(list(self.adapter_contracts), "adapter_contracts"),
        )
        object.__setattr__(
            self, "health_protocol", _token(self.health_protocol, "health_protocol")
        )
        object.__setattr__(
            self, "drain_protocol", _token(self.drain_protocol, "drain_protocol")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SERVICE_DEFINITION_SCHEMA,
            "definition_id": self.definition_id,
            "version": self.version,
            "release_digest": self.release_digest,
            "compatible_components": list(self.compatible_components),
            "provided_contracts": list(self.provided_contracts),
            "topology_mode": self.topology_mode,
            "protocol_version": self.protocol_version,
            "required_capabilities": list(self.required_capabilities),
            "trust_class": self.trust_class,
            "adapter_contracts": list(self.adapter_contracts),
            "health_protocol": self.health_protocol,
            "drain_protocol": self.drain_protocol,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ServiceDefinition":
        fields = {
            "schema",
            "definition_id",
            "version",
            "release_digest",
            "compatible_components",
            "provided_contracts",
            "topology_mode",
            "protocol_version",
            "required_capabilities",
            "trust_class",
            "adapter_contracts",
            "health_protocol",
            "drain_protocol",
        }
        payload = _strict(
            value,
            schema=SERVICE_DEFINITION_SCHEMA,
            fields=fields,
            field_name="ServiceDefinition",
        )
        return cls(
            definition_id=payload["definition_id"],
            version=payload["version"],
            release_digest=payload["release_digest"],
            compatible_components=_texts(
                payload["compatible_components"], "compatible_components"
            ),
            provided_contracts=_texts(
                payload["provided_contracts"], "provided_contracts"
            ),
            topology_mode=payload["topology_mode"],
            protocol_version=payload["protocol_version"],
            required_capabilities=_texts(
                payload["required_capabilities"], "required_capabilities"
            ),
            trust_class=payload["trust_class"],
            adapter_contracts=_texts(payload["adapter_contracts"], "adapter_contracts"),
            health_protocol=payload["health_protocol"],
            drain_protocol=payload["drain_protocol"],
        )


@dataclass(frozen=True, slots=True)
class ServiceGroup:
    group_id: str
    definition_id: str
    definition_version: str
    desired_generation: int
    desired_instances: int
    authority_policy: str
    placement: Mapping[str, Any]
    linked_datasets: tuple[str, ...]
    route_policy: Mapping[str, Any]
    desired_revision: int
    observed_revision: int = 0
    status: str = "pending"

    def __post_init__(self) -> None:
        object.__setattr__(self, "group_id", _token(self.group_id, "group_id"))
        object.__setattr__(
            self, "definition_id", _token(self.definition_id, "definition_id")
        )
        object.__setattr__(
            self,
            "definition_version",
            _token(self.definition_version, "definition_version"),
        )
        object.__setattr__(
            self,
            "desired_generation",
            _integer(self.desired_generation, "desired_generation", minimum=1),
        )
        object.__setattr__(
            self,
            "desired_instances",
            _integer(self.desired_instances, "desired_instances", minimum=1),
        )
        authority = _token(
            self.authority_policy, "authority_policy", max_length=30
        ).lower()
        if authority not in {"none", "singleton_fenced"}:
            raise DistributedContractError("authority_policy is invalid")
        object.__setattr__(self, "authority_policy", authority)
        object.__setattr__(self, "placement", dict(self.placement))
        object.__setattr__(
            self,
            "linked_datasets",
            _texts(list(self.linked_datasets), "linked_datasets"),
        )
        object.__setattr__(self, "route_policy", dict(self.route_policy))
        object.__setattr__(
            self,
            "desired_revision",
            _integer(self.desired_revision, "desired_revision", minimum=1),
        )
        object.__setattr__(
            self,
            "observed_revision",
            _integer(self.observed_revision, "observed_revision", minimum=0),
        )
        status = _token(self.status, "status", max_length=30).lower()
        if status not in {
            "pending",
            "ready",
            "degraded",
            "unavailable",
            "reconciling",
            "removed",
        }:
            raise DistributedContractError("service group status is invalid")
        object.__setattr__(self, "status", status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SERVICE_GROUP_SCHEMA,
            "group_id": self.group_id,
            "definition_id": self.definition_id,
            "definition_version": self.definition_version,
            "desired_generation": self.desired_generation,
            "desired_instances": self.desired_instances,
            "authority_policy": self.authority_policy,
            "placement": dict(self.placement),
            "linked_datasets": list(self.linked_datasets),
            "route_policy": dict(self.route_policy),
            "desired_revision": self.desired_revision,
            "observed_revision": self.observed_revision,
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ServiceGroup":
        fields = {
            "schema",
            "group_id",
            "definition_id",
            "definition_version",
            "desired_generation",
            "desired_instances",
            "authority_policy",
            "placement",
            "linked_datasets",
            "route_policy",
            "desired_revision",
            "observed_revision",
            "status",
        }
        payload = _strict(
            value, schema=SERVICE_GROUP_SCHEMA, fields=fields, field_name="ServiceGroup"
        )
        return cls(
            group_id=payload["group_id"],
            definition_id=payload["definition_id"],
            definition_version=payload["definition_version"],
            desired_generation=payload["desired_generation"],
            desired_instances=payload["desired_instances"],
            authority_policy=payload["authority_policy"],
            placement=_mapping(payload["placement"], "placement"),
            linked_datasets=_texts(payload["linked_datasets"], "linked_datasets"),
            route_policy=_mapping(payload["route_policy"], "route_policy"),
            desired_revision=payload["desired_revision"],
            observed_revision=payload["observed_revision"],
            status=payload["status"],
        )


@dataclass(frozen=True, slots=True)
class ServiceInstance:
    instance_id: str
    group_id: str
    node_id: str
    activation_id: str
    release_digest: str
    component_ref: str
    runtime_generation: int
    protocol_version: str
    topology_generation: int
    lease_id: str
    status: str
    readiness: bool
    health: Mapping[str, Any]
    pressure: Mapping[str, Any]
    capabilities: tuple[str, ...]
    endpoints: tuple[ServiceEndpoint, ...]
    observed_at: str
    revision: int = 1

    def __post_init__(self) -> None:
        for field_name in (
            "instance_id",
            "group_id",
            "node_id",
            "activation_id",
            "lease_id",
        ):
            object.__setattr__(
                self, field_name, _token(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self, "release_digest", _digest(self.release_digest, "release_digest")
        )
        object.__setattr__(self, "component_ref", _component_ref(self.component_ref))
        object.__setattr__(
            self,
            "runtime_generation",
            _integer(self.runtime_generation, "runtime_generation", minimum=1),
        )
        object.__setattr__(
            self, "protocol_version", _token(self.protocol_version, "protocol_version")
        )
        object.__setattr__(
            self,
            "topology_generation",
            _integer(self.topology_generation, "topology_generation", minimum=1),
        )
        status = _token(self.status, "status", max_length=30).lower()
        if status not in INSTANCE_STATES:
            raise DistributedContractError("service instance status is invalid")
        if not isinstance(self.readiness, bool):
            raise DistributedContractError("readiness must be a boolean")
        if self.readiness and status != "ready":
            raise DistributedContractError(
                "only a ready instance can publish readiness"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "health", dict(self.health))
        object.__setattr__(self, "pressure", dict(self.pressure))
        object.__setattr__(
            self, "capabilities", _texts(list(self.capabilities), "capabilities")
        )
        if any(not isinstance(item, ServiceEndpoint) for item in self.endpoints):
            raise DistributedContractError(
                "endpoints must contain ServiceEndpoint values"
            )
        endpoint_ids = [item.endpoint_id for item in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise DistributedContractError("service endpoint ids must be unique")
        object.__setattr__(
            self,
            "endpoints",
            tuple(sorted(self.endpoints, key=lambda item: item.endpoint_id)),
        )
        object.__setattr__(
            self, "observed_at", _timestamp(self.observed_at, "observed_at")
        )
        object.__setattr__(
            self, "revision", _integer(self.revision, "revision", minimum=1)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SERVICE_INSTANCE_SCHEMA,
            "instance_id": self.instance_id,
            "group_id": self.group_id,
            "node_id": self.node_id,
            "activation_id": self.activation_id,
            "release_digest": self.release_digest,
            "component_ref": self.component_ref,
            "runtime_generation": self.runtime_generation,
            "protocol_version": self.protocol_version,
            "topology_generation": self.topology_generation,
            "lease_id": self.lease_id,
            "status": self.status,
            "readiness": self.readiness,
            "health": dict(self.health),
            "pressure": dict(self.pressure),
            "capabilities": list(self.capabilities),
            "endpoints": [item.to_dict() for item in self.endpoints],
            "observed_at": self.observed_at,
            "revision": self.revision,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ServiceInstance":
        fields = {
            "schema",
            "instance_id",
            "group_id",
            "node_id",
            "activation_id",
            "release_digest",
            "component_ref",
            "runtime_generation",
            "protocol_version",
            "topology_generation",
            "lease_id",
            "status",
            "readiness",
            "health",
            "pressure",
            "capabilities",
            "endpoints",
            "observed_at",
            "revision",
        }
        payload = _strict(
            value,
            schema=SERVICE_INSTANCE_SCHEMA,
            fields=fields,
            field_name="ServiceInstance",
        )
        if not isinstance(payload["readiness"], bool):
            raise DistributedContractError("readiness must be a boolean")
        raw_endpoints = payload["endpoints"]
        if not isinstance(raw_endpoints, list):
            raise DistributedContractError("endpoints must be a list")
        return cls(
            instance_id=payload["instance_id"],
            group_id=payload["group_id"],
            node_id=payload["node_id"],
            activation_id=payload["activation_id"],
            release_digest=payload["release_digest"],
            component_ref=payload["component_ref"],
            runtime_generation=payload["runtime_generation"],
            protocol_version=payload["protocol_version"],
            topology_generation=payload["topology_generation"],
            lease_id=payload["lease_id"],
            status=payload["status"],
            readiness=payload["readiness"],
            health=_mapping(payload["health"], "health"),
            pressure=_mapping(payload["pressure"], "pressure"),
            capabilities=_texts(payload["capabilities"], "capabilities"),
            endpoints=tuple(
                ServiceEndpoint.from_mapping(item) for item in raw_endpoints
            ),
            observed_at=payload["observed_at"],
            revision=payload["revision"],
        )


@dataclass(frozen=True, slots=True)
class TopologyLease:
    lease_id: str
    scope_ref: str
    owner_instance_id: str
    kind: str
    epoch: int
    topology_generation: int
    operation_ref: str | None
    issued_at: str
    renew_by: str
    valid_until: str
    status: str = "active"
    previous_lease_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lease_id", _token(self.lease_id, "lease_id"))
        scope = _token(self.scope_ref, "scope_ref")
        if not scope.startswith(("service_group:", "partition:")):
            raise DistributedContractError("lease scope_ref is invalid")
        object.__setattr__(self, "scope_ref", scope)
        object.__setattr__(
            self,
            "owner_instance_id",
            _token(self.owner_instance_id, "owner_instance_id"),
        )
        kind = _token(self.kind, "lease kind", max_length=30).lower()
        if kind not in {"membership", "authority"}:
            raise DistributedContractError("lease kind is invalid")
        object.__setattr__(self, "kind", kind)
        epoch = _integer(self.epoch, "epoch", minimum=0)
        if kind == "authority" and epoch < 1:
            raise DistributedContractError("authority leases require epoch >= 1")
        if kind == "membership" and epoch != 0:
            raise DistributedContractError(
                "membership leases do not carry authority epochs"
            )
        object.__setattr__(self, "epoch", epoch)
        object.__setattr__(
            self,
            "topology_generation",
            _integer(self.topology_generation, "topology_generation", minimum=1),
        )
        object.__setattr__(
            self, "operation_ref", _optional_token(self.operation_ref, "operation_ref")
        )
        for field_name in ("issued_at", "renew_by", "valid_until"):
            object.__setattr__(
                self, field_name, _timestamp(getattr(self, field_name), field_name)
            )
        if not (self.issued_at <= self.renew_by <= self.valid_until):
            raise DistributedContractError("lease timestamps are out of order")
        status = _token(self.status, "lease status", max_length=20).lower()
        if status not in {"active", "expired", "revoked", "released"}:
            raise DistributedContractError("lease status is invalid")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "previous_lease_id",
            _optional_token(self.previous_lease_id, "previous_lease_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": TOPOLOGY_LEASE_SCHEMA,
            "lease_id": self.lease_id,
            "scope_ref": self.scope_ref,
            "owner_instance_id": self.owner_instance_id,
            "kind": self.kind,
            "epoch": self.epoch,
            "topology_generation": self.topology_generation,
            "operation_ref": self.operation_ref,
            "issued_at": self.issued_at,
            "renew_by": self.renew_by,
            "valid_until": self.valid_until,
            "status": self.status,
            "previous_lease_id": self.previous_lease_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TopologyLease":
        fields = {
            "schema",
            "lease_id",
            "scope_ref",
            "owner_instance_id",
            "kind",
            "epoch",
            "topology_generation",
            "operation_ref",
            "issued_at",
            "renew_by",
            "valid_until",
            "status",
            "previous_lease_id",
        }
        payload = _strict(
            value,
            schema=TOPOLOGY_LEASE_SCHEMA,
            fields=fields,
            field_name="TopologyLease",
        )
        return cls(**{key: payload[key] for key in fields if key != "schema"})


@dataclass(frozen=True, slots=True)
class Dataset:
    dataset_id: str
    owner_ref: str
    contract: str
    consistency_profile: str
    partition_scheme: Mapping[str, Any]
    retention: Mapping[str, Any]
    data_class: str
    desired_revision: int
    observed_revision: int = 0
    status: str = "pending"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_id", _token(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "owner_ref", _token(self.owner_ref, "owner_ref"))
        object.__setattr__(self, "contract", _token(self.contract, "contract"))
        profile = _token(
            self.consistency_profile, "consistency_profile", max_length=40
        ).lower()
        if profile not in CONSISTENCY_PROFILES:
            raise DistributedContractError("consistency_profile is invalid")
        object.__setattr__(self, "consistency_profile", profile)
        partition_scheme = _mapping(self.partition_scheme, "partition_scheme")
        if "kind" not in partition_scheme:
            raise DistributedContractError("partition_scheme.kind is required")
        object.__setattr__(self, "partition_scheme", partition_scheme)
        object.__setattr__(self, "retention", _mapping(self.retention, "retention"))
        data_class = _token(self.data_class, "data_class", max_length=30).lower()
        if data_class not in {"external", "derived", "authoritative", "cache"}:
            raise DistributedContractError("data_class is invalid")
        if profile == "external_authority" and data_class != "external":
            raise DistributedContractError(
                "external_authority datasets must use external data"
            )
        if profile == "derived_projection" and data_class != "derived":
            raise DistributedContractError(
                "derived_projection datasets must use derived data"
            )
        object.__setattr__(self, "data_class", data_class)
        object.__setattr__(
            self,
            "desired_revision",
            _integer(self.desired_revision, "desired_revision", minimum=1),
        )
        object.__setattr__(
            self,
            "observed_revision",
            _integer(self.observed_revision, "observed_revision", minimum=0),
        )
        status = _token(self.status, "status", max_length=30).lower()
        if status not in {
            "pending",
            "ready",
            "degraded",
            "unavailable",
            "reconciling",
            "removed",
        }:
            raise DistributedContractError("dataset status is invalid")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DATASET_SCHEMA,
            "dataset_id": self.dataset_id,
            "owner_ref": self.owner_ref,
            "contract": self.contract,
            "consistency_profile": self.consistency_profile,
            "partition_scheme": dict(self.partition_scheme),
            "retention": dict(self.retention),
            "data_class": self.data_class,
            "desired_revision": self.desired_revision,
            "observed_revision": self.observed_revision,
            "status": self.status,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Dataset":
        fields = {
            "schema",
            "dataset_id",
            "owner_ref",
            "contract",
            "consistency_profile",
            "partition_scheme",
            "retention",
            "data_class",
            "desired_revision",
            "observed_revision",
            "status",
            "metadata",
        }
        payload = _strict(
            value, schema=DATASET_SCHEMA, fields=fields, field_name="Dataset"
        )
        return cls(
            dataset_id=payload["dataset_id"],
            owner_ref=payload["owner_ref"],
            contract=payload["contract"],
            consistency_profile=payload["consistency_profile"],
            partition_scheme=_mapping(payload["partition_scheme"], "partition_scheme"),
            retention=_mapping(payload["retention"], "retention"),
            data_class=payload["data_class"],
            desired_revision=payload["desired_revision"],
            observed_revision=payload["observed_revision"],
            status=payload["status"],
            metadata=_mapping(payload["metadata"], "metadata"),
        )


@dataclass(frozen=True, slots=True)
class Partition:
    partition_id: str
    dataset_id: str
    selector: Mapping[str, Any]
    desired_replicas: int
    topology_generation: int
    authority_lease_id: str | None
    authority_epoch: int
    checkpoint: str | None
    status: str
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "partition_id", _token(self.partition_id, "partition_id")
        )
        object.__setattr__(self, "dataset_id", _token(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "selector", _mapping(self.selector, "selector"))
        object.__setattr__(
            self,
            "desired_replicas",
            _integer(self.desired_replicas, "desired_replicas", minimum=1),
        )
        object.__setattr__(
            self,
            "topology_generation",
            _integer(self.topology_generation, "topology_generation", minimum=1),
        )
        lease_id = _optional_token(self.authority_lease_id, "authority_lease_id")
        epoch = _integer(self.authority_epoch, "authority_epoch", minimum=0)
        if (lease_id is None) != (epoch == 0):
            raise DistributedContractError(
                "authority lease and positive authority epoch must be published together"
            )
        object.__setattr__(self, "authority_lease_id", lease_id)
        object.__setattr__(self, "authority_epoch", epoch)
        object.__setattr__(
            self, "checkpoint", _optional_token(self.checkpoint, "checkpoint")
        )
        status = _token(self.status, "status", max_length=30).lower()
        if status not in {
            "pending",
            "ready",
            "degraded",
            "unavailable",
            "moving",
            "removed",
        }:
            raise DistributedContractError("partition status is invalid")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self, "revision", _integer(self.revision, "revision", minimum=1)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PARTITION_SCHEMA,
            "partition_id": self.partition_id,
            "dataset_id": self.dataset_id,
            "selector": dict(self.selector),
            "desired_replicas": self.desired_replicas,
            "topology_generation": self.topology_generation,
            "authority_lease_id": self.authority_lease_id,
            "authority_epoch": self.authority_epoch,
            "checkpoint": self.checkpoint,
            "status": self.status,
            "revision": self.revision,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Partition":
        fields = {
            "schema",
            "partition_id",
            "dataset_id",
            "selector",
            "desired_replicas",
            "topology_generation",
            "authority_lease_id",
            "authority_epoch",
            "checkpoint",
            "status",
            "revision",
        }
        payload = _strict(
            value, schema=PARTITION_SCHEMA, fields=fields, field_name="Partition"
        )
        return cls(
            partition_id=payload["partition_id"],
            dataset_id=payload["dataset_id"],
            selector=_mapping(payload["selector"], "selector"),
            desired_replicas=payload["desired_replicas"],
            topology_generation=payload["topology_generation"],
            authority_lease_id=payload["authority_lease_id"],
            authority_epoch=payload["authority_epoch"],
            checkpoint=payload["checkpoint"],
            status=payload["status"],
            revision=payload["revision"],
        )


@dataclass(frozen=True, slots=True)
class Replica:
    replica_id: str
    partition_id: str
    instance_id: str
    node_id: str
    role: str
    lifecycle: str
    content_state: str
    authority_epoch: int
    checkpoint: str | None
    source_ref: str | None
    freshness_seconds: float | None
    item_count: int | None
    byte_count: int | None
    observed_at: str
    revision: int = 1

    def __post_init__(self) -> None:
        for field_name in ("replica_id", "partition_id", "instance_id", "node_id"):
            object.__setattr__(
                self, field_name, _token(getattr(self, field_name), field_name)
            )
        role = _token(self.role, "role", max_length=30).lower()
        if role not in REPLICA_ROLES:
            raise DistributedContractError("replica role is invalid")
        object.__setattr__(self, "role", role)
        lifecycle = _token(self.lifecycle, "lifecycle", max_length=30).lower()
        if lifecycle not in REPLICA_LIFECYCLES:
            raise DistributedContractError("replica lifecycle is invalid")
        object.__setattr__(self, "lifecycle", lifecycle)
        content_state = _token(
            self.content_state, "content_state", max_length=30
        ).lower()
        if content_state not in CONTENT_STATES:
            raise DistributedContractError("replica content_state is invalid")
        if lifecycle == "unavailable" and content_state != "unavailable":
            raise DistributedContractError(
                "unavailable replicas must publish unavailable content_state"
            )
        object.__setattr__(self, "content_state", content_state)
        epoch = _integer(self.authority_epoch, "authority_epoch", minimum=0)
        if role == "authority" and epoch < 1:
            raise DistributedContractError(
                "authority replicas require authority_epoch >= 1"
            )
        object.__setattr__(self, "authority_epoch", epoch)
        object.__setattr__(
            self, "checkpoint", _optional_token(self.checkpoint, "checkpoint")
        )
        object.__setattr__(
            self, "source_ref", _optional_token(self.source_ref, "source_ref")
        )
        freshness = self.freshness_seconds
        if freshness is not None:
            freshness = _number(freshness, "freshness_seconds")
        object.__setattr__(self, "freshness_seconds", freshness)
        for field_name in ("item_count", "byte_count"):
            raw_value = getattr(self, field_name)
            object.__setattr__(
                self,
                field_name,
                None
                if raw_value is None
                else _integer(raw_value, field_name, minimum=0),
            )
        object.__setattr__(
            self, "observed_at", _timestamp(self.observed_at, "observed_at")
        )
        object.__setattr__(
            self, "revision", _integer(self.revision, "revision", minimum=1)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": REPLICA_SCHEMA,
            "replica_id": self.replica_id,
            "partition_id": self.partition_id,
            "instance_id": self.instance_id,
            "node_id": self.node_id,
            "role": self.role,
            "lifecycle": self.lifecycle,
            "content_state": self.content_state,
            "authority_epoch": self.authority_epoch,
            "checkpoint": self.checkpoint,
            "source_ref": self.source_ref,
            "freshness_seconds": self.freshness_seconds,
            "item_count": self.item_count,
            "byte_count": self.byte_count,
            "observed_at": self.observed_at,
            "revision": self.revision,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Replica":
        fields = {
            "schema",
            "replica_id",
            "partition_id",
            "instance_id",
            "node_id",
            "role",
            "lifecycle",
            "content_state",
            "authority_epoch",
            "checkpoint",
            "source_ref",
            "freshness_seconds",
            "item_count",
            "byte_count",
            "observed_at",
            "revision",
        }
        payload = _strict(
            value, schema=REPLICA_SCHEMA, fields=fields, field_name="Replica"
        )
        return cls(**{key: payload[key] for key in fields if key != "schema"})


@dataclass(frozen=True, slots=True)
class RouteEndpoint:
    endpoint_ref: str
    replica_id: str
    partition_id: str
    role: str
    priority: int
    authority_epoch: int
    checkpoint: str | None
    freshness_seconds: float | None
    observed_at: str

    def __post_init__(self) -> None:
        for field_name in ("endpoint_ref", "replica_id", "partition_id"):
            object.__setattr__(
                self, field_name, _token(getattr(self, field_name), field_name)
            )
        role = _token(self.role, "role", max_length=30).lower()
        if role not in REPLICA_ROLES:
            raise DistributedContractError("route endpoint role is invalid")
        object.__setattr__(self, "role", role)
        object.__setattr__(
            self, "priority", _integer(self.priority, "priority", minimum=0)
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _integer(self.authority_epoch, "authority_epoch", minimum=0),
        )
        object.__setattr__(
            self, "checkpoint", _optional_token(self.checkpoint, "checkpoint")
        )
        freshness = self.freshness_seconds
        if freshness is not None:
            freshness = _number(freshness, "freshness_seconds")
        object.__setattr__(self, "freshness_seconds", freshness)
        object.__setattr__(
            self, "observed_at", _timestamp(self.observed_at, "observed_at")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_ref": self.endpoint_ref,
            "replica_id": self.replica_id,
            "partition_id": self.partition_id,
            "role": self.role,
            "priority": self.priority,
            "authority_epoch": self.authority_epoch,
            "checkpoint": self.checkpoint,
            "freshness_seconds": self.freshness_seconds,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RouteEndpoint":
        payload = _mapping(value, "RouteEndpoint")
        fields = {
            "endpoint_ref",
            "replica_id",
            "partition_id",
            "role",
            "priority",
            "authority_epoch",
            "checkpoint",
            "freshness_seconds",
            "observed_at",
        }
        if set(payload) != fields:
            raise DistributedContractError("RouteEndpoint fields are invalid")
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class DistributedRoute:
    route_id: str
    dataset_id: str
    partition_ids: tuple[str, ...]
    endpoints: tuple[RouteEndpoint, ...]
    consistency_profile: str
    topology_generation: int
    topology_revision: int
    partial: bool
    unavailable_partitions: tuple[str, ...]
    fallback: str
    auth_scope: str
    created_at: str
    expires_at: str
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "route_id", _token(self.route_id, "route_id"))
        object.__setattr__(self, "dataset_id", _token(self.dataset_id, "dataset_id"))
        partition_ids = _texts(list(self.partition_ids), "partition_ids")
        if not partition_ids:
            raise DistributedContractError("route partition_ids must not be empty")
        object.__setattr__(self, "partition_ids", partition_ids)
        if any(not isinstance(item, RouteEndpoint) for item in self.endpoints):
            raise DistributedContractError(
                "endpoints must contain RouteEndpoint values"
            )
        route_partition_ids = {item.partition_id for item in self.endpoints}
        if not route_partition_ids.issubset(partition_ids):
            raise DistributedContractError(
                "route endpoint references an unrequested partition"
            )
        object.__setattr__(
            self,
            "endpoints",
            tuple(
                sorted(
                    self.endpoints, key=lambda item: (item.priority, item.endpoint_ref)
                )
            ),
        )
        profile = _token(
            self.consistency_profile, "consistency_profile", max_length=40
        ).lower()
        if profile not in CONSISTENCY_PROFILES:
            raise DistributedContractError("consistency_profile is invalid")
        object.__setattr__(self, "consistency_profile", profile)
        object.__setattr__(
            self,
            "topology_generation",
            _integer(self.topology_generation, "topology_generation", minimum=1),
        )
        object.__setattr__(
            self,
            "topology_revision",
            _integer(self.topology_revision, "topology_revision", minimum=1),
        )
        if not isinstance(self.partial, bool):
            raise DistributedContractError("partial must be a boolean")
        unavailable = _texts(
            list(self.unavailable_partitions), "unavailable_partitions"
        )
        if not set(unavailable).issubset(partition_ids):
            raise DistributedContractError(
                "unavailable_partitions must be requested partitions"
            )
        if self.partial != bool(unavailable):
            raise DistributedContractError("partial must match unavailable_partitions")
        object.__setattr__(self, "unavailable_partitions", unavailable)
        fallback = _token(self.fallback, "fallback", max_length=30).lower()
        if fallback not in {"none", "stale", "coordinator", "external_source"}:
            raise DistributedContractError("route fallback is invalid")
        object.__setattr__(self, "fallback", fallback)
        object.__setattr__(self, "auth_scope", _token(self.auth_scope, "auth_scope"))
        object.__setattr__(
            self, "created_at", _timestamp(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "expires_at", _timestamp(self.expires_at, "expires_at")
        )
        if self.expires_at <= self.created_at:
            raise DistributedContractError("route expires_at must be after created_at")
        object.__setattr__(
            self, "revision", _integer(self.revision, "revision", minimum=1)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ROUTE_SCHEMA,
            "route_id": self.route_id,
            "dataset_id": self.dataset_id,
            "partition_ids": list(self.partition_ids),
            "endpoints": [item.to_dict() for item in self.endpoints],
            "consistency_profile": self.consistency_profile,
            "topology_generation": self.topology_generation,
            "topology_revision": self.topology_revision,
            "partial": self.partial,
            "unavailable_partitions": list(self.unavailable_partitions),
            "fallback": self.fallback,
            "auth_scope": self.auth_scope,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revision": self.revision,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DistributedRoute":
        fields = {
            "schema",
            "route_id",
            "dataset_id",
            "partition_ids",
            "endpoints",
            "consistency_profile",
            "topology_generation",
            "topology_revision",
            "partial",
            "unavailable_partitions",
            "fallback",
            "auth_scope",
            "created_at",
            "expires_at",
            "revision",
        }
        payload = _strict(
            value, schema=ROUTE_SCHEMA, fields=fields, field_name="DistributedRoute"
        )
        raw_endpoints = payload["endpoints"]
        if not isinstance(raw_endpoints, list):
            raise DistributedContractError("endpoints must be a list")
        return cls(
            route_id=payload["route_id"],
            dataset_id=payload["dataset_id"],
            partition_ids=_texts(payload["partition_ids"], "partition_ids"),
            endpoints=tuple(RouteEndpoint.from_mapping(item) for item in raw_endpoints),
            consistency_profile=payload["consistency_profile"],
            topology_generation=payload["topology_generation"],
            topology_revision=payload["topology_revision"],
            partial=payload["partial"],
            unavailable_partitions=_texts(
                payload["unavailable_partitions"], "unavailable_partitions"
            ),
            fallback=payload["fallback"],
            auth_scope=payload["auth_scope"],
            created_at=payload["created_at"],
            expires_at=payload["expires_at"],
            revision=payload["revision"],
        )


@dataclass(frozen=True, slots=True)
class TopologyPhaseResult:
    phase: str
    state: str
    attempt: int
    idempotency_key: str
    receipt: Mapping[str, Any]
    started_at: str
    finished_at: str | None
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "phase", _token(self.phase, "phase", max_length=40).lower()
        )
        state = _token(self.state, "state", max_length=30).lower()
        if state not in {
            "pending",
            "running",
            "succeeded",
            "failed",
            "uncertain",
            "skipped",
        }:
            raise DistributedContractError("topology phase state is invalid")
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self, "attempt", _integer(self.attempt, "attempt", minimum=1)
        )
        object.__setattr__(
            self, "idempotency_key", _token(self.idempotency_key, "idempotency_key")
        )
        receipt = _mapping(self.receipt, "receipt")
        encoded_size = len(str(receipt).encode("utf-8"))
        if encoded_size > 32_768:
            raise DistributedContractError("phase receipt exceeds 32 KiB")
        object.__setattr__(self, "receipt", receipt)
        object.__setattr__(
            self, "started_at", _timestamp(self.started_at, "started_at")
        )
        object.__setattr__(
            self, "finished_at", _optional_timestamp(self.finished_at, "finished_at")
        )
        if (
            state in {"succeeded", "failed", "uncertain", "skipped"}
            and self.finished_at is None
        ):
            raise DistributedContractError(
                "terminal topology phase requires finished_at"
            )
        object.__setattr__(
            self, "error_code", _optional_token(self.error_code, "error_code")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "state": self.state,
            "attempt": self.attempt,
            "idempotency_key": self.idempotency_key,
            "receipt": dict(self.receipt),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_code": self.error_code,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TopologyPhaseResult":
        payload = _mapping(value, "TopologyPhaseResult")
        fields = {
            "phase",
            "state",
            "attempt",
            "idempotency_key",
            "receipt",
            "started_at",
            "finished_at",
            "error_code",
        }
        if set(payload) != fields:
            raise DistributedContractError("TopologyPhaseResult fields are invalid")
        return cls(
            phase=payload["phase"],
            state=payload["state"],
            attempt=payload["attempt"],
            idempotency_key=payload["idempotency_key"],
            receipt=_mapping(payload["receipt"], "receipt"),
            started_at=payload["started_at"],
            finished_at=payload["finished_at"],
            error_code=payload["error_code"],
        )


@dataclass(frozen=True, slots=True)
class TopologyOperation:
    operation_id: str
    kind: str
    target_ref: str
    state: str
    expected_revision: int
    authority_epoch: int
    idempotency_key: str
    phases: tuple[TopologyPhaseResult, ...]
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "operation_id", _token(self.operation_id, "operation_id")
        )
        kind = _token(self.kind, "kind", max_length=30).lower()
        if kind not in {
            "reconcile",
            "replicate",
            "handoff",
            "drain",
            "remove",
            "repair",
        }:
            raise DistributedContractError("topology operation kind is invalid")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "target_ref", _token(self.target_ref, "target_ref"))
        state = _token(self.state, "state", max_length=30).lower()
        if state not in {"pending", "running", "succeeded", "failed", "uncertain"}:
            raise DistributedContractError("topology operation state is invalid")
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self,
            "expected_revision",
            _integer(self.expected_revision, "expected_revision", minimum=0),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _integer(self.authority_epoch, "authority_epoch", minimum=0),
        )
        object.__setattr__(
            self, "idempotency_key", _token(self.idempotency_key, "idempotency_key")
        )
        if any(not isinstance(item, TopologyPhaseResult) for item in self.phases):
            raise DistributedContractError(
                "phases must contain TopologyPhaseResult values"
            )
        object.__setattr__(self, "phases", tuple(self.phases))
        object.__setattr__(
            self, "created_at", _timestamp(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "updated_at", _timestamp(self.updated_at, "updated_at")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": TOPOLOGY_OPERATION_SCHEMA,
            "operation_id": self.operation_id,
            "kind": self.kind,
            "target_ref": self.target_ref,
            "state": self.state,
            "expected_revision": self.expected_revision,
            "authority_epoch": self.authority_epoch,
            "idempotency_key": self.idempotency_key,
            "phases": [item.to_dict() for item in self.phases],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TopologyOperation":
        fields = {
            "schema",
            "operation_id",
            "kind",
            "target_ref",
            "state",
            "expected_revision",
            "authority_epoch",
            "idempotency_key",
            "phases",
            "created_at",
            "updated_at",
        }
        payload = _strict(
            value,
            schema=TOPOLOGY_OPERATION_SCHEMA,
            fields=fields,
            field_name="TopologyOperation",
        )
        raw_phases = payload["phases"]
        if not isinstance(raw_phases, list):
            raise DistributedContractError("phases must be a list")
        return cls(
            operation_id=payload["operation_id"],
            kind=payload["kind"],
            target_ref=payload["target_ref"],
            state=payload["state"],
            expected_revision=payload["expected_revision"],
            authority_epoch=payload["authority_epoch"],
            idempotency_key=payload["idempotency_key"],
            phases=tuple(TopologyPhaseResult.from_mapping(item) for item in raw_phases),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )


@dataclass(frozen=True, slots=True)
class TransferRecord:
    transfer_id: str
    operation_id: str
    partition_id: str
    source_instance_id: str
    target_instance_id: str
    authority_epoch: int
    state: str
    checkpoint: str | None
    manifest_digest: str
    item_count: int
    byte_count: int
    resume_token_ref: str | None
    started_at: str
    updated_at: str

    def __post_init__(self) -> None:
        for field_name in (
            "transfer_id",
            "operation_id",
            "partition_id",
            "source_instance_id",
            "target_instance_id",
        ):
            object.__setattr__(
                self, field_name, _token(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "authority_epoch",
            _integer(self.authority_epoch, "authority_epoch", minimum=1),
        )
        state = _token(self.state, "state", max_length=30).lower()
        if state not in {
            "preparing",
            "transferring",
            "verifying",
            "complete",
            "failed",
            "uncertain",
        }:
            raise DistributedContractError("transfer state is invalid")
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self, "checkpoint", _optional_token(self.checkpoint, "checkpoint")
        )
        object.__setattr__(
            self, "manifest_digest", _digest(self.manifest_digest, "manifest_digest")
        )
        object.__setattr__(self, "item_count", _integer(self.item_count, "item_count"))
        object.__setattr__(self, "byte_count", _integer(self.byte_count, "byte_count"))
        object.__setattr__(
            self,
            "resume_token_ref",
            _optional_token(self.resume_token_ref, "resume_token_ref"),
        )
        object.__setattr__(
            self, "started_at", _timestamp(self.started_at, "started_at")
        )
        object.__setattr__(
            self, "updated_at", _timestamp(self.updated_at, "updated_at")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": TRANSFER_SCHEMA,
            "transfer_id": self.transfer_id,
            "operation_id": self.operation_id,
            "partition_id": self.partition_id,
            "source_instance_id": self.source_instance_id,
            "target_instance_id": self.target_instance_id,
            "authority_epoch": self.authority_epoch,
            "state": self.state,
            "checkpoint": self.checkpoint,
            "manifest_digest": self.manifest_digest,
            "item_count": self.item_count,
            "byte_count": self.byte_count,
            "resume_token_ref": self.resume_token_ref,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TransferRecord":
        fields = {
            "schema",
            "transfer_id",
            "operation_id",
            "partition_id",
            "source_instance_id",
            "target_instance_id",
            "authority_epoch",
            "state",
            "checkpoint",
            "manifest_digest",
            "item_count",
            "byte_count",
            "resume_token_ref",
            "started_at",
            "updated_at",
        }
        payload = _strict(
            value, schema=TRANSFER_SCHEMA, fields=fields, field_name="TransferRecord"
        )
        return cls(**{key: payload[key] for key in fields if key != "schema"})


def distributed_contract_digest(value: Any) -> str:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise DistributedContractError("distributed contract must be an object")
    return canonical_payload_digest(dict(value))


__all__ = [
    "CONSISTENCY_PROFILES",
    "CONTENT_STATES",
    "DATASET_SCHEMA",
    "Dataset",
    "DistributedContractError",
    "DistributedRoute",
    "INSTANCE_STATES",
    "PARTITION_SCHEMA",
    "Partition",
    "REPLICA_LIFECYCLES",
    "REPLICA_ROLES",
    "REPLICA_SCHEMA",
    "ROUTE_SCHEMA",
    "Replica",
    "RouteEndpoint",
    "SERVICE_DEFINITION_SCHEMA",
    "SERVICE_GROUP_SCHEMA",
    "SERVICE_INSTANCE_SCHEMA",
    "ServiceDefinition",
    "ServiceEndpoint",
    "ServiceGroup",
    "ServiceInstance",
    "TOPOLOGY_LEASE_SCHEMA",
    "TOPOLOGY_OPERATION_SCHEMA",
    "TRANSFER_SCHEMA",
    "TopologyLease",
    "TopologyOperation",
    "TopologyPhaseResult",
    "TransferRecord",
    "distributed_contract_digest",
    "utc_now",
]
