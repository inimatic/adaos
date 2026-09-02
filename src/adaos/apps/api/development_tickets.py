from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.builder.repair import BuilderRepairService
from adaos.services.builder.workspace import BuilderSourceRecoveryRequired
from adaos.services.development_tickets import (
    DevelopmentTicketService,
    development_source_options,
    project_development_ticket_summary,
)
from adaos.services.id_gen import new_id


router = APIRouter(tags=["development-tickets"], dependencies=[Depends(require_token)])


def _get_service() -> DevelopmentTicketService:
    return DevelopmentTicketService()


def _repair_service_for(service: DevelopmentTicketService) -> BuilderRepairService:
    return BuilderRepairService(state_dir=service.state_dir)


def _get_automation_service() -> Any:
    from adaos.services.builder.automation import BuilderAutomationService

    return BuilderAutomationService.from_context()


def _source_recovery_required(exc: BuilderSourceRecoveryRequired) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "source_recovery_required",
            "message": str(exc),
            "plan": exc.plan,
            "allowed_source_strategies": ["create_local_fork", "defer"],
        },
    )


class DevTicketCreateRequest(BaseModel):
    summary: str = Field(..., min_length=1)
    kind: str = "development_request"
    ticket_kind: str | None = None
    signal_kind: str | None = None
    target_scope: dict[str, Any] = Field(default_factory=lambda: {"type": "unknown"})
    owner_scope: dict[str, Any] | None = None
    origin_scope: dict[str, Any] | None = None
    severity: str = "medium"
    blocking: bool = False
    owner_area: str | None = None
    component_ref: str | None = None
    source: str = "ui_feedback"
    status: str = "proposed"
    dedup_key: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    relation_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)


class DevTicketArtifactUploadRequest(BaseModel):
    kind: str = "screenshot"
    content_type: str = "image/png"
    content_base64: str = Field(..., min_length=1, max_length=8 * 1024 * 1024)
    filename: str | None = None
    origin_scope: dict[str, Any] = Field(default_factory=dict)
    target_scope: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DevTicketUpdateRequest(BaseModel):
    summary: str | None = Field(default=None, min_length=1)
    actor: str = Field(default="ui", min_length=1)
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketBuilderQualificationRequest(BaseModel):
    builder_repair: dict[str, Any]
    reason: str = Field(..., min_length=1)
    actor: str = Field(default="builder", min_length=1)
    expected_updated_at: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketBuilderQualificationPrepareRequest(BaseModel):
    apply: bool = False
    actor: str = Field(default="builder.qualifier", min_length=1)
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketResponseRequest(BaseModel):
    response_action_id: str = Field(..., min_length=1)
    pending_action_id: str | None = None
    responder: dict[str, Any] = Field(default_factory=lambda: {"id": "ui"})
    payload: dict[str, Any] = Field(default_factory=dict)


class DevTicketDeferRequest(BaseModel):
    reason: str = ""
    actor: str = "ui"
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketHandoffRequest(BaseModel):
    mode: str = Field(default="interactive", pattern="^(interactive|autonomous)$")
    actor: str = "ui"


class DevTicketAutonomousRepairRequest(BaseModel):
    actor: str = "ui"
    webspace_id: str = "desktop"
    conversation_id: str | None = None
    source_strategy: str | None = Field(
        default=None,
        pattern="^(materialize_dev_source|create_local_fork|defer)$",
    )
    execution_budget: dict[str, Any] | None = None
    agent_profile: dict[str, Any] | None = None
    mcp: dict[str, Any] | None = None


class DevTicketPackagePlanRequest(BaseModel):
    ticket_ids: list[str] = Field(..., min_length=1, max_length=12)
    actor: str = Field(default="builder", min_length=1)
    execution_budget: dict[str, Any] | None = None
    source_strategy: str | None = Field(
        default=None,
        pattern="^(materialize_dev_source|create_local_fork)$",
    )


class DevTicketPackageStartRequest(BaseModel):
    actor: str = Field(default="builder", min_length=1)
    webspace_id: str = "desktop"
    conversation_id: str | None = None
    source_strategy: str | None = Field(
        default=None,
        pattern="^(materialize_dev_source|create_local_fork)$",
    )
    agent_profile: dict[str, Any] | None = None
    mcp: dict[str, Any] | None = None


class DevTicketTrialDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(accept|revise|rollback)$")
    actor: str = Field(default="user:owner", min_length=1)
    reason: str = ""
    expected_candidate_id: str = Field(..., min_length=1)
    expected_candidate_digest: str = Field(..., pattern="^sha256:[0-9a-f]{64}$")


class DevTicketBuilderSyncRequest(BaseModel):
    actor: str = "ui"
    repair_id: str | None = None


class DevTicketResolveRequest(BaseModel):
    evidence_refs: list[dict[str, Any]] = Field(..., min_length=1)
    actor: str = Field(default="ui", min_length=1)
    resolved_by_version: str | None = None
    resolved_by_overlay: str | None = None
    repair_id: str | None = None
    capability_works: bool = True
    regression_free: bool = True
    accept_reduced_scope: bool = False
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketCloseRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    actor: str = "ui"
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketClaimRequest(BaseModel):
    actor: str = "ui"
    owner: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketCommentRequest(BaseModel):
    body: str = Field(..., min_length=1)
    actor: str = "ui"
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketVerifyRequest(BaseModel):
    evidence_refs: list[dict[str, Any]] = Field(..., min_length=1)
    actor: str = Field(default="ui", min_length=1)
    repair_id: str | None = None
    notes: str = ""
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketReopenRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    actor: str = "ui"
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketDuplicateRequest(BaseModel):
    duplicate_of: str = Field(..., min_length=1)
    actor: str = "ui"
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketRelatedRequest(BaseModel):
    related_ticket_id: str = Field(..., min_length=1)
    relation: str = "related"
    actor: str = "ui"
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketExternalDraftRequest(BaseModel):
    actor: str = Field(default="builder", min_length=1)
    policy_mode: str = Field(
        default="draft_export",
        pattern="^(draft_export|private_repo_issue|public_upstream_issue)$",
    )
    provider: str = Field(default="github", min_length=1)
    repository: str = ""
    visibility: str = Field(default="private", pattern="^(private|public)$")
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketExternalDraftApprovalRequest(BaseModel):
    actor: str = Field(default="user:owner", min_length=1)
    expected_revision: int | None = Field(default=None, ge=1)


