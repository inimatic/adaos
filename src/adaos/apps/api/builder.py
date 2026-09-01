from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from adaos.apps.api.auth import require_token
from adaos.sdk.developer import artifact_context
from adaos.services.builder import (
    BuilderAutomationService,
    BuilderProjectCatalogService,
    BuilderProjectSourceService,
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


def _get_project_source_service() -> BuilderProjectSourceService:
    return BuilderProjectSourceService.from_context()


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
    object_type: str = Field(..., pattern="^(skill|scenario|project)$")
    object_id: str = Field(..., min_length=1)
    implementation_brief: str = Field(..., min_length=1)
    webspace_id: str = "desktop"
    conversation_id: str | None = None
    brief_path: str | None = None


class BuilderAutomationTurnRequest(BaseModel):
    text: str = Field(..., min_length=1)
    object_type: str | None = Field(default=None, pattern="^(skill|scenario|project)$")
    object_id: str | None = None
    webspace_id: str | None = None
    execution_budget: dict[str, Any] | None = None


class BuilderAutomationRecoveryRequest(BaseModel):
    object_type: str = Field(..., pattern="^(skill|scenario|project)$")
    object_id: str = Field(..., min_length=1)


class BuilderWorkflowTransitionRequest(BaseModel):
    object_type: str = Field(..., pattern="^(skill|scenario|project)$")
    object_id: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    actor: str = "builder.api"
    reason: str | None = None
    metadata: dict[str, Any] | None = None
    expected_generation: int | None = Field(default=None, ge=0)


class BuilderTrialDecisionRequest(BaseModel):
    object_type: str = Field(..., pattern="^(skill|scenario|project)$")
    object_id: str = Field(..., min_length=1)
    decision: str = Field(..., pattern="^(accept|revise|rollback)$")
    actor: str = Field(default="user:owner", min_length=1)
    reason: str = ""


@router.get("/approval-profiles")
def approval_profiles(service: BuilderWorkspaceService = Depends(_get_service)) -> dict[str, Any]:
    return {"ok": True, "profiles": service.approval_profiles()}


@router.put("/projects/{kind}/{project_id}/sources/{filename:path}")
async def add_project_source(
    kind: str,
    project_id: str,
    filename: str,
    request: Request,
    role: str = "source",
    expected_size_bytes: int | None = None,
    service: BuilderProjectSourceService = Depends(_get_project_source_service),
) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > service.max_source_bytes:
                raise HTTPException(status_code=413, detail=f"source exceeds max size: {service.max_source_bytes} bytes")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid content-length") from None
    chunks: list[bytes] = []
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > service.max_source_bytes:
                raise HTTPException(status_code=413, detail=f"source exceeds max size: {service.max_source_bytes} bytes")
            chunks.append(bytes(chunk))
    except ClientDisconnect as exc:
        raise HTTPException(status_code=499, detail="upload client disconnected") from exc
    if expected_size_bytes is not None and size != int(expected_size_bytes):
        raise HTTPException(
            status_code=400,
            detail=f"upload size mismatch: expected {int(expected_size_bytes)} bytes, received {size} bytes",
        )
    try:
        return service.add_bytes(
            kind=kind,
            project_id=project_id,
            name=filename,
            payload=b"".join(chunks),
            media_type=request.headers.get("content-type"),
            role=role,
            origin={"kind": "builder_upload"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/{kind}/{project_id}/sources")
def get_project_sources(
    kind: str,
    project_id: str,
    service: BuilderProjectSourceService = Depends(_get_project_source_service),
) -> dict[str, Any]:
    try:
        return {"ok": True, "state": service.get_state(kind, project_id), "bundle": service.current_bundle(kind, project_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/{kind}/{project_id}/sources/{digest}/content")
def get_project_source_content(
    kind: str,
    project_id: str,
    digest: str,
    service: BuilderProjectSourceService = Depends(_get_project_source_service),
) -> Response:
    normalized = digest if digest.startswith("sha256:") else f"sha256:{digest}"
    try:
        bundle = service.current_bundle(kind, project_id)
        source = next((item for item in bundle.get("sources") or [] if item.get("digest") == normalized), None)
        if source is None:
            raise FileNotFoundError("source is not a member of the current project SourceBundle")
        return Response(content=service.read_source(normalized), media_type=str(source.get("media_type") or "application/octet-stream"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/skill/{skill_id}/artifacts/{group_id}/{artifact_id}/content")
def get_skill_artifact_content(
    skill_id: str,
    group_id: str,
    artifact_id: str,
    download: bool = False,
) -> FileResponse:
    """Stream one manifest-admitted DEV artifact without exposing a native path."""

    try:
        resolved = artifact_context.resolve(skill_id, group_id, artifact_id)
    except artifact_context.ArtifactContextError as exc:
        message = str(exc)
        status = 404 if "was not found" in message or "is missing" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    filename = str(resolved.get("path") or artifact_id)
    media_type = artifact_context.media_type_for_name(filename, str(resolved.get("media_type") or ""))
    return FileResponse(
        str(resolved["native_path"]),
        media_type=media_type,
        filename=filename if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


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
            execution_budget=body.execution_budget,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/automation/status")
def automation_status(
    object_type: str,
    object_id: str,
    details: bool = False,
    service: BuilderAutomationService = Depends(_get_automation_service),
) -> dict[str, Any]:
    return service.status(
        object_type=object_type,
        object_id=object_id,
        include_session=details,
    )


@router.post("/automation/recover-validated")
def recover_validated_automation(
    body: BuilderAutomationRecoveryRequest,
    service: BuilderAutomationService = Depends(_get_automation_service),
) -> dict[str, Any]:
    """Resume validated post-Codex work inside the initialized node context."""

    try:
        return service.recover_validated_result(
            object_type=body.object_type,
            object_id=body.object_id,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/trial/decision")
def decide_trial(
    body: BuilderTrialDecisionRequest,
    service: BuilderAutomationService = Depends(_get_automation_service),
) -> dict[str, Any]:
    try:
        return service.decide_aprobation(
            object_type=body.object_type,
            object_id=body.object_id,
            decision=body.decision,
            actor=body.actor,
            reason=body.reason,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
            expected_generation=body.expected_generation,
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


@router.get("/workbench/context-inspector")
def get_workbench_context_inspector(
    webspace_id: str | None = None,
    run_ref: str | None = None,
    limit: int = 20,
    service: BuilderWorkbenchService = Depends(_get_workbench_service),
) -> dict[str, Any]:
    return {
        "ok": True,
        "inspector": service.context_inspector(
            webspace_id,
            run_ref=run_ref,
            limit=max(1, min(int(limit), 100)),
        ),
    }


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
    ticket_id: str | None = None,
    selected_object_type: str | None = None,
    selected_object_id: str | None = None,
    service: BuilderWorkbenchService = Depends(_get_workbench_service),
) -> dict[str, Any]:
    try:
        return await service.open_dev_webspace_ready(
            webspace_id,
            base_url=base_url,
            runtime_scenario_id=runtime_scenario_id,
            ticket_id=ticket_id,
            selected_object_type=selected_object_type,
            selected_object_id=selected_object_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
