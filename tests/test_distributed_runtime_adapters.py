from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any, Mapping

import httpx
import pytest

from adaos.domain.distributed_operations import TopologyPlan, TopologyPlanStep
from adaos.domain.distributed_runtime import (
    Dataset,
    Partition,
    Replica,
    ServiceInstance,
)
from adaos.services.distributed_runtime.adapters import (
    HttpTopologyPhaseTransport,
    MemberLinkTopologyPhaseTransport,
    SkillToolTopologyAdapter,
    execute_topology_phase_request,
)
from adaos.services.distributed_runtime.operations import (
    TopologyExecutionError,
    TopologyStepContext,
    UncertainTopologyPhaseError,
)
from adaos.services.distributed_runtime.service_invocation import (
    HttpServiceInvocationTransport,
    MemberLinkServiceInvocationTransport,
    execute_service_invocation_request,
)


_DIGEST = "sha256:" + "1" * 64


def _instance(instance_id: str, node_id: str) -> ServiceInstance:
    return ServiceInstance(
        instance_id=instance_id,
        group_id="catalog-agents",
        node_id=node_id,
        activation_id=f"activation-{instance_id}",
        release_digest=_DIGEST,
        component_ref="skill:document_index_agent",
        runtime_generation=1,
        protocol_version="1",
        topology_generation=1,
        lease_id=f"lease-{instance_id}",
        status="ready",
        readiness=True,
        health={},
        pressure={},
        capabilities=(),
        endpoints=(),
        observed_at="2026-08-19T00:00:00+00:00",
    )


def _dataset() -> Dataset:
    return Dataset(
        dataset_id="documents",
        owner_ref="skill:document_index_agent",
        contract="documents.index.v1",
        consistency_profile="derived_projection",
        partition_scheme={"kind": "prefix"},
        retention={"on_remove": "rebuild"},
        data_class="derived",
        desired_revision=1,
    )


def _partition() -> Partition:
    return Partition(
        partition_id="documents:a-f",
        dataset_id="documents",
        selector={"prefix": "a-f"},
        desired_replicas=2,
        topology_generation=1,
        authority_lease_id=None,
        authority_epoch=0,
        checkpoint="offset:10",
        status="ready",
    )


def _step() -> TopologyPlanStep:
    return TopologyPlanStep(
        step_id="move-documents",
        action="move",
        partition_id="documents:a-f",
        source_instance_id="documents-node-a",
        target_instance_id="documents-node-b",
        replica_role="derived",
        phases=("snapshot", "verify"),
        retention="rebuild",
    )


def _plan() -> TopologyPlan:
    return TopologyPlan(
        plan_id="move-documents-plan",
        kind="replicate",
        target_ref="partition:documents:a-f",
        expected_desired_revision=1,
        expected_observed_revision=1,
        authority_epoch=0,
        steps=(_step(),),
        created_at="2026-08-19T00:00:00+00:00",
    )


@dataclass
class _Store:
    replicas: dict[str, Replica] = field(default_factory=dict)

    def get_partition(self, _partition_id: str) -> Partition:
        return _partition()

    def get_dataset(self, _dataset_id: str) -> Dataset:
        return _dataset()

    def get_instance(self, instance_id: str) -> ServiceInstance:
        return _instance(
            instance_id,
            "node-a" if instance_id.endswith("node-a") else "node-b",
        )

    def get_plan(self, _plan_digest: str) -> TopologyPlan:
        return _plan()

    def get_replica(self, replica_id: str) -> Replica:
        try:
            return self.replicas[replica_id]
        except KeyError as exc:
            raise FileNotFoundError(replica_id) from exc

    def put_replica(self, replica: Replica, *, expected_revision: int) -> Replica:
        previous = self.replicas.get(replica.replica_id)
        assert expected_revision == (previous.revision if previous else 0)
        self.replicas[replica.replica_id] = replica
        return replica

    def list_replicas(self, *, partition_id: str, limit: int):
        assert limit == 200
        return (
            tuple(
                item
                for item in self.replicas.values()
                if item.partition_id == partition_id
            ),
            None,
        )


