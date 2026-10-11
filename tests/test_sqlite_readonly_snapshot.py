import sqlite3
import threading
from pathlib import Path

import pytest

from adaos.adapters.db.sqlite_store import SQLite


class Paths:
    def __init__(self, root: Path):
        self.root = root

    def state_dir(self):
        return self.root


@pytest.fixture
def store(tmp_path):
    # Spaces and non-ASCII characters must be safe in the read-only URI.
    sql = SQLite(Paths(tmp_path / 'read only тест'))
    with sql.connect() as con:
        con.execute('CREATE TABLE snapshot_probe(id INTEGER PRIMARY KEY, value TEXT)')
        con.execute("INSERT INTO snapshot_probe VALUES (1, 'before')")
        con.commit()
    return sql


def test_readonly_snapshot_does_not_wait_for_writer_gate(store):
    holder = store.connect()
    holder.execute('BEGIN IMMEDIATE')
    holder.execute("UPDATE snapshot_probe SET value='uncommitted' WHERE id=1")
    observed, errors = [], []

    def read():
        try:
            with store.connect(read_only=True) as con:
                con.execute('BEGIN')
                observed.append(con.execute('SELECT value FROM snapshot_probe').fetchone()[0])
        except BaseException as exc:
            errors.append(exc)

    reader = threading.Thread(target=read)
    try:
        reader.start()
        reader.join(timeout=2)
        assert not reader.is_alive(), 'Read-only WAL snapshot must not serialize behind runtime writers'
        assert observed == ['before']
        assert errors == []
    finally:
        holder.rollback()
        holder.close()
        reader.join(timeout=6)


def test_readonly_snapshot_is_consistent_across_writer_commit(store):
    with store.connect(read_only=True) as snapshot:
        snapshot.execute('BEGIN')
        assert snapshot.execute('SELECT value FROM snapshot_probe').fetchone() == ('before',)
        with store.connect() as writer:
            writer.execute("UPDATE snapshot_probe SET value='after' WHERE id=1")
            writer.commit()
        assert snapshot.execute('SELECT value FROM snapshot_probe').fetchone() == ('before',)
    with store.connect(read_only=True) as fresh:
        assert fresh.execute('SELECT value FROM snapshot_probe').fetchone() == ('after',)


@pytest.mark.parametrize('route', ['execute', 'cursor', 'script', 'disable_query_only'])
def test_readonly_connection_enforces_no_writes_in_sqlite_engine(store, route):
    with store.connect(read_only=True) as con:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            if route == 'cursor':
                con.cursor().execute("DELETE FROM snapshot_probe")
            elif route == 'script':
                con.executescript("DELETE FROM snapshot_probe;")
            else:
                if route == 'disable_query_only':
                    con.execute('PRAGMA query_only=OFF')
                con.execute("DELETE FROM snapshot_probe")
    with store.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM snapshot_probe').fetchone() == (1,)
