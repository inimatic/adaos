from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.distributed_runtime import DistributedContractError, utc_now


TOPOLOGY_PLAN_SCHEMA = "adaos.distributed.topology_plan.v1"
TOPOLOGY_PHASES = {
    "inspect",
    "reserve",
    "prepare",
    "snapshot",
    "stream_deltas",
    "catch_up",
    "verify",
    "activate_read",
    "promote",
    "demote",
    "drain",
    "remove",
    "route",
    "release",
}


def _text(value: Any, field_name: str, *, maximum: int = 500) -> str:
    result = str(value or "").strip()
    if not result:
        raise DistributedContractError(f"{field_name} is required")
    if len(result) > maximum:
        raise DistributedContractError(f"{field_name} exceeds {maximum} characters")
    return result


def _optional_text(value: Any, field_name: str) -> str | None:
    return None if value is None else _text(value, field_name)


def _integer(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise DistributedContractError(f"{field_name} must be an integer >= {minimum}")
    return value


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DistributedContractError(f"{field_name} must be an object")
    return {str(key): item for key, item in value.items()}


def _strict(
    value: Mapping[str, Any], fields: set[str], field_name: str
) -> dict[str, Any]:
    payload = _mapping(value, field_name)
    unknown = set(payload) - fields
    missing = fields - set(payload)
    if unknown or missing:
        detail = sorted(unknown or missing)
        reason = "unsupported" if unknown else "missing"
        raise DistributedContractError(
            f"{field_name} contains {reason} fields: {', '.join(detail)}"
        )
    return payload


@dataclass(frozen=True, slots=True)
class TopologyPlanStep:
    step_id: str
    action: str
    partition_id: str
    source_instance_id: str | None
    target_instance_id: str | None
    replica_role: str
    phases: tuple[str, ...]
    expected_bytes: int | None = None
    temporary_bytes: int = 0
    availability_impact: str = "none"
    retention: str = "retain"
    adapter_options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _text(self.step_id, "step_id"))
        action = _text(self.action, "action", maximum=30).lower()
        if action not in {
            "create",
            "move",
            "rebuild",
            "handoff",
            "drain",
            "remove",
            "repair",
        }:
            raise DistributedContractError("topology plan action is invalid")
        object.__setattr__(self, "action", action)
        object.__setattr__(
            self, "partition_id", _text(self.partition_id, "partition_id")
        )
        object.__setattr__(
            self,
            "source_instance_id",
            _optional_text(self.source_instance_id, "source_instance_id"),
        )
        object.__setattr__(
            self,
            "target_instance_id",
            _optional_text(self.target_instance_id, "target_instance_id"),
        )
        role = _text(self.replica_role, "replica_role", maximum=30).lower()
        if role not in {"authority", "follower", "derived", "cache"}:
            raise DistributedContractError("topology plan replica_role is invalid")
        object.__setattr__(self, "replica_role", role)
        phases = tuple(
            dict.fromkeys(_text(item, "phase", maximum=30) for item in self.phases)
        )
        if not phases or any(item not in TOPOLOGY_PHASES for item in phases):
            raise DistributedContractError("topology plan phases are invalid")
        object.__setattr__(self, "phases", phases)
        if self.expected_bytes is not None:
            object.__setattr__(
                self,
                "expected_bytes",
                _integer(self.expected_bytes, "expected_bytes"),
            )
        object.__setattr__(
            self, "temporary_bytes", _integer(self.temporary_bytes, "temporary_bytes")
        )
        impact = _text(
            self.availability_impact, "availability_impact", maximum=40
        ).lower()
        if impact not in {
            "none",
            "reduced_capacity",
            "read_only",
            "temporary_unavailable",
        }:
            raise DistributedContractError("topology availability_impact is invalid")
        object.__setattr__(self, "availability_impact", impact)
        retention = _text(self.retention, "retention", maximum=30).lower()
        if retention not in {"retain", "delete", "rebuild"}:
            raise DistributedContractError("topology retention is invalid")
        object.__setattr__(self, "retention", retention)
        options = _mapping(self.adapter_options, "adapter_options")
        if len(str(options).encode("utf-8")) > 32_768:
            raise DistributedContractError("adapter_options exceeds 32 KiB")
        object.__setattr__(self, "adapter_options", options)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "partition_id": self.partition_id,
            "source_instance_id": self.source_instance_id,
            "target_instance_id": self.target_instance_id,
            "replica_role": self.replica_role,
            "phases": list(self.phases),
            "expected_bytes": self.expected_bytes,
            "temporary_bytes": self.temporary_bytes,
            "availability_impact": self.availability_impact,
            "retention": self.retention,
            "adapter_options": dict(self.adapter_options),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TopologyPlanStep":
        fields = {
            "step_id",
            "action",
            "partition_id",
            "source_instance_id",
            "target_instance_id",
            "replica_role",
            "phases",
            "expected_bytes",
            "temporary_bytes",
            "availability_impact",
            "retention",
            "adapter_options",
        }
        payload = _strict(value, fields, "TopologyPlanStep")
        raw_phases = payload["phases"]
        if not isinstance(raw_phases, list):
            raise DistributedContractError("phases must be a list")
        return cls(
            step_id=payload["step_id"],
            action=payload["action"],
            partition_id=payload["partition_id"],
            source_instance_id=payload["source_instance_id"],
            target_instance_id=payload["target_instance_id"],
            replica_role=payload["replica_role"],
            phases=tuple(raw_phases),
            expected_bytes=payload["expected_bytes"],
            temporary_bytes=payload["temporary_bytes"],
            availability_impact=payload["availability_impact"],
            retention=payload["retention"],
            adapter_options=_mapping(payload["adapter_options"], "adapter_options"),
        )


