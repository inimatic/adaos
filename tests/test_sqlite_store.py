from __future__ import annotations

import multiprocessing
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from adaos.adapters.db.sqlite_store import SQLite, sqlite_connection_diagnostics_snapshot


class _FakePaths:
    def __init__(self, root: Path) -> None:
        self._root = root

    def state_dir(self) -> Path:
        return self._root


def _hold_core_sqlite_writer(state_dir: str, ready_path: str, release_path: str) -> None:
    sql = SQLite(_FakePaths(Path(state_dir)))
    with sql.connect() as con:
        con.execute("INSERT INTO process_gate_probe(owner) VALUES ('child')")
        Path(ready_path).touch()
        deadline = time.monotonic() + 10.0
        while not Path(release_path).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        con.commit()


def _churn_core_sqlite_writer(
    state_dir: str,
    ready_path: str,
    release_path: str,
    writes: int,
) -> None:
    sql = SQLite(_FakePaths(Path(state_dir)))
    with sql.connect() as con:
        con.execute("INSERT INTO process_gate_probe(owner) VALUES ('child-initial')")
        Path(ready_path).touch()
        deadline = time.monotonic() + 10.0
        while not Path(release_path).exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        con.commit()
    for index in range(writes):
        with sql.connect() as con:
            con.execute(
                "INSERT INTO process_gate_probe(owner) VALUES (?)",
                (f"child-{index}",),
            )
            con.commit()


def test_sqlite_init_tolerates_locked_wal_probe(tmp_path: Path, monkeypatch) -> None:
    paths = _FakePaths(tmp_path)
    SQLite(paths)
    db_path = tmp_path / "adaos.db"
    monkeypatch.setenv("ADAOS_SQLITE_TIMEOUT_S", "0.1")
    con = sqlite3.connect(db_path, timeout=0.1)
    try:
        con.execute("BEGIN EXCLUSIVE")

        SQLite(paths)
    finally:
        con.rollback()
        con.close()


def test_sqlite_connect_sets_foreign_keys(tmp_path: Path) -> None:
    sql = SQLite(_FakePaths(tmp_path))

    with sql.connect() as con:
        row = con.execute("PRAGMA foreign_keys").fetchone()

    assert row is not None
    assert row[0] == 1


def test_sqlite_context_manager_closes_connection(tmp_path: Path) -> None:
    sql = SQLite(_FakePaths(tmp_path))

    with sql.connect() as con:
        con.execute("SELECT 1").fetchone()

    try:
        con.execute("SELECT 1").fetchone()
    except sqlite3.ProgrammingError as exc:
        assert "closed" in str(exc).lower()
    else:
        raise AssertionError("SQLite connection remained usable after context exit")


def test_sqlite_diagnostics_attribute_active_write_transaction(tmp_path: Path) -> None:
    sql = SQLite(_FakePaths(tmp_path))
    baseline = sqlite_connection_diagnostics_snapshot()
    con = sql.connect()
    try:
        con.execute("CREATE TABLE diagnostic_probe(id INTEGER PRIMARY KEY, value TEXT)")
        con.commit()
        con.execute("INSERT INTO diagnostic_probe(value) VALUES (?)", ("pending",))

        snapshot = sqlite_connection_diagnostics_snapshot()
        active = next(
            item
            for item in snapshot["active_connections"]
            if item["connection_id"] == id(con)
        )

        assert snapshot["connections_opened_total"] >= baseline["connections_opened_total"] + 1
        assert "test_sqlite_diagnostics_attribute_active_write_transaction" in active["caller"]
        assert active["last_statement_kind"] == "INSERT"
        assert active["in_transaction"] is True
        assert active["write_transaction_age_s"] is not None
    finally:
        con.rollback()
        con.close()

    closed = sqlite_connection_diagnostics_snapshot()
    assert all(item["connection_id"] != id(con) for item in closed["active_connections"])


