from __future__ import annotations

from typing import Any, Mapping

from adaos.sdk import access
from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.core.contracts import public_contract
from adaos.sdk.data.root_mcp import call_local_root_mcp_tool


_PROFILE = "PublicSitesOperator"


def _bound_skill_id(requested: str) -> str:
    current = require_ctx().skill_ctx.get()
    current_id = str(getattr(current, "name", "") or "").strip()
    requested_id = str(requested or "").strip()
    if not current_id or requested_id != current_id:
        raise PermissionError("public Site control must target the current skill")
    return current_id


def _bound_webspace_id(requested: str | None) -> str:
    invocation = access.invocation() or {}
    current_id = str(invocation.get("webspace_id") or "").strip()
    requested_id = str(requested or "").strip()
    if not current_id or (requested_id and requested_id != current_id):
        raise PermissionError("public Site control requires the addressed webspace")
    return current_id


def _call(
    tool_id: str,
    *,
    skill_id: str,
    arguments: Mapping[str, Any],
    timeout_seconds: float,
    result_key: str = "publication",
) -> dict[str, Any]:
    bound_skill_id = _bound_skill_id(skill_id)
    payload = call_local_root_mcp_tool(
        tool_id,
        arguments=dict(arguments),
        capability_profile=_PROFILE,
        actor="skill:" + bound_skill_id,
        timeout_seconds=timeout_seconds,
    )
    envelope = payload.get("response") if isinstance(payload, Mapping) else None
    if not isinstance(envelope, Mapping):
        envelope = payload
    if not isinstance(envelope, Mapping) or envelope.get("ok") is not True:
        error = envelope.get("error") if isinstance(envelope, Mapping) else None
        message = error.get("message") if isinstance(error, Mapping) else None
        raise RuntimeError(str(message or "public Site control operation failed"))
    result = envelope.get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get(result_key), Mapping):
        raise RuntimeError("public Site control operation returned invalid state")
    return dict(result[result_key])


