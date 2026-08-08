"""Small synchronous YStore for the single Android desktop webspace."""

from __future__ import annotations

import hashlib
import json
import queue
import sqlite3
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, Iterable

import y_py as Y


_DEFAULT_MAX_SNAPSHOT_BYTES = 1024 * 1024
_SNAPSHOT_WARNING_BYTES = 2 * 1024 * 1024
_SNAPSHOT_CRITICAL_BYTES = 3 * 1024 * 1024


def _append_shared_value(target: Y.YArray, transaction: Any, value: Any) -> None:
    if isinstance(value, dict):
        child = Y.YMap({})
        target.append(transaction, child)
        for key in sorted(value):
            _set_shared_value(child, transaction, str(key), value[key])
        return
    if isinstance(value, list):
        child = Y.YArray()
        target.append(transaction, child)
        for item in value:
            _append_shared_value(child, transaction, item)
        return
    target.append(transaction, value)


def _set_shared_value(target: Y.YMap, transaction: Any, key: str, value: Any) -> None:
    if isinstance(value, dict):
        child = Y.YMap({})
        target.set(transaction, key, child)
        for child_key in sorted(value):
            _set_shared_value(child, transaction, str(child_key), value[child_key])
        return
    if isinstance(value, list):
        child = Y.YArray()
        target.set(transaction, key, child)
        for item in value:
            _append_shared_value(child, transaction, item)
        return
    target.set(transaction, key, value)


def _semantic_snapshot(document: Y.YDoc) -> dict[str, Any]:
    return {
        root_name: json.loads(document.get_map(root_name).to_json())
        for root_name in ("ui", "data", "registry", "runtime")
    }


def _rebuild_document(snapshot: dict[str, Any]) -> Y.YDoc:
    rebuilt = Y.YDoc()
    with rebuilt.begin_transaction() as transaction:
        for root_name in ("ui", "data", "registry", "runtime"):
            root = rebuilt.get_map(root_name)
            values = snapshot.get(root_name)
            if not isinstance(values, dict):
                continue
            for key in sorted(values):
                _set_shared_value(root, transaction, str(key), values[key])
    return rebuilt