class DevTicketExternalLinkRequest(BaseModel):
    provider: str = Field(default="github", min_length=1)
    repository: str = Field(..., min_length=1)
    issue_id: str = Field(..., min_length=1)
    actor: str = Field(default="user:owner", min_length=1)
    target_path: str = ""
    privacy: str = Field(default="private", pattern="^(private|public)$")
    sync_mode: str = Field(
        default="link_only",
        pattern="^(link_only|draft_export|private_repo_issue|public_upstream_issue|mirror_status)$",
    )
    expected_revision: int | None = Field(default=None, ge=1)


class CoreCapabilityRequest(BaseModel):
    summary: str = Field(..., min_length=1)
    component_ref: str = Field(..., min_length=1)
    desired_contract: str = Field(..., min_length=1)
    actor: str = "builder"
    impact: str = "contract_gap"
    motivation: str = ""
    observed_limitation: str = ""
    rejected_workarounds: list[dict[str, Any]] = Field(default_factory=list)
    blocked_ticket_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    target_scope: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    status: str = "proposed"


class CoreTicketTransitionRequest(BaseModel):
    transition: str = Field(
        ...,
        pattern="^(created|qualified|accepted|deferred|released|verified|reopened)$",
    )
    actor: str = Field(default="core:maintainer", min_length=1)
    reason: str = ""
    notes: str = ""
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    expected_revision: int | None = Field(default=None, ge=1)
    release_ref: dict[str, Any] = Field(default_factory=dict)
    capability_ref: dict[str, Any] = Field(default_factory=dict)
    publish_pending_actions: bool = True


class SdkUnderstandingRequest(BaseModel):
    kind: str = "sdk_unclear_definition"
    summary: str = Field(..., min_length=1)
    method_ref: str = Field(..., min_length=1)
    actor: str = "builder"
    expected_behavior: str = ""
    observed_behavior: str = ""
    diagnosis: str = ""
    project_ticket_id: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str = "proposed"


TICKET_KIND_TO_SIGNAL_KIND = {
    "feedback": "feedback_note",
    "development_request": "development_request",
    "runtime_compatibility_debt": "compatibility_finding",
    "runtime_failure": "runtime_failure",
    "review_debt": "review_comment",
    "nlu_repair": "nlu_failure",
    "user_adaptation": "user_adaptation_request",
    "sdk_understanding": "sdk_unclear_definition",
    "core_capability_request": "core_capability_request",
}
SIGNAL_KIND_TO_TICKET_KIND = {
    "feedback_note": "feedback",
    "development_request": "development_request",
    "compatibility_finding": "runtime_compatibility_debt",
    "runtime_failure": "runtime_failure",
    "review_comment": "review_debt",
    "nlu_failure": "nlu_repair",
    "user_adaptation_request": "user_adaptation",
    "sdk_unclear_definition": "sdk_understanding",
    "sdk_application_failure": "sdk_understanding",
    "sdk_observability_gap": "sdk_understanding",
    "sdk_example_gap": "sdk_understanding",
    "sdk_policy_boundary": "sdk_understanding",
    "sdk_generalization_pressure": "sdk_understanding",
    "builder_rejection_learning": "sdk_understanding",
    "core_capability_request": "core_capability_request",
}
TICKET_KINDS = set(TICKET_KIND_TO_SIGNAL_KIND)
SIGNAL_KINDS = set(SIGNAL_KIND_TO_TICKET_KIND)
ARTIFACT_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
MAX_ARTIFACT_BYTES = 6 * 1024 * 1024


def _clean_kind(value: str | None) -> str:
    return str(value or "").strip()


def _ticket_kind_for_create(kind: str, explicit_ticket_kind: str | None = None) -> str:
    explicit = _clean_kind(explicit_ticket_kind)
    if explicit:
        return explicit
    token = _clean_kind(kind)
    if token in TICKET_KINDS:
        return token
    if token in SIGNAL_KINDS:
        return SIGNAL_KIND_TO_TICKET_KIND[token]
    return token or "development_request"


def _signal_kind_for_create(
    kind: str,
    explicit_signal_kind: str | None = None,
    ticket_kind: str | None = None,
) -> str:
    explicit = _clean_kind(explicit_signal_kind)
    if explicit:
        return explicit
    token = _clean_kind(kind)
    if token in SIGNAL_KINDS:
        return token
    ticket = _clean_kind(ticket_kind)
    if ticket in TICKET_KINDS:
        return TICKET_KIND_TO_SIGNAL_KIND[ticket]
    return TICKET_KIND_TO_SIGNAL_KIND.get(token, "development_request")


