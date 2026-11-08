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
    from adaos.services import telegram_delivery, max_delivery

    conversation_store.ensure_schema()
    durable_delivery.ensure_schema()
    telegram_delivery.ensure_schema()
    max_delivery.ensure_schema()
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
            CREATE INDEX IF NOT EXISTS idx_conversation_output_route_due
                ON conversation_output_intents(route_id,status,next_attempt_at);
            CREATE INDEX IF NOT EXISTS idx_conversation_route_transport
                ON conversation_reply_routes(transport,route_id);
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
    existing_message_id: str | None = None,
    message_payload: Mapping[str, Any] | None = None,
    projection_webspace_id: str | None = None,
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
    request_input = dict(owner=owner, webspace_id=webspace_id, text=text,
        category=category, role=role, channel_id=channel_id, thread_id=thread_id,
        meta=dict(meta or {}), task_ref=task_ref)
    if projection_webspace_id is not None:
        if webspace_id != "global" or not projection_webspace_id or projection_webspace_id == "global":
            raise ValueError("local publication projection requires shared canonical history")
        pointer = conversation_store.get_dialog_channel(projection_webspace_id, channel_id)
        if (not pointer or pointer.get("owner") != owner
                or pointer.get("conversation_id") != conversation_id or pointer.get("status") != "active"):
            raise ValueError("publication projection outside the owned active channel")
        request_input["projection_webspace_id"] = projection_webspace_id
    presentation = dict(message_payload or {})
    allowed_presentation = {"actions", "interaction", "active_agent_id", "active_agent_label",
                            "active_agent_icon", "active_agent_avatar_ref"}
    if set(presentation) - allowed_presentation:
        raise ValueError("unsupported publication presentation fields")
    if presentation:
        request_input["presentation"] = presentation
    if existing_message_id is not None:
        if not isinstance(existing_message_id, str) or not existing_message_id.strip():
            raise ValueError("existing message identity is required")
        request_input["existing_message_id"] = existing_message_id
    request_digest = _digest(request_input)
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
            if existing_message_id is not None:
                # Attach delivery to an already committed turn without creating
                # a second transcript row or altering its author/actions.
                message = con.execute("""SELECT * FROM conversation_messages
                    WHERE message_id=? AND conversation_id=? AND owner=?
                        AND webspace_id=? AND channel_id=? AND COALESCE(thread_id,'')=?
                        AND text=? AND redaction_state='active'""",
                    (existing_message_id, conversation_id, owner, webspace_id,
                     channel_id, thread_id or '', text)).fetchone()
                if message is None:
                    raise ValueError("existing message ownership, content or redaction mismatch")
                payload = conversation_store._row_to_message(message)
            else:
                payload = conversation_store.append_message(conversation_id=conversation_id,
                    owner=owner, webspace_id=webspace_id, channel_id=channel_id,
                    thread_id=thread_id, text=text, role=role, meta=metadata,
                    payload={**presentation, "id": "message:" + _digest(publication_id)},
                    idempotency_key=publication_id, connection=con)
            intents = []
            # A MAX-origin request admits its reply to the same frozen binding.
            # Commit that reply with the transcript, including buttons. The
            # subsequent publication projection cannot become a second sender.
            if (metadata.get('io_type') == 'max' and role not in {'user', 'human'}
                    and all(metadata.get(k) for k in ('hub_id', 'bot_id', 'chat_id', 'user_id', 'binding_id'))
                    and not any(r['transport'] == 'max' and r['destination_ref'].get('binding_id') == metadata['binding_id'] for r in admitted)):
                from adaos.services.router.max_projection import project
                origin = {k: v for k, v in metadata.items() if k != 'output_delivery_owned'}
                project({'id': payload['id'], 'text': text, 'actions': presentation.get('actions', [])}, origin,
                        connection=con, operation_key='max-publication:' + publication_id, source_message_id=payload['id'])
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
    # append_message's first-write return can be a compact payload, whereas a
    # replay is decoded from the ledger. Both events must carry the same trusted
    # routing scope. Otherwise Router infers its active General owner/default
    # workspace and can overwrite this conversation and its output policy.
    projection_scope = projection_webspace_id or webspace_id
    payload = {**payload, "conversation_id": conversation_id,
        "webspace_id": projection_scope, "dialog_channel_id": channel_id,
        **({"thread_id": thread_id} if thread_id else {}),
        "_meta": {**dict(payload.get("_meta") or {}),
            "conversation_id": conversation_id, "conversation_owner": owner,
            "webspace_id": projection_scope, "dialog_channel_id": channel_id,
            **({"thread_id": thread_id, "conversation_topic_id": thread_id} if thread_id else {}),
            "output_delivery_owned": True, "publication_id": publication_id}}
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


def is_committed_projection_event(event: Any) -> bool:
    """Recognize a Core publication without rematerializing or moving its chat.

    A payload flag is not authority. Check the Core source, exact receipt/message,
    owner, thread, text and (for a shared history) the existing local pointer.
    """
    if getattr(event, "source", None) != "conversation.publication":
        return False
    payload = event.payload or {}
    meta = payload.get("_meta") or {}
    with get_ctx().sql.connect() as con:
        con.row_factory = sqlite3.Row
        publication = con.execute("SELECT receipt_json FROM conversation_publications WHERE publication_id=?",
                                  (meta.get("publication_id"),)).fetchone()
        if not publication or json.loads(publication[0]).get("message_id") != payload.get("id"):
            return False
        message = con.execute("SELECT * FROM conversation_messages WHERE message_id=?", (payload.get("id"),)).fetchone()
    if (not message or message["redaction_state"] != "active"
            or message["conversation_id"] != payload.get("conversation_id")
            or message["owner"] != meta.get("conversation_owner")
            or (message["thread_id"] or "") != (payload.get("thread_id") or "")
            or message["channel_id"] != payload.get("dialog_channel_id")
            or str(message["text"]).strip() != str(payload.get("text") or "").strip()):
        return False
    target = payload.get("webspace_id")
    if meta.get("webspace_id") != target or payload.get("webspace_ids") or meta.get("webspace_ids"):
        return False
    if target == message["webspace_id"]:
        return True
    if message["webspace_id"] != "global":
        return False
    pointer = conversation_store.get_dialog_channel(target, message["channel_id"])
    return bool(pointer and pointer.get("conversation_id") == message["conversation_id"]
                and pointer.get("owner") == message["owner"] and pointer.get("status") == "active")
