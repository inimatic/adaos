from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from adaos.apps.api.auth import require_token
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.operations import get_operation_manager, retry_operation


router = APIRouter(tags=["operations"], dependencies=[Depends(require_token)])


def _operation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="operation_not_found")
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc) or "operation_conflict")


@router.get("")
def list_operations(
    webspace_id: str | None = Query(default=None),
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    return get_operation_manager(ctx).snapshot(webspace_id=webspace_id)


@router.get("/{operation_id}")
def get_operation(
    operation_id: str,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return get_operation_manager(ctx).operation(operation_id)
    except KeyError as exc:
        raise _operation_error(exc) from exc


@router.post("/{operation_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_operation(
    operation_id: str,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return get_operation_manager(ctx).cancel_operation(operation_id)
    except (KeyError, ValueError) as exc:
        raise _operation_error(exc) from exc


@router.post("/{operation_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_failed_operation(
    operation_id: str,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return retry_operation(operation_id, ctx=ctx)
    except (KeyError, ValueError) as exc:
        raise _operation_error(exc) from exc
