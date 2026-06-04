from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.builder import BuilderWorkspaceService


router = APIRouter(dependencies=[Depends(require_token)])


def _get_service() -> BuilderWorkspaceService:
    return BuilderWorkspaceService.from_context()


class BuilderDraftRequest(BaseModel):
    kind: str = Field(default="skill", description="skill, scenario, or descriptor_fix")
    artifact_id: str = Field(..., min_length=1)
    source_idea: str = Field(..., min_length=1)
    task_id: str | None = None
    source: dict[str, Any] | None = None
    template_id: str | None = None
    target_kind: str | None = None
    target_root: str | None = None
    descriptor_changes: dict[str, Any] | None = None
    links: dict[str, Any] | None = None


class BuilderPreviewRequest(BaseModel):
    draft_id: str = Field(..., min_length=1)
    approval_profile: str | None = Field(default=None, description="Builder approval profile id.")


@router.get("/approval-profiles")
def approval_profiles(service: BuilderWorkspaceService = Depends(_get_service)) -> dict[str, Any]:
    return {"ok": True, "profiles": service.approval_profiles()}


@router.post("/draft")
def create_draft(body: BuilderDraftRequest, service: BuilderWorkspaceService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return service.create_draft(
            kind=body.kind,
            artifact_id=body.artifact_id,
            source_idea=body.source_idea,
            task_id=body.task_id,
            source=body.source,
            template_id=body.template_id,
            target_kind=body.target_kind,
            target_root=body.target_root,
            descriptor_changes=body.descriptor_changes,
            links=body.links,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, service: BuilderWorkspaceService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "draft": service.load_draft(draft_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/preview")
def preview(body: BuilderPreviewRequest, service: BuilderWorkspaceService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return service.preview(draft_id=body.draft_id, approval_profile=body.approval_profile)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/previews/{preview_id}")
def get_preview(preview_id: str, service: BuilderWorkspaceService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "preview": service.load_preview(preview_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