def _not_found(ticket_id: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ticket_not_found:{ticket_id}")


def _ticket_mutation_error(exc: ValueError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if "revision conflict" in str(exc) else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


def _ticket_detail(service: DevelopmentTicketService, ticket: dict[str, Any]) -> dict[str, Any]:
    signals = [
        signal
        for signal_id in ticket.get("signal_ids") or []
        for signal in [service.get_signal(str(signal_id))]
        if signal
    ]
    work_stream = _builder_work_stream(service, ticket)
    source_scope = dict(ticket.get("target_scope") or {})
    try:
        builder_target = service.builder_target(str(ticket.get("ticket_id") or ""))
    except (KeyError, ValueError):
        builder_target = {}
    if builder_target:
        source_scope.update(
            {
                "type": builder_target["object_type"],
                "id": builder_target["object_id"],
            }
        )
    return {
        "ticket": ticket,
        "signals": signals,
        "development_source": development_source_options(source_scope),
        "autonomous_repair_qualification": service.autonomous_repair_qualification(
            str(ticket.get("ticket_id") or "")
        ),
        "evidence": _evidence_view(ticket, signals),
        "work_stream": work_stream,
        "builder_work_items": work_stream["builder_work_items"],
    }


def _ticket_list_item(ticket: Mapping[str, Any]) -> dict[str, Any]:
    return project_development_ticket_summary(ticket)


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _builder_work_id(ref: Mapping[str, Any], index: int) -> str:
    for key in ("automation_task_id", "task_id", "work_id", "repair_id", "id"):
        token = str(ref.get(key) or "").strip()
        if token:
            return token
    return f"builder-work-{index + 1}"


def _builder_automation_context(ref: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    ref_automation = ref.get("automation") if isinstance(ref.get("automation"), Mapping) else {}
    if ref_automation:
        return dict(ref_automation)
    context = task.get("context") if isinstance(task.get("context"), Mapping) else {}
    automation = context.get("automation") if isinstance(context.get("automation"), Mapping) else {}
    return dict(automation) if isinstance(automation, Mapping) else {}


def _builder_attempt_is_current(ref: Mapping[str, Any], task: Mapping[str, Any]) -> bool:
    ref_task_id = str(ref.get("automation_task_id") or ref.get("task_id") or "").strip()
    context = task.get("context") if isinstance(task.get("context"), Mapping) else {}
    automation = context.get("automation") if isinstance(context.get("automation"), Mapping) else {}
    current_task_id = str(automation.get("task_id") or "").strip()
    if not ref_task_id:
        return not current_task_id
    return bool(current_task_id and ref_task_id == current_task_id)


def _builder_trial_sort_key(
    trial: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
    ref: Mapping[str, Any],
) -> tuple[str, str]:
    detail = trial.get("trial") if isinstance(trial.get("trial"), Mapping) else {}
    timestamp = str(
        detail.get("decided_at")
        or detail.get("started_at")
        or task.get("updated_at")
        or ref.get("updated_at")
        or task.get("created_at")
        or ref.get("created_at")
        or ""
    ).strip()
    return timestamp, str(detail.get("candidate_id") or "")


def _builder_repair_sort_key(
    repair_id: str,
    *,
    task: Mapping[str, Any],
    refs: list[Mapping[str, Any]],
) -> tuple[str, str]:
    timestamps = [
        str(task.get("updated_at") or "").strip(),
        str(task.get("created_at") or "").strip(),
    ]
    for ref in refs:
        timestamps.extend(
            [
                str(ref.get("updated_at") or "").strip(),
                str(ref.get("created_at") or "").strip(),
            ]
        )
    return max(timestamps, default=""), repair_id


def _builder_token_accounting(ref: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    context = task.get("context") if isinstance(task.get("context"), Mapping) else {}
    economic = context.get("economic") if isinstance(context.get("economic"), Mapping) else {}
    token_accounting = ref.get("token_accounting") if isinstance(ref.get("token_accounting"), Mapping) else {}
    usage = (
        ref.get("token_usage")
        if isinstance(ref.get("token_usage"), Mapping)
        else task.get("token_usage")
        if isinstance(task.get("token_usage"), Mapping)
        else context.get("usage")
        if isinstance(context.get("usage"), Mapping)
        else {}
    )
    aggregate_usage = (
        context.get("usage")
        if isinstance(context.get("usage"), Mapping)
        else {}
    )
    estimate = (
        ref.get("cost_estimate")
        if isinstance(ref.get("cost_estimate"), Mapping)
        else task.get("cost_estimate")
        if isinstance(task.get("cost_estimate"), Mapping)
        else context.get("cost_estimate")
        if isinstance(context.get("cost_estimate"), Mapping)
        else {}
    )
    return {
        "schema": "adaos.builder.codex_token_accounting.v1",
        "subscription_resource": str(
            token_accounting.get("subscription_resource")
            or economic.get("subscription_resource")
            or "codex.api.tokens"
        ),
        "source_of_truth": str(
            token_accounting.get("source_of_truth")
            or economic.get("source_of_truth")
            or "adaos.root_mgmnt.codex_usage_event.v1"
        ),
        "usage_event_endpoint": str(
            token_accounting.get("usage_event_endpoint")
            or economic.get("usage_event_endpoint")
            or "/hub/economic/codex/usage"
        ),
        "required_for_statuses": list(
            token_accounting.get("required_for_statuses")
            if isinstance(token_accounting.get("required_for_statuses"), list)
            else economic.get("required_for_statuses")
            if isinstance(economic.get("required_for_statuses"), list)
            else ["succeeded", "failed", "errored", "cancelled"]
        ),
        "policy": str(
            token_accounting.get("policy")
            or economic.get("policy")
            or "record provider-reported billable tokens even when repair work fails"
        ),
        "reported_usage": dict(usage) if isinstance(usage, Mapping) else {},
        "aggregate_usage": dict(aggregate_usage),
        "estimate": dict(estimate) if isinstance(estimate, Mapping) else {},
    }


def _compact_builder_trial(value: Any) -> dict[str, Any]:
    trial_context = dict(value) if isinstance(value, Mapping) else {}
    trial = (
        dict(trial_context.get("trial"))
        if isinstance(trial_context.get("trial"), Mapping)
        else {}
    )
    if not trial_context or not trial:
        return {}
    skills = [
        {
            key: item.get(key)
            for key in ("id", "version", "source_space", "runtime_space")
            if item.get(key) not in (None, "")
        }
        for item in trial_context.get("skills") or []
        if isinstance(item, Mapping)
    ][:12]
    scenario = (
        {
            key: trial_context["scenario"].get(key)
            for key in ("id", "version", "source_space", "runtime_space")
            if trial_context["scenario"].get(key) not in (None, "")
        }
        if isinstance(trial_context.get("scenario"), Mapping)
        else None
    )
    return {
        "schema": "adaos.builder.ticket_trial_summary.v1",
        "ok": bool(trial_context.get("ok")),
        "mode": trial_context.get("mode"),
        "webspace_id": trial_context.get("webspace_id"),
        "source_policy": trial_context.get("source_policy"),
        "ticket_id": trial_context.get("ticket_id"),
        "task_id": trial_context.get("task_id"),
        "trial": {
            key: trial.get(key)
            for key in (
                "status",
                "decision",
                "candidate_id",
                "candidate_digest",
                "release_digest",
                "version",
                "started_at",
                "decided_at",
                "decided_by",
            )
            if trial.get(key) not in (None, "")
        },
        "skills": skills,
        "scenario": scenario,
    }


def _compact_builder_acceptance(value: Any) -> dict[str, Any]:
    acceptance = dict(value) if isinstance(value, Mapping) else {}
    if not acceptance:
        return {}
    evidence_refs = _mapping_list(acceptance.get("evidence_refs"))
    return {
        key: acceptance.get(key)
        for key in (
            "status",
            "accepted",
            "capability_works",
            "regression_free",
            "actor",
            "recorded_at",
        )
        if acceptance.get(key) not in (None, "")
    } | {"evidence_ref_count": len(evidence_refs)}


def _compact_builder_timeline(value: Any) -> dict[str, Any]:
    timeline = _mapping_list(value)
    if not timeline:
        return {"event_count": 0}
    last_event = timeline[-1]
    return {
        "event_count": len(timeline),
        "last_event": {
            key: last_event.get(key)
            for key in ("kind", "status", "actor", "recorded_at", "created_at")
            if last_event.get(key) not in (None, "")
        },
    }


def _builder_stream_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "entry_id",
            "kind",
            "authority",
            "work_id",
            "work_item_id",
            "work_type",
            "mode",
            "status",
            "compatibility_status",
            "parent_work_status",
            "summary",
            "repair_id",
            "automation_task_id",
            "automation_status",
            "current_attempt",
            "repair_current_attempt",
            "human_manageable",
            "read_only",
            "created_at",
            "updated_at",
        )
        if item.get(key) not in (None, "")
    }


def _builder_work_stream(service: DevelopmentTicketService, ticket: dict[str, Any]) -> dict[str, Any]:
    ticket_id = str(ticket.get("ticket_id") or "").strip()
    refs = _mapping_list(ticket.get("builder_refs"))
    repair_ids = {
        str(ref.get("repair_id") or "").strip()
        for ref in refs
        if str(ref.get("repair_id") or "").strip()
    }
    repair_tasks: dict[str, dict[str, Any]] = {}
    if repair_ids:
        try:
            repair_tasks = {
                str(task.get("repair_id") or "").strip(): dict(task)
                for task in _repair_service_for(service).list()
                if str(task.get("repair_id") or "").strip() in repair_ids
            }
        except Exception:
            repair_tasks = {}
    refs_by_repair: dict[str, list[Mapping[str, Any]]] = {
        repair_id: [
            ref
            for ref in refs
            if str(ref.get("repair_id") or "").strip() == repair_id
        ]
        for repair_id in repair_ids
    }
    latest_repair_id = (
        max(
            repair_ids,
            key=lambda repair_id: _builder_repair_sort_key(
                repair_id,
                task=repair_tasks.get(repair_id) or {},
                refs=refs_by_repair.get(repair_id) or [],
            ),
        )
        if repair_ids
        else ""
    )
    builder_items: list[dict[str, Any]] = []
    trial_candidates: list[tuple[tuple[str, str], dict[str, Any]]] = []
    entries: list[dict[str, Any]] = [
        {
            "entry_id": f"{ticket_id}:ticket",
            "kind": "user_ticket",
            "authority": "adaos.dev.ticket",
            "title": str(ticket.get("summary") or "").strip(),
            "status": ticket.get("status"),
            "status_group": ticket.get("status_group"),
            "human_manageable": True,
            "read_only": False,
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
        }
    ]
    for index, comment in enumerate(_mapping_list(ticket.get("comments"))):
        comment_id = str(comment.get("id") or index).strip()
        entries.append(
            {
                "entry_id": f"{ticket_id}:comment:{comment_id}",
                "kind": "user_comment",
                "authority": "adaos.dev.ticket.comment",
                "title": str(comment.get("body") or comment.get("summary") or "").strip(),
                "actor": comment.get("actor"),
                "human_manageable": True,
                "read_only": True,
                "created_at": comment.get("created_at"),
                "updated_at": comment.get("created_at"),
                "evidence_refs": _mapping_list(comment.get("evidence_refs")),
            }
        )
    for index, ref in enumerate(refs):
        work_id = _builder_work_id(ref, index)
        repair_id = str(ref.get("repair_id") or "").strip()
        task = repair_tasks.get(repair_id) or {}
        context = task.get("context") if isinstance(task.get("context"), Mapping) else {}
        compact_trial = _compact_builder_trial(context.get("trial"))
        if compact_trial and repair_id == latest_repair_id:
            trial_candidates.append(
                (
                    _builder_trial_sort_key(compact_trial, task=task, ref=ref),
                    compact_trial,
                )
            )
        automation = _builder_automation_context(ref, task)
        repair_current_attempt = _builder_attempt_is_current(ref, task)
        current_attempt = repair_current_attempt and repair_id == latest_repair_id
        has_automation_attempt = bool(
            str(ref.get("automation_task_id") or automation.get("task_id") or "").strip()
        )
        attempt_status = str(ref.get("status") or "").strip() if has_automation_attempt else ""
        if not attempt_status:
            attempt_status = str(task.get("work_status") or task.get("status") or "linked")
        item = {
            "entry_id": f"{ticket_id}:builder:{work_id}",
            "kind": "builder_work_item",
            "authority": "adaos.builder.work_item",
            "work_id": work_id,
            "work_item_id": task.get("work_item_id") or work_id,
            "work_type": str(ref.get("type") or "builder_repair_task"),
            "mode": str(ref.get("mode") or ref.get("handoff_mode") or "").strip() or None,
            "status": attempt_status,
            "compatibility_status": ref.get("status") or task.get("status") or "linked",
            "parent_work_status": task.get("work_status") or task.get("status") or None,
            "parent_compatibility_status": task.get("status") or None,
            "current_attempt": current_attempt,
            "repair_current_attempt": repair_current_attempt,
            "revision": task.get("revision"),
            "package_id": task.get("package_id"),
            "ticket_ids": list(task.get("ticket_ids") or []),
            "summary": task.get("summary") or ref.get("summary") or "",
            "project_id": task.get("project_id") or context.get("project_id") or None,
            "repair_id": str(ref.get("repair_id") or task.get("repair_id") or "").strip() or None,
            "human_manageable": False,
            "read_only": True,
            "created_at": task.get("created_at") or ref.get("created_at"),
            "updated_at": task.get("updated_at") or ref.get("updated_at") or ref.get("created_at"),
            "acceptance": _compact_builder_acceptance(
                ref.get("acceptance")
                if isinstance(ref.get("acceptance"), Mapping)
                else task.get("acceptance")
                if repair_current_attempt
                else {}
            ),
            "automation": automation,
            # Trial belongs to the user ticket/release candidate, not to each
            # historical Builder attempt. Keep it once at work-stream level.
            "trial": {},
            "automation_session_id": automation.get("session_id") or ref.get("automation_session_id"),
            "automation_task_id": automation.get("task_id") or ref.get("automation_task_id"),
            "automation_status": automation.get("status") or ref.get("automation_status"),
            "token_accounting": _builder_token_accounting(ref, task),
            "timeline_summary": _compact_builder_timeline(
                ref.get("timeline")
                if isinstance(ref.get("timeline"), list)
                else task.get("timeline")
                if repair_current_attempt
                else []
            ),
            "parent_timeline_summary": _compact_builder_timeline(task.get("timeline")),
        }
        builder_items.append(item)
        entries.append(_builder_stream_entry(item))
    entries = sorted(entries, key=lambda item: (str(item.get("created_at") or item.get("updated_at") or ""), str(item.get("entry_id") or "")))
    return {
        "schema": "adaos.builder.ticket_work_stream.v1",
        "ticket_id": ticket_id,
        "authority": {
            "user_ticket": "adaos.dev.ticket",
            "builder_work": "adaos.builder.work_item",
            "token_usage": "adaos.root_mgmnt.codex_usage_event.v1",
        },
        "lifecycle_split": {
            "user_ticket_human_manageable": True,
            "builder_work_human_manageable": False,
            "builder_work_status_source": "Builder repair/task registry",
            "one_user_ticket_can_spawn_many_builder_items": True,
        },
        "builder_work_count": len(builder_items),
        "builder_work_items": builder_items,
        "trial": max(trial_candidates, key=lambda item: item[0])[1] if trial_candidates else {},
        "entries": entries,
    }


def _merged_refs(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            try:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
            except Exception:
                key = repr(sorted(item.items()))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _evidence_view(ticket: dict[str, Any], signals: list[dict[str, Any]]) -> dict[str, Any]:
    ticket_evidence_refs = list(ticket.get("evidence_refs") or [])
    ticket_artifact_refs = list(ticket.get("artifact_refs") or [])
    signal_evidence_refs = [
        item
        for signal in signals
        for item in (signal.get("evidence_refs") or [])
        if isinstance(item, dict)
    ]
    signal_artifact_refs = [
        item
        for signal in signals
        for item in (signal.get("artifact_refs") or [])
        if isinstance(item, dict)
    ]
    return {
        "ticket_id": ticket.get("ticket_id"),
        "evidence_refs": _merged_refs(ticket_evidence_refs, signal_evidence_refs),
        "artifact_refs": _merged_refs(ticket_artifact_refs, signal_artifact_refs),
        "ticket_evidence_refs": ticket_evidence_refs,
        "ticket_artifact_refs": ticket_artifact_refs,
        "signal_evidence_refs": signal_evidence_refs,
        "signal_artifact_refs": signal_artifact_refs,
        "pending_action_refs": list(ticket.get("pending_action_refs") or []),
        "builder_refs": list(ticket.get("builder_refs") or []),
        "external_refs": list(ticket.get("external_refs") or []),
        "metadata": ticket.get("metadata") or {},
        "policy": ticket.get("policy") or {},
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _append_filter_tokens(tokens: set[str], value: Any, *, expand_ref_tail: bool = True) -> None:
    if value is None:
        return
    if isinstance(value, Mapping):
        scope_type = _text(value.get("type") or value.get("kind"))
        scope_id = _text(value.get("id") or value.get("name"))
        for key in (
            "ref",
            "canonical_ref",
            "target_ref",
            "project_ref",
            "scenario_ref",
            "skill_ref",
            "project_id",
            "scenario_id",
            "skill_id",
        ):
            _append_filter_tokens(tokens, value.get(key), expand_ref_tail=expand_ref_tail)
        if scope_id:
            tokens.add(scope_id)
            if scope_type:
                tokens.add(f"{scope_type}:{scope_id}")
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _append_filter_tokens(tokens, item, expand_ref_tail=expand_ref_tail)
        return
    text = _text(value)
    if not text or text == ":" or "$" in text:
        return
    tokens.add(text)
    if expand_ref_tail and ":" in text:
        tail = text.rsplit(":", 1)[-1].strip()
        if tail:
            tokens.add(tail)


def _query_filter_tokens(request: Request, *names: str, expand_ref_tail: bool = True) -> set[str]:
    tokens: set[str] = set()
    for name in names:
        for raw in request.query_params.getlist(name):
            for part in str(raw or "").split(","):
                _append_filter_tokens(tokens, part, expand_ref_tail=expand_ref_tail)
    return tokens


def _ticket_target_tokens(ticket: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    _append_filter_tokens(tokens, ticket.get("owner_area"))
    _append_filter_tokens(tokens, ticket.get("component_ref"))
    target = ticket.get("target_scope")
    if isinstance(target, Mapping):
        _append_filter_tokens(tokens, target)
        for key in (
            "component_refs",
            "components",
            "target_refs",
            "affected_refs",
            "scope_refs",
            "related_refs",
        ):
            _append_filter_tokens(tokens, target.get(key))
    return tokens


def _bool_query(value: str | None) -> bool | None:
    token = _text(value).lower()
    if not token:
        return None
    if token in {"1", "true", "yes", "y", "on"}:
        return True
    if token in {"0", "false", "no", "n", "off"}:
        return False
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid_bool:{value}")


def _safe_artifact_id(value: str) -> str:
    token = _text(value)
    if not token or "/" in token or "\\" in token or ".." in token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_artifact_id",
        )
    if any(not (ch.isalnum() or ch in ".-_") for ch in token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_artifact_id",
        )
    return token


def _artifact_content_type(value: str) -> str:
    token = _text(value).split(";", 1)[0].lower() or "image/png"
    if token not in ARTIFACT_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="unsupported_artifact_content_type",
        )
    return token


def _artifact_payload(body: DevTicketArtifactUploadRequest) -> tuple[str, bytes]:
    content_type = _artifact_content_type(body.content_type)
    encoded = _text(body.content_base64)
    if encoded.startswith("data:"):
        header, _, payload = encoded.partition(",")
        encoded = payload.strip()
        if ";" in header:
            content_type = _artifact_content_type(header.removeprefix("data:").split(";", 1)[0])
    try:
        data = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_artifact_base64",
        ) from exc
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty_artifact")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="artifact_too_large",
        )
    return content_type, data


def _artifact_filename(value: str | None, *, artifact_id: str, extension: str) -> str:
    raw = _text(value) or f"{artifact_id}.{extension}"
    name = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in raw).strip("._")
    if not name:
        name = f"{artifact_id}.{extension}"
    if "." not in name:
        name = f"{name}.{extension}"
    return name[:120]