def test_sqlite_diagnostics_record_lock_owner_callsite(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SQLITE_TIMEOUT_S", "0.1")
    sql = SQLite(_FakePaths(tmp_path))
    with sql.connect() as con:
        con.execute("CREATE TABLE lock_probe(id INTEGER PRIMARY KEY)")
        con.commit()

    holder = sqlite3.connect(tmp_path / "adaos.db", timeout=0.1)
    contender = sql.connect()
    baseline = sqlite_connection_diagnostics_snapshot()["lock_error_total"]
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            contender.execute("INSERT INTO lock_probe DEFAULT VALUES")

        snapshot = sqlite_connection_diagnostics_snapshot()
        assert snapshot["lock_error_total"] == baseline + 1
        assert "test_sqlite_diagnostics_record_lock_owner_callsite" in snapshot["last_lock_caller"]
        assert "locked" in snapshot["last_lock_error"].lower()
    finally:
        holder.rollback()
        holder.close()
        contender.close()


def test_sqlite_diagnostics_expose_statement_while_waiting_on_lock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SQLITE_TIMEOUT_S", "0.3")
    sql = SQLite(_FakePaths(tmp_path))
    with sql.connect() as con:
        con.execute("CREATE TABLE blocked_probe(id INTEGER PRIMARY KEY)")
        con.commit()

    holder = sqlite3.connect(tmp_path / "adaos.db", timeout=0.3)
    holder.execute("BEGIN IMMEDIATE")
    opened = threading.Event()
    connection_ids: list[int] = []
    errors: list[BaseException] = []

    def _contend() -> None:
        con = sql.connect()
        connection_ids.append(id(con))
        opened.set()
        try:
            con.execute("INSERT INTO blocked_probe DEFAULT VALUES")
        except BaseException as exc:
            errors.append(exc)
        finally:
            con.close()

    thread = threading.Thread(target=_contend, name="sqlite-lock-contender")
    thread.start()
    try:
        assert opened.wait(timeout=1.0)
        deadline = time.time() + 1.0
        blocked = None
        while time.time() < deadline:
            snapshot = sqlite_connection_diagnostics_snapshot()
            blocked = next(
                (
                    item
                    for item in snapshot["active_connections"]
                    if item["connection_id"] == connection_ids[0]
                    and item["current_statement_kind"] == "INSERT"
                ),
                None,
            )
            if blocked is not None:
                break
            time.sleep(0.01)
        assert blocked is not None
        assert blocked["current_statement_age_s"] is not None
        assert blocked["thread_name"] == "sqlite-lock-contender"
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        assert errors and isinstance(errors[0], sqlite3.OperationalError)
        assert "locked" in str(errors[0]).lower()
    finally:
        holder.rollback()
        holder.close()
        thread.join(timeout=2.0)


def test_sqlite_process_write_gate_serializes_runtime_writers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SQLITE_TIMEOUT_S", "0.1")
    monkeypatch.setenv("ADAOS_SQLITE_WRITE_GATE_WARN_S", "0.01")
    sql = SQLite(_FakePaths(tmp_path))
    with sql.connect() as con:
        con.execute("CREATE TABLE gate_probe(id INTEGER PRIMARY KEY)")
        con.commit()

    holder = sql.connect()
    holder.execute("BEGIN IMMEDIATE")
    opened = threading.Event()
    connection_ids: list[int] = []
    errors: list[BaseException] = []

    def _write_after_gate() -> None:
        con = sql.connect()
        connection_ids.append(id(con))
        opened.set()
        try:
            con.execute("INSERT INTO gate_probe DEFAULT VALUES")
            con.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            con.close()

    baseline = sqlite_connection_diagnostics_snapshot()["write_gate_slow_wait_total"]
    thread = threading.Thread(target=_write_after_gate, name="sqlite-write-gate-contender")
    thread.start()
    try:
        assert opened.wait(timeout=1.0)
        deadline = time.time() + 1.0
        waiting = None
        while time.time() < deadline:
            snapshot = sqlite_connection_diagnostics_snapshot()
            waiting = next(
                (
                    item
                    for item in snapshot["active_connections"]
                    if item["connection_id"] == connection_ids[0]
                    and item["write_gate_wait_age_s"] is not None
                ),
                None,
            )
            if waiting is not None and float(waiting["write_gate_wait_age_s"]) >= 0.01:
                break
            time.sleep(0.01)
        assert waiting is not None
        assert waiting["current_statement_kind"] == "INSERT"
        assert waiting["thread_name"] == "sqlite-write-gate-contender"
        time.sleep(0.03)
    finally:
        holder.rollback()
        holder.close()
        thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert errors == []
    with sql.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM gate_probe").fetchone() == (1,)
    snapshot = sqlite_connection_diagnostics_snapshot()
    assert snapshot["write_gate_slow_wait_total"] >= baseline + 1
    assert snapshot["last_write_gate_wait"]["thread_name"] == "sqlite-write-gate-contender"


def test_sqlite_process_write_gate_serializes_separate_processes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SQLITE_TIMEOUT_S", "0.05")
    monkeypatch.setenv("ADAOS_SQLITE_PROCESS_WRITE_GATE_TIMEOUT_S", "2.0")
    sql = SQLite(_FakePaths(tmp_path))
    with sql.connect() as con:
        con.execute("CREATE TABLE process_gate_probe(id INTEGER PRIMARY KEY, owner TEXT NOT NULL)")
        con.commit()

    ready_path = tmp_path / "writer.ready"
    release_path = tmp_path / "writer.release"
    process = multiprocessing.get_context("spawn").Process(
        target=_hold_core_sqlite_writer,
        args=(str(tmp_path), str(ready_path), str(release_path)),
    )
    process.start()
    try:
        deadline = time.monotonic() + 5.0
        while not ready_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_path.exists()

        release_timer = threading.Timer(0.2, release_path.touch)
        release_timer.start()
        started = time.monotonic()
        with sql.connect() as con:
            con.execute("INSERT INTO process_gate_probe(owner) VALUES ('parent')")
            con.commit()
        elapsed_s = time.monotonic() - started
        release_timer.join(timeout=1.0)

        assert elapsed_s >= 0.15
        with sql.connect() as con:
            assert con.execute(
                "SELECT owner FROM process_gate_probe ORDER BY id"
            ).fetchall() == [("child",), ("parent",)]
    finally:
        release_path.touch(exist_ok=True)
        process.join(timeout=5.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2.0)
    assert process.exitcode == 0


def test_sqlite_process_write_gate_does_not_starve_waiting_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADAOS_SQLITE_TIMEOUT_S", "0.05")
    monkeypatch.setenv("ADAOS_SQLITE_PROCESS_WRITE_GATE_TIMEOUT_S", "5.0")
    sql = SQLite(_FakePaths(tmp_path))
    with sql.connect() as con:
        con.execute(
            "CREATE TABLE process_gate_probe(id INTEGER PRIMARY KEY, owner TEXT NOT NULL)"
        )
        con.commit()

    ready_path = tmp_path / "churn.ready"
    release_path = tmp_path / "churn.release"
    process = multiprocessing.get_context("spawn").Process(
        target=_churn_core_sqlite_writer,
        args=(str(tmp_path), str(ready_path), str(release_path), 40),
    )
    process.start()
    try:
        deadline = time.monotonic() + 5.0
        while not ready_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_path.exists()

        release_timer = threading.Timer(0.1, release_path.touch)
        release_timer.start()
        with sql.connect() as con:
            con.execute("INSERT INTO process_gate_probe(owner) VALUES ('parent-waiter')")
            con.commit()
        release_timer.join(timeout=1.0)
    finally:
        release_path.touch(exist_ok=True)
        process.join(timeout=10.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2.0)

    assert process.exitcode == 0
    with sql.connect() as con:
        owners = [row[0] for row in con.execute(
            "SELECT owner FROM process_gate_probe ORDER BY id"
        ).fetchall()]
    assert owners[:2] == ["child-initial", "parent-waiter"]
