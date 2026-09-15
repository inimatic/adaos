"""Private, checksum-pinned SQLite staging for a fenced Application cutover.

Runtime/owner admission and quiescence belong to the transition coordinator. This
adapter never consumes real records as model input or copies a live WAL file.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from adaos.domain.relational_storage import RelationalMigration
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock, replace_with_retry


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _read(path: Path):
    return closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1))


def _integrity(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity verification failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("SQLite foreign-key verification failed")


def _backup(source: Path, target: Path) -> None:
    started = time.monotonic()

    def progress(_status, remaining, total):
        if time.monotonic() - started > 120:
            raise TimeoutError("SQLite backup did not converge within 120 seconds")
        if page_size * total > 512 * 1024 * 1024:
            raise ValueError("SQLite snapshot exceeds 512 MiB")

    with _read(source) as original, closing(sqlite3.connect(target, timeout=1)) as copied:
        page_size = original.execute("PRAGMA page_size").fetchone()[0]
        if original.execute("PRAGMA page_count").fetchone()[0] * page_size > 512 * 1024 * 1024:
            raise ValueError("SQLite snapshot exceeds 512 MiB")
        _integrity(original)
        original.backup(copied, pages=256, progress=progress, sleep=0.01)
        _integrity(copied)
    with target.open("r+b") as stream:
        os.fsync(stream.fileno())


class SQLiteDataTransition:
    def __init__(self, private_root: Path):
        self.root = private_root.resolve()

    def _path(self, path: Path) -> Path:
        path = path.absolute()
        resolved = path.resolve()
        if path != resolved or not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ValueError("SQLite transition path escaped its private owner root or used a link")
        return resolved

    def snapshot(self, source: Path, destination: Path, *, operation_key: str) -> dict:
        """Idempotent consistent snapshot, including committed WAL records."""
        source, destination = self._path(source), self._path(destination)
        if source == destination or not source.is_file():
            raise ValueError("Snapshot requires distinct existing source and private destination")
        identity = {"kind": "snapshot", "operation_key": operation_key,
                    "source": str(source.relative_to(self.root))}
        return self._build(destination, identity, lambda target: _backup(source, target))

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
            _backup(snapshot, target)
            with closing(sqlite3.connect(target, isolation_level=None)) as connection:
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16 * 1024 * 1024)
                connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1024 * 1024)
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                connection.execute(f"PRAGMA max_page_count={512 * 1024 * 1024 // page_size}")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS adaos_schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL)")
                history = dict(connection.execute("SELECT version, checksum FROM adaos_schema_migrations"))
                declared = {item.version: item.checksum for item in ordered}
                if any(declared.get(version) != checksum for version, checksum in history.items()):
                    raise ValueError("Migration history differs from the immutable full migration chain")
                started = time.monotonic()
                connection.set_progress_handler(lambda: int(time.monotonic() - started > 120), 10000)
                for item in ordered:
                    if item.version in history:
                        continue
                    if history and item.version < max(history):
                        raise ValueError("Cannot insert a migration before the installed schema version")
                    connection.set_authorizer(self._authorize)
                    try:
                        for statement in item.statements:
                            connection.execute(statement)
                    finally:
                        connection.set_authorizer(None)
                    connection.execute("INSERT INTO adaos_schema_migrations VALUES (?,?,?)", (item.version, item.name, item.checksum))
                _integrity(connection)
                connection.commit()
        return self._build(destination, identity, build)

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
                pending.unlink()
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
                replace_with_retry(temporary, destination)
                atomic_write_json(metadata, receipt)
                pending.unlink()
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
        _backup(staged, target)
        return {"ok": True, "staged_digest": staged_digest}
