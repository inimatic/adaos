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
    cookie = request.cookies.get("adaos_notification_session", "")
    if not re.fullmatch(r"[a-f0-9]{48}", cookie):
        if not create:
            raise PermissionError("notification device session is not enrolled")
        cookie = secrets.token_hex(24)
        response.set_cookie("adaos_notification_session", cookie, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/api/notifications", max_age=2592000)
    return "notification-session:" + cookie


def _errors(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except NotificationConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except NotificationUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except (NotificationError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
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


class DeviceAck(StrictBody):
    intent_id: str = Field(min_length=1, max_length=240)
    presentation_token: str = Field(min_length=1, max_length=100)
    evidence: Literal["presented", "permission_denied", "failed"]


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
    return NotificationService().preferences.write(user, subnet, body.settings, expected_revision=body.expected_revision)


@router.get("/endpoints")
@_errors
def endpoints(request: Request, response: Response, discover: bool = False):
    user, subnet = _scope(request)
    _private(response)
    service = EndpointService()
    return {"items": service.discover_messengers(user, subnet) if discover else service.list(user, subnet)}


@router.post("/devices")
@_errors
def register_device(body: DeviceBody, request: Request, response: Response):
    user, subnet = _scope(request, write=True)
    _private(response)
    return EndpointService().admit_device(user, subnet, _session(request, response, create=True),
                                         kind=body.kind, permission=body.permission)


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
