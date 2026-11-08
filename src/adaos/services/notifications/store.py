"""Private recipient state in Core SQLite; transport state stays with Conversation."""
from __future__ import annotations

import json
import sqlite3

from adaos.services.agent_context import get_ctx, use_ctx

_DDL = """
CREATE TABLE IF NOT EXISTS notification_catalogs(
 catalog_key TEXT PRIMARY KEY, application_id TEXT NOT NULL, instance_id TEXT NOT NULL,
 namespace TEXT NOT NULL, release_digest TEXT NOT NULL, catalog_digest TEXT NOT NULL,
 catalog_json TEXT NOT NULL, binding_json TEXT NOT NULL, admitted_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notification_records(
 notification_id TEXT PRIMARY KEY, principal_ref TEXT NOT NULL, subnet_ref TEXT NOT NULL,
 application_id TEXT NOT NULL, instance_id TEXT NOT NULL, namespace TEXT NOT NULL,
 category_id TEXT NOT NULL, source_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
 content_revision INTEGER NOT NULL, recipient_revision INTEGER NOT NULL DEFAULT 0,
 read_revision INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
 source_state TEXT NOT NULL, expires_at REAL NOT NULL, snoozed_until REAL,
 updated_at REAL NOT NULL, record_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_notification_private_page
 ON notification_records(principal_ref,updated_at DESC,notification_id DESC);
CREATE INDEX IF NOT EXISTS idx_notification_visible_page
 ON notification_records(principal_ref,updated_at DESC,notification_id DESC)
 WHERE json_extract(record_json,'$.decision.visible')=1;
CREATE INDEX IF NOT EXISTS idx_notification_unread_page
 ON notification_records(principal_ref,updated_at DESC,notification_id DESC)
 WHERE json_extract(record_json,'$.decision.visible')=1 AND read_revision<content_revision AND archived=0;
CREATE INDEX IF NOT EXISTS idx_notification_archived_page
 ON notification_records(principal_ref,updated_at DESC,notification_id DESC)
 WHERE json_extract(record_json,'$.decision.visible')=1 AND archived=1;
CREATE INDEX IF NOT EXISTS idx_notification_owned_source
 ON notification_records(instance_id,namespace,source_id,source_state);
CREATE INDEX IF NOT EXISTS idx_notification_expiry
 ON notification_records(source_state,expires_at);
CREATE TABLE IF NOT EXISTS notification_intents(
 intent_id TEXT PRIMARY KEY, notification_id TEXT, principal_ref TEXT NOT NULL,
 request_digest TEXT NOT NULL, receipt_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_notification_intent_retention ON notification_intents(created_at);
CREATE TABLE IF NOT EXISTS notification_schedule(
 notification_id TEXT PRIMARY KEY, principal_ref TEXT NOT NULL,
 status TEXT NOT NULL, due_at REAL);
CREATE INDEX IF NOT EXISTS idx_notification_due ON notification_schedule(status,due_at);
CREATE TABLE IF NOT EXISTS notification_endpoints(
 endpoint_id TEXT PRIMARY KEY, principal_ref TEXT NOT NULL, subnet_ref TEXT NOT NULL,
 transport TEXT NOT NULL, route_id TEXT NOT NULL, revision INTEGER NOT NULL,
 status TEXT NOT NULL, label TEXT NOT NULL, capabilities_json TEXT NOT NULL,
 observed_at REAL NOT NULL, expires_at REAL, binding_digest TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_notification_endpoint_owner
 ON notification_endpoints(principal_ref,subnet_ref,status,endpoint_id);
CREATE TABLE IF NOT EXISTS notification_digest_windows(
 window_id TEXT PRIMARY KEY, principal_ref TEXT NOT NULL, endpoint_id TEXT NOT NULL,
 namespace TEXT NOT NULL, due_at REAL NOT NULL, status TEXT NOT NULL,
 content_json TEXT);
CREATE INDEX IF NOT EXISTS idx_notification_digest_due ON notification_digest_windows(status,due_at);
CREATE TABLE IF NOT EXISTS notification_digest_members(
 window_id TEXT NOT NULL, notification_id TEXT NOT NULL,
 content_revision INTEGER NOT NULL, PRIMARY KEY(window_id,notification_id));
CREATE TABLE IF NOT EXISTS notification_dispatch_reservations(
 intent_id TEXT PRIMARY KEY, principal_ref TEXT NOT NULL, application_id TEXT NOT NULL,
 endpoint_id TEXT NOT NULL, reserved_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_notification_budget_principal
 ON notification_dispatch_reservations(principal_ref,reserved_at);
CREATE INDEX IF NOT EXISTS idx_notification_budget_producer
 ON notification_dispatch_reservations(principal_ref,application_id,reserved_at);
CREATE TABLE IF NOT EXISTS notification_outputs(
 intent_id TEXT PRIMARY KEY, notification_id TEXT NOT NULL, endpoint_id TEXT NOT NULL,
 content_revision INTEGER NOT NULL, policy_revision INTEGER NOT NULL,
 endpoint_revision INTEGER NOT NULL, digest_window_id TEXT);
CREATE INDEX IF NOT EXISTS idx_notification_output_record ON notification_outputs(notification_id,intent_id);
CREATE INDEX IF NOT EXISTS idx_notification_output_endpoint ON notification_outputs(endpoint_id,intent_id);
CREATE TABLE IF NOT EXISTS notification_user_operations(
 operation_key TEXT PRIMARY KEY, principal_ref TEXT NOT NULL, request_digest TEXT NOT NULL,
 receipt_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notification_policy_rechecks(
 principal_ref TEXT NOT NULL, subnet_ref TEXT NOT NULL, revision INTEGER NOT NULL,
 after_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(principal_ref,subnet_ref));
CREATE INDEX IF NOT EXISTS idx_notification_recheck_records
 ON notification_records(principal_ref,subnet_ref,notification_id);
CREATE TABLE IF NOT EXISTS notification_publisher_grants(
 handle_digest TEXT PRIMARY KEY, skill_ref TEXT NOT NULL, manifest_digest TEXT NOT NULL,
 binding_json TEXT NOT NULL, expires_at REAL NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notification_schema_version(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS notification_admission_reservations(
 intent_id TEXT PRIMARY KEY, producer_key TEXT NOT NULL, principal_ref TEXT NOT NULL,
 event_class TEXT NOT NULL, admitted_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_notification_admission_producer
 ON notification_admission_reservations(producer_key,admitted_at);
CREATE INDEX IF NOT EXISTS idx_notification_admission_progress
 ON notification_admission_reservations(producer_key,event_class,admitted_at);
CREATE INDEX IF NOT EXISTS idx_notification_admission_retention ON notification_admission_reservations(admitted_at);
CREATE INDEX IF NOT EXISTS idx_notification_live_recipient ON notification_records(principal_ref,expires_at)
 WHERE source_state='active' AND archived=0;
"""