class _Remote:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def execute_phase(
        self, *, node_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.calls.append((node_id, payload))
        return {
            "schema": "adaos.distributed.topology_phase_result.v1",
            "ok": True,
            "receipt": {"remote": True},
        }


def test_skill_adapter_routes_source_and_target_phases_to_owning_nodes() -> None:
    local_calls: list[tuple[str, str, Mapping[str, Any]]] = []
    remote = _Remote()

    def local(
        skill_id: str, tool: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        local_calls.append((skill_id, tool, payload))
        return {"ok": True, "receipt": {"local": True}}

    adapter = SkillToolTopologyAdapter(
        store=_Store(),  # type: ignore[arg-type]
        local_node_id="node-b",
        local_executor=local,
        remote=remote,
    )
    base = {
        "operation_id": "operation-1",
        "plan_digest": str(_plan().plan_digest),
        "step": _step(),
        "authority_epoch": 0,
        "idempotency_key": "phase-1",
        "attempt": 1,
    }
    assert adapter.snapshot(TopologyStepContext(phase="snapshot", **base)) == {
        "remote": True
    }
    assert adapter.verify(TopologyStepContext(phase="verify", **base)) == {
        "local": True
    }
    assert remote.calls[0][0] == "node-a"
    assert local_calls[0][:2] == (
        "document_index_agent",
        "distributed_topology_phase",
    )


def test_skill_adapter_carries_bounded_snapshot_to_later_target_phase() -> None:
    captured: list[Mapping[str, Any]] = []
    store = _Store()
    inline_snapshot = {
        "schema": "adaos.distributed.inline_snapshot.v1",
        "payload_digest": _DIGEST,
        "payload": "bounded",
    }
    store.get_operation = lambda _operation_id: SimpleNamespace(  # type: ignore[attr-defined]
        phases=(
            SimpleNamespace(
                phase="move-documents.snapshot",
                receipt={"inline_snapshot": inline_snapshot},
            ),
        )
    )
    adapter = SkillToolTopologyAdapter(
        store=store,  # type: ignore[arg-type]
        local_node_id="node-b",
        local_executor=lambda _skill, _tool, payload: (
            captured.append(payload) or {"ok": True, "receipt": {}}
        ),
        remote=_Remote(),
    )

    adapter.verify(
        TopologyStepContext(
            operation_id="operation-1",
            plan_digest=str(_plan().plan_digest),
            step=_step(),
            phase="verify",
            authority_epoch=0,
            idempotency_key="phase-1",
            attempt=1,
        )
    )

    assert captured[0]["phase_inputs"] == {"source_snapshot": inline_snapshot}


def test_skill_adapter_commits_remote_replica_receipt_to_authority_store() -> None:
    store = _Store()
    replica = Replica(
        replica_id="replica-documents-node-b",
        partition_id="documents:a-f",
        instance_id="documents-node-b",
        node_id="node-b",
        role="derived",
        lifecycle="ready",
        content_state="non_empty",
        authority_epoch=0,
        checkpoint="offset:10",
        source_ref=None,
        freshness_seconds=0,
        item_count=10,
        byte_count=100,
        observed_at="2026-08-19T00:00:00+00:00",
    )
    adapter = SkillToolTopologyAdapter(
        store=store,  # type: ignore[arg-type]
        local_node_id="node-b",
        local_executor=lambda *_args: {
            "ok": True,
            "receipt": {"replica": replica.to_dict()},
        },
        remote=_Remote(),
    )
    receipt = adapter.verify(
        TopologyStepContext(
            operation_id="operation-1",
            plan_digest=str(_plan().plan_digest),
            step=_step(),
            phase="verify",
            authority_epoch=0,
            idempotency_key="phase-1",
            attempt=1,
        )
    )
    assert receipt["replica"]["revision"] == 1
    assert store.get_replica(replica.replica_id).checkpoint == "offset:10"

    invalid = replace(replica, node_id="node-a")
    adapter.local_executor = lambda *_args: {
        "ok": True,
        "receipt": {"replica": invalid.to_dict()},
    }
    with pytest.raises(TopologyExecutionError, match="identity_mismatch"):
        adapter.verify(
            TopologyStepContext(
                operation_id="operation-2",
                plan_digest=str(_plan().plan_digest),
                step=_step(),
                phase="verify",
                authority_epoch=0,
                idempotency_key="phase-2",
                attempt=1,
            )
        )


def test_skill_adapter_rejects_empty_target_before_read_activation() -> None:
    source = Replica(
        replica_id="replica-documents-node-a",
        partition_id="documents:a-f",
        instance_id="documents-node-a",
        node_id="node-a",
        role="derived",
        lifecycle="ready",
        content_state="non_empty",
        authority_epoch=0,
        checkpoint="offset:10",
        source_ref=None,
        freshness_seconds=0,
        item_count=10,
        byte_count=100,
        observed_at="2026-08-19T00:00:00+00:00",
    )
    store = _Store(replicas={source.replica_id: source})
    adapter = SkillToolTopologyAdapter(
        store=store,  # type: ignore[arg-type]
        local_node_id="node-b",
        local_executor=lambda *_args: {
            "ok": True,
            "receipt": {
                "content_witness": None,
                "checkpoint": None,
                "item_count": 0,
            },
        },
        remote=_Remote(),
    )

    with pytest.raises(TopologyExecutionError, match="content_witness_mismatch"):
        adapter.verify(
            TopologyStepContext(
                operation_id="operation-1",
                plan_digest=str(_plan().plan_digest),
                step=_step(),
                phase="verify",
                authority_epoch=0,
                idempotency_key="phase-1",
                attempt=1,
            )
        )


def test_receiver_validates_target_identity_and_reviewed_phase() -> None:
    source = _instance("documents-node-a", "node-a")
    target = _instance("documents-node-b", "node-b")
    payload = {
        "schema": "adaos.distributed.topology_phase_request.v1",
        "requesting_node_id": "node-a",
        "target_node_id": "node-b",
        "selected_instance_id": target.instance_id,
        "skill_id": "document_index_agent",
        "adapter_tool": "distributed_topology_phase",
        "operation_id": "operation-1",
        "plan_digest": str(_plan().plan_digest),
        "plan": _plan().to_dict(),
        "phase": "verify",
        "authority_epoch": 0,
        "idempotency_key": "phase-1",
        "attempt": 1,
        "step": _step().to_dict(),
        "dataset": _dataset().to_dict(),
        "partition": _partition().to_dict(),
        "source_instance": source.to_dict(),
        "target_instance": target.to_dict(),
        "source_replica": None,
        "target_replica": None,
        "phase_inputs": {},
    }
    result = execute_topology_phase_request(
        payload,
        local_node_id="node-b",
        executor=lambda *_args: {
            "ok": True,
            "receipt": {"witness": _DIGEST},
        },
    )
    assert result["receipt"] == {"witness": _DIGEST}
    with pytest.raises(TopologyExecutionError, match="target_node_mismatch"):
        execute_topology_phase_request(
            payload,
            local_node_id="node-c",
            executor=lambda *_args: {"ok": True, "receipt": {}},
        )


def test_http_topology_transport_marks_lost_ack_uncertain(monkeypatch) -> None:
    def timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("lost ack")

    monkeypatch.setattr(httpx, "post", timeout)
    transport = HttpTopologyPhaseTransport(
        endpoint_resolver=lambda _node_id: "http://node-b",
        token_provider=lambda: "token",
        source_node_id="node-a",
    )
    with pytest.raises(UncertainTopologyPhaseError, match="ack_timeout"):
        transport.execute_phase(
            node_id="node-b",
            payload={"idempotency_key": "phase-1"},
        )


def test_service_invocation_receiver_validates_target_instance() -> None:
    instance = _instance("documents-node-b", "node-b")
    payload = {
        "schema": "adaos.distributed.service_invocation.v1",
        "requesting_node_id": "node-a",
        "target_node_id": "node-b",
        "actor_ref": "skill:document_coordinator",
        "request_id": "pull-1",
        "instance": instance.to_dict(),
        "operation_id": "pull_deltas",
        "arguments": {"limit": 100},
        "timeout_seconds": 20,
    }
    result = execute_service_invocation_request(
        payload,
        local_node_id="node-b",
        executor=lambda _instance, operation, arguments, _timeout: {
            "operation": operation,
            "limit": arguments["limit"],
        },
    )
    assert result["result"] == {"operation": "pull_deltas", "limit": 100}
    with pytest.raises(TopologyExecutionError, match="target_node_mismatch"):
        execute_service_invocation_request(
            payload,
            local_node_id="node-c",
            executor=lambda *_args: {},
        )


def test_http_service_invocation_marks_lost_ack_uncertain(monkeypatch) -> None:
    def timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("lost ack")

    monkeypatch.setattr(httpx, "post", timeout)
    transport = HttpServiceInvocationTransport(
        endpoint_resolver=lambda _node_id: "http://node-b",
        token_provider=lambda: "token",
        source_node_id="node-a",
    )
    with pytest.raises(UncertainTopologyPhaseError, match="ack_timeout"):
        transport.invoke(
            instance=_instance("documents-node-b", "node-b"),
            operation_id="pull_deltas",
            arguments={"limit": 100},
            request_id="pull-1",
            timeout_seconds=20,
            actor_ref="skill:document_coordinator",
        )


def test_member_link_transports_use_distinct_core_methods() -> None:
    calls: list[dict[str, Any]] = []

    def rpc_call(node_id: str, **kwargs: Any) -> Mapping[str, Any]:
        calls.append({"node_id": node_id, **kwargs})
        if kwargs["method"] == "distributed.topology.phase":
            return {
                "schema": "adaos.distributed.topology_phase_result.v1",
                "ok": True,
                "receipt": {"verified": True},
            }
        return {
            "schema": "adaos.distributed.service_invocation_result.v1",
            "request_id": "pull-1",
            "result": {"items": []},
        }

    topology = MemberLinkTopologyPhaseTransport(rpc_call=rpc_call)
    result = topology.execute_phase(
        node_id="node-b", payload={"idempotency_key": "phase-1"}
    )
    service = MemberLinkServiceInvocationTransport(
        rpc_call=rpc_call, source_node_id="node-a"
    )
    invoked = service.invoke(
        instance=_instance("documents-node-b", "node-b"),
        operation_id="pull_deltas",
        arguments={"limit": 100},
        request_id="pull-1",
        timeout_seconds=20,
        actor_ref="skill:document_coordinator",
    )

    assert result["receipt"]["verified"] is True
    assert invoked == {"items": []}
    assert [item["method"] for item in calls] == [
        "distributed.topology.phase",
        "distributed.service.invoke",
    ]