def _artifact_manifest_path(service: DevelopmentTicketService, artifact_id: str) -> tuple[Path, Path]:
    artifact_dir = service.root / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    token = _safe_artifact_id(artifact_id)
    return artifact_dir, artifact_dir / f"{token}.json"


@router.get("")
def list_tickets(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    status_group: str | None = None,
    target_id: str | None = None,
    target_ref: str | None = None,
    kind: str | None = None,
    scenario_id: str | None = None,
    skill_id: str | None = None,
    modal_id: str | None = None,
    component: str | None = None,
    severity: str | None = None,
    blocking: str | None = None,
    source: str | None = None,
    owner: str | None = None,
    owner_area: str | None = None,
    component_ref: str | None = None,
    updated_since: str | None = None,
    search: str | None = None,
    projection: str = Query(default="full", pattern="^(full|summary)$"),
    limit: int | None = Query(default=None, ge=0, le=1000),
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    target_tokens = _query_filter_tokens(request, "target_id", "target_ids")
    ref_tokens = _query_filter_tokens(
        request,
        "target_ref",
        "target_refs",
        "scope_ref",
        "scope_refs",
        expand_ref_tail=False,
    )
    _append_filter_tokens(target_tokens, target_id)
    _append_filter_tokens(ref_tokens, target_ref, expand_ref_tail=False)
    kind_tokens = _query_filter_tokens(request, "kind", "kinds")
    _append_filter_tokens(kind_tokens, kind)
    scoped_tokens = set()
    for name in ("project_id", "project_ids", "scenario_id", "scenario_ids", "skill_id", "skill_ids", "modal_id", "modal_ids", "component", "components"):
        scoped_tokens.update(_query_filter_tokens(request, name))
    for value in (scenario_id, skill_id, modal_id, component):
        _append_filter_tokens(scoped_tokens, value)
    tickets = service.list_tickets(
        status=status_filter,
        status_group=status_group,
        severity=severity,
        blocking=_bool_query(blocking),
        source=source,
        owner=owner,
        owner_area=owner_area,
        component_ref=component_ref,
        updated_since=updated_since,
        search=search,
        limit=limit,
    )
    if target_tokens or ref_tokens:
        wanted = target_tokens | ref_tokens
        tickets = [ticket for ticket in tickets if _ticket_target_tokens(ticket) & wanted]
    if scoped_tokens:
        tickets = [ticket for ticket in tickets if _ticket_target_tokens(ticket) & scoped_tokens]
    if kind_tokens:
        tickets = [ticket for ticket in tickets if _text(ticket.get("kind")) in kind_tokens]
    items = [_ticket_list_item(ticket) for ticket in tickets] if projection == "summary" else tickets
    return {
        "ok": True,
        "tickets": items,
        "items": items,
        "count": len(items),
        "projection": projection,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_ticket(
    body: DevTicketCreateRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket_kind = _ticket_kind_for_create(body.kind, body.ticket_kind)
        signal_kind = _signal_kind_for_create(body.kind, body.signal_kind, ticket_kind)
        signal_result = service.capture_signal(
            kind=signal_kind,
            summary=body.summary,
            owner_scope=body.owner_scope or {"type": "workspace", "id": "local"},
            origin_scope=body.origin_scope or {"type": "ui", "surface": "development_tickets"},
            target_scope=body.target_scope,
            severity=body.severity,
            blocking=body.blocking,
            source=body.source,
            dedup_key=body.dedup_key,
            artifact_refs=body.artifact_refs,
            evidence_refs=body.evidence_refs,
            policy=body.policy,
            metadata=body.metadata,
            owner_area=body.owner_area,
            component_ref=body.component_ref,
            relation_refs=body.relation_refs,
        )
        ticket_result = service.ensure_ticket_for_signal(
            signal_result["signal"],
            kind=ticket_kind,
            status=body.status,
            source=body.source,
            dedup_key=body.dedup_key,
            metadata=body.metadata,
            policy=body.policy,
            owner_area=body.owner_area,
            component_ref=body.component_ref,
            relation_refs=body.relation_refs,
        )
        ticket = ticket_result["ticket"]
        return {
            "ok": True,
            "signal": signal_result["signal"],
            "ticket": ticket,
            "detail": _ticket_detail(service, ticket),
            "signal_duplicate": bool(signal_result.get("duplicate")),
            "ticket_duplicate": bool(ticket_result.get("duplicate")),
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/events")
def list_ticket_events(
    after: str | None = None,
    updated_since: str | None = None,
    ticket_id: str | None = None,
    owner_area: str | None = None,
    project_id: str | None = None,
    scenario_id: str | None = None,
    skill_id: str | None = None,
    modal_id: str | None = None,
    component: str | None = None,
    component_ref: str | None = None,
    kind: str | None = None,
    search: str | None = None,
    status_group: str = "open",
    include_snapshot: bool = True,
    limit: int = Query(default=500, ge=1, le=2000),
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    if ticket_id:
        events = service.list_lifecycle_events(
            after=after,
            updated_since=updated_since,
            ticket_id=ticket_id,
            owner_area=owner_area,
            limit=limit,
        )
        ticket = service.get_ticket(ticket_id)
        return {
            "ok": True,
            "schema": "adaos.dev_ticket.change_feed.v1",
            "snapshot": [ticket] if include_snapshot and not after and ticket else [],
            "events": events,
            "cursor": events[-1]["event_id"] if events else after,
        }
    feed = service.read_change_feed(
        after=after,
        updated_since=updated_since,
        include_snapshot=include_snapshot,
        status_group=status_group,
        project_id=project_id,
        scenario_id=scenario_id,
        skill_id=skill_id,
        modal_id=modal_id,
        component=component,
        component_ref=component_ref,
        kind=kind,
        owner_area=owner_area,
        search=search,
        limit=limit,
    )
    return {
        "ok": True,
        **feed,
    }


@router.get("/core-backlog")
def list_core_backlog(
    component_ref: str | None = None,
    impact: str | None = None,
    status_group: str | None = "open",
    affected_project_id: str | None = None,
    affected_subnet_id: str | None = None,
    release_target: str | None = None,
    verification_state: str | None = None,
    search: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    return {
        "ok": True,
        **service.list_core_backlog(
            component_ref=component_ref,
            impact=impact,
            status_group=status_group,
            affected_project_id=affected_project_id,
            affected_subnet_id=affected_subnet_id,
            release_target=release_target,
            verification_state=verification_state,
            search=search,
            limit=limit,
        ),
    }


@router.post("/core-capability-requests", status_code=status.HTTP_201_CREATED)
def create_core_capability_request(
    body: CoreCapabilityRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        result = service.create_core_capability_request(
            summary=body.summary,
            component_ref=body.component_ref,
            desired_contract=body.desired_contract,
            actor=body.actor,
            impact=body.impact,
            motivation=body.motivation,
            observed_limitation=body.observed_limitation,
            rejected_workarounds=body.rejected_workarounds,
            blocked_ticket_ids=body.blocked_ticket_ids,
            evidence_refs=body.evidence_refs,
            target_scope=body.target_scope,
            metadata=body.metadata,
            policy=body.policy,
            status=body.status,
        )
        return {"ok": True, **result, "detail": _ticket_detail(service, result["ticket"])}
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{ticket_id}/core-lifecycle")
def transition_core_ticket(
    ticket_id: str,
    body: CoreTicketTransitionRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return service.transition_core_ticket(
            ticket_id,
            transition=body.transition,
            actor=body.actor,
            reason=body.reason,
            notes=body.notes,
            evidence_refs=body.evidence_refs,
            release_ref=body.release_ref,
            capability_ref=body.capability_ref,
            publish_pending_actions=body.publish_pending_actions,
            expected_revision=body.expected_revision,
        )
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/sdk-understanding", status_code=status.HTTP_201_CREATED)
def create_sdk_understanding_signal(
    body: SdkUnderstandingRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        result = service.record_sdk_understanding_signal(
            kind=body.kind,
            summary=body.summary,
            method_ref=body.method_ref,
            actor=body.actor,
            expected_behavior=body.expected_behavior,
            observed_behavior=body.observed_behavior,
            diagnosis=body.diagnosis,
            project_ticket_id=body.project_ticket_id,
            evidence_refs=body.evidence_refs,
            metadata=body.metadata,
            status=body.status,
        )
        return {"ok": True, **result, "detail": _ticket_detail(service, result["ticket"])}
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/builder-packages/plan", status_code=status.HTTP_201_CREATED)
def plan_builder_package(
    body: DevTicketPackagePlanRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return service.plan_builder_package(
            body.ticket_ids,
            actor=body.actor,
            repair_service=_repair_service_for(service),
            execution_budget=body.execution_budget,
            source_strategy=body.source_strategy,
        )
    except BuilderSourceRecoveryRequired as exc:
        raise _source_recovery_required(exc) from exc
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/builder-packages/{package_id}/start")
def start_builder_package(
    package_id: str,
    body: DevTicketPackageStartRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return service.start_autonomous_package(
            package_id,
            actor=body.actor,
            repair_service=_repair_service_for(service),
            automation_service=_get_automation_service(),
            webspace_id=body.webspace_id,
            conversation_id=body.conversation_id,
            source_strategy=body.source_strategy,
            agent_profile=body.agent_profile,
            mcp=body.mcp,
        )
    except BuilderSourceRecoveryRequired as exc:
        raise _source_recovery_required(exc) from exc
    except KeyError as exc:
        missing_id = str(exc).strip("'")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"builder_package_not_found:{missing_id}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/builder-packages/{package_id}")
def get_builder_package(
    package_id: str,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    repair_service = _repair_service_for(service)
    items = repair_service.list(package_id=package_id)
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"builder_package_not_found:{package_id}",
        )
    return {
        "ok": True,
        "package_id": package_id,
        "work_items": items,
        "rollup": repair_service.package_rollup(package_id),
    }


@router.post("/{ticket_id}/trial/decision")
def decide_ticket_trial(
    ticket_id: str,
    body: DevTicketTrialDecisionRequest,
    service: DevelopmentTicketService = Depends(_get_service),
    automation: Any = Depends(_get_automation_service),
) -> dict[str, Any]:
    try:
        target = service.builder_target(ticket_id)
        result = automation.decide_aprobation(
            object_type=target["object_type"],
            object_id=target["object_id"],
            decision=body.decision,
            actor=body.actor,
            reason=body.reason,
            expected_candidate_id=body.expected_candidate_id,
            expected_candidate_digest=body.expected_candidate_digest,
        )
        updated = service.get_ticket(ticket_id)
        return {
            **result,
            "detail": _ticket_detail(service, updated) if updated else None,
        }
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/artifacts", status_code=status.HTTP_201_CREATED)
def upload_artifact(
    body: DevTicketArtifactUploadRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    content_type, data = _artifact_payload(body)
    artifact_id = f"dartifact.{new_id()}"
    extension = ARTIFACT_CONTENT_TYPES[content_type]
    artifact_dir, manifest_path = _artifact_manifest_path(service, artifact_id)
    file_name = f"{artifact_id}.{extension}"
    file_path = artifact_dir / file_name
    digest = hashlib.sha256(data).hexdigest()
    file_path.write_bytes(data)
    manifest = {
        "schema": "adaos.dev_ticket.artifact.v1",
        "artifact_id": artifact_id,
        "kind": _text(body.kind) or "artifact",
        "content_type": content_type,
        "filename": _artifact_filename(
            body.filename,
            artifact_id=artifact_id,
            extension=extension,
        ),
        "file_name": file_name,
        "size_bytes": len(data),
        "sha256": f"sha256:{digest}",
        "origin_scope": body.origin_scope or {},
        "target_scope": body.target_scope or {},
        "metadata": body.metadata or {},
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ref = {
        "type": manifest["kind"],
        "artifact_id": artifact_id,
        "uri": f"dev-ticket-artifact:{artifact_id}",
        "content_api_path": f"/api/development-tickets/artifacts/{artifact_id}/content",
        "content_type": content_type,
        "filename": manifest["filename"],
        "size_bytes": manifest["size_bytes"],
        "sha256": manifest["sha256"],
    }
    return {"ok": True, "artifact": ref, "artifact_ref": ref}


@router.get("/artifacts")
def list_artifacts(
    ticket_id: str | None = None,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        artifacts = service.list_artifacts(ticket_id=ticket_id)
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    return {"ok": True, "artifacts": artifacts, "items": artifacts, "count": len(artifacts)}


@router.get("/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: str,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    artifact = service.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"artifact_not_found:{artifact_id}",
        )
    return {"ok": True, "artifact": artifact}


@router.get("/artifacts/{artifact_id}/content")
def get_artifact_content(
    artifact_id: str,
    service: DevelopmentTicketService = Depends(_get_service),
) -> FileResponse:
    artifact_dir, manifest_path = _artifact_manifest_path(service, artifact_id)
    if not manifest_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"artifact_not_found:{artifact_id}",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="artifact_manifest_invalid",
        ) from exc
    file_name = _safe_artifact_id(_text(manifest.get("file_name")))
    file_path = (artifact_dir / file_name).resolve()
    root = artifact_dir.resolve()
    if root not in file_path.parents:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="artifact_path_invalid",
        )
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"artifact_content_not_found:{artifact_id}",
        )
    return FileResponse(
        file_path,
        media_type=_artifact_content_type(_text(manifest.get("content_type"))),
        filename=_artifact_filename(
            _text(manifest.get("filename")),
            artifact_id=_safe_artifact_id(artifact_id),
            extension=file_path.suffix.lstrip(".") or "bin",
        ),
    )


@router.get("/{ticket_id}")
def get_ticket(
    ticket_id: str,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    ticket = service.get_ticket(ticket_id)
    if not ticket:
        raise _not_found(ticket_id)
    return {"ok": True, **_ticket_detail(service, ticket)}


@router.patch("/{ticket_id}")
def update_ticket(
    ticket_id: str,
    body: DevTicketUpdateRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    if body.summary is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="summary is required")
    try:
        ticket = service.update_ticket_summary(
            ticket_id,
            summary=body.summary,
            actor=body.actor,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, **_ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/builder-qualification")
def requalify_builder_repair(
    ticket_id: str,
    body: DevTicketBuilderQualificationRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.requalify_builder_repair(
            ticket_id,
            builder_repair=body.builder_repair,
            actor=body.actor,
            reason=body.reason,
            expected_updated_at=body.expected_updated_at,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        code = status.HTTP_409_CONFLICT if "changed since" in str(exc) or "revision conflict" in str(exc) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/{ticket_id}/builder-qualification/prepare")
def prepare_builder_repair_qualification(
    ticket_id: str,
    body: DevTicketBuilderQualificationPrepareRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        result = service.prepare_builder_repair_qualification(
            ticket_id,
            actor=body.actor,
            apply=body.apply,
            expected_revision=body.expected_revision,
        )
        return {
            **result,
            "detail": _ticket_detail(service, result["ticket"]),
        }
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.get("/{ticket_id}/evidence")
def get_ticket_evidence(
    ticket_id: str,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    ticket = service.get_ticket(ticket_id)
    if not ticket:
        raise _not_found(ticket_id)
    signals = [
        signal
        for signal_id in ticket.get("signal_ids") or []
        for signal in [service.get_signal(str(signal_id))]
        if signal
    ]
    return {"ok": True, "evidence": _evidence_view(ticket, signals)}


@router.post("/{ticket_id}/claim")
def claim_ticket(
    ticket_id: str,
    body: DevTicketClaimRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.claim_ticket(
            ticket_id,
            actor=body.actor,
            owner=body.owner,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/start")
def start_ticket(
    ticket_id: str,
    body: DevTicketClaimRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.start_ticket(
            ticket_id,
            actor=body.actor,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/comment")
def comment_ticket(
    ticket_id: str,
    body: DevTicketCommentRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.comment_ticket(
            ticket_id,
            body=body.body,
            actor=body.actor,
            evidence_refs=body.evidence_refs,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/respond")
def respond_to_ticket(
    ticket_id: str,
    body: DevTicketResponseRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        result = service.handle_compatibility_response(
            ticket_id=ticket_id,
            response_action_id=body.response_action_id,
            pending_action_id=body.pending_action_id,
            responder=body.responder,
            response_payload=body.payload,
            repair_service=_repair_service_for(service),
        )
        return {"ok": True, **result, "detail": _ticket_detail(service, result["ticket"])}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{ticket_id}/defer")
def defer_ticket(
    ticket_id: str,
    body: DevTicketDeferRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.defer_ticket(
            ticket_id,
            actor=body.actor,
            reason=body.reason,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/handoff")
def handoff_ticket(
    ticket_id: str,
    body: DevTicketHandoffRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        result = service.handoff_ticket(
            ticket_id,
            mode=body.mode,
            actor=body.actor,
            repair_service=_repair_service_for(service),
        )
        return {"ok": True, **result, "detail": _ticket_detail(service, result["ticket"])}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{ticket_id}/autonomous-repair")
def start_autonomous_repair(
    ticket_id: str,
    body: DevTicketAutonomousRepairRequest,
    service: DevelopmentTicketService = Depends(_get_service),
    automation_service: Any = Depends(_get_automation_service),
) -> dict[str, Any]:
    try:
        result = service.start_autonomous_repair(
            ticket_id,
            actor=body.actor,
            repair_service=_repair_service_for(service),
            automation_service=automation_service,
            webspace_id=body.webspace_id,
            conversation_id=body.conversation_id,
            source_strategy=body.source_strategy,
            execution_budget=body.execution_budget,
            agent_profile=body.agent_profile,
            mcp=body.mcp,
        )
        return {"ok": True, **result, "detail": _ticket_detail(service, result["ticket"])}
    except BuilderSourceRecoveryRequired as exc:
        raise _source_recovery_required(exc) from exc
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{ticket_id}/builder-sync")
def sync_builder_repair(
    ticket_id: str,
    body: DevTicketBuilderSyncRequest,
    service: DevelopmentTicketService = Depends(_get_service),
    automation_service: Any = Depends(_get_automation_service),
) -> dict[str, Any]:
    try:
        result = service.sync_builder_repair(
            ticket_id,
            actor=body.actor,
            repair_id=body.repair_id,
            repair_service=_repair_service_for(service),
            automation_service=automation_service,
        )
        return {"ok": True, **result, "detail": _ticket_detail(service, result["ticket"])}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{ticket_id}/resolve")
def resolve_ticket(
    ticket_id: str,
    body: DevTicketResolveRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        result = service.record_resolution(
            ticket_id,
            evidence_refs=body.evidence_refs,
            actor=body.actor,
            resolved_by_version=body.resolved_by_version,
            resolved_by_overlay=body.resolved_by_overlay,
            repair_service=_repair_service_for(service),
            repair_id=body.repair_id,
            capability_works=body.capability_works,
            regression_free=body.regression_free,
            accept_reduced_scope=body.accept_reduced_scope,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, **result, "detail": _ticket_detail(service, result["ticket"])}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/close")
def close_ticket(
    ticket_id: str,
    body: DevTicketCloseRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.close_ticket(
            ticket_id,
            reason=body.reason,
            actor=body.actor,
            evidence_refs=body.evidence_refs,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/verify")
def verify_ticket(
    ticket_id: str,
    body: DevTicketVerifyRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        result = service.verify_ticket(
            ticket_id,
            evidence_refs=body.evidence_refs,
            actor=body.actor,
            repair_id=body.repair_id,
            notes=body.notes,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, **result, "detail": _ticket_detail(service, result["ticket"])}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/reopen")
def reopen_ticket(
    ticket_id: str,
    body: DevTicketReopenRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.reopen_ticket(
            ticket_id,
            actor=body.actor,
            reason=body.reason,
            evidence_refs=body.evidence_refs,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/duplicate")
def duplicate_ticket(
    ticket_id: str,
    body: DevTicketDuplicateRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.duplicate_ticket(
            ticket_id,
            duplicate_of=body.duplicate_of,
            actor=body.actor,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/related")
def related_ticket(
    ticket_id: str,
    body: DevTicketRelatedRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.relate_ticket(
            ticket_id,
            related_ticket_id=body.related_ticket_id,
            relation=body.relation,
            actor=body.actor,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/external-drafts")
def prepare_external_issue_draft(
    ticket_id: str,
    body: DevTicketExternalDraftRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        result = service.prepare_external_issue_draft(
            ticket_id,
            actor=body.actor,
            policy_mode=body.policy_mode,
            provider=body.provider,
            repository=body.repository,
            visibility=body.visibility,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, **result}
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/external-drafts/{external_ref_id}/approve")
def approve_external_issue_draft(
    ticket_id: str,
    external_ref_id: str,
    body: DevTicketExternalDraftApprovalRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        result = service.approve_external_issue_draft(
            ticket_id,
            external_ref_id=external_ref_id,
            actor=body.actor,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, **result}
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc


@router.post("/{ticket_id}/external-links")
def link_external_issue(
    ticket_id: str,
    body: DevTicketExternalLinkRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        result = service.link_external_issue(
            ticket_id,
            provider=body.provider,
            repository=body.repository,
            issue_id=body.issue_id,
            actor=body.actor,
            target_path=body.target_path,
            privacy=body.privacy,
            sync_mode=body.sync_mode,
            expected_revision=body.expected_revision,
        )
        return {"ok": True, **result}
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except ValueError as exc:
        raise _ticket_mutation_error(exc) from exc
