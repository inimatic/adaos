from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.development_feedback import DevelopmentFeedbackService


router = APIRouter(tags=["development-feedback"], dependencies=[Depends(require_token)])


def _get_service() -> DevelopmentFeedbackService:
    return DevelopmentFeedbackService()


class DevelopmentFeedbackCreateRequest(BaseModel):
    source: str
    category: str
    summary: str = Field(..., min_length=3, max_length=4000)
    blocking: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)
    impact: list[str] = Field(default_factory=list)
    target_refs: list[str] = Field(default_factory=list)
    details: str = ""
    recommendation: str = ""
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    relation_refs: list[dict[str, Any]] = Field(default_factory=list)
    classification: dict[str, Any] = Field(default_factory=dict)
    dedup_key: str | None = None
    actor: str = "api"


class DevelopmentFeedbackTransitionRequest(BaseModel):
    status: str
    actor: str = "api"
    reason: str = ""
    classification: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=1)


class DevelopmentFeedbackCommentRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    actor: str = "api"
    expected_revision: int | None = Field(default=None, ge=1)


class DevelopmentFeedbackPromoteRequest(BaseModel):
    route: str = Field(..., pattern="^(project|sdk_understanding|core)$")
    actor: str = "api"
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=1)


@router.post("")
def create_feedback(
    body: DevelopmentFeedbackCreateRequest,
    service: DevelopmentFeedbackService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return service.capture(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("")
def list_feedback(
    feedback_status: str | None = Query(default=None, alias="status"),
    category: str | None = None,
    source: str | None = None,
    blocking: bool | None = None,
    target_ref: str | None = None,
    search: str | None = None,
    rejection_class: str | None = None,
    updated_since: str | None = None,
    limit: int = Query(default=200, ge=0, le=1000),
    service: DevelopmentFeedbackService = Depends(_get_service),
) -> dict[str, Any]:
    items = service.list(
        status=feedback_status,
        category=category,
        source=source,
        blocking=blocking,
        target_ref=target_ref,
        search=search,
        rejection_class=rejection_class,
        updated_since=updated_since,
        limit=limit,
    )
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/{feedback_id}")
def get_feedback(
    feedback_id: str,
    service: DevelopmentFeedbackService = Depends(_get_service),
) -> dict[str, Any]:
    item = service.get(feedback_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="development feedback not found")
    return {"ok": True, "feedback": item}


@router.post("/{feedback_id}/transition")
def transition_feedback(
    feedback_id: str,
    body: DevelopmentFeedbackTransitionRequest,
    service: DevelopmentFeedbackService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return {"ok": True, "feedback": service.transition(feedback_id, **body.model_dump())}
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        code = status.HTTP_409_CONFLICT if "revision" in str(exc) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/{feedback_id}/comments")
def comment_feedback(
    feedback_id: str,
    body: DevelopmentFeedbackCommentRequest,
    service: DevelopmentFeedbackService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return {"ok": True, "feedback": service.comment(feedback_id, **body.model_dump())}
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        code = status.HTTP_409_CONFLICT if "revision" in str(exc) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/{feedback_id}/promote")
def promote_feedback(
    feedback_id: str,
    body: DevelopmentFeedbackPromoteRequest,
    service: DevelopmentFeedbackService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return service.promote(feedback_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        code = status.HTTP_409_CONFLICT if "revision" in str(exc) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=str(exc)) from exc
