from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.builder.repair import BuilderRepairService
from adaos.services.development_tickets import (
    DevelopmentTicketService,
    development_source_options,
)


router = APIRouter(tags=["development-tickets"], dependencies=[Depends(require_token)])


def _get_service() -> DevelopmentTicketService:
    return DevelopmentTicketService()


def _repair_service_for(service: DevelopmentTicketService) -> BuilderRepairService:
    return BuilderRepairService(state_dir=service.state_dir)


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
    source: str = "ui_feedback"
    status: str = "proposed"
    dedup_key: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)


class DevTicketResponseRequest(BaseModel):
    response_action_id: str = Field(..., min_length=1)
    pending_action_id: str | None = None
    responder: dict[str, Any] = Field(default_factory=lambda: {"id": "ui"})
    payload: dict[str, Any] = Field(default_factory=dict)


class DevTicketDeferRequest(BaseModel):
    reason: str = ""
    actor: str = "ui"


class DevTicketHandoffRequest(BaseModel):
    mode: str = Field(default="interactive", pattern="^(interactive|autonomous)$")
    actor: str = "ui"


class DevTicketResolveRequest(BaseModel):
    evidence_refs: list[dict[str, Any]] = Field(..., min_length=1)
    actor: str = Field(default="ui", min_length=1)
    resolved_by_version: str | None = None
    resolved_by_overlay: str | None = None
    repair_id: str | None = None
    capability_works: bool = True
    regression_free: bool = True


class DevTicketCloseRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    actor: str = "ui"
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


TICKET_KIND_TO_SIGNAL_KIND = {
    "feedback": "feedback_note",
    "development_request": "development_request",
    "runtime_compatibility_debt": "compatibility_finding",
    "runtime_failure": "runtime_failure",
    "review_debt": "review_comment",
    "nlu_repair": "nlu_failure",
    "user_adaptation": "user_adaptation_request",
}
SIGNAL_KIND_TO_TICKET_KIND = {
    "feedback_note": "feedback",
    "development_request": "development_request",
    "compatibility_finding": "runtime_compatibility_debt",
    "runtime_failure": "runtime_failure",
    "review_comment": "review_debt",
    "nlu_failure": "nlu_repair",
    "user_adaptation_request": "user_adaptation",
}
TICKET_KINDS = set(TICKET_KIND_TO_SIGNAL_KIND)
SIGNAL_KINDS = set(SIGNAL_KIND_TO_TICKET_KIND)


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


def _ticket_detail(service: DevelopmentTicketService, ticket: dict[str, Any]) -> dict[str, Any]:
    signals = [
        signal
        for signal_id in ticket.get("signal_ids") or []
        for signal in [service.get_signal(str(signal_id))]
        if signal
    ]
    return {
        "ticket": ticket,
        "signals": signals,
        "development_source": development_source_options(ticket.get("target_scope") or {}),
        "evidence": _evidence_view(ticket, signals),
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


@router.get("")
def list_tickets(
    status_filter: str | None = Query(default=None, alias="status"),
    target_id: str | None = None,
    kind: str | None = None,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    tickets = service.list_tickets(status=status_filter, target_id=target_id)
    if kind:
        tickets = [ticket for ticket in tickets if ticket.get("kind") == kind]
    return {"ok": True, "tickets": tickets, "items": tickets, "count": len(tickets)}


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
        )
        ticket_result = service.ensure_ticket_for_signal(
            signal_result["signal"],
            kind=ticket_kind,
            status=body.status,
            source=body.source,
            dedup_key=body.dedup_key,
            metadata=body.metadata,
            policy=body.policy,
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


@router.get("/{ticket_id}")
def get_ticket(
    ticket_id: str,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    ticket = service.get_ticket(ticket_id)
    if not ticket:
        raise _not_found(ticket_id)
    return {"ok": True, **_ticket_detail(service, ticket)}


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
        ticket = service.defer_ticket(ticket_id, actor=body.actor, reason=body.reason)
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc


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
        )
        return {"ok": True, **result, "detail": _ticket_detail(service, result["ticket"])}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
