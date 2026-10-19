from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.node_departure import prepare_deletion

router = APIRouter(dependencies=[Depends(require_token)], tags=["node-lifecycle"])


class PrepareRemoval(BaseModel):
    expected_node_id: str = Field(min_length=1, max_length=160)
    allow_standalone: bool = False


@router.post("/prepare")
async def prepare(body: PrepareRemoval):
    try:
        operation = await prepare_deletion(expected_node_id=body.expected_node_id, allow_standalone=body.allow_standalone)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=503, detail="Node removal preparation failed; local data has been retained") from None
    return {"ok": True, "operation": operation}


