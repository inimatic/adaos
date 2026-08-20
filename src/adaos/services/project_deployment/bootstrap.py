from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .execution import ComponentDeploymentAdapter
from .runtime import (
    NodeInventoryProvider,
    ProjectDeploymentRuntime,
    ProjectReleaseProvider,
    register_project_deployment_runtime,
)
from .store import ProjectDeploymentStore


def configure_project_deployment_runtime(
    *,
    releases: ProjectReleaseProvider,
    inventory: NodeInventoryProvider,
    adapter: ComponentDeploymentAdapter,
    state_dir: Path | None = None,
    local_node_id: str | None = None,
    projection_publisher: Callable[[Mapping[str, Any]], Any] | None = None,
) -> ProjectDeploymentRuntime:
    """Install one process-wide runtime from explicit trusted infrastructure ports."""

    runtime = ProjectDeploymentRuntime(
        store=ProjectDeploymentStore(state_dir=state_dir),
        releases=releases,
        inventory=inventory,
        adapter=adapter,
        local_node_id=local_node_id,
        projection_publisher=projection_publisher,
    )
    register_project_deployment_runtime(runtime)
    return runtime


__all__ = ["configure_project_deployment_runtime"]
