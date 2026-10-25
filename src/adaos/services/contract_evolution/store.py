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


class EvolutionQueryLimitError(ValueError):
    """A bounded query is incomplete; never infer compatibility from its prefix."""


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
                    CREATE TABLE IF NOT EXISTS migrations (
                        usage_lock_id TEXT NOT NULL, change_id TEXT NOT NULL, receipt_id TEXT NOT NULL,
                        PRIMARY KEY(usage_lock_id,change_id,receipt_id));
                    CREATE TABLE IF NOT EXISTS assessment_heads (
                        application_ref TEXT, consumer_digest TEXT, change_id TEXT, environment_ref TEXT,
                        assessment_id TEXT NOT NULL, generation INTEGER NOT NULL,
                        PRIMARY KEY(application_ref,consumer_digest,change_id,environment_ref));
                    CREATE TABLE IF NOT EXISTS events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL,
                        body TEXT NOT NULL);
                """)
                db.execute("BEGIN IMMEDIATE")
                # Upgrade an earlier MVP store without losing its existing
                # assessments when the new head table is first initialized.
                # Existing selections (including reselected older records)
                # always win over insertion order.
                db.execute("""
                    INSERT OR IGNORE INTO assessment_heads
                    SELECT application_ref,consumer_digest,change_id,
                           json_extract(body,'$.environment_ref'),id,1
                    FROM records WHERE seq IN (
                        SELECT MAX(seq) FROM records
                        WHERE schema='adaos.consumer_contract_assessment.v1'
                        GROUP BY application_ref,consumer_digest,change_id,
                                 json_extract(body,'$.environment_ref'))
                """)
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
                self._select_assessment(db, value)
                return validate(json.loads(previous[0]))
            db.execute("INSERT INTO records(id,schema,application_ref,consumer_digest,change_id,body) VALUES (?,?,?,?,?,?)",
                       (identity, value["schema"], value.get("application_ref"), value.get("consumer_digest"),
                        value.get("change_id"), json.dumps(value, ensure_ascii=False)))
            self._event(db, event_type, identity, {k: value[k] for k in (
                "application_ref", "consumer_digest", "contract_ref", "change_id", "wave_id",
                "target_contract_digest", "environment_ref", "policy_digest") if k in value})
            self._select_assessment(db, value)
        return value

    def _select_assessment(self, db: sqlite3.Connection, value: Mapping[str, Any]):
        if value["schema"] != "adaos.consumer_contract_assessment.v1":
            return
        key = tuple(value[name] for name in ("application_ref", "consumer_digest", "change_id", "environment_ref"))
        previous = db.execute("SELECT assessment_id,generation FROM assessment_heads WHERE application_ref=? AND consumer_digest=? AND change_id=? AND environment_ref=?", key).fetchone()
        if previous and previous[0] == value["assessment_id"]:
            return
        generation = previous[1] + 1 if previous else 1
        db.execute("INSERT OR REPLACE INTO assessment_heads VALUES (?,?,?,?,?,?)", (*key, value["assessment_id"], generation))
        self._event(db, "consumer_assessment.selected", value["assessment_id"], {
            "application_ref": key[0], "consumer_digest": key[1], "change_id": key[2],
            "environment_ref": key[3], "generation": generation, "replaces": previous[0] if previous else None})

    def get(self, identity: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT body FROM records WHERE id=?", (identity,)).fetchone() if db else None
        if row is None:
            raise FileNotFoundError(f"Contract Evolution record not found: {identity}")
        return validate(json.loads(row[0]))

    def list(self, schema: str, *, application_ref: str | None = None,
             consumer_digest: str | None = None, change_id: str | None = None,
             limit: int = 1000, current: bool = False) -> list[dict[str, Any]]:
        if schema not in IDENTITIES or not 1 <= limit <= 10000:
            raise ValueError("invalid Contract Evolution query")
        clauses, params = ["schema=?"], [schema]
        for key, value in (("application_ref", application_ref), ("consumer_digest", consumer_digest), ("change_id", change_id)):
            if value is not None:
                clauses.append(f"{key}=?")
                params.append(value)
        with self.connection() as db:
            if current and db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assessment_heads'").fetchone():
                    clauses.append("id IN (SELECT assessment_id FROM assessment_heads)")
                else:
                    # Read an earlier MVP store without mutating it. The next
                    # explicit assessment write initializes its head index.
                    clauses.append("seq IN (SELECT MAX(seq) FROM records WHERE schema='adaos.consumer_contract_assessment.v1' GROUP BY application_ref,consumer_digest,change_id,json_extract(body,'$.environment_ref'))")
            rows = db.execute("SELECT body FROM records WHERE " + " AND ".join(clauses) + " ORDER BY seq DESC LIMIT ?",
                              [*params, limit + 1]).fetchall() if db else []
        if len(rows) > limit:
            raise EvolutionQueryLimitError(f"Contract Evolution {schema} query exceeds its {limit}-record limit; assessment coverage is incomplete")
        return [validate(json.loads(row[0])) for row in rows]

    def append_event(self, kind: str, subject: str, refs: Mapping[str, Any]):
        with self.connection(write=True) as db:
            self._event(db, kind, subject, refs)

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

    def link_migration(self, usage_lock_id: str, change_id: str, receipt_id: str):
        with self.connection(write=True) as db:
            db.execute("INSERT OR IGNORE INTO migrations VALUES (?,?,?)", (usage_lock_id, change_id, receipt_id))
            self._event(db, "contract_migration.applied", receipt_id, {"usage_lock_id": usage_lock_id, "change_id": change_id})

    def migration_receipts(self, usage_lock_id: str, change_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            if db and not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='migrations'").fetchone():
                return []
            rows = db.execute("SELECT receipt_id FROM migrations WHERE usage_lock_id=? AND change_id=?",
                              (usage_lock_id, change_id)).fetchall() if db else []
        return [self.get(row[0]) for row in rows]