@public_contract(
    capabilities=("public_sites.read",),
    effects=("root_control_plane_read",),
    errors=("permission_denied", "selected_trial_missing", "publication_state_invalid"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def publication_status(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
) -> dict[str, Any]:
    """Return bounded active, published, and prepared state for one public Site."""
    return _call(
        "public_sites.status",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
        },
        timeout_seconds=30.0,
    )


@public_contract(
    capabilities=("public_sites.read",),
    effects=("root_control_plane_read",),
    errors=("permission_denied", "entitlement_state_invalid"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def entitlement_status(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    """Return Root-owned Site Studio feature availability, usage, and limits."""
    return _call(
        "public_sites.entitlement.status",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
        },
        timeout_seconds=30.0,
        result_key="entitlement",
    )


@public_contract(
    capabilities=("public_sites.prepare",),
    effects=("root_control_plane_write", "quota_consume"),
    errors=("permission_denied", "entitlement_state_invalid", "quota_exhausted"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def authorize_authoring(
    *,
    scenario_id: str,
    skill_id: str,
    operation: str,
    webspace_id: str | None = None,
    project_id: str | None = None,
    amount: int = 1,
    resource_id: str | None = None,
) -> dict[str, Any]:
    """Authorize and meter one Root-governed Site Studio authoring operation."""
    arguments: dict[str, Any] = {
        "scenario_id": scenario_id,
        "skill_id": skill_id,
        "webspace_id": _bound_webspace_id(webspace_id),
        "operation": operation,
        "amount": amount,
    }
    if project_id:
        arguments["project_id"] = project_id
    if resource_id:
        arguments["resource_id"] = resource_id
    return _call(
        "public_sites.authoring.authorize",
        skill_id=skill_id,
        arguments=arguments,
        timeout_seconds=30.0,
        result_key="admission",
    )


@public_contract(
    capabilities=("public_sites.prepare",),
    effects=("durable_write", "provider_plan"),
    errors=("permission_denied", "revision_conflict", "provider_plan_failed"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def prepare_publication(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
    expected_revision_id: str,
    expected_source_digest: str,
    release_target: str = "production",
) -> dict[str, Any]:
    """Prepare an immutable candidate for the exact observed Site revision."""
    return _call(
        "public_sites.prepare",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
            "expected_revision_id": expected_revision_id,
            "expected_source_digest": expected_source_digest,
            "release_target": release_target,
        },
        timeout_seconds=300.0,
    )


@public_contract(
    capabilities=("public_sites.publish",),
    effects=("durable_write", "external_publish"),
    errors=(
        "permission_denied",
        "confirmation_required",
        "revision_conflict",
        "candidate_stale",
        "provider_publish_failed",
    ),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def publish_candidate(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
    operation_id: str,
    plan_digest: str,
    expected_revision_id: str,
    expected_source_digest: str,
    confirmed: bool,
) -> dict[str, Any]:
    """Publish one exact reviewed candidate after explicit owner confirmation."""
    return _call(
        "public_sites.publish",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
            "operation_id": operation_id,
            "plan_digest": plan_digest,
            "expected_revision_id": expected_revision_id,
            "expected_source_digest": expected_source_digest,
            "confirmed": confirmed,
        },
        timeout_seconds=300.0,
    )


@public_contract(
    capabilities=("public_sites.read",),
    effects=("root_control_plane_read",),
    errors=("permission_denied", "selected_trial_missing", "domain_state_invalid"),
    boundedness={"kind": "collection", "max_items": 20},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def domain_statuses(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
) -> dict[str, Any]:
    """Return bounded custom-domain state for the current public Site."""
    return _call(
        "public_sites.domains.status",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
        },
        timeout_seconds=30.0,
        result_key="domains",
    )


@public_contract(
    capabilities=("public_sites.domains",),
    effects=("durable_write", "external_domain_request"),
    errors=("permission_denied", "domain_invalid", "domain_conflict", "provider_failed"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def request_domain(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
    hostname: str,
) -> dict[str, Any]:
    """Request ownership and certificate validation for one external domain."""
    return _call(
        "public_sites.domains.request",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
            "hostname": hostname,
        },
        timeout_seconds=60.0,
        result_key="domain",
    )


@public_contract(
    capabilities=("public_sites.domains",),
    effects=("durable_write", "external_domain_activation"),
    errors=(
        "permission_denied",
        "confirmation_required",
        "domain_unverified",
        "provider_failed",
    ),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def activate_domain(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
    hostname: str,
    confirmed: bool,
) -> dict[str, Any]:
    """Activate one verified external domain after explicit owner confirmation."""
    return _call(
        "public_sites.domains.activate",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
            "hostname": hostname,
            "confirmed": confirmed,
        },
        timeout_seconds=60.0,
        result_key="domain",
    )


@public_contract(
    capabilities=("public_sites.read",),
    effects=("root_control_plane_read",),
    errors=("permission_denied", "component_state_invalid"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def component_status(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
) -> dict[str, Any]:
    """Return pinned/recommended component sets, findings, history, and rollback."""
    return _call(
        "public_sites.components.status",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
        },
        timeout_seconds=30.0,
        result_key="components",
    )


@public_contract(
    capabilities=("public_sites.prepare",),
    effects=("durable_write",),
    errors=("permission_denied", "component_state_invalid"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def set_component_upgrade_policy(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
    mode: str,
) -> dict[str, Any]:
    """Select explicit review or bounded compatible-update policy for one site."""
    return _call(
        "public_sites.components.policy.set",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
            "mode": mode,
        },
        timeout_seconds=30.0,
        result_key="components",
    )


@public_contract(
    capabilities=("public_sites.prepare",),
    effects=("durable_write", "private_preview"),
    errors=("permission_denied", "component_state_invalid", "qualification_required"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def prepare_component_upgrade(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
    candidate_set_digest: str | None = None,
) -> dict[str, Any]:
    """Prepare an owner-only current-versus-candidate component preview."""
    arguments = {
        "scenario_id": scenario_id,
        "skill_id": skill_id,
        "webspace_id": _bound_webspace_id(webspace_id),
        "project_id": project_id,
    }
    if candidate_set_digest:
        arguments["candidate_set_digest"] = candidate_set_digest
    return _call(
        "public_sites.components.upgrade.prepare",
        skill_id=skill_id,
        arguments=arguments,
        timeout_seconds=60.0,
        result_key="upgrade",
    )


@public_contract(
    capabilities=("public_sites.prepare",),
    effects=("durable_write", "provider_plan"),
    errors=("permission_denied", "confirmation_required", "candidate_stale"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def decide_component_upgrade(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
    review_id: str,
    plan_digest: str,
    decision: str,
    confirmed: bool,
) -> dict[str, Any]:
    """Approve or reject one exact reviewed component candidate."""
    return _call(
        "public_sites.components.upgrade.decide",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
            "review_id": review_id,
            "plan_digest": plan_digest,
            "decision": decision,
            "confirmed": confirmed,
        },
        timeout_seconds=300.0,
        result_key="upgrade",
    )


@public_contract(
    capabilities=("public_sites.publish",),
    effects=("durable_write", "external_publish"),
    errors=("permission_denied", "confirmation_required", "candidate_stale"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def publish_component_upgrade(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
    operation_id: str,
    plan_digest: str,
    confirmed: bool,
) -> dict[str, Any]:
    """Move the site pointer to one approved component deployment."""
    return _call(
        "public_sites.components.upgrade.publish",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
            "operation_id": operation_id,
            "plan_digest": plan_digest,
            "confirmed": confirmed,
        },
        timeout_seconds=300.0,
    )


@public_contract(
    capabilities=("public_sites.prepare",),
    effects=("durable_write", "provider_plan"),
    errors=("permission_denied", "rollback_unavailable", "provider_plan_failed"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def prepare_rollback(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
) -> dict[str, Any]:
    """Prepare a complete rollback to the retained predecessor deployment."""
    return _call(
        "public_sites.rollback.prepare",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
        },
        timeout_seconds=300.0,
    )


@public_contract(
    capabilities=("public_sites.publish",),
    effects=("durable_write", "external_publish"),
    errors=("permission_denied", "confirmation_required", "candidate_stale"),
    boundedness={"kind": "single_result"},
    pagination={"supported": False, "arguments": []},
    stability="beta",
    since="1.5.0",
)
def publish_rollback(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str | None = None,
    project_id: str,
    operation_id: str,
    plan_digest: str,
    confirmed: bool,
) -> dict[str, Any]:
    """Publish one reviewed predecessor deployment as a new pointer generation."""
    return _call(
        "public_sites.rollback.publish",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": _bound_webspace_id(webspace_id),
            "project_id": project_id,
            "operation_id": operation_id,
            "plan_digest": plan_digest,
            "confirmed": confirmed,
        },
        timeout_seconds=300.0,
    )


__all__ = [
    "activate_domain",
    "authorize_authoring",
    "component_status",
    "decide_component_upgrade",
    "domain_statuses",
    "entitlement_status",
    "prepare_component_upgrade",
    "prepare_publication",
    "prepare_rollback",
    "publication_status",
    "publish_candidate",
    "publish_component_upgrade",
    "publish_rollback",
    "request_domain",
    "set_component_upgrade_policy",
]
