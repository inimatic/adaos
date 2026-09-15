import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
import yaml

from adaos.domain.relational_storage import RelationalMigration
from adaos.services.applications.sqlite_data_transition import initialize_sqlite_schema, SQLiteDataTransition


CHAIN = (RelationalMigration(version=1, name="base", statements=("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, title TEXT)",), dialects=("sqlite",)),
         RelationalMigration(version=2, name="metadata", statements=("ALTER TABLE items ADD COLUMN note TEXT",), dialects=("sqlite",)))


def columns(path):
    with closing(sqlite3.connect(path)) as connection:
        return [row[1] for row in connection.execute("PRAGMA table_info(items)")]


def test_fresh_repeat_and_beta_share_identical_checksum_history(tmp_path):
    source = tmp_path / "source.db"
    assert initialize_sqlite_schema(source, CHAIN)["applied_versions"] == [1, 2]
    assert initialize_sqlite_schema(source, CHAIN)["applied_versions"] == []
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("INSERT INTO items VALUES (1,'synthetic','retained')")
        connection.commit()
    transition = SQLiteDataTransition(tmp_path)
    snapshot = transition.snapshot(source, tmp_path / "snapshot.db", operation_key="snapshot")
    transition.migrate(tmp_path / "snapshot.db", tmp_path / "beta.db", snapshot_digest=snapshot["digest"], migrations=CHAIN, operation_key="beta")
    assert initialize_sqlite_schema(tmp_path / "beta.db", CHAIN)["applied_versions"] == []
    with closing(sqlite3.connect(tmp_path / "beta.db")) as connection:
        assert connection.execute("SELECT * FROM items").fetchall() == [(1, "synthetic", "retained")]


def test_legacy_installed_data_cannot_be_migrated_during_runtime_initialization(tmp_path):
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO items VALUES (1,'synthetic')")
        connection.commit()
    original = path.read_bytes()
    with pytest.raises(ValueError, match="fenced Beta"):
        initialize_sqlite_schema(path, CHAIN)
    assert path.read_bytes() == original
    assert initialize_sqlite_schema(path, CHAIN, development=True)["applied_versions"] == [1, 2]
    assert columns(path) == ["id", "title", "note"]


def test_installed_schema_needs_fenced_upgrade_and_rejects_history_drift(tmp_path):
    path = tmp_path / "data.db"
    initialize_sqlite_schema(path, CHAIN[:1])
    with pytest.raises(ValueError, match="fenced Beta"):
        initialize_sqlite_schema(path, CHAIN)
    assert columns(path) == ["id", "title"]
    altered = (RelationalMigration(version=1, name="changed", statements=("SELECT 1",), dialects=("sqlite",)),)
    with pytest.raises(ValueError, match="history differs"):
        initialize_sqlite_schema(path, altered, development=True)


@pytest.mark.parametrize("sql", ["DROP TABLE adaos_schema_migrations", "PRAGMA user_version=99", "COMMIT", "THIS IS NOT SQL"])
def test_bad_migration_rolls_back_schema_and_ledger(tmp_path, sql):
    path = tmp_path / "data.db"
    chain = (CHAIN[0], RelationalMigration(version=2, name="invalid", statements=(sql,), dialects=("sqlite",)))
    with pytest.raises(sqlite3.DatabaseError):
        initialize_sqlite_schema(path, chain)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_sdk_uses_declared_path_and_actual_dev_scope_only(monkeypatch, tmp_path):
    from adaos.sdk.data import lifecycle

    source = tmp_path / "dev/skills/sample"
    source.mkdir(parents=True)
    manifest = source / "skill.yaml"
    declaration = {"data_lifecycle": {"schema": "adaos.skill.data_lifecycle.v1", "execution": "native_tools", "databases": [
        {"path": "sample.db", "migrations": [{"version": item.version, "name": item.name, "statements": list(item.statements)} for item in CHAIN]}]}}
    manifest.write_text(yaml.safe_dump(declaration), encoding="utf-8")
    current = SimpleNamespace(name="sample", path=source)
    ctx = SimpleNamespace(paths=SimpleNamespace(dev_skills_dir=lambda: source.parent), skill_ctx=SimpleNamespace(get=lambda: current))
    monkeypatch.setattr(lifecycle, "require_ctx", lambda *args: ctx)
    capabilities = []
    monkeypatch.setattr(lifecycle, "require_skill_capability", lambda ctx, capability: capabilities.append(capability) or SimpleNamespace(manifest_path=manifest))
    data = tmp_path / "data"
    monkeypatch.setattr(lifecycle, "resolve_skill_data_root", lambda *args: data)
    result = lifecycle.ensure_database("sample.db")
    assert result == {"path": "sample.db", "schema_version": 2, "applied_versions": [1, 2]}
    assert capabilities == ["storage.relational"]
    for path in ("../other.db", str(tmp_path / "other.db"), "undeclared.db"):
        with pytest.raises(ValueError, match="not declared"):
            lifecycle.ensure_database(path)
    async def misuse():
        with pytest.raises(RuntimeError, match="a_ensure_database"):
            lifecycle.ensure_database("sample.db")
    asyncio.run(misuse())
