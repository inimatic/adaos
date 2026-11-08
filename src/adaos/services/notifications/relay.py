"""Exact, fresh Core authorization at secondary queue and Root/provider boundaries.

The opaque check token permits only this check, exposes no notification content,
and grants no send by itself. Root must obtain fresh proof for every provider
operation. An offline Hub cannot authorize queued private disclosure.
"""
from __future__ import annotations

import hmac
import json
import time
from contextlib import nullcontext

from adaos.domain.notifications import digest
from adaos.services.notifications.runtime import NotificationService
from adaos.services.notifications.policy import evaluate


def authorize(intent_id: str, token: str, *, now=None, connection=None) -> dict:
    from adaos.services.notifications.delivery import _row_record, live_application_authority
    current = time.time() if now is None else now
    service = NotificationService()
    with (nullcontext(connection) if connection is not None else service.store.connect()) as con:
        link = con.execute("SELECT * FROM notification_outputs WHERE intent_id=?", (intent_id,)).fetchone()
        if (not link or not isinstance(token, str) or len(token) != 48
            or not hmac.compare_digest(link["relay_grant_digest"] or "", digest(token))):
            raise PermissionError("invalid notification relay check")
        item = con.execute("SELECT * FROM conversation_output_intents WHERE intent_id=?", (intent_id,)).fetchone()
        endpoint = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=?", (link["endpoint_id"],)).fetchone()
        record = _row_record(con, link["notification_id"])
        reason, due = None, None
        if not item or item["status"] in {"suppressed", "dead_letter", "needs_review", "delivered"}:
            reason = "output_no_longer_dispatchable"
        elif not record or not endpoint:
            reason = "source_or_endpoint_missing"
        elif record["content_revision"] != link["content_revision"]:
            reason = "notification_revision_superseded"
        elif endpoint["revision"] != link["endpoint_revision"]:
            reason = "endpoint_revision_changed"
        elif not live_application_authority(record, service.ctx):
            reason = "permission_denied"
        if reason is None:
            raw = con.execute("SELECT payload_json FROM conversation_response_outbox WHERE envelope_id=?", (item["envelope_id"],)).fetchone()
            envelope = json.loads(raw[0]) if raw else None
            route_raw = con.execute("SELECT payload_json FROM conversation_reply_routes WHERE route_id=?", (endpoint["route_id"],)).fetchone()
            route = json.loads(route_raw[0]) if route_raw else None
            if (not envelope or envelope["status"] in {"cancelled", "superseded"} or not route or route["status"] != "active"
                or endpoint["binding_digest"] != envelope["payload"]["data"]["route_digest"]):
                reason = "binding_or_envelope_changed"
            else:
                from adaos.services import durable_delivery
                from datetime import datetime, timezone
                if durable_delivery._expired(route.get("expires_at"), now=datetime.fromtimestamp(current, timezone.utc).isoformat()):
                    reason = "route_expired"
                sources = envelope["payload"]["data"].get("members") or [
                    {"notification_id": record["notification_id"], "content_revision": record["content_revision"]}]
                caps = json.loads(endpoint["capabilities_json"])
                if caps.get("source_audience") != "personal":
                    reason = "shared_destination_not_admitted"
                for source in sources if reason is None else []:
                    notice = _row_record(con, source["notification_id"])
                    if (not notice or notice["content_revision"] != source["content_revision"]
                        or notice["principal_ref"] != record["principal_ref"] or notice["subnet_ref"] != record["subnet_ref"]
                        or not live_application_authority(notice, service.ctx)):
                        reason = "source_changed"
                        break
                    prefs = service.preferences.read(notice["principal_ref"], notice["subnet_ref"], connection=con)
                    plan = evaluate(notice, prefs, [dict(endpoint)], now=current)
                    if not plan["alert"] or endpoint["endpoint_id"] not in plan["endpoint_ids"]:
                        reason, due = plan["reason"], plan["next_eligible_at"]
                        break
                    levels = ("public", "private", "sensitive")
                    if levels.index(notice["sensitivity"]) > levels.index(caps.get("maximum_sensitivity", "public")):
                        reason = "endpoint_sensitivity_denied"
                        break
                if route["transport"] == "web" and caps.get("permission") != "granted":
                    reason = "device_permission_not_granted"
    return {"schema": "adaos.notifications.relay_authorization.v1", "intent_id": intent_id,
        "allowed": reason is None, "reason": reason or "authorized", "next_eligible_at": due,
        "valid_until": min(current + 1, record["expires_at"]) if reason is None else current}


def authorize_attempt(durable_attempt_id: str) -> dict | None:
    """Local MAX secondary queue uses the exact same final disclosure check."""
    service = NotificationService()
    with service.store.connect() as con:
        row = con.execute("""SELECT e.payload_json FROM conversation_output_intents i
            JOIN notification_outputs o ON o.intent_id=i.intent_id
            JOIN conversation_response_outbox e ON e.envelope_id=i.envelope_id WHERE i.attempt_id=?""",
            (durable_attempt_id,)).fetchone()
    if not row:
        return None
    guard = json.loads(row[0])["payload"]["data"]["relay_guard"]
    return authorize(guard["intent_id"], guard["token"])


def require_root_guard(client):
    """Versioned route is mandatory: an old Root must never ignore the guard."""
    from adaos.domain.notifications import NotificationUnavailable
    response = client.request("POST", "/v1/hub/communication/notifications/capabilities", json={}, timeout=5)
    if (response.get("schema") != "adaos.notifications.relay_capabilities.v1"
        or response.get("guard_version") != 1 or response.get("online_source_check") is not True
        or response.get("personal_destinations_only") is not True):
        raise NotificationUnavailable("Root notification disclosure guard v1 is unavailable")
