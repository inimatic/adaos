from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any, Callable, Mapping, Protocol

import httpx

from adaos.domain.distributed_operations import TopologyPlan, TopologyPlanStep
from adaos.domain.distributed_runtime import (
    Dataset,
    Partition,
    Replica,
    ServiceInstance,
    TransferRecord,
    utc_now,
)

from .operations import (
    RetryableTopologyPhaseError,
    TopologyExecutionError,
    TopologyStepContext,
    UncertainTopologyPhaseError,
)
from .store import DistributedRuntimeStore
from .transfer import (
    AuthenticatedTransferSink,
    AuthenticatedTransferSource,
    BoundedTransferController,
    TransferChunk,
    TransferTransportError,
)


TOPOLOGY_PHASE_REQUEST_SCHEMA = "adaos.distributed.topology_phase_request.v1"
TOPOLOGY_PHASE_RESULT_SCHEMA = "adaos.distributed.topology_phase_result.v1"
TOPOLOGY_TRANSFER_REQUEST_SCHEMA = "adaos.distributed.topology_transfer_request.v1"
TOPOLOGY_TRANSFER_RESULT_SCHEMA = "adaos.distributed.topology_transfer_result.v1"
MAX_TOPOLOGY_PHASE_BYTES = 256 * 1024
MAX_TOPOLOGY_TRANSFER_BYTES = 256 * 1024
MAX_TOPOLOGY_TRANSFER_CHUNK_BYTES = 96 * 1024
DEFAULT_TOPOLOGY_ADAPTER_TOOL = "distributed_topology_phase"
DEFAULT_TOPOLOGY_TRANSFER_TOOL = "distributed_topology_transfer"

_receiver_lock = RLock()
_receiver_node_id = ""
_receiver_executor: (
    Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]] | None
) = None


