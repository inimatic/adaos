from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from adaos.services.project_deployment.runtime import (
    NodeInventoryProvider,
    ProjectReleaseProvider,
)
from adaos.services.project_deployment.store import ProjectDeploymentStore

from .operations import TopologyAdapter
from .runtime import DistributedRuntime, register_distributed_runtime
from .store import DistributedRuntimeStore


def configure_distributed_runtime(
    *,
    releases: ProjectReleaseProvider,
    inventory: NodeInventoryProvider,
    topology_adapter: TopologyAdapter | None = None,
    state_dir: Path | None = None,
    deployment_store: ProjectDeploymentStore | None = None,
    projection_publisher: Callable[[Mapping[str, Any]], Any] | None = None,
) -> DistributedRuntime:
    """Install the public distributed SDK runtime over explicit provider ports."""

    runtime = DistributedRuntime(
        store=DistributedRuntimeStore(state_dir=state_dir),
        deployment_store=deployment_store
        or ProjectDeploymentStore(state_dir=state_dir),
        releases=releases,
        inventory=inventory,
        topology_adapter=topology_adapter,
        projection_publisher=projection_publisher,
    )
    register_distributed_runtime(runtime)
    return runtime


__all__ = ["configure_distributed_runtime"]
