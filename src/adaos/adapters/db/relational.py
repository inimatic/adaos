"""Relational capability providers for SQLite and PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine, URL, make_url

from adaos.domain.relational_storage import (
    RelationalBackup,
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
            backup_restore=True,
            localities=("node",),
            isolation="file",
            transaction_levels=("atomic", "serializable"),
            capacity_enforcement=True,
            retention_policies=("retain", "delete_on_uninstall"),
            migration_rollback=("transaction", "restore"),
        )
        self._targets: dict[str, Path] = {}
        self._engines: dict[str, Engine] = {}
        self._backups: dict[str, Path] = {}
        self._requirements: dict[str, RelationalStorageRequirements] = {}
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
            requirements=requirements.to_dict(),
        )
        with self._lock:
            existing = self._targets.get(binding_id)
            if existing is not None and existing != target:
                raise RelationalStorageIsolationError(
                    "binding id resolved to a different owner-scoped SQLite target"
                )
            self._targets[binding_id] = target
            self._requirements[binding_id] = requirements
            engine = self._engines.get(binding_id)
            if engine is None:
                engine = self._create_engine(target)
                self._engines[binding_id] = engine
        with engine.begin() as connection:
            connection.execute(text("SELECT 1"))
            if requirements.capacity_bytes is not None:
                page_size = int(connection.exec_driver_sql("PRAGMA page_size").scalar_one())
                max_pages = max(1, int(requirements.capacity_bytes) // page_size)
                connection.exec_driver_sql(f"PRAGMA max_page_count={max_pages}")
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
            "backup_restore": True,
            "retention": list(self.capabilities.retention_policies),
        }

    def backup(self, binding: RelationalStorageBinding, *, owner_ref: str) -> RelationalBackup:
        engine = self._engine_for(binding, owner_ref)
        with self._lock:
            target = self._targets[binding.binding_id]
            backup_id = f"relbackup.{hashlib.sha256(f'{binding.binding_id}:{time.time_ns()}'.encode()).hexdigest()}"
            destination = target.parent / "backups" / f"{backup_id}.sqlite"
            destination.parent.mkdir(parents=True, exist_ok=True)
            with engine.connect() as connection:
                raw = connection.connection.driver_connection
                import sqlite3

                backup_connection = sqlite3.connect(destination)
                try:
                    raw.backup(backup_connection)
                finally:
                    backup_connection.close()
            digest = f"sha256:{hashlib.sha256(destination.read_bytes()).hexdigest()}"
            self._backups[backup_id] = destination
        return RelationalBackup(
            backup_id=backup_id,
            binding_id=binding.binding_id,
            owner_ref=binding.owner_ref,
            provider_id=self.capabilities.provider_id,
            locator=f"skill-data:db/backups/{backup_id}.sqlite",
            digest=digest,
        )

    def restore(
        self,
        binding: RelationalStorageBinding,
        backup: RelationalBackup,
        *,
        owner_ref: str,
    ) -> None:
        engine = self._engine_for(binding, owner_ref)
        if backup.owner_ref != binding.owner_ref or backup.binding_id != binding.binding_id:
            raise RelationalStorageIsolationError("backup does not belong to this binding")
        with self._lock:
            source = self._backups.get(backup.backup_id)
            target = self._targets[binding.binding_id]
            if source is None or not source.exists():
                raise FileNotFoundError(f"relational backup not found: {backup.backup_id}")
            if f"sha256:{hashlib.sha256(source.read_bytes()).hexdigest()}" != backup.digest:
                raise RelationalStorageCapabilityError("relational backup digest mismatch")
            engine.dispose()
            temporary = target.with_name(f".{target.name}.{os.getpid()}.restore")
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
            self._engines[binding.binding_id] = self._create_engine(target)

    def delete(self, binding: RelationalStorageBinding, *, owner_ref: str, reason: str) -> None:
        self._engine_for(binding, owner_ref)
        if not str(reason or "").strip():
            raise ValueError("deletion reason is required")
        with self._lock:
            requirements = self._requirements.get(binding.binding_id)
            if requirements is None:
                raise RelationalStorageIsolationError("binding is not registered")
            if requirements.retention_policy == "retain":
                raise RelationalStorageCapabilityError(
                    "binding retention policy is retain; deletion is not admitted"
                )
            target = self._targets.pop(binding.binding_id)
            engine = self._engines.pop(binding.binding_id)
            self._requirements.pop(binding.binding_id, None)
            engine.dispose()
            target.unlink(missing_ok=True)

    def target_path_for_testing(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> Path:
        """Return the private target for adapter tests, never for the SDK."""

        self._engine_for(binding, owner_ref)
        return self._targets[binding.binding_id]

    def service_uri(self, binding: RelationalStorageBinding, *, owner_ref: str) -> str:
        """Return the private SQLAlchemy URI to the core service supervisor only."""

        self._engine_for(binding, owner_ref)
        with self._lock:
            target = self._targets[binding.binding_id]
        return f"sqlite:///{target.as_posix()}"


_POSTGRES_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class PostgreSQLRelationalStorageProvider:
    """PostgreSQL provider using one isolated logical database per binding.

    The administrator URL remains inside the adapter. Skills receive only an
    opaque AdaOS binding and cannot select a database or obtain credentials.
    """

    def __init__(
        self,
        admin_url: str,
        *,
        secret_ref: str = "core:storage/postgresql",
        pool_size: int = 5,
        max_overflow: int = 5,
    ) -> None:
        url = make_url(str(admin_url or "").strip())
        if not url.drivername.startswith("postgresql"):
            raise ValueError("PostgreSQL provider requires a postgresql SQLAlchemy URL")
        if not url.database:
            url = url.set(database="postgres")
        self._admin_url = url
        self._secret_ref = str(secret_ref or "").strip() or "core:storage/postgresql"
        self._pool_size = max(1, int(pool_size))
        self._max_overflow = max(0, int(max_overflow))
        self._capabilities = RelationalProviderCapabilities(
            provider_id="postgresql",
            durability=("durable", "ephemeral"),
            transactions=True,
            max_concurrent_writers=None,
            json=True,
            backup_restore=True,
            localities=("network",),
            isolation="database",
            transaction_levels=("atomic", "serializable"),
            retention_policies=("retain", "delete_on_uninstall"),
            migration_rollback=("transaction", "restore"),
            owner_roles=True,
            credential_rotation=True,
        )
        self._targets: dict[str, URL] = {}
        self._engines: dict[str, Engine] = {}
        self._roles: dict[str, str] = {}
        self._role_passwords: dict[str, str] = {}
        self._backups: dict[str, tuple[str, str]] = {}
        self._requirements: dict[str, RelationalStorageRequirements] = {}
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

    @staticmethod
    def _role_name(owner_ref: str) -> str:
        return f"adaos_owner_{hashlib.sha256(owner_ref.encode('utf-8')).hexdigest()[:24]}"

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
        role_name = self._role_name(owner)
        binding_id = _binding_id(self.capabilities.provider_id, owner, logical)
        with self._lock:
            role_password = self._role_passwords.setdefault(
                role_name,
                secrets.token_urlsafe(32),
            )
        self._ensure_database(database_name, role_name, role_password)
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
            requirements=requirements.to_dict(),
        )
        with self._lock:
            self._targets[binding_id] = target_url
            self._roles[binding_id] = role_name
            self._requirements[binding_id] = requirements
            if binding_id not in self._engines:
                self._engines[binding_id] = self._create_binding_engine(target_url, role_name)
        return binding

    def _create_binding_engine(self, target_url: URL, role_name: str) -> Engine:
        engine = create_engine(
            target_url,
            future=True,
            pool_pre_ping=True,
            pool_size=self._pool_size,
            max_overflow=self._max_overflow,
        )

        @event.listens_for(engine, "checkout")
        def _assume_owner_role(dbapi_connection: Any, *_: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f'SET ROLE "{role_name}"')
            finally:
                cursor.close()

        return engine

    def _ensure_database(self, database_name: str, role_name: str, role_password: str) -> None:
        admin_engine = create_engine(
            self._admin_url,
            future=True,
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
        )
        try:
            with admin_engine.connect() as connection:
                role_exists = connection.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
                    {"name": role_name},
                ).scalar_one_or_none()
                escaped_password = role_password.replace("'", "''")
                if role_exists is None:
                    connection.exec_driver_sql(
                        f'CREATE ROLE "{role_name}" LOGIN PASSWORD \'{escaped_password}\''
                    )
                else:
                    connection.exec_driver_sql(
                        f'ALTER ROLE "{role_name}" LOGIN PASSWORD \'{escaped_password}\''
                    )
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
                        connection.exec_driver_sql(
                            f'CREATE DATABASE "{database_name}" OWNER "{role_name}"'
                        )
                    else:
                        connection.exec_driver_sql(
                            f'ALTER DATABASE "{database_name}" OWNER TO "{role_name}"'
                        )
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
            "pool_size": self._pool_size,
            "max_overflow": self._max_overflow,
            "owner_role": True,
            "backup_restore": True,
        }

    def _admin_engine(self) -> Engine:
        return create_engine(
            self._admin_url,
            future=True,
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
        )

    def rotate_admin_credentials(self, admin_url: str) -> Mapping[str, Any]:
        """Hot-reload a core-owned administrator credential and binding pools.

        The secret reference remains stable and the URL never enters a public
        binding or status payload. The candidate credential is probed before
        any active engine is replaced.
        """

        candidate = make_url(str(admin_url or "").strip())
        if not candidate.drivername.startswith("postgresql"):
            raise ValueError("PostgreSQL provider requires a postgresql SQLAlchemy URL")
        if not candidate.database:
            candidate = candidate.set(database="postgres")
        probe = create_engine(
            candidate,
            future=True,
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
        )
        try:
            with probe.connect() as connection:
                server_version = str(
                    connection.execute(text("SHOW server_version_num")).scalar_one()
                )
                with self._lock:
                    database_names = tuple(
                        str(target.database or "") for target in self._targets.values()
                    )
                for database_name in database_names:
                    exists = connection.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :name"),
                        {"name": database_name},
                    ).scalar_one_or_none()
                    if exists is None:
                        raise RelationalStorageCapabilityError(
                            "credential rotation target cannot access an active binding database"
                        )
        finally:
            probe.dispose()

        with self._lock:
            old_engines = tuple(self._engines.values())
            self._admin_url = candidate
            for binding_id, target in tuple(self._targets.items()):
                rebound = candidate.set(database=target.database)
                self._targets[binding_id] = rebound
                self._engines[binding_id] = self._create_binding_engine(
                    rebound,
                    self._roles[binding_id],
                )
        for engine in old_engines:
            engine.dispose()
        return {
            "ok": True,
            "provider_id": self.capabilities.provider_id,
            "secret_ref": self._secret_ref,
            "bindings_rebound": len(database_names),
            "server_version_num": server_version,
        }

    def service_uri(self, binding: RelationalStorageBinding, *, owner_ref: str) -> str:
        """Return a least-privilege login URI to the core supervisor only."""

        self._engine_for(binding, owner_ref)
        with self._lock:
            target = self._targets[binding.binding_id]
            role = self._roles[binding.binding_id]
            password = self._role_passwords[role]
        return target.set(username=role, password=password).render_as_string(hide_password=False)

    def backup(self, binding: RelationalStorageBinding, *, owner_ref: str) -> RelationalBackup:
        self._engine_for(binding, owner_ref)
        with self._lock:
            target_url = self._targets[binding.binding_id]
            engine = self._engines[binding.binding_id]
            backup_id = f"relbackup.{hashlib.sha256(f'{binding.binding_id}:{time.time_ns()}'.encode()).hexdigest()}"
            backup_name = f"adaos_backup_{hashlib.sha256(backup_id.encode()).hexdigest()[:32]}"
            engine.dispose()
            admin = self._admin_engine()
            try:
                with admin.connect() as connection:
                    connection.execute(
                        text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name AND pid <> pg_backend_pid()"),
                        {"name": str(target_url.database)},
                    )
                    connection.exec_driver_sql(
                        f'CREATE DATABASE "{backup_name}" TEMPLATE "{target_url.database}"'
                    )
            finally:
                admin.dispose()
            self._backups[backup_id] = (binding.binding_id, backup_name)
        digest_source = f"{binding.binding_id}:{backup_name}:{binding.owner_ref}".encode("utf-8")
        return RelationalBackup(
            backup_id=backup_id,
            binding_id=binding.binding_id,
            owner_ref=binding.owner_ref,
            provider_id=self.capabilities.provider_id,
            locator=f"adaos-db-backup:{backup_id}",
            digest=f"sha256:{hashlib.sha256(digest_source).hexdigest()}",
        )

    def restore(
        self,
        binding: RelationalStorageBinding,
        backup: RelationalBackup,
        *,
        owner_ref: str,
    ) -> None:
        self._engine_for(binding, owner_ref)
        if backup.owner_ref != binding.owner_ref or backup.binding_id != binding.binding_id:
            raise RelationalStorageIsolationError("backup does not belong to this binding")
        with self._lock:
            backup_record = self._backups.get(backup.backup_id)
            target_url = self._targets[binding.binding_id]
            role_name = self._roles[binding.binding_id]
            if backup_record is None or backup_record[0] != binding.binding_id:
                raise FileNotFoundError(f"relational backup not found: {backup.backup_id}")
            backup_name = backup_record[1]
            expected = hashlib.sha256(
                f"{binding.binding_id}:{backup_name}:{binding.owner_ref}".encode("utf-8")
            ).hexdigest()
            if backup.digest != f"sha256:{expected}":
                raise RelationalStorageCapabilityError("relational backup digest mismatch")
            self._engines[binding.binding_id].dispose()
            admin = self._admin_engine()
            try:
                with admin.connect() as connection:
                    connection.execute(
                        text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name AND pid <> pg_backend_pid()"),
                        {"name": str(target_url.database)},
                    )
                    connection.exec_driver_sql(f'DROP DATABASE "{target_url.database}"')
                    connection.exec_driver_sql(
                        f'CREATE DATABASE "{target_url.database}" TEMPLATE "{backup_name}" OWNER "{role_name}"'
                    )
            finally:
                admin.dispose()
            self._engines[binding.binding_id] = self._create_binding_engine(target_url, role_name)

    def delete(self, binding: RelationalStorageBinding, *, owner_ref: str, reason: str) -> None:
        if not str(reason or "").strip():
            raise ValueError("deletion reason is required")
        self._engine_for(binding, owner_ref)
        with self._lock:
            requirements = self._requirements.get(binding.binding_id)
        if requirements is None:
            raise RelationalStorageIsolationError("binding is not registered")
        if requirements.retention_policy == "retain":
            raise RelationalStorageCapabilityError(
                "binding retention policy is retain; deletion is not admitted"
            )
        self.destroy_for_testing(binding, owner_ref=owner_ref)

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
            backup_names: list[str] = []
            for backup_id, (backup_binding_id, name) in list(self._backups.items()):
                if backup_binding_id == binding.binding_id:
                    backup_names.append(name)
                    self._backups.pop(backup_id, None)
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
                for backup_name in backup_names:
                    if not re.fullmatch(r"adaos_backup_[0-9a-f]{32}", backup_name):
                        raise RelationalStorageIsolationError(
                            "refusing to drop an unexpected backup database name"
                        )
                    connection.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :name AND pid <> pg_backend_pid()"
                        ),
                        {"name": backup_name},
                    )
                    connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{backup_name}"')
        finally:
            admin_engine.dispose()
        with self._lock:
            self._targets.pop(binding.binding_id, None)
            self._roles.pop(binding.binding_id, None)
            self._requirements.pop(binding.binding_id, None)


__all__ = ["PostgreSQLRelationalStorageProvider", "SQLiteRelationalStorageProvider"]
