from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from adaos.adapters.db.relational import (
    PostgreSQLRelationalStorageProvider,
    SQLiteRelationalStorageProvider,
)
from adaos.domain.relational_storage import (
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
        assert db.health()["ok"] is True
        assert db.binding.provider_id == "postgresql"
        assert db.binding.isolation == "database"
        encoded = json.dumps(db.binding.to_dict()).lower()
        assert "://" not in encoded
        assert "password" not in encoded
    finally:
        provider.destroy_for_testing(db.binding, owner_ref="skill:postgres_contract_skill")