@dataclass(frozen=True, slots=True)
class TopologyPlan:
    plan_id: str
    kind: str
    target_ref: str
    expected_desired_revision: int
    expected_observed_revision: int
    authority_epoch: int
    steps: tuple[TopologyPlanStep, ...]
    required_approvals: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    status: str = "ready"
    created_at: str = field(default_factory=utc_now)
    plan_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id"))
        kind = _text(self.kind, "kind", maximum=30).lower()
        if kind not in {
            "reconcile",
            "replicate",
            "handoff",
            "drain",
            "remove",
            "repair",
        }:
            raise DistributedContractError("topology plan kind is invalid")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "target_ref", _text(self.target_ref, "target_ref"))
        object.__setattr__(
            self,
            "expected_desired_revision",
            _integer(self.expected_desired_revision, "expected_desired_revision"),
        )
        object.__setattr__(
            self,
            "expected_observed_revision",
            _integer(self.expected_observed_revision, "expected_observed_revision"),
        )
        object.__setattr__(
            self, "authority_epoch", _integer(self.authority_epoch, "authority_epoch")
        )
        if not self.steps or any(
            not isinstance(item, TopologyPlanStep) for item in self.steps
        ):
            raise DistributedContractError("topology plan requires typed steps")
        step_ids = [item.step_id for item in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise DistributedContractError("topology plan step ids must be unique")
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(
            self,
            "required_approvals",
            tuple(
                sorted({_text(item, "approval") for item in self.required_approvals})
            ),
        )
        object.__setattr__(
            self,
            "warnings",
            tuple(
                dict.fromkeys(
                    _text(item, "warning", maximum=1000) for item in self.warnings
                )
            ),
        )
        status = _text(self.status, "status", maximum=30).lower()
        if status not in {"ready", "blocked", "applied", "superseded", "failed"}:
            raise DistributedContractError("topology plan status is invalid")
        object.__setattr__(self, "status", status)
        _text(self.created_at, "created_at", maximum=80)
        payload = self._unsigned_dict()
        expected_digest = canonical_payload_digest(payload)
        if self.plan_digest is not None and self.plan_digest != expected_digest:
            raise DistributedContractError("topology plan digest mismatch")
        object.__setattr__(self, "plan_digest", expected_digest)

    def _unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema": TOPOLOGY_PLAN_SCHEMA,
            "plan_id": self.plan_id,
            "kind": self.kind,
            "target_ref": self.target_ref,
            "expected_desired_revision": self.expected_desired_revision,
            "expected_observed_revision": self.expected_observed_revision,
            "authority_epoch": self.authority_epoch,
            "steps": [item.to_dict() for item in self.steps],
            "required_approvals": list(self.required_approvals),
            "warnings": list(self.warnings),
            "status": self.status,
            "created_at": self.created_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned_dict(), "plan_digest": self.plan_digest}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TopologyPlan":
        fields = {
            "schema",
            "plan_id",
            "kind",
            "target_ref",
            "expected_desired_revision",
            "expected_observed_revision",
            "authority_epoch",
            "steps",
            "required_approvals",
            "warnings",
            "status",
            "created_at",
            "plan_digest",
        }
        payload = _strict(value, fields, "TopologyPlan")
        if payload["schema"] != TOPOLOGY_PLAN_SCHEMA:
            raise DistributedContractError("unsupported topology plan schema")
        for field_name in ("steps", "required_approvals", "warnings"):
            if not isinstance(payload[field_name], list):
                raise DistributedContractError(f"{field_name} must be a list")
        return cls(
            plan_id=payload["plan_id"],
            kind=payload["kind"],
            target_ref=payload["target_ref"],
            expected_desired_revision=payload["expected_desired_revision"],
            expected_observed_revision=payload["expected_observed_revision"],
            authority_epoch=payload["authority_epoch"],
            steps=tuple(
                TopologyPlanStep.from_mapping(item) for item in payload["steps"]
            ),
            required_approvals=tuple(payload["required_approvals"]),
            warnings=tuple(payload["warnings"]),
            status=payload["status"],
            created_at=payload["created_at"],
            plan_digest=payload["plan_digest"],
        )


__all__ = [
    "TOPOLOGY_PHASES",
    "TOPOLOGY_PLAN_SCHEMA",
    "TopologyPlan",
    "TopologyPlanStep",
]
