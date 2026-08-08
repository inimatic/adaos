from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from adaos.adapters.db.relational import (
    PostgreSQLRelationalStorageProvider,
    SQLiteRelationalStorageProvider,
)
from adaos.domain.relational_storage import (
    RelationalMigration,
    RelationalStorageCapabilityError,
    RelationalStorageIsolationError,
    RelationalStorageRequirements,
)
from adaos.sdk.data.relational import database
from adaos.services.policy.skill_capabilities import SkillCapabilityAdmissionError
from adaos.services.storage.relational import RelationalStorageBroker


def _activate_skill(
    ctx,
    name: str,
    *,
    capabilities: tuple[str, ...] = ("storage.relational",),
) -> Path:
    source = Path(ctx.paths.skills_dir()) / name
    source.mkdir(parents=True, exist_ok=True)
    (source / "skill.yaml").write_text(
        "name: " + name + "\nversion: 0.1.0\ncapabilities:\n"
        + "".join(f"  - {item}\n" for item in capabilities),
        encoding="utf-8",
    )
    assert ctx.skill_ctx.set(name, source)
    return source


def test_relational_storage_is_exported_from_public_data_sdk() -> None:
    from adaos.sdk.data import (
        RelationalStorageRequirements as PublicRequirements,
        relational_database,
    )

    assert relational_database is database
    assert PublicRequirements is RelationalStorageRequirements


def test_sdk_database_is_private_to_current_skill(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "alpha_skill")
    alpha = database("main")
    alpha.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    alpha.execute("INSERT INTO notes(id, body) VALUES (:id, :body)", {"id": 1, "body": "alpha"})
    assert alpha.fetch_one("SELECT id, body FROM notes WHERE id = :id", {"id": 1}) == {
        "id": 1,
        "body": "alpha",
    }

    _activate_skill(ctx, "beta_skill")
    beta = database("main")
    beta.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    beta.execute("INSERT INTO notes(id, body) VALUES (:id, :body)", {"id": 1, "body": "beta"})
    assert beta.scalar("SELECT body FROM notes WHERE id = :id", {"id": 1}) == "beta"

    with pytest.raises(RelationalStorageIsolationError):
        alpha.scalar("SELECT body FROM notes WHERE id = :id", {"id": 1})

    _activate_skill(ctx, "alpha_skill")
    assert database("main").scalar("SELECT body FROM notes WHERE id = :id", {"id": 1}) == "alpha"


def test_sdk_database_requires_relational_capability(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "ungranted_skill", capabilities=())

    with pytest.raises(SkillCapabilityAdmissionError, match="not_declared"):
        database("main")


