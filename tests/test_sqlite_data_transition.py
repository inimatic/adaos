from contextlib import closing
import sqlite3

import pytest

from adaos.domain.relational_storage import RelationalMigration
from adaos.services.applications.sqlite_data_transition import SQLiteDataTransition
from adaos.services.applications.sqlite_data_transition import initialize_sqlite_schema
from adaos.services.applications.sqlite_data_transition import SQLiteTransitionPolicy


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


def test_verified_empty_chain_reuses_snapshot_without_mutation_or_writable_alias(tmp_path):
    adapter, stable = seed(tmp_path)
    snapshot, beta = tmp_path / "snapshot.sqlite", tmp_path / "beta.sqlite"
    receipt = adapter.snapshot(stable, snapshot, operation_key="empty-chain")
    original = snapshot.read_bytes()
    result = adapter.verify_unmigrated_snapshot(snapshot, snapshot_digest=receipt["digest"])
    assert result["mode"] == "verified_empty_migration_chain"
    assert snapshot.read_bytes() == original
    adapter.install(snapshot, beta, staged_digest=result["digest"])
    write(beta, "INSERT INTO records VALUES (2, 'beta only')")
    assert len(rows(snapshot)) == len(rows(stable)) == 1
    assert len(rows(beta)) == 2


def test_empty_chain_cannot_discard_installed_migration_history(tmp_path):
    adapter, stable = seed(tmp_path)
    initialize_sqlite_schema(stable, [RelationalMigration(1, "existing", ("CREATE TABLE another(id INTEGER)",))], development=True)
    snapshot = tmp_path / "snapshot.sqlite"
    receipt = adapter.snapshot(stable, snapshot, operation_key="existing-chain")
    with pytest.raises(ValueError, match="full migration chain"):
        adapter.verify_unmigrated_snapshot(snapshot, snapshot_digest=receipt["digest"])


def test_unchanged_verification_retains_proof_and_baseline_not_duplicate(tmp_path):
    adapter, stable = seed(tmp_path)
    baseline, checked = tmp_path / "baseline.sqlite", tmp_path / "checked.sqlite"
    snapshot = adapter.snapshot(stable, baseline, operation_key="base")
    proof = adapter.verify_snapshot_unchanged(stable, baseline, checked,
        baseline_digest=snapshot["digest"], operation_key="before")
    assert proof["digest"] == snapshot["digest"]
    assert baseline.exists() and not checked.exists()
    assert checked.with_suffix(".sqlite.unchanged.json").is_file()
    write(stable, "INSERT INTO records VALUES (2, 'adopted')")
    assert adapter.verify_snapshot_unchanged(stable, baseline, checked,
        baseline_digest=snapshot["digest"], operation_key="before") == proof
    assert len(rows(baseline)) == 1 and len(rows(stable)) == 2


def test_changed_stable_is_rejected_and_verification_copy_is_retained(tmp_path):
    adapter, stable = seed(tmp_path)
    baseline, checked = tmp_path / "baseline.sqlite", tmp_path / "checked.sqlite"
    snapshot = adapter.snapshot(stable, baseline, operation_key="base")
    write(stable, "INSERT INTO records VALUES (2, 'unexpected write')")
    with pytest.raises(ValueError, match="Stable data changed"):
        adapter.verify_snapshot_unchanged(stable, baseline, checked,
            baseline_digest=snapshot["digest"], operation_key="before")
    assert len(rows(checked)) == 2 and len(rows(baseline)) == 1
    assert not checked.with_suffix(".sqlite.unchanged.json").exists()


def test_equality_proof_survives_interruption_before_duplicate_retirement(tmp_path, monkeypatch):
    adapter, stable = seed(tmp_path)
    baseline, checked = tmp_path / "baseline.sqlite", tmp_path / "checked.sqlite"
    snapshot = adapter.snapshot(stable, baseline, operation_key="base")
    retire = adapter._retire_verified_duplicate
    def interrupted(*args):
        raise OSError("injected interruption")
    monkeypatch.setattr(adapter, "_retire_verified_duplicate", interrupted)
    with pytest.raises(OSError, match="interruption"):
        adapter.verify_snapshot_unchanged(stable, baseline, checked,
            baseline_digest=snapshot["digest"], operation_key="before")
    assert checked.exists()
    monkeypatch.setattr(adapter, "_retire_verified_duplicate", retire)
    write(stable, "INSERT INTO records VALUES (2, 'adopted')")
    assert adapter.verify_snapshot_unchanged(stable, baseline, checked,
        baseline_digest=snapshot["digest"], operation_key="before")["ok"]
    assert not checked.exists()


