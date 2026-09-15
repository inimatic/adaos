from contextlib import closing
import sqlite3

import pytest

from adaos.domain.relational_storage import RelationalMigration
from adaos.services.applications.sqlite_data_transition import SQLiteDataTransition


def write(path, sql, *args):
    with closing(sqlite3.connect(path)) as c:
        c.execute(sql, args)
        c.commit()


def rows(path, query="SELECT * FROM records"):
    with closing(sqlite3.connect(path)) as c:
        return c.execute(query).fetchall()


def seed(tmp_path):
    stable = tmp_path / "stable.sqlite"
    write(stable, "CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    write(stable, "INSERT INTO records VALUES (1, ?)", "private original")
    return SQLiteDataTransition(tmp_path), stable


def test_two_forward_cycles_preserve_stable_and_beta_writes(tmp_path):
    adapter, stable = seed(tmp_path)
    migrations = []
    for number in (1, 2):
        snapshot, staged, beta = [tmp_path / f"{name}{number}.sqlite" for name in ("snapshot", "staged", "beta")]
        snap = adapter.snapshot(stable, snapshot, operation_key=f"snapshot{number}")
        migrations.append(RelationalMigration(number, f"extra{number}", (f"ALTER TABLE records ADD COLUMN extra{number} TEXT",)))
        result = adapter.migrate(snapshot, staged, snapshot_digest=snap["digest"], migrations=migrations, operation_key=f"migration{number}")
        adapter.install(staged, beta, staged_digest=result["digest"])
        write(beta, "INSERT INTO records(id, value) VALUES (?, ?)", number + 1, f"beta{number}")
        accepted = tmp_path / f"accepted{number}.sqlite"
        beta_snapshot = adapter.snapshot(beta, accepted, operation_key=f"accept{number}")
        adapter.install(accepted, stable, staged_digest=beta_snapshot["digest"])
        assert rows(stable, "SELECT id,value FROM records ORDER BY id") == [(1, "private original")] + [(i + 1, f"beta{i}") for i in range(1, number + 1)]
    assert "private original" not in str(result)


def test_new_beta_uses_stable_not_previous_beta_and_replay_does_not_reseed(tmp_path):
    adapter, stable = seed(tmp_path)
    snapshot = tmp_path / "snapshot.sqlite"
    first = adapter.snapshot(stable, snapshot, operation_key="first")
    write(stable, "INSERT INTO records VALUES (2, 'later')")
    assert adapter.snapshot(stable, snapshot, operation_key="first") == first
    assert len(rows(snapshot)) == 1
    second = tmp_path / "second.sqlite"
    adapter.snapshot(stable, second, operation_key="second")
    assert len(rows(second)) == 2
    with pytest.raises(ValueError, match="changed"):
        adapter.snapshot(stable, snapshot, operation_key="different")


def test_snapshot_includes_live_wal_and_install_handles_existing_wal(tmp_path):
    adapter, stable = seed(tmp_path)
    with closing(sqlite3.connect(stable)) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("INSERT INTO records VALUES (2, 'wal')")
        live.commit()
        snapshot = tmp_path / "snapshot.sqlite"
        receipt = adapter.snapshot(stable, snapshot, operation_key="wal")
        live.execute("INSERT INTO records VALUES (3, 'after snapshot')")
        live.commit()
        adapter.install(snapshot, stable, staged_digest=receipt["digest"])
        assert live.execute("SELECT count(*) FROM records").fetchone()[0] == 2


@pytest.mark.parametrize("statement", ["ATTACH DATABASE ':memory:' AS stolen", "COMMIT", "PRAGMA writable_schema=ON", "DELETE FROM adaos_schema_migrations", "SELECT load_extension('arbitrary')"])
def test_migration_cannot_escape_transaction_or_private_database(tmp_path, statement):
    adapter, stable = seed(tmp_path)
    snapshot, staged = tmp_path / "snapshot.sqlite", tmp_path / "staged.sqlite"
    receipt = adapter.snapshot(stable, snapshot, operation_key="first")
    with pytest.raises(sqlite3.DatabaseError):
        adapter.migrate(snapshot, staged, snapshot_digest=receipt["digest"], operation_key="bad",
                        migrations=[RelationalMigration(1, "bad", (statement,))])
    assert not staged.exists()
    assert rows(stable) == [(1, "private original")]


def test_migration_history_and_snapshot_checksums_are_pinned(tmp_path):
    adapter, stable = seed(tmp_path)
    snapshot = tmp_path / "snapshot.sqlite"
    receipt = adapter.snapshot(stable, snapshot, operation_key="first")
    first = [RelationalMigration(1, "first", ("ALTER TABLE records ADD COLUMN extra TEXT",))]
    staged = tmp_path / "staged.sqlite"
    result = adapter.migrate(snapshot, staged, snapshot_digest=receipt["digest"], migrations=first, operation_key="first")
    with pytest.raises(ValueError, match="history differs"):
        adapter.migrate(staged, tmp_path / "next.sqlite", snapshot_digest=result["digest"], migrations=[], operation_key="second")
    write(snapshot, "DELETE FROM records")
    with pytest.raises(ValueError, match="identity mismatch"):
        adapter.migrate(snapshot, tmp_path / "bad.sqlite", snapshot_digest=receipt["digest"], migrations=first, operation_key="bad")


def test_interruption_after_snapshot_rename_does_not_recapture_new_records(tmp_path, monkeypatch):
    from adaos.services.applications import sqlite_data_transition as module

    adapter, stable = seed(tmp_path)
    destination = tmp_path / "snapshot.sqlite"
    original_write = module.atomic_write_json

    def fail_receipt(path, data):
        if str(path).endswith(".receipt.json"):
            raise SystemExit("interruption before receipt")
        original_write(path, data)

    monkeypatch.setattr(module, "atomic_write_json", fail_receipt)
    with pytest.raises(SystemExit):
        adapter.snapshot(stable, destination, operation_key="once")
    write(stable, "INSERT INTO records VALUES (2, 'later')")
    monkeypatch.setattr(module, "atomic_write_json", original_write)
    receipt = adapter.snapshot(stable, destination, operation_key="once")
    assert receipt["ok"]
    assert len(rows(destination)) == 1
    assert not list(tmp_path.glob("*.pending.json"))


def test_unrecognized_destination_and_paths_outside_owner_are_rejected(tmp_path):
    adapter, stable = seed(tmp_path)
    unknown = tmp_path / "unknown.sqlite"
    unknown.write_bytes(b"owned elsewhere")
    with pytest.raises(ValueError, match="explicit recovery"):
        adapter.snapshot(stable, unknown, operation_key="first")
    assert unknown.read_bytes() == b"owned elsewhere"
    with pytest.raises(ValueError, match="escaped"):
        adapter.snapshot(stable, tmp_path.parent / "outside.sqlite", operation_key="first")
