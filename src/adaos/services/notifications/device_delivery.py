"""Private device handoff over the shared output intents; client proof is explicit."""
from __future__ import annotations

import json
import time
import uuid

from adaos.domain.notifications import NotificationConflict, NotificationError
from adaos.services import durable_delivery
from adaos.services.agent_context import get_ctx
from adaos.services.notifications.store import NotificationStore


class DeviceAdapter:
    def send(self, envelope, route, attempt):
        if route["transport"] != "web" or not route["channel_context"].get("notification_device"):
            raise PermissionError("notification device endpoint is not admitted")
        return {"accepted": True, "accepted_state": "awaiting_endpoint"}


def pull(principal_ref: str, endpoint_id: str, session_ref: str, *, limit: int = 10) -> dict:
    if type(limit) is not int or not 1 <= limit <= 10:
        raise NotificationError("device pull limit must be 1..10")
    store = NotificationStore()
    from adaos.services.notifications.runtime import NotificationService
    from adaos.services.notifications.policy import evaluate
    from adaos.services.notifications.delivery import live_application_authority
    service = NotificationService()
    now = time.time()
    with store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        endpoint = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=? AND principal_ref=? AND status='active'",
                               (endpoint_id, principal_ref)).fetchone()
        if not endpoint:
            raise PermissionError("device endpoint is not owned by this user")
        route = json.loads(con.execute("SELECT payload_json FROM conversation_reply_routes WHERE route_id=?",
                                      (endpoint["route_id"],)).fetchone()[0])
        if route["destination_ref"].get("session_ref") != session_ref:
            raise PermissionError("device session does not match the admitted endpoint")
        con.execute("""UPDATE conversation_output_intents SET status='needs_review',last_error='device_ack_unknown'
            WHERE status='endpoint_leased' AND lease_until<? AND intent_id IN
            (SELECT intent_id FROM notification_outputs WHERE endpoint_id=?)""", (now, endpoint_id))
        rows = con.execute("""SELECT i.*,o.content_revision FROM conversation_output_intents i
            JOIN notification_outputs o ON o.intent_id=i.intent_id
            WHERE o.endpoint_id=? AND i.status='awaiting_endpoint' ORDER BY i.updated_at LIMIT ?""",
            (endpoint_id, limit)).fetchall()
        result = []
        for item in rows:
            envelope = json.loads(con.execute("SELECT payload_json FROM conversation_response_outbox WHERE envelope_id=?",
                                             (item["envelope_id"],)).fetchone()[0])
            record = service.get(principal_ref, envelope["payload"]["data"]["notification_ref"], connection=con)
            prefs = service.preferences.read(principal_ref, endpoint["subnet_ref"], connection=con)
            decision = evaluate(record, prefs, [dict(endpoint)], now=now) if record else None
            if (not record or not live_application_authority(record, service.ctx)
                or not decision["alert"] or endpoint_id not in decision["endpoint_ids"]
                or envelope["payload"]["data"]["expires_at"] <= now):
                con.execute("UPDATE conversation_output_intents SET status='suppressed',last_error='expired' WHERE intent_id=?",
                            (item["intent_id"],))
                continue
            lease = uuid.uuid4().hex
            con.execute("UPDATE conversation_output_intents SET status='endpoint_leased',lease_token=?,lease_until=? WHERE intent_id=?",
                        (lease, now + 60, item["intent_id"]))
            result.append({"intent_id": item["intent_id"], "presentation_token": lease,
                           "text": envelope["payload"]["text"], "content_revision": item["content_revision"],
                           "expires_at": envelope["payload"]["data"]["expires_at"]})
        con.commit()
    return {"items": result}


def acknowledge(principal_ref, endpoint_id, session_ref, *, intent_id, presentation_token, evidence):
    if evidence not in {"presented", "permission_denied", "failed"}:
        raise NotificationError("unsupported device evidence")
    store = NotificationStore()
    with store.connect() as con:
        item = con.execute("""SELECT i.*,e.principal_ref,r.payload_json AS route_json FROM conversation_output_intents i
            JOIN notification_outputs o ON o.intent_id=i.intent_id
            JOIN notification_endpoints e ON e.endpoint_id=o.endpoint_id
            JOIN conversation_reply_routes r ON r.route_id=e.route_id
            WHERE i.intent_id=? AND e.endpoint_id=? AND e.principal_ref=?""",
            (intent_id, endpoint_id, principal_ref)).fetchone()
        if not item or json.loads(item["route_json"])["destination_ref"].get("session_ref") != session_ref:
            raise PermissionError("device presentation is not owned by this user/session")
        if item["lease_token"] != presentation_token or item["status"] not in {"endpoint_leased", "delivered"}:
            raise NotificationConflict("device presentation token is stale")
        if item["status"] == "delivered":
            return {"status": "presented", "read": False}
    durable_delivery.complete_delivery(item["attempt_id"], delivered=evidence == "presented",
        receipt={"evidence": evidence, "endpoint_id": endpoint_id, "read": False})
    with store.connect() as con:
        con.execute("UPDATE conversation_output_intents SET status=?,last_error=? WHERE intent_id=? AND lease_token=?",
            ("delivered" if evidence == "presented" else "dead_letter",
             None if evidence == "presented" else evidence, intent_id, presentation_token))
        con.commit()
    return {"status": evidence, "read": False}
