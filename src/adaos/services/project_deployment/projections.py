from __future__ import annotations

from collections import Counter
from typing import Any

from .store import ProjectDeploymentStore


def build_project_deployment_projection(
    store: ProjectDeploymentStore,
    *,
    cursor: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Build a bounded desired/observed operator projection, never raw package data."""

    deployments, next_cursor = store.list_deployments(cursor=cursor, limit=limit)
    items: list[dict[str, Any]] = []
    for desired in deployments:
        activations, activation_cursor = store.list_activations(
            deployment_id=desired.deployment_id,
            limit=100,
        )
        operations, operation_cursor = store.list_operations(
            deployment_id=desired.deployment_id,
            limit=100,
        )
        activation_states = Counter(item.status for item in activations)
        operation_states = Counter(item.state for item in operations)
        latest_operation = max(
            operations,
            key=lambda item: (item.updated_at, item.operation_id),
            default=None,
        )
        items.append(
            {
                "deployment_id": desired.deployment_id,
                "project_ref": desired.project_ref,
                "subnet_id": desired.subnet_id,
                "desired": {
                    "revision": desired.revision,
                    "release_digest": desired.release_digest,
                    "status": desired.status,
                    "components": len(desired.placements),
                },
                "observed": {
                    "activation_total": len(activations),
                    "activation_states": dict(sorted(activation_states.items())),
                    "activation_partial": activation_cursor is not None,
                    "operation_total": len(operations),
                    "operation_states": dict(sorted(operation_states.items())),
                    "operation_partial": operation_cursor is not None,
                    "latest_operation": (
                        {
                            "operation_id": latest_operation.operation_id,
                            "kind": latest_operation.kind,
                            "state": latest_operation.state,
                            "uncertain": latest_operation.uncertain,
                            "updated_at": latest_operation.updated_at,
                        }
                        if latest_operation is not None
                        else None
                    ),
                },
            }
        )
    return {
        "schema": "adaos.project.deployment_projection.v1",
        "items": items,
        "next_cursor": next_cursor,
        "partial": next_cursor is not None,
    }


__all__ = ["build_project_deployment_projection"]
