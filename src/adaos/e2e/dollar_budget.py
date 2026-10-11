"""Durable cost reservations; unknown/ambiguous calls keep their full reserve."""
from pathlib import Path
from contextlib import contextmanager
import math
import sqlite3
import time


class DollarBudget:
    def __init__(self, path, *, limit_usd):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not math.isfinite(limit_usd) or limit_usd <= 0:
            raise ValueError('Positive dollar limit required')
        self.limit = float(limit_usd)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS dollar_limit (id INTEGER PRIMARY KEY, usd REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS cost_reservations (request_key TEXT PRIMARY KEY, reserved_usd REAL NOT NULL, observed_usd REAL, reserved_at REAL NOT NULL)')
            row = db.execute('SELECT usd FROM dollar_limit WHERE id=1').fetchone()
            if row is not None and row[0] != self.limit:
                raise ValueError('Dollar limit drift')
            db.execute('INSERT OR IGNORE INTO dollar_limit VALUES (1, ?)', (self.limit,))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, request_key, *, reserve_usd):
        if not request_key or not math.isfinite(reserve_usd) or reserve_usd <= 0:
            raise ValueError('Request key and positive reservation required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM cost_reservations WHERE request_key=?', (request_key,)).fetchone():
                raise ValueError('Already reserved; recover the retained job, never resubmit')
            charged = db.execute('SELECT COALESCE(SUM(COALESCE(observed_usd,reserved_usd)),0) FROM cost_reservations').fetchone()[0]
            if charged + reserve_usd > self.limit + 1e-9:
                raise ValueError('Insufficient additional dollar budget')
            db.execute('INSERT INTO cost_reservations VALUES (?,?,NULL,?)', (request_key, reserve_usd, time.time()))

    def observe(self, request_key, *, usd):
        if not math.isfinite(usd) or usd < 0:
            raise ValueError('Cost cannot be negative')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT observed_usd FROM cost_reservations WHERE request_key=?', (request_key,)).fetchone()
            if row is None or row[0] is not None and abs(row[0] - usd) > 1e-9:
                raise ValueError('Unknown reservation or observed cost drift')
            db.execute('UPDATE cost_reservations SET observed_usd=? WHERE request_key=?', (usd, request_key))

    def snapshot(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute('SELECT * FROM cost_reservations ORDER BY reserved_at')]
        charged = sum(row['observed_usd'] if row['observed_usd'] is not None else row['reserved_usd'] for row in rows)
        return {'limit_usd': self.limit, 'committed_estimated_usd': round(charged, 6),
            'remaining_estimated_usd': round(self.limit - charged, 6), 'reservations': rows,
            'basis': 'Standard provider rate estimate, NOT a Root invoice. Reserves are conservative estimates, not provider-enforced dollar ceilings; unreported costs remain reserved.'}