def test_equality_proof_never_retires_a_changed_duplicate(tmp_path, monkeypatch):
    adapter, stable = seed(tmp_path)
    baseline, checked = tmp_path / "baseline.sqlite", tmp_path / "checked.sqlite"
    snapshot = adapter.snapshot(stable, baseline, operation_key="base")
    retire = adapter._retire_verified_duplicate
    monkeypatch.setattr(adapter, "_retire_verified_duplicate", lambda *args: None)
    adapter.verify_snapshot_unchanged(stable, baseline, checked,
        baseline_digest=snapshot["digest"], operation_key="before")
    write(checked, "INSERT INTO records VALUES (2, 'tampered')")
    monkeypatch.setattr(adapter, "_retire_verified_duplicate", retire)
    with pytest.raises(ValueError, match="changed before retirement"):
        adapter.verify_snapshot_unchanged(stable, baseline, checked,
            baseline_digest=snapshot["digest"], operation_key="before")
    assert checked.exists()


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


def test_checked_column_migrates_existing_rows_and_reopens_with_same_ledger(tmp_path):
    adapter, stable = seed(tmp_path)
    snapshot, staged = tmp_path / "snapshot.sqlite", tmp_path / "staged.sqlite"
    receipt = adapter.snapshot(stable, snapshot, operation_key="checked")
    chain = [RelationalMigration(1, "checked_column", (
        "ALTER TABLE records ADD COLUMN score INTEGER CHECK(score BETWEEN 1 AND 5 OR score IS NULL)",))]
    adapter.migrate(snapshot, staged, snapshot_digest=receipt["digest"], migrations=chain, operation_key="checked")
    assert rows(staged) == [(1, "private original", None)]
    assert rows(stable) == [(1, "private original")]
    assert initialize_sqlite_schema(staged, chain)["applied_versions"] == []
    with pytest.raises(sqlite3.IntegrityError):
        write(staged, "UPDATE records SET score = 6")
    write(staged, "UPDATE records SET score = 4")
    assert rows(staged) == [(1, "private original", 4)]


def test_fenced_upgrade_preserves_extended_relational_sdk_ledger(tmp_path):
    """An installed SDK database must retain its five-column migration ledger."""
    adapter, stable = seed(tmp_path)
    first = RelationalMigration(1, "initial", ("CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)",), idempotent=True)
    write(stable, "CREATE TABLE adaos_schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL, staged INTEGER NOT NULL)")
    write(stable, "INSERT INTO adaos_schema_migrations VALUES (1, ?, ?, ?, 0)", first.name, first.checksum, "original-timestamp")
    second = RelationalMigration(2, "chat_ref", ("ALTER TABLE records ADD COLUMN chat_ref TEXT",))
    with pytest.raises(ValueError, match="fenced"):
        initialize_sqlite_schema(stable, [first, second])
    snapshot, staged = tmp_path / "snapshot.sqlite", tmp_path / "staged.sqlite"
    proof = adapter.snapshot(stable, snapshot, operation_key="extended-ledger-snapshot")
    adapter.migrate(snapshot, staged, snapshot_digest=proof["digest"], migrations=[first, second], operation_key="extended-ledger-migration")
    history = rows(staged, "SELECT version,checksum,applied_at,staged FROM adaos_schema_migrations ORDER BY version")
    assert history[0] == (1, first.checksum, "original-timestamp", 0)
    assert history[1][:2] == (2, second.checksum) and history[1][2].endswith("Z") and history[1][3] == 0
    assert rows(staged) == [(1, "private original", None)]
    assert initialize_sqlite_schema(staged, [first, second])["applied_versions"] == []
    assert rows(stable, "SELECT version FROM adaos_schema_migrations") == [(1,)]


def test_checked_column_rejects_existing_violations_and_rolls_back(tmp_path):
    adapter, stable = seed(tmp_path)
    snapshot, staged = tmp_path / "snapshot.sqlite", tmp_path / "staged.sqlite"
    receipt = adapter.snapshot(stable, snapshot, operation_key="checked")
    with pytest.raises(sqlite3.DatabaseError, match="CHECK constraint failed"):
        adapter.migrate(snapshot, staged, snapshot_digest=receipt["digest"], operation_key="bad-check",
            migrations=[RelationalMigration(1, "bad_check", (
                "ALTER TABLE records ADD COLUMN score INTEGER DEFAULT 9 CHECK(score < 5)",))])
    assert not staged.exists()
    assert rows(stable) == [(1, "private original")]


@pytest.mark.parametrize("statement", ["PRAGMA quick_check(records)", "SELECT * FROM pragma_quick_check('records')",
    "PRAGMA writable_schema=ON", "PRAGMA foreign_keys=OFF", "DELETE FROM adaos_schema_migrations"])
def test_alter_grant_cannot_escape_to_another_statement(tmp_path, statement):
    database = tmp_path / "fresh.sqlite"
    chain = [RelationalMigration(1, "create", ("CREATE TABLE records (id INTEGER PRIMARY KEY)",)),
             RelationalMigration(2, "alter", ("ALTER TABLE records ADD COLUMN value TEXT CHECK(length(value) < 20)", statement))]
    with pytest.raises(sqlite3.DatabaseError, match="authorized"):
        initialize_sqlite_schema(database, chain)
    assert rows(database, "SELECT name FROM sqlite_master WHERE type='table'") == []


