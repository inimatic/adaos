from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from adaos.services.public_site_publication import public_site_publication_service

from .model import (
    ROOT_MCP_RESPONSE_SCHEMA,
    RootMcpSurface,
    RootMcpToolContract,
    schema_object,
)


def _identity_properties() -> dict[str, Any]:
    identifier = {
        "type": "string",
        "minLength": 1,
        "maxLength": 180,
        "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    }
    return {
        "scenario_id": deepcopy(identifier),
        "skill_id": deepcopy(identifier),
        "webspace_id": deepcopy(identifier),
        "project_id": deepcopy(identifier),
    }


def contracts() -> list[RootMcpToolContract]:
    identity = _identity_properties()
    digest = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
    specs = (
        (
            "public_sites.status",
            "Read public Site publication status",
            identity,
            ["scenario_id", "skill_id", "webspace_id", "project_id"],
            "public_sites.read",
            "none",
        ),
        (
            "public_sites.prepare",
            "Prepare an immutable public Site publication candidate",
            {
                **identity,
                "expected_revision_id": {"type": "string", "minLength": 1},
                "expected_source_digest": deepcopy(digest),
            },
            [
                "scenario_id",
                "skill_id",
                "webspace_id",
                "project_id",
                "expected_revision_id",
                "expected_source_digest",
            ],
            "public_sites.prepare",
            "write",
        ),
        (
            "public_sites.publish",
            "Publish one exact reviewed public Site candidate",
            {
                **identity,
                "operation_id": {
                    "type": "string",
                    "pattern": "^pspub_[0-9a-f]{32}$",
                },
                "plan_digest": deepcopy(digest),
                "expected_revision_id": {"type": "string", "minLength": 1},
                "expected_source_digest": deepcopy(digest),
                "confirmed": {"const": True},
            },
            [
                "scenario_id",
                "skill_id",
                "webspace_id",
                "project_id",
                "operation_id",
                "plan_digest",
                "expected_revision_id",
                "expected_source_digest",
                "confirmed",
            ],
            "public_sites.publish",
            "write",
        ),
    )
    return [
        RootMcpToolContract(
            id=tool_id,
            title=title,
            surface=RootMcpSurface.OPERATIONS,
            summary=(
                title
                + "; Core resolves the selected Trial and retains provider credentials."
            ),
            input_schema=schema_object(properties=properties, required=required),
            output_schema=deepcopy(ROOT_MCP_RESPONSE_SCHEMA),
            required_capability=capability,
            side_effects=side_effects,
            stability="beta",
            metadata={
                "published_by": "plane:public_sites",
                "adapter": "adaos.sdk.public_sites",
                "handler": tool_id.replace(".", "_"),
            },
        )
        for tool_id, title, properties, required, capability, side_effects in specs
    ]


def local_tool_capabilities() -> dict[str, str]:
    return {contract.id: str(contract.required_capability) for contract in contracts()}


def _request(arguments: Mapping[str, Any]) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    context = arguments.get("_mcp_context")
    context = dict(context) if isinstance(context, Mapping) else {}
    actor = str(context.get("actor") or "").strip()
    expected_actor = "skill:" + str(request.get("skill_id") or "").strip()
    if actor != expected_actor:
        raise ValueError("public Site operation must be called by its owning skill")
    return request


def _status(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    del dry_run
    return {"publication": public_site_publication_service().status(**_request(arguments))}


def _prepare(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = _request(arguments)
    if dry_run:
        return {"would_prepare": True, "request": request}
    return {"publication": public_site_publication_service().prepare(**request)}


def _publish(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = _request(arguments)
    if dry_run:
        return {
            "would_publish": True,
            "operation_id": request.get("operation_id"),
            "plan_digest": request.get("plan_digest"),
        }
    return {"publication": public_site_publication_service().publish(**request)}


def handlers() -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "public_sites.status": _status,
        "public_sites.prepare": _prepare,
        "public_sites.publish": _publish,
    }


__all__ = ["contracts", "handlers", "local_tool_capabilities"]
