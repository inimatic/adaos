from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from adaos.apps.api.auth import require_token
from adaos.services.supervisor_event_bridge import (
    MAX_SUPERVISOR_EVENT_BYTES,
    SupervisorEventBridgeError,
    publish_supervisor_event,
)


router = APIRouter(
    tags=["supervisor-event-bridge"],
    dependencies=[Depends(require_token)],
)


@router.post("", include_in_schema=False)
async def ingest_supervisor_event(request: Request) -> dict[str, object]:
    content_length = str(request.headers.get("content-length") or "").strip()
    if content_length:
        try:
            if int(content_length) > MAX_SUPERVISOR_EVENT_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="supervisor_event_payload_too_large",
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="content_length_invalid") from exc
    raw = await request.body()
    if len(raw) > MAX_SUPERVISOR_EVENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail="supervisor_event_payload_too_large",
        )
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="supervisor_event_json_invalid") from exc
    if not isinstance(body, dict) or not isinstance(body.get("payload"), dict):
        raise HTTPException(status_code=400, detail="supervisor_event_envelope_invalid")
    try:
        return publish_supervisor_event(
            topic=str(body.get("topic") or ""),
            payload=body["payload"],
            remote_host=str(request.client.host if request.client else ""),
        )
    except SupervisorEventBridgeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
