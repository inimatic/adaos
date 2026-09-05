from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from .model import ROOT_MCP_RESPONSE_SCHEMA, RootMcpSurface, RootMcpToolContract, schema_object


def _sdk():
    from adaos.sdk import applications

    return applications


def contracts() -> list[RootMcpToolContract]:
    response = lambda: deepcopy(ROOT_MCP_RESPONSE_SCHEMA)
    published = {"published_by": "plane:applications", "adapter": "adaos.sdk.applications"}
    identity = {
        "application_id": {"type": "string"},
        "expected_revision": {"type": "integer", "minimum": 0},
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
    }
    return [
        RootMcpToolContract(
            id="applications.list",
            title="List Applications",
            surface=RootMcpSurface.OPERATIONS,
            summary="List bounded installed and Catalog Application models.",
            input_schema=schema_object(properties={"installed_only": {"type": "boolean"}}),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_list"},
        ),
        RootMcpToolContract(
            id="applications.show",
            title="Show Application",
            surface=RootMcpSurface.OPERATIONS,
            summary="Read one Application with exact installation, channel, and operation state.",
            input_schema=schema_object(properties={"application_id": {"type": "string"}}, required=["application_id"]),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_show"},
        ),
        RootMcpToolContract(
            id="applications.list_releases",
            title="List Application releases",
            surface=RootMcpSurface.OPERATIONS,
            summary="List immutable releases and effective channel bindings for one Application.",
            input_schema=schema_object(properties={"application_id": {"type": "string"}}, required=["application_id"]),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_list_releases"},
        ),
        RootMcpToolContract(
            id="applications.list_operations",
            title="List Application operations",
            surface=RootMcpSurface.OPERATIONS,
            summary="Poll durable Application operations after reconnect.",
            input_schema=schema_object(properties={"application_id": {"type": "string"}}),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_list_operations"},
        ),
        RootMcpToolContract(
            id="applications.get_operation",
            title="Get Application operation",
            surface=RootMcpSurface.OPERATIONS,
            summary="Read one durable operation and structured recovery reason.",
            input_schema=schema_object(properties={"operation_id": {"type": "string"}}, required=["operation_id"]),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_get_operation"},
        ),
        RootMcpToolContract(
            id="applications.plan",
            title="Plan Application mutation",
            surface=RootMcpSurface.OPERATIONS,
            summary="Persist a reviewable install, update, remove, or update-track plan without applying it.",
            input_schema=schema_object(
                properties={
                    **identity,
                    "kind": {"enum": ["install", "update", "remove", "select_track"]},
                    "release_digest": {"type": ["string", "null"]},
                    "data_policy": {"enum": ["retain", "delete", "snapshot_then_delete"]},
                    "update_track": {"enum": ["stable", "prerelease"]},
                    "update_policy": {"enum": ["notify", "auto_compatible", "pinned"]},
                    "paused": {"type": "boolean"},
                    "pinned_release_digest": {"type": ["string", "null"]},
                },
                required=["application_id", "kind", "expected_revision", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.plan",
            side_effects="write",
            metadata={**published, "handler": "applications_plan"},
        ),
        RootMcpToolContract(
            id="applications.apply",
            title="Apply reviewed Application plan",
            surface=RootMcpSurface.OPERATIONS,
            summary="Apply one exact reviewed plan and return its durable operation receipt.",
            input_schema=schema_object(
                properties={
                    "operation_id": {"type": "string"},
                    "plan_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                required=["operation_id", "plan_digest", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.apply",
            side_effects="write",
            metadata={**published, "handler": "applications_apply"},
        ),
        RootMcpToolContract(
            id="applications.explain_plan",
            title="Explain Application plan",
            surface=RootMcpSurface.OPERATIONS,
            summary="Explain exact release, compatibility, snapshot, removal, and conflict decisions.",
            input_schema=schema_object(properties={"operation_id": {"type": "string"}}, required=["operation_id"]),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_explain_plan"},
        ),
    ]


def _context(arguments: Mapping[str, Any]) -> tuple[str, str]:
    raw = arguments.get("_mcp_context")
    context = dict(raw) if isinstance(raw, Mapping) else {}
    scope = context.get("scope") if isinstance(context.get("scope"), Mapping) else {}
    auth = context.get("auth_context") if isinstance(context.get("auth_context"), Mapping) else {}
    actor = str(context.get("actor") or auth.get("actor") or "").strip()
    subnet_id = str(scope.get("subnet_id") or auth.get("subnet_id") or "").strip()
    if not actor:
        raise ValueError("MCP actor context is required")
    if not subnet_id:
        raise ValueError("MCP subnet context is required")
    subnet_ref = subnet_id if subnet_id.startswith("subnet:") else f"subnet:{subnet_id}"
    return actor, subnet_ref


def _application_id(arguments: Mapping[str, Any]) -> str:
    value = str(arguments.get("application_id") or "").strip()
    if not value:
        raise ValueError("application_id is required")
    return value


def _handle_list(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {"applications": _sdk().list_applications(installed_only=bool(arguments.get("installed_only", False)))}


def _handle_show(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {"application": _sdk().get_application(_application_id(arguments))}


def _handle_releases(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {"releases": _sdk().list_releases(_application_id(arguments))}


def _handle_operations(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    application_id = str(arguments.get("application_id") or "").strip() or None
    return {"operations": _sdk().list_operations(application_id)}


def _handle_get_operation(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    operation_id = str(arguments.get("operation_id") or "").strip()
    if not operation_id:
        raise ValueError("operation_id is required")
    return {"operation": _sdk().get_operation(operation_id)}


def _handle_plan(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_plan": True, "request": request}
    actor_ref, subnet_ref = _context(arguments)
    application_id = _application_id(arguments)
    kind = str(arguments.get("kind") or "").strip()
    common = {
        "expected_revision": int(arguments.get("expected_revision") or 0),
        "actor_ref": actor_ref,
        "subnet_ref": subnet_ref,
        "idempotency_key": str(arguments.get("idempotency_key") or "").strip(),
    }
    sdk = _sdk()
    if kind == "install":
        operation = sdk.plan_install(
            application_id,
            release_digest=str(arguments.get("release_digest") or "").strip() or None,
            data_policy=str(arguments.get("data_policy") or "retain"),
            **common,
        )
    elif kind == "update":
        operation = sdk.plan_update(
            application_id,
            release_digest=str(arguments.get("release_digest") or "").strip() or None,
            **common,
        )
    elif kind == "remove":
        operation = sdk.plan_remove(
            application_id,
            data_policy=str(arguments.get("data_policy") or "retain"),
            **common,
        )
    elif kind == "select_track":
        operation = sdk.plan_update_track(
            application_id,
            update_track=str(arguments.get("update_track") or "stable"),
            update_policy=str(arguments.get("update_policy") or "notify"),
            paused=bool(arguments.get("paused", False)),
            pinned_release_digest=str(arguments.get("pinned_release_digest") or "").strip() or None,
            **common,
        )
    else:
        raise ValueError("kind must be install, update, remove, or select_track")
    return {"operation": operation}


def _handle_apply(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_apply": True, "request": request}
    return {
        "operation": _sdk().apply_operation(
            str(arguments.get("operation_id") or ""),
            plan_digest=str(arguments.get("plan_digest") or ""),
            idempotency_key=str(arguments.get("idempotency_key") or ""),
        )
    }


def _handle_explain(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    operation_id = str(arguments.get("operation_id") or "").strip()
    if not operation_id:
        raise ValueError("operation_id is required")
    return {"explanation": _sdk().explain_plan(operation_id)}


def handlers() -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "applications.list": _handle_list,
        "applications.show": _handle_show,
        "applications.list_releases": _handle_releases,
        "applications.list_operations": _handle_operations,
        "applications.get_operation": _handle_get_operation,
        "applications.plan": _handle_plan,
        "applications.apply": _handle_apply,
        "applications.explain_plan": _handle_explain,
    }


__all__ = ["contracts", "handlers"]
