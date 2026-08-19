from __future__ import annotations

from types import SimpleNamespace

from adaos.services.agent_context import get_ctx
from adaos.services.distributed_runtime import (
    SkillToolTopologyAdapter,
    get_distributed_runtime,
    register_distributed_runtime,
    register_service_invocation_receiver,
    register_topology_phase_receiver,
)
from adaos.services.project_deployment import (
    get_project_deployment_runtime,
    register_local_deployment_receiver,
    register_project_deployment_runtime,
)
from adaos.services.project_deployment.default_runtime import (
    configure_default_distributed_runtimes,
    deployment_runtime_inventory_payload,
    local_node_inventory_record,
)


def test_default_runtimes_share_durable_store_and_publish_local_inventory(monkeypatch) -> None:
    ctx = get_ctx()
    object.__setattr__(
        ctx,
        "config",
        SimpleNamespace(
            node_id="node-local",
            subnet_id="subnet-home",
            role="hub",
            token="test-token",
        ),
    )
    monkeypatch.setenv("ADAOS_NODE_DEPLOYMENT_URL", "http://192.0.2.10:8778")

    configured = configure_default_distributed_runtimes(ctx)
    deployment = get_project_deployment_runtime()
    distributed = get_distributed_runtime()
    payload = deployment_runtime_inventory_payload(ctx)
    node = local_node_inventory_record(ctx)

    assert configured["configured"] is True
    assert deployment.store.root.parent == distributed.deployment_store.root.parent
    assert node.node_id == "node-local"
    assert node.trust_state == "trusted"
    assert "project.activate" in node.capabilities
    assert node.endpoints[0].role == "deployment"
    assert payload["protocols"]["distributed_topology"] == "1"
    assert isinstance(distributed.topology_adapter, SkillToolTopologyAdapter)
    assert distributed.service_invoker is not None

    register_project_deployment_runtime(None)
    register_distributed_runtime(None)
    register_local_deployment_receiver(None)
    register_topology_phase_receiver(None)
    register_service_invocation_receiver(None)
