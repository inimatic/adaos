from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from adaos.services.agent_context import get_ctx


ATTEMPT_SCHEMA = "adaos.telegram.outbound_attempt.v1"
_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS telegram_outbound_attempts (
        attempt_id TEXT PRIMARY KEY,
        operation_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        claimed_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_telegram_outbound_attempts_status
    ON telegram_outbound_attempts(status, claimed_at)
    """,
)


class TelegramDeliveryError(ValueError):
    """Raised when a transport receipt cannot be applied unambiguously."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sql() -> Any:
    try:
        sql = get_ctx().sql
    except Exception as exc:  # pragma: no cover - runtime wiring failure
        raise TelegramDeliveryError("durable AdaOS SQLite store is unavailable") from exc
    if not sql or not hasattr(sql, "connect"):
        raise TelegramDeliveryError("durable AdaOS SQLite store is unavailable")
    return sql


def _validator() -> Draft202012Validator:
    path = Path(__file__).resolve().parents[1] / "abi" / "telegram.outbound_attempt.v1.schema.json"
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def validate_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "abi" / "telegram.delivery_receipt.v1.schema.json"
    record = copy.deepcopy(dict(value))
    errors = sorted(
        Draft202012Validator(json.loads(path.read_text(encoding="utf-8"))).iter_errors(record),
        key=lambda item: list(item.path),
    )
    if errors:
        raise TelegramDeliveryError(errors[0].message)
    return record


def _validate(value: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    errors = sorted(_validator().iter_errors(record), key=lambda item: list(item.path))
    if errors:
        raise TelegramDeliveryError(errors[0].message)
    return record


def _dump(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TelegramDeliveryError("stored Telegram delivery attempt is not an object")
    return parsed


def ensure_schema() -> None:
    with _sql().connect() as con:
        try:
            con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        for statement in _SCHEMA:
            con.execute(statement)
        con.commit()


def claim_outbound(
    operation_key: str,
    *,
    hub_id: str,
    bot_id: str,
    chat_id: str,
    message_count: int,
    response_envelope_id: str | None = None,
    durable_attempt_id: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    ensure_schema()
    immutable = {
        "operation_key": str(operation_key or "").strip(),
        "hub_id": str(hub_id or "").strip(),
        "bot_id": str(bot_id or "").strip(),
        "chat_id": str(chat_id or "").strip(),
        "message_count": int(message_count),
        "response_envelope_id": str(response_envelope_id or "").strip() or None,
        "durable_attempt_id": str(durable_attempt_id or "").strip() or None,
    }
    payload_digest = "sha256:" + hashlib.sha256(_dump(immutable).encode("utf-8")).hexdigest()
    timestamp = now or _now()
    record = _validate(
        {
            "schema": ATTEMPT_SCHEMA,
            "attempt_id": f"tg-delivery.{uuid.uuid4().hex}",
            **immutable,
            "status": "claimed",
            "payload_digest": payload_digest,
            "receipt": None,
            "error": None,
            "claimed_at": timestamp,
            "completed_at": None,
        }
    )
    with _sql().connect() as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT payload_json FROM telegram_outbound_attempts WHERE operation_key=?",
            (record["operation_key"],),
        ).fetchone()
        if row:
            current = _validate(_load(row["payload_json"]))
            con.rollback()
            if current["payload_digest"] != payload_digest:
                raise TelegramDeliveryError("Telegram operation key idempotency conflict")
            return current
        con.execute(
            """
            INSERT INTO telegram_outbound_attempts(
                attempt_id, operation_key, status, payload_digest, payload_json,
                claimed_at, completed_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                record["attempt_id"], record["operation_key"], record["status"],
                record["payload_digest"], _dump(record), record["claimed_at"], None,
            ),
        )
        con.commit()
    return record


def get_attempt(attempt_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with _sql().connect() as con:
        row = con.execute(
            "SELECT payload_json FROM telegram_outbound_attempts WHERE attempt_id=?",
            (str(attempt_id or "").strip(),),
        ).fetchone()
    return _validate(_load(row[0])) if row else None


def complete_outbound(
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
            "SELECT payload_json FROM telegram_outbound_attempts WHERE attempt_id=?",
            (str(attempt_id or "").strip(),),
        ).fetchone()
        if not row:
            con.rollback()
            raise TelegramDeliveryError(f"Telegram delivery attempt not found: {attempt_id}")
        attempt = _validate(_load(row["payload_json"]))
        if attempt["status"] in {"delivered", "failed"}:
            con.rollback()
            return attempt
        attempt["status"] = "delivered" if delivered else "failed"
        attempt["receipt"] = copy.deepcopy(dict(receipt or {})) or None
        attempt["error"] = None if delivered else str(error or "telegram_delivery_failed")[:4000]
        attempt["completed_at"] = timestamp
        attempt = _validate(attempt)
        con.execute(
            "UPDATE telegram_outbound_attempts SET status=?, payload_json=?, completed_at=? WHERE attempt_id=?",
            (attempt["status"], _dump(attempt), timestamp, attempt["attempt_id"]),
        )
        con.commit()
    return attempt
