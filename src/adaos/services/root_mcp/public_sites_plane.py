from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from adaos.services.public_site_publication import public_site_publication_service
from adaos.services.site_studio_entitlements import (
    entitlement_scope_refs,
    site_studio_entitlement_service,
)

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
            "public_sites.entitlement.status",
            "Read effective Site Studio service entitlement and usage",
            identity,
            ["scenario_id", "skill_id", "webspace_id"],
            "public_sites.read",
            "none",
        ),
        (
            "public_sites.status",
            "Read public Site publication status",
            identity,
            ["scenario_id", "skill_id", "webspace_id", "project_id"],
            "public_sites.read",
            "none",
        ),
        (
            "public_sites.authoring.authorize",
            "Authorize and meter one centralized Site Studio authoring operation",
            {
                **identity,
                "operation": {
                    "enum": [
                        "project.create",
                        "revision.retain",
                        "asset.upload_bytes",
                        "compile",
                        "llm.request",
                    ]
                },
                "amount": {"type": "integer", "minimum": 1, "maximum": 1073741824},
                "resource_id": {"type": "string", "minLength": 1, "maxLength": 240},
            },
            [
                "scenario_id",
                "skill_id",
                "webspace_id",
                "operation",
            ],
            "public_sites.prepare",
            "write",
        ),
        (
            "public_sites.prepare",
            "Prepare an immutable public Site publication candidate",
            {
                **identity,
                "expected_revision_id": {"type": "string", "minLength": 1},
                "expected_source_digest": deepcopy(digest),
                "release_target": {
                    "enum": ["development", "production"]
                },
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
        (
            "public_sites.domains.status",
            "Read bounded public Site custom-domain status",
            identity,
            ["scenario_id", "skill_id", "webspace_id", "project_id"],
            "public_sites.read",
            "none",
        ),
        (
            "public_sites.domains.request",
            "Request validation for one external public Site domain",
            {
                **identity,
                "hostname": {
                    "type": "string",
                    "minLength": 4,
                    "maxLength": 253,
                    "pattern": "^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
                },
            },
            ["scenario_id", "skill_id", "webspace_id", "project_id", "hostname"],
            "public_sites.domains",
            "write",
        ),
        (
            "public_sites.domains.activate",
            "Activate one verified external public Site domain",
            {
                **identity,
                "hostname": {
                    "type": "string",
                    "minLength": 4,
                    "maxLength": 253,
                    "pattern": "^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
                },
                "confirmed": {"const": True},
            },
            [
                "scenario_id",
                "skill_id",
                "webspace_id",
                "project_id",
                "hostname",
                "confirmed",
            ],
            "public_sites.domains",
            "write",
        ),
        (
            "public_sites.components.status",
            "Read public Site component release and upgrade status",
            identity,
            ["scenario_id", "skill_id", "webspace_id", "project_id"],
            "public_sites.read",
            "none",
        ),
        (
            "public_sites.components.policy.set",
            "Set the owner component upgrade policy for one public Site",
            {
                **identity,
                "mode": {"enum": ["explicit_review", "compatible_auto"]},
            },
            ["scenario_id", "skill_id", "webspace_id", "project_id", "mode"],
            "public_sites.prepare",
            "write",
        ),
        (
            "public_sites.components.upgrade.prepare",
            "Prepare an owner-only component release-set upgrade preview",
            {
                **identity,
                "candidate_set_digest": deepcopy(digest),
            },
            ["scenario_id", "skill_id", "webspace_id", "project_id"],
            "public_sites.prepare",
            "write",
        ),
        (
            "public_sites.components.upgrade.preview.resolve",
            "Resolve one short-lived owner-bound component upgrade preview",
            {
                **identity,
                "review_id": {
                    "type": "string",
                    "pattern": "^psupgrade_[0-9a-f]{32}$",
                },
                "grant_id": {
                    "type": "string",
                    "pattern": "^pspreview_[0-9a-f]{32}$",
                },
                "token": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9_-]{40,64}$",
                },
            },
            [
                "scenario_id",
                "skill_id",
                "webspace_id",
                "project_id",
                "review_id",
                "grant_id",
                "token",
            ],
            "public_sites.prepare",
            "none",
        ),
        (
            "public_sites.components.upgrade.decide",
            "Approve or reject one exact component upgrade review",
            {
                **identity,
                "review_id": {
                    "type": "string",
                    "pattern": "^psupgrade_[0-9a-f]{32}$",
                },
                "plan_digest": deepcopy(digest),
                "decision": {"enum": ["approve", "reject"]},
                "confirmed": {"const": True},
            },
            [
                "scenario_id",
                "skill_id",
                "webspace_id",
                "project_id",
                "review_id",
                "plan_digest",
                "decision",
                "confirmed",
            ],
            "public_sites.prepare",
            "write",
        ),
        (
            "public_sites.components.upgrade.publish",
            "Publish one approved component release-set upgrade",
            {
                **identity,
                "operation_id": {
                    "type": "string",
                    "pattern": "^psupgrade_[0-9a-f]{32}$",
                },
                "plan_digest": deepcopy(digest),
                "confirmed": {"const": True},
            },
            [
                "scenario_id",
                "skill_id",
                "webspace_id",
                "project_id",
                "operation_id",
                "plan_digest",
                "confirmed",
            ],
            "public_sites.publish",
            "write",
        ),
        (
            "public_sites.rollback.prepare",
            "Prepare a complete predecessor deployment rollback",
            identity,
            ["scenario_id", "skill_id", "webspace_id", "project_id"],
            "public_sites.prepare",
            "write",
        ),
        (
            "public_sites.rollback.publish",
            "Publish one exact reviewed predecessor rollback",
            {
                **identity,
                "operation_id": {
                    "type": "string",
                    "pattern": "^psrollback_[0-9a-f]{32}$",
                },
                "plan_digest": deepcopy(digest),
                "confirmed": {"const": True},
            },
            [
                "scenario_id",
                "skill_id",
                "webspace_id",
                "project_id",
                "operation_id",
                "plan_digest",
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


def _request_context(
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    context = arguments.get("_mcp_context")
    context = dict(context) if isinstance(context, Mapping) else {}
    actor = str(context.get("actor") or "").strip()
    expected_actor = "skill:" + str(request.get("skill_id") or "").strip()
    if actor != expected_actor:
        raise ValueError("public Site operation must be called by its owning skill")
    return request, context


def _request(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return _request_context(arguments)[0]


def _entitlement(
    arguments: Mapping[str, Any],
    *,
    feature: str | None = None,
    operation: str = "status",
    consume: bool = False,
    resource_id: str | None = None,
    amount: int = 1,
) -> dict[str, Any]:
    request, context = _request_context(arguments)
    common = {
        "scope_refs": entitlement_scope_refs(context),
        "environment": "prod",
        "scenario_id": str(request.get("scenario_id") or ""),
        "skill_id": str(request.get("skill_id") or ""),
    }
    service = site_studio_entitlement_service()
    if feature is None:
        return service.status(**common)
    return service.authorize(
        **common,
        feature=feature,
        operation=operation,
        resource_id=resource_id,
        idempotency_key=str(context.get("request_id") or ""),
        consume=consume,
        amount=amount,
        actor=str(context.get("actor") or ""),
    )


def _entitlement_status(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    del dry_run
    return {"entitlement": _entitlement(arguments)}


def _status(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    del dry_run
    entitlement = _entitlement(arguments)
    publication = public_site_publication_service().status(**_request(arguments))
    publication["service_entitlement"] = entitlement
    publication["can_prepare"] = bool(
        publication.get("can_prepare")
        and entitlement["features"]["publication"]["status"] in {"available", "limited"}
    )
    publication["can_publish"] = bool(
        publication.get("can_publish")
        and entitlement["features"]["publication"]["status"] in {"available", "limited"}
    )
    return {"publication": publication}


def _authorize_authoring(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = _request(arguments)
    admission = _entitlement(
        arguments,
        feature="authoring",
        operation=str(request.get("operation") or ""),
        consume=not dry_run,
        amount=int(request.get("amount") or 1),
        resource_id=str(request.get("resource_id") or request.get("project_id") or "")
        or None,
    )
    return {"admission": admission}


def _prepare(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = _request(arguments)
    _entitlement(
        arguments,
        feature="publication",
        operation="prepare",
        consume=not dry_run,
        resource_id=str(request.get("project_id") or ""),
    )
    if dry_run:
        return {"would_prepare": True, "request": request}
    return {"publication": public_site_publication_service().prepare(**request)}


def _publish(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = _request(arguments)
    _entitlement(
        arguments,
        feature="publication",
        operation="publish",
        consume=not dry_run,
        resource_id=str(request.get("project_id") or ""),
    )
    if dry_run:
        return {
            "would_publish": True,
            "operation_id": request.get("operation_id"),
            "plan_digest": request.get("plan_digest"),
        }
    return {"publication": public_site_publication_service().publish(**request)}


def _domains(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    del dry_run
    entitlement = _entitlement(arguments)
    domains = public_site_publication_service().domains(**_request(arguments))
    domains["service_entitlement"] = entitlement
    return {"domains": domains}


def _request_domain(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = _request(arguments)
    _entitlement(
        arguments,
        feature="custom_domains",
        operation="domain.request",
        consume=not dry_run,
        resource_id=str(request.get("hostname") or "").lower(),
    )
    if dry_run:
        return {"would_request_domain": True, "hostname": request.get("hostname")}
    return {"domain": public_site_publication_service().request_domain(**request)}


def _activate_domain(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = _request(arguments)
    _entitlement(
        arguments,
        feature="custom_domains",
        operation="status",
    )
    if dry_run:
        return {"would_activate_domain": True, "hostname": request.get("hostname")}
    return {"domain": public_site_publication_service().activate_domain(**request)}


def _component_status(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    del dry_run
    _entitlement(arguments, feature="publication", operation="status")
    return {
        "components": public_site_publication_service().component_status(
            **_request(arguments)
        )
    }


def _set_component_policy(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = _request(arguments)
    _entitlement(arguments, feature="publication", operation="status")
    if dry_run:
        return {"would_set_component_policy": True, "mode": request.get("mode")}
    return {
        "components": public_site_publication_service().set_component_upgrade_policy(
            **request
        )
    }


def _prepare_component_upgrade(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = _request(arguments)
    _entitlement(
        arguments,
        feature="publication",
        operation="prepare",
        consume=not dry_run,
        resource_id=str(request.get("project_id") or ""),
    )
    if dry_run:
        return {"would_prepare_component_upgrade": True}
    return {
        "upgrade": public_site_publication_service().prepare_component_upgrade(
            **request
        )
    }


def _decide_component_upgrade(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = _request(arguments)
    _entitlement(
        arguments,
        feature="publication",
        operation="prepare",
        consume=not dry_run,
        resource_id=str(request.get("project_id") or ""),
    )
    if dry_run:
        return {
            "would_decide_component_upgrade": True,
            "decision": request.get("decision"),
        }
    return {
        "upgrade": public_site_publication_service().decide_component_upgrade(
            **request
        )
    }


def _resolve_component_upgrade_preview(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    del dry_run
    request = _request(arguments)
    _entitlement(arguments, feature="publication", operation="status")
    return {
        "upgrade": public_site_publication_service().resolve_component_upgrade_preview(
            **request
        )
    }


def _publish_component_upgrade(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = _request(arguments)
    _entitlement(
        arguments,
        feature="publication",
        operation="publish",
        consume=not dry_run,
        resource_id=str(request.get("project_id") or ""),
    )
    if dry_run:
        return {"would_publish_component_upgrade": True}
    return {
        "publication": public_site_publication_service().publish_component_upgrade(
            **request
        )
    }


def _prepare_rollback(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = _request(arguments)
    _entitlement(
        arguments,
        feature="publication",
        operation="prepare",
        consume=not dry_run,
        resource_id=str(request.get("project_id") or ""),
    )
    if dry_run:
        return {"would_prepare_rollback": True}
    return {
        "publication": public_site_publication_service().prepare_rollback(**request)
    }


def _publish_rollback(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = _request(arguments)
    _entitlement(
        arguments,
        feature="publication",
        operation="publish",
        consume=not dry_run,
        resource_id=str(request.get("project_id") or ""),
    )
    if dry_run:
        return {"would_publish_rollback": True}
    return {
        "publication": public_site_publication_service().publish_rollback(**request)
    }


def handlers() -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "public_sites.entitlement.status": _entitlement_status,
        "public_sites.status": _status,
        "public_sites.authoring.authorize": _authorize_authoring,
        "public_sites.prepare": _prepare,
        "public_sites.publish": _publish,
        "public_sites.domains.status": _domains,
        "public_sites.domains.request": _request_domain,
        "public_sites.domains.activate": _activate_domain,
        "public_sites.components.status": _component_status,
        "public_sites.components.policy.set": _set_component_policy,
        "public_sites.components.upgrade.prepare": _prepare_component_upgrade,
        "public_sites.components.upgrade.preview.resolve": _resolve_component_upgrade_preview,
        "public_sites.components.upgrade.decide": _decide_component_upgrade,
        "public_sites.components.upgrade.publish": _publish_component_upgrade,
        "public_sites.rollback.prepare": _prepare_rollback,
        "public_sites.rollback.publish": _publish_rollback,
    }


__all__ = ["contracts", "handlers", "local_tool_capabilities"]
