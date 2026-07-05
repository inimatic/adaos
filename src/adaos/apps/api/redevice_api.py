from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services import redevice_lan_admission as lan

router = APIRouter()


class EnableDiscoveryRequest(BaseModel):
    ttl_s: float | None = Field(default=None)
    hub_base_url: str | None = Field(default=None)


class LanAdmissionRequest(BaseModel):
    endpoint_id: str | None = None
    device_label: str | None = None
    language: str | None = None
    diagnostic_report: dict[str, Any] | None = None
    endpoint_manifest: dict[str, Any] | None = None


class ApproveRequest(BaseModel):
    display_name: str | None = None


class DenyRequest(BaseModel):
    reason: str | None = None


class CommandRequest(BaseModel):
    command: dict[str, Any] = Field(default_factory=dict)


class EventRequest(BaseModel):
    event: dict[str, Any] = Field(default_factory=dict)


class AckRequest(BaseModel):
    state: str | None = None
    event: dict[str, Any] | None = None
    details: dict[str, Any] | None = None


class ProfileRequest(BaseModel):
    display_name: str | None = None
    aliases: list[str] | None = None


def _client_host(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return str(getattr(request.client, "host", "") or "").strip()


def _endpoint_token(header_value: str | None) -> str | None:
    return str(header_value or "").strip() or None


@router.get("/api/redevice/lan/discovery")
async def redevice_lan_discovery() -> dict[str, Any]:
    return lan.discovery_status()


@router.post("/api/redevice/lan/discovery/enable", dependencies=[Depends(require_token)])
async def redevice_lan_discovery_enable(body: EnableDiscoveryRequest) -> dict[str, Any]:
    return lan.enable_discovery(ttl_s=body.ttl_s, hub_base_url=body.hub_base_url)


@router.post("/api/redevice/lan/discovery/disable", dependencies=[Depends(require_token)])
async def redevice_lan_discovery_disable() -> dict[str, Any]:
    return lan.disable_discovery()


@router.post("/api/redevice/lan/requests")
async def redevice_lan_request(body: LanAdmissionRequest, request: Request) -> dict[str, Any]:
    return lan.submit_request(body.model_dump(exclude_none=True), client_host=_client_host(request))


@router.get("/api/redevice/lan/requests", dependencies=[Depends(require_token)])
async def redevice_lan_requests(include_terminal: bool = True) -> dict[str, Any]:
    return lan.list_requests(include_terminal=include_terminal)


@router.get("/api/redevice/lan/requests/{request_id}")
async def redevice_lan_request_status(request_id: str) -> dict[str, Any]:
    return lan.poll_request(request_id)


@router.post("/api/redevice/lan/requests/{request_id}/approve", dependencies=[Depends(require_token)])
async def redevice_lan_request_approve(request_id: str, body: ApproveRequest) -> dict[str, Any]:
    return lan.approve_request(request_id, display_name=body.display_name)


@router.post("/api/redevice/lan/requests/{request_id}/deny", dependencies=[Depends(require_token)])
async def redevice_lan_request_deny(request_id: str, body: DenyRequest) -> dict[str, Any]:
    return lan.deny_request(request_id, reason=body.reason)


@router.get("/v1/redevice/devices")
async def redevice_devices() -> dict[str, Any]:
    return lan.list_devices()


@router.post("/v1/redevice/devices/{code}/commands")
async def redevice_enqueue_command(code: str, body: CommandRequest) -> dict[str, Any]:
    return lan.enqueue_command(code, body.command)


@router.get("/v1/redevice/devices/{code}/commands/next")
async def redevice_next_command(code: str, x_redevice_token: str | None = Header(default=None)) -> dict[str, Any]:
    return lan.next_command(code, endpoint_token=_endpoint_token(x_redevice_token))


@router.post("/v1/redevice/devices/{code}/events")
async def redevice_record_event(code: str, body: EventRequest, x_redevice_token: str | None = Header(default=None)) -> dict[str, Any]:
    return lan.record_event(code, body.event, endpoint_token=_endpoint_token(x_redevice_token))


@router.post("/v1/redevice/devices/{code}/commands/{command_id}/ack")
async def redevice_ack_command(
    code: str,
    command_id: str,
    body: AckRequest,
    x_redevice_token: str | None = Header(default=None),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state": body.state,
        "event": body.event or {},
        "details": body.details or {},
    }
    return lan.ack_command(code, command_id, payload, endpoint_token=_endpoint_token(x_redevice_token))


@router.patch("/v1/redevice/devices/{code}/profile")
async def redevice_update_profile(code: str, payload: ProfileRequest) -> dict[str, Any]:
    return lan.update_profile(code, payload.model_dump(exclude_none=True))


@router.post("/v1/redevice/devices/{code}/revoke")
async def redevice_revoke(code: str) -> dict[str, Any]:
    return lan.revoke(code)


@router.post("/v1/redevice/devices/{code}/retire")
async def redevice_retire(code: str) -> dict[str, Any]:
    return lan.retire(code)
