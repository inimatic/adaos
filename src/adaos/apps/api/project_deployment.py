from __future__ import annotations

from typing import Any, Mapping

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request

from adaos.apps.api.auth import require_token
from adaos.services.project_deployment import (
    ProjectDeploymentExecutionError,
    RetryableDeploymentPhaseError,
    execute_remote_component_phase,
    observe_remote_component_phase,
)
from adaos.services.project_deployment.authority import (
    ProjectDeploymentAuthorityError,
    execute_authority_request,
)
from adaos.services.project_deployment.transport import MAX_REMOTE_PACKAGE_BYTES


router = APIRouter(tags=["project-deployment"], dependencies=[Depends(require_token)])
_MAX_REQUEST_BYTES = MAX_REMOTE_PACKAGE_BYTES * 4 // 3 + 2 * 1024 * 1024
_MAX_AUTHORITY_REQUEST_BYTES = 512 * 1024


def _require_loopback(request: Request) -> None:
    host = str(request.client.host if request.client else "").strip().lower()
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=403, detail="deployment_authority_loopback_required"
        )


@router.post("/authority")
async def project_deployment_authority(request: Request) -> dict[str, Any]:
    _require_loopback(request)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_AUTHORITY_REQUEST_BYTES:
                raise HTTPException(
                    status_code=413, detail="deployment_authority_request_too_large"
                )
        except ValueError:
            raise HTTPException(
                status_code=400, detail="deployment_authority_content_length_invalid"
            ) from None
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="deployment_authority_json_invalid"
        ) from exc
    if not isinstance(payload, Mapping):
        raise HTTPException(
            status_code=400, detail="deployment_authority_body_must_be_object"
        )
    try:
        return await anyio.to_thread.run_sync(execute_authority_request, dict(payload))
    except ProjectDeploymentAuthorityError as exc:
        status_code = (
            503 if "unavailable" in str(exc) or "candidate" in str(exc) else 400
        )
        raise HTTPException(status_code=status_code, detail=str(exc)[:500]) from exc


@router.post("/phase")
async def project_deployment_phase(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_REQUEST_BYTES:
                raise HTTPException(
                    status_code=413, detail="remote_phase_request_too_large"
                )
        except ValueError:
            raise HTTPException(
                status_code=400, detail="remote_phase_content_length_invalid"
            ) from None
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="remote_phase_json_invalid"
        ) from exc
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


@router.post("/phase/status")
async def project_deployment_phase_status(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="remote_phase_status_json_invalid"
        ) from exc
    if not isinstance(payload, Mapping):
        raise HTTPException(
            status_code=400, detail="remote_phase_status_body_must_be_object"
        )
    try:
        return await anyio.to_thread.run_sync(
            observe_remote_component_phase,
            dict(payload),
        )
    except ProjectDeploymentExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:500]) from exc


__all__ = ["router"]
