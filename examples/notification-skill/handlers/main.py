"""SDK-only source transaction/outbox and captured background completion.

This module owns source facts only. Core owns recipient state and all delivery.
Run initialize in the admitted data lifecycle; installed upgrades use the
Application migration owner, never opportunistic schema changes in a handler.
"""
import json
import time

from adaos.sdk import notifications
from adaos.sdk.data import relational


def initialize():
    db = relational.database()
    db.migrate([relational.RelationalMigration(version=1, name="source-and-notification-intent", statements=(
        "CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, publisher_ref TEXT NOT NULL)",
        "CREATE TABLE notification_outbox(event_id TEXT PRIMARY KEY, publisher_ref TEXT NOT NULL, request_json TEXT NOT NULL, receipt_json TEXT)",
    ))])


def begin_task(task_id: str, title: str):
    """At verified ingress, capture the original user's owned runtime identity."""
    db = relational.database()
    existing = db.fetch_one("SELECT id FROM tasks WHERE id=:id", {"id": task_id})
    if existing:
        return existing
    captured = notifications.capture_publisher(lifetime_seconds=86400)
    with db.transaction() as tx:
        tx.execute("INSERT INTO tasks VALUES(:id,:title,0,'running',:publisher) ON CONFLICT(id) DO NOTHING",
                   {"id": task_id, "title": title, "publisher": captured["publisher_ref"]})
    return {"id": task_id}


def complete_task(task_id: str, *, title: str, expected_revision: int):
    """Commit the outcome and durable admission intent in the same source transaction.

    A crash before commit admits neither fact. A crash after commit leaves the
    exact stable request for reconciliation. Neither recovery re-runs the task.
    """
    db = relational.database()
    with db.transaction() as tx:
        task = tx.fetch_one("SELECT * FROM tasks WHERE id=:id", {"id": task_id})
        if not task:
            raise ValueError("task not found")
        if task["revision"] != expected_revision:
            raise ValueError("source revision changed; reconcile its existing outcome")
        revision = expected_revision + 1
        request = {"category_id": "task.completed", "event_id": f"{task_id}:completed:{revision}",
            "source_id": "task:" + task_id, "source_revision": revision,
            "occurred_at": time.time(), "payload": {"title": title}}
        updated = tx.execute("UPDATE tasks SET title=:title,revision=:revision,status='completed' WHERE id=:id AND revision=:expected",
            {"id": task_id, "title": title, "revision": revision, "expected": expected_revision})
        if updated.rowcount != 1:
            raise ValueError("source completion lost its revision fence")
        tx.execute("INSERT INTO notification_outbox VALUES(:event,:publisher,:request,NULL)",
            {"event": request["event_id"], "publisher": task["publisher_ref"], "request": json.dumps(request)})
    return {"task_id": task_id, "revision": revision, "notification": "awaiting_Core_admission"}


def reconcile_notifications(*, limit: int = 25):
    """Bounded source->Core handoff; transport retries belong to Core alone."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
    db = relational.database()
    rows = db.fetch_all("SELECT * FROM notification_outbox WHERE receipt_json IS NULL ORDER BY event_id LIMIT :limit", {"limit": limit})
    for row in rows:
        # Failure leaves the original durable request. Retry never changes the
        # event/occurrence time, executes the task, or invents a new recipient.
        receipt = notifications.emit_captured(row["publisher_ref"], **json.loads(row["request_json"]))
        db.execute("UPDATE notification_outbox SET receipt_json=:receipt WHERE event_id=:event",
            {"event": row["event_id"], "receipt": json.dumps(receipt)})
    return {"admitted": len(rows)}


def retract_task_notice(task_id: str):
    """Explicit foreground withdrawal changes attention, not the task outcome."""
    return notifications.withdraw("task:" + task_id)
