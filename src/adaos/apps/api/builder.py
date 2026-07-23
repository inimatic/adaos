from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.builder import (
    BuilderAutomationService,
    BuilderProjectCatalogService,
    BuilderWorkflowError,
    BuilderWorkflowService,
    BuilderWorkbenchService,
    BuilderWorkspaceService,
)


router = APIRouter(dependencies=[Depends(require_token)])


def _get_service() -> BuilderWorkspaceService:
    return BuilderWorkspaceService.from_context()


def _get_workbench_service() -> BuilderWorkbenchService:
    return BuilderWorkbenchService.from_context()


def _get_automation_service() -> BuilderAutomationService:
    return BuilderAutomationService.from_context()


def _get_project_catalog_service() -> BuilderProjectCatalogService:
    return BuilderProjectCatalogService.from_context()


def _get_workflow_service() -> BuilderWorkflowService:
    return BuilderWorkflowService.from_context()


class BuilderDraftRequest(BaseModel):
    kind: str = Field(default="skill", description="skill, scenario, or descriptor_fix")
    artifact_id: str = Field(..., min_length=1)
    source_idea: str = Field(..., min_length=1)
    webspace_id: str | None = None
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
    webspace_id: str | None = None


class BuilderRealizeRequest(BaseModel):
    draft_id: str | None = Field(default=None, min_length=1)
    target: dict[str, Any] | None = None
    artifacts: dict[str, Any] | None = None
    repo: dict[str, Any] | None = None
    constraints: dict[str, Any] | None = None
    mcp: dict[str, Any] | None = None
    acceptance: dict[str, Any] | None = None
    links: dict[str, Any] | None = None
    source_session_id: str | None = None
    source_conversation_id: str | None = None
    user_subnet_id: str | None = None
    submit_remote: bool = False
    create_pending_action: bool = True


class BuilderWorkbenchEnsureRequest(BaseModel):
    webspace_id: str | None = None
    active_draft_id: str | None = None
    runtime_scenario_id: str | None = None


class BuilderActiveDraftRequest(BaseModel):
    webspace_id: str | None = None
    draft_id: str | None = None
    runtime_scenario_id: str | None = None


class BuilderAutomationStartRequest(BaseModel):
    object_type: str = Field(..., pattern="^(skill|scenario)$")
    object_id: str = Field(..., min_length=1)
    implementation_brief: str = Field(..., min_length=1)
    webspace_id: str = "desktop"
    conversation_id: str | None = None
    brief_path: str | None = None


class BuilderAutomationTurnRequest(BaseModel):
    text: str = Field(..., min_length=1)
    object_type: str | None = Field(default=None, pattern="^(skill|scenario)$")
    object_id: str | None = None
    webspace_id: str | None = None


