from __future__ import annotations

from pathlib import Path
import logging
import sqlite3
import time
from typing import Any

import anyio
import aiosqlite
import y_py as Y
from ypy_websocket.ystore import SQLiteYStore, get_new_path

from adaos.services.agent_context import get_ctx

_log = logging.getLogger("adaos.yjs.ystore")


def ystores_root() -> Path:
    """
    Return the root directory for Yjs stores, ensuring it exists.
    """
    ctx = get_ctx()
    root = ctx.paths.state_dir() / "ystores"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ystore_path_for_webspace(webspace_id: str) -> Path:
    """
    Map a webspace id to a filesystem path for its SQLite-backed Yjs store.
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in webspace_id)
    return ystores_root() / f"{safe}.sqlite3"


class AdaosSQLiteYStore(SQLiteYStore):
    """
    SQLiteYStore variant that:
      * uses a dedicated DB file per webspace;
      * applies basic self-healing on persistent \"database is locked\" errors.

    We intentionally trade history durability for robustness in dev/edge cases:
    if the store is hopelessly locked, we rotate the DB file and recreate it
    from scratch so the hub can recover without manual intervention.
    """

    def __init__(self, webspace_id: str, *args: Any, **kwargs: Any) -> None:
        # In the upstream class, "path" is the document key, while db_path
        # points to the shared SQLite file. We want a per-webspace DB file,
        # so we treat webspace_id as the logical path and derive db_path here.
        super().__init__(path=webspace_id, *args, **kwargs)
        self.db_path = str(ystore_path_for_webspace(webspace_id))

    async def _init_db(self) -> None:  # type: ignore[override]
        create_db = False
        move_db = False

        try:
            if not await anyio.Path(self.db_path).exists():
                create_db = True
            else:
                async with self.lock:
                    async with aiosqlite.connect(self.db_path) as db:
                        cursor = await db.execute(
                            "SELECT count(name) FROM sqlite_master WHERE type='table' and name='yupdates'"
                        )
                        table_exists = (await cursor.fetchone())[0]
                        if table_exists:
                            cursor = await db.execute("pragma user_version")
                            version = (await cursor.fetchone())[0]
                            if version != self.version:
                                move_db = True
                                create_db = True
                        else:
                            create_db = True
        except sqlite3.OperationalError as exc:
            # If the DB is locked during init (e.g. after an unclean shutdown),
            # rotate it out of the way and recreate from scratch.
            _log.warning("YStore init failed for %s: %s", self.db_path, exc)
            move_db = True
            create_db = True

        if move_db:
            new_path = await get_new_path(self.db_path)
            _log.warning("YStore moving locked/corrupt DB %s to %s", self.db_path, new_path)
            await anyio.Path(self.db_path).rename(new_path)

        if create_db:
            async with self.lock:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(
                        "CREATE TABLE IF NOT EXISTS yupdates (path TEXT NOT NULL, yupdate BLOB, metadata BLOB, timestamp REAL NOT NULL)"
                    )
                    await db.execute(
                        "CREATE INDEX IF NOT EXISTS idx_yupdates_path_timestamp ON yupdates (path, timestamp)"
                    )
                    await db.execute(f"PRAGMA user_version = {self.version}")
                    await db.commit()

        self.db_initialized.set()

    async def write(self, data: bytes) -> None:  # type: ignore[override]
        """
        Store an update, with a defensive fallback for locked DBs.
        """
        await self.db_initialized.wait()

        async def _write_once() -> None:
            async with self.lock:
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute(
                        "SELECT timestamp FROM yupdates WHERE path = ? ORDER BY timestamp DESC LIMIT 1",
                        (self.path,),
                    )
                    row = await cursor.fetchone()
                    diff = (time.time() - row[0]) if row else 0

                    if self.document_ttl is not None and diff > self.document_ttl:
                        # Squash updates into a single snapshot.
                        ydoc = Y.YDoc()
                        async with db.execute(
                            "SELECT yupdate FROM yupdates WHERE path = ?", (self.path,)
                        ) as cursor:
                            async for (update,) in cursor:
                                Y.apply_update(ydoc, update)
                        await db.execute("DELETE FROM yupdates WHERE path = ?", (self.path,))
                        squashed_update = Y.encode_state_as_update(ydoc)
                        metadata = await self.get_metadata()
                        await db.execute(
                            "INSERT INTO yupdates VALUES (?, ?, ?, ?)",
                            (self.path, squashed_update, metadata, time.time()),
                        )

                    metadata = await self.get_metadata()
                    await db.execute(
                        "INSERT INTO yupdates VALUES (?, ?, ?, ?)",
                        (self.path, data, metadata, time.time()),
                    )
                    await db.commit()

        try:
            await _write_once()
        except sqlite3.OperationalError as exc:
            # Best-effort self-healing: if the DB is locked, rotate and retry once.
            if "database is locked" not in str(exc).lower():
                raise
            _log.warning("YStore write hit locked DB %s: %s; rotating", self.db_path, exc)
            new_path = await get_new_path(self.db_path)
            try:
                await anyio.Path(self.db_path).rename(new_path)
            except Exception:
                # If we cannot rotate, propagate the original error.
                raise
            # Recreate fresh DB and retry write once.
            self.db_initialized = anyio.Event()  # type: ignore[attr-defined]
            await self._init_db()
            await _write_once()

