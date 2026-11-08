"""Release-owned notifications, private attention controls and captured publishers.

Ordinary tool replies use the existing chat/conversation contract. These APIs
publish asynchronous facts and never execute an Interaction's business effect.
Sync operations perform local I/O; async handlers use their a_* counterparts.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
from functools import partial
from contextvars import ContextVar
from typing import Any, Literal, Mapping

from adaos.domain.notifications import NotificationPage, NotificationReceipt, compile_manifest, validator
from adaos.sdk.core.contracts import public_contract

_test_runtime = ContextVar("adaos_notification_test_runtime", default=None)


def _runtime():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        fixture = _test_runtime.get()
        if fixture is not None:
            return fixture
        from adaos.services.notifications.sdk_runtime import NotificationSDKRuntime
        return NotificationSDKRuntime()
    raise RuntimeError("notification I/O in async handlers requires a_* SDK methods")


def _contract(*, capability="notifications.send", effect="notification_intent", bound=1):
    return public_contract(capabilities=(capability,), permissions=(capability,),
        effects=(effect,), errors=("permission_denied", "notification_conflict", "notification_unavailable", "notification_invalid"),
        boundedness={"kind": "single_result" if bound == 1 else "bounded_collection", "max_items": bound},
        stability="beta", since="2027", runtime_support={"owner": "notifications", "min_contract": 1},
        action_closure={"owner": "Core", "preferences": "verified_user_only",
                        "delivery": "independent_receipts", "interaction": "existing_PA_GWR_authority"})


@public_contract(capabilities=("notifications.authoring",), effects=(),
    boundedness={"kind": "bounded_contract", "max_categories": 100, "max_payload_bytes": 131072},
    stability="beta", runtime_support={"owner": "notifications", "min_contract": 1})
def validate_declaration(declaration: Mapping[str, Any]) -> dict[str, Any]:
    """Compile inline typed category/template contracts and permission disclosure.

    This is pure validation, grants no permission and sends nothing. Package-file
    references are resolved only by Core admission of the selected owned package.
    """
    return compile_manifest(declaration)


@public_contract(capabilities=("notifications.authoring",), effects=(),
    boundedness={"kind": "exact_schema", "max_items": 1}, stability="beta")
def contract(name: Literal["manifest", "record", "preferences", "endpoint", "receipt", "attention_decision"]) -> dict[str, Any]:
    """Read an exact versioned notification JSON schema; grants no runtime authority."""
    if name not in {"manifest", "record", "preferences", "endpoint", "receipt", "attention_decision"}:
        raise ValueError("unknown notification contract")
    return copy.deepcopy(validator(name).schema)


@_contract()
def emit(*, category_id: str, event_id: str, source_id: str, source_revision: int,
         occurred_at: float, payload: Mapping[str, Any], urgency: Literal["normal", "important", "urgent"] = "normal",
         locale: str = "en") -> NotificationReceipt:
    """Notify the verified initiating user of an owned committed source fact.

    Capture a stable event ID and occurrence time in the source transaction.
    Reusing that exact event after restart returns the same acceptance receipt.
    No recipients, bot IDs, credentials or policy overrides are accepted.
    DEV uses a synthetic recipient and cannot send production messages.
    """
    runtime = _runtime()
    binding = runtime.producer()
    return runtime.service.emit(binding, category_id=category_id, event_id=event_id, source_id=source_id,
        source_revision=source_revision, occurred_at=occurred_at, payload=payload, urgency=urgency, locale=locale)


@_contract(effect="publisher_capture")
def capture_publisher(*, lifetime_seconds: int = 86400) -> dict[str, Any]:
    """Capture an opaque, expiring Core publisher for a background continuation.

    Persist publisher_ref with the task. It retains original Application/user/
    namespace/author, not the UI's later focus. It grants no new permission.
    """
    return _runtime().capture(lifetime_seconds=lifetime_seconds)


@_contract()
def emit_captured(publisher_ref: str, *, category_id: str, event_id: str, source_id: str,
                  source_revision: int, occurred_at: float, payload: Mapping[str, Any],
                  urgency: Literal["normal", "important", "urgent"] = "normal",
                  locale: str = "en") -> NotificationReceipt:
    """Publish a deferred task result through its captured owned Core publisher.

    Requires the original admitted active component and a live current grant.
    Retired packages, wrong component, expired handle and revoked authority fail.
    Transport retry never calls this emitter or executes the originating task.
    """
    runtime = _runtime()
    binding = runtime.captured(publisher_ref)
    return runtime.service.emit(binding, category_id=category_id, event_id=event_id, source_id=source_id,
        source_revision=source_revision, occurred_at=occurred_at, payload=payload, urgency=urgency, locale=locale)


@_contract(effect="notification_withdrawal")
def withdraw(source_id: str) -> dict[str, int]:
    """Withdraw only this admitted Application/namespace's source notifications.

    Stops unsent disclosure; already sent provider content has separate cleanup
    capability and receipts. This never cancels or modifies the business task.
    """
    runtime = _runtime()
    return {"withdrawn": runtime.service.withdraw(runtime.producer(), source_id)}


@_contract(capability="notifications.manage", effect="read", bound=100)
def list_notifications(*, limit: int = 50, cursor: str | None = None,
                       view: Literal["all", "unread", "archived", "snoozed", "needs_action"] = "all") -> NotificationPage:
    """Read a bounded private inbox for the verified user; never shared Yjs."""
    runtime = _runtime()
    user, _ = runtime.management()
    return runtime.service.list(user, limit=limit, cursor=cursor, view=view)


@_contract(capability="notifications.manage", effect="read")
def get(notification_id: str) -> dict[str, Any] | None:
    """Read an exact owned notice with source, author and factual attention explanation."""
    runtime = _runtime()
    user, _ = runtime.management()
    record = runtime.service.get(user, notification_id)
    return runtime.service.public_record(record) if record else None


@_contract(capability="notifications.manage", effect="recipient_state")
def mark_read(notification_id: str, *, content_revision: int, expected_revision: int,
              operation_key: str) -> dict[str, Any]:
    """Mark only an existing shown content revision read; never approve its action."""
    runtime = _runtime()
    user, _ = runtime.management(write=True)
    return runtime.service.recipient_command(user, notification_id, action="read", value=content_revision,
        expected_revision=expected_revision, operation_key=operation_key)


@_contract(capability="notifications.manage", effect="recipient_state")
def archive(notification_id: str, *, archived: bool, expected_revision: int,
            operation_key: str) -> dict[str, Any]:
    """Archive/unarchive owned attention state without refusing/cancelling a task."""
    runtime = _runtime()
    user, _ = runtime.management(write=True)
    return runtime.service.recipient_command(user, notification_id, action="archive", value=archived,
        expected_revision=expected_revision, operation_key=operation_key)


@_contract(capability="notifications.manage", effect="recipient_state")
def snooze(notification_id: str, *, until: float | None, expected_revision: int,
           operation_key: str) -> dict[str, Any]:
    """Delay reminders until a UTC instant before source expiry; never renew a PA."""
    runtime = _runtime()
    user, _ = runtime.management(write=True)
    return runtime.service.recipient_command(user, notification_id, action="snooze", value=until,
        expected_revision=expected_revision, operation_key=operation_key)


@_contract(capability="notifications.manage", effect="read")
def get_preferences() -> dict[str, Any]:
    """Read effective category/object/destination rules and their CAS revision."""
    runtime = _runtime()
    user, subnet = runtime.management()
    return runtime.service.preferences.read(user, subnet)


@_contract(capability="notifications.manage", effect="attention_preferences")
def set_preferences(settings: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
    """Set the user's typed notification rules; stale revision fails, mute persists.

    This uses Identity preference storage, not application configuration. Endpoint
    discovery or an Application update cannot enable an external destination.
    """
    runtime = _runtime()
    user, subnet = runtime.management(write=True)
    if getattr(runtime, "test_story", False):
        runtime.validate_destinations(settings)
    return runtime.service.preferences.write(user, subnet, settings, expected_revision=expected_revision)


@_contract(capability="notifications.manage", effect="read", bound=100)
def endpoints(*, discover: bool = False) -> list[dict[str, Any]]:
    """List owned opaque delivery destinations; discovery never selects or sends."""
    from adaos.services.notifications.endpoints import EndpointService
    runtime = _runtime()
    user, subnet = runtime.management()
    service = EndpointService(runtime.ctx)
    return service.discover_messengers(user, subnet) if discover and not getattr(runtime, "test_story", False) else service.list(user, subnet)


@_contract(capability="notifications.manage", effect="endpoint_policy")
def set_endpoint_status(endpoint_id: str, *, status: Literal["active", "paused", "revoked"],
                        expected_revision: int) -> dict[str, Any]:
    """Pause/revoke/resume an owned destination; queued older sends stay suppressed."""
    from adaos.services.notifications.endpoints import EndpointService
    runtime = _runtime()
    user, _ = runtime.management(write=True)
    return EndpointService(runtime.ctx).set_status(user, endpoint_id, status=status, expected_revision=expected_revision)


# Explicit async wrappers preserve invocation ContextVars and keep local SQLite/
# package I/O out of the conversational/API event loop.
async def a_emit(**kwargs) -> NotificationReceipt:
    return await asyncio.to_thread(partial(emit, **kwargs))


async def a_emit_captured(publisher_ref: str, **kwargs) -> NotificationReceipt:
    return await asyncio.to_thread(partial(emit_captured, publisher_ref, **kwargs))


async def a_capture_publisher(*, lifetime_seconds: int = 86400) -> dict[str, Any]:
    return await asyncio.to_thread(capture_publisher, lifetime_seconds=lifetime_seconds)


async def a_list_notifications(**kwargs) -> NotificationPage:
    return await asyncio.to_thread(partial(list_notifications, **kwargs))


async def a_get_preferences() -> dict[str, Any]:
    return await asyncio.to_thread(get_preferences)


async def a_set_preferences(settings: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
    return await asyncio.to_thread(set_preferences, settings, expected_revision=expected_revision)

async def a_get(*args, **kwargs):
    return await asyncio.to_thread(partial(get, *args, **kwargs))


async def a_mark_read(*args, **kwargs):
    return await asyncio.to_thread(partial(mark_read, *args, **kwargs))


async def a_archive(*args, **kwargs):
    return await asyncio.to_thread(partial(archive, *args, **kwargs))


async def a_snooze(*args, **kwargs):
    return await asyncio.to_thread(partial(snooze, *args, **kwargs))


async def a_endpoints(**kwargs):
    return await asyncio.to_thread(partial(endpoints, **kwargs))


async def a_set_endpoint_status(*args, **kwargs):
    return await asyncio.to_thread(partial(set_endpoint_status, *args, **kwargs))


async def a_withdraw(source_id):
    return await asyncio.to_thread(withdraw, source_id)


for _function, _schema_name in ((emit, "receipt"), (emit_captured, "receipt"),
                              (mark_read, "record"), (archive, "record"), (snooze, "record"),
                              (get_preferences, "preferences"), (set_preferences, "preferences"),
                              (set_endpoint_status, "endpoint")):
    _function.__adaos_public_contract__["output_schema"] = contract(_schema_name)
get.__adaos_public_contract__["output_schema"] = {"anyOf": [contract("record"), {"type": "null"}]}
endpoints.__adaos_public_contract__["output_schema"] = {"type": "array", "maxItems": 100, "items": contract("endpoint")}
list_notifications.__adaos_public_contract__["output_schema"] = {
    "type": "object", "additionalProperties": False, "required": ["items", "next_cursor"],
    "properties": {"items": {"type": "array", "maxItems": 100, "items": contract("record")},
                   "next_cursor": {"type": ["string", "null"], "maxLength": 1024}}}
set_preferences.__adaos_public_contract__["input_schema"] = {
    "type": "object", "additionalProperties": False, "required": ["settings", "expected_revision"],
    "properties": {"settings": contract("preferences"), "expected_revision": {"type": "integer", "minimum": 0}}}


for _sync, _async in ((emit, a_emit), (emit_captured, a_emit_captured),
                     (capture_publisher, a_capture_publisher), (list_notifications, a_list_notifications),
                     (get_preferences, a_get_preferences), (set_preferences, a_set_preferences),
                     (get, a_get), (mark_read, a_mark_read), (archive, a_archive), (snooze, a_snooze),
                     (endpoints, a_endpoints), (set_endpoint_status, a_set_endpoint_status), (withdraw, a_withdraw)):
    _async.__signature__ = inspect.signature(_sync)
    _async.__annotations__ = dict(_sync.__annotations__)
    _async.__adaos_public_contract__ = copy.deepcopy(_sync.__adaos_public_contract__)
    _async.__doc__ = "Async version; preserves verified invocation and offloads local I/O. " + str(_sync.__doc__)


__all__ = ["contract", "validate_declaration", "emit", "emit_captured", "capture_publisher", "withdraw",
           "list_notifications", "get", "mark_read", "archive", "snooze", "get_preferences",
           "set_preferences", "endpoints", "set_endpoint_status", "a_emit", "a_emit_captured",
           "a_capture_publisher", "a_list_notifications", "a_get_preferences", "a_set_preferences"]
__all__ += ["a_get", "a_mark_read", "a_archive", "a_snooze", "a_endpoints", "a_set_endpoint_status", "a_withdraw"]