class BuilderWorkflowTransitionRequest(BaseModel):
    object_type: str = Field(..., pattern="^(skill|scenario)$")
    object_id: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    actor: str = "builder.api"
    reason: str | None = None
    metadata: dict[str, Any] | None = None


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
            webspace_id=body.webspace_id,
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
        return service.preview(draft_id=body.draft_id, approval_profile=body.approval_profile, webspace_id=body.webspace_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/realize")
def create_realize_request(body: BuilderRealizeRequest, service: BuilderWorkspaceService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return service.create_realize_request(
            draft_id=body.draft_id,
            target=body.target,
            artifacts=body.artifacts,
            repo=body.repo,
            constraints=body.constraints,
            mcp=body.mcp,
            acceptance=body.acceptance,
            links=body.links,
            source_session_id=body.source_session_id,
            source_conversation_id=body.source_conversation_id,
            user_subnet_id=body.user_subnet_id,
            submit_remote=body.submit_remote,
            create_pending_action=body.create_pending_action,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/automation/start")
def start_automation(
    body: BuilderAutomationStartRequest,
    service: BuilderAutomationService = Depends(_get_automation_service),
) -> dict[str, Any]:
    try:
        return service.start_from_execute(
            object_type=body.object_type,
            object_id=body.object_id,
            implementation_brief=body.implementation_brief,
            webspace_id=body.webspace_id,
            conversation_id=body.conversation_id,
            brief_path=body.brief_path,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/automation/turn")
def submit_automation_turn(
    body: BuilderAutomationTurnRequest,
    service: BuilderAutomationService = Depends(_get_automation_service),
) -> dict[str, Any]:
    try:
        return service.submit_turn(
            text=body.text,
            object_type=body.object_type,
            object_id=body.object_id,
            webspace_id=body.webspace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/automation/status")
def automation_status(
    object_type: str,
    object_id: str,
    service: BuilderAutomationService = Depends(_get_automation_service),
) -> dict[str, Any]:
    return service.status(object_type=object_type, object_id=object_id)


@router.get("/workflow")
def workflow_state(
    object_type: str,
    object_id: str,
    service: BuilderWorkflowService = Depends(_get_workflow_service),
) -> dict[str, Any]:
    try:
        return {"ok": True, "workflow": service.describe(object_type, object_id)}
    except (FileNotFoundError, BuilderWorkflowError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workflow/transition")
def transition_workflow(
    body: BuilderWorkflowTransitionRequest,
    service: BuilderWorkflowService = Depends(_get_workflow_service),
) -> dict[str, Any]:
    try:
        return service.transition(
            body.object_type,
            body.object_id,
            body.action,
            actor=body.actor,
            reason=body.reason,
            metadata=body.metadata,
        )
    except (FileNotFoundError, BuilderWorkflowError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/previews/{preview_id}")
def get_preview(preview_id: str, service: BuilderWorkspaceService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "preview": service.load_preview(preview_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workbench/ensure")
async def ensure_workbench(
    body: BuilderWorkbenchEnsureRequest,
    service: BuilderWorkbenchService = Depends(_get_workbench_service),
) -> dict[str, Any]:
    return {
        "ok": True,
        "binding": await service.ensure_dev_webspace(
            body.webspace_id,
            active_draft_id=body.active_draft_id,
            runtime_scenario_id=body.runtime_scenario_id,
        ),
    }


@router.get("/workbench/binding")
def get_workbench_binding(
    webspace_id: str | None = None,
    service: BuilderWorkbenchService = Depends(_get_workbench_service),
) -> dict[str, Any]:
    return {"ok": True, "binding": service.get_workspace_binding(webspace_id)}


@router.get("/workbench/projects")
async def list_workbench_projects(
    kind: str | None = None,
    query: str | None = None,
    limit: int = 200,
    selected_object_type: str | None = None,
    selected_object_id: str | None = None,
    webspace_id: str | None = None,
    include_archived: bool = False,
    service: BuilderProjectCatalogService = Depends(_get_project_catalog_service),
) -> list[dict[str, Any]]:
    try:
        return service.list_projects(
            kind=kind,
            query=query,
            limit=limit,
            selected_object_type=selected_object_type,
            selected_object_id=selected_object_id,
            webspace_id=webspace_id,
            include_archived=include_archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workbench/open")
async def open_workbench_dev_webspace(
    webspace_id: str | None = None,
    base_url: str | None = None,
    runtime_scenario_id: str | None = None,
    service: BuilderWorkbenchService = Depends(_get_workbench_service),
) -> dict[str, Any]:
    return await service.open_dev_webspace_ready(
        webspace_id,
        base_url=base_url,
        runtime_scenario_id=runtime_scenario_id,
    )


@router.get("/workbench/dialog-widget")
def get_workbench_dialog_widget(
    webspace_id: str | None = None,
    service: BuilderWorkbenchService = Depends(_get_workbench_service),
) -> dict[str, Any]:
    binding = service.get_workspace_binding(webspace_id)
    widget = binding.get("dialog") if isinstance(binding.get("dialog"), dict) else service.dialog_widget_config(webspace_id)
    return {"ok": True, "widget": widget, "binding": binding}


@router.post("/workbench/active-draft")
async def set_workbench_active_draft(
    body: BuilderActiveDraftRequest,
    service: BuilderWorkbenchService = Depends(_get_workbench_service),
) -> dict[str, Any]:
    return {
        "ok": True,
        "binding": await service.ensure_dev_webspace(
            body.webspace_id,
            active_draft_id=body.draft_id,
            runtime_scenario_id=body.runtime_scenario_id,
        ),
    }


@router.get("/workbench/development-skills")
def list_workbench_development_skills(
    webspace_id: str | None = None,
    service: BuilderWorkbenchService = Depends(_get_workbench_service),
) -> dict[str, Any]:
    return service.list_development_skills(webspace_id)


@router.delete("/workbench/development-skills/{draft_id}")
def delete_workbench_development_skill(
    draft_id: str,
    webspace_id: str | None = None,
    service: BuilderWorkbenchService = Depends(_get_workbench_service),
) -> dict[str, Any]:
    result = service.delete_development_skill(draft_id, webspace_id)
    if not result.get("ok") and result.get("error") == "draft_not_found":
        raise HTTPException(status_code=404, detail=f"Builder draft not found: {draft_id}")
    return result
