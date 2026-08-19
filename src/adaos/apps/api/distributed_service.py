from __future__ import annotations

from typing import Any, Mapping

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request

from adaos.apps.api.auth import require_token
from adaos.services.distributed_runtime import (
    RetryableTopologyPhaseError,
    TopologyExecutionError,
    UncertainTopologyPhaseError,
)
from adaos.services.distributed_runtime.service_invocation import (
    MAX_SERVICE_INVOCATION_BYTES,
    execute_registered_service_invocation,
)


router = APIRouter(tags=["distributed-service"], dependencies=[Depends(require_token)])


@router.post("/invoke")
async def distributed_service_invoke(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_SERVICE_INVOCATION_BYTES:
                raise HTTPException(status_code=413, detail="service_invocation_request_too_large")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="service_invocation_content_length_invalid",
            ) from None
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="service_invocation_json_invalid") from exc
    if not isinstance(payload, Mapping):
        raise HTTPException(status_code=400, detail="service_invocation_body_must_be_object")
    try:
        return await anyio.to_thread.run_sync(
            execute_registered_service_invocation,
            dict(payload),
        )
    except RetryableTopologyPhaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:500]) from exc
    except UncertainTopologyPhaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:500]) from exc
    except TopologyExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:500]) from exc


__all__ = ["router"]