class RemoteTopologyPhaseTransport(Protocol):
    def execute_phase(
        self, *, node_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def execute_transfer(
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


def _bounded_transfer_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TransferTransportError("topology_transfer_result_must_be_object")
    result = {str(key): item for key, item in value.items()}
    if len(str(result).encode("utf-8")) > MAX_TOPOLOGY_TRANSFER_BYTES:
        raise TransferTransportError("topology_transfer_result_too_large")
    if result.get("uncertain") is True:
        raise UncertainTopologyPhaseError(
            str(result.get("error_code") or "topology_transfer_outcome_uncertain")
        )
    if result.get("retryable") is True:
        raise RetryableTopologyPhaseError(
            str(result.get("error_code") or "topology_transfer_retryable_failure")
        )
    if result.get("ok") is not True:
        raise TransferTransportError(
            str(result.get("error_code") or "topology_transfer_failed")
        )
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise TransferTransportError("topology_transfer_receipt_missing")
    return {str(key): item for key, item in receipt.items()}


@dataclass(slots=True)
class _ToolTransferSource(AuthenticatedTransferSource):
    operation_id: str
    call: Callable[[str | None, int], Mapping[str, Any]]

    def authorize(self, *, auth_scope: str, operation_id: str) -> bool:
        return (
            auth_scope == "distributed.replica.transfer"
            and operation_id == self.operation_id
        )

    def read(
        self,
        *,
        checkpoint: str | None,
        max_bytes: int,
        cancelled: Callable[[], bool],
    ) -> TransferChunk:
        if cancelled():
            raise TransferTransportError("topology_transfer_cancelled")
        receipt = dict(self.call(checkpoint, max_bytes))
        try:
            payload = base64.b64decode(
                str(receipt.get("payload_base64") or ""), validate=True
            )
        except Exception as exc:
            raise TransferTransportError("topology_transfer_chunk_invalid") from exc
        if len(payload) > max_bytes or len(payload) > MAX_TOPOLOGY_TRANSFER_CHUNK_BYTES:
            raise TransferTransportError("topology_transfer_chunk_too_large")
        next_checkpoint = str(receipt.get("checkpoint") or "").strip()
        if not next_checkpoint:
            raise TransferTransportError("topology_transfer_checkpoint_missing")
        eof = bool(receipt.get("eof"))
        witness = str(receipt.get("content_witness") or "").strip() or None
        if eof and not witness:
            raise TransferTransportError("topology_transfer_witness_missing")
        return TransferChunk(
            payload=payload,
            checkpoint=next_checkpoint,
            eof=eof,
            content_witness=witness,
        )


@dataclass(slots=True)
class _ToolTransferSink(AuthenticatedTransferSink):
    operation_id: str
    call: Callable[[str | None, TransferChunk], Mapping[str, Any]]

    def authorize(self, *, auth_scope: str, operation_id: str) -> bool:
        return (
            auth_scope == "distributed.replica.transfer"
            and operation_id == self.operation_id
        )

    def write(
        self,
        *,
        previous_checkpoint: str | None,
        chunk: TransferChunk,
        cancelled: Callable[[], bool],
    ) -> str | None:
        if cancelled():
            raise TransferTransportError("topology_transfer_cancelled")
        receipt = dict(self.call(previous_checkpoint, chunk))
        if str(receipt.get("checkpoint") or "") != chunk.checkpoint:
            raise TransferTransportError("topology_transfer_sink_checkpoint_mismatch")
        witness = str(receipt.get("content_witness") or "").strip() or None
        if chunk.eof and not witness:
            raise TransferTransportError("topology_transfer_sink_witness_missing")
        return witness


@dataclass(slots=True)
class SkillToolTopologyAdapter:
    store: DistributedRuntimeStore
    local_node_id: str
    local_executor: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]
    remote: RemoteTopologyPhaseTransport
    pressure_probe: Callable[[TopologyPlanStep], float] | None = None
    pressure_limit: float = 0.9

    def _replica_for_instance(
        self, partition_id: str, instance: ServiceInstance | None
    ) -> Replica | None:
        if instance is None:
            return None
        replicas, cursor = self.store.list_replicas(
            partition_id=partition_id, limit=200
        )
        if cursor is not None:
            raise TopologyExecutionError("topology_partition_replica_limit_exceeded")
        return next(
            (item for item in replicas if item.instance_id == instance.instance_id),
            None,
        )

    def _commit_replica_receipt(
        self,
        receipt: Mapping[str, Any],
        *,
        partition: Partition,
        selected: ServiceInstance,
        context: TopologyStepContext,
    ) -> dict[str, Any]:
        committed = dict(receipt)
        raw_replica = committed.get("replica")
        if not isinstance(raw_replica, Mapping):
            return committed
        try:
            replica = Replica.from_mapping(raw_replica)
        except Exception as exc:
            raise TopologyExecutionError("topology_replica_receipt_invalid") from exc
        if (
            replica.partition_id != partition.partition_id
            or replica.instance_id != selected.instance_id
            or replica.node_id != selected.node_id
        ):
            raise TopologyExecutionError("topology_replica_receipt_identity_mismatch")
        if (
            replica.role == "authority"
            and replica.authority_epoch != context.authority_epoch
        ):
            raise TopologyExecutionError("topology_replica_receipt_epoch_mismatch")
        try:
            previous = self.store.get_replica(replica.replica_id)
        except FileNotFoundError:
            previous = None
        revision = 0 if previous is None else previous.revision
        replica = replace(replica, revision=revision + 1)
        if previous is not None:
            previous_value = previous.to_dict()
            candidate_value = replica.to_dict()
            for field in ("observed_at", "revision"):
                previous_value.pop(field, None)
                candidate_value.pop(field, None)
            if previous_value == candidate_value:
                committed["replica"] = previous.to_dict()
                return committed
        saved = self.store.put_replica(replica, expected_revision=revision)
        committed["replica"] = saved.to_dict()
        return committed

    def _phase_inputs(self, context: TopologyStepContext) -> dict[str, Any]:
        get_operation = getattr(self.store, "get_operation", None)
        if not callable(get_operation):
            return {}
        try:
            operation = get_operation(context.operation_id)
        except FileNotFoundError:
            return {}
        prefix = f"{context.step.step_id}."
        for result in reversed(operation.phases):
            if not result.phase.startswith(prefix):
                continue
            inline_snapshot = result.receipt.get("inline_snapshot")
            if isinstance(inline_snapshot, Mapping):
                return {"source_snapshot": dict(inline_snapshot)}
            transfer_manifest = result.receipt.get("transfer_manifest")
            if isinstance(transfer_manifest, Mapping):
                return {"source_transfer": dict(transfer_manifest)}
        return {}

    def _execute_transfer_tool(
        self,
        *,
        instance: ServiceInstance,
        skill_id: str,
        transfer_tool: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if len(str(payload).encode("utf-8")) > MAX_TOPOLOGY_TRANSFER_BYTES:
            raise TransferTransportError("topology_transfer_request_too_large")
        if instance.node_id == self.local_node_id:
            result = self.local_executor(skill_id, transfer_tool, payload)
        else:
            execute_transfer = getattr(self.remote, "execute_transfer", None)
            if not callable(execute_transfer):
                raise TransferTransportError("topology_transfer_transport_unavailable")
            result = execute_transfer(node_id=instance.node_id, payload=payload)
        return _bounded_transfer_result(result)

    def _ensure_transfer(
        self,
        context: TopologyStepContext,
        *,
        manifest: Mapping[str, Any],
        plan: TopologyPlan,
        dataset: Dataset,
        partition: Partition,
        source: ServiceInstance,
        target: ServiceInstance,
        skill_id: str,
    ) -> dict[str, Any]:
        if manifest.get("schema") != "adaos.distributed.transfer_manifest.v1":
            raise TransferTransportError("topology_transfer_manifest_invalid")
        manifest_digest = str(manifest.get("payload_digest") or "").strip()
        if not (
            manifest_digest.startswith("sha256:")
            and len(manifest_digest) == len("sha256:") + 64
        ):
            raise TransferTransportError("topology_transfer_manifest_digest_invalid")
        expected_bytes = max(0, int(manifest.get("payload_bytes") or 0))
        expected_items = max(0, int(manifest.get("item_count") or 0))
        transfer_seed = (
            f"{context.operation_id}:{context.step.step_id}:{manifest_digest}"
        ).encode("utf-8")
        transfer_id = "transfer-" + hashlib.sha256(transfer_seed).hexdigest()[:28]
        try:
            transfer = self.store.get_transfer(transfer_id)
        except FileNotFoundError:
            now = utc_now()
            transfer = self.store.put_transfer(
                TransferRecord(
                    transfer_id=transfer_id,
                    operation_id=context.operation_id,
                    partition_id=partition.partition_id,
                    source_instance_id=source.instance_id,
                    target_instance_id=target.instance_id,
                    authority_epoch=context.authority_epoch,
                    state="preparing",
                    checkpoint=None,
                    manifest_digest=manifest_digest,
                    item_count=0,
                    byte_count=0,
                    resume_token_ref=None,
                    started_at=now,
                    updated_at=now,
                )
            )
        if transfer.state == "complete":
            if (
                transfer.byte_count != expected_bytes
                or transfer.item_count != expected_items
            ):
                raise TransferTransportError(
                    "topology_transfer_manifest_count_mismatch"
                )
            return transfer.to_dict()
        if transfer.state not in {"preparing", "transferring"}:
            raise TransferTransportError("topology_transfer_not_resumable")

        transfer_tool = str(
            dataset.metadata.get("topology_transfer_tool")
            or DEFAULT_TOPOLOGY_TRANSFER_TOOL
        ).strip()
        if not transfer_tool or len(transfer_tool) > 200:
            raise TransferTransportError("topology_transfer_tool_invalid")

        def request(
            *,
            instance: ServiceInstance,
            direction: str,
            checkpoint: str | None,
            max_bytes: int,
            chunk: Mapping[str, Any] | None,
        ) -> dict[str, Any]:
            payload = {
                "schema": TOPOLOGY_TRANSFER_REQUEST_SCHEMA,
                "requesting_node_id": self.local_node_id,
                "target_node_id": instance.node_id,
                "selected_instance_id": instance.instance_id,
                "skill_id": skill_id,
                "transfer_tool": transfer_tool,
                "direction": direction,
                "operation_id": context.operation_id,
                "plan_digest": context.plan_digest,
                "plan": plan.to_dict(),
                "step": context.step.to_dict(),
                "dataset": dataset.to_dict(),
                "partition": partition.to_dict(),
                "source_instance": source.to_dict(),
                "target_instance": target.to_dict(),
                "authority_epoch": context.authority_epoch,
                "idempotency_key": (
                    f"{context.idempotency_key}:transfer:{direction}:"
                    f"{checkpoint or 'start'}"
                ),
                "transfer_id": transfer_id,
                "manifest": dict(manifest),
                "checkpoint": checkpoint,
                "max_bytes": max_bytes,
                "chunk": dict(chunk) if chunk is not None else None,
            }
            return self._execute_transfer_tool(
                instance=instance,
                skill_id=skill_id,
                transfer_tool=transfer_tool,
                payload=payload,
            )

        source_adapter = _ToolTransferSource(
            operation_id=context.operation_id,
            call=lambda checkpoint, max_bytes: request(
                instance=source,
                direction="read",
                checkpoint=checkpoint,
                max_bytes=max_bytes,
                chunk=None,
            ),
        )
        sink_adapter = _ToolTransferSink(
            operation_id=context.operation_id,
            call=lambda checkpoint, chunk: request(
                instance=target,
                direction="write",
                checkpoint=checkpoint,
                max_bytes=MAX_TOPOLOGY_TRANSFER_CHUNK_BYTES,
                chunk={
                    "payload_base64": base64.b64encode(chunk.payload).decode("ascii"),
                    "checkpoint": chunk.checkpoint,
                    "eof": chunk.eof,
                    "content_witness": chunk.content_witness,
                },
            ),
        )
        controller = BoundedTransferController(
            store=self.store,
            max_chunk_bytes=MAX_TOPOLOGY_TRANSFER_CHUNK_BYTES,
            pressure_probe=(
                (lambda: float(self.pressure_probe(context.step)))
                if self.pressure_probe is not None
                else None
            ),
            pressure_limit=self.pressure_limit,
        )
        for _ in range(8):
            transfer = controller.pump(
                transfer_id,
                source=source_adapter,
                sink=sink_adapter,
                auth_scope="distributed.replica.transfer",
                max_chunks=100,
                expected_item_count=expected_items,
            )
            if transfer.state == "complete":
                break
        if transfer.state != "complete":
            raise RetryableTopologyPhaseError("topology_transfer_incomplete")
        if (
            transfer.byte_count != expected_bytes
            or transfer.item_count != expected_items
        ):
            raise TransferTransportError("topology_transfer_manifest_count_mismatch")
        return transfer.to_dict()

    @staticmethod
    def _validate_target_witness(
        receipt: Mapping[str, Any],
        *,
        phase: str,
        source: Replica | None,
        selected: ServiceInstance,
    ) -> None:
        if phase not in {"catch_up", "verify", "activate_read", "promote"}:
            return
        if source is None or source.instance_id == selected.instance_id:
            return

        raw_replica = receipt.get("replica")
        observed = (
            dict(raw_replica) if isinstance(raw_replica, Mapping) else dict(receipt)
        )
        expected_checkpoint = str(source.checkpoint or "").strip()
        observed_checkpoint = str(
            observed.get("checkpoint") or observed.get("content_witness") or ""
        ).strip()
        expected_items = int(source.item_count or 0)
        observed_items = int(observed.get("item_count") or 0)
        observed_state = str(observed.get("content_state") or "").strip().lower()

        if expected_checkpoint and observed_checkpoint != expected_checkpoint:
            raise TopologyExecutionError("topology_target_content_witness_mismatch")
        if expected_items > observed_items:
            raise TopologyExecutionError("topology_target_content_incomplete")
        if source.content_state == "non_empty" and (
            observed_items <= 0 or observed_state == "empty"
        ):
            raise TopologyExecutionError("topology_target_content_incomplete")

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
        if plan.status != "ready" or not any(item == step for item in plan.steps):
            raise TopologyExecutionError("topology_phase_plan_not_reviewed")
        if selected.component_ref != f"skill:{skill_id}":
            raise TopologyExecutionError("topology_adapter_component_owner_mismatch")
        adapter_tool = str(
            dataset.metadata.get("topology_adapter_tool")
            or DEFAULT_TOPOLOGY_ADAPTER_TOOL
        ).strip()
        if not adapter_tool or len(adapter_tool) > 200:
            raise TopologyExecutionError("topology_adapter_tool_invalid")
        phase_inputs = self._phase_inputs(context)
        source_transfer = phase_inputs.get("source_transfer")
        if context.phase == "catch_up" and isinstance(source_transfer, Mapping):
            if source is None or target is None:
                raise TopologyExecutionError("topology_transfer_participant_missing")
            phase_inputs["transfer_receipt"] = self._ensure_transfer(
                context,
                manifest=source_transfer,
                plan=plan,
                dataset=dataset,
                partition=partition,
                source=source,
                target=target,
                skill_id=skill_id,
            )
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
            "source_replica": (
                replica.to_dict()
                if (
                    replica := self._replica_for_instance(
                        partition.partition_id, source
                    )
                )
                is not None
                else None
            ),
            "target_replica": (
                replica.to_dict()
                if (
                    replica := self._replica_for_instance(
                        partition.partition_id, target
                    )
                )
                is not None
                else None
            ),
            "phase_inputs": phase_inputs,
        }
        if len(str(payload).encode("utf-8")) > MAX_TOPOLOGY_PHASE_BYTES:
            raise TopologyExecutionError("topology_phase_request_too_large")
        if selected.node_id == self.local_node_id:
            result = self.local_executor(skill_id, adapter_tool, payload)
        else:
            result = self.remote.execute_phase(
                node_id=selected.node_id, payload=payload
            )
        receipt = _bounded_result(result)
        source_replica = self._replica_for_instance(partition.partition_id, source)
        self._validate_target_witness(
            receipt,
            phase=context.phase,
            source=source_replica,
            selected=selected,
        )
        return self._commit_replica_receipt(
            receipt,
            partition=partition,
            selected=selected,
            context=context,
        )

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
            raise UncertainTopologyPhaseError("remote_topology_ack_timeout") from exc
        except httpx.RequestError as exc:
            raise RetryableTopologyPhaseError(
                "remote_topology_transport_failed"
            ) from exc
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
                str(
                    body.get("detail") or f"remote_topology_http_{response.status_code}"
                )
            )
        if not isinstance(body, Mapping):
            raise TopologyExecutionError("remote_topology_response_invalid")
        if body.get("schema") != TOPOLOGY_PHASE_RESULT_SCHEMA:
            raise TopologyExecutionError("remote_topology_response_schema_invalid")
        return body

    def execute_transfer(
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
                f"{endpoint}/api/node/distributed-topology/transfer",
                json=dict(payload),
                headers=headers,
                timeout=timeout,
            )
        except httpx.ConnectError as exc:
            raise RetryableTopologyPhaseError("remote_topology_connect_failed") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            raise UncertainTopologyPhaseError("remote_topology_ack_timeout") from exc
        except httpx.RequestError as exc:
            raise RetryableTopologyPhaseError(
                "remote_topology_transport_failed"
            ) from exc
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
                str(
                    body.get("detail") or f"remote_topology_http_{response.status_code}"
                )
            )
        if not isinstance(body, Mapping):
            raise TopologyExecutionError("remote_topology_response_invalid")
        if body.get("schema") != TOPOLOGY_TRANSFER_RESULT_SCHEMA:
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
            raise RetryableTopologyPhaseError(
                "remote_topology_member_link_unavailable"
            ) from exc
        except RuntimeError as exc:
            reason = str(exc)
            if any(
                token in reason
                for token in (
                    "member_not_connected",
                    "member_rpc_busy",
                    "link_replaced",
                )
            ):
                raise RetryableTopologyPhaseError(reason) from exc
            raise TopologyExecutionError(reason) from exc
        if not isinstance(body, Mapping):
            raise TopologyExecutionError("remote_topology_response_invalid")
        if body.get("schema") != TOPOLOGY_PHASE_RESULT_SCHEMA:
            raise TopologyExecutionError("remote_topology_response_schema_invalid")
        return dict(body)

    def execute_transfer(
        self, *, node_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            body = self.rpc_call(
                node_id,
                method="distributed.topology.transfer",
                params=dict(payload),
                timeout=max(30.0, float(self.operation_timeout_seconds)),
            )
        except TimeoutError as exc:
            raise UncertainTopologyPhaseError("remote_topology_ack_timeout") from exc
        except ConnectionError as exc:
            raise RetryableTopologyPhaseError(
                "remote_topology_member_link_unavailable"
            ) from exc
        except RuntimeError as exc:
            reason = str(exc)
            if any(
                token in reason
                for token in (
                    "member_not_connected",
                    "member_rpc_busy",
                    "link_replaced",
                )
            ):
                raise RetryableTopologyPhaseError(reason) from exc
            raise TopologyExecutionError(reason) from exc
        if not isinstance(body, Mapping):
            raise TopologyExecutionError("remote_topology_response_invalid")
        if body.get("schema") != TOPOLOGY_TRANSFER_RESULT_SCHEMA:
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
        "source_replica",
        "target_replica",
        "phase_inputs",
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
    source_replica = (
        Replica.from_mapping(payload["source_replica"])
        if isinstance(payload.get("source_replica"), Mapping)
        else None
    )
    target_replica = (
        Replica.from_mapping(payload["target_replica"])
        if isinstance(payload.get("target_replica"), Mapping)
        else None
    )
    if (
        partition.partition_id != step.partition_id
        or partition.dataset_id != dataset.dataset_id
    ):
        raise TopologyExecutionError("topology_phase_resource_identity_mismatch")
    if plan.plan_digest != str(payload.get("plan_digest") or ""):
        raise TopologyExecutionError("topology_phase_plan_digest_mismatch")
    if plan.status != "ready" or not any(item == step for item in plan.steps):
        raise TopologyExecutionError("topology_phase_plan_not_reviewed")
    selected = _selected_instance(
        str(payload.get("phase") or ""), source=source, target=target
    )
    for instance, replica in (
        (source, source_replica),
        (target, target_replica),
    ):
        if replica is not None and (
            instance is None
            or replica.partition_id != partition.partition_id
            or replica.instance_id != instance.instance_id
            or replica.node_id != instance.node_id
        ):
            raise TopologyExecutionError("topology_phase_replica_identity_mismatch")
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
        dataset.metadata.get("topology_adapter_tool") or DEFAULT_TOPOLOGY_ADAPTER_TOOL
    ).strip()
    if str(payload.get("adapter_tool") or "") != expected_tool:
        raise TopologyExecutionError("topology_phase_adapter_tool_mismatch")
    if str(payload.get("phase") or "") not in step.phases:
        raise TopologyExecutionError("topology_phase_not_in_reviewed_plan")
    expected_epoch = plan.authority_epoch + (
        1
        if plan.kind == "handoff"
        and str(payload.get("phase") or "") in {"promote", "route", "demote"}
        else 0
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


def execute_topology_transfer_request(
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
        "transfer_tool",
        "direction",
        "operation_id",
        "plan_digest",
        "plan",
        "step",
        "dataset",
        "partition",
        "source_instance",
        "target_instance",
        "authority_epoch",
        "idempotency_key",
        "transfer_id",
        "manifest",
        "checkpoint",
        "max_bytes",
        "chunk",
    }
    if (
        set(payload) != fields
        or payload.get("schema") != TOPOLOGY_TRANSFER_REQUEST_SCHEMA
    ):
        raise TopologyExecutionError("topology_transfer_request_schema_invalid")
    if len(str(payload).encode("utf-8")) > MAX_TOPOLOGY_TRANSFER_BYTES:
        raise TopologyExecutionError("topology_transfer_request_too_large")
    if str(payload.get("target_node_id") or "") != local_node_id:
        raise TopologyExecutionError("topology_transfer_target_node_mismatch")
    step = TopologyPlanStep.from_mapping(payload["step"])
    plan = TopologyPlan.from_mapping(payload["plan"])
    dataset = Dataset.from_mapping(payload["dataset"])
    partition = Partition.from_mapping(payload["partition"])
    source = ServiceInstance.from_mapping(payload["source_instance"])
    target = ServiceInstance.from_mapping(payload["target_instance"])
    if (
        partition.partition_id != step.partition_id
        or partition.dataset_id != dataset.dataset_id
    ):
        raise TopologyExecutionError("topology_transfer_resource_identity_mismatch")
    if plan.plan_digest != str(payload.get("plan_digest") or ""):
        raise TopologyExecutionError("topology_transfer_plan_digest_mismatch")
    if plan.status != "ready" or not any(item == step for item in plan.steps):
        raise TopologyExecutionError("topology_transfer_plan_not_reviewed")
    direction = str(payload.get("direction") or "").strip().lower()
    if direction == "read":
        selected = source
        if not any(phase in step.phases for phase in ("snapshot", "stream_deltas")):
            raise TopologyExecutionError("topology_transfer_read_not_reviewed")
        if payload.get("chunk") is not None:
            raise TopologyExecutionError("topology_transfer_read_chunk_forbidden")
    elif direction == "write":
        selected = target
        if "catch_up" not in step.phases:
            raise TopologyExecutionError("topology_transfer_write_not_reviewed")
        chunk = payload.get("chunk")
        if not isinstance(chunk, Mapping):
            raise TopologyExecutionError("topology_transfer_write_chunk_missing")
    else:
        raise TopologyExecutionError("topology_transfer_direction_invalid")
    if selected.node_id != local_node_id or selected.instance_id != str(
        payload.get("selected_instance_id") or ""
    ):
        raise TopologyExecutionError("topology_transfer_participant_mismatch")
    skill_id = _owner_skill(dataset.owner_ref)
    if (
        skill_id != str(payload.get("skill_id") or "")
        or selected.component_ref != f"skill:{skill_id}"
    ):
        raise TopologyExecutionError("topology_transfer_component_mismatch")
    expected_tool = str(
        dataset.metadata.get("topology_transfer_tool") or DEFAULT_TOPOLOGY_TRANSFER_TOOL
    ).strip()
    if str(payload.get("transfer_tool") or "") != expected_tool:
        raise TopologyExecutionError("topology_transfer_tool_mismatch")
    if (
        int(payload.get("authority_epoch") or 0) != partition.authority_epoch
        or plan.authority_epoch != partition.authority_epoch
    ):
        raise TopologyExecutionError("topology_transfer_authority_epoch_mismatch")
    manifest = payload.get("manifest")
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("schema") != "adaos.distributed.transfer_manifest.v1"
    ):
        raise TopologyExecutionError("topology_transfer_manifest_invalid")
    max_bytes = int(payload.get("max_bytes") or 0)
    if not 1 <= max_bytes <= MAX_TOPOLOGY_TRANSFER_CHUNK_BYTES:
        raise TopologyExecutionError("topology_transfer_max_bytes_invalid")
    result = executor(
        skill_id,
        str(payload.get("transfer_tool") or ""),
        dict(payload),
    )
    receipt = _bounded_transfer_result(result)
    return {
        "schema": TOPOLOGY_TRANSFER_RESULT_SCHEMA,
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


def execute_registered_topology_transfer(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    with _receiver_lock:
        executor = _receiver_executor
        node_id = _receiver_node_id
    if executor is None or not node_id:
        raise TopologyExecutionError("topology_phase_receiver_not_configured")
    return execute_topology_transfer_request(
        payload,
        local_node_id=node_id,
        executor=executor,
    )


__all__ = [
    "DEFAULT_TOPOLOGY_ADAPTER_TOOL",
    "DEFAULT_TOPOLOGY_TRANSFER_TOOL",
    "HttpTopologyPhaseTransport",
    "MemberLinkTopologyPhaseTransport",
    "MAX_TOPOLOGY_PHASE_BYTES",
    "MAX_TOPOLOGY_TRANSFER_BYTES",
    "MAX_TOPOLOGY_TRANSFER_CHUNK_BYTES",
    "RemoteTopologyPhaseTransport",
    "SkillToolTopologyAdapter",
    "TOPOLOGY_PHASE_REQUEST_SCHEMA",
    "TOPOLOGY_PHASE_RESULT_SCHEMA",
    "TOPOLOGY_TRANSFER_REQUEST_SCHEMA",
    "TOPOLOGY_TRANSFER_RESULT_SCHEMA",
    "execute_registered_topology_phase",
    "execute_registered_topology_transfer",
    "execute_topology_phase_request",
    "execute_topology_transfer_request",
    "register_topology_phase_receiver",
]
