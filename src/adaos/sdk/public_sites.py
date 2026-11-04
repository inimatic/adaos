from __future__ import annotations

from typing import Any, Mapping

from adaos.sdk.data.root_mcp import call_local_root_mcp_tool


_PROFILE = "PublicSitesOperator"


def _call(
    tool_id: str,
    *,
    skill_id: str,
    arguments: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    payload = call_local_root_mcp_tool(
        tool_id,
        arguments=dict(arguments),
        capability_profile=_PROFILE,
        actor="skill:" + str(skill_id),
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
    if not isinstance(result, Mapping) or not isinstance(
        result.get("publication"), Mapping
    ):
        raise RuntimeError("public Site control operation returned invalid state")
    return dict(result["publication"])


def publication_status(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str,
    project_id: str,
) -> dict[str, Any]:
    return _call(
        "public_sites.status",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": webspace_id,
            "project_id": project_id,
        },
        timeout_seconds=30.0,
    )


def prepare_publication(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str,
    project_id: str,
    expected_revision_id: str,
    expected_source_digest: str,
) -> dict[str, Any]:
    return _call(
        "public_sites.prepare",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": webspace_id,
            "project_id": project_id,
            "expected_revision_id": expected_revision_id,
            "expected_source_digest": expected_source_digest,
        },
        timeout_seconds=300.0,
    )


def publish_candidate(
    *,
    scenario_id: str,
    skill_id: str,
    webspace_id: str,
    project_id: str,
    operation_id: str,
    plan_digest: str,
    expected_revision_id: str,
    expected_source_digest: str,
    confirmed: bool,
) -> dict[str, Any]:
    return _call(
        "public_sites.publish",
        skill_id=skill_id,
        arguments={
            "scenario_id": scenario_id,
            "skill_id": skill_id,
            "webspace_id": webspace_id,
            "project_id": project_id,
            "operation_id": operation_id,
            "plan_digest": plan_digest,
            "expected_revision_id": expected_revision_id,
            "expected_source_digest": expected_source_digest,
            "confirmed": confirmed,
        },
        timeout_seconds=300.0,
    )


__all__ = ["prepare_publication", "publication_status", "publish_candidate"]
