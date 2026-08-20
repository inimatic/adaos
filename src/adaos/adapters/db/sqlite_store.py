# src\adaos\adapters\db\sqlite_store.py
# соединение SQLite (SQLite) + простое KV (SQLiteKV)
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Final

from adaos.ports import KV, SQL
from adaos.ports.paths import PathProvider

_DB_FILE = "adaos.db"
_log = logging.getLogger("adaos.sqlite")
_SQLITE_DIAGNOSTICS_LOCK = threading.RLock()
_SQLITE_WRITE_GATES_LOCK = threading.RLock()
_SQLITE_WRITE_GATES: dict[str, "_WriteGate"] = {}
_ACTIVE_CONNECTIONS: dict[int, dict[str, Any]] = {}
_SQLITE_DIAGNOSTICS: dict[str, Any] = {
    "connections_opened_total": 0,
    "connections_closed_total": 0,
    "max_active_connections": 0,
    "slow_connection_total": 0,
    "lock_error_total": 0,
    "write_statement_total": 0,
    "write_gate_wait_total": 0,
    "write_gate_slow_wait_total": 0,
    "last_write_gate_wait": None,
    "last_lock_error": "",
    "last_lock_error_at": None,
    "last_lock_caller": "",
    "last_slow_connection": None,
}


def _connection_caller(depth: int = 2) -> str:
    try:
        frame = sys._getframe(depth)
        module = str(frame.f_globals.get("__name__") or "")
        function = str(frame.f_code.co_name or "")
        return f"{module}.{function}:{frame.f_lineno}"
    except Exception:
        return "unknown"


def _sqlite_connection_warn_s() -> float:
    try:
        return max(0.0, float(os.getenv("ADAOS_SQLITE_CONNECTION_WARN_S", "2.0") or "2.0"))
    except (TypeError, ValueError):
        return 2.0


def _statement_kind(statement: Any) -> str:
    token = str(statement or "").lstrip().split(None, 1)
    return token[0].upper()[:24] if token else "UNKNOWN"


def _register_connection(con: sqlite3.Connection, *, caller: str) -> None:
    connection_id = id(con)
    opened_at = time.time()
    with _SQLITE_DIAGNOSTICS_LOCK:
        _ACTIVE_CONNECTIONS[connection_id] = {
            "connection_id": connection_id,
            "caller": caller,
            "thread_id": threading.get_ident(),
            "thread_name": threading.current_thread().name,
            "opened_at": opened_at,
            "last_statement_kind": "",
            "last_statement_at": None,
            "current_statement_kind": "",
            "current_statement_started_at": None,
            "in_transaction": False,
            "write_transaction_started_at": None,
            "write_gate_wait_started_at": None,
            "write_gate_acquired_at": None,
        }
        _SQLITE_DIAGNOSTICS["connections_opened_total"] = int(
            _SQLITE_DIAGNOSTICS.get("connections_opened_total") or 0
        ) + 1
        _SQLITE_DIAGNOSTICS["max_active_connections"] = max(
            int(_SQLITE_DIAGNOSTICS.get("max_active_connections") or 0),
            len(_ACTIVE_CONNECTIONS),
        )


def _is_write_statement(kind: str) -> bool:
    return kind in {
        "ALTER",
        "BEGIN",
        "CREATE",
        "DELETE",
        "DROP",
        "INSERT",
        "REINDEX",
        "REPLACE",
        "UPDATE",
        "VACUUM",
    }


def _sqlite_write_gate_warn_s() -> float:
    try:
        return max(0.0, float(os.getenv("ADAOS_SQLITE_WRITE_GATE_WARN_S", "0.1") or "0.1"))
    except (TypeError, ValueError):
        return 0.1


def _sqlite_process_write_gate_timeout_s() -> float:
    try:
        timeout_s = float(
            os.getenv("ADAOS_SQLITE_PROCESS_WRITE_GATE_TIMEOUT_S", "120.0") or "120.0"
        )
    except Exception:
        timeout_s = 120.0
    return max(1.0, min(900.0, timeout_s))


