from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from adaos.services.agent_context import get_ctx
from adaos.services.governed_workflow import (
    WORKFLOW_EVENT_SCHEMA,
    WORKFLOW_INSTANCE_SCHEMA,
    validate_workflow_record,
)


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS governed_workflow_instances (
        instance_id TEXT PRIMARY KEY,
        workflow_type TEXT NOT NULL,
        definition_version TEXT NOT NULL,
        state TEXT NOT NULL,
        generation INTEGER NOT NULL,
        snapshot_digest TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS governed_workflow_journal (
        instance_id TEXT NOT NULL,
        generation INTEGER NOT NULL,
        event_id TEXT NOT NULL UNIQUE,
        event_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(instance_id, generation)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS governed_workflow_inbox (
        instance_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(instance_id, idempotency_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS governed_workflow_outbox (
        outbox_id TEXT PRIMARY KEY,
        instance_id TEXT NOT NULL,
        generation INTEGER NOT NULL,
        topic TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        delivered_at TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governed_workflow_outbox_pending
    ON governed_workflow_outbox(status, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS governed_workflow_activity_attempts (
        attempt_id TEXT PRIMARY KEY,
        instance_id TEXT NOT NULL,
        generation INTEGER NOT NULL,
        transition_id TEXT NOT NULL,
        activity TEXT NOT NULL,
        effect_binding_json TEXT NOT NULL,
        target_digest TEXT,
        approval_witness_json TEXT,
        retry_policy TEXT NOT NULL,
        status TEXT NOT NULL,
        effect_started INTEGER NOT NULL DEFAULT 0,
        outcome_json TEXT,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governed_workflow_activity_recovery
    ON governed_workflow_activity_attempts(status, effect_started, updated_at)
    """,
)


class WorkflowPersistenceError(ValueError):
    """Raised when durable workflow commit or recovery cannot be proven safe."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sql() -> Any:
    try:
        sql = get_ctx().sql
    except Exception as exc:  # pragma: no cover - runtime wiring failure
        raise WorkflowPersistenceError("durable AdaOS SQLite store is unavailable") from exc
    if not sql or not hasattr(sql, "connect"):
        raise WorkflowPersistenceError("durable AdaOS SQLite store is unavailable")
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


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def create_instance(instance: Mapping[str, Any]) -> dict[str, Any]:
    ensure_schema()
    record = validate_workflow_record(WORKFLOW_INSTANCE_SCHEMA, instance)
    payload = _dump(record)
    digest = _digest(record)
    with _sql().connect() as con:
        row = con.execute(
            "SELECT snapshot_digest, snapshot_json FROM governed_workflow_instances WHERE instance_id=?",
            (record["instance_id"],),
        ).fetchone()
        if row:
            if str(row[0]) != digest:
                raise WorkflowPersistenceError("workflow instance idempotency conflict")
            return validate_workflow_record(WORKFLOW_INSTANCE_SCHEMA, json.loads(row[1]))
        con.execute(
            """
            INSERT INTO governed_workflow_instances(
                instance_id, workflow_type, definition_version, state, generation,
                snapshot_digest, snapshot_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                record["instance_id"], record["workflow_type"], record["definition_version"],
                record["state"], record["generation"], digest, payload,
                record.get("created_at") or record.get("updated_at") or _now(),
                record.get("updated_at") or _now(),
            ),
        )
        con.commit()
    return record


def get_instance(instance_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with _sql().connect() as con:
        row = con.execute(
            "SELECT snapshot_json FROM governed_workflow_instances WHERE instance_id=?",
            (str(instance_id or "").strip(),),
        ).fetchone()
    return validate_workflow_record(WORKFLOW_INSTANCE_SCHEMA, json.loads(row[0])) if row else None


def commit_decision(
    decision: Mapping[str, Any],
    *,
    idempotency_key: str,
    permission_granted: bool,
    target_digest: str | None = None,
    expected_target_digest: str | None = None,
    approval_required: bool = False,
    approval_witness: Mapping[str, Any] | None = None,
    effect_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically commit snapshot, journal, inbox, outbox, and activity intent."""

    ensure_schema()
    value = copy.deepcopy(dict(decision))
    if not bool(value.get("accepted")) or value.get("status") != "accepted":
        raise WorkflowPersistenceError("only a newly accepted workflow decision can be committed")
    if not permission_granted:
        raise WorkflowPersistenceError("permission must be revalidated at durable commit")
    before = validate_workflow_record(WORKFLOW_INSTANCE_SCHEMA, value.get("before") or {})
    after = validate_workflow_record(WORKFLOW_INSTANCE_SCHEMA, value.get("after") or {})
    if after["instance_id"] != before["instance_id"] or int(after["generation"]) != int(before["generation"]) + 1:
        raise WorkflowPersistenceError("workflow decision generation is not contiguous")
    if expected_target_digest is not None and target_digest != expected_target_digest:
        raise WorkflowPersistenceError("workflow target digest changed before durable commit")
    if approval_required and not approval_witness:
        raise WorkflowPersistenceError("approval witness is required at durable commit")
    activity = dict(value.get("activity") or {})
    activity_name = str(activity.get("activity") or "").strip()
    binding = dict(effect_binding or {})
    if activity_name:
        if str(binding.get("activity") or "").strip() != activity_name:
            raise WorkflowPersistenceError("activity effect binding is missing or mismatched")
        if not str(binding.get("executor") or "").strip():
            raise WorkflowPersistenceError("activity effect binding requires executor identity")
    key = str(idempotency_key or "").strip()
    if not key:
        raise WorkflowPersistenceError("idempotency_key is required")
    event_records = [
        validate_workflow_record(WORKFLOW_EVENT_SCHEMA, item)
        for item in value.get("event_records") or []
    ]
    if len(event_records) != 1:
        raise WorkflowPersistenceError("one accepted transition must contain one canonical event")
    event = event_records[0]
    request_digest = str(event["payload_digest"])
    timestamp = str(value.get("decided_at") or after.get("updated_at") or _now())
    result = {
        "instance": after,
        "event": event,
        "activity_attempt_id": None,
        "duplicate": False,
    }
    with _sql().connect() as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        inbox = con.execute(
            "SELECT payload_digest, result_json FROM governed_workflow_inbox WHERE instance_id=? AND idempotency_key=?",
            (after["instance_id"], key),
        ).fetchone()
        if inbox:
            con.rollback()
            if str(inbox["payload_digest"]) != request_digest:
                raise WorkflowPersistenceError("workflow inbox idempotency conflict")
            duplicate = dict(json.loads(inbox["result_json"]))
            duplicate["duplicate"] = True
            return duplicate
        current = con.execute(
            "SELECT generation, snapshot_digest FROM governed_workflow_instances WHERE instance_id=?",
            (after["instance_id"],),
        ).fetchone()
        if current is None:
            raise WorkflowPersistenceError("workflow instance must be created before commit")
        if int(current["generation"]) != int(before["generation"]):
            raise WorkflowPersistenceError("stale workflow generation at durable commit")
        before_digest = _digest(before)
        if str(current["snapshot_digest"]) != before_digest:
            raise WorkflowPersistenceError("workflow snapshot changed without generation advance")
        after_payload = _dump(after)
        updated = con.execute(
            """
            UPDATE governed_workflow_instances
            SET definition_version=?, state=?, generation=?, snapshot_digest=?, snapshot_json=?, updated_at=?
            WHERE instance_id=? AND generation=? AND snapshot_digest=?
            """,
            (
                after["definition_version"], after["state"], after["generation"], _digest(after), after_payload,
                after.get("updated_at") or timestamp, after["instance_id"],
                before["generation"], before_digest,
            ),
        )
        if updated.rowcount != 1:
            raise WorkflowPersistenceError("workflow compare-and-swap failed")
        con.execute(
            "INSERT INTO governed_workflow_journal(instance_id, generation, event_id, event_json, created_at) VALUES(?,?,?,?,?)",
            (after["instance_id"], after["generation"], event["event_id"], _dump(event), event["created_at"]),
        )
        outbox_id = f"workflow-outbox:{event['event_id']}"
        con.execute(
            """
            INSERT INTO governed_workflow_outbox(
                outbox_id, instance_id, generation, topic, payload_json, status,
                attempt_count, created_at, delivered_at
            ) VALUES(?,?,?,?,?,'pending',0,?,NULL)
            """,
            (
                outbox_id, after["instance_id"], after["generation"],
                str(event["type"]), _dump(event), timestamp,
            ),
        )
        if activity_name:
            attempt_id = f"activity:{after['instance_id']}:{after['generation']}:{value['transition_id']}"
            con.execute(
                """
                INSERT INTO governed_workflow_activity_attempts(
                    attempt_id, instance_id, generation, transition_id, activity,
                    effect_binding_json, target_digest, approval_witness_json,
                    retry_policy, status, effect_started, outcome_json,
                    evidence_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,'scheduled',0,NULL,'[]',?,?)
                """,
                (
                    attempt_id, after["instance_id"], after["generation"],
                    value["transition_id"], activity_name, _dump(binding), target_digest,
                    _dump(approval_witness) if approval_witness is not None else None,
                    str(activity.get("retry") or "never"), timestamp, timestamp,
                ),
            )
            result["activity_attempt_id"] = attempt_id
        con.execute(
            "INSERT INTO governed_workflow_inbox(instance_id, idempotency_key, payload_digest, result_json, created_at) VALUES(?,?,?,?,?)",
            (after["instance_id"], key, request_digest, _dump(result), timestamp),
        )
        con.commit()
    return result


def list_events(instance_id: str) -> list[dict[str, Any]]:
    ensure_schema()
    with _sql().connect() as con:
        rows = con.execute(
            "SELECT event_json FROM governed_workflow_journal WHERE instance_id=? ORDER BY generation",
            (str(instance_id or "").strip(),),
        ).fetchall()
    return [validate_workflow_record(WORKFLOW_EVENT_SCHEMA, json.loads(row[0])) for row in rows]


def _attempt(attempt_id: str) -> dict[str, Any]:
    ensure_schema()
    with _sql().connect() as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM governed_workflow_activity_attempts WHERE attempt_id=?",
            (str(attempt_id or "").strip(),),
        ).fetchone()
    if not row:
        raise WorkflowPersistenceError(f"workflow activity attempt not found: {attempt_id}")
    return {
        "attempt_id": row["attempt_id"],
        "instance_id": row["instance_id"],
        "generation": int(row["generation"]),
        "transition_id": row["transition_id"],
        "activity": row["activity"],
        "effect_binding": json.loads(row["effect_binding_json"]),
        "target_digest": row["target_digest"],
        "approval_witness": json.loads(row["approval_witness_json"]) if row["approval_witness_json"] else None,
        "retry_policy": row["retry_policy"],
        "status": row["status"],
        "effect_started": bool(row["effect_started"]),
        "outcome": json.loads(row["outcome_json"]) if row["outcome_json"] else None,
        "evidence_refs": json.loads(row["evidence_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def claim_activity(attempt_id: str, *, now: str | None = None) -> dict[str, Any]:
    timestamp = now or _now()
    attempt = _attempt(attempt_id)
    if attempt["status"] == "claimed" and not attempt["effect_started"]:
        return attempt
    if attempt["status"] != "scheduled":
        raise WorkflowPersistenceError(f"activity attempt is not claimable: {attempt['status']}")
    with _sql().connect() as con:
        con.execute(
            "UPDATE governed_workflow_activity_attempts SET status='claimed', updated_at=? WHERE attempt_id=? AND status='scheduled'",
            (timestamp, attempt_id),
        )
        con.commit()
    return _attempt(attempt_id)


def mark_effect_started(attempt_id: str, *, now: str | None = None) -> dict[str, Any]:
    attempt = _attempt(attempt_id)
    if attempt["status"] == "running" and attempt["effect_started"]:
        return attempt
    if attempt["status"] != "claimed" or attempt["effect_started"]:
        raise WorkflowPersistenceError("activity effect can start only after a durable claim")
    with _sql().connect() as con:
        con.execute(
            "UPDATE governed_workflow_activity_attempts SET status='running', effect_started=1, updated_at=? WHERE attempt_id=?",
            (now or _now(), attempt_id),
        )
        con.commit()
    return _attempt(attempt_id)


def complete_activity(
    attempt_id: str,
    outcome: str,
    *,
    result: Mapping[str, Any] | None = None,
    evidence_refs: list[str] | tuple[str, ...] = (),
    now: str | None = None,
) -> dict[str, Any]:
    attempt = _attempt(attempt_id)
    selected = str(outcome or "").strip().lower()
    if selected not in {"succeeded", "failed", "cancelled", "outcome_unknown"}:
        raise WorkflowPersistenceError("invalid activity outcome")
    if attempt["status"] in {"succeeded", "failed", "cancelled", "outcome_unknown"}:
        if attempt["status"] != selected:
            raise WorkflowPersistenceError("terminal activity outcome cannot be changed")
        return attempt
    if selected == "succeeded" and not attempt["effect_started"]:
        raise WorkflowPersistenceError("activity cannot succeed before its effect started")
    with _sql().connect() as con:
        con.execute(
            """
            UPDATE governed_workflow_activity_attempts
            SET status=?, outcome_json=?, evidence_json=?, updated_at=?
            WHERE attempt_id=?
            """,
            (
                selected, _dump(dict(result or {})),
                _dump(list(dict.fromkeys(str(item) for item in evidence_refs if str(item).strip()))),
                now or _now(), attempt_id,
            ),
        )
        con.commit()
    return _attempt(attempt_id)


def recovery_report(instance_id: str | None = None) -> dict[str, Any]:
    """Classify abandoned attempts without executing or retrying any effect."""

    ensure_schema()
    where = "WHERE status IN ('scheduled','claimed','running','outcome_unknown')"
    params: list[Any] = []
    if instance_id:
        where += " AND instance_id=?"
        params.append(str(instance_id))
    with _sql().connect() as con:
        rows = con.execute(
            f"SELECT attempt_id FROM governed_workflow_activity_attempts {where} ORDER BY created_at",
            params,
        ).fetchall()
    safe_resume: list[dict[str, Any]] = []
    reconciliation: list[dict[str, Any]] = []
    for row in rows:
        attempt = _attempt(row[0])
        target = reconciliation if attempt["effect_started"] or attempt["status"] == "outcome_unknown" else safe_resume
        target.append(
            {
                "attempt_id": attempt["attempt_id"],
                "activity": attempt["activity"],
                "reason_code": (
                    "effect_outcome_unknown" if target is reconciliation else "effect_not_started"
                ),
            }
        )
    return {"safe_resume": safe_resume, "reconciliation_required": reconciliation}


def cancel_activity(
    attempt_id: str,
    *,
    reason: str,
    now: str | None = None,
) -> dict[str, Any]:
    attempt = _attempt(attempt_id)
    if attempt["status"] in {"succeeded", "failed", "cancelled", "outcome_unknown"}:
        return attempt
    outcome = "outcome_unknown" if attempt["effect_started"] else "cancelled"
    return complete_activity(
        attempt_id,
        outcome,
        result={"reason": str(reason or "cancelled")[:1000]},
        now=now,
    )


def claim_outbox(limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema()
    with _sql().connect() as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            """
            SELECT * FROM governed_workflow_outbox
            WHERE status='pending' ORDER BY created_at LIMIT ?
            """,
            (max(1, min(1000, int(limit))),),
        ).fetchall()
        for row in rows:
            con.execute(
                "UPDATE governed_workflow_outbox SET status='claimed', attempt_count=attempt_count+1 WHERE outbox_id=? AND status='pending'",
                (row["outbox_id"],),
            )
        con.commit()
    return [
        {
            "outbox_id": row["outbox_id"],
            "instance_id": row["instance_id"],
            "generation": int(row["generation"]),
            "topic": row["topic"],
            "payload": json.loads(row["payload_json"]),
            "attempt_count": int(row["attempt_count"]) + 1,
        }
        for row in rows
    ]


def complete_outbox(outbox_id: str, *, delivered: bool, now: str | None = None) -> None:
    ensure_schema()
    with _sql().connect() as con:
        row = con.execute(
            "SELECT status FROM governed_workflow_outbox WHERE outbox_id=?",
            (str(outbox_id or "").strip(),),
        ).fetchone()
        if not row:
            raise WorkflowPersistenceError(f"workflow outbox item not found: {outbox_id}")
        if str(row[0]) == "delivered":
            return
        con.execute(
            "UPDATE governed_workflow_outbox SET status=?, delivered_at=? WHERE outbox_id=?",
            ("delivered" if delivered else "pending", (now or _now()) if delivered else None, outbox_id),
        )
        con.commit()


def operator_describe(instance_id: str) -> dict[str, Any]:
    snapshot = get_instance(instance_id)
    if snapshot is None:
        raise WorkflowPersistenceError(f"workflow instance not found: {instance_id}")
    ensure_schema()
    with _sql().connect() as con:
        outbox = con.execute(
            "SELECT status, COUNT(*) FROM governed_workflow_outbox WHERE instance_id=? GROUP BY status",
            (instance_id,),
        ).fetchall()
        activities = con.execute(
            "SELECT status, COUNT(*) FROM governed_workflow_activity_attempts WHERE instance_id=? GROUP BY status",
            (instance_id,),
        ).fetchall()
    return {
        "schema": "adaos.workflow.operator_description.v1",
        "instance_id": instance_id,
        "workflow_type": snapshot["workflow_type"],
        "definition_version": snapshot["definition_version"],
        "state": snapshot["state"],
        "generation": snapshot["generation"],
        "outbox": {str(row[0]): int(row[1]) for row in outbox},
        "activities": {str(row[0]): int(row[1]) for row in activities},
        "recovery": recovery_report(instance_id),
    }


def operational_metrics() -> dict[str, Any]:
    ensure_schema()
    with _sql().connect() as con:
        page_count = int(con.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(con.execute("PRAGMA page_size").fetchone()[0])
        instances = int(con.execute("SELECT COUNT(*) FROM governed_workflow_instances").fetchone()[0])
        events = int(con.execute("SELECT COUNT(*) FROM governed_workflow_journal").fetchone()[0])
        attempts = int(con.execute("SELECT COUNT(*) FROM governed_workflow_activity_attempts").fetchone()[0])
        unknown = int(
            con.execute(
                "SELECT COUNT(*) FROM governed_workflow_activity_attempts WHERE status='outcome_unknown' OR (status='running' AND effect_started=1)"
            ).fetchone()[0]
        )
        pending_outbox = int(
            con.execute("SELECT COUNT(*) FROM governed_workflow_outbox WHERE status!='delivered'").fetchone()[0]
        )
    return {
        "schema": "adaos.workflow.reference_metrics.v1",
        "storage_bytes": page_count * page_size,
        "instances": instances,
        "events": events,
        "activity_attempts": attempts,
        "outcome_unknown": unknown,
        "pending_outbox": pending_outbox,
        "recovery_branches": 2,
        "automatic_unknown_retries": 0,
    }


def compact_reference_state(*, retain_delivered_outbox: int = 1000) -> dict[str, int]:
    """Bound delivered transport state while retaining snapshots and canonical journal."""

    ensure_schema()
    keep = max(0, int(retain_delivered_outbox))
    with _sql().connect() as con:
        before = con.total_changes
        con.execute(
            """
            DELETE FROM governed_workflow_outbox
            WHERE status='delivered' AND outbox_id NOT IN (
                SELECT outbox_id FROM governed_workflow_outbox
                WHERE status='delivered' ORDER BY delivered_at DESC LIMIT ?
            )
            """,
            (keep,),
        )
        deleted = con.total_changes - before
        con.commit()
    return {"deleted_delivered_outbox": deleted, "retained_canonical_journal": 1}


def export_instance(instance_id: str) -> dict[str, Any]:
    snapshot = get_instance(instance_id)
    if snapshot is None:
        raise WorkflowPersistenceError(f"workflow instance not found: {instance_id}")
    return {
        "schema": "adaos.workflow.backup.v1",
        "snapshot": snapshot,
        "events": list_events(instance_id),
        "exported_at": _now(),
    }


def restore_instance(backup: Mapping[str, Any]) -> dict[str, Any]:
    if backup.get("schema") != "adaos.workflow.backup.v1":
        raise WorkflowPersistenceError("unsupported workflow backup schema")
    snapshot = validate_workflow_record(WORKFLOW_INSTANCE_SCHEMA, backup.get("snapshot") or {})
    if get_instance(snapshot["instance_id"]) is not None:
        raise WorkflowPersistenceError("workflow restore target already exists")
    events = [validate_workflow_record(WORKFLOW_EVENT_SCHEMA, item) for item in backup.get("events") or []]
    if events and int(events[-1]["generation"]) != int(snapshot["generation"]):
        raise WorkflowPersistenceError("workflow backup journal does not match snapshot generation")
    ensure_schema()
    with _sql().connect() as con:
        con.execute("BEGIN IMMEDIATE")
        payload = _dump(snapshot)
        con.execute(
            """
            INSERT INTO governed_workflow_instances(
                instance_id, workflow_type, definition_version, state, generation,
                snapshot_digest, snapshot_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot["instance_id"], snapshot["workflow_type"], snapshot["definition_version"],
                snapshot["state"], snapshot["generation"], _digest(snapshot), payload,
                snapshot.get("created_at") or _now(), snapshot.get("updated_at") or _now(),
            ),
        )
        for event in events:
            con.execute(
                "INSERT INTO governed_workflow_journal(instance_id, generation, event_id, event_json, created_at) VALUES(?,?,?,?,?)",
                (snapshot["instance_id"], event["generation"], event["event_id"], _dump(event), event["created_at"]),
            )
        con.commit()
    return snapshot
