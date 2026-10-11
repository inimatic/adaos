"""Durable, cross-process attempted-request cap for a preregistered paid series.

A slot is consumed before submission, including ambiguous or failed delivery.
Never recycle slots or silently resubmit a retained request. MCP read-only
preflight is not an LLM request. Provider retry caps are verified separately.
"""
from __future__ import annotations
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time


class RequestBudgetExceeded(RuntimeError):
    pass


class RequestBudget:
    def __init__(self, path, *, maximum=32, limits=None):
        self.path = Path(path)
        self.maximum = maximum
        self.limits = limits or {"primary": 20, "repair": 8, "judge": 4}
        if maximum <= 0 or sum(self.limits.values()) > maximum or any(value < 0 for value in self.limits.values()):
            raise ValueError("Invalid request budget")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS configuration (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS attempts (ordinal INTEGER PRIMARY KEY AUTOINCREMENT, request_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, reserved_at REAL NOT NULL, job_id TEXT, status TEXT NOT NULL)")
            config = json.dumps({"maximum": maximum, "limits": self.limits}, sort_keys=True)
            previous = db.execute("SELECT value FROM configuration WHERE id=1").fetchone()
            if previous and limits is None and json.loads(previous[0])["maximum"] == maximum:
                self.limits = json.loads(previous[0])["limits"]
                config = previous[0]
            if previous and previous[0] != config:
                raise ValueError("Request budget configuration drift")
            db.execute("INSERT OR IGNORE INTO configuration VALUES (1, ?)", (config,))

    def reallocate(self, limits, *, reason):
        """Journal a fixed-total allocation amendment; never reset attempted rows."""
        if not reason or sum(limits.values()) > self.maximum or set(limits) != set(self.limits) or any(v < 0 for v in limits.values()):
            raise ValueError("Invalid fixed-total allocation amendment")
        with self.transaction() as db:
            for kind, limit in limits.items():
                if db.execute("SELECT COUNT(*) FROM attempts WHERE kind=?", (kind,)).fetchone()[0] > limit:
                    raise ValueError("Cannot censor or recycle existing attempts")
            db.execute("CREATE TABLE IF NOT EXISTS amendments (at REAL, before TEXT, after TEXT, reason TEXT)")
            before = db.execute("SELECT value FROM configuration WHERE id=1").fetchone()[0]
            after = json.dumps({"maximum":self.maximum,"limits":limits},sort_keys=True)
            db.execute("INSERT INTO amendments VALUES (?,?,?,?)",(time.time(),before,after,reason))
            db.execute("UPDATE configuration SET value=? WHERE id=1",(after,))
        self.limits = dict(limits)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def reserve(self, kind: str, request_key: str) -> int:
        if kind not in self.limits or not isinstance(request_key, str) or not request_key or len(request_key) > 500:
            raise ValueError("Unknown request kind or invalid durable key")
        with self.transaction() as db:
            if db.execute("SELECT ordinal FROM attempts WHERE request_key=?", (request_key,)).fetchone():
                raise RequestBudgetExceeded("Retained request already attempted; recover it instead of resubmitting")
            total = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            count = db.execute("SELECT COUNT(*) FROM attempts WHERE kind=?", (kind,)).fetchone()[0]
            if total >= self.maximum or count >= self.limits[kind]:
                raise RequestBudgetExceeded(f"Preregistered {kind} budget exhausted; refusing a paid request")
            cursor = db.execute("INSERT INTO attempts(request_key,kind,reserved_at,status) VALUES (?,?,?,?)",
                                (request_key, kind, time.time(), "attempted"))
            return cursor.lastrowid

    def observe(self, ordinal: int, *, job_id: str | None = None, status: str):
        with self.transaction() as db:
            cursor = db.execute("UPDATE attempts SET job_id=COALESCE(?,job_id), status=? WHERE ordinal=?", (job_id, status, ordinal))
            if cursor.rowcount != 1:
                raise ValueError("Unknown reserved request")

    def snapshot(self):
        with self.transaction() as db:
            db.row_factory = sqlite3.Row
            attempts = [dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY ordinal")]
        return {"maximum": self.maximum, "limits": self.limits, "attempted_requests": len(attempts),
            "counts": {kind: sum(item["kind"] == kind for item in attempts) for kind in self.limits}, "attempts": attempts}
