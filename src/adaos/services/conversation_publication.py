"""Core publication boundary: one message, independently deliverable outputs.

The transcript is never a transport queue. Publication and its per-target
envelopes commit together; ephemeral bus events only accelerate projections.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from typing import Any, Mapping, Sequence

from adaos.services import conversation_store, durable_delivery
from adaos.services.agent_context import get_ctx


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def ensure_schema() -> None:
    from adaos.services import telegram_delivery

    conversation_store.ensure_schema()
    durable_delivery.ensure_schema()
    telegram_delivery.ensure_schema()
    with get_ctx().sql.connect() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS conversation_publications (
                publication_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                request_digest TEXT NOT NULL, receipt_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_output_selections (
                response_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                selection_digest TEXT NOT NULL, receipt_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_output_intents (
                intent_id TEXT PRIMARY KEY, publication_id TEXT NOT NULL,
                envelope_id TEXT NOT NULL UNIQUE, route_id TEXT NOT NULL,
                status TEXT NOT NULL, next_attempt_at REAL NOT NULL,
                lease_token TEXT, lease_until REAL, attempt_count INTEGER NOT NULL DEFAULT 0,
                attempt_id TEXT, transport_attempt_id TEXT, last_error TEXT, updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_outputs_due
                ON conversation_output_intents(status, next_attempt_at);
            CREATE INDEX IF NOT EXISTS idx_conversation_outputs_lease
                ON conversation_output_intents(status, lease_until);
        """)
        columns = {row[1] for row in con.execute("PRAGMA table_info(conversation_output_intents)")}
        if "transport_attempt_id" not in columns:
            con.execute("ALTER TABLE conversation_output_intents ADD COLUMN transport_attempt_id TEXT")
        con.commit()


def publish(
    *, publication_key: str, conversation_id: str, owner: str, webspace_id: str,
    text: str, category: str = "notification", role: str = "hub",
    channel_id: str = "general", thread_id: str | None = None,
    route_ids: Sequence[str] = (), meta: Mapping[str, Any] | None = None,
    task_ref: Mapping[str, Any] | None = None, bus: Any = None,
) -> dict[str, Any]:
    """Commit a message and one existing durable envelope per authorized target.

    Called by Core after ingress admission. Routes are Core-created ReplyRoute
    identities, never application-supplied transport addresses. Empty routes
    retains the canonical message without initiating external communication.
    Reusing a key with a changed request fails before any write or notification.
    """
    if not publication_key or len(publication_key) > 500:
        raise ValueError("publication_key must contain 1..500 characters")
    ensure_schema()
    publication_id = "publication:" + _digest([owner, conversation_id, publication_key])
    routes = list(dict.fromkeys(route_ids))
    if len(routes) > 20:
        raise ValueError("too many output targets")
    request_digest = _digest(dict(owner=owner, webspace_id=webspace_id, text=text,
        category=category, role=role, channel_id=channel_id, thread_id=thread_id,
        meta=dict(meta or {}), task_ref=task_ref))
    now = time.time()
    metadata = {**dict(meta or {}), "publication_id": publication_id,
                "response_idempotency_key": publication_id, "output_delivery_owned": True}
    with get_ctx().sql.connect() as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        previous = con.execute("SELECT * FROM conversation_publications WHERE publication_id=?",
                               (publication_id,)).fetchone()
        if previous:
            if previous["request_digest"] != request_digest:
                raise ValueError("publication idempotency conflict")
            receipt = json.loads(previous["receipt_json"])
            message = con.execute("SELECT * FROM conversation_messages WHERE message_id=?",
                                  (receipt["message_id"],)).fetchone()
            payload = conversation_store._row_to_message(message)
        else:
            conversation = con.execute(
                "SELECT owner,webspace_id,redaction_state FROM conversation_conversations WHERE conversation_id=?",
                (conversation_id,)).fetchone()
            if not conversation or conversation["owner"] != owner or conversation["webspace_id"] != webspace_id:
                raise ValueError("publication conversation ownership mismatch")
            if conversation["redaction_state"] != "active":
                raise ValueError("publication conversation is redacted")
            admitted = []
            for route_id in routes:
                row = con.execute("SELECT payload_json FROM conversation_reply_routes WHERE route_id=?",
                                  (route_id,)).fetchone()
                route = json.loads(row[0]) if row else None
                if not route or route["conversation_id"] != conversation_id:
                    raise ValueError("output route is unavailable or belongs to another conversation")
                # Withdrawal of external consent must not suppress the canonical
                # message in the owned chat. No work is queued for withdrawn targets.
                if (route["status"] != "active"
                        or durable_delivery._expired(route.get("expires_at"), now=durable_delivery._now())):
                    continue
                admitted.append(route)
            payload = conversation_store.append_message(conversation_id=conversation_id,
                owner=owner, webspace_id=webspace_id, channel_id=channel_id,
                thread_id=thread_id, text=text, role=role, meta=metadata,
                payload={"id": "message:" + _digest(publication_id)},
                idempotency_key=publication_id, connection=con)
            intents = []
            for route in admitted:
                intent_id = "output:" + _digest([publication_id, route["route_id"]])
                envelope = durable_delivery.enqueue_response(conversation_id, category,
                    text=text, data={"message_id": payload["id"], "publication_id": publication_id,
                        "route_digest": _digest({key: route.get(key) for key in
                            ("route_id", "conversation_id", "transport", "destination_ref", "principal_scope")})},
                    reply_route_ids=[route["route_id"]], task_ref=task_ref,
                    coalesce_key=("progress:" + _digest([task_ref, route["route_id"]])) if category == "progress" else None,
                    envelope_id=intent_id, terminal_key=intent_id if category == "terminal" else None,
                    connection=con)
                envelope.update(materialization_status="materialized", materialized_at=durable_delivery._now())
                durable_delivery._save_envelope(con, envelope)
                con.execute("""INSERT INTO conversation_output_intents
                    (intent_id,publication_id,envelope_id,route_id,status,next_attempt_at,updated_at)
                    VALUES(?,?,?,?,'pending',?,?)""",
                    (intent_id, publication_id, envelope["envelope_id"], route["route_id"], now, now))
                intents.append(intent_id)
            receipt = {"schema": "adaos.conversation.publication_receipt.v1",
                "publication_id": publication_id, "message_id": payload["id"],
                "intent_ids": intents, "status": "committed", "committed_at": now}
            receipt = durable_delivery._validate("adaos.conversation.publication_receipt.v1", receipt)
            con.execute("INSERT INTO conversation_publications VALUES(?,?,?,?)",
                        (publication_id, conversation_id, request_digest, json.dumps(receipt)))
        con.commit()
    # Re-emission repairs a lost projection event without appending a second message.
    if bus is not None and payload.get("redaction_state", "active") == "active":
        from adaos.domain import Event
        try:
            bus.publish(Event(type="io.out.chat.append", source="conversation.publication",
                              ts=now, payload=payload))
        except Exception:
            # Projection failure cannot undo a committed publication. Canonical
            # reads/reconnect and an idempotent replay can recover presentation.
            logging.getLogger(__name__).warning("publication committed; projection deferred publication=%s",
                                                publication_id, exc_info=True)
    return receipt


def output_status(publication_id: str) -> list[dict[str, Any]]:
    with get_ctx().sql.connect() as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute("""SELECT intent_id,status,attempt_count,last_error
            FROM conversation_output_intents WHERE publication_id=? ORDER BY intent_id""", (publication_id,))]
