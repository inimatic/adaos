"""Typed Dev Ticket facade for agents and Python services.

The local service remains authoritative. This module avoids shelling out to the
CLI and keeps Codex, Builder, API, and MCP consumers on one lifecycle.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from adaos.services.development_tickets import DevelopmentTicketService


_TICKET_TO_SIGNAL = {
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


def _service() -> DevelopmentTicketService:
    return DevelopmentTicketService()


def list_tickets(**filters: Any) -> list[dict[str, Any]]:
    return _service().list_tickets(**filters)


def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    return _service().get_ticket(ticket_id)


def create_ticket(
    summary: str,
    *,
    kind: str = "development_request",
    signal_kind: str | None = None,
    target_scope: Mapping[str, Any] | None = None,
    owner_scope: Mapping[str, Any] | None = None,
    origin_scope: Mapping[str, Any] | None = None,
    severity: str = "medium",
    blocking: bool = False,
    source: str = "sdk",
    status: str = "proposed",
    actor: str = "sdk",
    evidence_refs: Sequence[Mapping[str, Any]] = (),
    artifact_refs: Sequence[Mapping[str, Any]] = (),
    metadata: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
    owner_area: str | None = None,
    component_ref: str | None = None,
    relation_refs: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    service = _service()
    signal = service.capture_signal(
        kind=signal_kind or _TICKET_TO_SIGNAL.get(kind, "development_request"),
        summary=summary,
        owner_scope=owner_scope or {"type": "workspace", "id": "local"},
        origin_scope=origin_scope or {"type": "agent", "id": actor, "surface": "sdk"},
        target_scope=target_scope or {"type": "unknown"},
        severity=severity,
        blocking=blocking,
        source=source,
        evidence_refs=evidence_refs,
        artifact_refs=artifact_refs,
        metadata=metadata,
        policy=policy,
        owner_area=owner_area,
        component_ref=component_ref,
        relation_refs=relation_refs,
    )
    ticket = service.ensure_ticket_for_signal(
        signal["signal"],
        kind=kind,
        status=status,
        source=source,
        metadata=metadata,
        policy=policy,
        owner_area=owner_area,
        component_ref=component_ref,
        relation_refs=relation_refs,
    )
    return {
        "ok": True,
        "signal": signal["signal"],
        "ticket": ticket["ticket"],
        "signal_duplicate": bool(signal.get("duplicate")),
        "ticket_duplicate": bool(ticket.get("duplicate")),
    }


def operate_ticket(
    ticket_id: str,
    operation: str,
    *,
    actor: str = "sdk",
    payload: Mapping[str, Any] | None = None,
    evidence_refs: Sequence[Mapping[str, Any]] = (),
    expected_revision: int | None = None,
) -> dict[str, Any]:
    service = _service()
    body = dict(payload or {})
    operation_id = str(operation or "").strip().lower()
    refs = [dict(item) for item in evidence_refs if isinstance(item, Mapping)]
    if operation_id == "claim":
        return {
            "ticket": service.claim_ticket(
                ticket_id,
                actor=actor,
                owner=body.get("owner"),
                expected_revision=expected_revision,
            )
        }
    if operation_id == "start":
        return {
            "ticket": service.start_ticket(
                ticket_id,
                actor=actor,
                expected_revision=expected_revision,
            )
        }
    if operation_id == "comment":
        return {
            "ticket": service.comment_ticket(
                ticket_id,
                body=str(body.get("body") or body.get("comment") or ""),
                actor=actor,
                evidence_refs=refs,
                expected_revision=expected_revision,
            )
        }
    if operation_id in {"defer", "postpone"}:
        return {
            "ticket": service.defer_ticket(
                ticket_id,
                actor=actor,
                reason=str(body.get("reason") or ""),
                expected_revision=expected_revision,
            )
        }
    if operation_id == "resolve":
        return service.record_resolution(
            ticket_id,
            evidence_refs=refs,
            actor=actor,
            resolved_by_version=str(body.get("resolved_by_version") or "") or None,
            resolved_by_overlay=str(body.get("resolved_by_overlay") or "") or None,
            repair_id=str(body.get("repair_id") or "") or None,
            accept_reduced_scope=bool(body.get("accept_reduced_scope", False)),
            expected_revision=expected_revision,
        )
    if operation_id == "verify":
        return service.verify_ticket(
            ticket_id,
            evidence_refs=refs,
            actor=actor,
            repair_id=str(body.get("repair_id") or "") or None,
            notes=str(body.get("notes") or ""),
            expected_revision=expected_revision,
        )
    if operation_id == "close":
        return {
            "ticket": service.close_ticket(
                ticket_id,
                reason=str(body.get("reason") or "closed"),
                actor=actor,
                evidence_refs=refs,
                expected_revision=expected_revision,
            )
        }
    if operation_id == "reopen":
        return {
            "ticket": service.reopen_ticket(
                ticket_id,
                actor=actor,
                reason=str(body.get("reason") or ""),
                evidence_refs=refs,
                expected_revision=expected_revision,
            )
        }
    if operation_id == "duplicate":
        return {
            "ticket": service.duplicate_ticket(
                ticket_id,
                duplicate_of=str(body.get("duplicate_of") or ""),
                actor=actor,
                expected_revision=expected_revision,
            )
        }
    if operation_id in {"related", "relate"}:
        return {
            "ticket": service.relate_ticket(
                ticket_id,
                related_ticket_id=str(body.get("related_ticket_id") or ""),
                relation=str(body.get("relation") or "related"),
                actor=actor,
                expected_revision=expected_revision,
            )
        }
    if operation_id == "core_transition":
        return service.transition_core_ticket(
            ticket_id,
            transition=str(body.get("transition") or ""),
            actor=actor,
            reason=str(body.get("reason") or ""),
            notes=str(body.get("notes") or ""),
            evidence_refs=refs,
            release_ref=body.get("release_ref") if isinstance(body.get("release_ref"), Mapping) else None,
            capability_ref=body.get("capability_ref") if isinstance(body.get("capability_ref"), Mapping) else None,
            publish_pending_actions=bool(body.get("publish_pending_actions", True)),
            expected_revision=expected_revision,
        )
    raise ValueError(f"unsupported Dev Ticket operation: {operation_id}")


def list_events(**filters: Any) -> list[dict[str, Any]]:
    return _service().list_lifecycle_events(**filters)


def read_feed(**filters: Any) -> dict[str, Any]:
    return _service().read_change_feed(**filters)


def list_artifacts(ticket_id: str | None = None) -> list[dict[str, Any]]:
    return _service().list_artifacts(ticket_id=ticket_id)


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    return _service().get_artifact(artifact_id)


__all__ = [
    "create_ticket",
    "get_artifact",
    "get_ticket",
    "list_artifacts",
    "list_events",
    "read_feed",
    "list_tickets",
    "operate_ticket",
]
