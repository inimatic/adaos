"""Private, checksum-pinned SQLite staging for a fenced Application cutover.

Runtime/owner admission and quiescence belong to the transition coordinator. This
adapter never consumes real records as model input or copies a live WAL file.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Callable
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid

from adaos.domain.relational_storage import RelationalMigration
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock, replace_with_retry, sync_directory


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _read(path: Path):
    return closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1))


@dataclass(frozen=True)
class SQLiteTransitionPolicy:
    """Internal, owner-admitted limits; never accept these from a tool caller.

    A large-library profile must separately admit aggregate growth, mount
    identity and free-space floors. Raising a per-database limit is not such
    admission. Defaults retain the existing small-store envelope.
    """

    max_database_bytes: int = 512 * 1024 * 1024
    operation_timeout_seconds: float = 120

    def __post_init__(self):
        if (type(self.max_database_bytes) is not int
                or not 4096 <= self.max_database_bytes <= 4 * 1024**3):
            raise ValueError("SQLite database limit must be between 4 KiB and 4 GiB")
        if (isinstance(self.operation_timeout_seconds, bool)
                or not isinstance(self.operation_timeout_seconds, (int, float))
                or not math.isfinite(self.operation_timeout_seconds)
                or not 0 < self.operation_timeout_seconds <= 1800):
            raise ValueError("SQLite operation deadline must be finite and within 1800 seconds")


DEFAULT_POLICY = SQLiteTransitionPolicy()


def _sync_metadata(path: Path) -> None:
    if os.name == 'posix' and not sync_directory(path.parent):
        raise OSError('SQLite transition metadata could not be persisted')


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("SQLite transition operation exceeded its admitted deadline")


def _integrity(connection: sqlite3.Connection, *, deadline: float | None = None) -> None:
    deadline = deadline if deadline is not None else time.monotonic() + DEFAULT_POLICY.operation_timeout_seconds
    _check_deadline(deadline)
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
    try:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity verification failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("SQLite foreign-key verification failed")
        _check_deadline(deadline)
    except sqlite3.OperationalError as exc:
        if time.monotonic() >= deadline:
            raise TimeoutError("SQLite integrity verification exceeded its admitted deadline") from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)


def _backup(source: Path, target: Path, *, policy: SQLiteTransitionPolicy = DEFAULT_POLICY,
            deadline: float | None = None, admit_write: Callable[[Path, int], None] | None = None) -> None:
    deadline = deadline if deadline is not None else time.monotonic() + policy.operation_timeout_seconds

    def progress(_status, remaining, total):
        _check_deadline(deadline)
        if page_size * total > policy.max_database_bytes:
            raise ValueError("SQLite snapshot exceeds its admitted database byte limit")
        if admit_write is not None and total > admitted_pages:
            raise ValueError('SQLite source grew beyond its admitted backup envelope; re-admit the transition')

    with _read(source) as original:
        page_size = original.execute("PRAGMA page_size").fetchone()[0]
        admitted_pages = original.execute('PRAGMA page_count').fetchone()[0]
        _check_deadline(deadline)
        if admitted_pages * page_size > policy.max_database_bytes:
            raise ValueError("SQLite snapshot exceeds its admitted database byte limit")
        if admit_write is not None:
            admit_write(target, _backup_write_envelope(target, admitted_pages * page_size, page_size))
        _integrity(original, deadline=deadline)
        with closing(sqlite3.connect(target, timeout=1)) as copied:
            original.backup(copied, pages=256, progress=progress, sleep=0.01)
            _integrity(copied, deadline=deadline)
        if admit_write is not None:
            admit_write(target, 0)
    with target.open("r+b") as stream:
        os.fsync(stream.fileno())


def _backup_write_envelope(target: Path, source_bytes: int, page_size: int) -> int:
    """Charge target growth and transaction sidecars before opening a writer.

    Existing WAL targets can write a full frame per copied source page. Legacy
    rollback-journal targets conservatively retain twice their old extent;
    neither case may be admitted as a zero-growth overwrite. Reading the fixed
    format header avoids opening/recovering the target database in preflight.
    """
    old_bytes = target.stat().st_size if target.exists() else 0
    if not old_bytes:
        return source_bytes + 65536
    with target.open('rb') as stream:
        header = stream.read(100)
    if len(header) != 100 or header[:16] != b'SQLite format 3\0':
        raise ValueError('SQLite backup target is not a recognized database')
    if header[18:20] == b'\x02\x02':
        sidecar = source_bytes + ((source_bytes + page_size - 1) // page_size) * 24 + 32
    else:
        sidecar = 2 * old_bytes
    return max(0, source_bytes - old_bytes) + sidecar + 65536


def initialize_sqlite_schema(path: Path, migrations: Sequence[RelationalMigration], *, development: bool = False) -> dict:
    """Bootstrap an empty store or verify its exact ledger; installed upgrades require cutover."""
    ordered = sorted(migrations, key=lambda item: item.version)
    if len({item.version for item in ordered}) != len(ordered) or any("sqlite" not in item.dialects for item in ordered):
        raise ValueError("Migration versions must be unique and support SQLite")
    with closing(sqlite3.connect(path, isolation_level=None, timeout=5)) as connection:
        return _apply_migrations(connection, ordered, allow_existing=development)


def _apply_migrations(connection: sqlite3.Connection, ordered: Sequence[RelationalMigration], *, allow_existing: bool,
                      deadline: float | None = None) -> dict:
    deadline = deadline if deadline is not None else time.monotonic() + DEFAULT_POLICY.operation_timeout_seconds
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16 * 1024 * 1024)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1024 * 1024)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN IMMEDIATE")
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        history = dict(connection.execute("SELECT version, checksum FROM adaos_schema_migrations")) if "adaos_schema_migrations" in tables else {}
        declared = {item.version: item.checksum for item in ordered}
        if any(declared.get(version) != checksum for version, checksum in history.items()):
            raise ValueError("Migration history differs from the immutable full migration chain")
        pending = [item for item in ordered if item.version not in history]
        if pending and history and min(item.version for item in pending) < max(history):
            raise ValueError("Cannot insert a migration before the installed schema version")
        if pending and tables - {"adaos_schema_migrations", "sqlite_sequence"} and not allow_existing:
            raise ValueError("Existing installed data requires the fenced Beta migration, not runtime initialization")
        connection.execute("CREATE TABLE IF NOT EXISTS adaos_schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL)")
        _check_deadline(deadline)
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
        for item in pending:
            for statement in item.statements:
                _check_deadline(deadline)
                connection.set_authorizer(SQLiteDataTransition._statement_authorizer())
                try:
                    connection.execute(statement)
                finally:
                    connection.set_authorizer(None)
            # Relational SDK ledgers also carry these required metadata
            # columns. Preserve their immutable checksums and satisfy that
            # existing shape when staging a later Application migration.
            ledger_columns = {row[1] for row in connection.execute("PRAGMA table_info(adaos_schema_migrations)")}
            fields = ["version", "name", "checksum"]
            values = [item.version, item.name, item.checksum]
            for field, value in (("applied_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), ("staged", 0)):
                if field in ledger_columns:
                    fields.append(field)
                    values.append(value)
            connection.execute(f"INSERT INTO adaos_schema_migrations ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", values)
        if pending:
            _integrity(connection, deadline=deadline)
        _check_deadline(deadline)
        connection.commit()
        return {"schema_version": max(declared, default=0), "applied_versions": [item.version for item in pending]}
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.set_authorizer(None)
        connection.set_progress_handler(None, 0)


class SQLiteDataTransition:
    def __init__(self, private_root: Path, *, policy: SQLiteTransitionPolicy = DEFAULT_POLICY,
                 admit_write: Callable[[Path, int], None] | None = None):
        self.root = private_root.resolve()
        self.policy = policy
        self.admit_write = admit_write

    def _path(self, path: Path) -> Path:
        path = path.absolute()
        resolved = path.resolve()
        if path != resolved or not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ValueError("SQLite transition path escaped its private owner root or used a link")
        if self.admit_write is not None:
            self.admit_write(resolved, 0)
        return resolved

    def snapshot(self, source: Path, destination: Path, *, operation_key: str) -> dict:
        """Idempotent consistent snapshot, including committed WAL records."""
        source, destination = self._path(source), self._path(destination)
        if source == destination or not source.is_file():
            raise ValueError("Snapshot requires distinct existing source and private destination")
        identity = {"kind": "snapshot", "operation_key": operation_key,
                    "source": str(source.relative_to(self.root))}
        return self._build(destination, identity, lambda target: _backup(source, target, policy=self.policy, admit_write=self.admit_write))

    def migrate(self, snapshot: Path, destination: Path, *, snapshot_digest: str,
                migrations: Sequence[RelationalMigration], operation_key: str) -> dict:
        snapshot, destination = self._path(snapshot), self._path(destination)
        if snapshot == destination or _hash(snapshot) != snapshot_digest:
            raise ValueError("Migration snapshot identity mismatch")
        ordered = sorted(migrations, key=lambda item: item.version)
        if len({item.version for item in ordered}) != len(ordered) or any("sqlite" not in item.dialects for item in ordered):
            raise ValueError("Migration versions must be unique and support SQLite")
        identity = {"kind": "forward_migration", "operation_key": operation_key,
                    "snapshot_digest": snapshot_digest, "migrations": [item.checksum for item in ordered]}

        def build(target):
            deadline = time.monotonic() + self.policy.operation_timeout_seconds
            _backup(snapshot, target, policy=self.policy, deadline=deadline, admit_write=self.admit_write)
            if self.admit_write is not None:
                # A declared migration may grow to its cap and needs rollback
                # journal space as well; do not infer safety from source size.
                self.admit_write(target, self.policy.max_database_bytes)
            with closing(sqlite3.connect(target, isolation_level=None)) as connection:
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                connection.execute(f"PRAGMA max_page_count={self.policy.max_database_bytes // page_size}")
                _apply_migrations(connection, ordered, allow_existing=True, deadline=deadline)
            if self.admit_write is not None:
                self.admit_write(target, 0)
        return self._build(destination, identity, build)

    def verify_unmigrated_snapshot(self, snapshot: Path, *, snapshot_digest: str) -> dict:
        """Reuse an immutable snapshot only for an explicitly empty migration chain.

        The caller still installs an independent writable Beta database. Never
        alias a live database, create a hard link, or omit an installed ledger.
        """
        snapshot = self._path(snapshot)
        if _hash(snapshot) != snapshot_digest:
            raise ValueError("Migration snapshot identity mismatch")
        with _read(snapshot) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "adaos_schema_migrations" in tables and connection.execute(
                "SELECT version, checksum FROM adaos_schema_migrations LIMIT 1"
            ).fetchone() is not None:
                raise ValueError("Migration history differs from the immutable full migration chain")
        return {"ok": True, "digest": snapshot_digest, "bytes": snapshot.stat().st_size,
                "mode": "verified_empty_migration_chain"}

    def verify_snapshot_unchanged(self, source: Path, baseline: Path, destination: Path,
                                  *, baseline_digest: str, operation_key: str) -> dict:
        """Retain a durable equality proof instead of a second identical backup.

        The consistent validation snapshot is still required, including WAL.
        Only this operation's duplicate is retired after a matching immutable
        baseline and a durable proof exist. A changed source is retained for
        diagnosis. Replays after adoption use the proof, not the now-adopted DB.
        """
        source, baseline, destination = (self._path(path) for path in (source, baseline, destination))
        if len({source, baseline, destination}) != 3 or _hash(baseline) != baseline_digest:
            raise ValueError("Stable verification baseline identity mismatch")
        if not operation_key or len(operation_key) > 256:
            raise ValueError("A bounded operation key is required")
        identity = {"kind": "unchanged_snapshot", "operation_key": operation_key,
                    "source": str(source.relative_to(self.root)),
                    "baseline": str(baseline.relative_to(self.root)), "digest": baseline_digest}
        proof_path = destination.with_suffix(destination.suffix + ".unchanged.json")
        with mutation_lock(proof_path.with_suffix(".lock")):
            if proof_path.exists():
                proof = json.loads(proof_path.read_text(encoding="utf-8"))
                if proof.get("identity") != identity or proof.get("digest") != baseline_digest:
                    raise ValueError("Retained SQLite equality proof changed")
            else:
                checked = self.snapshot(source, destination, operation_key=operation_key)
                if checked["digest"] != baseline_digest:
                    raise ValueError("Stable data changed while Beta was selected; explicit reconciliation required")
                proof = {"ok": True, "identity": identity, "digest": baseline_digest,
                         "bytes": checked["bytes"], "mode": "verified_identical_snapshot"}
                atomic_write_json(proof_path, proof)
            _sync_metadata(proof_path)
            self._retire_verified_duplicate(destination, source, operation_key, baseline_digest)
            return proof

    def _retire_verified_duplicate(self, destination: Path, source: Path,
                                   operation_key: str, digest: str) -> None:
        metadata = destination.with_suffix(destination.suffix + ".receipt.json")
        with mutation_lock(metadata.with_suffix(".lock")):
            if not destination.exists() and not metadata.exists():
                return
            receipt = json.loads(metadata.read_text(encoding="utf-8"))
            identity = {"kind": "snapshot", "operation_key": operation_key,
                        "source": str(source.relative_to(self.root))}
            if receipt.get("identity") != identity or receipt.get("digest") != digest:
                raise ValueError("SQLite validation snapshot cannot be retired without exact ownership")
            if destination.exists():
                if _hash(destination) != digest:
                    raise ValueError("SQLite validation snapshot changed before retirement")
                destination.unlink()
                _sync_metadata(destination)
            metadata.unlink()
            _sync_metadata(metadata)

    @staticmethod
    def _statement_authorizer():
        altered = None

        def authorize(action, first, second, database, trigger):
            nonlocal altered
            verdict = SQLiteDataTransition._authorize(action, first, second, database, trigger)
            if action == sqlite3.SQLITE_ALTER_TABLE and verdict == sqlite3.SQLITE_OK:
                altered = (first, second)
            # SQLite validates ADD COLUMN constraints through an internal
            # table-valued quick_check. Authorize only the current ALTER target;
            # a new statement gets a fresh authorizer with no inherited grant.
            if (action == sqlite3.SQLITE_PRAGMA and first == "quick_check"
                    and altered == (database, second) and trigger is None):
                return sqlite3.SQLITE_OK
            return verdict

        return authorize

    @staticmethod
    def _authorize(action, arg1, arg2, _database, _trigger):
        denied = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_TRANSACTION,
                  sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_PRAGMA, sqlite3.SQLITE_CREATE_VTABLE,
                  sqlite3.SQLITE_DROP_VTABLE}
        if action in denied or (action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in {"load_extension", "readfile", "writefile"}):
            return sqlite3.SQLITE_DENY
        if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                      sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE} and "adaos_schema_migrations" in {arg1, arg2}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _build(self, destination, identity, build):
        if not identity.get("operation_key") or len(identity["operation_key"]) > 256:
            raise ValueError("A bounded operation key is required")
        metadata = destination.with_suffix(destination.suffix + ".receipt.json")
        pending = metadata.with_suffix(".pending.json")
        with mutation_lock(metadata.with_suffix(".lock")):
            if metadata.exists():
                receipt = json.loads(metadata.read_text(encoding="utf-8"))
                if receipt["identity"] != identity or not destination.is_file() or _hash(destination) != receipt["digest"]:
                    raise ValueError("Retained SQLite staging evidence changed")
                _sync_metadata(metadata)
                return receipt
            if pending.exists():
                saved = json.loads(pending.read_text(encoding="utf-8"))
                receipt = saved["receipt"]
                if receipt["identity"] != identity:
                    raise ValueError("Pending SQLite staging intent changed")
                temporary = self._path(destination.parent / saved["temporary"])
                if temporary.parent != destination.parent or not temporary.name.startswith(f".{destination.name}."):
                    raise ValueError("Pending staging path is invalid")
                if destination.exists():
                    if _hash(destination) != receipt["digest"]:
                        raise ValueError("Pending SQLite destination changed")
                else:
                    if _hash(temporary) != receipt["digest"]:
                        raise ValueError("Pending SQLite staging content changed")
                    replace_with_retry(temporary, destination)
                atomic_write_json(metadata, receipt)
                _sync_metadata(metadata)
                pending.unlink()
                _sync_metadata(pending)
                return receipt
            if destination.exists():
                raise ValueError("Unrecognized SQLite destination requires explicit recovery")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.staging")
            started = time.monotonic()
            try:
                build(temporary)
                with temporary.open("r+b") as stream:
                    os.fsync(stream.fileno())
                receipt = {"ok": True, "identity": identity, "digest": _hash(temporary), "bytes": temporary.stat().st_size,
                           "duration_ms": round((time.monotonic() - started) * 1000, 3)}
                atomic_write_json(pending, {"temporary": temporary.name, "receipt": receipt})
                _sync_metadata(pending)
                replace_with_retry(temporary, destination)
                atomic_write_json(metadata, receipt)
                _sync_metadata(metadata)
                pending.unlink()
                _sync_metadata(pending)
                return receipt
            finally:
                if not pending.exists():
                    temporary.unlink(missing_ok=True)

    def install(self, staged: Path, target: Path, *, staged_digest: str) -> dict:
        """Owner must keep target fenced, including after this effect returns.

        SQLite backup commits its destination atomically and handles any existing
        WAL; filesystem replacement of the main file alone would be unsafe.
        The coordinator must retain target's pre-cutover snapshot separately.
        """
        staged, target = self._path(staged), self._path(target)
        if staged == target or _hash(staged) != staged_digest:
            raise ValueError("Staged SQLite identity mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        _backup(staged, target, policy=self.policy, admit_write=self.admit_write)
        return {"ok": True, "staged_digest": staged_digest}
