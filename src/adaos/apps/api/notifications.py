"""Authenticated private notification commands, independent of shared desktop Yjs."""
from __future__ import annotations

import asyncio
from functools import wraps
import re
import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from adaos.apps.api.auth import require_token
from adaos.domain.notifications import NotificationConflict, NotificationError, NotificationUnavailable
from adaos.domain.notifications import NotificationBackpressure
from adaos.domain.notifications import digest
from adaos.domain.personalization_access import SubjectRef
from adaos.services.agent_context import get_ctx
from adaos.services.notifications.runtime import NotificationService
from adaos.services.notifications.endpoints import EndpointService
from adaos.services.personalization_runtime import current_subnet_id, personalization_access_service


async def notification_user(request: Request):
    credential = str(request.headers.get("X-AdaOS-Desktop-Credential") or "")
    bearer = str(request.headers.get("Authorization") or "")
    if bearer.lower().startswith("bearer adaos-desktop-v1."):
        credential = bearer[7:].strip()
    if credential:
        from adaos.services.desktop_access import DesktopAccessService
        try:
            admitted = await asyncio.to_thread(DesktopAccessService().authenticate, credential)
            actor = admitted.actor
        except (PermissionError, ValueError) as exc:
            raise HTTPException(401, "notification_user_not_authenticated") from exc
    else:
        await require_token(request, request.headers.get("X-AdaOS-Token"), request.headers.get("Authorization"))
        actor = request.state.adaos_verified_caller
    subject = actor
    if actor.kind == "session":
        record = personalization_access_service(get_ctx()).store.get_session(actor.id)
        bound = (record or {}).get("subject") or {}
        if bound.get("kind") == "user" and bound.get("id"):
            subject = SubjectRef("user", bound["id"])
    if subject.kind != "user":
        raise HTTPException(403, "notifications_require_a_personal_user")
    request.state.notification_actor = actor
    request.state.notification_subject = subject


router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=[Depends(notification_user)])
relay_router = APIRouter(prefix="/notification-relay", tags=["notification-relay"])


def _scope(request: Request, *, write=False):
    subject = request.state.notification_subject
    ctx = get_ctx()
    personalization_access_service(ctx).require_user_private_content_access(
        actor=request.state.notification_actor, subject=subject,
        action="preferences.write.self" if write else "preferences.read.self",
        resource="notifications", audit_success=False)
    return subject.ref(), "subnet:" + current_subnet_id(ctx).removeprefix("subnet:")


