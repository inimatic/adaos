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
from adaos.services.distributed_runtime.adapters import (
    MAX_TOPOLOGY_PHASE_BYTES,
    MAX_TOPOLOGY_TRANSFER_BYTES,
    execute_registered_topology_phase,
    execute_registered_topology_transfer,
)


router = APIRouter(tags=["distributed-topology"], dependencies=[Depends(require_token)])


@router.post("/phase")
async def distributed_topology_phase(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_TOPOLOGY_PHASE_BYTES:
                raise HTTPException(
                    status_code=413, detail="topology_phase_request_too_large"
                )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="topology_phase_content_length_invalid",
            ) from None
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="topology_phase_json_invalid"
        ) from exc
    if not isinstance(payload, Mapping):
        raise HTTPException(
            status_code=400, detail="topology_phase_body_must_be_object"
        )
    try:
        return await anyio.to_thread.run_sync(
            execute_registered_topology_phase,
            dict(payload),
        )
    except RetryableTopologyPhaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:500]) from exc
    except UncertainTopologyPhaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:500]) from exc
    except TopologyExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:500]) from exc


@router.post("/transfer")
async def distributed_topology_transfer(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_TOPOLOGY_TRANSFER_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="topology_transfer_request_too_large",
                )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="topology_transfer_content_length_invalid",
            ) from None
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="topology_transfer_json_invalid",
        ) from exc
    if not isinstance(payload, Mapping):
        raise HTTPException(
            status_code=400,
            detail="topology_transfer_body_must_be_object",
        )
    try:
        return await anyio.to_thread.run_sync(
            execute_registered_topology_transfer,
            dict(payload),
        )
    except RetryableTopologyPhaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:500]) from exc
    except UncertainTopologyPhaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:500]) from exc
    except TopologyExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:500]) from exc


__all__ = ["router"]
