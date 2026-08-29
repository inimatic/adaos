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
from adaos.services.development_tickets import (
    DevelopmentTicketService,
    development_source_options,
)
from adaos.services.id_gen import new_id


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


class DevTicketClaimRequest(BaseModel):
    actor: str = "ui"
    owner: str | None = None


class DevTicketCommentRequest(BaseModel):
    body: str = Field(..., min_length=1)
    actor: str = "ui"
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class DevTicketVerifyRequest(BaseModel):
    evidence_refs: list[dict[str, Any]] = Field(..., min_length=1)
    actor: str = Field(default="ui", min_length=1)
    repair_id: str | None = None
    notes: str = ""


class DevTicketReopenRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    actor: str = "ui"
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class DevTicketDuplicateRequest(BaseModel):
    duplicate_of: str = Field(..., min_length=1)
    actor: str = "ui"


class DevTicketRelatedRequest(BaseModel):
    related_ticket_id: str = Field(..., min_length=1)
    relation: str = "related"
    actor: str = "ui"


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
    updated_since: str | None = None,
    search: str | None = None,
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
        )
        return {"ok": True, **_ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
        ticket = service.claim_ticket(ticket_id, actor=body.actor, owner=body.owner)
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{ticket_id}/start")
def start_ticket(
    ticket_id: str,
    body: DevTicketClaimRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.start_ticket(ticket_id, actor=body.actor)
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
        )
        return {"ok": True, **result, "detail": _ticket_detail(service, result["ticket"])}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(ticket_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{ticket_id}/duplicate")
def duplicate_ticket(
    ticket_id: str,
    body: DevTicketDuplicateRequest,
    service: DevelopmentTicketService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        ticket = service.duplicate_ticket(ticket_id, duplicate_of=body.duplicate_of, actor=body.actor)
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
        )
        return {"ok": True, "ticket": ticket, "detail": _ticket_detail(service, ticket)}
    except KeyError as exc:
        raise _not_found(str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
