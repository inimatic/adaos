"""Rebuildable selectors for the immutable, numbered access-audit journal.

JSON events remain authoritative and returned payloads are always re-read.
This is a persistent query index, not a TTL/result/authorization cache. Cold
construction and incremental catch-up are measured explicitly. The index lock
does not block journal appenders; only the initial filename snapshot briefly
uses their mutation lock. A crash before updating sequence.json is recovered
by probing the next numbered event, not by trusting that derived cursor.
"""

import logging
import os
from pathlib import Path
import sqlite3
import time
import uuid

from adaos.services.artifact_pipeline.storage import mutation_lock


_log = logging.getLogger("adaos.applications.audit_index")


class AccessAuditIndexError(RuntimeError):
    pass


class _SourceSnapshotRequired(Exception):
    pass


def _source_snapshot(parent: Path, source_lock: Path) -> list[int]:
    with mutation_lock(source_lock, timeout_s=30):
        with os.scandir(parent) as entries:
            return sorted(int(item.name[:-5]) for item in entries
                          if item.name.endswith(".json") and item.name[:-5].isdigit())


def _row(parent, sequence, read):
    name = f"{sequence:020d}.json"
    event = read(parent / name)
    if event.get("sequence") != sequence or not event.get("application_id") or not event.get("subject_ref"):
        raise AccessAuditIndexError(f"access audit source identity mismatch: {name}")
    return sequence, str(event["application_id"]), str(event["subject_ref"])


def _query(db, parent, source_snapshot, read, application_id, subject_ref, limit):
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY, application_id TEXT NOT NULL, subject_ref TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS by_application ON events(application_id, sequence DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS by_subject ON events(subject_ref, sequence DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS by_application_subject ON events(application_id, subject_ref, sequence DESC)")
        db.execute("CREATE TABLE IF NOT EXISTS cursor (id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL)")
        cursor = db.execute("SELECT sequence FROM cursor WHERE id=1").fetchone()
        scanned = 0
        mode = "indexed"
        started = time.perf_counter()
        if cursor is None:
            if source_snapshot is None:
                raise _SourceSnapshotRequired
            sequences = source_snapshot
            db.executemany("INSERT INTO events VALUES(?,?,?)", (_row(parent, seq, read) for seq in sequences))
            watermark = max(sequences, default=0)
            scanned = len(sequences)
            mode = "cold_build"
        else:
            watermark = cursor[0]
        # Probe actual immutable files. sequence.json is intentionally not an
        # authority: it may be stale, missing or torn after an append crash.
        # Freeze the tail before parsing it so a continuous appender cannot
        # turn catch-up into an unbounded read.
        tail = []
        next_sequence = watermark + 1
        while (parent / f"{next_sequence:020d}.json").is_file():
            tail.append(next_sequence)
            next_sequence += 1
        if tail:
            db.executemany("INSERT INTO events VALUES(?,?,?)", (_row(parent, seq, read) for seq in tail))
            watermark = tail[-1]
            scanned += len(tail)
            if mode != "cold_build":
                mode = "incremental"
        if cursor is None or scanned:
            db.execute("INSERT OR REPLACE INTO cursor VALUES(1,?)", (watermark,))
        source_ms = (time.perf_counter() - started) * 1000
        terms, parameters = [], []
        for key, value in (("application_id", application_id), ("subject_ref", subject_ref)):
            if value is not None:
                terms.append(f"{key}=?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(terms) if terms else ""
        rows = db.execute("SELECT sequence, application_id, subject_ref FROM events" + where +
                          " ORDER BY sequence DESC LIMIT ?", (*parameters, limit)).fetchall()
        db.commit()
        return rows, {"mode": mode, "source_files_scanned": scanned,
                      "watermark": watermark, "source_ms": round(source_ms, 3)}
    except BaseException:
        db.rollback()
        raise


def select_access_audit(root: Path, *, read, application_id=None, subject_ref=None, limit=200):
    parent = root / "application_access_audit"
    if not parent.is_dir():
        return ()
    directory = root / "read_models"
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "access_audit.v1.sqlite3"
    started = time.perf_counter()
    snapshot = None
    snapshot_ms = lock_ms = 0.0
    for snapshot_attempt in range(2):
        need_snapshot = False
        waiting = time.perf_counter()
        with mutation_lock(directory / "access_audit.index.lock", timeout_s=60):
            lock_ms += (time.perf_counter() - waiting) * 1000
            for attempt in range(2):
                db = sqlite3.connect(database, timeout=30)
                try:
                    rows, metrics = _query(db, parent, snapshot, read,
                                           application_id, subject_ref, max(1, min(int(limit), 1000)))
                    break
                except _SourceSnapshotRequired:
                    need_snapshot = True
                    break
                except sqlite3.DatabaseError as exc:
                    # Busy/read-only/disk-full are real operational errors,
                    # not grounds to discard an index or hide faults by scans.
                    if attempt or getattr(exc, "sqlite_errorcode", None) not in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
                        raise AccessAuditIndexError("access audit query index unavailable") from exc
                    db.close()
                    recovery = database.with_name(database.name + ".invalid." + uuid.uuid4().hex)
                    os.replace(database, recovery)
                    _log.warning("access audit index corrupt; preserving derived file and rebuilding source")
                finally:
                    db.close()
        if not need_snapshot:
            break
        # Never acquire the source mutation lock while holding the index lock:
        # a caller may already own the source lock before requesting a read.
        snapshot_started = time.perf_counter()
        snapshot = _source_snapshot(parent, root / ".mutation.lock")
        snapshot_ms += (time.perf_counter() - snapshot_started) * 1000
    else:  # pragma: no cover
        raise AccessAuditIndexError("access audit source snapshot unavailable")
    metrics["snapshot_ms"] = round(snapshot_ms, 3)
    events = []
    payload_started = time.perf_counter()
    for sequence, indexed_application, indexed_subject in rows:
        event = read(parent / f"{sequence:020d}.json")
        if (event.get("sequence"), event.get("application_id"), event.get("subject_ref")) != (sequence, indexed_application, indexed_subject):
            # An immutable source was changed outside its append owner. Do not
            # silently return stale selectors, cached grants, or empty results.
            raise AccessAuditIndexError("access audit source changed after indexing")
        events.append(event)
    elapsed = (time.perf_counter() - started) * 1000
    emit = _log.info if elapsed >= 100 or metrics["mode"] != "indexed" else _log.debug
    emit("access audit read total_ms=%.3f lock_ms=%.3f payload_ms=%.3f returned=%d metrics=%s",
         elapsed, lock_ms, (time.perf_counter() - payload_started) * 1000, len(events), metrics)
    return tuple(events)
