"""One local Application channel; read leases span actual tool execution.

SQLite rollback-journal locks allow concurrent readers and fence channel writes
across processes. A caller timeout does not release the executing thread's lease.
"""

from __future__ import annotations

from contextlib import contextmanager, closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence

from adaos.domain.application import RuntimeSelection


class RuntimeChannelConflict(RuntimeError):
    pass


class ApplicationRuntimeChannel:
    def __init__(self, state_dir: Path, application_id: str):
        if not application_id:
            raise ValueError("Application identity is required")
        self.application_id = application_id
        self.path = Path(state_dir) / "applications/runtime_channels" / (
            hashlib.sha256(application_id.encode("utf-8")).hexdigest() + ".sqlite3")

    def _decode(self, raw: str) -> tuple[RuntimeSelection, ...]:
        document = json.loads(raw)
        if document.get("schema") != "adaos.application.runtime_channel.v1" or document.get("application_id") != self.application_id:
            raise RuntimeChannelConflict("Application channel identity mismatch")
        values = tuple(RuntimeSelection.from_mapping(item) for item in document["selections"])
        self._validate(values)
        return values

    def _validate(self, values: Sequence[RuntimeSelection]) -> None:
        if (any(item.application_id != self.application_id for item in values)
                or len({item.webspace_id for item in values}) != len(values)
                or len({(item.source, item.release_digest, item.runtime_root_ref) for item in values}) > 1):
            raise RuntimeChannelConflict("Conflicting Application channels require explicit reconciliation")

    def _encode(self, values: Sequence[RuntimeSelection]) -> str:
        self._validate(values)
        return json.dumps({"schema": "adaos.application.runtime_channel.v1", "application_id": self.application_id,
                           "selections": [item.to_dict() for item in values]}, ensure_ascii=False)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Do not queue a cutover behind unbounded application work.
        connection = sqlite3.connect(self.path, timeout=0.25, isolation_level=None)
        try:
            yield connection
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise RuntimeChannelConflict("Application is executing or changing channel; retry after it is idle") from exc
            raise
        finally:
            connection.close()

    def _initialize(self, connection: sqlite3.Connection, legacy: Sequence[RuntimeSelection]) -> None:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='channel'").fetchone()
        if exists:
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS channel (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO channel VALUES (1, ?)", (self._encode(legacy),))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def read(self) -> tuple[RuntimeSelection, ...] | None:
        if not self.path.is_file():
            return None
        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='channel'").fetchone()
            if not exists:
                return None
            row = connection.execute("SELECT document FROM channel WHERE id=1").fetchone()
            if row is None:
                raise RuntimeChannelConflict("Application channel record is missing")
            return self._decode(row[0])

    @classmethod
    def list_selections(cls, state_dir: Path, *, include_pending: bool = False) -> tuple[RuntimeSelection, ...]:
        values = []
        root = Path(state_dir) / "applications/runtime_channels"
        for path in root.glob("*.sqlite3"):
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as connection:
                    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='channel'").fetchone():
                        continue
                    row = connection.execute("SELECT document FROM channel WHERE id=1").fetchone()
                    if row is None:
                        raise RuntimeChannelConflict("Application channel record is missing")
                    identity = json.loads(row[0]).get("application_id")
                    channel = cls(state_dir, identity)
                    if channel.path.name != path.name:
                        raise RuntimeChannelConflict("Application channel path identity mismatch")
                    values.extend(channel._decode(row[0]))
                    if include_pending and connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='transitions'").fetchone():
                        for pending in cls._pending_transitions(connection):
                            target = RuntimeSelection.from_mapping(pending["intent"]["target"])
                            if target.application_id != identity:
                                raise RuntimeChannelConflict("Transition target owner differs from its channel")
                            values.append(target)
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise RuntimeChannelConflict("Application channel is changing; retry when it is idle") from exc
                raise
        return tuple(values)

    def select(self, value: RuntimeSelection, *, expected_revision: int,
               legacy: Sequence[RuntimeSelection] = ()) -> RuntimeSelection:
        from adaos.services.applications.store import ApplicationRevisionConflict

        with self._connection() as connection:
            self._initialize(connection, legacy)
            connection.execute("BEGIN EXCLUSIVE")
            self._assert_available(connection)
            values = self._decode(connection.execute("SELECT document FROM channel WHERE id=1").fetchone()[0])
            current = next((item for item in values if item.webspace_id == value.webspace_id), None)
            observed = current.revision if current else 0
            if observed != expected_revision or value.revision != observed + 1:
                raise ApplicationRevisionConflict(expected=expected_revision, observed=observed)
            target = (value.source, value.release_digest, value.runtime_root_ref)
            if current is None and values and target != (values[0].source, values[0].release_digest, values[0].runtime_root_ref):
                raise RuntimeChannelConflict("Another Webspace selected this Application channel; review it before switching")
            projected = []
            for item in values:
                if item.webspace_id == value.webspace_id:
                    continue
                if (item.source, item.release_digest, item.runtime_root_ref) != target:
                    item = replace(item, source=value.source, release_digest=value.release_digest,
                                   runtime_root_ref=value.runtime_root_ref, revision=item.revision + 1,
                                   updated_at=value.updated_at)
                projected.append(item)
            projected.append(value)
            connection.execute("UPDATE channel SET document=? WHERE id=1", (self._encode(projected),))
            connection.commit()
            return value

    @contextmanager
    def execution(self, runtime_root_ref: str, release_digest: str, *,
                  legacy: Sequence[RuntimeSelection] = ()) -> Iterator[None]:
        with self._connection() as connection:
            self._initialize(connection, legacy)
            connection.execute("BEGIN")
            self._assert_available(connection)
            values = self._decode(connection.execute("SELECT document FROM channel WHERE id=1").fetchone()[0])
            if not values or any(item.runtime_root_ref != runtime_root_ref or item.release_digest != release_digest for item in values):
                raise RuntimeChannelConflict("This Application runtime is inactive; reopen the selected channel")
            yield
            connection.rollback()

    @staticmethod
    def _pending_transitions(connection: sqlite3.Connection) -> tuple[dict, ...]:
        return tuple(record for (document,) in connection.execute(
            "SELECT document FROM transitions WHERE completed=0"
        ) if not (record := json.loads(document)).get("aborted"))

    @staticmethod
    def _assert_available(connection: sqlite3.Connection) -> None:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='transitions'").fetchone()
        if exists and ApplicationRuntimeChannel._pending_transitions(connection):
            raise RuntimeChannelConflict("Application transition requires completion or recovery; runtime is fenced")
