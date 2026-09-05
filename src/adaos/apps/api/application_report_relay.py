from __future__ import annotations

import hmac
import json
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from adaos.services.applications import get_development_report_service
from adaos.services.applications.report_relay import (
    DevelopmentReportRelayBackpressure,
    DevelopmentReportRelayError,
)


router = APIRouter(
    prefix="/v1/root/applications/development-reports/relay",
    tags=["application-development-report-relay"],
)


class ForwardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: dict[str, Any]
    offer: dict[str, Any]
    source_identity: dict[str, Any]


async def _bounded_forward_request(request: Request) -> ForwardRequest:
    limit = 4_000_000
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > limit:
                raise HTTPException(status_code=413, detail="Root relay envelope is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length is invalid") from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="Root relay envelope is too large")
    try:
        payload = json.loads(body)
        return ForwardRequest.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Root relay request is invalid") from exc


def _authorize(token: str | None) -> None:
    expected = str(os.getenv("ADAOS_ROOT_RELAY_INGRESS_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Root relay ingress is not configured")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Root relay ingress token is invalid")


def _service():
    try:
        return get_development_report_service()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Development Report relay is unavailable") from exc


@router.get("/identity")
async def relay_identity(
    x_adaos_relay_token: str | None = Header(default=None, alias="X-AdaOS-Relay-Token"),
) -> dict[str, Any]:
    _authorize(x_adaos_relay_token)
    return {"ok": True, "identity": _service().relay.public_identity()}


@router.get("/directory")
async def relay_directory(
    x_adaos_relay_token: str | None = Header(default=None, alias="X-AdaOS-Relay-Token"),
) -> dict[str, Any]:
    _authorize(x_adaos_relay_token)
    return {"ok": True, "directory": _service().directory.projection()}


@router.post("/forward")
async def accept_forward(
    request: Request,
    x_adaos_relay_token: str | None = Header(default=None, alias="X-AdaOS-Relay-Token"),
) -> dict[str, Any]:
    _authorize(x_adaos_relay_token)
    body = await _bounded_forward_request(request)
    try:
        receipt = _service().relay.accept_forward(
            body.envelope,
            offer=body.offer,
            source_identity=body.source_identity,
        )
    except DevelopmentReportRelayBackpressure as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except DevelopmentReportRelayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "receipt": receipt}


@router.post("/flush")
async def flush_forward_queue(
    x_adaos_relay_token: str | None = Header(default=None, alias="X-AdaOS-Relay-Token"),
) -> dict[str, Any]:
    _authorize(x_adaos_relay_token)
    return {"ok": True, "result": _service().flush_relay_forwards(limit=100)}


__all__ = ["router"]
