"""Addressable resource state and bounded journals with one-time JSON import."""

from __future__ import annotations

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping


def _encode(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))


class ResourceStorage:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "resources.sqlite3"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30.0)) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='resource_imports'").fetchone():
                connection.execute("PRAGMA journal_mode=WAL")
                with connection:
                    connection.execute("CREATE TABLE IF NOT EXISTS resource_states (resource_type TEXT PRIMARY KEY, project_ref TEXT, ui_revision TEXT, body TEXT NOT NULL)")
                    connection.execute("CREATE INDEX IF NOT EXISTS resource_project ON resource_states(project_ref, ui_revision)")
                    connection.execute("CREATE TABLE IF NOT EXISTS resource_journal (sequence INTEGER PRIMARY KEY AUTOINCREMENT, stream TEXT NOT NULL, body TEXT NOT NULL)")
                    connection.execute("CREATE INDEX IF NOT EXISTS resource_journal_stream ON resource_journal(stream, sequence)")
                    connection.execute("CREATE TABLE IF NOT EXISTS resource_imports (name TEXT PRIMARY KEY)")
            yield connection

    def import_json(self, path: Path, *, stream: str | None = None) -> None:
        name = f"journal:{stream}" if stream else "states"
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM resource_imports WHERE name=?", (name,)).fetchone():
                return
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT 1 FROM resource_imports WHERE name=?", (name,)).fetchone():
                    return
                value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
                if not isinstance(value, Mapping):
                    raise ValueError(f"invalid resource legacy document: {path.name}")
                if stream:
                    items = value.get("items", [])
                    if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
                        raise ValueError(f"invalid resource legacy journal: {path.name}")
                    connection.executemany("INSERT INTO resource_journal(stream, body) VALUES (?, ?)", [(stream, _encode(item)) for item in items[-1000:]])
                else:
                    resources = value.get("resources", {})
                    if not isinstance(resources, Mapping) or any(not isinstance(item, Mapping) for item in resources.values()):
                        raise ValueError(f"invalid resource legacy registry: {path.name}")
                    for key, state in resources.items():
                        self._put(connection, str(key), state)
                connection.execute("INSERT INTO resource_imports(name) VALUES (?)", (name,))

    @staticmethod
    def _put(connection: sqlite3.Connection, key: str, state: Mapping[str, Any]) -> None:
        connection.execute(
            "INSERT INTO resource_states(resource_type, project_ref, ui_revision, body) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(resource_type) DO UPDATE SET project_ref=excluded.project_ref, ui_revision=excluded.ui_revision, body=excluded.body",
            (key, state.get("project_ref"), state.get("webui_digest"), _encode(state)),
        )

    def put(self, key: str, state: Mapping[str, Any]) -> None:
        with self._connect() as connection, connection:
            self._put(connection, key, state)

    def get(self, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT body FROM resource_states WHERE resource_type=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def states(self, *, project_ref: str | None = None, ui_revision: str | None = None) -> dict[str, dict[str, Any]]:
        sql, params = "SELECT resource_type, body FROM resource_states", []
        if project_ref is not None:
            sql += " WHERE project_ref=? AND ui_revision=?"
            params = [project_ref, ui_revision]
        with self._connect() as connection:
            return {key: json.loads(body) for key, body in connection.execute(sql, params)}

    def append(self, stream: str, payload: Mapping[str, Any]) -> None:
        with self._connect() as connection, connection:
            connection.execute("INSERT INTO resource_journal(stream, body) VALUES (?, ?)", (stream, _encode(payload)))
            connection.execute(
                "DELETE FROM resource_journal WHERE stream=? AND sequence <= "
                "(SELECT sequence FROM resource_journal WHERE stream=? ORDER BY sequence DESC LIMIT 1 OFFSET 1000)",
                (stream, stream),
            )

    def journal(self, stream: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [json.loads(body) for (body,) in connection.execute("SELECT body FROM resource_journal WHERE stream=? ORDER BY sequence", (stream,))]
