import asyncio
import json
from contextlib import closing
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
import yaml
from jsonschema import Draft202012Validator

from adaos.domain.relational_storage import RelationalMigration
from adaos.services.applications.sqlite_data_transition import initialize_sqlite_schema, SQLiteDataTransition


CHAIN = (RelationalMigration(version=1, name="base", statements=("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, title TEXT)",), dialects=("sqlite",)),
         RelationalMigration(version=2, name="metadata", statements=("ALTER TABLE items ADD COLUMN note TEXT",), dialects=("sqlite",)))


def test_authoring_capsule_matches_admitted_capabilities_and_separates_test_double_evidence():
    from adaos.services.applications.data_lifecycle import automation_data_contract

    contract = automation_data_contract()
    example = contract["capability_declaration"]["example"]
    abi = json.loads((Path(__file__).resolve().parents[1] / "src/adaos/abi/skill.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(abi["properties"]["capabilities"])
    validator.validate(example["capabilities"])
    assert not validator.is_valid({"required": example["capabilities"]})
    assert "does not verify Core migrations/checksums" in contract["initialization_contract"]["testing"]


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


def test_complete_chain_verification_does_not_wait_for_reserved_app_writer(tmp_path):
    path = tmp_path / "data.db"
    initialize_sqlite_schema(path, CHAIN)
    with closing(sqlite3.connect(path, isolation_level=None)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            # A redundant BEGIN IMMEDIATE used to fail here with database locked.
            assert initialize_sqlite_schema(path, CHAIN)["applied_versions"] == []
        finally:
            writer.rollback()


def test_parallel_bootstrap_applies_schema_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from adaos.services.applications import sqlite_data_transition as module
    path = tmp_path / "data.db"
    applied = []
    original = module._apply_migrations
    def apply(*args, **kwargs):
        applied.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "_apply_migrations", apply)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: initialize_sqlite_schema(path, CHAIN), range(4)))
    assert len(applied) == 1
    assert sum(bool(result['applied_versions']) for result in results) == 1


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


def test_sdk_initializes_verified_preview_store_and_restores_runtime_scope(monkeypatch, tmp_path):
    from adaos.sdk.data import lifecycle
    from adaos.sdk.data.preview_binding import _bind_preview_data_binding

    source = tmp_path / "dev/skills/sample"
    source.mkdir(parents=True)
    manifest = source / "skill.yaml"
    declaration = {"data_lifecycle": {"schema": "adaos.skill.data_lifecycle.v1", "execution": "native_tools", "databases": [
        {"path": "sample.db", "migrations": [{"version": item.version, "name": item.name, "statements": list(item.statements)} for item in CHAIN]}]}}
    manifest.write_text(yaml.safe_dump(declaration), encoding="utf-8")
    current = SimpleNamespace(name="sample", path=source)
    ctx = SimpleNamespace(paths=SimpleNamespace(dev_skills_dir=lambda: source.parent), skill_ctx=SimpleNamespace(get=lambda: current))
    monkeypatch.setattr(lifecycle, "require_ctx", lambda *args: ctx)
    monkeypatch.setattr(lifecycle, "require_skill_capability", lambda *args: SimpleNamespace(manifest_path=manifest))
    runtime = tmp_path / "runtime-data"
    sandbox = tmp_path / "verified-sandbox"
    monkeypatch.setattr(lifecycle, "resolve_skill_data_root", lambda *args: runtime)
    with _bind_preview_data_binding({"mode": "sandbox", "_data_root": str(sandbox)}):
        assert asyncio.run(lifecycle.a_ensure_database("sample.db"))["applied_versions"] == [1, 2]
        assert columns(sandbox / "sample.db") == ["id", "title", "note"]
        assert not runtime.exists()
        assert lifecycle.ensure_database("sample.db")["applied_versions"] == []
    assert lifecycle.ensure_database("sample.db")["applied_versions"] == [1, 2]
    assert columns(runtime / "sample.db") == ["id", "title", "note"]


@pytest.mark.parametrize("complete", [True, False])
def test_sdk_trial_verifies_existing_chain_without_requesting_dev(monkeypatch, tmp_path, complete):
    from adaos.sdk.data import lifecycle
    from adaos.services.applications.trial_runtime import TrialPaths, TrialRuntimeUnavailable

    owner = SimpleNamespace(paths=SimpleNamespace(package_path=lambda: tmp_path / "package", subnet_id="sn-test"))
    paths = TrialPaths(owner, tmp_path / "trials/candidate-example")
    with pytest.raises(TrialRuntimeUnavailable):
        paths.dev_skills_dir()
    source = paths.root / "skills/sample"
    source.mkdir(parents=True)
    manifest = source / "skill.yaml"
    declaration = {"data_lifecycle": {"schema": "adaos.skill.data_lifecycle.v1", "execution": "native_tools", "databases": [
        {"path": "sample.db", "migrations": [{"version": item.version, "name": item.name, "statements": list(item.statements)} for item in CHAIN]}]}}
    manifest.write_text(yaml.safe_dump(declaration), encoding="utf-8")
    current = SimpleNamespace(name="sample", path=source)
    ctx = SimpleNamespace(paths=paths, skill_ctx=SimpleNamespace(get=lambda: current))
    monkeypatch.setattr(lifecycle, "require_ctx", lambda *args: ctx)
    monkeypatch.setattr(lifecycle, "require_skill_capability", lambda *args: SimpleNamespace(manifest_path=manifest))
    data = paths.root / "skills/.runtime/sample/v0.1/data"
    data.mkdir(parents=True)
    database = data / "sample.db"
    initialize_sqlite_schema(database, CHAIN if complete else CHAIN[:1])
    original = database.read_bytes()
    monkeypatch.setattr(lifecycle, "resolve_skill_data_root", lambda *args: data)
    if complete:
        assert lifecycle.ensure_database("sample.db")["applied_versions"] == []
    else:
        with pytest.raises(ValueError, match="fenced Beta"):
            lifecycle.ensure_database("sample.db")
    assert database.read_bytes() == original
