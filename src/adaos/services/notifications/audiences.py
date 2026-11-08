"""Source-owned finite recipient resolution; payload IDs never grant membership.

The v1 owner adapter is the canonical Interaction. New source kinds need their
own authority adapter, not a caller-authored list or a scan of tenant users.
"""
from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime
import json

from adaos.domain.notifications import NotificationConflict, NotificationError, NotificationUnavailable, digest, dump, render, token, validate
from adaos.services.conversation_interactions import principal_can_read_interaction


def _source(con, binding, source_ref):
    token(source_ref, "canonical source reference")
    row = con.execute("SELECT payload_json FROM conversation_interactions WHERE interaction_id=?", (source_ref,)).fetchone()
    source = json.loads(row[0]) if row else None
    if (not source or source["owner"] not in {binding.author_ref, "application:" + binding.application_id}
        or source.get("content_redaction", {}).get("state") == "redacted"):
        raise PermissionError("notification source is unavailable or belongs to another owner")
    return source


def _recipients(source, policy):
    if policy == "authorized_assignees":
        scopes = [scope for action in source.get("actions", []) for scope in action.get("principal_scope", [])]
    elif policy == "shared_audience":
        scopes = source.get("metadata", {}).get("participants", [])
    else:
        raise NotificationError("this category does not declare a source-owned audience")
    if not isinstance(scopes, list) or len(scopes) > 100:
        raise NotificationError("source audience exceeds the v1 bound of 100 explicit principals")
    values = sorted({ref for ref in scopes if isinstance(ref, str) and ref.startswith("user:") and ref != "user:*"})
    for ref in values:
        token(ref, "canonical recipient")
    if not values and scopes:
        raise NotificationUnavailable("source has no finite explicit user audience; wildcard scope cannot resolve recipients")
    return values


def source_access(con, record, *, for_delivery=False, now=None):
    binding = record.get("producer_binding", {})
    descriptor = binding.get("authority", {}).get("recipient_source")
    if not descriptor:
        return True
    from adaos.domain.notifications import ProducerBinding
    try:
        owned = ProducerBinding(**{**binding, "admitted_recipients": tuple(binding.get("admitted_recipients", []))})
        source = _source(con, owned, descriptor["source_ref"])
        snapshot = con.execute("SELECT * FROM notification_audience_snapshots WHERE snapshot_id=?", (descriptor["snapshot_id"],)).fetchone()
        if (not snapshot or snapshot["status"] != "active" or snapshot["principal_ref"] != owned.principal_ref
            or record["principal_ref"] not in json.loads(snapshot["recipients_json"])):
            return False
        if for_delivery and (snapshot["expires_at"] <= now or source["status"] == "expired"
            or source.get("expires_at") and datetime.fromisoformat(source["expires_at"].replace("Z", "+00:00")).timestamp() <= now):
            return False
        return (source["generation"] == descriptor["generation"]
            and source["status"] not in {"cancelled", "superseded"}
            and record["principal_ref"] in _recipients(source, descriptor["policy"])
            and principal_can_read_interaction(source, {"kind": "user", "id": record["principal_ref"].removeprefix("user:")}))
    except (KeyError, ValueError, PermissionError):
        return False


def emit_to_source(service, binding, *, limit=25, cursor=None, **request):
    """One durable page; retry exact source outbox input after a lost response."""
    if type(limit) is not int or not 1 <= limit <= 25:
        raise NotificationError("source fanout page must contain 1..25 recipients")
    if binding.test_only:
        raise NotificationUnavailable("source-audience emission requires an admitted canonical source runtime; Preview cannot target real participants")
    validate("intent", request)
    for name in ("category_id", "event_id", "source_id"):
        token(request.get(name), name)
    occurrence = request.get("occurred_at")
    if type(occurrence) not in (int, float) or occurrence > service.clock() + 300:
        raise NotificationError("source fanout requires a stable source occurrence time")
    snapshot_id = digest([binding.instance_id, binding.namespace, binding.release_digest, binding.principal_ref,
                          request["category_id"], request["event_id"]])
    fingerprint = digest([request, binding.author_ref])
    after = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 240:
                raise ValueError()
            scope, after = json.loads(base64.urlsafe_b64decode(cursor))
            if scope != snapshot_id or type(after) is not int or not 0 <= after <= 100:
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise NotificationError("source fanout cursor does not match this exact request") from exc
    with service.store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        _, _, category = service._category(con, binding, request["category_id"])
        render(category, request.get("payload", {}), request.get("locale", "en"))
        descriptor = category.get("source_binding")
        if not descriptor or category["recipient_policy"] == "current_user":
            raise NotificationError("category requires current-user emission, not source fanout")
        source_ref = request["payload"][descriptor["payload_field"]]
        source = _source(con, binding, source_ref)
        recipients = _recipients(source, category["recipient_policy"])
        if source["status"] in {"cancelled", "superseded", "expired"}:
            raise PermissionError("source is no longer eligible for audience notification")
        if not principal_can_read_interaction(source, {"kind": "user", "id": binding.principal_ref.removeprefix("user:")}):
            raise PermissionError("initiating subject cannot read this canonical source")
        snapshot = con.execute("SELECT * FROM notification_audience_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if snapshot:
            if snapshot["request_digest"] != fingerprint:
                raise NotificationConflict("source audience event idempotency conflict")
            if snapshot["status"] != "active" or snapshot["expires_at"] <= service.clock():
                return {"receipts": [], "next_cursor": None, "skipped_count": 0, "status": "expired_or_erased"}
            if snapshot["source_ref"] != source_ref:
                raise NotificationConflict("source audience event idempotency conflict")
            if snapshot["source_generation"] != source["generation"]:
                raise PermissionError("source generation changed; historical fanout cannot acquire new authority")
            frozen = json.loads(snapshot["recipients_json"])
        else:
            if after:
                raise NotificationError("source audience cursor has no admitted snapshot")
            expiry = occurrence + category["lifetime_seconds"]
            if source.get("expires_at"):
                expiry = min(expiry, datetime.fromisoformat(source["expires_at"].replace("Z", "+00:00")).timestamp())
            frozen = recipients
            con.execute("INSERT INTO notification_audience_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,'active')",
                (snapshot_id, binding.principal_ref, binding.instance_id, binding.namespace, request["source_id"], source_ref,
                 source["generation"], fingerprint, dump(frozen), service.clock(), expiry))
        receipts, skipped = [], 0
        for recipient in frozen[after:after + limit]:
            if recipient not in recipients:
                skipped += 1
                continue
            authority = {**binding.authority, "recipient_source": {"source_ref": source_ref,
                "generation": source["generation"], "policy": category["recipient_policy"], "snapshot_id": snapshot_id}}
            owned = replace(binding, admitted_recipients=(recipient,), authority=authority)
            receipts.append(service.emit(owned, recipient_ref=recipient, connection=con, **request))
        position = after + limit
        next_cursor = base64.urlsafe_b64encode(dump([snapshot_id, position]).encode()).decode() if position < len(frozen) else None
        con.commit()
    return {"receipts": receipts, "next_cursor": next_cursor, "skipped_count": skipped, "status": "committed"}
