"""Public distributed Project deployment SDK for skills and scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

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
    invoke_project_deployment_authority,
)


_APPROVAL_PERMISSIONS = {
    "remote_install": "project.component.install.remote",
    "component_drain": "project.component.drain",
    "component_remove": "project.component.remove",
    "runtime_data_delete": "project.data.runtime.delete",
    "derived_data_delete": "project.data.derived.delete",
}


@dataclass(frozen=True, slots=True)
class NodeInventoryPage:
    subnet_id: str
    nodes: tuple[NodeInventoryRecord, ...]
    total: int
    truncated: bool


def _result(
    operation: str, arguments: Mapping[str, Any], principal: DeploymentPrincipal
) -> Any:
    return invoke_project_deployment_authority(
        operation,
        arguments,
        principal=principal,
    )


def _mapping(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"deployment authority returned invalid {operation} result")
    return dict(value)


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

    payload = _result(
        "define",
        {
            "desired": desired.to_dict(),
            "expected_revision": expected_revision,
            "reason": reason,
        },
        _principal(("project.deployment.manage",)),
    )
    return ProjectDeployment.from_mapping(_mapping(payload, "define"))


def plan(deployment_id: str) -> DeploymentPlan:
    """Build and persist an immutable dry-run plan for the current inventory."""

    payload = _result(
        "plan",
        {"deployment_id": deployment_id},
        _principal(("project.deployment.inspect",)),
    )
    return DeploymentPlan.from_mapping(_mapping(payload, "plan"))


def apply(
    plan_digest: str,
    *,
    idempotency_key: str,
    approvals: Iterable[str] = (),
) -> DeploymentOperation:
    """Apply one reviewed plan; uncertain adapter outcomes are never retried implicitly."""

    payload = _result(
        "apply",
        {"plan_digest": plan_digest, "idempotency_key": idempotency_key},
        _principal(("project.deployment.apply",), approvals=approvals),
    )
    return DeploymentOperation.from_mapping(_mapping(payload, "apply"))


def submit(
    plan_digest: str,
    *,
    idempotency_key: str,
    approvals: Iterable[str] = (),
) -> DeploymentOperation:
    """Accept a reviewed plan and run it as a durable background operation."""

    payload = _result(
        "submit",
        {"plan_digest": plan_digest, "idempotency_key": idempotency_key},
        _principal(("project.deployment.apply",), approvals=approvals),
    )
    return DeploymentOperation.from_mapping(_mapping(payload, "submit"))


def get_operation(operation_id: str) -> DeploymentOperation:
    """Read one deployment operation without scanning deployment history."""

    payload = _result(
        "get_operation",
        {"operation_id": operation_id},
        _principal(("project.deployment.inspect",)),
    )
    return DeploymentOperation.from_mapping(_mapping(payload, "get_operation"))


def reconcile(
    deployment_id: str,
    *,
    idempotency_key: str,
    approvals: Iterable[str] = (),
) -> DeploymentOperation:
    """Plan current desired/observed drift and apply it as a journaled operation."""

    principal = _principal(
        (
            "project.deployment.inspect",
            "project.deployment.apply",
            "project.deployment.reconcile",
        ),
        approvals=approvals,
    )
    payload = _result(
        "reconcile",
        {"deployment_id": deployment_id, "idempotency_key": idempotency_key},
        principal,
    )
    return DeploymentOperation.from_mapping(_mapping(payload, "reconcile"))


def inspect(
    deployment_id: str,
    *,
    activation_cursor: str | None = None,
    operation_cursor: str | None = None,
    limit: int = 50,
) -> DeploymentInspection:
    """Read a bounded desired/observed deployment page."""

    value = _mapping(
        _result(
            "inspect",
            {
                "deployment_id": deployment_id,
                "activation_cursor": activation_cursor,
                "operation_cursor": operation_cursor,
                "limit": limit,
            },
            _principal(("project.deployment.inspect",)),
        ),
        "inspect",
    )
    return DeploymentInspection(
        desired=ProjectDeployment.from_mapping(
            _mapping(value.get("desired"), "inspect.desired")
        ),
        activations=tuple(
            ComponentActivation.from_mapping(_mapping(item, "inspect.activation"))
            for item in list(value.get("activations") or ())
        ),
        operations=tuple(
            DeploymentOperation.from_mapping(_mapping(item, "inspect.operation"))
            for item in list(value.get("operations") or ())
        ),
        activation_cursor=str(value.get("activation_cursor") or "").strip() or None,
        operation_cursor=str(value.get("operation_cursor") or "").strip() or None,
    )


def list_deployments(
    *, cursor: str | None = None, limit: int = 50
) -> tuple[tuple[ProjectDeployment, ...], str | None]:
    """List desired deployments through an opaque cursor."""

    value = _mapping(
        _result(
            "list_deployments",
            {"cursor": cursor, "limit": limit},
            _principal(("project.deployment.inspect",)),
        ),
        "list_deployments",
    )
    return (
        tuple(
            ProjectDeployment.from_mapping(_mapping(item, "list_deployments.item"))
            for item in list(value.get("deployments") or ())
        ),
        str(value.get("cursor") or "").strip() or None,
    )


def recommend_nodes(
    deployment_id: str,
    component_ref: str,
    *,
    limit: int = 20,
) -> dict[str, object]:
    """Rank bounded eligible nodes without changing desired deployment state."""

    return _mapping(
        _result(
            "recommend_nodes",
            {
                "deployment_id": deployment_id,
                "component_ref": component_ref,
                "limit": limit,
            },
            _principal(("project.deployment.inspect",)),
        ),
        "recommend_nodes",
    )


def drain(
    activation_id: str,
    *,
    idempotency_key: str,
) -> DeploymentOperation:
    """Cordon and drain one activation without changing desired placement."""

    payload = _result(
        "drain",
        {"activation_id": activation_id, "idempotency_key": idempotency_key},
        _principal(("project.deployment.apply",), approvals=("component_drain",)),
    )
    return DeploymentOperation.from_mapping(_mapping(payload, "drain"))


def remove(
    activation_id: str,
    *,
    idempotency_key: str,
) -> DeploymentOperation:
    """Drain and remove one activation while retaining externally owned data."""

    payload = _result(
        "remove",
        {"activation_id": activation_id, "idempotency_key": idempotency_key},
        _principal(("project.deployment.apply",), approvals=("component_remove",)),
    )
    return DeploymentOperation.from_mapping(_mapping(payload, "remove"))


def list_nodes(subnet_id: str, *, limit: int = 100) -> NodeInventoryPage:
    """Read the authority-owned bounded deployment inventory for one subnet."""

    value = _mapping(
        _result(
            "inventory",
            {"subnet_id": subnet_id, "limit": limit},
            _principal(("project.deployment.inspect",)),
        ),
        "inventory",
    )
    return NodeInventoryPage(
        subnet_id=str(value.get("subnet_id") or ""),
        nodes=tuple(
            NodeInventoryRecord.from_mapping(_mapping(item, "inventory.node"))
            for item in list(value.get("nodes") or ())
        ),
        total=max(0, int(value.get("total") or 0)),
        truncated=bool(value.get("truncated")),
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
    "NodeInventoryPage",
    "ProjectDeployment",
    "RolloutPolicy",
    "apply",
    "get_operation",
    "define",
    "drain",
    "inspect",
    "list_deployments",
    "list_nodes",
    "plan",
    "recommend_nodes",
    "reconcile",
    "remove",
    "submit",
]
