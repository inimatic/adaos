"""Identity-owned notification preferences in the existing private KV store."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from adaos.domain.notifications import NotificationConflict, digest, dump, validate
from adaos.services.attention_schedule import validate_schedule


def default_preferences() -> dict[str, Any]:
    return {"schema": "adaos.notifications.preferences.v1", "revision": 0,
            "defaults": {"mode": "center_only", "endpoint_ids": []}, "rules": [],
            "quiet_hours": {"enabled": False, "start": "22:00", "end": "08:00", "timezone": "UTC"},
            "budgets": {"per_hour": 30, "producer_per_minute": 30, "digest_seconds": 3600}}


class AttentionPreferences:
    """Core port; callers authorize principal access before entering this store.

    The reserved per-subnet typed preference is separate from the compatibility
    preferences.v0 aggregate, so legacy profile patches cannot reset a mute.
    """
    def __init__(self, ctx):
        self.ctx = ctx

    def _key(self, principal: str, subnet_ref: str) -> tuple[str, str]:
        return getattr(self.ctx.kv, "ns", "kv"), f"users/{principal}/preferences/notifications.v1/{digest(subnet_ref)}"

    def read(self, principal: str, subnet_ref: str, *, connection=None) -> dict[str, Any]:
        if connection is None:
            with self.ctx.sql.connect() as con:
                return self.read(principal, subnet_ref, connection=con)
        row = connection.execute("SELECT v FROM kv WHERE ns=? AND k=?", self._key(principal, subnet_ref)).fetchone()
        return validate("preferences", json.loads(row[0])) if row else default_preferences()

    def write(self, principal: str, subnet_ref: str, value: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
        result = validate("preferences", value)
        validate_schedule(result["quiet_hours"])
        scopes = [digest(item["scope"]) for item in result["rules"]]
        if len(scopes) != len(set(scopes)):
            raise ValueError("duplicate notification preference scope")
        with self.ctx.sql.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = self.read(principal, subnet_ref, connection=con)
            if type(expected_revision) is not int or current["revision"] != expected_revision:
                raise NotificationConflict(f"preference revision conflict: observed {current['revision']}")
            result["revision"] = expected_revision + 1
            ns, key = self._key(principal, subnet_ref)
            con.execute("INSERT INTO kv(ns,k,v) VALUES(?,?,?) ON CONFLICT(ns,k) DO UPDATE SET v=excluded.v",
                        (ns, key, dump(result)))
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='notification_policy_rechecks'").fetchone():
                con.execute("""INSERT INTO notification_policy_rechecks VALUES(?,?,?,'')
                    ON CONFLICT(principal_ref,subnet_ref) DO UPDATE SET revision=excluded.revision,after_id=''""",
                    (principal, subnet_ref, result["revision"]))
            con.commit()
        return result
