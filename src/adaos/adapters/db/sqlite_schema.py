# src/adaos/adapters/db/sqlite_schema.py
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import threading
import time

_log = logging.getLogger("adaos.sqlite.schema")

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS skills (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE,
        active_version TEXT,
        repo_url TEXT,
        installed BOOLEAN DEFAULT 1,
        last_updated TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS skill_versions (
        id INTEGER PRIMARY KEY,
        skill_name TEXT,
        version TEXT,
        path TEXT,
        status TEXT,
        created_at TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS scenarios (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE,
        active_version TEXT,
        repo_url TEXT,
        installed BOOLEAN DEFAULT 1,
        last_updated TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS scenario_versions (
        id INTEGER PRIMARY KEY,
        scenario_name TEXT,
        version TEXT,
        path TEXT,
        status TEXT,
        created_at TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS subnets (
        subnet_id TEXT PRIMARY KEY,
        owner_id TEXT,
        created_at INT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY,
        subnet_id TEXT NOT NULL,
        role TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        cert_pem TEXT NOT NULL,
        issued_at INT NOT NULL,
        expires_at INT NOT NULL,
        UNIQUE(subnet_id, fingerprint)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_cache (
        key TEXT,
        method TEXT,
        path TEXT,
        principal_id TEXT,
        body_hash TEXT,
        status_code INT,
        body_json TEXT,
        event_id TEXT,
        server_time_utc TEXT,
        created_at INT,
        expires_at INT,
        PRIMARY KEY(key, method, path, principal_id, body_hash)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS ca_state (
        id INTEGER PRIMARY KEY CHECK(id=1),
        ca_key_pem TEXT NOT NULL,
        ca_cert_pem TEXT NOT NULL,
        next_serial INTEGER NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_devices_fpr ON devices(fingerprint);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_idem_exp ON idempotency_cache(expires_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS pair_codes (
        code TEXT PRIMARY KEY,
        bot_id TEXT,
        hub_id TEXT,
        webspace_id TEXT,
        expires_at INT,
        state TEXT,
        created_at INT,
        note TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_bindings (
        platform TEXT,
        user_id TEXT,
        bot_id TEXT,
        ada_user_id TEXT,
        hub_id TEXT,
        webspace_id TEXT,
        created_at INT,
        last_seen INT,
        PRIMARY KEY(platform, user_id, bot_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS durable_state (
        namespace TEXT NOT NULL,
        key TEXT NOT NULL,
        value_json TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(namespace, key)
    );
    """,
)

_SCHEMA_REVISION = hashlib.sha256("\n".join(_SCHEMA).encode("utf-8")).hexdigest()[:16]
_SCHEMA_LOCK = threading.RLock()
_ENSURED_SCHEMA_REVISIONS: set[tuple[str, str]] = set()


def _schema_identity(sql) -> str:
    raw_path = getattr(sql, "_db_path", None)
    if raw_path is not None:
        try:
            return str(Path(raw_path).resolve())
        except Exception:
            return str(raw_path)
    return f"{type(sql).__module__}.{type(sql).__qualname__}:{id(sql)}"


def _schema_warn_ms() -> float:
    try:
        return max(0.0, float(os.getenv("ADAOS_SQLITE_SCHEMA_WARN_MS", "250") or "250"))
    except (TypeError, ValueError):
        return 250.0


def ensure_schema(sql) -> None:
    identity = _schema_identity(sql)
    cache_key = (identity, _SCHEMA_REVISION)
    wait_started = time.perf_counter()
    with _SCHEMA_LOCK:
        wait_ms = (time.perf_counter() - wait_started) * 1000.0
        if cache_key in _ENSURED_SCHEMA_REVISIONS:
            return
        apply_started = time.perf_counter()
        with sql.connect() as con:
            cur = con.cursor()
            for stmt in _SCHEMA:
                cur.execute(stmt)
            for table in ("pair_codes", "chat_bindings"):
                columns = {str(row[1]) for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
                if "webspace_id" not in columns:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN webspace_id TEXT")
            con.commit()
        apply_ms = (time.perf_counter() - apply_started) * 1000.0
        _ENSURED_SCHEMA_REVISIONS.add(cache_key)

    total_ms = wait_ms + apply_ms
    if total_ms >= _schema_warn_ms():
        _log.warning(
            "SQLite core schema ensure slow identity=%s revision=%s wait_ms=%.3f apply_ms=%.3f total_ms=%.3f",
            identity,
            _SCHEMA_REVISION,
            wait_ms,
            apply_ms,
            total_ms,
        )
