from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from adaos.services.site_studio_entitlements import (
    DEFAULT_LIMITS,
    FEATURES,
    site_studio_entitlement_service,
)

from .model import (
    ROOT_MCP_RESPONSE_SCHEMA,
    RootMcpSurface,
    RootMcpToolContract,
    RootMcpToolError,
    schema_object,
)


def contracts() -> list[RootMcpToolContract]:
    identifier = {
        "type": "string",
        "minLength": 1,
        "maxLength": 240,
        "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    }
    specs = (
        (
            "site_studio.entitlements.list",
            "List Root-owned Site Studio service entitlements",
            {},
            [],
            "site_studio.entitlements.read",
            "none",
        ),
        (
            "site_studio.entitlements.grant",
            "Grant one Root-owned Site Studio service entitlement",
            {
                "scope_kind": {"enum": ["subject", "organization"]},
                "scope_id": deepcopy(identifier),
                "environment": deepcopy(identifier),
                "application_id": deepcopy(identifier),
                "scenario_id": deepcopy(identifier),
                "skill_id": deepcopy(identifier),
                "features": {
                    "type": "array",
                    "items": {"enum": list(FEATURES)},
                    "minItems": 1,
                    "uniqueItems": True,
                },
                "effective_from": {"type": "string", "format": "date-time"},
                "effective_until": {"type": "string", "format": "date-time"},
                "limits": {
                    "type": "object",
                    "properties": {
                        key: {"type": "integer", "minimum": 1}
                        for key in DEFAULT_LIMITS
                    },
                    "additionalProperties": False,
                },
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            [
                "scope_kind",
                "scope_id",
                "environment",
                "scenario_id",
                "skill_id",
                "features",
                "reason",
            ],
            "site_studio.entitlements.manage",
            "write",
        ),
        (
            "site_studio.entitlements.set_status",
            "Suspend or revoke one Site Studio service entitlement",
            {
                "entitlement_id": deepcopy(identifier),
                "status": {"enum": ["active", "suspended", "revoked"]},
                "expected_revision": {"type": "integer", "minimum": 1},
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            ["entitlement_id", "status", "expected_revision", "reason"],
            "site_studio.entitlements.manage",
            "write",
        ),
        (
            "site_studio.entitlements.set_switch",
            "Set a Root-owned Site Studio emergency or abuse switch",
            {
                "feature": {"enum": list(FEATURES)},
                "status": {
                    "enum": ["available", "limited", "suspended", "unavailable"]
                },
                "reason_code": deepcopy(identifier),
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            ["feature", "status", "reason_code", "reason"],
            "site_studio.entitlements.manage",
            "write",
        ),
    )
    return [
        RootMcpToolContract(
            id=tool_id,
            title=title,
            surface=RootMcpSurface.OPERATIONS,
            summary=title + "; this authority is independent from Application permissions and update subscriptions.",
            input_schema=schema_object(properties=properties, required=required),
            output_schema=deepcopy(ROOT_MCP_RESPONSE_SCHEMA),
            required_capability=capability,
            side_effects=side_effects,
            stability="beta",
            metadata={
                "published_by": "plane:site_studio_entitlements",
                "handler": tool_id.replace(".", "_"),
            },
        )
        for tool_id, title, properties, required, capability, side_effects in specs
    ]


def local_tool_capabilities() -> dict[str, str]:
    return {contract.id: str(contract.required_capability) for contract in contracts()}


def _admin_context(arguments: Mapping[str, Any]) -> tuple[str, str | None]:
    raw = arguments.get("_mcp_context")
    context = dict(raw) if isinstance(raw, Mapping) else {}
    actor = str(context.get("actor") or "").strip()
    if not actor or actor.startswith("skill:"):
        raise RootMcpToolError(
            "site_studio_entitlement_admin_required",
            "Site Studio entitlement administration requires an owner actor.",
        )
    return actor, str(context.get("request_id") or "").strip() or None


def _list(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    del dry_run
    _admin_context(arguments)
    return {"entitlements": site_studio_entitlement_service().list_entitlements()}


def _grant(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    actor, request_id = _admin_context(arguments)
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_grant": True, "binding": request}
    return {
        "entitlement": site_studio_entitlement_service().grant(
            **request, actor=actor, request_id=request_id
        )
    }


def _set_status(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    actor, request_id = _admin_context(arguments)
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_set_status": True, "change": request}
    entitlement_id = str(request.pop("entitlement_id"))
    return {
        "entitlement": site_studio_entitlement_service().set_status(
            entitlement_id, **request, actor=actor, request_id=request_id
        )
    }


def _set_switch(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    actor, request_id = _admin_context(arguments)
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_set_switch": True, "change": request}
    feature = str(request.pop("feature"))
    return {
        "switch": site_studio_entitlement_service().set_switch(
            feature, **request, actor=actor, request_id=request_id
        )
    }


def handlers() -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "site_studio.entitlements.list": _list,
        "site_studio.entitlements.grant": _grant,
        "site_studio.entitlements.set_status": _set_status,
        "site_studio.entitlements.set_switch": _set_switch,
    }


__all__ = ["contracts", "handlers", "local_tool_capabilities"]
