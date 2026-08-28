from __future__ import annotations

from pathlib import Path
from typing import Any

from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.storage import mutation_lock

from .adapters import (
    ProjectDeploymentExecutionError,
    verify_materialized_component_target,
)
from .store import ProjectDeploymentStore


class ProjectOwnedComponentMutationError(RuntimeError):
    """Raised when a standalone lifecycle tries to mutate a project component."""


def _context_paths(ctx: Any) -> tuple[Path, Path, str] | None:
    paths = getattr(ctx, "paths", None)
    state_dir = getattr(paths, "state_dir", None)
    workspace_dir = getattr(paths, "workspace_dir", None)
    node_id = str(getattr(getattr(ctx, "config", None), "node_id", "") or "").strip()
    if not callable(state_dir) or not callable(workspace_dir) or not node_id:
        return None
    return Path(state_dir()).resolve(), Path(workspace_dir()).resolve(), node_id


def active_project_component(ctx: Any, component_ref: str) -> bool:
    resolved = _context_paths(ctx)
    if resolved is None:
        return False
    state_dir, _workspace_root, local_node_id = resolved
    store = ProjectDeploymentStore(state_dir=state_dir)
    cursor: str | None = None
    while True:
        page, cursor = store.list_activations(cursor=cursor, limit=100)
        if any(
            activation.node_id == local_node_id
            and activation.status == "active"
            and activation.component_ref == component_ref
            for activation in page
        ):
            return True
        if cursor is None:
            return False


def ensure_standalone_component_mutation_allowed(
    ctx: Any,
    component_ref: str,
    *,
    operation: str,
) -> None:
    component_ref = str(component_ref or "").strip()
    if not component_ref or not active_project_component(ctx, component_ref):
        return
    raise ProjectOwnedComponentMutationError(
        "project_owned_component: "
        f"{component_ref} is managed by an active project deployment; "
        f"use the project deployment lifecycle instead of standalone {operation}"
    )


def restore_project_owned_materializations(ctx: Any) -> dict[str, Any]:
    resolved = _context_paths(ctx)
    if resolved is None:
        return {
            "ok": True,
            "configured": False,
            "checked": [],
            "repaired": [],
            "reason": "project_materialization_context_unavailable",
        }
    state_dir, workspace_root, local_node_id = resolved
    store = ProjectDeploymentStore(state_dir=state_dir)
    package_store = ContentAddressedPackageStore(
        state_dir / "artifact_pipeline" / "packages"
    )
    active = []
    cursor: str | None = None
    while True:
        page, cursor = store.list_activations(cursor=cursor, limit=100)
        active.extend(
            activation
            for activation in page
            if activation.node_id == local_node_id
            and activation.status == "active"
        )
        if cursor is None:
            break

    by_component: dict[str, Any] = {}
    conflicts: list[str] = []
    for activation in sorted(
        active,
        key=lambda item: (item.component_ref, item.generation, item.updated_at),
    ):
        previous = by_component.get(activation.component_ref)
        if previous is not None and previous.activation_id != activation.activation_id:
            conflicts.append(activation.component_ref)
            continue
        by_component[activation.component_ref] = activation
    if conflicts:
        return {
            "ok": False,
            "configured": True,
            "checked": [],
            "repaired": [],
            "error": "project_component_ownership_conflict",
            "components": sorted(set(conflicts)),
        }

    checked: list[str] = []
    repaired: list[str] = []
    errors: list[dict[str, str]] = []
    lock_path = state_dir / "project_deployments" / "component_operations" / ".mutation.lock"
    with mutation_lock(lock_path, timeout_s=60.0):
        for component_ref, activation in sorted(by_component.items()):
            kind, component_id = component_ref.split(":", 1)
            expected_relative = f"{kind}s/{component_id}"
            try:
                verified = package_store.verify(activation.package_digest)
                package = verified.ref
                if package.materialization_path != expected_relative:
                    raise ProjectDeploymentExecutionError(
                        "project component materialization path is not canonical"
                    )
                target = (workspace_root / expected_relative).resolve()
                if workspace_root != target and workspace_root not in target.parents:
                    raise ProjectDeploymentExecutionError(
                        "project component materialization escaped workspace"
                    )
                checked.append(component_ref)
                try:
                    verify_materialized_component_target(
                        package_store,
                        package,
                        target,
                    )
                except ProjectDeploymentExecutionError:
                    package_store.materialize(package.digest, target)
                    verify_materialized_component_target(
                        package_store,
                        package,
                        target,
                    )
                    repaired.append(component_ref)
            except Exception as exc:
                errors.append(
                    {
                        "component_ref": component_ref,
                        "error_type": type(exc).__name__,
                    }
                )

    return {
        "ok": not errors,
        "configured": True,
        "checked": checked,
        "repaired": repaired,
        "errors": errors,
    }


__all__ = [
    "ProjectOwnedComponentMutationError",
    "active_project_component",
    "ensure_standalone_component_mutation_allowed",
    "restore_project_owned_materializations",
]
