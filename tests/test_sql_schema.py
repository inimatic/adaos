# tests/test_sql_schema.py
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import threading

from adaos.adapters.db.sqlite_store import SQLite
from adaos.adapters.db import sqlite_schema
from adaos.adapters.db.sqlite_schema import ensure_schema
from adaos.services.agent_context import get_ctx


def test_sqlite_schema_tables_exist():
    sql = get_ctx().sql
    ensure_schema(sql)
    with sql.connect() as con:
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {r[0] for r in cur.fetchall()}
    assert {"skills", "skill_versions", "scenarios", "scenario_versions"} <= names


def test_sqlite_schema_is_applied_once_per_database_revision(tmp_path):
    class _Paths:
        def state_dir(self):
            return tmp_path

    class _CountingSQL:
        def __init__(self):
            self.delegate = SQLite(_Paths())
            self._db_path = self.delegate._db_path
            self.connect_total = 0

        def connect(self):
            self.connect_total += 1
            return self.delegate.connect()

    sql = _CountingSQL()
    ensure_schema(sql)
    ensure_schema(sql)

    assert sql.connect_total == 1


def test_sqlite_schema_serializes_concurrent_first_use(tmp_path):
    class _Paths:
        def state_dir(self):
            return tmp_path

    class _CountingSQL:
        def __init__(self):
            self.delegate = SQLite(_Paths())
            self._db_path = self.delegate._db_path
            self.connect_total = 0
            self.counter_lock = threading.Lock()

        def connect(self):
            with self.counter_lock:
                self.connect_total += 1
            return self.delegate.connect()

    sql = _CountingSQL()
    sqlite_schema._ENSURED_SCHEMA_REVISIONS.discard(
        (sqlite_schema._schema_identity(sql), sqlite_schema._SCHEMA_REVISION)
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: ensure_schema(sql), range(24)))

    assert sql.connect_total == 1
