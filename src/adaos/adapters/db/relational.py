"""Relational capability providers for SQLite and PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine, URL, make_url

from adaos.domain.relational_storage import (
    RelationalProviderCapabilities,
    RelationalStorageBinding,
    RelationalStorageCapabilityError,
    RelationalStorageIsolationError,
    RelationalStorageRequirements,
    validate_logical_name,
    validate_owner_ref,
)


def _binding_id(provider_id: str, owner_ref: str, logical_name: str) -> str:
    payload = json.dumps(
        {
            "capability": "storage.relational",
            "provider_id": provider_id,
            "owner_ref": owner_ref,
            "logical_name": logical_name,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"relbind.{hashlib.sha256(payload).hexdigest()}"


def _sqlite_timeout_s() -> float:
    try:
        value = float(os.getenv("ADAOS_SQLITE_TIMEOUT_S", "5.0") or "5.0")
    except (TypeError, ValueError):
        value = 5.0
    return max(value, 0.1)


class SQLiteRelationalStorageProvider:
    """One SQLite database file per owner and logical binding."""

    def __init__(self) -> None:
        self._capabilities = RelationalProviderCapabilities(
            provider_id="sqlite",
            durability=("durable", "ephemeral"),
            transactions=True,
            max_concurrent_writers=1,
            json=True,
            backup_restore=False,
            localities=("node",),
            isolation="file",
        )
        self._targets: dict[str, Path] = {}
        self._engines: dict[str, Engine] = {}
        self._lock = threading.RLock()

    @property
    def capabilities(self) -> RelationalProviderCapabilities:
        return self._capabilities

    def bind(
        self,
        *,
        owner_ref: str,
        logical_name: str,
        requirements: RelationalStorageRequirements,
        scope_root: Path,
    ) -> RelationalStorageBinding:
        owner = validate_owner_ref(owner_ref)
        logical = validate_logical_name(logical_name)
        requirements = requirements.for_owner(owner)
        rejected = self.capabilities.rejection_reasons(requirements)
        if rejected:
            raise RelationalStorageCapabilityError(
                f"sqlite cannot satisfy relational requirements: {', '.join(rejected)}"
            )
        root = Path(scope_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = (root / f"{logical}.db").resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:  # pragma: no cover - guarded by logical-name validation
            raise RelationalStorageIsolationError("SQLite binding escaped its owner scope") from exc
        binding_id = _binding_id(self.capabilities.provider_id, owner, logical)
        binding = RelationalStorageBinding(
            binding_id=binding_id,
            provider_id=self.capabilities.provider_id,
            owner_ref=owner,
            logical_name=logical,
            isolation=self.capabilities.isolation,
            locator=f"skill-data:db/{logical}.db",
            migration_owner=requirements.migration_owner or owner,
            capabilities=self.capabilities.to_dict(),
        )
        with self._lock:
            existing = self._targets.get(binding_id)
            if existing is not None and existing != target:
                raise RelationalStorageIsolationError(
                    "binding id resolved to a different owner-scoped SQLite target"
                )
            self._targets[binding_id] = target
            engine = self._engines.get(binding_id)
            if engine is None:
                engine = self._create_engine(target)
                self._engines[binding_id] = engine
        with engine.begin() as connection:
            connection.execute(text("SELECT 1"))
            if requirements.json_required:
                try:
                    connection.execute(text("SELECT json('{}')"))
                except Exception as exc:
                    raise RelationalStorageCapabilityError(
                        "the active SQLite build does not provide required JSON support"
                    ) from exc
        return binding

    @staticmethod
    def _create_engine(target: Path) -> Engine:
        url = URL.create("sqlite+pysqlite", database=str(target))
        engine = create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False, "timeout": _sqlite_timeout_s()},
        )

        @event.listens_for(engine, "connect")
        def _configure(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f"PRAGMA busy_timeout={int(_sqlite_timeout_s() * 1000)}")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
            finally:
                cursor.close()

        return engine

    def _engine_for(self, binding: RelationalStorageBinding, owner_ref: str) -> Engine:
        binding.assert_owner(owner_ref)
        if binding.provider_id != self.capabilities.provider_id:
            raise RelationalStorageIsolationError("binding belongs to another provider")
        with self._lock:
            engine = self._engines.get(binding.binding_id)
        if engine is None:
            raise RelationalStorageIsolationError(
                "binding is not registered in this provider process; acquire it again"
            )
        return engine

    @contextmanager
    def transaction(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> Iterator[Connection]:
        engine = self._engine_for(binding, owner_ref)
        with engine.begin() as connection:
            yield connection

    def health(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> Mapping[str, Any]:
        try:
            with self.transaction(binding, owner_ref=owner_ref) as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            return {
                "ok": False,
                "provider_id": self.capabilities.provider_id,
                "binding_id": binding.binding_id,
                "error": type(exc).__name__,
            }
        return {
            "ok": True,
            "provider_id": self.capabilities.provider_id,
            "binding_id": binding.binding_id,
        }

    def target_path_for_testing(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> Path:
        """Return the private target for adapter tests, never for the SDK."""

        self._engine_for(binding, owner_ref)
        return self._targets[binding.binding_id]


_POSTGRES_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class PostgreSQLRelationalStorageProvider:
    """PostgreSQL provider using one isolated logical database per binding.

    The administrator URL remains inside the adapter. Skills receive only an
    opaque AdaOS binding and cannot select a database or obtain credentials.
    """

    def __init__(self, admin_url: str, *, secret_ref: str = "core:storage/postgresql") -> None:
        url = make_url(str(admin_url or "").strip())
        if not url.drivername.startswith("postgresql"):
            raise ValueError("PostgreSQL provider requires a postgresql SQLAlchemy URL")
        if not url.database:
            url = url.set(database="postgres")
        self._admin_url = url
        self._secret_ref = str(secret_ref or "").strip() or "core:storage/postgresql"
        self._capabilities = RelationalProviderCapabilities(
            provider_id="postgresql",
            durability=("durable", "ephemeral"),
            transactions=True,
            max_concurrent_writers=None,
            json=True,
            backup_restore=False,
            localities=("network",),
            isolation="database",
        )
        self._targets: dict[str, URL] = {}
        self._engines: dict[str, Engine] = {}
        self._lock = threading.RLock()

    @property
    def capabilities(self) -> RelationalProviderCapabilities:
        return self._capabilities

    @staticmethod
    def _database_name(owner_ref: str, logical_name: str) -> str:
        digest = hashlib.sha256(f"{owner_ref}\0{logical_name}".encode("utf-8")).hexdigest()[:32]
        name = f"adaos_{digest}"
        if not _POSTGRES_NAME_RE.fullmatch(name):  # pragma: no cover - construction invariant
            raise ValueError("generated PostgreSQL database name is invalid")
        return name

    def bind(
        self,
        *,
        owner_ref: str,
        logical_name: str,
        requirements: RelationalStorageRequirements,
        scope_root: Path,
    ) -> RelationalStorageBinding:
        del scope_root  # PostgreSQL storage is not represented by a local path.
        owner = validate_owner_ref(owner_ref)
        logical = validate_logical_name(logical_name)
        requirements = requirements.for_owner(owner)
        rejected = self.capabilities.rejection_reasons(requirements)
        if rejected:
            raise RelationalStorageCapabilityError(
                f"postgresql cannot satisfy relational requirements: {', '.join(rejected)}"
            )
        database_name = self._database_name(owner, logical)
        binding_id = _binding_id(self.capabilities.provider_id, owner, logical)
        self._ensure_database(database_name)
        target_url = self._admin_url.set(database=database_name)
        binding = RelationalStorageBinding(
            binding_id=binding_id,
            provider_id=self.capabilities.provider_id,
            owner_ref=owner,
            logical_name=logical,
            isolation=self.capabilities.isolation,
            locator=f"adaos-db:{binding_id}",
            migration_owner=requirements.migration_owner or owner,
            capabilities=self.capabilities.to_dict(),
            secret_ref=self._secret_ref,
        )
        with self._lock:
            self._targets[binding_id] = target_url
            if binding_id not in self._engines:
                self._engines[binding_id] = create_engine(
                    target_url,
                    future=True,
                    pool_pre_ping=True,
                )
        return binding

    def _ensure_database(self, database_name: str) -> None:
        admin_engine = create_engine(
            self._admin_url,
            future=True,
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
        )
        try:
            with admin_engine.connect() as connection:
                connection.execute(
                    text("SELECT pg_advisory_lock(hashtext(:name))"),
                    {"name": database_name},
                )
                try:
                    exists = connection.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :name"),
                        {"name": database_name},
                    ).scalar_one_or_none()
                    if exists is None:
                        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
                finally:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(hashtext(:name))"),
                        {"name": database_name},
                    )
        finally:
            admin_engine.dispose()

    def _engine_for(self, binding: RelationalStorageBinding, owner_ref: str) -> Engine:
        binding.assert_owner(owner_ref)
        if binding.provider_id != self.capabilities.provider_id:
            raise RelationalStorageIsolationError("binding belongs to another provider")
        with self._lock:
            engine = self._engines.get(binding.binding_id)
        if engine is None:
            raise RelationalStorageIsolationError(
                "binding is not registered in this provider process; acquire it again"
            )
        return engine

    @contextmanager
    def transaction(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> Iterator[Connection]:
        engine = self._engine_for(binding, owner_ref)
        with engine.begin() as connection:
            yield connection

    def health(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> Mapping[str, Any]:
        try:
            with self.transaction(binding, owner_ref=owner_ref) as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            return {
                "ok": False,
                "provider_id": self.capabilities.provider_id,
                "binding_id": binding.binding_id,
                "error": type(exc).__name__,
            }
        return {
            "ok": True,
            "provider_id": self.capabilities.provider_id,
            "binding_id": binding.binding_id,
        }

    def destroy_for_testing(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> None:
        """Drop an adapter-test database after exact owner/binding validation."""

        binding.assert_owner(owner_ref)
        if binding.provider_id != self.capabilities.provider_id:
            raise RelationalStorageIsolationError("binding belongs to another provider")
        with self._lock:
            target_url = self._targets.get(binding.binding_id)
            engine = self._engines.pop(binding.binding_id, None)
        if target_url is None:
            raise RelationalStorageIsolationError("binding is not registered")
        if engine is not None:
            engine.dispose()
        database_name = str(target_url.database or "")
        expected_name = self._database_name(binding.owner_ref, binding.logical_name)
        if (
            database_name != expected_name
            or not database_name.startswith("adaos_")
            or not _POSTGRES_NAME_RE.fullmatch(database_name)
        ):
            raise RelationalStorageIsolationError("refusing to drop an unexpected database name")
        admin_engine = create_engine(
            self._admin_url,
            future=True,
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
        )
        try:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name AND pid <> pg_backend_pid()"
                    ),
                    {"name": database_name},
                )
                connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            admin_engine.dispose()
        with self._lock:
            self._targets.pop(binding.binding_id, None)


__all__ = ["PostgreSQLRelationalStorageProvider", "SQLiteRelationalStorageProvider"]
