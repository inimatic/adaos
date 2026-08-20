from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Mapping, Protocol

import httpx

from adaos.domain.distributed_operations import TopologyPlan, TopologyPlanStep
from adaos.domain.distributed_runtime import Dataset, Partition, ServiceInstance

from .operations import (
    RetryableTopologyPhaseError,
    TopologyExecutionError,
    TopologyStepContext,
    UncertainTopologyPhaseError,
)
from .store import DistributedRuntimeStore


TOPOLOGY_PHASE_REQUEST_SCHEMA = "adaos.distributed.topology_phase_request.v1"
TOPOLOGY_PHASE_RESULT_SCHEMA = "adaos.distributed.topology_phase_result.v1"
MAX_TOPOLOGY_PHASE_BYTES = 256 * 1024
DEFAULT_TOPOLOGY_ADAPTER_TOOL = "distributed_topology_phase"

_receiver_lock = RLock()
_receiver_node_id = ""
_receiver_executor: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]] | None = None


class RemoteTopologyPhaseTransport(Protocol):
    def execute_phase(
        self, *, node_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


def _owner_skill(owner_ref: str) -> str:
    prefix, separator, skill_id = str(owner_ref).partition(":")
    if prefix != "skill" or separator != ":" or not skill_id.strip():
        raise TopologyExecutionError("dataset_owner_is_not_skill_adapter")
    return skill_id.strip()


def _selected_instance(
    phase: str,
    *,
    source: ServiceInstance | None,
    target: ServiceInstance | None,
) -> ServiceInstance:
    source_phases = {"snapshot", "stream_deltas", "demote", "drain", "remove"}
    selected = source if phase in source_phases else target or source
    if selected is None:
        raise TopologyExecutionError("topology_phase_has_no_participant")
    return selected


def _bounded_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TopologyExecutionError("topology_adapter_result_must_be_object")
    result = {str(key): item for key, item in value.items()}
    if len(str(result).encode("utf-8")) > MAX_TOPOLOGY_PHASE_BYTES:
        raise TopologyExecutionError("topology_adapter_result_too_large")
    if result.get("uncertain") is True:
        raise UncertainTopologyPhaseError(
            str(result.get("error_code") or "topology_adapter_outcome_uncertain")
        )
    if result.get("retryable") is True:
        raise RetryableTopologyPhaseError(
            str(result.get("error_code") or "topology_adapter_retryable_failure")
        )
    if result.get("ok") is not True:
        raise TopologyExecutionError(
            str(result.get("error_code") or "topology_adapter_phase_failed")
        )
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise TopologyExecutionError("topology_adapter_receipt_missing")
    return {str(key): item for key, item in receipt.items()}


@dataclass(slots=True)
class SkillToolTopologyAdapter:
    store: DistributedRuntimeStore
    local_node_id: str
    local_executor: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]
    remote: RemoteTopologyPhaseTransport

    def _call(self, context: TopologyStepContext) -> Mapping[str, Any]:
        step = context.step
        partition = self.store.get_partition(step.partition_id)
        dataset = self.store.get_dataset(partition.dataset_id)
        source = (
            self.store.get_instance(step.source_instance_id)
            if step.source_instance_id
            else None
        )
        target = (
            self.store.get_instance(step.target_instance_id)
            if step.target_instance_id
            else None
        )
        selected = _selected_instance(
            context.phase,
            source=source,
            target=target,
        )
        skill_id = _owner_skill(dataset.owner_ref)
        plan = self.store.get_plan(context.plan_digest)
        if plan.status != "ready" or not any(
            item == step for item in plan.steps
        ):
            raise TopologyExecutionError("topology_phase_plan_not_reviewed")
        if selected.component_ref != f"skill:{skill_id}":
            raise TopologyExecutionError("topology_adapter_component_owner_mismatch")
        adapter_tool = str(
            dataset.metadata.get("topology_adapter_tool")
            or DEFAULT_TOPOLOGY_ADAPTER_TOOL
        ).strip()
        if not adapter_tool or len(adapter_tool) > 200:
            raise TopologyExecutionError("topology_adapter_tool_invalid")
        payload = {
            "schema": TOPOLOGY_PHASE_REQUEST_SCHEMA,
            "requesting_node_id": self.local_node_id,
            "target_node_id": selected.node_id,
            "selected_instance_id": selected.instance_id,
            "skill_id": skill_id,
            "adapter_tool": adapter_tool,
            "operation_id": context.operation_id,
            "plan_digest": context.plan_digest,
            "plan": plan.to_dict(),
            "phase": context.phase,
            "authority_epoch": context.authority_epoch,
            "idempotency_key": context.idempotency_key,
            "attempt": context.attempt,
            "step": step.to_dict(),
            "dataset": dataset.to_dict(),
            "partition": partition.to_dict(),
            "source_instance": source.to_dict() if source is not None else None,
            "target_instance": target.to_dict() if target is not None else None,
        }
        if len(str(payload).encode("utf-8")) > MAX_TOPOLOGY_PHASE_BYTES:
            raise TopologyExecutionError("topology_phase_request_too_large")
        if selected.node_id == self.local_node_id:
            result = self.local_executor(skill_id, adapter_tool, payload)
        else:
            result = self.remote.execute_phase(node_id=selected.node_id, payload=payload)
        return _bounded_result(result)

    inspect = reserve = prepare = snapshot = stream_deltas = catch_up = _call
    verify = activate_read = promote = demote = drain = remove = route = release = _call