def test_internal_quick_check_grant_is_target_scoped_and_keeps_ledger_protected():
    authorize = SQLiteDataTransition._statement_authorizer()
    assert authorize(sqlite3.SQLITE_PRAGMA, "quick_check", "records", "main", None) == sqlite3.SQLITE_DENY
    assert authorize(sqlite3.SQLITE_ALTER_TABLE, "main", "records", None, None) == sqlite3.SQLITE_OK
    assert authorize(sqlite3.SQLITE_PRAGMA, "quick_check", "records", "main", None) == sqlite3.SQLITE_OK
    for database, table, trigger in [("main", "other", None), ("temp", "records", None), ("main", "records", "user_trigger")]:
        assert authorize(sqlite3.SQLITE_PRAGMA, "quick_check", table, database, trigger) == sqlite3.SQLITE_DENY
    assert authorize(sqlite3.SQLITE_ALTER_TABLE, "main", "adaos_schema_migrations", None, None) == sqlite3.SQLITE_DENY


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


def test_default_limits_are_not_raised_by_a_large_library_adapter(tmp_path):
    default = SQLiteDataTransition(tmp_path)
    large = SQLiteDataTransition(tmp_path, policy=SQLiteTransitionPolicy(4 * 1024**3, 1800))
    assert default.policy.max_database_bytes == 512 * 1024**2
    assert default.policy.operation_timeout_seconds == 120
    assert large.policy.max_database_bytes == 4 * 1024**3
    assert SQLiteDataTransition(tmp_path).policy == default.policy


@pytest.mark.parametrize("size,seconds", [(True, 120), (4095, 120), (4 * 1024**3 + 1, 120),
    (4096, 0), (4096, float('nan')), (4096, float('inf')), (4096, True), (4096, 1801)])
def test_transition_policy_rejects_unbounded_or_invalid_limits(size, seconds):
    with pytest.raises(ValueError):
        SQLiteTransitionPolicy(size, seconds)


def test_explicit_database_cap_rejects_snapshot_without_modifying_source(tmp_path):
    _adapter, stable = seed(tmp_path)
    original = stable.read_bytes()
    adapter = SQLiteDataTransition(tmp_path, policy=SQLiteTransitionPolicy(4096, 120))
    destination = tmp_path / 'too-large.sqlite'
    with pytest.raises(ValueError, match='admitted database byte limit'):
        adapter.snapshot(stable, destination, operation_key='bounded')
    assert stable.read_bytes() == original
    assert not destination.exists() and not list(tmp_path.glob('*.staging'))


def test_integrity_check_is_interrupted_at_its_deadline_and_handler_is_cleared(tmp_path, monkeypatch):
    from adaos.services.applications import sqlite_data_transition as module
    _adapter, stable = seed(tmp_path)
    # Enough VM instructions to invoke SQLite's progress handler during a real
    # full integrity scan, without large fixture databases or a wall-clock wait.
    with closing(sqlite3.connect(stable)) as connection:
        connection.executemany('INSERT INTO records VALUES (?, ?)', ((i, 'bounded') for i in range(2, 6000)))
        connection.commit()
        ticks = iter([0.0, 2.0, 2.0])
        monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks, 2.0))
        with pytest.raises(TimeoutError, match='integrity verification exceeded'):
            module._integrity(connection, deadline=1.0)
        assert connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)]


def test_migration_backup_and_validation_share_one_deadline(tmp_path, monkeypatch):
    from adaos.services.applications import sqlite_data_transition as module
    adapter, stable = seed(tmp_path)
    snapshot = tmp_path / 'snapshot.sqlite'
    receipt = adapter.snapshot(stable, snapshot, operation_key='deadline-base')
    real_backup = module._backup
    deadlines = []
    def backup(*args, **kwargs):
        deadlines.append(kwargs['deadline'])
        return real_backup(*args, **kwargs)
    real_apply = module._apply_migrations
    def migrate(*args, **kwargs):
        deadlines.append(kwargs['deadline'])
        return real_apply(*args, **kwargs)
    monkeypatch.setattr(module, '_backup', backup)
    monkeypatch.setattr(module, '_apply_migrations', migrate)
    adapter.migrate(snapshot, tmp_path / 'staged.sqlite', snapshot_digest=receipt['digest'],
        migrations=[], operation_key='deadline-migration')
    assert len(deadlines) == 2 and deadlines[0] == deadlines[1]


def test_capacity_rejection_happens_before_opening_the_destination(tmp_path):
    _adapter, stable = seed(tmp_path)
    opened = []
    def admit(path, reserve):
        if reserve:
            assert not path.exists()
            opened.append(reserve)
            raise ValueError('injected capacity rejection')
    adapter = SQLiteDataTransition(tmp_path, admit_write=admit)
    target = tmp_path / 'not-created.sqlite'
    with pytest.raises(ValueError, match='capacity rejection'):
        adapter.snapshot(stable, target, operation_key='capacity')
    assert opened and not target.exists()
    assert not list(tmp_path.glob('*.staging'))
