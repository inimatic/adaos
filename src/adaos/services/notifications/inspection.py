"""Bounded private delivery facts, without message bodies or provider addresses."""
from __future__ import annotations

import json

from adaos.domain.notifications import NotificationError
from adaos.services.notifications.endpoints import EndpointService


def inspect_endpoint(ctx, principal_ref, subnet_ref, endpoint_id, *, limit=20, cursor=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise NotificationError("inspection limit must be 1..100")
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 240):
        raise NotificationError("invalid inspection cursor")
    service = EndpointService(ctx)
    with service.store.connect(read_only=True) as con:
        endpoint = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=? AND principal_ref=? AND subnet_ref=?",
                               (endpoint_id, principal_ref, subnet_ref)).fetchone()
        if not endpoint:
            raise PermissionError("endpoint is not owned by this user")
        # Stable opaque intent order permits indexed, bounded pagination even
        # while the worker changes delivery status. It is not a time ordering.
        rows = con.execute("""SELECT o.intent_id,o.notification_id,o.content_revision,i.status,i.last_error,i.updated_at,
            a.payload_json AS attempt_json,n.read_revision
            FROM notification_outputs o JOIN conversation_output_intents i ON i.intent_id=o.intent_id
            LEFT JOIN conversation_delivery_attempts a ON a.attempt_id=i.attempt_id
            LEFT JOIN notification_records n ON n.notification_id=o.notification_id AND n.principal_ref=?
            WHERE o.endpoint_id=? AND o.intent_id>? ORDER BY o.intent_id LIMIT ?""",
            (principal_ref, endpoint_id, cursor or "", limit + 1)).fetchall()
        items = []
        for row in rows[:limit]:
            receipt = (json.loads(row["attempt_json"] or "{}").get("receipt") or {})
            evidence = receipt.get("evidence")
            created = True if receipt.get("provider_message_id") or receipt.get("external_message_ids") else None
            items.append({"intent_id": row["intent_id"], "notification_id": row["notification_id"],
                "content_revision": row["content_revision"], "status": row["status"],
                "reason": (row["last_error"] or "")[:240] or None, "updated_at": row["updated_at"],
                "evidence": {"root_accepted": True if receipt.get("root_accepted") is True or row["status"] == "accepted_by_root" else None,
                    "provider_created": created, "device_presented": True if evidence == "presented" else None,
                    "system_accepted": True if evidence == "system_accepted" else None, "provider_read": None,
                    "recipient_read": row["read_revision"] is not None and row["read_revision"] >= row["content_revision"]},
                "guidance": _guidance(row["status"], row["last_error"], endpoint["status"])})
    return {"endpoint": service.public(endpoint), "items": items,
            "next_cursor": items[-1]["intent_id"] if len(rows) > limit else None}


def _guidance(status, reason, endpoint_status):
    if endpoint_status != "active":
        return "Explicitly reconnect or resume the destination, then send a new test. Old sends remain stopped."
    if status in {"needs_review", "reconciling"}:
        return "Delivery is unproven. Inspect the exact attempt; do not replay the business task."
    if status == "suppressed":
        return "Inspect the current policy and source reason. A failed test never enables fallback."
    if status == "dead_letter":
        return "Check destination permission and binding. A new one-time test needs explicit authorization."
    if status == "delivered":
        return "Presentation or provider creation does not prove reading or action completion."
    return "Waiting for this delivery owner; no recipient read or action outcome is inferred."
