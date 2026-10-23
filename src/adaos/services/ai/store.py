"""Node-private metadata. No OAuth credentials or application history in SQLite."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class AIStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'ai.sqlite3'
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts(
                    id TEXT PRIMARY KEY, principal TEXT NOT NULL, consumer TEXT NOT NULL,
                    request_id TEXT NOT NULL, digest TEXT NOT NULL, binding TEXT NOT NULL,
                    status TEXT NOT NULL, output TEXT NOT NULL DEFAULT '',
                    citations TEXT NOT NULL DEFAULT '[]', usage TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
                    UNIQUE(principal,consumer,request_id));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