class AndroidYStore:
    """Own one live YDoc and durably snapshot each accepted update in SQLite."""

    def __init__(
        self,
        path: Path,
        seed_update: bytes,
        *,
        webspace_id: str = "desktop",
        legacy_updates: Iterable[bytes] = (),
        max_updates: int = 512,
        max_update_bytes: int = 8 * 1024 * 1024,
        max_snapshot_bytes: int = _DEFAULT_MAX_SNAPSHOT_BYTES,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.webspace_id = str(webspace_id)
        self.max_updates = int(max_updates)
        self.max_update_bytes = int(max_update_bytes)
        self.max_snapshot_bytes = max(64 * 1024, int(max_snapshot_bytes))
        self._tasks: queue.Queue[tuple[str, tuple[Any, ...], Future[Any]] | None] = queue.Queue()
        self._owner = threading.Thread(
            target=self._run,
            name="adaos-android-yjs-owner",
            daemon=True,
        )
        self._owner.start()
        self._call("_initialize", bytes(seed_update), list(legacy_updates))

    def _run(self) -> None:
        while True:
            task = self._tasks.get()
            if task is None:
                return
            method_name, arguments, future = task
            try:
                result = getattr(self, method_name)(*arguments)
            except BaseException as error:
                future.set_exception(error)
            else:
                future.set_result(result)

    def _call(self, method_name: str, *arguments: Any) -> Any:
        if threading.current_thread() is self._owner:
            return getattr(self, method_name)(*arguments)
        if not self._owner.is_alive():
            raise RuntimeError("Android YStore owner thread is not running")
        future: Future[Any] = Future()
        self._tasks.put((method_name, arguments, future))
        return future.result(timeout=30)

    def _initialize(self, seed_update: bytes, legacy_updates: list[bytes]) -> None:
        self._compacted_on_startup = False
        self._compaction_total = 0
        self._last_compaction_at = 0.0
        self._last_compaction_reason = ""
        self._last_compaction_source_bytes = 0
        self._last_compaction_result_bytes = 0
        self.document = Y.YDoc()
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

        if seed_update:
            Y.apply_update(self.document, seed_update)
        row = self.connection.execute(
            "SELECT snapshot, state_vector, revision, generation "
            "FROM y_documents WHERE webspace_id = ?",
            (self.webspace_id,),
        ).fetchone()
        stored_vector = bytes(row[1]) if row else b""
        self.revision = int(row[2]) if row else 0
        self.generation = max(1, int(row[3])) if row else 1
        if row and row[0]:
            Y.apply_update(self.document, bytes(row[0]))

        migrated = 0
        for update in legacy_updates:
            if update and self._apply_to_document_locked(bytes(update)):
                migrated += 1

        stored_snapshot_bytes = len(bytes(row[0])) if row and row[0] else 0
        if stored_snapshot_bytes > self.max_snapshot_bytes:
            self._compacted_on_startup = self._structurally_compact_locked(
                reason="startup_snapshot_pressure"
            )

        current_vector = bytes(Y.encode_state_vector(self.document))
        if row is None or migrated or (
            current_vector != stored_vector and not self._compacted_on_startup
        ):
            self.revision += 1
            self._persist_snapshot_locked()

    def _create_schema(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS y_documents (
                    webspace_id TEXT PRIMARY KEY,
                    snapshot BLOB NOT NULL,
                    state_vector BLOB NOT NULL,
                    revision INTEGER NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                )
                """
            )
            columns = {
                str(row[1])
                for row in self.connection.execute("PRAGMA table_info(y_documents)")
            }
            if "generation" not in columns:
                self.connection.execute(
                    "ALTER TABLE y_documents ADD COLUMN generation INTEGER NOT NULL DEFAULT 1"
                )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS y_updates (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    webspace_id TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    update_blob BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(webspace_id, digest)
                )
                """
            )

    def _structurally_compact_locked(self, *, reason: str) -> bool:
        source = bytes(Y.encode_state_as_update(self.document))
        semantic = _semantic_snapshot(self.document)
        rebuilt = _rebuild_document(semantic)
        if _semantic_snapshot(rebuilt) != semantic:
            raise RuntimeError("android_yjs_structural_compaction_semantic_mismatch")
        compacted = bytes(Y.encode_state_as_update(rebuilt))
        if not compacted or len(compacted) >= len(source):
            return False

        previous = self.document
        self.document = rebuilt
        del previous
        self.generation += 1
        self.revision += 1
        self._compacted_on_startup = True
        self._compaction_total += 1
        self._last_compaction_at = time.time()
        self._last_compaction_reason = str(reason or "snapshot_pressure")
        self._last_compaction_source_bytes = len(source)
        self._last_compaction_result_bytes = len(compacted)
        self.connection.execute(
            "DELETE FROM y_updates WHERE webspace_id = ?",
            (self.webspace_id,),
        )
        self.connection.execute(
            """
            INSERT INTO y_updates(webspace_id, digest, update_blob, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                self.webspace_id,
                hashlib.sha256(compacted).hexdigest(),
                compacted,
                time.time(),
            ),
        )
        self._persist_snapshot_locked()
        return True

    def _apply_to_document_locked(self, update: bytes) -> bool:
        before = bytes(Y.encode_state_vector(self.document))
        Y.apply_update(self.document, update)
        return bytes(Y.encode_state_vector(self.document)) != before

    def _persist_snapshot_locked(self) -> None:
        snapshot = bytes(Y.encode_state_as_update(self.document))
        state_vector = bytes(Y.encode_state_vector(self.document))
        self.connection.execute(
            """
            INSERT INTO y_documents(
                webspace_id, snapshot, state_vector, revision, generation, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(webspace_id) DO UPDATE SET
                snapshot = excluded.snapshot,
                state_vector = excluded.state_vector,
                revision = excluded.revision,
                generation = excluded.generation,
                updated_at = excluded.updated_at
            """,
            (
                self.webspace_id,
                snapshot,
                state_vector,
                self.revision,
                self.generation,
                time.time(),
            ),
        )
        self.connection.commit()

    def _record_update_locked(self, update: bytes) -> None:
        digest = hashlib.sha256(update).hexdigest()
        self.connection.execute(
            """
            INSERT OR IGNORE INTO y_updates(webspace_id, digest, update_blob, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (self.webspace_id, digest, update, time.time()),
        )
        count, total = self.connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(LENGTH(update_blob)), 0)
            FROM y_updates WHERE webspace_id = ?
            """,
            (self.webspace_id,),
        ).fetchone()
        if int(count) > self.max_updates or int(total) > self.max_update_bytes:
            snapshot = bytes(Y.encode_state_as_update(self.document))
            self.connection.execute(
                "DELETE FROM y_updates WHERE webspace_id = ?",
                (self.webspace_id,),
            )
            self.connection.execute(
                """
                INSERT INTO y_updates(webspace_id, digest, update_blob, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    self.webspace_id,
                    hashlib.sha256(snapshot).hexdigest(),
                    snapshot,
                    time.time(),
                ),
            )

    def state_vector(self) -> bytes:
        return self._call("_state_vector")

    def _state_vector(self) -> bytes:
        return bytes(Y.encode_state_vector(self.document))

    def update_for_state_vector(self, state_vector: bytes) -> bytes:
        return self._call("_update_for_state_vector", bytes(state_vector))

    def _update_for_state_vector(self, state_vector: bytes) -> bytes:
        return bytes(Y.encode_state_as_update(self.document, state_vector))

    def full_update(self) -> bytes:
        return self._call("_full_update")

    def _full_update(self) -> bytes:
        return bytes(Y.encode_state_as_update(self.document))

    def apply_update(self, update: bytes) -> bool:
        payload = bytes(update)
        if not payload:
            return False
        return self._call("_apply_update", payload)

    def _apply_update(self, payload: bytes) -> bool:
        if not self._apply_to_document_locked(payload):
            return False
        self.revision += 1
        self._record_update_locked(payload)
        self._persist_snapshot_locked()
        return True

    def mutate(self, callback: Callable[[Y.YDoc, Any], None]) -> bytes:
        return self._call("_mutate", callback)

    def _mutate(self, callback: Callable[[Y.YDoc, Any], None]) -> bytes:
        before = bytes(Y.encode_state_vector(self.document))
        with self.document.begin_transaction() as transaction:
            callback(self.document, transaction)
        update = bytes(Y.encode_state_as_update(self.document, before))
        if update not in {b"", b"\x00\x00"}:
            self.revision += 1
            self._record_update_locked(update)
            self._persist_snapshot_locked()
        return update

    def snapshot_json(self) -> dict[str, Any]:
        return self._call("_snapshot_json")

    def _snapshot_json(self) -> dict[str, Any]:
        return _semantic_snapshot(self.document)

    def stats(self) -> dict[str, Any]:
        return self._call("_stats")

    def _stats(self) -> dict[str, Any]:
        count, total = self.connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(LENGTH(update_blob)), 0)
            FROM y_updates WHERE webspace_id = ?
            """,
            (self.webspace_id,),
        ).fetchone()
        snapshot_bytes = int(
            self.connection.execute(
                "SELECT COALESCE(LENGTH(snapshot), 0) FROM y_documents "
                "WHERE webspace_id = ?",
                (self.webspace_id,),
            ).fetchone()[0]
        )
        if snapshot_bytes >= _SNAPSHOT_CRITICAL_BYTES:
            pressure = "critical"
        elif snapshot_bytes >= _SNAPSHOT_WARNING_BYTES:
            pressure = "warning"
        else:
            pressure = "ready"
        return {
            "backend": "sqlite_snapshot_log",
            "path": str(self.path),
            "revision": self.revision,
            "generation": self.generation,
            "update_count": int(count),
            "update_bytes": int(total),
            "snapshot_bytes": snapshot_bytes,
            "snapshot_limit_bytes": self.max_snapshot_bytes,
            "snapshot_pressure": pressure,
            "state_vector_bytes": len(Y.encode_state_vector(self.document)),
            "compacted_on_startup": self._compacted_on_startup,
            "compaction_total": self._compaction_total,
            "last_compaction_at": self._last_compaction_at,
            "last_compaction_reason": self._last_compaction_reason,
            "last_compaction_source_bytes": self._last_compaction_source_bytes,
            "last_compaction_result_bytes": self._last_compaction_result_bytes,
        }

    def close(self) -> None:
        if not self._owner.is_alive():
            return
        try:
            self._call("_close")
        finally:
            self._tasks.put(None)
            self._owner.join(timeout=5)

    def _close(self) -> None:
        self._persist_snapshot_locked()
        self.connection.close()
        # y-py YDoc is deliberately !Send: its destructor must run on the
        # same thread which created it. Drop the last store-owned reference
        # before the owner thread exits instead of leaving it to the caller.
        document = self.document
        del self.document
        del document
