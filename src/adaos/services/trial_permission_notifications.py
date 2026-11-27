"""Deliver a Trial decision through selected personal notification endpoints."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any

from adaos.domain.notifications import ProducerBinding, digest
from adaos.services.notifications.runtime import NotificationService
from adaos.services.personalization_runtime import current_user_profile_service
from adaos.services.trial_permission_actions import active_request, decision_copy, owner_ref


APPLICATION = "adaos.pending-actions"
CATEGORY = "trial.permissions"
ORIGIN = "core.trial_permission_notice.v1"
CALLBACK_PREFIX = "pa:"
_CALLBACK = re.compile(r"pa:[A-Za-z0-9_-]{60}")
_CHOICES = {0: "approve", 1: "refuse"}
_MANIFEST = {
    "schema": "adaos.notifications.manifest.v1",
    "categories": [{
        "id": CATEGORY,
        "title_key": "pending_actions.trial.title",
        "purpose_key": "pending_actions.trial.purpose",
        "source_event": ORIGIN,
        "payload_schema": {
            "type": "object", "required": ["message"],
            "properties": {"message": {"type": "string", "maxLength": 3000}},
            "additionalProperties": False,
        },
        "recipient_policy": "current_user",
        "default_mode": "center_only",
        "maximum_urgency": "normal",
        "sensitivity": "private",
        "lifetime_seconds": 7 * 86400,
        "template": {"en": "{message}", "ru": "{message}"},
    }],
}


def notice_authority(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    binding = record.get("producer_binding") or {}
    authority = binding.get("authority") or {}
    if (
        record.get("application_id") == APPLICATION
        and record.get("category_id") == CATEGORY
        and record.get("author_ref") == "core:pending-actions"
        and binding.get("principal_ref") == record.get("principal_ref")
        and authority.get("origin") == ORIGIN
    ):
        return authority
    return None


def _current_notice_request(record: Mapping[str, Any], ctx: Any) -> dict[str, Any] | None:
    from adaos.services.development_tickets import DevelopmentTicketService

    authority = notice_authority(record)
    if not authority or record.get("principal_ref") != owner_ref(ctx):
        return None
    ticket = DevelopmentTicketService().get_ticket(authority["ticket_id"])
    request = active_request(ticket) if ticket else None
    if request and all(request[key] == authority.get(key) for key in ("action_id", "task_id", "package_digest")):
        return request
    return None


def notice_is_current(record: Mapping[str, Any], ctx: Any) -> bool:
    return _current_notice_request(record, ctx) is not None


def publish_notice(ctx: Any, ticket: Mapping[str, Any], action: Mapping[str, Any], *, locale: str | None = None) -> dict | None:
    from adaos.services.trial_permission_actions import matches_action

    request = active_request(ticket)
    if not request or not matches_action(request, action) or action.get("status") not in {"pending", "postponed"}:
        return None
    hub_id = str(ctx.config.subnet_id)
    if not hub_id:
        return None
    if locale is None:
        profile = current_user_profile_service(ctx).get_profile()
        locale = profile.locale or profile.language or "en"
    locale = "ru" if locale.lower().startswith("ru") else "en"
    copy = decision_copy(ticket, request, locale=locale)
    message = f"{copy['title']}\n\n{copy['request_text']}"
    if len(message.encode("utf-16-le")) // 2 > 2900:
        return None  # The complete question remains available in the owner's desktop.
    authority = {"origin": ORIGIN, "labels": copy["labels"], **{
        key: request[key] for key in ("action_id", "ticket_id", "task_id", "package_digest")
    }}
    binding = ProducerBinding(APPLICATION, f"core-pending-actions:{hub_id}", "pending-actions",
                              ORIGIN, owner_ref(ctx), f"subnet:{hub_id}", "core:pending-actions", "Builder",
                              authority=authority)
    service = NotificationService(ctx)
    service.admit_catalog(binding, _MANIFEST)
    return service.emit(binding, category_id=CATEGORY, event_id=request["action_id"], source_id=request["action_id"],
                        source_revision=1, occurred_at=action["created_at"], payload={"message": message}, locale=locale)


def _compact(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _callback(intent_id: str, choice: int, guard: Mapping[str, Any]) -> str:
    # Telegram permits 64 bytes. The hub fingerprint only routes the click.
    # Core authenticates the full outbox identity, choice and personal binding.
    hub = hashlib.sha256(guard["hub_id"].encode("utf-8")).digest()[:4]
    message = bytes.fromhex(intent_id.removeprefix("notification-output:")) + hub + bytes([choice])
    tag = hmac.new(bytes.fromhex(guard["token"]), message, hashlib.sha256).digest()[:8]
    return CALLBACK_PREFIX + _compact(message + tag)


def messenger_buttons(envelope: Mapping[str, Any]) -> list[dict[str, str]]:
    data = envelope["payload"]["data"]
    labels = data.get("trial_permission_buttons")
    if not labels:
        return []
    return [{"label": labels[choice], "token": _callback(envelope["envelope_id"], code, data["relay_guard"])}
            for code, choice in _CHOICES.items()]


def trusted_callback(transport: str, source: str) -> bool:
    return (transport, source) in {("telegram", "io.nats"), ("max", "max.root.mailbox")}


def _authorized_button(ctx: Any, token: str, metadata: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    from adaos.services import durable_delivery
    from adaos.services.notifications.delivery import _row_record
    from adaos.services.notifications.policy import effective_choice

    if not _CALLBACK.fullmatch(token):
        raise PermissionError("invalid Trial decision button")
    pointer = base64.urlsafe_b64decode(token.removeprefix(CALLBACK_PREFIX))
    code = pointer[36]
    if code not in _CHOICES:
        raise PermissionError("invalid Trial decision choice")
    intent_id = "notification-output:" + pointer[:32].hex()
    service = NotificationService(ctx)
    now = service.clock()
    with service.store.connect(read_only=True) as con:
        link = con.execute("SELECT * FROM notification_outputs WHERE intent_id=?", (intent_id,)).fetchone()
        if not link or link["digest_window_id"]:
            raise PermissionError("Trial decision button is unavailable")
        record = _row_record(con, link["notification_id"])
        endpoint = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=?", (link["endpoint_id"],)).fetchone()
        item = con.execute("SELECT * FROM conversation_output_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if (
            not record or not endpoint or not item or not item["transport_attempt_id"]
            or item["status"] in {"suppressed", "dead_letter"}
            or record["content_revision"] != link["content_revision"]
            or record["expires_at"] <= now or record["source_state"] != "active"
            or endpoint["status"] != "active" or endpoint["revision"] != link["endpoint_revision"]
            or endpoint["principal_ref"] != record["principal_ref"] or endpoint["subnet_ref"] != record["subnet_ref"]
            or endpoint["expires_at"] is not None and endpoint["expires_at"] <= now
            or json.loads(endpoint["capabilities_json"]).get("source_audience") != "personal"
        ):
            raise PermissionError("Trial decision destination is no longer active")
        preferences = service.preferences.read(record["principal_ref"], record["subnet_ref"], connection=con)
        choice = effective_choice(record, preferences)
        if choice["mode"] not in {"immediate", "digest"} or endpoint["endpoint_id"] not in choice["endpoint_ids"]:
            raise PermissionError("Trial decision destination was deselected")

    envelope = durable_delivery.get_envelope(intent_id)
    route = durable_delivery.get_reply_route(endpoint["route_id"])
    request = _current_notice_request(record, ctx)
    transport = metadata.get("io_type")
    if (
        not request or not envelope or not route or route["status"] != "active"
        or route["transport"] != transport or route["principal_scope"] != [record["principal_ref"]]
        or durable_delivery._expired(route.get("expires_at"), now=durable_delivery._now())
    ):
        raise PermissionError("Trial decision or its verified route is no longer current")
    destination = route["destination_ref"]
    identity = ("hub_id", "bot_id", "chat_id", "user_id", "binding_id") if transport == "max" else ("hub_id", "bot_id", "chat_id")
    if (any(not destination.get(key) or str(destination[key]) != str(metadata.get(key)) for key in identity)
        or (transport == "telegram" and str(metadata.get("user_id")) != str(destination["chat_id"]))):
        raise PermissionError("Trial decision belongs to another messenger binding")
    route_digest = digest({key: route[key] for key in (
        "route_id", "conversation_id", "transport", "destination_ref", "principal_scope",
    )})
    data = envelope["payload"]["data"]
    if (
        route_digest != endpoint["binding_digest"] or data.get("route_digest") != route_digest
        or not data.get("trial_permission_buttons")
        or not hmac.compare_digest(token, _callback(intent_id, code, data["relay_guard"]))
    ):
        raise PermissionError("Trial decision button does not match this delivery")
    return request, _CHOICES[code], data["trial_permission_buttons"][_CHOICES[code]]


async def respond_from_messenger(ctx: Any, token: str, metadata: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    from adaos.services.pending_actions import respond_pending_action_async
    from adaos.services.runtime_executor import run_runtime_interactive

    transport = str(metadata.get("io_type") or "")
    if not trusted_callback(transport, source):
        raise PermissionError("Trial decision needs verified messenger ingress")
    request, choice, label = await run_runtime_interactive(_authorized_button, ctx, token, metadata)
    result = await respond_pending_action_async(
        request["action_id"], choice, ctx=ctx, webspace_id=request["webspace_id"],
        idempotency_key=str(metadata["idempotency_key"]),
        responder={"type": "transport", "transport": transport, "actor": owner_ref(ctx), "verified": True},
        response_payload={"channel": transport, "surface": "pending_actions"},
    )
    return {"action_id": request["action_id"], "label": label, **result}
