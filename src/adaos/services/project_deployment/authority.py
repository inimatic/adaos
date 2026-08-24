from __future__ import annotations

import os
from threading import RLock
from typing import Any, Mapping

import requests

from adaos.domain.project_deployment import ProjectDeployment
from adaos.services.runtime_identity import (
    runtime_identity_snapshot,
    runtime_transition_role,
)

from .authorization import DeploymentPrincipal
from .runtime import ProjectDeploymentRuntime, get_project_deployment_runtime


AUTHORITY_REQUEST_SCHEMA = "adaos.project.deployment.authority.request.v1"
AUTHORITY_RESPONSE_SCHEMA = "adaos.project.deployment.authority.response.v1"
MAX_AUTHORITY_PAGE_SIZE = 200
_OPERATIONS = frozenset(
    {
        "inventory",
        "define",
        "plan",
        "apply",
        "submit",
        "get_operation",
        "reconcile",
        "inspect",
        "list_deployments",
        "recommend_nodes",
        "drain",
        "remove",
    }
)


class ProjectDeploymentAuthorityError(RuntimeError):
    pass


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectDeploymentAuthorityError(f"{field}_must_be_object")
    return dict(value)


def _text(value: Any, field: str, *, max_length: int = 1000) -> str:
    token = str(value or "").strip()
    if not token:
        raise ProjectDeploymentAuthorityError(f"{field}_is_required")
    if len(token) > max_length:
        raise ProjectDeploymentAuthorityError(f"{field}_is_too_long")
    return token


def _limit(value: Any, *, default: int = 50) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise ProjectDeploymentAuthorityError("limit_is_invalid") from exc
    return max(1, min(parsed, MAX_AUTHORITY_PAGE_SIZE))


def _principal_payload(principal: DeploymentPrincipal) -> dict[str, Any]:
    return {
        "actor_ref": principal.actor_ref,
        "permissions": sorted(principal.permissions),
        "approvals": sorted(principal.approvals),
    }


def _principal_from_payload(value: Any) -> DeploymentPrincipal:
    payload = _mapping(value, "principal")
    return DeploymentPrincipal.create(
        actor_ref=_text(
            payload.get("actor_ref"), "principal_actor_ref", max_length=300
        ),
        permissions=tuple(str(item) for item in list(payload.get("permissions") or ())),
        approvals=tuple(str(item) for item in list(payload.get("approvals") or ())),
    )


def _execute(
    runtime: ProjectDeploymentRuntime,
    operation: str,
    arguments: Mapping[str, Any],
    principal: DeploymentPrincipal,
) -> Any:
    args = dict(arguments)
    if operation == "inventory":
        principal.require("project.deployment.inspect")
        subnet_id = _text(args.get("subnet_id"), "subnet_id", max_length=160)
        nodes = tuple(runtime.inventory.list_nodes(subnet_id))
        limit = _limit(args.get("limit"), default=100)
        return {
            "schema": "adaos.project.deployment.inventory.v1",
            "subnet_id": subnet_id,
            "nodes": [item.to_dict() for item in nodes[:limit]],
            "total": len(nodes),
            "truncated": len(nodes) > limit,
        }
    if operation == "define":
        desired = ProjectDeployment.from_mapping(
            _mapping(args.get("desired"), "desired")
        )
        return runtime.define(
            desired,
            expected_revision=int(args.get("expected_revision") or 0),
            reason=_text(args.get("reason"), "reason"),
            principal=principal,
        ).to_dict()
    if operation == "plan":
        return runtime.plan(
            _text(args.get("deployment_id"), "deployment_id"),
            principal=principal,
        ).to_dict()
    if operation in {"apply", "submit"}:
        method = runtime.apply if operation == "apply" else runtime.submit
        return method(
            _text(args.get("plan_digest"), "plan_digest"),
            idempotency_key=_text(args.get("idempotency_key"), "idempotency_key"),
            principal=principal,
        ).to_dict()
    if operation == "get_operation":
        return runtime.get_operation(
            _text(args.get("operation_id"), "operation_id"),
            principal=principal,
        ).to_dict()
    if operation == "reconcile":
        return runtime.reconcile(
            _text(args.get("deployment_id"), "deployment_id"),
            idempotency_key=_text(args.get("idempotency_key"), "idempotency_key"),
            principal=principal,
        ).to_dict()
    if operation == "inspect":
        return runtime.inspect(
            _text(args.get("deployment_id"), "deployment_id"),
            activation_cursor=str(args.get("activation_cursor") or "").strip() or None,
            operation_cursor=str(args.get("operation_cursor") or "").strip() or None,
            limit=_limit(args.get("limit")),
            principal=principal,
        ).to_dict()
    if operation == "list_deployments":
        deployments, cursor = runtime.list_deployments(
            cursor=str(args.get("cursor") or "").strip() or None,
            limit=_limit(args.get("limit")),
            principal=principal,
        )
        return {
            "schema": "adaos.project.deployment.list.v1",
            "deployments": [item.to_dict() for item in deployments],
            "cursor": cursor,
        }
    if operation == "recommend_nodes":
        return runtime.recommend_nodes(
            _text(args.get("deployment_id"), "deployment_id"),
            _text(args.get("component_ref"), "component_ref"),
            limit=_limit(args.get("limit"), default=20),
            principal=principal,
        )
    if operation in {"drain", "remove"}:
        method = runtime.drain if operation == "drain" else runtime.remove
        return method(
            _text(args.get("activation_id"), "activation_id"),
            idempotency_key=_text(args.get("idempotency_key"), "idempotency_key"),
            principal=principal,
        ).to_dict()
    raise ProjectDeploymentAuthorityError("authority_operation_not_supported")


