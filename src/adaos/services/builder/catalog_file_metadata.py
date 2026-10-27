"""Rebuildable Core SQL projection of compact DEV file metadata, never grants."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager, closing
from pathlib import Path
from typing import Any, Mapping

_log = logging.getLogger(__name__)


def file_key(path: Path) -> str:
    # Callers supply already resolved authoring roots. Do not resolve/stat every
    # row in the interactive query path (especially expensive on Windows).
    return os.path.normcase(str(path))


def compact_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"archived": bool(payload.get("archived")),
            "updated_at": str(payload.get("updated_at") or "").strip() or None,
            "builder_llm_model": payload.get("builder_llm_model")}


class CatalogFileMetadata:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "applications/catalog-file-metadata.sqlite3"

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS scopes(
                scope TEXT PRIMARY KEY, roots_json TEXT NOT NULL,
                validated_at REAL NOT NULL, schema_version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS files(
                scope TEXT NOT NULL, path TEXT NOT NULL, summary_json TEXT NOT NULL,
                signature_json TEXT, observed_at REAL NOT NULL,
                PRIMARY KEY(scope,path));
            """)
            with connection:
                yield connection
        finally:
            connection.close()

    def read(self, scope: str) -> tuple[dict[str, dict], float | None]:
        with self._connect() as connection:
            stamp = connection.execute("SELECT validated_at FROM scopes WHERE scope=? AND schema_version=1", (scope,)).fetchone()
            records = connection.execute("SELECT path,summary_json FROM files WHERE scope=?", (scope,)).fetchall()
        return {path: json.loads(value) for path, value in records}, stamp[0] if stamp else None

    def register_scope(self, scope: str, roots: list[Path]) -> None:
        # Register roots before preparing a cold snapshot so a concurrent SDK
        # write can publish its newer per-file observation during preparation.
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO scopes VALUES(?,?,0,1)",
                (scope, json.dumps([file_key(path) for path in roots])))

    def replace_snapshot(self, scope: str, roots: list[Path], records: list[tuple], *, started_at: float) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TEMP TABLE current_sources(path TEXT PRIMARY KEY)")
            connection.executemany("INSERT OR IGNORE INTO current_sources VALUES(?)", ((row[0],) for row in records))
            connection.execute("DELETE FROM files WHERE scope=? AND observed_at<? AND path NOT IN (SELECT path FROM current_sources)", (scope, started_at))
            connection.executemany("""INSERT INTO files(scope,path,summary_json,signature_json,observed_at)
                VALUES(?,?,?,?,?) ON CONFLICT(scope,path) DO UPDATE SET
                summary_json=excluded.summary_json, signature_json=excluded.signature_json,
                observed_at=excluded.observed_at WHERE files.observed_at<=excluded.observed_at""",
                ((scope, path, json.dumps(summary, ensure_ascii=False), json.dumps(signature), started_at)
                 for path, summary, signature in records))
            connection.execute("""INSERT INTO scopes VALUES(?,?,?,1) ON CONFLICT(scope) DO UPDATE SET
                roots_json=excluded.roots_json, validated_at=excluded.validated_at, schema_version=1""",
                (scope, json.dumps([file_key(path) for path in roots]), started_at))

    def update_file(self, path: Path) -> int:
        if path.name != "prompt_state.json" or not self.path.is_file():
            return 0
        # A delayed writer callback must not publish its older payload over a
        # newer source replacement. Read the current file and retain observation
        # ordering independently from time spent waiting for the SQL write gate.
        observed_at = time.time()
        key, parent = file_key(path), file_key(path.parent.parent)
        with self._connect() as connection:
            known = {row[0] for row in connection.execute("SELECT scope FROM files WHERE path=?", (key,))}
            scopes = [scope for scope, roots in connection.execute("SELECT scope,roots_json FROM scopes")
                      if scope in known or parent in json.loads(roots)]
            if not scopes:
                return 0
            stat = path.stat()
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            after = path.stat()
            if (stat.st_ino, stat.st_mtime_ns, stat.st_size) != (after.st_ino, after.st_mtime_ns, after.st_size):
                return 0
            if not isinstance(payload, Mapping):
                raise ValueError("catalog metadata source must be an object")
            signature = [stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size]
            connection.executemany("""INSERT INTO files VALUES(?,?,?,?,?) ON CONFLICT(scope,path) DO UPDATE SET
                summary_json=excluded.summary_json, signature_json=excluded.signature_json,
                observed_at=excluded.observed_at WHERE files.observed_at<=excluded.observed_at""",
                ((scope, key, json.dumps(compact_summary(payload), ensure_ascii=False), json.dumps(signature), observed_at)
                 for scope in scopes))
        return len(scopes)

    def diagnostics(self) -> dict:
        if not self.path.is_file():
            return {"status": "not_initialized", "authority": "query_projection_only"}
        try:
            with closing(sqlite3.connect(self.path)) as connection:
                rows = connection.execute("SELECT scope,validated_at,(SELECT COUNT(*) FROM files WHERE files.scope=scopes.scope) FROM scopes").fetchall()
            return {"status": "available", "authority": "query_projection_only", "scopes": [
                {"scope": scope, "validated_at": stamp, "file_total": count} for scope, stamp, count in rows]}
        except (sqlite3.Error, OSError):
            return {"status": "unavailable", "authority": "query_projection_only"}


def record_catalog_file_metadata(state_dir: Path, path: Path) -> None:
    """Post-write projection update. Source remains authoritative on failure."""
    try:
        CatalogFileMetadata(state_dir).update_file(path)
    except (sqlite3.Error, OSError, ValueError):
        _log.warning("catalog metadata projection update failed; reconciliation required", exc_info=True)
