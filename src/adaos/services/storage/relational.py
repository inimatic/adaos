"""Capability broker and owner-guarded relational database facade."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from sqlalchemy import text

from adaos.adapters.db.relational import (
    PostgreSQLRelationalStorageProvider,
    SQLiteRelationalStorageProvider,
)
from adaos.domain.relational_storage import (
    RelationalBackup,
    RelationalMigration,
    RelationalMigrationResult,
    RelationalProviderCapabilities,
    RelationalStorageBinding,
    RelationalStorageCapabilityError,
    RelationalStorageIsolationError,
    RelationalStorageRequirements,
    validate_logical_name,
    validate_owner_ref,
)
from adaos.ports.relational_storage import RelationalStorageProvider
from adaos.services.skill.data_paths import resolve_skill_data_root


@dataclass(frozen=True, slots=True)
class RelationalResult:
    rowcount: int


class RelationalSession:
    """Small SQLAlchemy-backed SQL surface with named parameters only."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @staticmethod
    def _statement(statement: str) -> Any:
        value = str(statement or "").strip()
        if not value:
            raise ValueError("SQL statement must be non-empty")
        return text(value)

    @staticmethod
    def _parameters(parameters: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None) -> Any:
        if parameters is None:
            return {}
        if isinstance(parameters, Mapping):
            return dict(parameters)
        if isinstance(parameters, (str, bytes)):
            raise TypeError("SQL parameters must be a mapping or a sequence of mappings")
        return [dict(item) for item in parameters]

    def execute(
        self,
        statement: str,
        parameters: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    ) -> RelationalResult:
        result = self._connection.execute(
            self._statement(statement),
            self._parameters(parameters),
        )
        return RelationalResult(rowcount=max(int(result.rowcount or 0), 0))

    def fetch_one(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        result = self._connection.execute(
            self._statement(statement),
            self._parameters(parameters),
        )
        row = result.first()
        return dict(row._mapping) if row is not None else None

    def fetch_all(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        result = self._connection.execute(
            self._statement(statement),
            self._parameters(parameters),
        )
        return [dict(row._mapping) for row in result.fetchall()]

    def scalar(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> Any:
        result = self._connection.execute(
            self._statement(statement),
            self._parameters(parameters),
        )
        return result.scalar_one_or_none()


class RelationalStorageBroker:
    """Resolve requirements against registered providers without leaking DSNs."""

    def __init__(self, providers: Sequence[RelationalStorageProvider]) -> None:
        ordered = tuple(providers)
        if not ordered:
            raise ValueError("at least one relational provider is required")
        by_id: dict[str, RelationalStorageProvider] = {}
        for provider in ordered:
            provider_id = provider.capabilities.provider_id
            if provider_id in by_id:
                raise ValueError(f"duplicate relational provider id: {provider_id}")
            by_id[provider_id] = provider
        self._ordered = ordered
        self._by_id = by_id

    def provider_profiles(self) -> tuple[RelationalProviderCapabilities, ...]:
        return tuple(provider.capabilities for provider in self._ordered)

    def _candidates(
        self,
        requirements: RelationalStorageRequirements,
    ) -> tuple[RelationalStorageProvider, ...]:
        if not requirements.preferred_providers:
            return self._ordered
        unknown = [item for item in requirements.preferred_providers if item not in self._by_id]
        if unknown:
            raise RelationalStorageCapabilityError(
                f"unknown preferred relational providers: {', '.join(unknown)}"
            )
        return tuple(self._by_id[item] for item in requirements.preferred_providers)

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
        rejections: dict[str, tuple[str, ...]] = {}
        for provider in self._candidates(requirements):
            rejected = provider.capabilities.rejection_reasons(requirements)
            if rejected:
                rejections[provider.capabilities.provider_id] = rejected
                continue
            return provider.bind(
                owner_ref=owner,
                logical_name=logical,
                requirements=requirements,
                scope_root=scope_root,
            )
        detail = "; ".join(
            f"{provider_id}=[{', '.join(reasons)}]"
            for provider_id, reasons in rejections.items()
        ) or "no candidate providers"
        raise RelationalStorageCapabilityError(
            f"no relational provider satisfies the requested capability: {detail}"
        )

    def provider_for(self, binding: RelationalStorageBinding) -> RelationalStorageProvider:
        provider = self._by_id.get(binding.provider_id)
        if provider is None:
            raise RelationalStorageCapabilityError(
                f"binding provider is unavailable: {binding.provider_id}"
            )
        return provider

    def service_uri(self, binding: RelationalStorageBinding, *, owner_ref: str) -> str:
        """Resolve a process-only URI for a service component owned by the binding.

        This method deliberately lives below the skill SDK. The public binding
        remains opaque; only the core supervisor may inject the URI into the
        owning service process.
        """

        binding.assert_owner(owner_ref)
        provider = self.provider_for(binding)
        return provider.service_uri(binding, owner_ref=owner_ref)


def build_default_relational_storage_broker() -> RelationalStorageBroker:
    providers: list[RelationalStorageProvider] = [SQLiteRelationalStorageProvider()]
    postgres_url = str(os.getenv("ADAOS_RELATIONAL_POSTGRES_URL") or "").strip()
    if postgres_url:
        providers.append(PostgreSQLRelationalStorageProvider(postgres_url))
    return RelationalStorageBroker(providers)


def get_relational_storage_broker(ctx: Any) -> RelationalStorageBroker:
    broker = getattr(ctx, "relational_storage", None)
    if broker is None:
        broker = build_default_relational_storage_broker()
        object.__setattr__(ctx, "relational_storage", broker)
    return broker


class RelationalStorageService:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._broker = get_relational_storage_broker(ctx)

    def _current_owner(self) -> tuple[str, Any]:
        skill_ctx = getattr(self._ctx, "skill_ctx", None)
        current = skill_ctx.get() if skill_ctx is not None else None
        skill_name = str(getattr(current, "name", "") or "").strip()
        if not skill_name:
            raise RelationalStorageIsolationError(
                "storage.relational SDK access requires an active skill context"
            )
        return validate_owner_ref(f"skill:{skill_name}"), current

    def acquire_for_current_skill(
        self,
        logical_name: str = "main",
        *,
        requirements: RelationalStorageRequirements | None = None,
    ) -> "RelationalDatabase":
        owner_ref, current = self._current_owner()
        normalized = (requirements or RelationalStorageRequirements()).for_owner(owner_ref)
        data_root = resolve_skill_data_root(self._ctx, current)
        binding = self._broker.bind(
            owner_ref=owner_ref,
            logical_name=logical_name,
            requirements=normalized,
            scope_root=data_root / "db",
        )
        return RelationalDatabase(self, binding)

    def assert_current_owner(self, binding: RelationalStorageBinding) -> str:
        owner_ref, current = self._current_owner()
        binding.assert_owner(owner_ref)
        data_root = resolve_skill_data_root(self._ctx, current)
        self._broker.provider_for(binding).assert_scope(
            binding,
            owner_ref=owner_ref,
            scope_root=data_root / "db",
        )
        return owner_ref

    @contextmanager
    def transaction(self, binding: RelationalStorageBinding) -> Iterator[RelationalSession]:
        owner_ref = self.assert_current_owner(binding)
        provider = self._broker.provider_for(binding)
        with provider.transaction(binding, owner_ref=owner_ref) as connection:
            yield RelationalSession(connection)

    def health(self, binding: RelationalStorageBinding) -> Mapping[str, Any]:
        owner_ref = self.assert_current_owner(binding)
        provider = self._broker.provider_for(binding)
        return provider.health(binding, owner_ref=owner_ref)

    def backup(self, binding: RelationalStorageBinding) -> RelationalBackup:
        owner_ref = self.assert_current_owner(binding)
        return self._broker.provider_for(binding).backup(binding, owner_ref=owner_ref)

    def restore(self, binding: RelationalStorageBinding, backup: RelationalBackup) -> None:
        owner_ref = self.assert_current_owner(binding)
        self._broker.provider_for(binding).restore(
            binding,
            backup,
            owner_ref=owner_ref,
        )

    def migrate(
        self,
        binding: RelationalStorageBinding,
        migrations: Sequence[RelationalMigration],
        *,
        staged: bool,
    ) -> RelationalMigrationResult:
        owner_ref = self.assert_current_owner(binding)
        if binding.migration_owner != owner_ref:
            raise RelationalStorageIsolationError("current skill is not the migration owner")
        ordered = tuple(sorted(migrations, key=lambda item: item.version))
        if len({item.version for item in ordered}) != len(ordered):
            raise ValueError("migration versions must be unique")
        provider = self._broker.provider_for(binding)
        dialect = "postgresql" if binding.provider_id == "postgresql" else "sqlite"
        with provider.transaction(binding, owner_ref=owner_ref) as connection:
            session = RelationalSession(connection)
            session.execute(
                "CREATE TABLE IF NOT EXISTS adaos_schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
                "applied_at TEXT NOT NULL, staged INTEGER NOT NULL)"
            )
            existing = {
                int(row["version"]): row
                for row in session.fetch_all(
                    "SELECT version, name, checksum FROM adaos_schema_migrations ORDER BY version"
                )
            }
        pending: list[RelationalMigration] = []
        for migration in ordered:
            previous = existing.get(migration.version)
            if previous is not None:
                if str(previous["checksum"]) != migration.checksum:
                    raise ValueError(
                        f"migration {migration.version} checksum differs from applied history"
                    )
                continue
            if dialect not in migration.dialects:
                raise ValueError(f"migration {migration.version} does not support {dialect}")
            pending.append(migration)
        if not pending:
            return RelationalMigrationResult(
                binding_id=binding.binding_id,
                owner_ref=owner_ref,
                applied_versions=(),
                current_version=max(existing.keys(), default=0),
                staged=bool(staged),
            )
        applied: list[int] = []
        safety_backup = (
            provider.backup(binding, owner_ref=owner_ref)
            if binding.provider_id == "sqlite"
            else None
        )
        try:
            with provider.transaction(binding, owner_ref=owner_ref) as connection:
                session = RelationalSession(connection)
                for migration in pending:
                    for statement in migration.statements:
                        session.execute(statement)
                    session.execute(
                        "INSERT INTO adaos_schema_migrations"
                        "(version, name, checksum, applied_at, staged) "
                        "VALUES (:version, :name, :checksum, :applied_at, :staged)",
                        {
                            "version": migration.version,
                            "name": migration.name,
                            "checksum": migration.checksum,
                            "applied_at": datetime.now(timezone.utc).isoformat(),
                            "staged": int(bool(staged)),
                        },
                    )
                    applied.append(migration.version)
                current = max((*existing.keys(), *applied), default=0)
        except Exception:
            if safety_backup is not None:
                provider.restore(binding, safety_backup, owner_ref=owner_ref)
            raise
        return RelationalMigrationResult(
            binding_id=binding.binding_id,
            owner_ref=owner_ref,
            applied_versions=tuple(applied),
            current_version=current,
            staged=bool(staged),
        )


class RelationalDatabase:
    """Owner-guarded SDK-facing handle; it never exposes a DSN or engine."""

    def __init__(self, service: RelationalStorageService, binding: RelationalStorageBinding) -> None:
        self._service = service
        self._binding = binding

    @property
    def binding(self) -> RelationalStorageBinding:
        return self._binding

    @contextmanager
    def transaction(self) -> Iterator[RelationalSession]:
        with self._service.transaction(self._binding) as session:
            yield session

    def execute(
        self,
        statement: str,
        parameters: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    ) -> RelationalResult:
        with self.transaction() as session:
            return session.execute(statement, parameters)

    def fetch_one(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self.transaction() as session:
            return session.fetch_one(statement, parameters)

    def fetch_all(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        with self.transaction() as session:
            return session.fetch_all(statement, parameters)

    def scalar(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> Any:
        with self.transaction() as session:
            return session.scalar(statement, parameters)

    def health(self) -> Mapping[str, Any]:
        return self._service.health(self._binding)

    def migrate(
        self,
        migrations: Sequence[RelationalMigration],
        *,
        staged: bool = False,
    ) -> RelationalMigrationResult:
        return self._service.migrate(self._binding, migrations, staged=staged)

    def backup(self) -> RelationalBackup:
        return self._service.backup(self._binding)

    def restore(self, backup: RelationalBackup) -> None:
        self._service.restore(self._binding, backup)


__all__ = [
    "RelationalDatabase",
    "RelationalResult",
    "RelationalSession",
    "RelationalStorageBroker",
    "RelationalStorageService",
    "build_default_relational_storage_broker",
    "get_relational_storage_broker",
]