def test_profile_can_deny_a_declared_relational_capability(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "profile_denied_skill")
    profile = Path(ctx.paths.state_dir()) / "capabilities" / "skill_grants.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        json.dumps(
            {
                "schema": "adaos.skill_capability_grants.v1",
                "subjects": {
                    "skill:profile_denied_skill": {
                        "deny": ["storage.relational"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SkillCapabilityAdmissionError, match="profile_denied"):
        database("main")


def test_sqlite_binding_uses_runtime_data_db_and_is_redacted(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "tracker_skill")
    db = database("telemetry", requirements=RelationalStorageRequirements(json_required=True))
    db.execute("CREATE TABLE payloads (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
    db.execute(
        "INSERT INTO payloads(id, payload) VALUES (:id, :payload)",
        {"id": 1, "payload": json.dumps({"ok": True})},
    )

    broker = ctx.relational_storage
    provider = broker.provider_for(db.binding)
    assert isinstance(provider, SQLiteRelationalStorageProvider)
    target = provider.target_path_for_testing(db.binding, owner_ref="skill:tracker_skill")
    expected = (
        Path(ctx.paths.skills_dir())
        / ".runtime"
        / "tracker_skill"
        / "v0.0"
        / "data"
        / "db"
        / "telemetry.db"
    ).resolve()
    assert target == expected
    assert target.exists()

    public = db.binding.to_dict()
    encoded = json.dumps(public, sort_keys=True).lower()
    assert public["capability"] == "storage.relational"
    assert public["owner_ref"] == "skill:tracker_skill"
    assert public["migration_owner"] == "skill:tracker_skill"
    assert public["locator"] == "skill-data:db/telemetry.db"
    assert "password" not in encoded
    assert str(target).lower() not in encoded
    assert "://" not in encoded


def test_transaction_rolls_back_and_named_parameters_are_portable(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "analysis_skill")
    db = database("analysis")
    db.execute("CREATE TABLE values_table (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)")

    with pytest.raises(RuntimeError):
        with db.transaction() as session:
            session.execute(
                "INSERT INTO values_table(id, value) VALUES (:id, :value)",
                {"id": 1, "value": 42},
            )
            raise RuntimeError("abort")

    assert db.scalar("SELECT COUNT(*) FROM values_table") == 0


def test_owner_migrations_are_versioned_idempotent_and_rollback_on_failure(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "migration_skill")
    db = database("research")
    plan = (
        RelationalMigration(
            version=1,
            name="create studies",
            statements=("CREATE TABLE studies (id TEXT PRIMARY KEY, title TEXT NOT NULL)",),
            idempotent=True,
        ),
        RelationalMigration(
            version=2,
            name="add state",
            statements=("ALTER TABLE studies ADD COLUMN state TEXT NOT NULL DEFAULT 'draft'",),
        ),
    )
    first = db.migrate(plan, staged=True)
    second = db.migrate(plan, staged=True)
    assert first.applied_versions == (1, 2)
    assert first.current_version == 2
    assert second.applied_versions == ()

    changed = RelationalMigration(
        version=2,
        name="rewritten history",
        statements=("SELECT 1",),
    )
    with pytest.raises(ValueError, match="checksum differs"):
        db.migrate((changed,), staged=True)

    failing = RelationalMigration(
        version=3,
        name="atomic failure",
        statements=(
            "CREATE TABLE rolled_back (id INTEGER PRIMARY KEY)",
            "INSERT INTO missing_table(id) VALUES (1)",
        ),
    )
    with pytest.raises(Exception):
        db.migrate((failing,), staged=True)
    assert db.scalar(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='rolled_back'"
    ) == 0


def test_sqlite_backup_restore_and_capacity_contract(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "backup_skill")
    db = database(
        "durable",
        requirements=RelationalStorageRequirements(
            backup_required=True,
            restore_required=True,
            capacity_bytes=1024 * 1024,
            rollback_policy="restore",
        ),
    )
    db.execute("CREATE TABLE values_table (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO values_table(id, value) VALUES (1, 'before')")
    backup = db.backup()
    db.execute("UPDATE values_table SET value = 'after' WHERE id = 1")
    db.restore(backup)
    assert db.scalar("SELECT value FROM values_table WHERE id = 1") == "before"


def test_sqlite_capacity_exhaustion_and_binding_deletion_fail_closed(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "capacity_skill")
    db = database(
        "bounded",
        requirements=RelationalStorageRequirements(
            capacity_bytes=512 * 1024,
            retention_policy="delete_on_uninstall",
        ),
    )
    db.execute("CREATE TABLE blobs (id INTEGER PRIMARY KEY, payload BLOB NOT NULL)")
    with pytest.raises(Exception, match="(?i)(full|space|capacity)"):
        db.execute(
            "INSERT INTO blobs(id, payload) VALUES (:id, :payload)",
            {"id": 1, "payload": bytes(2 * 1024 * 1024)},
        )
    provider = ctx.relational_storage.provider_for(db.binding)
    provider.delete(db.binding, owner_ref="skill:capacity_skill", reason="conformance cleanup")
    with pytest.raises(RelationalStorageIsolationError, match="not registered"):
        db.scalar("SELECT 1")


def test_retention_is_negotiated_and_deletion_fails_closed(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "retained_skill")
    retained = database("retained")
    assert retained.binding.requirements["retention_policy"] == "retain"
    provider = ctx.relational_storage.provider_for(retained.binding)
    with pytest.raises(RelationalStorageCapabilityError, match="retention policy is retain"):
        provider.delete(
            retained.binding,
            owner_ref="skill:retained_skill",
            reason="must not override negotiated retention",
        )
    with pytest.raises(RelationalStorageCapabilityError, match="retention:ttl"):
        database(
            "ttl",
            requirements=RelationalStorageRequirements(
                retention_policy="ttl",
                retention_days=7,
            ),
        )


def test_broker_rejects_requirements_instead_of_silently_weakening_them(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "parallel_skill")
    with pytest.raises(RelationalStorageCapabilityError, match="concurrent_writers"):
        database(
            "parallel",
            requirements=RelationalStorageRequirements(concurrent_writers=2),
        )


def test_private_binding_cannot_assign_another_migration_owner(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "owner_skill")
    with pytest.raises(RelationalStorageIsolationError, match="migrations"):
        database(
            "main",
            requirements=RelationalStorageRequirements(migration_owner="skill:other_skill"),
        )


def test_logical_name_cannot_escape_skill_data_root(_autocontext) -> None:
    ctx = _autocontext
    _activate_skill(ctx, "safe_skill")
    with pytest.raises(ValueError, match="logical_name"):
        database("../other_skill")


@pytest.mark.integration
def test_postgresql_provider_conformance_when_server_is_configured(_autocontext) -> None:
    admin_url = str(os.getenv("ADAOS_TEST_POSTGRES_URL") or "").strip()
    if not admin_url:
        pytest.skip("ADAOS_TEST_POSTGRES_URL is not configured")

    ctx = _autocontext
    provider = PostgreSQLRelationalStorageProvider(
        admin_url,
        secret_ref="test:storage/postgresql",
    )
    object.__setattr__(
        ctx,
        "relational_storage",
        RelationalStorageBroker((SQLiteRelationalStorageProvider(), provider)),
    )
    _activate_skill(ctx, "postgres_contract_skill")
    logical_name = f"contract_{uuid.uuid4().hex[:12]}"
    db = database(
        logical_name,
        requirements=RelationalStorageRequirements(
            concurrent_writers=2,
            json_required=True,
            backup_required=True,
            restore_required=True,
            rollback_policy="restore",
            locality="network",
            preferred_providers=("postgresql",),
        ),
    )
    try:
        db.execute("CREATE TABLE contract_values (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        db.execute(
            "INSERT INTO contract_values(id, value) VALUES (:id, :value)",
            {"id": 1, "value": "postgres"},
        )
        assert db.fetch_all("SELECT id, value FROM contract_values") == [
            {"id": 1, "value": "postgres"}
        ]
        backup = db.backup()
        db.execute("UPDATE contract_values SET value = 'changed' WHERE id = 1")
        db.restore(backup)
        assert db.scalar("SELECT value FROM contract_values WHERE id = 1") == "postgres"
        assert db.health()["ok"] is True
        assert db.binding.provider_id == "postgresql"
        assert db.binding.isolation == "database"
        assert db.binding.requirements["backup_required"] is True
        encoded = json.dumps(db.binding.to_dict()).lower()
        assert "://" not in encoded
        assert "password" not in encoded
        service_url = make_url(
            provider.service_uri(
                db.binding,
                owner_ref="skill:postgres_contract_skill",
            )
        )
        admin = make_url(admin_url)
        assert service_url.username
        assert service_url.username.startswith("adaos_owner_")
        assert service_url.username != admin.username
        assert service_url.password
        assert service_url.database and service_url.database.startswith("adaos_")
        service_engine = create_engine(service_url, future=True, pool_pre_ping=True)
        try:
            with service_engine.begin() as connection:
                identity = connection.execute(
                    text("SELECT current_user, current_database()")
                ).one()
                assert identity[0] == service_url.username
                assert identity[1] == service_url.database
                connection.execute(
                    text(
                        "CREATE TABLE service_login_probe "
                        "(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO service_login_probe(id, value) "
                        "VALUES (:id, :value)"
                    ),
                    {"id": 1, "value": "process-only"},
                )
                assert connection.execute(
                    text("SELECT value FROM service_login_probe WHERE id=:id"),
                    {"id": 1},
                ).scalar_one() == "process-only"
        finally:
            service_engine.dispose()
        rotation = provider.rotate_admin_credentials(admin_url)
        assert rotation["ok"] is True
        assert rotation["bindings_rebound"] == 1
        assert rotation["secret_ref"] == "test:storage/postgresql"
        assert db.health()["ok"] is True
    finally:
        provider.destroy_for_testing(db.binding, owner_ref="skill:postgres_contract_skill")
