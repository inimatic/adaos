"""Local immutable records, append-only events and exact Ticket backlinks.

SQLite commits the record and its event together. Reading an absent database
does not initialize it; Application first paint never triggers scanning.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping

from .records import IDENTITIES, digest, utc_now, validate


class EvolutionStore:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "contract_evolution" / "records.sqlite3"

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection | None]:
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path) if write else self.path.resolve().as_uri() + "?mode=ro",
                             uri=not write, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS records (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT UNIQUE NOT NULL, schema TEXT NOT NULL,
                        application_ref TEXT, consumer_digest TEXT, change_id TEXT,
                        body TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS records_consumer ON records(application_ref, consumer_digest, schema);
                    CREATE INDEX IF NOT EXISTS records_change ON records(change_id, schema);
                    CREATE TABLE IF NOT EXISTS links (
                        assessment_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL,
                        body TEXT NOT NULL);
                """)
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db: sqlite3.Connection, kind: str, subject: str, refs: Mapping[str, Any]):
        key = digest([kind, subject, dict(refs)])
        event = {"schema": "adaos.contract_evolution.event.v1", "event_id": key,
                 "type": kind, "subject_ref": subject, "refs": dict(refs), "recorded_at": utc_now()}
        db.execute("INSERT OR IGNORE INTO events(key,body) VALUES (?,?)", (key, json.dumps(event)))

    def put(self, record: Mapping[str, Any], event_type: str) -> dict[str, Any]:
        value = validate(record)
        identity = value[IDENTITIES[value["schema"]]]
        with self.connection(write=True) as db:
            previous = db.execute("SELECT body FROM records WHERE id=?", (identity,)).fetchone()
            if previous:
                return validate(json.loads(previous[0]))
            db.execute("INSERT INTO records(id,schema,application_ref,consumer_digest,change_id,body) VALUES (?,?,?,?,?,?)",
                       (identity, value["schema"], value.get("application_ref"), value.get("consumer_digest"),
                        value.get("change_id"), json.dumps(value, ensure_ascii=False)))
            self._event(db, event_type, identity, {k: value[k] for k in (
                "application_ref", "consumer_digest", "contract_ref", "change_id", "wave_id",
                "target_contract_digest", "environment_ref", "policy_digest") if k in value})
        return value

    def get(self, identity: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT body FROM records WHERE id=?", (identity,)).fetchone() if db else None
        if row is None:
            raise FileNotFoundError(f"Contract Evolution record not found: {identity}")
        return validate(json.loads(row[0]))

    def list(self, schema: str, *, application_ref: str | None = None,
             consumer_digest: str | None = None, change_id: str | None = None,
             limit: int = 1000) -> list[dict[str, Any]]:
        if schema not in IDENTITIES or not 1 <= limit <= 10000:
            raise ValueError("invalid Contract Evolution query")
        clauses, params = ["schema=?"], [schema]
        for key, value in (("application_ref", application_ref), ("consumer_digest", consumer_digest), ("change_id", change_id)):
            if value is not None:
                clauses.append(f"{key}=?")
                params.append(value)
        with self.connection() as db:
            rows = db.execute("SELECT body FROM records WHERE " + " AND ".join(clauses) + " ORDER BY seq DESC LIMIT ?",
                              [*params, limit]).fetchall() if db else []
        return [validate(json.loads(row[0])) for row in rows]

    def ticket_links(self) -> dict[str, str]:
        with self.connection() as db:
            return dict(db.execute("SELECT assessment_id,ticket_id FROM links").fetchall()) if db else {}

    def link_ticket(self, assessment_id: str, ticket_id: str):
        self.get(assessment_id)
        with self.connection(write=True) as db:
            row = db.execute("SELECT ticket_id FROM links WHERE assessment_id=?", (assessment_id,)).fetchone()
            if row and row[0] != ticket_id:
                raise ValueError("assessment is already linked to another Ticket")
            db.execute("INSERT OR IGNORE INTO links VALUES (?,?)", (assessment_id, ticket_id))
            self._event(db, "compatibility_ticket.linked", assessment_id, {"ticket_id": ticket_id})

    def events(self, *, after: int = 0, limit: int = 100) -> dict[str, Any]:
        if after < 0 or not 1 <= limit <= 1000:
            raise ValueError("invalid Contract Evolution event cursor/limit")
        with self.connection() as db:
            rows = db.execute("SELECT seq,body FROM events WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)).fetchall() if db else []
        return {"items": [{**json.loads(row[1]), "sequence": row[0]} for row in rows],
                "next_after": rows[-1][0] if rows else after}
