from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.component_updates import ComponentUpdateService


router = APIRouter(tags=["component-updates"], dependencies=[Depends(require_token)])


def _get_service() -> ComponentUpdateService:
    return ComponentUpdateService()


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
