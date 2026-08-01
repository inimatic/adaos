from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.services.agent_context import get_ctx
from adaos.services.conversation_attention import default_attention_policy, plan_attention


RESPONSE_ENVELOPE_SCHEMA = "adaos.conversation.response_envelope.v1"
REPLY_ROUTE_SCHEMA = "adaos.conversation.reply_route.v1"
DELIVERY_ATTEMPT_SCHEMA = "adaos.conversation.delivery_attempt.v1"

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS conversation_reply_routes (
        route_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        transport TEXT NOT NULL,
        status TEXT NOT NULL,
        priority INTEGER NOT NULL,
        expires_at TEXT,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_reply_routes_active
    ON conversation_reply_routes(conversation_id, status, priority, updated_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_response_outbox (
        envelope_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        category TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        coalesce_key TEXT,
        status TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(conversation_id, sequence)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_response_outbox_pending
    ON conversation_response_outbox(status, conversation_id, sequence)
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_delivery_attempts (
        attempt_id TEXT PRIMARY KEY,
        envelope_id TEXT NOT NULL,
        route_id TEXT NOT NULL,
        presentation_id TEXT,
        transport TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        attempt_number INTEGER NOT NULL,
        status TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        claimed_at TEXT NOT NULL,
        completed_at TEXT,
        UNIQUE(envelope_id, route_id, presentation_id, attempt_number)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_delivery_attempts_envelope
    ON conversation_delivery_attempts(envelope_id, route_id, attempt_number)
    """,
)


class DurableDeliveryError(ValueError):
    """Raised when response delivery state would become ambiguous or unsafe."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sql() -> Any:
    try:
        sql = get_ctx().sql
    except Exception as exc:  # pragma: no cover - runtime wiring failure
        raise DurableDeliveryError("durable AdaOS SQLite store is unavailable") from exc
    if not sql or not hasattr(sql, "connect"):
        raise DurableDeliveryError("durable AdaOS SQLite store is unavailable")
    return sql


def ensure_schema() -> None:
    with _sql().connect() as con:
        try:
            con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        for statement in _SCHEMA:
            con.execute(statement)
        con.commit()


def _abi(name: str) -> dict[str, Any]:
    filename = name.removeprefix("adaos.")
    path = Path(__file__).resolve().parents[1] / "abi" / f"{filename}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    if name == REPLY_ROUTE_SCHEMA:
        record.setdefault("thread_id", None)
        record.setdefault("authorized_fallback_route_ids", [])
        record.setdefault("channel_context", {})
        record.setdefault("ordering_key", str(record.get("conversation_id") or "conversation"))
        record.setdefault(
            "delivery_policy",
            {
                "late_result": "origin_thread",
                "on_expiry": "query_only",
                "cross_channel": False,
                "retry_without_execution": True,
            },
        )
    elif name == RESPONSE_ENVELOPE_SCHEMA:
        category = str(record.get("category") or "notification")
        record.setdefault(
            "attention_plan",
            plan_attention(
                category,
                requested_attention=str(record.get("attention") or "normal"),
                coalesce_key=record.get("coalesce_key"),
                policy=default_attention_policy(),
                now=record.get("created_at"),
            ),
        )
        record.setdefault("terminal_key", None)
        record.setdefault("materialization_status", "pending")
        record.setdefault("materialized_at", None)
        record.setdefault("acknowledged_at", None)
    errors = sorted(
        Draft202012Validator(_abi(name)).iter_errors(record),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise DurableDeliveryError(f"{name} validation failed at {location}: {errors[0].message}")
    return record


def _dump(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str) -> dict[str, Any]:
    return dict(json.loads(value))


def _expired(value: str | None, *, now: str) -> bool:
    if not value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) <= datetime.fromisoformat(
            now.replace("Z", "+00:00")
        )
    except ValueError:
        return True


def create_reply_route(
    conversation_id: str,
    *,
    transport: str,
    destination_ref: Mapping[str, Any],
    principal_scope: Sequence[str],
    thread_id: str | None = None,
    route_id: str | None = None,
    capability_profile_ref: Mapping[str, Any] | None = None,
    priority: int = 100,
    expires_at: str | None = None,
    authorized_fallback_route_ids: Sequence[str] = (),
    channel_context: Mapping[str, Any] | None = None,
    ordering_key: str | None = None,
    delivery_policy: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    ensure_schema()
    timestamp = now or _now()
    record = _validate(
        REPLY_ROUTE_SCHEMA,
        {
            "schema": REPLY_ROUTE_SCHEMA,
            "route_id": str(route_id or f"reply-route.{uuid.uuid4().hex}").strip(),
            "conversation_id": str(conversation_id or "").strip(),
            "thread_id": str(thread_id).strip() if thread_id else None,
            "transport": str(transport or "").strip(),
            "destination_ref": copy.deepcopy(dict(destination_ref or {})),
            "principal_scope": list(
                dict.fromkeys(str(item).strip() for item in principal_scope if str(item).strip())
            ),
            "capability_profile_ref": (
                copy.deepcopy(dict(capability_profile_ref))
                if capability_profile_ref is not None
                else None
            ),
            "priority": int(priority),
            "authorized_fallback_route_ids": list(
                dict.fromkeys(
                    str(item).strip()
                    for item in authorized_fallback_route_ids
                    if str(item).strip()
                )
            ),
            "channel_context": copy.deepcopy(dict(channel_context or {})),
            "ordering_key": str(ordering_key or conversation_id or "conversation").strip(),
            "delivery_policy": {
                "late_result": "origin_thread",
                "on_expiry": "query_only",
                "cross_channel": bool(authorized_fallback_route_ids),
                "retry_without_execution": True,
                **copy.deepcopy(dict(delivery_policy or {})),
            },
            "status": "active",
            "expires_at": expires_at,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    )
    payload = _dump(record)
    with _sql().connect() as con:
        existing = con.execute(
            "SELECT payload_json FROM conversation_reply_routes WHERE route_id=?",
            (record["route_id"],),
        ).fetchone()
        if existing:
            current = _load(existing[0])
            if current != record:
                raise DurableDeliveryError("reply route idempotency conflict")
            return current
        con.execute(
            """
            INSERT INTO conversation_reply_routes(
                route_id, conversation_id, transport, status, priority,
                expires_at, payload_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                record["route_id"], record["conversation_id"], record["transport"],
                record["status"], record["priority"], record["expires_at"], payload,
                record["created_at"], record["updated_at"],
            ),
        )
        con.commit()
    return record


def get_reply_route(route_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with _sql().connect() as con:
        row = con.execute(
            "SELECT payload_json FROM conversation_reply_routes WHERE route_id=?",
            (str(route_id or "").strip(),),
        ).fetchone()
    return _validate(REPLY_ROUTE_SCHEMA, _load(row[0])) if row else None


def enqueue_response(
    conversation_id: str,
    category: str,
    *,
    text: str | None = None,
    data: Mapping[str, Any] | None = None,
    workflow_ref: Mapping[str, Any] | None = None,
    task_ref: Mapping[str, Any] | None = None,
    interaction_ref: Mapping[str, Any] | None = None,
    command_id: str | None = None,
    reply_route_ids: Sequence[str] = (),
    sensitivity: str = "internal",
    attention: str = "normal",
    coalesce_key: str | None = None,
    terminal_key: str | None = None,
    attention_policy: Mapping[str, Any] | None = None,
    envelope_id: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    ensure_schema()
    timestamp = now or _now()
    conversation = str(conversation_id or "").strip()
    selected_id = str(envelope_id or f"response.{uuid.uuid4().hex}").strip()
    correlation_task = copy.deepcopy(dict(task_ref)) if task_ref is not None else None
    correlation_workflow = copy.deepcopy(dict(workflow_ref)) if workflow_ref is not None else None
    task_id = str(dict(task_ref or {}).get("id") or "").strip()
    effective_coalesce_key = str(coalesce_key).strip() if coalesce_key else None
    if category == "progress" and not effective_coalesce_key and task_id:
        effective_coalesce_key = f"task:{task_id}:progress"
    effective_terminal_key = str(terminal_key or "").strip() or None
    if category == "terminal" and effective_terminal_key is None:
        workflow_id = str(dict(workflow_ref or {}).get("id") or "").strip()
        effective_terminal_key = (
            f"task:{task_id}" if task_id else f"workflow:{workflow_id}:command:{command_id}"
            if workflow_id or command_id
            else f"conversation:{conversation}:terminal"
        )
    attention_plan = plan_attention(
        str(category or "notification"),
        requested_attention=attention,
        coalesce_key=effective_coalesce_key,
        outcome=str(dict(data or {}).get("outcome") or "") or None,
        reason_code=str(dict(data or {}).get("reason_code") or "") or None,
        policy=attention_policy or default_attention_policy(),
        now=timestamp,
    )
    request_value = {
        "conversation_id": conversation,
        "category": category,
        "text": text,
        "data": dict(data or {}),
        "workflow_ref": correlation_workflow,
        "task_ref": correlation_task,
        "interaction_ref": dict(interaction_ref) if interaction_ref is not None else None,
        "command_id": command_id,
        "reply_route_ids": list(reply_route_ids),
        "sensitivity": sensitivity,
        "attention": attention_plan["attention"],
        "attention_plan": attention_plan,
        "coalesce_key": effective_coalesce_key,
        "terminal_key": effective_terminal_key,
    }
    request_digest = hashlib.sha256(
        json.dumps(
            request_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with _sql().connect() as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        sequence = int(
            con.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM conversation_response_outbox WHERE conversation_id=?",
                (conversation,),
            ).fetchone()[0]
        ) + 1
        status = "pending" if reply_route_ids else "undeliverable"
        record = _validate(
            RESPONSE_ENVELOPE_SCHEMA,
            {
                "schema": RESPONSE_ENVELOPE_SCHEMA,
                "envelope_id": selected_id,
                "conversation_id": conversation,
                "category": str(category or "").strip(),
                "sequence": sequence,
                "correlation": {
                    "workflow_ref": correlation_workflow,
                    "task_ref": correlation_task,
                    "interaction_ref": copy.deepcopy(dict(interaction_ref)) if interaction_ref is not None else None,
                    "command_id": str(command_id).strip() if command_id else None,
                },
                "payload": {"text": str(text) if text is not None else None, "data": copy.deepcopy(dict(data or {}))},
                "sensitivity": sensitivity,
                "attention": attention_plan["attention"],
                "attention_plan": attention_plan,
                "coalesce_key": effective_coalesce_key,
                "terminal_key": effective_terminal_key,
                "reply_route_ids": list(
                    dict.fromkeys(str(item).strip() for item in reply_route_ids if str(item).strip())
                ),
                "status": status,
                "materialization_status": "pending",
                "created_at": timestamp,
                "updated_at": timestamp,
                "materialized_at": None,
                "delivered_at": None,
                "acknowledged_at": None,
            },
        )
        payload = _dump(record)
        existing = con.execute(
            "SELECT payload_digest, payload_json FROM conversation_response_outbox WHERE envelope_id=?",
            (selected_id,),
        ).fetchone()
        if existing:
            con.rollback()
            if str(existing["payload_digest"]) != request_digest:
                raise DurableDeliveryError("response envelope idempotency conflict")
            return _validate(RESPONSE_ENVELOPE_SCHEMA, _load(existing["payload_json"]))
        if category == "terminal" and effective_terminal_key:
            terminal_rows = con.execute(
                """
                SELECT payload_digest, payload_json FROM conversation_response_outbox
                WHERE conversation_id=? AND category='terminal'
                ORDER BY sequence DESC
                """,
                (conversation,),
            ).fetchall()
            for terminal_row in terminal_rows:
                prior_terminal = _validate(
                    RESPONSE_ENVELOPE_SCHEMA,
                    _load(terminal_row["payload_json"]),
                )
                if prior_terminal.get("terminal_key") != effective_terminal_key:
                    continue
                con.rollback()
                if str(terminal_row["payload_digest"]) == request_digest:
                    return prior_terminal
                raise DurableDeliveryError(
                    "terminal response already exists for this workflow/task correlation"
                )
        if category == "progress" and effective_coalesce_key and attention_plan["coalesce"]:
            rows = con.execute(
                """
                SELECT envelope_id, payload_json FROM conversation_response_outbox
                WHERE conversation_id=? AND category='progress' AND coalesce_key=?
                  AND status IN ('pending','delivering')
                """,
                (conversation, effective_coalesce_key),
            ).fetchall()
            for row in rows:
                prior = _load(row["payload_json"])
                prior["status"] = "superseded"
                prior["updated_at"] = timestamp
                con.execute(
                    "UPDATE conversation_response_outbox SET status='superseded', payload_json=?, updated_at=? WHERE envelope_id=?",
                    (_dump(prior), timestamp, row["envelope_id"]),
                )
        con.execute(
            """
            INSERT INTO conversation_response_outbox(
                envelope_id, conversation_id, category, sequence, coalesce_key,
                status, payload_digest, payload_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                selected_id, conversation, record["category"], sequence,
                record["coalesce_key"], status, request_digest, payload, timestamp, timestamp,
            ),
        )
        con.commit()
    return record


def get_envelope(envelope_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with _sql().connect() as con:
        row = con.execute(
            "SELECT payload_json FROM conversation_response_outbox WHERE envelope_id=?",
            (str(envelope_id or "").strip(),),
        ).fetchone()
    return _validate(RESPONSE_ENVELOPE_SCHEMA, _load(row[0])) if row else None


def _save_envelope(con: sqlite3.Connection, record: Mapping[str, Any]) -> None:
    value = _validate(RESPONSE_ENVELOPE_SCHEMA, record)
    con.execute(
        "UPDATE conversation_response_outbox SET status=?, payload_json=?, updated_at=? WHERE envelope_id=?",
        (value["status"], _dump(value), value["updated_at"], value["envelope_id"]),
    )


def mark_response_materialized(
    envelope_id: str,
    *,
    message_ref: Mapping[str, Any] | None = None,
    failed: bool = False,
    now: str | None = None,
) -> dict[str, Any]:
    """Record response materialization without changing transport delivery state."""

    ensure_schema()
    timestamp = now or _now()
    with _sql().connect() as con:
        row = con.execute(
            "SELECT payload_json FROM conversation_response_outbox WHERE envelope_id=?",
            (str(envelope_id or "").strip(),),
        ).fetchone()
        if not row:
            raise DurableDeliveryError(f"response envelope not found: {envelope_id}")
        envelope = _validate(RESPONSE_ENVELOPE_SCHEMA, _load(row[0]))
        if envelope["materialization_status"] == "materialized" and not failed:
            return envelope
        envelope["materialization_status"] = "failed" if failed else "materialized"
        envelope["materialized_at"] = timestamp
        envelope["updated_at"] = timestamp
        if message_ref is not None:
            envelope["payload"].setdefault("data", {})["materialized_message_ref"] = copy.deepcopy(
                dict(message_ref)
            )
        _save_envelope(con, envelope)
        con.commit()
    return envelope


def acknowledge_response(
    envelope_id: str,
    *,
    receipt: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Acknowledge a delivered result; acknowledgement never repeats delivery or work."""

    ensure_schema()
    timestamp = now or _now()
    with _sql().connect() as con:
        row = con.execute(
            "SELECT payload_json FROM conversation_response_outbox WHERE envelope_id=?",
            (str(envelope_id or "").strip(),),
        ).fetchone()
        if not row:
            raise DurableDeliveryError(f"response envelope not found: {envelope_id}")
        envelope = _validate(RESPONSE_ENVELOPE_SCHEMA, _load(row[0]))
        if envelope["status"] == "acknowledged":
            return envelope
        if envelope["status"] != "delivered":
            raise DurableDeliveryError("only a delivered response can be acknowledged")
        envelope["status"] = "acknowledged"
        envelope["acknowledged_at"] = timestamp
        envelope["updated_at"] = timestamp
        if receipt is not None:
            envelope["payload"].setdefault("data", {})["acknowledgement_receipt"] = copy.deepcopy(
                dict(receipt)
            )
        _save_envelope(con, envelope)
        con.commit()
    return envelope


def cancel_response(envelope_id: str, *, now: str | None = None) -> dict[str, Any]:
    """Cancel pending delivery only; the business task has its own cancellation contract."""

    ensure_schema()
    timestamp = now or _now()
    with _sql().connect() as con:
        row = con.execute(
            "SELECT payload_json FROM conversation_response_outbox WHERE envelope_id=?",
            (str(envelope_id or "").strip(),),
        ).fetchone()
        if not row:
            raise DurableDeliveryError(f"response envelope not found: {envelope_id}")
        envelope = _validate(RESPONSE_ENVELOPE_SCHEMA, _load(row[0]))
        if envelope["status"] in {"delivered", "acknowledged", "superseded", "cancelled"}:
            return envelope
        envelope["status"] = "cancelled"
        envelope["updated_at"] = timestamp
        _save_envelope(con, envelope)
        con.commit()
    return envelope


def claim_delivery(
    envelope_id: str,
    route_id: str,
    *,
    presentation_id: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    ensure_schema()
    timestamp = now or _now()
    envelope = get_envelope(envelope_id)
    route = get_reply_route(route_id)
    if envelope is None or route is None:
        raise DurableDeliveryError("response envelope or reply route not found")
    if route_id not in envelope["reply_route_ids"]:
        raise DurableDeliveryError("reply route is not authorized for this envelope")
    if route["conversation_id"] != envelope["conversation_id"]:
        raise DurableDeliveryError("reply route conversation does not match envelope")
    if route["status"] != "active" or _expired(route.get("expires_at"), now=timestamp):
        raise DurableDeliveryError("reply route is unavailable or expired")
    if envelope["status"] in {"delivered", "acknowledged", "undeliverable", "superseded", "cancelled"}:
        raise DurableDeliveryError(f"response envelope is terminal: {envelope['status']}")
    with _sql().connect() as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            """
            SELECT payload_json FROM conversation_delivery_attempts
            WHERE envelope_id=? AND route_id=? AND presentation_id IS ?
            ORDER BY attempt_number DESC
            """,
            (envelope_id, route_id, presentation_id),
        ).fetchall()
        if rows:
            latest = _validate(DELIVERY_ATTEMPT_SCHEMA, _load(rows[0]["payload_json"]))
            if latest["status"] in {"claimed", "delivered"}:
                con.rollback()
                return latest
            attempt_number = int(latest["attempt_number"]) + 1
        else:
            attempt_number = 1
        stable_key = "deliver:" + hashlib.sha256(
            f"{envelope_id}:{route_id}:{presentation_id or ''}".encode("utf-8")
        ).hexdigest()
        attempt = _validate(
            DELIVERY_ATTEMPT_SCHEMA,
            {
                "schema": DELIVERY_ATTEMPT_SCHEMA,
                "attempt_id": f"delivery.{uuid.uuid4().hex}",
                "envelope_id": envelope_id,
                "route_id": route_id,
                "presentation_id": presentation_id,
                "transport": route["transport"],
                "idempotency_key": stable_key,
                "attempt_number": attempt_number,
                "status": "claimed",
                "error": None,
                "receipt": None,
                "claimed_at": timestamp,
                "completed_at": None,
            },
        )
        con.execute(
            """
            INSERT INTO conversation_delivery_attempts(
                attempt_id, envelope_id, route_id, presentation_id, transport,
                idempotency_key, attempt_number, status, payload_json,
                claimed_at, completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                attempt["attempt_id"], envelope_id, route_id, presentation_id,
                attempt["transport"], stable_key, attempt_number, "claimed",
                _dump(attempt), timestamp, None,
            ),
        )
        envelope["status"] = "delivering"
        envelope["updated_at"] = timestamp
        _save_envelope(con, envelope)
        con.commit()
    return attempt


def complete_delivery(
    attempt_id: str,
    *,
    delivered: bool,
    receipt: Mapping[str, Any] | None = None,
    error: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    ensure_schema()
    timestamp = now or _now()
    with _sql().connect() as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT payload_json FROM conversation_delivery_attempts WHERE attempt_id=?",
            (str(attempt_id or "").strip(),),
        ).fetchone()
        if not row:
            raise DurableDeliveryError(f"delivery attempt not found: {attempt_id}")
        attempt = _validate(DELIVERY_ATTEMPT_SCHEMA, _load(row["payload_json"]))
        if attempt["status"] in {"delivered", "failed", "expired", "cancelled"}:
            return attempt
        attempt["status"] = "delivered" if delivered else "failed"
        attempt["receipt"] = copy.deepcopy(dict(receipt)) if receipt is not None else None
        attempt["error"] = None if delivered else str(error or "delivery_failed")[:4000]
        attempt["completed_at"] = timestamp
        con.execute(
            "UPDATE conversation_delivery_attempts SET status=?, payload_json=?, completed_at=? WHERE attempt_id=?",
            (attempt["status"], _dump(attempt), timestamp, attempt["attempt_id"]),
        )
        envelope_row = con.execute(
            "SELECT payload_json FROM conversation_response_outbox WHERE envelope_id=?",
            (attempt["envelope_id"],),
        ).fetchone()
        envelope = _validate(RESPONSE_ENVELOPE_SCHEMA, _load(envelope_row["payload_json"]))
        envelope["status"] = "delivered" if delivered else "pending"
        envelope["delivered_at"] = timestamp if delivered else None
        envelope["updated_at"] = timestamp
        _save_envelope(con, envelope)
        con.commit()
    return attempt


def recover_delivery(*, conversation_id: str | None = None, now: str | None = None) -> dict[str, Any]:
    """Describe resumable delivery only; this function never invokes business work."""

    ensure_schema()
    timestamp = now or _now()
    where = "WHERE status IN ('pending','delivering')"
    params: list[Any] = []
    if conversation_id:
        where += " AND conversation_id=?"
        params.append(str(conversation_id))
    with _sql().connect() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT payload_json FROM conversation_response_outbox {where} ORDER BY conversation_id, sequence",
            params,
        ).fetchall()
    resumable: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for row in rows:
        envelope = _validate(RESPONSE_ENVELOPE_SCHEMA, _load(row["payload_json"]))
        routes = [get_reply_route(item) for item in envelope["reply_route_ids"]]
        active = [
            item for item in routes
            if item and item["status"] == "active" and not _expired(item.get("expires_at"), now=timestamp)
        ]
        target = resumable if active else blocked
        target.append(
            {
                "envelope_id": envelope["envelope_id"],
                "sequence": envelope["sequence"],
                "category": envelope["category"],
                "route_ids": [item["route_id"] for item in active],
                "reason_code": None if active else "no_active_reply_route",
            }
        )
    return {"resumable": resumable, "blocked": blocked}


def terminal_result(conversation_id: str, *, include_sensitive: bool = False) -> dict[str, Any] | None:
    ensure_schema()
    with _sql().connect() as con:
        row = con.execute(
            """
            SELECT payload_json FROM conversation_response_outbox
            WHERE conversation_id=? AND category='terminal'
            ORDER BY sequence DESC LIMIT 1
            """,
            (str(conversation_id or "").strip(),),
        ).fetchone()
    if not row:
        return None
    result = _validate(RESPONSE_ENVELOPE_SCHEMA, _load(row[0]))
    if result["sensitivity"] == "sensitive" and not include_sensitive:
        result["payload"] = {"text": "[redacted]", "data": {}}
    return result
