"""Provider-neutral Context Control Plane SDK.

The SDK calls the authoritative local service directly. Agent-facing MCP and
HTTP are adapters over the same contracts, not alternate persistence paths.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from adaos.services.context_control import ContextControlService


def _service() -> ContextControlService:
    return ContextControlService()


def register_capsule(value: Mapping[str, Any], *, bind: bool = False) -> dict[str, Any]:
    return _service().register_capsule(value, bind=bind)


def get_capsule(capsule_id: str, *, include_content: bool = False) -> dict[str, Any]:
    return _service().get_capsule(capsule_id, include_content=include_content)


def add_relationship(value: Mapping[str, Any]) -> dict[str, Any]:
    return _service().add_relationship(value)


def bind(
    subject_ref: str,
    capsule_id: str,
    *,
    purpose: str = "*",
    audience: str = "*",
    branch: str = "main",
    expected_revision: int | None = None,
    actor_ref: str = "sdk",
    reason: str = "updated",
) -> dict[str, Any]:
    return _service().bind_subject(
        subject_ref=subject_ref,
        capsule_id=capsule_id,
        purpose=purpose,
        audience=audience,
        branch=branch,
        expected_revision=expected_revision,
        actor_ref=actor_ref,
        reason=reason,
    )


def compare_bindings(
    subject_ref: str,
    *,
    purpose: str = "*",
    audience: str = "*",
    left_branch: str = "main",
    right_branch: str = "main",
) -> dict[str, Any]:
    return _service().compare_bindings(
        subject_ref=subject_ref,
        purpose=purpose,
        audience=audience,
        left_branch=left_branch,
        right_branch=right_branch,
    )


def merge_binding(
    subject_ref: str,
    *,
    source_branch: str,
    target_branch: str = "main",
    purpose: str = "*",
    audience: str = "*",
    base_capsule_id: str | None = None,
    expected_target_revision: int | None = None,
    actor_ref: str = "sdk",
    reason: str = "branch_merge",
) -> dict[str, Any]:
    return _service().merge_binding(
        subject_ref=subject_ref,
        source_branch=source_branch,
        target_branch=target_branch,
        purpose=purpose,
        audience=audience,
        base_capsule_id=base_capsule_id,
        expected_target_revision=expected_target_revision,
        actor_ref=actor_ref,
        reason=reason,
    )


def resolve(
    subject_refs: Sequence[str] | None = None,
    *,
    scope_ref: str | None = None,
    purpose: str = "general",
    audience: str = "agent",
    policy: Mapping[str, Any] | None = None,
    as_of: str | None = None,
    branch: str = "main",
) -> dict[str, Any]:
    return _service().resolve(
        {
            "subject_refs": list(subject_refs or []),
            "scope_ref": scope_ref,
            "purpose": purpose,
            "audience": audience,
            "policy": dict(policy or {}),
            "as_of": as_of,
            "branch": branch,
        }
    )


def plan(
    resolution: Mapping[str, Any],
    *,
    token_budget: int,
    model_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _service().plan(
        {
            "resolution": dict(resolution),
            "token_budget": token_budget,
            "model_profile": dict(model_profile or {}),
        }
    )


def compile(
    plan_value: Mapping[str, Any],
    *,
    output_format: str = "json",
    base_packet_ref: str | None = None,
    role_authority: Mapping[str, Any] | None = None,
    output_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _service().compile(
        {
            "plan": dict(plan_value),
            "output_format": output_format,
            "base_packet_ref": base_packet_ref,
            "role_authority": dict(role_authority or {}),
            "output_contract": dict(output_contract or {}),
        }
    )


def record_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    return _service().record_receipt(value)


def propose_memory(value: Mapping[str, Any]) -> dict[str, Any]:
    return _service().propose_memory(value)


def inspect(run_ref: str) -> dict[str, Any]:
    return _service().inspect(run_ref)


def invalidate(
    subject_ref: str,
    *,
    reason: str,
    event_ref: str,
    source_digest: str | None = None,
    edge_type: str | None = None,
) -> dict[str, Any]:
    return _service().invalidate(
        subject_ref=subject_ref,
        reason=reason,
        event_ref=event_ref,
        source_digest=source_digest,
        edge_type=edge_type,
    )


def list_invalidations(
    *,
    subject_ref: str | None = None,
    event_ref: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    return _service().list_invalidations(
        subject_ref=subject_ref,
        event_ref=event_ref,
        limit=limit,
    )


__all__ = [
    "add_relationship",
    "bind",
    "compare_bindings",
    "compile",
    "get_capsule",
    "inspect",
    "invalidate",
    "list_invalidations",
    "merge_binding",
    "plan",
    "propose_memory",
    "record_receipt",
    "register_capsule",
    "resolve",
]
