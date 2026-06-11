# src\adaos\adapters\db\sqlite_store.py
# соединение SQLite (SQLite) + простое KV (SQLiteKV)
from __future__ import annotations
import os
import sqlite3, json
from pathlib import Path
from typing import Any, Optional, Final
from adaos.ports import KV, SQL
from adaos.ports.paths import PathProvider

_DB_FILE = "adaos.db"


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, tb))
        finally:
            self.close()


def _sqlite_timeout_s() -> float:
    try:
        timeout_s = float(os.getenv("ADAOS_SQLITE_TIMEOUT_S", "5.0") or "5.0")
    except Exception:
        timeout_s = 5.0
    if timeout_s < 0.1:
        timeout_s = 0.1
    return timeout_s


def _configure_connection(con: sqlite3.Connection, *, foreign_keys: bool) -> None:
    timeout_ms = int(_sqlite_timeout_s() * 1000)
    try:
        con.execute(f"PRAGMA busy_timeout={timeout_ms}")
    except Exception:
        pass
    if foreign_keys:
        con.execute("PRAGMA foreign_keys=ON")


class SQLite(SQL):
    def __init__(self, paths: PathProvider):
        self._db_path: Final[Path] = Path(paths.state_dir()) / _DB_FILE
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # ленивое создание файла
        with sqlite3.connect(self._db_path, timeout=_sqlite_timeout_s(), factory=_ClosingConnection) as con:
            _configure_connection(con, foreign_keys=False)
            try:
                con.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path, timeout=_sqlite_timeout_s(), factory=_ClosingConnection)
        _configure_connection(con, foreign_keys=True)
        return con


class SQLiteKV(KV):
    def __init__(self, sql: SQLite, namespace: str = "kv"):
        self.sql = sql
        self.ns = namespace
        self._ensure()

    def _ensure(self) -> None:
        with self.sql.connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS kv (
                    ns TEXT NOT NULL,
                    k  TEXT NOT NULL,
                    v  BLOB,
                    PRIMARY KEY (ns, k)
                )
            """
            )

    def get(self, key: str, default: Any = None) -> Any:
        with self.sql.connect() as con:
            cur = con.execute("SELECT v FROM kv WHERE ns=? AND k=?", (self.ns, key))
            row = cur.fetchone()
            if not row:
                return default
            try:
                return json.loads(row[0])
            except Exception:
                return row[0]

    def set(self, key: str, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False)
        with self.sql.connect() as con:
            con.execute(
                "INSERT INTO kv(ns,k,v) VALUES(?,?,?) ON CONFLICT(ns,k) DO UPDATE SET v=excluded.v",
                (self.ns, key, data),
            )
            con.commit()

    def delete(self, key: str) -> None:
        with self.sql.connect() as con:
            con.execute("DELETE FROM kv WHERE ns=? AND k=?", (self.ns, key))
            con.commit()

    def list(self, prefix: str = "") -> list[str]:
        pattern = f"{prefix}%" if prefix else "%"
        with self.sql.connect() as con:
            cur = con.execute("SELECT k FROM kv WHERE ns=? AND k LIKE ?", (self.ns, pattern))
            return [row[0] for row in cur.fetchall()]