class NotificationStore:
    def __init__(self, ctx=None):
        self.ctx = ctx or get_ctx()
        seen = set()
        while getattr(self.ctx, "authority_context", None) is not None:
            if id(self.ctx) in seen:
                raise RuntimeError("cyclic notification authority context")
            seen.add(id(self.ctx))
            self.ctx = self.ctx.authority_context
        with self.ctx.sql.connect(read_only=True) as con:
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='notification_schema_version'").fetchone()
            if exists and con.execute("SELECT MAX(version) FROM notification_schema_version").fetchone()[0] >= 7:
                return
        # Existing delivery tables are shared; no second provider queue is created.
        from adaos.services import conversation_publication
        with use_ctx(self.ctx):
            conversation_publication.ensure_schema()
        with self.ctx.sql.connect() as con:
            con.executescript("BEGIN IMMEDIATE;\n" + _DDL)
            if "relay_grant_digest" not in {row[1] for row in con.execute("PRAGMA table_info(notification_outputs)")}:
                con.execute("ALTER TABLE notification_outputs ADD COLUMN relay_grant_digest TEXT")
            con.execute("INSERT OR IGNORE INTO notification_schema_version VALUES(7)")
            con.commit()

    def connect(self, *, read_only=False):
        con = self.ctx.sql.connect(read_only=read_only)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def record(row):
        if row is None:
            return None
        value = json.loads(row["record_json"])
        for key in ("source_revision", "content_revision", "recipient_revision",
                    "read_revision", "source_state", "expires_at", "snoozed_until", "updated_at"):
            value[key] = row[key]
        value["archived"] = bool(row["archived"])
        value["unread"] = row["read_revision"] < row["content_revision"]
        return value