def _try_lock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class _WriteGate:
    def __init__(self, database_path: Path) -> None:
        self._thread_lock = threading.RLock()
        self._lock_path = database_path.with_name(f"{database_path.name}.write.lock")
        self._owner_thread_id: int | None = None
        self._depth = 0
        self._handle: Any | None = None

    def acquire(self) -> None:
        self._thread_lock.acquire()
        handle = None
        try:
            thread_id = threading.get_ident()
            if self._owner_thread_id == thread_id:
                self._depth += 1
                return

            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self._lock_path.open("a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()

            timeout_s = _sqlite_process_write_gate_timeout_s()
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    _try_lock_file(handle)
                    self._owner_thread_id = thread_id
                    self._depth = 1
                    self._handle = handle
                    return
                except (BlockingIOError, OSError) as exc:
                    if time.monotonic() >= deadline:
                        raise sqlite3.OperationalError(
                            "database is locked: timed out waiting for the AdaOS process write gate "
                            f"after {timeout_s:.1f} seconds"
                        ) from exc
                    time.sleep(0.025)
        except BaseException:
            if handle is not None:
                handle.close()
            self._thread_lock.release()
            raise

    def release(self) -> None:
        try:
            if self._owner_thread_id != threading.get_ident() or self._depth <= 0:
                raise RuntimeError("SQLite write gate released by a non-owner thread")
            self._depth -= 1
            if self._depth == 0:
                handle = self._handle
                self._handle = None
                self._owner_thread_id = None
                if handle is not None:
                    try:
                        _unlock_file(handle)
                    finally:
                        handle.close()
        finally:
            self._thread_lock.release()


def _write_gate_for_path(path: Path) -> _WriteGate:
    key = str(path.resolve())
    with _SQLITE_WRITE_GATES_LOCK:
        gate = _SQLITE_WRITE_GATES.get(key)
        if gate is None:
            gate = _WriteGate(path.resolve())
            _SQLITE_WRITE_GATES[key] = gate
        return gate


def _record_write_gate_wait_start(con: sqlite3.Connection, *, kind: str) -> None:
    now = time.time()
    with _SQLITE_DIAGNOSTICS_LOCK:
        row = _ACTIVE_CONNECTIONS.get(id(con))
        if row is not None:
            row["current_statement_kind"] = kind
            row["current_statement_started_at"] = now
            row["write_gate_wait_started_at"] = now


def _record_write_gate_acquired(
    con: sqlite3.Connection,
    *,
    kind: str,
    waited_s: float,
) -> None:
    now = time.time()
    with _SQLITE_DIAGNOSTICS_LOCK:
        row = _ACTIVE_CONNECTIONS.get(id(con))
        if row is not None:
            row["write_gate_wait_started_at"] = None
            row["write_gate_acquired_at"] = now
            caller = str(row.get("caller") or "unknown")
            thread_name = str(row.get("thread_name") or "")
        else:
            caller = "unknown"
            thread_name = threading.current_thread().name
        _SQLITE_DIAGNOSTICS["write_gate_wait_total"] = int(
            _SQLITE_DIAGNOSTICS.get("write_gate_wait_total") or 0
        ) + 1
        wait = {
            "caller": caller,
            "thread_name": thread_name,
            "statement_kind": kind,
            "wait_s": round(max(0.0, waited_s), 6),
            "acquired_at": now,
        }
        _SQLITE_DIAGNOSTICS["last_write_gate_wait"] = wait
        warn_s = _sqlite_write_gate_warn_s()
        if waited_s >= warn_s:
            _SQLITE_DIAGNOSTICS["write_gate_slow_wait_total"] = int(
                _SQLITE_DIAGNOSTICS.get("write_gate_slow_wait_total") or 0
            ) + 1
            _log.warning(
                "SQLite write gate wait caller=%s duration_s=%.3f statement=%s thread=%s",
                caller,
                waited_s,
                kind,
                thread_name or "-",
            )


def _record_write_gate_released(con: sqlite3.Connection) -> None:
    with _SQLITE_DIAGNOSTICS_LOCK:
        row = _ACTIVE_CONNECTIONS.get(id(con))
        if row is not None:
            row["write_gate_wait_started_at"] = None
            row["write_gate_acquired_at"] = None


def _record_statement_start(con: sqlite3.Connection, statement: Any) -> None:
    kind = _statement_kind(statement)
    now = time.time()
    with _SQLITE_DIAGNOSTICS_LOCK:
        row = _ACTIVE_CONNECTIONS.get(id(con))
        if row is not None:
            row["last_statement_kind"] = kind
            row["current_statement_kind"] = kind
            row["current_statement_started_at"] = now
        if _is_write_statement(kind):
            _SQLITE_DIAGNOSTICS["write_statement_total"] = int(
                _SQLITE_DIAGNOSTICS.get("write_statement_total") or 0
            ) + 1


def _record_statement_finish(
    con: sqlite3.Connection,
    statement: Any,
    *,
    error: BaseException | None = None,
) -> None:
    connection_id = id(con)
    kind = _statement_kind(statement)
    now = time.time()
    write_statement = _is_write_statement(kind)
    with _SQLITE_DIAGNOSTICS_LOCK:
        row = _ACTIVE_CONNECTIONS.get(connection_id)
        if row is not None:
            row["last_statement_kind"] = kind
            row["last_statement_at"] = now
            row["current_statement_kind"] = ""
            row["current_statement_started_at"] = None
            try:
                in_transaction = bool(con.in_transaction)
            except Exception:
                in_transaction = bool(row.get("in_transaction"))
            row["in_transaction"] = in_transaction
            if write_statement and in_transaction and row.get("write_transaction_started_at") is None:
                row["write_transaction_started_at"] = now
            caller = str(row.get("caller") or "unknown")
        else:
            caller = "unknown"
        if isinstance(error, sqlite3.OperationalError) and "locked" in str(error).lower():
            _SQLITE_DIAGNOSTICS["lock_error_total"] = int(
                _SQLITE_DIAGNOSTICS.get("lock_error_total") or 0
            ) + 1
            _SQLITE_DIAGNOSTICS["last_lock_error"] = f"{type(error).__name__}: {error}"
            _SQLITE_DIAGNOSTICS["last_lock_error_at"] = now
            _SQLITE_DIAGNOSTICS["last_lock_caller"] = caller


def _record_transaction_end(con: sqlite3.Connection) -> None:
    with _SQLITE_DIAGNOSTICS_LOCK:
        row = _ACTIVE_CONNECTIONS.get(id(con))
        if row is not None:
            row["in_transaction"] = False
            row["write_transaction_started_at"] = None


def _record_connection_close(
    con: sqlite3.Connection,
    *,
    in_transaction_before_close: bool,
) -> None:
    now = time.time()
    with _SQLITE_DIAGNOSTICS_LOCK:
        row = _ACTIVE_CONNECTIONS.pop(id(con), None)
        if row is None:
            return
        duration_s = max(0.0, now - float(row.get("opened_at") or now))
        _SQLITE_DIAGNOSTICS["connections_closed_total"] = int(
            _SQLITE_DIAGNOSTICS.get("connections_closed_total") or 0
        ) + 1
        warn_s = _sqlite_connection_warn_s()
        if duration_s >= warn_s:
            slow = {
                "caller": str(row.get("caller") or "unknown"),
                "duration_s": round(duration_s, 3),
                "last_statement_kind": str(row.get("last_statement_kind") or ""),
                "in_transaction_before_close": bool(in_transaction_before_close),
                "closed_at": now,
            }
            _SQLITE_DIAGNOSTICS["slow_connection_total"] = int(
                _SQLITE_DIAGNOSTICS.get("slow_connection_total") or 0
            ) + 1
            _SQLITE_DIAGNOSTICS["last_slow_connection"] = slow
            _log.warning(
                "SQLite connection held too long caller=%s duration_s=%.3f last_statement=%s in_transaction=%s",
                slow["caller"],
                duration_s,
                slow["last_statement_kind"] or "-",
                bool(in_transaction_before_close),
            )


def sqlite_connection_diagnostics_snapshot() -> dict[str, Any]:
    now = time.time()
    with _SQLITE_DIAGNOSTICS_LOCK:
        active = []
        for row in sorted(
            _ACTIVE_CONNECTIONS.values(),
            key=lambda item: float(item.get("opened_at") or now),
        )[:20]:
            opened_at = float(row.get("opened_at") or now)
            write_started_at = row.get("write_transaction_started_at")
            statement_started_at = row.get("current_statement_started_at")
            active.append(
                {
                    "connection_id": int(row.get("connection_id") or 0),
                    "caller": str(row.get("caller") or "unknown"),
                    "thread_id": int(row.get("thread_id") or 0),
                    "thread_name": str(row.get("thread_name") or ""),
                    "age_s": round(max(0.0, now - opened_at), 3),
                    "last_statement_kind": str(row.get("last_statement_kind") or ""),
                    "current_statement_kind": str(row.get("current_statement_kind") or ""),
                    "current_statement_age_s": (
                        round(max(0.0, now - float(statement_started_at)), 3)
                        if statement_started_at is not None
                        else None
                    ),
                    "in_transaction": bool(row.get("in_transaction")),
                    "write_transaction_age_s": (
                        round(max(0.0, now - float(write_started_at)), 3)
                        if write_started_at is not None
                        else None
                    ),
                    "write_gate_wait_age_s": (
                        round(max(0.0, now - float(row["write_gate_wait_started_at"])), 3)
                        if row.get("write_gate_wait_started_at") is not None
                        else None
                    ),
                    "write_gate_held_age_s": (
                        round(max(0.0, now - float(row["write_gate_acquired_at"])), 3)
                        if row.get("write_gate_acquired_at") is not None
                        else None
                    ),
                }
            )
        return {
            "schema": "adaos.sqlite.connection_diagnostics.v1",
            **dict(_SQLITE_DIAGNOSTICS),
            "active_connection_total": len(_ACTIVE_CONNECTIONS),
            "active_connections": active,
        }


class _ClosingConnection(sqlite3.Connection):
    _adaos_write_gate: _WriteGate | None = None
    _adaos_write_gate_acquired = False

    def _acquire_write_gate(self, statement: Any) -> None:
        kind = _statement_kind(statement)
        gate = self._adaos_write_gate
        if not _is_write_statement(kind) or gate is None or self._adaos_write_gate_acquired:
            return
        _record_write_gate_wait_start(self, kind=kind)
        started_at = time.monotonic()
        try:
            gate.acquire()
        except BaseException as exc:
            _record_statement_finish(self, statement, error=exc)
            raise
        waited_s = max(0.0, time.monotonic() - started_at)
        self._adaos_write_gate_acquired = True
        _record_write_gate_acquired(self, kind=kind, waited_s=waited_s)

    def _release_write_gate(self) -> None:
        gate = self._adaos_write_gate
        if gate is None or not self._adaos_write_gate_acquired:
            return
        self._adaos_write_gate_acquired = False
        _record_write_gate_released(self)
        gate.release()

    def execute(self, sql: Any, parameters: Any = (), /) -> sqlite3.Cursor:
        self._acquire_write_gate(sql)
        _record_statement_start(self, sql)
        try:
            cursor = super().execute(sql, parameters)
        except BaseException as exc:
            _record_statement_finish(self, sql, error=exc)
            raise
        _record_statement_finish(self, sql)
        return cursor

    def executemany(self, sql: Any, seq_of_parameters: Any, /) -> sqlite3.Cursor:
        self._acquire_write_gate(sql)
        _record_statement_start(self, sql)
        try:
            cursor = super().executemany(sql, seq_of_parameters)
        except BaseException as exc:
            _record_statement_finish(self, sql, error=exc)
            raise
        _record_statement_finish(self, sql)
        return cursor

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        self._acquire_write_gate(sql_script)
        _record_statement_start(self, sql_script)
        try:
            cursor = super().executescript(sql_script)
        except BaseException as exc:
            _record_statement_finish(self, sql_script, error=exc)
            raise
        _record_statement_finish(self, sql_script)
        return cursor

    def commit(self) -> None:
        try:
            super().commit()
        except BaseException:
            raise
        else:
            _record_transaction_end(self)
            self._release_write_gate()

    def rollback(self) -> None:
        try:
            super().rollback()
        except BaseException:
            raise
        else:
            _record_transaction_end(self)
            self._release_write_gate()

    def close(self) -> None:
        try:
            in_transaction = bool(self.in_transaction)
        except Exception:
            in_transaction = False
        try:
            _record_connection_close(self, in_transaction_before_close=in_transaction)
            super().close()
        finally:
            self._release_write_gate()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        try:
            in_transaction = bool(self.in_transaction)
        except Exception:
            in_transaction = False
        try:
            return bool(super().__exit__(exc_type, exc, tb))
        finally:
            _record_connection_close(self, in_transaction_before_close=in_transaction)
            try:
                sqlite3.Connection.close(self)
            finally:
                self._release_write_gate()


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
        self._write_gate = _write_gate_for_path(self._db_path)
        with self._connect_raw() as con:
            _configure_connection(con, foreign_keys=False)
            _register_connection(con, caller="adaos.sqlite.bootstrap")
            try:
                con.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise

    def connect(self) -> sqlite3.Connection:
        con = self._connect_raw()
        _configure_connection(con, foreign_keys=True)
        _register_connection(con, caller=_connection_caller(2))
        return con

    def _connect_raw(self) -> _ClosingConnection:
        con = sqlite3.connect(
            self._db_path,
            timeout=_sqlite_timeout_s(),
            factory=_ClosingConnection,
        )
        con._adaos_write_gate = self._write_gate
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