@dataclass(slots=True)
class HttpTopologyPhaseTransport:
    endpoint_resolver: Callable[[str], str]
    token_provider: Callable[[], str]
    source_node_id: str
    connect_timeout_seconds: float = 10.0
    operation_timeout_seconds: float = 600.0

    def execute_phase(
        self, *, node_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        endpoint = str(self.endpoint_resolver(node_id) or "").strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise RetryableTopologyPhaseError("remote_topology_endpoint_unavailable")
        headers = {
            "X-AdaOS-Token": str(self.token_provider() or ""),
            "X-AdaOS-Source-Node": self.source_node_id,
            "X-AdaOS-Operation-Id": str(payload.get("idempotency_key") or ""),
        }
        timeout = httpx.Timeout(
            timeout=max(30.0, float(self.operation_timeout_seconds)),
            connect=max(1.0, float(self.connect_timeout_seconds)),
        )
        try:
            response = httpx.post(
                f"{endpoint}/api/node/distributed-topology/phase",
                json=dict(payload),
                headers=headers,
                timeout=timeout,
            )
        except httpx.ConnectError as exc:
            raise RetryableTopologyPhaseError("remote_topology_connect_failed") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            raise UncertainTopologyPhaseError(
                "remote_topology_ack_timeout"
            ) from exc
        except httpx.RequestError as exc:
            raise RetryableTopologyPhaseError("remote_topology_transport_failed") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise TopologyExecutionError("remote_topology_response_invalid") from exc
        if response.status_code in {429, 502, 503, 504}:
            raise RetryableTopologyPhaseError(
                str(body.get("detail") or "remote_topology_busy")
            )
        if response.status_code >= 400:
            raise TopologyExecutionError(
                str(body.get("detail") or f"remote_topology_http_{response.status_code}")
            )
        if not isinstance(body, Mapping):
            raise TopologyExecutionError("remote_topology_response_invalid")
        if body.get("schema") != TOPOLOGY_PHASE_RESULT_SCHEMA:
            raise TopologyExecutionError("remote_topology_response_schema_invalid")
        return body


@dataclass(slots=True)
class MemberLinkTopologyPhaseTransport:
    rpc_call: Callable[..., Any]
    operation_timeout_seconds: float = 600.0

    def execute_phase(
        self, *, node_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            body = self.rpc_call(
                node_id,
                method="distributed.topology.phase",
                params=dict(payload),
                timeout=max(30.0, float(self.operation_timeout_seconds)),
            )
        except TimeoutError as exc:
            raise UncertainTopologyPhaseError("remote_topology_ack_timeout") from exc
        except ConnectionError as exc:
            raise RetryableTopologyPhaseError("remote_topology_member_link_unavailable") from exc
        except RuntimeError as exc:
            reason = str(exc)
            if any(token in reason for token in ("member_not_connected", "member_rpc_busy", "link_replaced")):
                raise RetryableTopologyPhaseError(reason) from exc
            raise TopologyExecutionError(reason) from exc
        if not isinstance(body, Mapping):
            raise TopologyExecutionError("remote_topology_response_invalid")
        if body.get("schema") != TOPOLOGY_PHASE_RESULT_SCHEMA:
            raise TopologyExecutionError("remote_topology_response_schema_invalid")
        return dict(body)


def execute_topology_phase_request(
    payload: Mapping[str, Any],
    *,
    local_node_id: str,
    executor: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    fields = {
        "schema",
        "requesting_node_id",
        "target_node_id",
        "selected_instance_id",
        "skill_id",
        "adapter_tool",
        "operation_id",
        "plan_digest",
        "plan",
        "phase",
        "authority_epoch",
        "idempotency_key",
        "attempt",
        "step",
        "dataset",
        "partition",
        "source_instance",
        "target_instance",
    }
    if set(payload) != fields or payload.get("schema") != TOPOLOGY_PHASE_REQUEST_SCHEMA:
        raise TopologyExecutionError("topology_phase_request_schema_invalid")
    if str(payload.get("target_node_id") or "") != local_node_id:
        raise TopologyExecutionError("topology_phase_target_node_mismatch")
    step = TopologyPlanStep.from_mapping(payload["step"])
    plan = TopologyPlan.from_mapping(payload["plan"])
    dataset = Dataset.from_mapping(payload["dataset"])
    partition = Partition.from_mapping(payload["partition"])
    source = (
        ServiceInstance.from_mapping(payload["source_instance"])
        if isinstance(payload.get("source_instance"), Mapping)
        else None
    )
    target = (
        ServiceInstance.from_mapping(payload["target_instance"])
        if isinstance(payload.get("target_instance"), Mapping)
        else None
    )
    if partition.partition_id != step.partition_id or partition.dataset_id != dataset.dataset_id:
        raise TopologyExecutionError("topology_phase_resource_identity_mismatch")
    if plan.plan_digest != str(payload.get("plan_digest") or ""):
        raise TopologyExecutionError("topology_phase_plan_digest_mismatch")
    if plan.status != "ready" or not any(item == step for item in plan.steps):
        raise TopologyExecutionError("topology_phase_plan_not_reviewed")
    selected = _selected_instance(str(payload.get("phase") or ""), source=source, target=target)
    if selected.node_id != local_node_id:
        raise TopologyExecutionError("topology_phase_participant_node_mismatch")
    if selected.instance_id != str(payload.get("selected_instance_id") or ""):
        raise TopologyExecutionError("topology_phase_instance_identity_mismatch")
    skill_id = _owner_skill(dataset.owner_ref)
    if skill_id != str(payload.get("skill_id") or ""):
        raise TopologyExecutionError("topology_phase_skill_identity_mismatch")
    if selected.component_ref != f"skill:{skill_id}":
        raise TopologyExecutionError("topology_phase_component_identity_mismatch")
    expected_tool = str(
        dataset.metadata.get("topology_adapter_tool")
        or DEFAULT_TOPOLOGY_ADAPTER_TOOL
    ).strip()
    if str(payload.get("adapter_tool") or "") != expected_tool:
        raise TopologyExecutionError("topology_phase_adapter_tool_mismatch")
    if str(payload.get("phase") or "") not in step.phases:
        raise TopologyExecutionError("topology_phase_not_in_reviewed_plan")
    expected_epoch = plan.authority_epoch + (
        1 if plan.kind == "handoff" and str(payload.get("phase") or "") in {"promote", "route", "demote"} else 0
    )
    if (
        int(payload.get("authority_epoch") or 0) != partition.authority_epoch
        or expected_epoch != partition.authority_epoch
    ):
        raise TopologyExecutionError("topology_phase_authority_epoch_mismatch")
    result = executor(skill_id, str(payload.get("adapter_tool") or ""), dict(payload))
    receipt = _bounded_result(result)
    return {
        "schema": TOPOLOGY_PHASE_RESULT_SCHEMA,
        "ok": True,
        "receipt": receipt,
    }


def register_topology_phase_receiver(
    executor: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]] | None,
    *,
    node_id: str = "",
) -> None:
    global _receiver_executor, _receiver_node_id
    with _receiver_lock:
        _receiver_executor = executor
        _receiver_node_id = str(node_id or "").strip()


def execute_registered_topology_phase(payload: Mapping[str, Any]) -> dict[str, Any]:
    with _receiver_lock:
        executor = _receiver_executor
        node_id = _receiver_node_id
    if executor is None or not node_id:
        raise TopologyExecutionError("topology_phase_receiver_not_configured")
    return execute_topology_phase_request(
        payload,
        local_node_id=node_id,
        executor=executor,
    )


__all__ = [
    "DEFAULT_TOPOLOGY_ADAPTER_TOOL",
    "HttpTopologyPhaseTransport",
    "MemberLinkTopologyPhaseTransport",
    "MAX_TOPOLOGY_PHASE_BYTES",
    "RemoteTopologyPhaseTransport",
    "SkillToolTopologyAdapter",
    "TOPOLOGY_PHASE_REQUEST_SCHEMA",
    "TOPOLOGY_PHASE_RESULT_SCHEMA",
    "execute_registered_topology_phase",
    "execute_topology_phase_request",
    "register_topology_phase_receiver",
]
