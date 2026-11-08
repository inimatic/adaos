"""Durable Core notification admission, private recipient queries and lifecycle."""
from __future__ import annotations

import base64
from dataclasses import asdict
import json
import math
import sqlite3
import time
from typing import Any, Callable, Mapping

from adaos.domain.notifications import (
    NotificationConflict, NotificationError, NotificationReceipt, ProducerBinding,
    compile_manifest, digest, dump, render, token,
)
from adaos.services.agent_context import get_ctx
from adaos.services.notifications.policy import evaluate
from adaos.services.notifications.store import NotificationStore
from adaos.services.user.attention_preferences import AttentionPreferences


class NotificationService:
    """Internal port. Only verified Core/SDK ingress constructs producer bindings."""
    def __init__(self, ctx=None, *, clock: Callable[[], float] = time.time):
        self.ctx = ctx or get_ctx()
        self.store = NotificationStore(self.ctx)
        self.preferences = AttentionPreferences(self.ctx)
        self.clock = clock

    def admit_catalog(self, binding: ProducerBinding, manifest: Mapping[str, Any], *, root=None) -> dict:
        compiled = compile_manifest(manifest, root=root)
        key = digest([binding.instance_id, binding.namespace, binding.release_digest])
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            previous = con.execute("SELECT catalog_digest FROM notification_catalogs WHERE catalog_key=?", (key,)).fetchone()
            if previous and previous[0] != compiled["digest"] and not binding.test_only:
                raise NotificationConflict("an admitted immutable release cannot change its categories")
            con.execute("""INSERT INTO notification_catalogs VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(catalog_key) DO UPDATE SET catalog_digest=excluded.catalog_digest,
                catalog_json=excluded.catalog_json,binding_json=excluded.binding_json""",
                (key, binding.application_id, binding.instance_id, binding.namespace,
                 binding.release_digest, compiled["digest"], dump(compiled), dump(asdict(binding)), self.clock()))
            con.commit()
        return {"catalog_key": key, "digest": compiled["digest"],
                "permission_disclosure": compiled["permission_disclosure"]}

    def _category(self, con, binding, category_id):
        key = digest([binding.instance_id, binding.namespace, binding.release_digest])
        row = con.execute("SELECT catalog_json FROM notification_catalogs WHERE catalog_key=?", (key,)).fetchone()
        if row is None:
            raise NotificationError("notification catalog is not admitted")
        catalog = json.loads(row[0])
        category = catalog["categories"].get(category_id)
        if category is None:
            raise NotificationError("notification category is not declared")
        return key, catalog, category

    @staticmethod
    def _endpoints(con, principal, subnet):
        return [dict(row) for row in con.execute(
            "SELECT * FROM notification_endpoints WHERE principal_ref=? AND subnet_ref=? ORDER BY endpoint_id LIMIT 100",
            (principal, subnet))]

    def _save(self, con, record):
        con.execute("""UPDATE notification_records SET source_revision=?,content_revision=?,
            recipient_revision=?,read_revision=?,archived=?,source_state=?,expires_at=?,
            snoozed_until=?,updated_at=?,record_json=? WHERE notification_id=?""",
            (record["source_revision"], record["content_revision"], record["recipient_revision"],
             record["read_revision"], int(record["archived"]), record["source_state"], record["expires_at"],
             record["snoozed_until"], record["updated_at"], dump(record), record["notification_id"]))

    def _plan(self, con, record, *, admission=False):
        prefs = self.preferences.read(record["principal_ref"], record["subnet_ref"], connection=con)
        endpoints = self._endpoints(con, record["principal_ref"], record["subnet_ref"])
        decision = evaluate(record, prefs, endpoints, now=self.clock(), admission=admission)
        old = record.get("decision") or {}
        revision = int(old.get("decision_revision", 0))
        if digest({k: v for k, v in old.items() if k != "decision_revision"}) != digest(decision):
            revision += 1
        record["decision"] = {**decision, "decision_revision": revision}
        if admission:
            record["admitted_endpoint_ids"] = list(decision["endpoint_ids"])
            record["admission_muted"] = decision["reason"] == "muted"
        elif decision["mode"] in {"off", "center_only"}:
            record["cancelled_endpoint_ids"] = list(record.get("admitted_endpoint_ids", []))
        else:
            active = {ep["endpoint_id"] for ep in endpoints if ep["status"] == "active"}
            selected = set(decision["endpoint_ids"])
            cancelled = set(record.get("cancelled_endpoint_ids", []))
            cancelled.update(set(record.get("admitted_endpoint_ids", [])) - (active & selected))
            record["cancelled_endpoint_ids"] = sorted(cancelled)
        due, status = decision["next_eligible_at"], "paused"
        if decision["alert"]:
            from adaos.services.notifications.delivery import enqueue_targets
            enqueue_targets(self, con, record, prefs, endpoints)
            status = "planned"
            if record["decision"]["next_eligible_at"] is not None:
                due, status = record["decision"]["next_eligible_at"], "pending"
        elif due is not None:
            status = "pending"
        elif decision["reason"] in {"expired", "withdrawn", "muted", "preview", "archived", "already_read"}:
            status = "terminal"
        con.execute("""INSERT INTO notification_schedule VALUES(?,?,?,?)
            ON CONFLICT(notification_id) DO UPDATE SET status=excluded.status,due_at=excluded.due_at""",
            (record["notification_id"], record["principal_ref"], status, due))
        self._save(con, record)
        return record["decision"]

    def emit(
        self, binding: ProducerBinding, *, category_id: str, event_id: str, source_id: str,
        source_revision: int, payload: Mapping[str, Any], recipient_ref: str | None = None,
        urgency: str = "normal", locale: str = "en", occurred_at: float | None = None, connection=None,
    ) -> NotificationReceipt:
        for name, value in (("category_id", category_id), ("event_id", event_id), ("source_id", source_id)):
            token(value, name)
        if type(source_revision) is not int or source_revision < 1:
            raise NotificationError("source_revision must be a positive integer")
        if occurred_at is not None and (
            type(occurred_at) not in (int, float) or not math.isfinite(occurred_at)
            or occurred_at > self.clock() + 300
        ):
            raise NotificationError("invalid source occurrence time")
        principal = recipient_ref or binding.principal_ref
        if principal not in {binding.principal_ref, *binding.admitted_recipients}:
            raise PermissionError("notification recipient was not admitted by Core")
        if connection is None:
            with self.store.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                result = self.emit(binding, category_id=category_id, event_id=event_id, source_id=source_id,
                    source_revision=source_revision, payload=payload, recipient_ref=principal,
                    urgency=urgency, locale=locale, occurred_at=occurred_at, connection=con)
                con.commit()
                return result
        con = connection
        key, catalog, category = self._category(con, binding, category_id)
        if category["recipient_policy"] == "current_user" and principal != binding.principal_ref:
            raise PermissionError("category only admits its verified initiating user")
        if urgency not in ("normal", "important", "urgent") or (
            ("normal", "important", "urgent").index(urgency) >
            ("normal", "important", "urgent").index(category["maximum_urgency"])
        ):
            raise NotificationError("notification urgency exceeds declaration")
        text = render(category, payload, locale)
        intent_id = "notification-intent:" + digest([binding.instance_id, binding.namespace, principal, category_id, event_id])
        request_digest = digest([source_id, source_revision, payload, urgency, binding.author_ref, occurred_at])
        previous = con.execute("SELECT request_digest,receipt_json FROM notification_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if previous:
            if previous[0] != request_digest:
                raise NotificationConflict("notification event idempotency conflict")
            return json.loads(previous[1])
        replace = category["grouping"]["replacement"] == "same_source_object"
        notification_id = "notification:" + digest([
            binding.instance_id, binding.namespace, principal, category_id,
            source_id if replace else event_id])
        row = con.execute("SELECT * FROM notification_records WHERE notification_id=?", (notification_id,)).fetchone()
        old = self.store.record(row)
        now = self.clock()
        if old and source_revision <= old["source_revision"]:
            receipt = {"schema": "adaos.notifications.receipt.v1", "intent_id": intent_id,
                       "notification_id": notification_id, "content_revision": old["content_revision"], "status": "stale"}
        else:
            if old and old["source_state"] == "withdrawn":
                raise NotificationConflict("withdrawn source cannot be resurrected by a late event")
            revision = (old["content_revision"] if old else 0) + 1
            record = {
                "schema": "adaos.notifications.record.v1", "notification_id": notification_id,
                "principal_ref": principal, "subnet_ref": binding.subnet_ref,
                "application_id": binding.application_id, "instance_id": binding.instance_id,
                "namespace": binding.namespace, "category_id": category_id, "source_id": source_id,
                "source_ref": f"{binding.instance_id}/{binding.namespace}/{source_id}",
                "source_revision": source_revision, "source_state": "active",
                "content_revision": revision, "recipient_revision": old["recipient_revision"] if old else 0,
                "read_revision": old["read_revision"] if old else 0, "archived": old["archived"] if old else False,
                "snoozed_until": old["snoozed_until"] if old else None,
                "occurred_at": now if occurred_at is None else occurred_at,
                "expires_at": (now if occurred_at is None else occurred_at) + category["lifetime_seconds"], "updated_at": now,
                "author_ref": binding.author_ref, "author_label": binding.author_label,
                "release_digest": binding.release_digest, "catalog_key": key, "catalog_digest": catalog["digest"],
                "sensitivity": category["sensitivity"], "urgency": urgency, "text": text,
                "actions": category["actions"], "test_only": binding.test_only,
                "producer_binding": asdict(binding), "admitted_endpoint_ids": [], "admission_muted": False,
            }
            if old and not category["material_updates"]:
                # Progress advances its content while keeping a read/archived choice.
                if old["read_revision"] >= old["content_revision"]:
                    record["read_revision"] = revision
                record["suppress_if_read"] = True
            elif old:
                record["archived"] = False
            if row is None:
                con.execute("""INSERT INTO notification_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (notification_id, principal, binding.subnet_ref, binding.application_id, binding.instance_id,
                     binding.namespace, category_id, source_id, source_revision, revision, record["recipient_revision"],
                     record["read_revision"], int(record["archived"]), "active", record["expires_at"],
                     record["snoozed_until"], now, dump(record)))
            self._plan(con, record, admission=True)
            if record["admission_muted"]:
                record["text"], record["actions"] = "", []
                self._save(con, record)
            receipt = {"schema": "adaos.notifications.receipt.v1", "intent_id": intent_id,
                       "notification_id": notification_id, "content_revision": revision,
                       "status": "suppressed" if record["admission_muted"] else "committed"}
        con.execute("INSERT INTO notification_intents VALUES(?,?,?,?,?,?)",
                    (intent_id, notification_id, principal, request_digest, dump(receipt), now))
        return receipt

    def get(self, principal_ref: str, notification_id: str, *, connection=None) -> dict | None:
        if connection is None:
            with self.store.connect() as con:
                return self.get(principal_ref, notification_id, connection=con)
        row = connection.execute("SELECT * FROM notification_records WHERE notification_id=? AND principal_ref=?",
                                 (notification_id, principal_ref)).fetchone()
        record = self.store.record(row)
        if record is not None:
            record["unread"] = record["read_revision"] < record["content_revision"]
            if record["source_state"] == "active" and record["expires_at"] <= self.clock():
                record["source_state"] = "expired"
        return record

    @staticmethod
    def public_record(record: dict) -> dict:
        return {key: value for key, value in record.items()
                if key not in {"producer_binding", "catalog_key", "admitted_endpoint_ids", "admission_muted",
                               "cancelled_endpoint_ids", "escalation_due", "suppress_if_read"}}

    def list(self, principal_ref: str, *, limit: int = 50, cursor: str | None = None, view: str = "all") -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise NotificationError("notification page limit must be 1..100")
        filters = {"all": "", "unread": " AND read_revision<content_revision AND archived=0",
                   "archived": " AND archived=1", "snoozed": " AND snoozed_until>?",
                   "needs_action": " AND source_state='active' AND json_array_length(json_extract(record_json,'$.actions'))>0"}
        if view not in filters:
            raise NotificationError("unknown notification view")
        clauses = "principal_ref=? AND json_extract(record_json,'$.decision.visible')=1" + filters[view]
        args = [principal_ref]
        if view == "snoozed":
            args.append(self.clock())
        if cursor:
            try:
                if len(cursor) > 1024:
                    raise ValueError()
                date, key = json.loads(base64.urlsafe_b64decode(cursor))
                if not isinstance(date, (int, float)) or not isinstance(key, str):
                    raise ValueError()
                clauses += " AND (updated_at<? OR (updated_at=? AND notification_id<?))"
                args += [date, date, key]
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise NotificationError("invalid notification cursor") from exc
        with self.store.connect() as con:
            rows = con.execute(f"SELECT * FROM notification_records WHERE {clauses} ORDER BY updated_at DESC,notification_id DESC LIMIT ?",
                               (*args, limit + 1)).fetchall()
        items = [self.public_record(self.store.record(row)) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(dump([last["updated_at"], last["notification_id"]]).encode()).decode()
        return {"items": items, "next_cursor": next_cursor}

    def recipient_command(
        self, principal_ref: str, notification_id: str, *, operation_key: str,
        expected_revision: int, action: str, value: float | int | bool | None = None,
    ) -> dict:
        token(operation_key, "operation_key")
        key = digest([principal_ref, operation_key])
        request = digest([notification_id, expected_revision, action, value])
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            prior = con.execute("SELECT request_digest,receipt_json FROM notification_user_operations WHERE operation_key=?",
                                (key,)).fetchone()
            if prior:
                if prior[0] != request:
                    raise NotificationConflict("recipient operation key conflict")
                return json.loads(prior[1])
            record = self.get(principal_ref, notification_id, connection=con)
            if record is None:
                raise PermissionError("notification is not owned by the verified user")
            if type(expected_revision) is not int or expected_revision != record["recipient_revision"]:
                raise NotificationConflict(f"recipient revision conflict: observed {record['recipient_revision']}")
            if action == "read":
                if type(value) is not int or not 0 < value <= record["content_revision"]:
                    raise NotificationError("read must name an existing content revision")
                record["read_revision"] = max(record["read_revision"], value)
            elif action == "archive" and type(value) is bool:
                record["archived"] = value
            elif action == "snooze":
                if value is not None and (type(value) not in (int, float) or not self.clock() < value < record["expires_at"]):
                    raise NotificationError("snooze must precede source expiry")
                record["snoozed_until"] = value
            else:
                raise NotificationError("unsupported recipient command")
            record["recipient_revision"] += 1
            record["updated_at"] = self.clock()
            self._plan(con, record)
            receipt = self.public_record(record)
            con.execute("INSERT INTO notification_user_operations VALUES(?,?,?,?,?)",
                        (key, principal_ref, request, dump(receipt), self.clock()))
            con.commit()
            return receipt

    def withdraw(self, binding: ProducerBinding, source_id: str) -> int:
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            cursor = con.execute("""UPDATE notification_records SET source_state='withdrawn',updated_at=?
                WHERE instance_id=? AND namespace=? AND source_id=? AND source_state='active'""",
                (self.clock(), binding.instance_id, binding.namespace, source_id))
            con.execute("""UPDATE notification_schedule SET status='terminal',due_at=NULL WHERE notification_id IN
                (SELECT notification_id FROM notification_records WHERE instance_id=? AND namespace=? AND source_id=?)""",
                (binding.instance_id, binding.namespace, source_id))
            con.commit()
            return cursor.rowcount

    def schedule_due(self, limit: int = 50) -> int:
        if not 1 <= limit <= 100:
            raise NotificationError("scheduler limit must be 1..100")
        with self.store.connect() as con:
            rows = con.execute("""SELECT notification_id,principal_ref FROM notification_schedule
                WHERE status='pending' AND due_at<=? ORDER BY due_at,notification_id LIMIT ?""",
                (self.clock(), limit)).fetchall()
        for row in rows:
            with self.store.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                record = self.get(row["principal_ref"], row["notification_id"], connection=con)
                if record:
                    self._plan(con, record)
                con.commit()
        return len(rows)

    def recheck_preferences(self, limit: int = 50) -> int:
        if not 1 <= limit <= 100:
            raise NotificationError("recheck limit must be 1..100")
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            change = con.execute("SELECT * FROM notification_policy_rechecks ORDER BY principal_ref,subnet_ref LIMIT 1").fetchone()
            if not change:
                return 0
            rows = con.execute("""SELECT * FROM notification_records WHERE principal_ref=? AND subnet_ref=?
                AND notification_id>? ORDER BY notification_id LIMIT ?""",
                (change["principal_ref"], change["subnet_ref"], change["after_id"], limit)).fetchall()
            for row in rows:
                record = self.store.record(row)
                self._plan(con, record)
                con.execute("""UPDATE conversation_output_intents SET next_attempt_at=?
                    WHERE status='retry_wait' AND intent_id IN
                    (SELECT intent_id FROM notification_outputs WHERE notification_id=?)""",
                    (self.clock(), record["notification_id"]))
            if len(rows) < limit:
                con.execute("DELETE FROM notification_policy_rechecks WHERE principal_ref=? AND subnet_ref=?",
                            (change["principal_ref"], change["subnet_ref"]))
            else:
                con.execute("UPDATE notification_policy_rechecks SET after_id=? WHERE principal_ref=? AND subnet_ref=?",
                            (rows[-1]["notification_id"], change["principal_ref"], change["subnet_ref"]))
            con.commit()
        return len(rows)

    def expire_due(self, limit: int = 50) -> int:
        if not 1 <= limit <= 100:
            raise NotificationError("expiry limit must be 1..100")
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute("""SELECT * FROM notification_records WHERE source_state='active' AND expires_at<=?
                ORDER BY expires_at LIMIT ?""", (self.clock(), limit)).fetchall()
            for row in rows:
                record = self.store.record(row)
                record["source_state"] = "expired"
                self._plan(con, record)
                con.execute("""UPDATE conversation_output_intents SET status='suppressed',last_error='expired'
                    WHERE status IN ('pending','retry_wait','awaiting_endpoint') AND intent_id IN
                    (SELECT intent_id FROM notification_outputs WHERE notification_id=?)""", (record["notification_id"],))
            con.commit()
        return len(rows)

    def forget_user(self, principal_ref: str) -> dict:
        """Erase private snapshots; retain only opaque deduplication tombstones."""
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("""UPDATE conversation_output_intents SET status='suppressed',last_error='recipient_deleted'
                WHERE status IN ('pending','retry_wait','leased','awaiting_endpoint') AND intent_id IN
                (SELECT o.intent_id FROM notification_outputs o JOIN notification_records r
                 ON r.notification_id=o.notification_id WHERE r.principal_ref=?)""", (principal_ref,))
            con.execute("""UPDATE conversation_response_outbox
                SET payload_json=json_set(payload_json,'$.payload.text','','$.payload.data',json('{}'))
                WHERE envelope_id IN (SELECT o.intent_id FROM notification_outputs o JOIN notification_records r
                ON r.notification_id=o.notification_id WHERE r.principal_ref=?)""", (principal_ref,))
            # Receipt keys/digests remain to prevent restore or source retry from replaying content.
            rows = con.execute("SELECT intent_id FROM notification_intents WHERE principal_ref=?", (principal_ref,)).fetchall()
            for row in rows:
                receipt = {"schema": "adaos.notifications.receipt.v1", "intent_id": row[0],
                           "notification_id": None, "content_revision": 0, "status": "suppressed"}
                con.execute("UPDATE notification_intents SET principal_ref=?,receipt_json=? WHERE intent_id=?",
                            ("deleted:" + digest(principal_ref), dump(receipt), row[0]))
            con.execute("DELETE FROM notification_schedule WHERE principal_ref=?", (principal_ref,))
            con.execute("DELETE FROM notification_digest_members WHERE window_id IN (SELECT window_id FROM notification_digest_windows WHERE principal_ref=?)",
                        (principal_ref,))
            con.execute("DELETE FROM notification_digest_windows WHERE principal_ref=?", (principal_ref,))
            con.execute("DELETE FROM notification_user_operations WHERE principal_ref=?", (principal_ref,))
            con.execute("DELETE FROM notification_policy_rechecks WHERE principal_ref=?", (principal_ref,))
            con.execute("UPDATE notification_endpoints SET status='revoked',revision=revision+1 WHERE principal_ref=?", (principal_ref,))
            count = con.execute("DELETE FROM notification_records WHERE principal_ref=?", (principal_ref,)).rowcount
            con.commit()
        return {"deleted_records": count, "deduplication": "opaque_tombstones_retained"}
