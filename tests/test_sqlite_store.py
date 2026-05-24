from __future__ import annotations

import sqlite3
from pathlib import Path

from adaos.adapters.db.sqlite_store import SQLite


class _FakePaths:
    def __init__(self, root: Path) -> None:
        self._root = root

    def state_dir(self) -> Path:
        return self._root


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
