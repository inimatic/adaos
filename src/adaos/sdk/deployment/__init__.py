"""Public distributed Project deployment SDK for skills and scenarios."""

from __future__ import annotations

from typing import Iterable

from adaos.domain.project_deployment import (
    ComponentActivation,
    ComponentPlacementPolicy,
    DataRetentionPolicy,
    DeploymentCompatibilityPolicy,
    DeploymentOperation,
    DeploymentPlan,
    NodeEndpointRecord,
    NodeInventoryRecord,
    ProjectDeployment,
    RolloutPolicy,
)
from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.project_deployment import (
    DeploymentInspection,
    DeploymentPrincipal,
    get_project_deployment_runtime,
)


_APPROVAL_PERMISSIONS = {
    "remote_install": "project.component.install.remote",
    "component_drain": "project.component.drain",
    "component_remove": "project.component.remove",
    "runtime_data_delete": "project.data.runtime.delete",
    "derived_data_delete": "project.data.derived.delete",
}


def _actor_ref(ctx) -> str:
    current = ctx.skill_ctx.get()
    skill_name = str(getattr(current, "name", "") or "").strip()
    if not skill_name:
        raise RuntimeError("deployment SDK requires an active skill context")
    return f"skill:{skill_name}"


def _principal(
    required: Iterable[str], *, approvals: Iterable[str] = ()
) -> DeploymentPrincipal:
    ctx = require_ctx("sdk.deployment")
    permissions = {str(item) for item in required}
    approval_set = {str(item) for item in approvals}
    for permission in sorted(permissions):
        require_skill_capability(ctx, permission)
    for approval in sorted(approval_set):
        permission = _APPROVAL_PERMISSIONS.get(approval)
        if permission is None:
            raise ValueError(f"unsupported deployment approval: {approval}")
        require_skill_capability(ctx, permission)
        permissions.add(permission)
    return DeploymentPrincipal.create(
        actor_ref=_actor_ref(ctx),
        permissions=permissions,
        approvals=approval_set,
    )


def define(
    desired: ProjectDeployment,
    *,
    expected_revision: int,
    reason: str,
) -> ProjectDeployment:
    """Persist one compare-and-switch desired Project deployment revision."""

    return get_project_deployment_runtime().define(
        desired,
        expected_revision=expected_revision,
        reason=reason,
        principal=_principal(("project.deployment.manage",)),
    )


def plan(deployment_id: str) -> DeploymentPlan:
    """Build and persist an immutable dry-run plan for the current inventory."""

    return get_project_deployment_runtime().plan(
        deployment_id,
        principal=_principal(("project.deployment.inspect",)),
    )


def apply(
    plan_digest: str,
    *,
    idempotency_key: str,
    approvals: Iterable[str] = (),
) -> DeploymentOperation:
    """Apply one reviewed plan; uncertain adapter outcomes are never retried implicitly."""

    return get_project_deployment_runtime().apply(
        plan_digest,
        idempotency_key=idempotency_key,
        principal=_principal(("project.deployment.apply",), approvals=approvals),
    )


def reconcile(
    deployment_id: str,
    *,
    idempotency_key: str,
    approvals: Iterable[str] = (),
) -> DeploymentOperation:
    """Plan current desired/observed drift and apply it as a journaled operation."""

    return get_project_deployment_runtime().reconcile(
        deployment_id,
        idempotency_key=idempotency_key,
        principal=_principal(
            (
                "project.deployment.inspect",
                "project.deployment.apply",
                "project.deployment.reconcile",
            ),
            approvals=approvals,
        ),
    )


def inspect(
    deployment_id: str,
    *,
    activation_cursor: str | None = None,
    operation_cursor: str | None = None,
    limit: int = 50,
) -> DeploymentInspection:
    """Read a bounded desired/observed deployment page."""

    return get_project_deployment_runtime().inspect(
        deployment_id,
        activation_cursor=activation_cursor,
        operation_cursor=operation_cursor,
        limit=limit,
        principal=_principal(("project.deployment.inspect",)),
    )


def list_deployments(
    *, cursor: str | None = None, limit: int = 50
) -> tuple[tuple[ProjectDeployment, ...], str | None]:
    """List desired deployments through an opaque cursor."""

    return get_project_deployment_runtime().list_deployments(
        cursor=cursor,
        limit=limit,
        principal=_principal(("project.deployment.inspect",)),
    )


def recommend_nodes(
    deployment_id: str,
    component_ref: str,
    *,
    limit: int = 20,
) -> dict[str, object]:
    """Rank bounded eligible nodes without changing desired deployment state."""

    return get_project_deployment_runtime().recommend_nodes(
        deployment_id,
        component_ref,
        limit=limit,
        principal=_principal(("project.deployment.inspect",)),
    )


def drain(
    activation_id: str,
    *,
    idempotency_key: str,
) -> DeploymentOperation:
    """Cordon and drain one activation without changing desired placement."""

    return get_project_deployment_runtime().drain(
        activation_id,
        idempotency_key=idempotency_key,
        principal=_principal(
            ("project.deployment.apply",), approvals=("component_drain",)
        ),
    )


def remove(
    activation_id: str,
    *,
    idempotency_key: str,
) -> DeploymentOperation:
    """Drain and remove one activation while retaining externally owned data."""

    return get_project_deployment_runtime().remove(
        activation_id,
        idempotency_key=idempotency_key,
        principal=_principal(
            ("project.deployment.apply",), approvals=("component_remove",)
        ),
    )


__all__ = [
    "ComponentActivation",
    "ComponentPlacementPolicy",
    "DataRetentionPolicy",
    "DeploymentCompatibilityPolicy",
    "DeploymentInspection",
    "DeploymentOperation",
    "DeploymentPlan",
    "NodeEndpointRecord",
    "NodeInventoryRecord",
    "ProjectDeployment",
    "RolloutPolicy",
    "apply",
    "define",
    "drain",
    "inspect",
    "list_deployments",
    "plan",
    "recommend_nodes",
    "reconcile",
    "remove",
]