def _private(response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization, X-AdaOS-Token, X-AdaOS-Desktop-Credential, Cookie"


def _session(request: Request, response: Response, *, create=False):
    name = "adaos_notification_" + digest(current_subnet_id(get_ctx()))[:16]
    token = str(request.headers.get("X-AdaOS-Notification-Session") or "").strip()
    if re.fullmatch(r"[a-f0-9]{48}", token):
        return "notification-session:" + token
    cookie = request.cookies.get(name, "")
    if not re.fullmatch(r"[a-f0-9]{48}", cookie):
        if not create:
            raise PermissionError("notification device session is not enrolled")
        cookie = secrets.token_hex(24)
        response.set_cookie(name, cookie, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/", max_age=2592000)
    return "notification-session:" + cookie


def _errors(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PermissionError as exc:
            raise HTTPException(403, {"code": "permission_denied", "message": str(exc)[:4000], "retryable": False}) from exc
        except NotificationConflict as exc:
            raise HTTPException(409, exc.as_dict()) from exc
        except NotificationBackpressure as exc:
            raise HTTPException(429, exc.as_dict(), headers={"Retry-After": str(exc.retry_after_seconds)}) from exc
        except NotificationUnavailable as exc:
            raise HTTPException(503, exc.as_dict()) from exc
        except (NotificationError, ValueError) as exc:
            raise HTTPException(422, exc.as_dict() if isinstance(exc, NotificationError) else {
                "code": "notification_invalid", "message": str(exc)[:4000], "retryable": False}) from exc
    return call


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PreferencesBody(StrictBody):
    settings: dict[str, Any]
    expected_revision: int = Field(ge=0)


class RecipientBody(StrictBody):
    action: Literal["read", "archive", "snooze"]
    value: int | float | bool | None
    expected_revision: int = Field(ge=0)
    operation_key: str = Field(min_length=1, max_length=240)


class EndpointBody(StrictBody):
    status: Literal["active", "paused", "revoked"]
    expected_revision: int = Field(ge=1)


class DeviceBody(StrictBody):
    kind: Literal["browser", "native"] = "browser"
    permission: Literal["default", "granted", "denied"] = "default"


class EndpointTestBody(StrictBody):
    expected_revision: int = Field(ge=1)
    operation_key: str = Field(min_length=1, max_length=240)
    allow_quiet: bool = False
    locale: str = Field(default="en", min_length=2, max_length=5)


class EndpointReconnectBody(StrictBody):
    expected_revision: int = Field(ge=1)
    permission: Literal["default", "granted", "denied"] | None = None


class DeviceAck(StrictBody):
    intent_id: str = Field(min_length=1, max_length=240)
    presentation_token: str = Field(min_length=1, max_length=100)
    evidence: Literal["presented", "system_accepted", "permission_denied", "failed"]


class RelayCheck(StrictBody):
    intent_id: str = Field(min_length=1, max_length=240)
    token: str = Field(min_length=48, max_length=48)


@relay_router.post("/authorize")
@_errors
def authorize_relay(body: RelayCheck, response: Response):
    from adaos.services.notifications.relay import authorize
    _private(response)
    return authorize(body.intent_id, body.token)


@router.get("")
@_errors
def list_notifications(request: Request, response: Response,
                       limit: int = Query(50, ge=1, le=100), cursor: str | None = Query(None, max_length=1024),
                       view: Literal["all", "unread", "archived", "snoozed", "needs_action"] = "all"):
    user, _ = _scope(request)
    _private(response)
    return NotificationService().list(user, limit=limit, cursor=cursor, view=view)


@router.get("/preferences")
@_errors
def read_preferences(request: Request, response: Response):
    user, subnet = _scope(request)
    _private(response)
    return NotificationService().preferences.read(user, subnet)


@router.put("/preferences")
@_errors
def write_preferences(body: PreferencesBody, request: Request, response: Response):
    user, subnet = _scope(request, write=True)
    _private(response)
    return NotificationService().write_preferences(user, subnet, body.settings, expected_revision=body.expected_revision)


@router.get("/endpoints")
@_errors
def endpoints(request: Request, response: Response, discover: bool = False):
    user, subnet = _scope(request)
    _private(response)
    service = EndpointService()
    return {"items": service.discover_messengers(user, subnet) if discover else service.list(user, subnet)}


@router.get("/export")
@_errors
def export_notifications(request: Request, response: Response, limit: int = Query(50, ge=1, le=100),
                         cursor: str | None = Query(None, max_length=1024)):
    user, _ = _scope(request)
    _private(response)
    return NotificationService().list(user, limit=limit, cursor=cursor, _export=True)


@router.delete("/history")
@_errors
def erase_notification_history(request: Request, response: Response):
    user, _ = _scope(request, write=True)
    _private(response)
    return NotificationService().clear_history(user)


@router.delete("/data")
@_errors
def reset_notification_data(request: Request, response: Response):
    """Explicit notification reset: erase snapshots and revoke endpoint/publisher grants."""
    user, _ = _scope(request, write=True)
    _private(response)
    return NotificationService().forget_user(user)


@router.post("/devices")
@_errors
def register_device(body: DeviceBody, request: Request, response: Response):
    user, subnet = _scope(request, write=True)
    _private(response)
    session_ref = _session(request, response, create=True)
    endpoint = EndpointService().admit_device(
        user, subnet, session_ref, kind=body.kind, permission=body.permission
    )
    # The browser keeps this opaque capability in memory.  It avoids a
    # credentialed-CORS dependency while remaining bound to the admitted user,
    # subnet and endpoint session inside EndpointService.
    return {
        **endpoint,
        "device_session_token": session_ref.removeprefix("notification-session:"),
    }


@router.patch("/endpoints/{endpoint_id}")
@_errors
def update_endpoint(endpoint_id: str, body: EndpointBody, request: Request, response: Response):
    user, _ = _scope(request, write=True)
    _private(response)
    return EndpointService().set_status(user, endpoint_id, status=body.status, expected_revision=body.expected_revision)


@router.get("/endpoints/{endpoint_id}/pending")
@_errors
def device_pending(endpoint_id: str, request: Request, response: Response):
    from adaos.services.notifications.device_delivery import pull
    user, _ = _scope(request)
    _private(response)
    return pull(user, endpoint_id, _session(request, response))


@router.post("/endpoints/{endpoint_id}/test")
@_errors
def test_endpoint(endpoint_id: str, body: EndpointTestBody, request: Request, response: Response):
    from adaos.services.notifications.probes import send_test
    user, subnet = _scope(request, write=True)
    _private(response)
    return send_test(get_ctx(), user, subnet, request.state.notification_actor.ref(), endpoint_id, **body.model_dump())


@router.post("/endpoints/{endpoint_id}/reconnect")
@_errors
def reconnect_endpoint(endpoint_id: str, body: EndpointReconnectBody, request: Request, response: Response):
    user, subnet = _scope(request, write=True)
    _private(response)
    try:
        session_ref = _session(request, response)
    except PermissionError:
        session_ref = None
    return EndpointService().reconnect(user, subnet, endpoint_id, expected_revision=body.expected_revision,
        permission=body.permission, session_ref=session_ref)


@router.get("/endpoints/{endpoint_id}/deliveries")
@_errors
def inspect_endpoint(endpoint_id: str, request: Request, response: Response,
                     limit: int = Query(20, ge=1, le=100), cursor: str | None = Query(None, max_length=240)):
    from adaos.services.notifications.inspection import inspect_endpoint as inspect
    user, subnet = _scope(request)
    _private(response)
    return inspect(get_ctx(), user, subnet, endpoint_id, limit=limit, cursor=cursor)


@router.post("/endpoints/{endpoint_id}/ack")
@_errors
def device_ack(endpoint_id: str, body: DeviceAck, request: Request, response: Response):
    from adaos.services.notifications.device_delivery import acknowledge
    user, _ = _scope(request, write=True)
    _private(response)
    return acknowledge(user, endpoint_id, _session(request, response), **body.model_dump())


@router.get("/records/{notification_id}")
@_errors
def get_notification(notification_id: str, request: Request, response: Response):
    user, _ = _scope(request)
    _private(response)
    service = NotificationService()
    record = service.get(user, notification_id)
    if record is None:
        raise HTTPException(404, "notification_not_found")
    return service.public_record(record)


@router.post("/records/{notification_id}/state")
@_errors
def recipient_command(notification_id: str, body: RecipientBody, request: Request, response: Response):
    user, _ = _scope(request, write=True)
    _private(response)
    return NotificationService().recipient_command(user, notification_id, **body.model_dump())
