from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx
import pytest

from adaos.domain.distributed_operations import TopologyPlan, TopologyPlanStep
from adaos.domain.distributed_runtime import Dataset, Partition, ServiceInstance
from adaos.services.distributed_runtime.adapters import (
    HttpTopologyPhaseTransport,
    SkillToolTopologyAdapter,
    execute_topology_phase_request,
)
from adaos.services.distributed_runtime.operations import (
    TopologyExecutionError,
    TopologyStepContext,
    UncertainTopologyPhaseError,
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