_authority_lock = RLock()
_registered_runtime: ProjectDeploymentRuntime | None = None
_client_only = False


def register_project_deployment_authority(
    runtime: ProjectDeploymentRuntime | None,
    *,
    client_only: bool = False,
) -> None:
    global _registered_runtime, _client_only
    with _authority_lock:
        _registered_runtime = runtime
        _client_only = bool(client_only)


def _authority_runtime() -> ProjectDeploymentRuntime:
    with _authority_lock:
        runtime = _registered_runtime
        client_only = _client_only
    if runtime is not None:
        return runtime
    if client_only:
        raise ProjectDeploymentAuthorityError("deployment_authority_not_local")
    return get_project_deployment_runtime()


def execute_authority_request(value: Mapping[str, Any]) -> dict[str, Any]:
    request = _mapping(value, "request")
    if request.get("schema") != AUTHORITY_REQUEST_SCHEMA:
        raise ProjectDeploymentAuthorityError("authority_request_schema_invalid")
    operation = _text(request.get("operation"), "operation", max_length=80)
    if operation not in _OPERATIONS:
        raise ProjectDeploymentAuthorityError("authority_operation_not_supported")
    if runtime_transition_role() == "candidate":
        raise ProjectDeploymentAuthorityError("deployment_authority_candidate_passive")
    runtime = _authority_runtime()
    result = _execute(
        runtime,
        operation,
        _mapping(request.get("arguments") or {}, "arguments"),
        _principal_from_payload(request.get("principal")),
    )
    identity = runtime_identity_snapshot()
    return {
        "schema": AUTHORITY_RESPONSE_SCHEMA,
        "operation": operation,
        "authority": {
            "runtime_instance_id": identity.get("runtime_instance_id"),
            "transition_role": identity.get("transition_role"),
            "local_node_id": runtime.local_node_id,
        },
        "result": result,
    }


def _invoke_http(request: Mapping[str, Any]) -> dict[str, Any]:
    from adaos.apps.cli.active_control import (
        resolve_control_base_url,
        resolve_control_token,
    )

    explicit = str(os.getenv("ADAOS_PROJECT_DEPLOYMENT_AUTHORITY_URL") or "").strip()
    base_url = resolve_control_base_url(
        explicit=explicit or None, prefer_local=True
    ).rstrip("/")
    token = resolve_control_token(base_url=base_url)
    session = requests.Session()
    session.trust_env = False
    operation = str(request.get("operation") or "").strip()
    read_timeout = (
        600.0 if operation in {"apply", "reconcile", "drain", "remove"} else 30.0
    )
    try:
        response = session.post(
            base_url + "/api/node/project-deployment/authority",
            headers={"X-AdaOS-Token": token, "Accept": "application/json"},
            json=dict(request),
            timeout=(2.0, read_timeout),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise ProjectDeploymentAuthorityError(
            f"deployment_authority_unavailable:{type(exc).__name__}"
        ) from exc
    finally:
        session.close()
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema") != AUTHORITY_RESPONSE_SCHEMA
    ):
        raise ProjectDeploymentAuthorityError("deployment_authority_response_invalid")
    return dict(payload)


def invoke_project_deployment_authority(
    operation: str,
    arguments: Mapping[str, Any],
    *,
    principal: DeploymentPrincipal,
) -> Any:
    operation_name = _text(operation, "operation", max_length=80)
    if operation_name not in _OPERATIONS:
        raise ProjectDeploymentAuthorityError("authority_operation_not_supported")
    request = {
        "schema": AUTHORITY_REQUEST_SCHEMA,
        "operation": operation_name,
        "arguments": dict(arguments),
        "principal": _principal_payload(principal),
    }
    with _authority_lock:
        local = _registered_runtime is not None
        client_only = _client_only
    if local:
        return execute_authority_request(request)["result"]
    if client_only:
        return _invoke_http(request)["result"]
    try:
        return execute_authority_request(request)["result"]
    except RuntimeError as local_error:
        try:
            return _invoke_http(request)["result"]
        except ProjectDeploymentAuthorityError:
            raise ProjectDeploymentAuthorityError(
                "deployment_authority_unavailable"
            ) from local_error


__all__ = [
    "AUTHORITY_REQUEST_SCHEMA",
    "AUTHORITY_RESPONSE_SCHEMA",
    "ProjectDeploymentAuthorityError",
    "execute_authority_request",
    "invoke_project_deployment_authority",
    "register_project_deployment_authority",
]
