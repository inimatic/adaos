from __future__ import annotations

from functools import partial

import anyio
from fastapi import APIRouter, HTTPException, Request

from adaos.services.skill.service_event_bridge import (
    MAX_SERVICE_EVENT_BYTES,
    ServiceEventBridgeError,
    publish_service_event,
)


router = APIRouter(tags=["service-event-bridge"])


@router.post("", include_in_schema=False)
async def ingest_service_event(request: Request) -> dict:
    content_length = str(request.headers.get("content-length") or "").strip()
    if content_length:
        try:
            if int(content_length) > MAX_SERVICE_EVENT_BYTES:
                raise HTTPException(status_code=413, detail="service_event_payload_too_large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="content_length_invalid") from exc
    raw = await request.body()
    if len(raw) > MAX_SERVICE_EVENT_BYTES:
        raise HTTPException(status_code=413, detail="service_event_payload_too_large")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="service_event_json_invalid") from exc
    if not isinstance(body, dict) or not isinstance(body.get("payload"), dict):
        raise HTTPException(status_code=400, detail="service_event_envelope_invalid")
    try:
        return await anyio.to_thread.run_sync(
            partial(
                publish_service_event,
                token=str(
                    request.headers.get("x-adaos-service-event-token") or ""
                ),
                topic=str(body.get("topic") or ""),
                payload=body["payload"],
                remote_host=str(request.client.host if request.client else ""),
            )
        )
    except ServiceEventBridgeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
