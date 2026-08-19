from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from adaos.domain.artifact_release import canonical_payload_digest


NODE_INVENTORY_SCHEMA = "adaos.node.inventory.v1"
PROJECT_DEPLOYMENT_SCHEMA = "adaos.project.deployment.v1"
DEPLOYMENT_REVISION_SCHEMA = "adaos.project.deployment_revision.v1"
DEPLOYMENT_PLAN_SCHEMA = "adaos.project.deployment_plan.v1"
COMPONENT_ACTIVATION_SCHEMA = "adaos.project.component_activation.v1"
DEPLOYMENT_OPERATION_SCHEMA = "adaos.project.deployment_operation.v1"

PLACEMENT_MODES = {
    "singleton",
    "selected_nodes",
    "all_matching",
    "per_endpoint",
    "co_located_with",
}
DEPLOYMENT_STATUSES = {
    "draft",
    "planned",
    "applying",
    "active",
    "degraded",
    "failed",
    "removing",
    "removed",
}
ACTIVATION_STATUSES = {
    "preparing",
    "active",
    "draining",
    "inactive",
    "failed",
    "uncertain",
    "removed",
}
PLAN_STATUSES = {"ready", "blocked", "applied", "superseded", "failed"}
PLAN_ACTIONS = {"install", "update", "noop", "cordon", "drain", "deactivate", "remove"}
OPERATION_KINDS = {"apply", "reconcile", "drain", "remove", "rollback"}
OPERATION_STATES = {
    "accepted",
    "running",
    "succeeded",
    "partial",
    "failed",
    "uncertain",
    "rolled_back",
    "cancelled",
}
TRUST_STATES = {"trusted", "pending", "revoked", "untrusted"}
OPERATION_PHASES = {
    "fetch",
    "verify",
    "stage",
    "activate",
    "health",
    "commit",
    "cordon",
    "drain",
    "deactivate",
    "remove",
    "rollback",
}
PHASE_STATES = {"pending", "running", "succeeded", "failed", "uncertain", "skipped"}


