from __future__ import annotations

import time
from typing import Any, Mapping


PROMOTION_STATES = (
    "local_learned",
    "promotion_candidate",
    "promoted_to_workspace",
    "pushed_to_repo",
    "published",
    "rejected_for_publication",
)

PORTABILITY_CLASSES = (
    "session-local",
    "user-local",
    "workspace-local",
    "scenario-local",
    "skill-global",
    "system-global",
    "public-reusable",
)


def portability_for_target(target: Mapping[str, Any] | None) -> str:
    target_type = str((target or {}).get("type") or "").strip()
    if target_type in {"session", "route"}:
        return "session-local"
    if target_type == "user":
        return "user-local"
    if target_type == "scenario":
        return "scenario-local"
    if target_type == "skill":
        return "skill-global"
    if target_type in {"system", "system_action"}:
        return "system-global"
    if target_type == "public":
        return "public-reusable"
    return "workspace-local"


def promotion_record(
    target: Mapping[str, Any] | None,
    *,
    state: str = "local_learned",
    public_export_allowed: bool = False,
    privacy_gate: str = "operator_approval_required",
) -> dict[str, Any]:
    normalized_state = state if state in PROMOTION_STATES else "local_learned"
    return {
        "state": normalized_state,
        "portability": portability_for_target(target),
        "public_export_allowed": bool(public_export_allowed),
        "privacy_gate": privacy_gate,
    }


def privacy_record(
    *,
    retention_policy: str = "nlu.teacher.retention.v1",
    promotion_policy: str = "nlu.teacher.promotion.v1",
    raw_utterance_scope: str = "local_state",
    public_promotion_requires_review: bool = True,
) -> dict[str, Any]:
    return {
        "retention_policy": retention_policy,
        "promotion_policy": promotion_policy,
        "raw_utterance_scope": raw_utterance_scope,
        "public_promotion_requires_review": bool(public_promotion_requires_review),
    }


def _safe_scope_from_meta(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, Mapping):
        return {}
    allowed = ("webspace_id", "workspace_id", "route_id", "route", "subnet_id", "zone", "source")
    out: dict[str, Any] = {}
    for key in allowed:
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def provenance_record(
    *,
    source: str = "nlu_teacher",
    webspace_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    candidate_id: str | None = None,
    owner: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    operator_action: str | None = None,
    verification_result: Mapping[str, Any] | None = None,
    rollback_pointer: Mapping[str, Any] | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    prompt_hash: str | None = None,
    context_hash: str | None = None,
    meta: Mapping[str, Any] | None = None,
    accepted_at: float | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source": source,
        "mcp_bearer_embedded": False,
        "accepted_at": accepted_at if accepted_at is not None else time.time(),
    }
    for key, value in (
        ("webspace_id", webspace_id),
        ("request_id", request_id),
        ("thread_id", thread_id),
        ("candidate_id", candidate_id),
        ("operator_action", operator_action),
        ("model_id", model_id),
        ("model_version", model_version),
        ("prompt_hash", prompt_hash),
        ("context_hash", context_hash),
    ):
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    if isinstance(owner, Mapping) and owner:
        out["owner"] = dict(owner)
    if isinstance(target, Mapping) and target:
        out["target"] = dict(target)
    safe_scope = _safe_scope_from_meta(meta)
    if safe_scope:
        out["mcp_scope"] = safe_scope
    if isinstance(verification_result, Mapping) and verification_result:
        out["verification_result"] = dict(verification_result)
    if isinstance(rollback_pointer, Mapping) and rollback_pointer:
        out["rollback_pointer"] = dict(rollback_pointer)
    return out


def accepted_artifact_metadata(
    *,
    target: Mapping[str, Any] | None,
    source: str = "nlu_teacher",
    webspace_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    candidate_id: str | None = None,
    owner: Mapping[str, Any] | None = None,
    operator_action: str | None = None,
    verification_result: Mapping[str, Any] | None = None,
    rollback_pointer: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    accepted_at: float | None = None,
) -> dict[str, dict[str, Any]]:
    accepted_at = accepted_at if accepted_at is not None else time.time()
    return {
        "promotion": promotion_record(target),
        "provenance": provenance_record(
            source=source,
            webspace_id=webspace_id,
            request_id=request_id,
            thread_id=thread_id,
            candidate_id=candidate_id,
            owner=owner,
            target=target,
            operator_action=operator_action,
            verification_result=verification_result,
            rollback_pointer=rollback_pointer,
            meta=meta,
            accepted_at=accepted_at,
        ),
        "privacy": privacy_record(),
    }
