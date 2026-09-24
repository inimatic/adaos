from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.applications.access import ApplicationAccessError
from adaos.services.applications.runtime_channel import RuntimeChannelConflict
from adaos.services.component_updates import ComponentUpdateService


router = APIRouter(tags=["component-updates"], dependencies=[Depends(require_token)])


def _get_service() -> ComponentUpdateService:
    return ComponentUpdateService()


def _acceptance_receipt(result: Any) -> dict[str, Any]:
    """Return the stable acceptance identity, not its internal projections.

    Publication carries full workflow and runtime-refresh diagnostics for local
    recovery.  Sending those private projections to the desktop made a single
    successful acceptance response exceed a megabyte even though the client
    only needs acknowledgement and the newly selected runtime identity.
    """

    source = dict(result) if isinstance(result, dict) else {}
    workflow = source.get("workflow") if isinstance(source.get("workflow"), dict) else {}
    delivery = workflow.get("delivery") if isinstance(workflow.get("delivery"), dict) else {}
    publication = workflow.get("publication") if isinstance(workflow.get("publication"), dict) else {}
    installation = source.get("installation") if isinstance(source.get("installation"), dict) else {}
    selection = source.get("runtime_selection") if isinstance(source.get("runtime_selection"), dict) else {}
    verification = (
        source.get("application_verification")
        if isinstance(source.get("application_verification"), dict)
        else {}
    )
    promoted = (
        source.get("publication_verification")
        if isinstance(source.get("publication_verification"), dict)
        else {}
    )
    report = promoted.get("report") if isinstance(promoted.get("report"), dict) else {}
    return {
        "schema": "adaos.component_trial_acceptance_receipt.v1",
        "ok": bool(source.get("ok", True)),
        "workflow": {
            "generation": workflow.get("generation"),
            "delivery_status": delivery.get("status"),
            "publication_status": publication.get("status"),
        },
        "installation": {
            key: installation.get(key)
            for key in ("application_id", "status", "installed_release_digest", "revision")
            if installation.get(key) is not None
        },
        "runtime_selection": selection,
        "application_verification": {
            key: verification.get(key)
            for key in ("application_id", "release_digest", "stage", "report_digest")
            if verification.get(key) is not None
        },
        "publication_verification": {
            "publication_allowed": promoted.get("publication_allowed"),
            "promoted": promoted.get("promoted"),
            "report_digest": report.get("report_digest") or promoted.get("report_digest"),
        },
    }


class ComponentUpdateResponseRequest(BaseModel):
    action: str = Field(pattern="^(presented|review_started|dismiss_auto|restore_auto)$")
    actor: str = "user:local"
    webspace_id: str = "desktop"


@router.get("")
def list_component_updates(
    component_type: str | None = Query(default=None),
    component_id: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    status: str | None = Query(default="active"),
    actor: str = Query(default="user:local"),
    webspace_id: str = Query(default="desktop"),
    unread_only: bool = Query(default=False),
    service: ComponentUpdateService = Depends(_get_service),
) -> dict[str, Any]:
    service.reconcile_builder_sessions()
    service.reconcile_local_trials(webspace_id)
    items = service.list_notices(
        component_type=component_type,
        component_id=component_id,
        stage=stage,
        status=status,
        actor=actor,
        webspace_id=webspace_id,
        unread_only=unread_only,
    )
    return {
        "ok": True,
        "items": items,
        "total": len(items),
        "unread": sum(1 for item in items if item.get("unread")),
        "awaiting_decision": sum(
            1
            for item in items
            if bool((item.get("transition") or {}).get("requires_user_decision"))
        ),
        "publishing": sum(
            1
            for item in items
            if str((item.get("transition") or {}).get("state") or "") == "publishing"
        ),
        "workspace_committed": sum(
            1
            for item in items
            if bool((item.get("transition") or {}).get("workspace_committed"))
        ),
    }


@router.post("/{notice_id}/respond")
def respond_to_component_update(
    notice_id: str,
    body: ComponentUpdateResponseRequest,
    service: ComponentUpdateService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        notice = service.respond(
            notice_id,
            action=body.action,
            actor=body.actor,
            webspace_id=body.webspace_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="component_update_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "notice": notice}


class ComponentTrialAcceptRequest(BaseModel):
    candidate_id: str = Field(min_length=1)
    candidate_digest: str = Field(min_length=1)
    webspace_id: str = Field(min_length=1)
    confirmed: bool = False


@router.post("/{notice_id}/accept-trial")
def accept_component_trial(notice_id: str, body: ComponentTrialAcceptRequest,
                           service: ComponentUpdateService = Depends(_get_service)) -> dict[str, Any]:
    from adaos.services.personalization_runtime import current_user_id

    if not body.confirmed:
        raise HTTPException(status_code=409, detail="Workspace acceptance requires confirmation")
    try:
        result = service.accept_local_trial(notice_id, candidate_id=body.candidate_id,
            candidate_digest=body.candidate_digest, webspace_id=body.webspace_id,
            actor="user:" + current_user_id())
        return _acceptance_receipt(result)
    except (ApplicationAccessError, ValueError, FileNotFoundError, RuntimeChannelConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
