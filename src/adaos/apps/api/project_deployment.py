from __future__ import annotations

from typing import Any, Mapping

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request

from adaos.apps.api.auth import require_token
from adaos.services.project_deployment import (
    ProjectDeploymentExecutionError,
    RetryableDeploymentPhaseError,
    execute_remote_component_phase,
)
from adaos.services.project_deployment.transport import MAX_REMOTE_PACKAGE_BYTES


router = APIRouter(tags=["project-deployment"], dependencies=[Depends(require_token)])
_MAX_REQUEST_BYTES = MAX_REMOTE_PACKAGE_BYTES * 4 // 3 + 2 * 1024 * 1024


@router.post("/phase")
async def project_deployment_phase(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="remote_phase_request_too_large")
        except ValueError:
            raise HTTPException(status_code=400, detail="remote_phase_content_length_invalid") from None
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="remote_phase_json_invalid") from exc
    if not isinstance(payload, Mapping):
        raise HTTPException(status_code=400, detail="remote_phase_body_must_be_object")
    try:
        return await anyio.to_thread.run_sync(
            execute_remote_component_phase,
            dict(payload),
        )
    except RetryableDeploymentPhaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:500]) from exc
    except ProjectDeploymentExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:500]) from exc


__all__ = ["router"]