class ProjectDeploymentContractError(ValueError):
    """Raised when a distributed Project deployment contract is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectDeploymentContractError(f"{field_name} must be an object")
    return {str(key): item for key, item in value.items()}


def _optional_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    return _mapping(value, field_name)


def _require_contract(
    value: Mapping[str, Any],
    *,
    schema: str,
    allowed: set[str],
    required: set[str],
    field_name: str,
) -> dict[str, Any]:
    payload = _mapping(value, field_name)
    if payload.get("schema") != schema:
        raise ProjectDeploymentContractError(f"{field_name} must use {schema}")
    unknown = set(payload).difference(allowed)
    if unknown:
        raise ProjectDeploymentContractError(
            f"{field_name} contains unsupported fields: {', '.join(sorted(unknown))}"
        )
    missing = required.difference(payload)
    if missing:
        raise ProjectDeploymentContractError(
            f"{field_name} is missing fields: {', '.join(sorted(missing))}"
        )
    return payload


def _token(value: Any, field_name: str, *, max_length: int = 500) -> str:
    token = str(value or "").strip()
    if not token:
        raise ProjectDeploymentContractError(f"{field_name} is required")
    if len(token) > max_length:
        raise ProjectDeploymentContractError(
            f"{field_name} exceeds {max_length} characters"
        )
    return token


def _optional_token(value: Any, *, max_length: int = 500) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    if len(token) > max_length:
        raise ProjectDeploymentContractError(f"value exceeds {max_length} characters")
    return token


def _component_ref(value: Any, field_name: str = "component_ref") -> str:
    token = _token(value, field_name, max_length=160)
    kind, separator, artifact_id = token.partition(":")
    if separator != ":" or kind not in {"skill", "scenario"} or not artifact_id:
        raise ProjectDeploymentContractError(
            f"{field_name} must be skill:<id> or scenario:<id>"
        )
    return token


def _project_ref(value: Any) -> str:
    token = _token(value, "project_ref", max_length=160)
    if not token.startswith("project:") or not token.partition(":")[2]:
        raise ProjectDeploymentContractError("project_ref must be project:<id>")
    return token


def _digest(value: Any, field_name: str) -> str:
    token = _token(value, field_name, max_length=80)
    if not token.startswith("sha256:") or len(token) != 71:
        raise ProjectDeploymentContractError(f"{field_name} must be a sha256 digest")
    try:
        int(token[7:], 16)
    except ValueError as exc:
        raise ProjectDeploymentContractError(
            f"{field_name} must be a sha256 digest"
        ) from exc
    return token.lower()


def _integer(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ProjectDeploymentContractError(f"{field_name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectDeploymentContractError(
            f"{field_name} must be an integer"
        ) from exc
    if result < minimum:
        raise ProjectDeploymentContractError(f"{field_name} must be >= {minimum}")
    return result


def _timestamp(value: Any, field_name: str) -> str:
    token = _token(value, field_name, max_length=80)
    try:
        datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectDeploymentContractError(
            f"{field_name} must be an ISO date-time"
        ) from exc
    return token


def _texts(value: Any, field_name: str, *, max_items: int = 200) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ProjectDeploymentContractError(f"{field_name} must be a list")
    if len(value) > max_items:
        raise ProjectDeploymentContractError(f"{field_name} exceeds {max_items} items")
    items = tuple(sorted({_token(item, field_name, max_length=300) for item in value}))
    return items


def _ordered_texts(
    value: Any, field_name: str, *, max_items: int = 200
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ProjectDeploymentContractError(f"{field_name} must be a list")
    if len(value) > max_items:
        raise ProjectDeploymentContractError(f"{field_name} exceeds {max_items} items")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = _token(item, field_name, max_length=300)
        if token not in seen:
            result.append(token)
            seen.add(token)
    return tuple(result)


def _mappings(
    value: Any, field_name: str, *, max_items: int = 1000
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise ProjectDeploymentContractError(f"{field_name} must be a list")
    if len(value) > max_items:
        raise ProjectDeploymentContractError(f"{field_name} exceeds {max_items} items")
    return tuple(_mapping(item, field_name) for item in value)


@dataclass(frozen=True, slots=True)
class NodeEndpointRecord:
    endpoint_id: str
    role: str
    available: bool
    capabilities: tuple[str, ...] = ()
    labels: Mapping[str, str] = field(default_factory=dict)
    capacity: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "endpoint_id",
            _token(self.endpoint_id, "endpoint_id", max_length=300),
        )
        object.__setattr__(
            self, "role", _token(self.role, "endpoint role", max_length=80)
        )
        if not isinstance(self.available, bool):
            raise ProjectDeploymentContractError("endpoint available must be a boolean")
        object.__setattr__(
            self,
            "capabilities",
            _texts(list(self.capabilities), "endpoint capabilities"),
        )
        object.__setattr__(
            self,
            "labels",
            {str(key): str(item) for key, item in dict(self.labels).items()},
        )
        capacity: dict[str, int] = {}
        for key, value in dict(self.capacity).items():
            capacity[str(key)] = _integer(value, f"endpoint capacity {key}", minimum=0)
        object.__setattr__(self, "capacity", capacity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "role": self.role,
            "available": self.available,
            "capabilities": list(self.capabilities),
            "labels": dict(self.labels),
            "capacity": dict(self.capacity),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NodeEndpointRecord":
        payload = _mapping(value, "NodeEndpointRecord")
        allowed = {
            "endpoint_id",
            "role",
            "available",
            "capabilities",
            "labels",
            "capacity",
        }
        unknown = set(payload).difference(allowed)
        missing = allowed.difference(payload)
        if unknown or missing:
            detail = sorted(unknown or missing)
            problem = "unsupported" if unknown else "missing"
            raise ProjectDeploymentContractError(
                f"NodeEndpointRecord contains {problem} fields: {', '.join(detail)}"
            )
        if not isinstance(payload["available"], bool):
            raise ProjectDeploymentContractError("endpoint available must be a boolean")
        return cls(
            endpoint_id=payload["endpoint_id"],
            role=payload["role"],
            available=payload["available"],
            capabilities=_texts(payload["capabilities"], "endpoint capabilities"),
            labels=_optional_mapping(payload["labels"], "endpoint labels"),
            capacity=_optional_mapping(payload["capacity"], "endpoint capacity"),
        )


@dataclass(frozen=True, slots=True)
class NodeInventoryRecord:
    node_id: str
    subnet_id: str
    trust_state: str
    online: bool
    architecture: str
    runtime_version: str
    capabilities: tuple[str, ...] = ()
    labels: Mapping[str, str] = field(default_factory=dict)
    capacity: Mapping[str, int] = field(default_factory=dict)
    endpoints: tuple[NodeEndpointRecord, ...] = ()
    observed_at: str = field(default_factory=utc_now)
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "node_id", _token(self.node_id, "node_id", max_length=300)
        )
        object.__setattr__(
            self, "subnet_id", _token(self.subnet_id, "subnet_id", max_length=160)
        )
        trust_state = _token(self.trust_state, "trust_state", max_length=20).lower()
        if trust_state not in TRUST_STATES:
            raise ProjectDeploymentContractError("trust_state is invalid")
        object.__setattr__(self, "trust_state", trust_state)
        if not isinstance(self.online, bool):
            raise ProjectDeploymentContractError("online must be a boolean")
        object.__setattr__(
            self,
            "architecture",
            _token(self.architecture, "architecture", max_length=80),
        )
        object.__setattr__(
            self,
            "runtime_version",
            _token(self.runtime_version, "runtime_version", max_length=120),
        )
        object.__setattr__(
            self, "capabilities", _texts(list(self.capabilities), "capabilities")
        )
        labels = {str(key): str(item) for key, item in dict(self.labels).items()}
        object.__setattr__(self, "labels", labels)
        capacity: dict[str, int] = {}
        for key, value in dict(self.capacity).items():
            capacity[str(key)] = _integer(value, f"capacity {key}", minimum=0)
        object.__setattr__(self, "capacity", capacity)
        if any(not isinstance(item, NodeEndpointRecord) for item in self.endpoints):
            raise ProjectDeploymentContractError(
                "endpoints must contain NodeEndpointRecord values"
            )
        endpoint_ids = [item.endpoint_id for item in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ProjectDeploymentContractError("endpoint ids must be unique per node")
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
            "schema": NODE_INVENTORY_SCHEMA,
            "node_id": self.node_id,
            "subnet_id": self.subnet_id,
            "trust_state": self.trust_state,
            "online": bool(self.online),
            "architecture": self.architecture,
            "runtime_version": self.runtime_version,
            "capabilities": list(self.capabilities),
            "labels": dict(self.labels),
            "capacity": dict(self.capacity),
            "endpoints": [item.to_dict() for item in self.endpoints],
            "observed_at": self.observed_at,
            "revision": self.revision,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NodeInventoryRecord":
        payload = _require_contract(
            value,
            schema=NODE_INVENTORY_SCHEMA,
            field_name="NodeInventoryRecord",
            allowed={
                "schema",
                "node_id",
                "subnet_id",
                "trust_state",
                "online",
                "architecture",
                "runtime_version",
                "capabilities",
                "labels",
                "capacity",
                "endpoints",
                "observed_at",
                "revision",
            },
            required={
                "schema",
                "node_id",
                "subnet_id",
                "trust_state",
                "online",
                "architecture",
                "runtime_version",
                "capabilities",
                "labels",
                "capacity",
                "endpoints",
                "observed_at",
                "revision",
            },
        )
        if not isinstance(payload["online"], bool):
            raise ProjectDeploymentContractError("online must be a boolean")
        return cls(
            node_id=payload["node_id"],
            subnet_id=payload["subnet_id"],
            trust_state=payload["trust_state"],
            online=payload["online"],
            architecture=payload["architecture"],
            runtime_version=payload["runtime_version"],
            capabilities=_texts(payload["capabilities"], "capabilities"),
            labels=_optional_mapping(payload["labels"], "labels"),
            capacity=_optional_mapping(payload["capacity"], "capacity"),
            endpoints=tuple(
                NodeEndpointRecord.from_mapping(item)
                for item in _mappings(payload["endpoints"], "endpoints", max_items=100)
            ),
            observed_at=payload["observed_at"],
            revision=payload["revision"],
        )


@dataclass(frozen=True, slots=True)
class ComponentPlacementPolicy:
    component_ref: str
    mode: str
    selected_node_ids: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    required_labels: Mapping[str, str] = field(default_factory=dict)
    endpoint_role: str | None = None
    co_located_with: str | None = None
    min_instances: int = 1
    max_instances: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "component_ref", _component_ref(self.component_ref))
        mode = _token(self.mode, "placement mode", max_length=40).lower()
        if mode not in PLACEMENT_MODES:
            raise ProjectDeploymentContractError("placement mode is invalid")
        object.__setattr__(self, "mode", mode)
        selected = _texts(list(self.selected_node_ids), "selected_node_ids")
        if mode == "selected_nodes" and not selected:
            raise ProjectDeploymentContractError(
                "selected_nodes placement requires selected_node_ids"
            )
        object.__setattr__(self, "selected_node_ids", selected)
        object.__setattr__(
            self,
            "required_capabilities",
            _texts(list(self.required_capabilities), "required_capabilities"),
        )
        object.__setattr__(
            self,
            "required_labels",
            {str(key): str(item) for key, item in dict(self.required_labels).items()},
        )
        endpoint_role = _optional_token(self.endpoint_role, max_length=80)
        if mode == "per_endpoint" and not endpoint_role:
            raise ProjectDeploymentContractError(
                "per_endpoint placement requires endpoint_role"
            )
        object.__setattr__(self, "endpoint_role", endpoint_role)
        colocated = _optional_token(self.co_located_with, max_length=160)
        if colocated:
            colocated = _component_ref(colocated, "co_located_with")
        if mode == "co_located_with" and not colocated:
            raise ProjectDeploymentContractError(
                "co_located_with placement requires a component ref"
            )
        if colocated == self.component_ref:
            raise ProjectDeploymentContractError(
                "a component cannot be colocated with itself"
            )
        object.__setattr__(self, "co_located_with", colocated)
        minimum = _integer(self.min_instances, "min_instances", minimum=0)
        maximum = (
            None
            if self.max_instances is None
            else _integer(self.max_instances, "max_instances", minimum=1)
        )
        if maximum is not None and maximum < minimum:
            raise ProjectDeploymentContractError(
                "max_instances must be >= min_instances"
            )
        if mode == "singleton" and (minimum != 1 or maximum not in {None, 1}):
            raise ProjectDeploymentContractError(
                "singleton placement requires exactly one instance"
            )
        object.__setattr__(self, "min_instances", minimum)
        object.__setattr__(self, "max_instances", maximum)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_ref": self.component_ref,
            "mode": self.mode,
            "selected_node_ids": list(self.selected_node_ids),
            "required_capabilities": list(self.required_capabilities),
            "required_labels": dict(self.required_labels),
            "endpoint_role": self.endpoint_role,
            "co_located_with": self.co_located_with,
            "min_instances": self.min_instances,
            "max_instances": self.max_instances,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ComponentPlacementPolicy":
        payload = _mapping(value, "ComponentPlacementPolicy")
        allowed = {
            "component_ref",
            "mode",
            "selected_node_ids",
            "required_capabilities",
            "required_labels",
            "endpoint_role",
            "co_located_with",
            "min_instances",
            "max_instances",
        }
        unknown = set(payload).difference(allowed)
        if unknown:
            raise ProjectDeploymentContractError(
                f"ComponentPlacementPolicy contains unsupported fields: {', '.join(sorted(unknown))}"
            )
        return cls(
            component_ref=payload.get("component_ref"),
            mode=payload.get("mode"),
            selected_node_ids=_texts(
                payload.get("selected_node_ids") or [], "selected_node_ids"
            ),
            required_capabilities=_texts(
                payload.get("required_capabilities") or [], "required_capabilities"
            ),
            required_labels=_optional_mapping(
                payload.get("required_labels"), "required_labels"
            ),
            endpoint_role=payload.get("endpoint_role"),
            co_located_with=payload.get("co_located_with"),
            min_instances=payload.get("min_instances", 1),
            max_instances=payload.get("max_instances"),
        )


@dataclass(frozen=True, slots=True)
class DeploymentCompatibilityPolicy:
    architectures: tuple[str, ...] = ()
    minimum_runtime_version: str | None = None
    required_protocols: Mapping[str, str] = field(default_factory=dict)
    allow_release_skew: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "architectures",
            _texts(list(self.architectures), "architectures", max_items=20),
        )
        object.__setattr__(
            self,
            "minimum_runtime_version",
            _optional_token(self.minimum_runtime_version, max_length=120),
        )
        object.__setattr__(
            self,
            "required_protocols",
            {
                str(key): str(item)
                for key, item in dict(self.required_protocols).items()
            },
        )
        if not isinstance(self.allow_release_skew, bool):
            raise ProjectDeploymentContractError("allow_release_skew must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "architectures": list(self.architectures),
            "minimum_runtime_version": self.minimum_runtime_version,
            "required_protocols": dict(self.required_protocols),
            "allow_release_skew": self.allow_release_skew,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeploymentCompatibilityPolicy":
        payload = _mapping(value, "DeploymentCompatibilityPolicy")
        allowed = {
            "architectures",
            "minimum_runtime_version",
            "required_protocols",
            "allow_release_skew",
        }
        unknown = set(payload).difference(allowed)
        missing = allowed.difference(payload)
        if unknown or missing:
            detail = sorted(unknown or missing)
            problem = "unsupported" if unknown else "missing"
            raise ProjectDeploymentContractError(
                f"DeploymentCompatibilityPolicy contains {problem} fields: {', '.join(detail)}"
            )
        if not isinstance(payload["allow_release_skew"], bool):
            raise ProjectDeploymentContractError("allow_release_skew must be a boolean")
        return cls(
            architectures=_texts(
                payload["architectures"], "architectures", max_items=20
            ),
            minimum_runtime_version=payload["minimum_runtime_version"],
            required_protocols=_optional_mapping(
                payload["required_protocols"], "required_protocols"
            ),
            allow_release_skew=payload["allow_release_skew"],
        )


@dataclass(frozen=True, slots=True)
class RolloutPolicy:
    batch_size: int = 1
    max_unavailable: int = 1
    pause_seconds: int = 0
    stop_on_failure: bool = True
    rollback_on_failure: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "batch_size", _integer(self.batch_size, "batch_size", minimum=1)
        )
        object.__setattr__(
            self,
            "max_unavailable",
            _integer(self.max_unavailable, "max_unavailable", minimum=0),
        )
        object.__setattr__(
            self,
            "pause_seconds",
            _integer(self.pause_seconds, "pause_seconds", minimum=0),
        )
        if not isinstance(self.stop_on_failure, bool):
            raise ProjectDeploymentContractError("stop_on_failure must be a boolean")
        if not isinstance(self.rollback_on_failure, bool):
            raise ProjectDeploymentContractError(
                "rollback_on_failure must be a boolean"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_size": self.batch_size,
            "max_unavailable": self.max_unavailable,
            "pause_seconds": self.pause_seconds,
            "stop_on_failure": self.stop_on_failure,
            "rollback_on_failure": self.rollback_on_failure,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RolloutPolicy":
        payload = _mapping(value, "RolloutPolicy")
        allowed = {
            "batch_size",
            "max_unavailable",
            "pause_seconds",
            "stop_on_failure",
            "rollback_on_failure",
        }
        unknown = set(payload).difference(allowed)
        missing = allowed.difference(payload)
        if unknown or missing:
            detail = sorted(unknown or missing)
            problem = "unsupported" if unknown else "missing"
            raise ProjectDeploymentContractError(
                f"RolloutPolicy contains {problem} fields: {', '.join(detail)}"
            )
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class DataRetentionPolicy:
    runtime_data: str = "retain"
    derived_data: str = "retain"
    external_data: str = "retain"

    def __post_init__(self) -> None:
        runtime_data = _token(self.runtime_data, "runtime_data", max_length=20).lower()
        derived_data = _token(self.derived_data, "derived_data", max_length=20).lower()
        external_data = _token(
            self.external_data, "external_data", max_length=20
        ).lower()
        if runtime_data not in {"retain", "delete"}:
            raise ProjectDeploymentContractError("runtime_data retention is invalid")
        if derived_data not in {"retain", "delete", "rebuild"}:
            raise ProjectDeploymentContractError("derived_data retention is invalid")
        if external_data != "retain":
            raise ProjectDeploymentContractError(
                "external_data retention must be retain"
            )
        object.__setattr__(self, "runtime_data", runtime_data)
        object.__setattr__(self, "derived_data", derived_data)
        object.__setattr__(self, "external_data", external_data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_data": self.runtime_data,
            "derived_data": self.derived_data,
            "external_data": self.external_data,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DataRetentionPolicy":
        payload = _mapping(value, "DataRetentionPolicy")
        allowed = {"runtime_data", "derived_data", "external_data"}
        unknown = set(payload).difference(allowed)
        missing = allowed.difference(payload)
        if unknown or missing:
            detail = sorted(unknown or missing)
            problem = "unsupported" if unknown else "missing"
            raise ProjectDeploymentContractError(
                f"DataRetentionPolicy contains {problem} fields: {', '.join(detail)}"
            )
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ProjectDeployment:
    deployment_id: str
    project_ref: str
    release_digest: str
    subnet_id: str
    revision: int
    placements: tuple[ComponentPlacementPolicy, ...]
    compatibility: DeploymentCompatibilityPolicy = field(
        default_factory=DeploymentCompatibilityPolicy
    )
    rollout: RolloutPolicy = field(default_factory=RolloutPolicy)
    retention: DataRetentionPolicy = field(default_factory=DataRetentionPolicy)
    status: str = "draft"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "deployment_id", _token(self.deployment_id, "deployment_id")
        )
        object.__setattr__(self, "project_ref", _project_ref(self.project_ref))
        object.__setattr__(
            self, "release_digest", _digest(self.release_digest, "release_digest")
        )
        object.__setattr__(
            self, "subnet_id", _token(self.subnet_id, "subnet_id", max_length=160)
        )
        object.__setattr__(
            self, "revision", _integer(self.revision, "revision", minimum=1)
        )
        if not self.placements:
            raise ProjectDeploymentContractError(
                "ProjectDeployment requires at least one placement"
            )
        if any(
            not isinstance(item, ComponentPlacementPolicy) for item in self.placements
        ):
            raise ProjectDeploymentContractError(
                "placements must contain ComponentPlacementPolicy values"
            )
        component_refs = [item.component_ref for item in self.placements]
        if len(component_refs) != len(set(component_refs)):
            raise ProjectDeploymentContractError(
                "placement component refs must be unique"
            )
        placement_refs = set(component_refs)
        for placement in self.placements:
            if (
                placement.co_located_with
                and placement.co_located_with not in placement_refs
            ):
                raise ProjectDeploymentContractError(
                    "co_located_with must reference a declared placement"
                )
        object.__setattr__(
            self,
            "placements",
            tuple(sorted(self.placements, key=lambda item: item.component_ref)),
        )
        if not isinstance(self.compatibility, DeploymentCompatibilityPolicy):
            raise ProjectDeploymentContractError(
                "compatibility must be a DeploymentCompatibilityPolicy"
            )
        if not isinstance(self.rollout, RolloutPolicy):
            raise ProjectDeploymentContractError("rollout must be a RolloutPolicy")
        if not isinstance(self.retention, DataRetentionPolicy):
            raise ProjectDeploymentContractError(
                "retention must be a DataRetentionPolicy"
            )
        status = _token(self.status, "status", max_length=20).lower()
        if status not in DEPLOYMENT_STATUSES:
            raise ProjectDeploymentContractError("deployment status is invalid")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self, "created_at", _timestamp(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "updated_at", _timestamp(self.updated_at, "updated_at")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PROJECT_DEPLOYMENT_SCHEMA,
            "deployment_id": self.deployment_id,
            "project_ref": self.project_ref,
            "release_digest": self.release_digest,
            "subnet_id": self.subnet_id,
            "revision": self.revision,
            "placements": [item.to_dict() for item in self.placements],
            "compatibility": self.compatibility.to_dict(),
            "rollout": self.rollout.to_dict(),
            "retention": self.retention.to_dict(),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProjectDeployment":
        payload = _require_contract(
            value,
            schema=PROJECT_DEPLOYMENT_SCHEMA,
            field_name="ProjectDeployment",
            allowed={
                "schema",
                "deployment_id",
                "project_ref",
                "release_digest",
                "subnet_id",
                "revision",
                "placements",
                "compatibility",
                "rollout",
                "retention",
                "status",
                "created_at",
                "updated_at",
            },
            required={
                "schema",
                "deployment_id",
                "project_ref",
                "release_digest",
                "subnet_id",
                "revision",
                "placements",
                "compatibility",
                "rollout",
                "retention",
                "status",
                "created_at",
                "updated_at",
            },
        )
        return cls(
            deployment_id=payload["deployment_id"],
            project_ref=payload["project_ref"],
            release_digest=payload["release_digest"],
            subnet_id=payload["subnet_id"],
            revision=payload["revision"],
            placements=tuple(
                ComponentPlacementPolicy.from_mapping(item)
                for item in _mappings(
                    payload["placements"], "placements", max_items=100
                )
            ),
            compatibility=DeploymentCompatibilityPolicy.from_mapping(
                _mapping(payload["compatibility"], "compatibility")
            ),
            rollout=RolloutPolicy.from_mapping(_mapping(payload["rollout"], "rollout")),
            retention=DataRetentionPolicy.from_mapping(
                _mapping(payload["retention"], "retention")
            ),
            status=payload["status"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )


@dataclass(frozen=True, slots=True)
class DeploymentRevision:
    deployment_id: str
    revision: int
    desired: ProjectDeployment
    actor_ref: str
    reason: str
    created_at: str = field(default_factory=utc_now)
    previous_desired_digest: str | None = None
    desired_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "deployment_id", _token(self.deployment_id, "deployment_id")
        )
        object.__setattr__(
            self, "revision", _integer(self.revision, "revision", minimum=1)
        )
        if not isinstance(self.desired, ProjectDeployment):
            raise ProjectDeploymentContractError("desired must be a ProjectDeployment")
        if self.desired.deployment_id != self.deployment_id:
            raise ProjectDeploymentContractError(
                "desired deployment_id does not match revision"
            )
        if self.desired.revision != self.revision:
            raise ProjectDeploymentContractError(
                "desired revision does not match DeploymentRevision"
            )
        object.__setattr__(
            self, "actor_ref", _token(self.actor_ref, "actor_ref", max_length=300)
        )
        object.__setattr__(
            self, "reason", _token(self.reason, "reason", max_length=1000)
        )
        object.__setattr__(
            self, "created_at", _timestamp(self.created_at, "created_at")
        )
        if self.previous_desired_digest is not None:
            object.__setattr__(
                self,
                "previous_desired_digest",
                _digest(self.previous_desired_digest, "previous_desired_digest"),
            )
        computed = canonical_payload_digest(self.desired.to_dict())
        if self.desired_digest is not None:
            digest = _digest(self.desired_digest, "desired_digest")
            if digest != computed:
                raise ProjectDeploymentContractError(
                    "desired_digest does not match ProjectDeployment content"
                )
            object.__setattr__(self, "desired_digest", digest)
        else:
            object.__setattr__(self, "desired_digest", computed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DEPLOYMENT_REVISION_SCHEMA,
            "deployment_id": self.deployment_id,
            "revision": self.revision,
            "desired": self.desired.to_dict(),
            "desired_digest": self.desired_digest,
            "previous_desired_digest": self.previous_desired_digest,
            "actor_ref": self.actor_ref,
            "reason": self.reason,
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeploymentRevision":
        payload = _require_contract(
            value,
            schema=DEPLOYMENT_REVISION_SCHEMA,
            field_name="DeploymentRevision",
            allowed={
                "schema",
                "deployment_id",
                "revision",
                "desired",
                "desired_digest",
                "previous_desired_digest",
                "actor_ref",
                "reason",
                "created_at",
            },
            required={
                "schema",
                "deployment_id",
                "revision",
                "desired",
                "desired_digest",
                "previous_desired_digest",
                "actor_ref",
                "reason",
                "created_at",
            },
        )
        return cls(
            deployment_id=payload["deployment_id"],
            revision=payload["revision"],
            desired=ProjectDeployment.from_mapping(
                _mapping(payload["desired"], "desired")
            ),
            desired_digest=payload["desired_digest"],
            previous_desired_digest=payload["previous_desired_digest"],
            actor_ref=payload["actor_ref"],
            reason=payload["reason"],
            created_at=payload["created_at"],
        )


@dataclass(frozen=True, slots=True)
class DeploymentPlanChange:
    action: str
    component_ref: str
    node_id: str
    target_package_digest: str | None = None
    current_activation_ref: str | None = None
    reason: str = ""
    phases: tuple[str, ...] = ()
    availability_impact: str = "none"

    def __post_init__(self) -> None:
        action = _token(self.action, "change action", max_length=20).lower()
        if action not in PLAN_ACTIONS:
            raise ProjectDeploymentContractError("deployment plan action is invalid")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "component_ref", _component_ref(self.component_ref))
        object.__setattr__(
            self, "node_id", _token(self.node_id, "node_id", max_length=300)
        )
        package_digest = self.target_package_digest
        if package_digest is not None:
            package_digest = _digest(package_digest, "target_package_digest")
        if action in {"install", "update", "noop"} and not package_digest:
            raise ProjectDeploymentContractError(
                f"{action} requires target_package_digest"
            )
        object.__setattr__(self, "target_package_digest", package_digest)
        object.__setattr__(
            self, "current_activation_ref", _optional_token(self.current_activation_ref)
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        phases = _ordered_texts(list(self.phases), "phases", max_items=20)
        if any(phase not in OPERATION_PHASES for phase in phases):
            raise ProjectDeploymentContractError(
                "deployment plan contains an invalid phase"
            )
        object.__setattr__(self, "phases", phases)
        impact = _token(
            self.availability_impact, "availability_impact", max_length=40
        ).lower()
        if impact not in {
            "none",
            "reduced_capacity",
            "read_only",
            "temporary_unavailable",
        }:
            raise ProjectDeploymentContractError("availability_impact is invalid")
        object.__setattr__(self, "availability_impact", impact)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "component_ref": self.component_ref,
            "node_id": self.node_id,
            "target_package_digest": self.target_package_digest,
            "current_activation_ref": self.current_activation_ref,
            "reason": self.reason,
            "phases": list(self.phases),
            "availability_impact": self.availability_impact,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeploymentPlanChange":
        payload = _mapping(value, "DeploymentPlanChange")
        allowed = {
            "action",
            "component_ref",
            "node_id",
            "target_package_digest",
            "current_activation_ref",
            "reason",
            "phases",
            "availability_impact",
        }
        unknown = set(payload).difference(allowed)
        if unknown:
            raise ProjectDeploymentContractError(
                f"DeploymentPlanChange contains unsupported fields: {', '.join(sorted(unknown))}"
            )
        return cls(
            action=payload.get("action"),
            component_ref=payload.get("component_ref"),
            node_id=payload.get("node_id"),
            target_package_digest=payload.get("target_package_digest"),
            current_activation_ref=payload.get("current_activation_ref"),
            reason=str(payload.get("reason") or ""),
            phases=_ordered_texts(payload.get("phases") or [], "phases", max_items=20),
            availability_impact=payload.get("availability_impact") or "none",
        )


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    plan_id: str
    deployment_id: str
    expected_revision: int
    release_digest: str
    inventory_revision: str
    changes: tuple[DeploymentPlanChange, ...]
    warnings: tuple[str, ...] = ()
    required_approvals: tuple[str, ...] = ()
    status: str = "ready"
    created_at: str = field(default_factory=utc_now)
    plan_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _token(self.plan_id, "plan_id"))
        object.__setattr__(
            self, "deployment_id", _token(self.deployment_id, "deployment_id")
        )
        object.__setattr__(
            self,
            "expected_revision",
            _integer(self.expected_revision, "expected_revision", minimum=1),
        )
        object.__setattr__(
            self, "release_digest", _digest(self.release_digest, "release_digest")
        )
        object.__setattr__(
            self,
            "inventory_revision",
            _token(self.inventory_revision, "inventory_revision"),
        )
        if any(not isinstance(item, DeploymentPlanChange) for item in self.changes):
            raise ProjectDeploymentContractError(
                "changes must contain DeploymentPlanChange values"
            )
        object.__setattr__(
            self,
            "changes",
            tuple(
                sorted(
                    self.changes,
                    key=lambda item: (item.component_ref, item.node_id, item.action),
                )
            ),
        )
        object.__setattr__(
            self, "warnings", _texts(list(self.warnings), "warnings", max_items=200)
        )
        object.__setattr__(
            self,
            "required_approvals",
            _texts(list(self.required_approvals), "required_approvals", max_items=100),
        )
        status = _token(self.status, "plan status", max_length=20).lower()
        if status not in PLAN_STATUSES:
            raise ProjectDeploymentContractError("deployment plan status is invalid")
        if (
            self.warnings
            and status == "ready"
            and any(item.startswith("blocked:") for item in self.warnings)
        ):
            raise ProjectDeploymentContractError(
                "a plan with blocked warnings must be blocked"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self, "created_at", _timestamp(self.created_at, "created_at")
        )
        if self.plan_digest is not None:
            digest = _digest(self.plan_digest, "plan_digest")
            if digest != self.computed_digest():
                raise ProjectDeploymentContractError(
                    "plan_digest does not match DeploymentPlan content"
                )
            object.__setattr__(self, "plan_digest", digest)
        else:
            object.__setattr__(self, "plan_digest", self.computed_digest())

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema": DEPLOYMENT_PLAN_SCHEMA,
            "plan_id": self.plan_id,
            "deployment_id": self.deployment_id,
            "expected_revision": self.expected_revision,
            "release_digest": self.release_digest,
            "inventory_revision": self.inventory_revision,
            "changes": [item.to_dict() for item in self.changes],
            "warnings": list(self.warnings),
            "required_approvals": list(self.required_approvals),
            "status": self.status,
            "created_at": self.created_at,
        }

    def computed_digest(self) -> str:
        return canonical_payload_digest(self.unsigned_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["plan_digest"] = self.plan_digest
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeploymentPlan":
        payload = _require_contract(
            value,
            schema=DEPLOYMENT_PLAN_SCHEMA,
            field_name="DeploymentPlan",
            allowed={
                "schema",
                "plan_id",
                "deployment_id",
                "expected_revision",
                "release_digest",
                "inventory_revision",
                "changes",
                "warnings",
                "required_approvals",
                "status",
                "created_at",
                "plan_digest",
            },
            required={
                "schema",
                "plan_id",
                "deployment_id",
                "expected_revision",
                "release_digest",
                "inventory_revision",
                "changes",
                "warnings",
                "required_approvals",
                "status",
                "created_at",
                "plan_digest",
            },
        )
        result = cls(
            plan_id=payload["plan_id"],
            deployment_id=payload["deployment_id"],
            expected_revision=payload["expected_revision"],
            release_digest=payload["release_digest"],
            inventory_revision=payload["inventory_revision"],
            changes=tuple(
                DeploymentPlanChange.from_mapping(item)
                for item in _mappings(payload["changes"], "changes")
            ),
            warnings=_texts(payload["warnings"], "warnings"),
            required_approvals=_texts(
                payload["required_approvals"], "required_approvals"
            ),
            status=payload["status"],
            created_at=payload["created_at"],
            plan_digest=payload["plan_digest"],
        )
        return result


@dataclass(frozen=True, slots=True)
class ComponentActivation:
    activation_id: str
    deployment_id: str
    component_ref: str
    node_id: str
    release_digest: str
    package_digest: str
    generation: int
    status: str
    health: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "activation_id", _token(self.activation_id, "activation_id")
        )
        object.__setattr__(
            self, "deployment_id", _token(self.deployment_id, "deployment_id")
        )
        object.__setattr__(self, "component_ref", _component_ref(self.component_ref))
        object.__setattr__(
            self, "node_id", _token(self.node_id, "node_id", max_length=300)
        )
        object.__setattr__(
            self, "release_digest", _digest(self.release_digest, "release_digest")
        )
        object.__setattr__(
            self, "package_digest", _digest(self.package_digest, "package_digest")
        )
        object.__setattr__(
            self, "generation", _integer(self.generation, "generation", minimum=1)
        )
        status = _token(self.status, "activation status", max_length=20).lower()
        if status not in ACTIVATION_STATUSES:
            raise ProjectDeploymentContractError(
                "component activation status is invalid"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "health", dict(self.health))
        object.__setattr__(self, "evidence", dict(self.evidence))
        object.__setattr__(
            self, "created_at", _timestamp(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "updated_at", _timestamp(self.updated_at, "updated_at")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": COMPONENT_ACTIVATION_SCHEMA,
            "activation_id": self.activation_id,
            "deployment_id": self.deployment_id,
            "component_ref": self.component_ref,
            "node_id": self.node_id,
            "release_digest": self.release_digest,
            "package_digest": self.package_digest,
            "generation": self.generation,
            "status": self.status,
            "health": dict(self.health),
            "evidence": dict(self.evidence),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ComponentActivation":
        payload = _require_contract(
            value,
            schema=COMPONENT_ACTIVATION_SCHEMA,
            field_name="ComponentActivation",
            allowed={
                "schema",
                "activation_id",
                "deployment_id",
                "component_ref",
                "node_id",
                "release_digest",
                "package_digest",
                "generation",
                "status",
                "health",
                "evidence",
                "created_at",
                "updated_at",
            },
            required={
                "schema",
                "activation_id",
                "deployment_id",
                "component_ref",
                "node_id",
                "release_digest",
                "package_digest",
                "generation",
                "status",
                "health",
                "evidence",
                "created_at",
                "updated_at",
            },
        )
        return cls(
            activation_id=payload["activation_id"],
            deployment_id=payload["deployment_id"],
            component_ref=payload["component_ref"],
            node_id=payload["node_id"],
            release_digest=payload["release_digest"],
            package_digest=payload["package_digest"],
            generation=payload["generation"],
            status=payload["status"],
            health=_optional_mapping(payload["health"], "health"),
            evidence=_optional_mapping(payload["evidence"], "evidence"),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )


@dataclass(frozen=True, slots=True)
class DeploymentPhaseResult:
    phase: str
    state: str
    attempt: int
    idempotency_key: str
    receipt: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None

    def __post_init__(self) -> None:
        phase = _token(self.phase, "operation phase", max_length=30).lower()
        if phase not in OPERATION_PHASES:
            raise ProjectDeploymentContractError(
                "deployment operation phase is invalid"
            )
        object.__setattr__(self, "phase", phase)
        state = _token(self.state, "phase state", max_length=20).lower()
        if state not in PHASE_STATES:
            raise ProjectDeploymentContractError(
                "deployment operation phase state is invalid"
            )
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self, "attempt", _integer(self.attempt, "attempt", minimum=1)
        )
        object.__setattr__(
            self,
            "idempotency_key",
            _token(self.idempotency_key, "idempotency_key"),
        )
        object.__setattr__(self, "receipt", dict(self.receipt))
        object.__setattr__(self, "error", dict(self.error))
        object.__setattr__(
            self, "started_at", _timestamp(self.started_at, "started_at")
        )
        if self.finished_at is not None:
            object.__setattr__(
                self,
                "finished_at",
                _timestamp(self.finished_at, "finished_at"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "state": self.state,
            "attempt": self.attempt,
            "idempotency_key": self.idempotency_key,
            "receipt": dict(self.receipt),
            "error": dict(self.error),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeploymentPhaseResult":
        payload = _mapping(value, "DeploymentPhaseResult")
        allowed = {
            "phase",
            "state",
            "attempt",
            "idempotency_key",
            "receipt",
            "error",
            "started_at",
            "finished_at",
        }
        unknown = set(payload).difference(allowed)
        missing = allowed.difference(payload)
        if unknown or missing:
            detail = sorted(unknown or missing)
            problem = "unsupported" if unknown else "missing"
            raise ProjectDeploymentContractError(
                f"DeploymentPhaseResult contains {problem} fields: {', '.join(detail)}"
            )
        return cls(
            phase=payload["phase"],
            state=payload["state"],
            attempt=payload["attempt"],
            idempotency_key=payload["idempotency_key"],
            receipt=_optional_mapping(payload["receipt"], "receipt"),
            error=_optional_mapping(payload["error"], "error"),
            started_at=payload["started_at"],
            finished_at=payload["finished_at"],
        )


@dataclass(frozen=True, slots=True)
class DeploymentNodeResult:
    node_id: str
    state: str
    phases: tuple[DeploymentPhaseResult, ...]
    activation_ref: str | None = None
    error: Mapping[str, Any] = field(default_factory=dict)
    uncertain: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "node_id", _token(self.node_id, "node_id", max_length=300)
        )
        state = _token(self.state, "node result state", max_length=20).lower()
        if state not in OPERATION_STATES:
            raise ProjectDeploymentContractError(
                "deployment node result state is invalid"
            )
        if not isinstance(self.uncertain, bool):
            raise ProjectDeploymentContractError("uncertain must be a boolean")
        if self.uncertain and state not in {"uncertain", "partial", "failed"}:
            raise ProjectDeploymentContractError(
                "uncertain node results must expose uncertain/partial/failed state"
            )
        if any(not isinstance(item, DeploymentPhaseResult) for item in self.phases):
            raise ProjectDeploymentContractError(
                "phases must contain DeploymentPhaseResult values"
            )
        phase_names = [item.phase for item in self.phases]
        if len(phase_names) != len(set(phase_names)):
            raise ProjectDeploymentContractError(
                "deployment node result phases must be unique"
            )
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "phases", tuple(self.phases))
        object.__setattr__(
            self,
            "activation_ref",
            _optional_token(self.activation_ref, max_length=500),
        )
        object.__setattr__(self, "error", dict(self.error))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "state": self.state,
            "phases": [item.to_dict() for item in self.phases],
            "activation_ref": self.activation_ref,
            "error": dict(self.error),
            "uncertain": self.uncertain,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeploymentNodeResult":
        payload = _mapping(value, "DeploymentNodeResult")
        allowed = {"node_id", "state", "phases", "activation_ref", "error", "uncertain"}
        unknown = set(payload).difference(allowed)
        missing = allowed.difference(payload)
        if unknown or missing:
            detail = sorted(unknown or missing)
            problem = "unsupported" if unknown else "missing"
            raise ProjectDeploymentContractError(
                f"DeploymentNodeResult contains {problem} fields: {', '.join(detail)}"
            )
        if not isinstance(payload["uncertain"], bool):
            raise ProjectDeploymentContractError("uncertain must be a boolean")
        return cls(
            node_id=payload["node_id"],
            state=payload["state"],
            phases=tuple(
                DeploymentPhaseResult.from_mapping(item)
                for item in _mappings(payload["phases"], "phases", max_items=20)
            ),
            activation_ref=payload["activation_ref"],
            error=_optional_mapping(payload["error"], "error"),
            uncertain=payload["uncertain"],
        )


@dataclass(frozen=True, slots=True)
class DeploymentOperation:
    operation_id: str
    deployment_id: str
    plan_digest: str
    kind: str
    state: str
    expected_revision: int
    idempotency_key: str
    node_results: tuple[DeploymentNodeResult, ...] = ()
    error: Mapping[str, Any] = field(default_factory=dict)
    uncertain: bool = False
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "operation_id", _token(self.operation_id, "operation_id")
        )
        object.__setattr__(
            self, "deployment_id", _token(self.deployment_id, "deployment_id")
        )
        object.__setattr__(
            self, "plan_digest", _digest(self.plan_digest, "plan_digest")
        )
        kind = _token(self.kind, "operation kind", max_length=20).lower()
        if kind not in OPERATION_KINDS:
            raise ProjectDeploymentContractError("deployment operation kind is invalid")
        object.__setattr__(self, "kind", kind)
        state = _token(self.state, "operation state", max_length=20).lower()
        if state not in OPERATION_STATES:
            raise ProjectDeploymentContractError(
                "deployment operation state is invalid"
            )
        if not isinstance(self.uncertain, bool):
            raise ProjectDeploymentContractError("uncertain must be a boolean")
        if self.uncertain and state not in {"uncertain", "partial", "failed"}:
            raise ProjectDeploymentContractError(
                "uncertain operations must expose uncertain/partial/failed state"
            )
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self,
            "expected_revision",
            _integer(self.expected_revision, "expected_revision", minimum=1),
        )
        object.__setattr__(
            self, "idempotency_key", _token(self.idempotency_key, "idempotency_key")
        )
        if any(
            not isinstance(item, DeploymentNodeResult) for item in self.node_results
        ):
            raise ProjectDeploymentContractError(
                "node_results must contain DeploymentNodeResult values"
            )
        node_ids = [item.node_id for item in self.node_results]
        if len(node_ids) != len(set(node_ids)):
            raise ProjectDeploymentContractError(
                "deployment operation node results must be unique"
            )
        object.__setattr__(self, "node_results", tuple(self.node_results))
        object.__setattr__(self, "error", dict(self.error))
        object.__setattr__(
            self, "created_at", _timestamp(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "updated_at", _timestamp(self.updated_at, "updated_at")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DEPLOYMENT_OPERATION_SCHEMA,
            "operation_id": self.operation_id,
            "deployment_id": self.deployment_id,
            "plan_digest": self.plan_digest,
            "kind": self.kind,
            "state": self.state,
            "expected_revision": self.expected_revision,
            "idempotency_key": self.idempotency_key,
            "node_results": [item.to_dict() for item in self.node_results],
            "error": dict(self.error),
            "uncertain": bool(self.uncertain),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeploymentOperation":
        payload = _require_contract(
            value,
            schema=DEPLOYMENT_OPERATION_SCHEMA,
            field_name="DeploymentOperation",
            allowed={
                "schema",
                "operation_id",
                "deployment_id",
                "plan_digest",
                "kind",
                "state",
                "expected_revision",
                "idempotency_key",
                "node_results",
                "error",
                "uncertain",
                "created_at",
                "updated_at",
            },
            required={
                "schema",
                "operation_id",
                "deployment_id",
                "plan_digest",
                "kind",
                "state",
                "expected_revision",
                "idempotency_key",
                "node_results",
                "error",
                "uncertain",
                "created_at",
                "updated_at",
            },
        )
        if not isinstance(payload["uncertain"], bool):
            raise ProjectDeploymentContractError("uncertain must be a boolean")
        return cls(
            operation_id=payload["operation_id"],
            deployment_id=payload["deployment_id"],
            plan_digest=payload["plan_digest"],
            kind=payload["kind"],
            state=payload["state"],
            expected_revision=payload["expected_revision"],
            idempotency_key=payload["idempotency_key"],
            node_results=tuple(
                DeploymentNodeResult.from_mapping(item)
                for item in _mappings(payload["node_results"], "node_results")
            ),
            error=_optional_mapping(payload["error"], "error"),
            uncertain=payload["uncertain"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )


def inventory_revision(records: Iterable[NodeInventoryRecord]) -> str:
    payload = [
        item.to_dict() for item in sorted(records, key=lambda item: item.node_id)
    ]
    return canonical_payload_digest(payload)


__all__ = [
    "ACTIVATION_STATUSES",
    "COMPONENT_ACTIVATION_SCHEMA",
    "DEPLOYMENT_OPERATION_SCHEMA",
    "DEPLOYMENT_PLAN_SCHEMA",
    "DEPLOYMENT_REVISION_SCHEMA",
    "NODE_INVENTORY_SCHEMA",
    "PLACEMENT_MODES",
    "PROJECT_DEPLOYMENT_SCHEMA",
    "ComponentActivation",
    "ComponentPlacementPolicy",
    "DataRetentionPolicy",
    "DeploymentCompatibilityPolicy",
    "DeploymentNodeResult",
    "DeploymentOperation",
    "DeploymentPhaseResult",
    "DeploymentPlan",
    "DeploymentPlanChange",
    "DeploymentRevision",
    "NodeEndpointRecord",
    "NodeInventoryRecord",
    "ProjectDeployment",
    "ProjectDeploymentContractError",
    "RolloutPolicy",
    "inventory_revision",
    "utc_now",
]
