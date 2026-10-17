"""One local USD admission ledger for Builder and its provider attempts.

Reservations are conservative commitments, never fabricated invoices. A timeout,
cancel, cache hit or missing tariff does not release an unknown provider charge.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from adaos.services.runtime_paths import current_state_dir


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _units(value, *, positive=False):
    if isinstance(value, bool):
        raise ValueError('USD amount must be finite and nonnegative')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or positive and amount <= 0:
            raise ValueError('USD amount must be finite and nonnegative')
        units = int((amount * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))
        if units > 2**62:
            raise ValueError('USD amount exceeds the ledger bound')
        return units
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('Invalid USD amount') from exc


def _usd(value):
    return value / 1_000_000_000


class BuilderSpendLedger:
    def __init__(self, *, state_dir=None):
        self.path = Path(state_dir or current_state_dir()) / 'builder' / 'spend.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    scope TEXT PRIMARY KEY, limit_units INTEGER NOT NULL,
                    default_reserve_units INTEGER NOT NULL, authorization_ref TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    request_id TEXT PRIMARY KEY, scope TEXT NOT NULL, run_id TEXT NOT NULL,
                    input_digest TEXT NOT NULL, reserve_units INTEGER NOT NULL,
                    observed_units INTEGER, cost_basis TEXT, status TEXT NOT NULL,
                    provider_base TEXT, job_id TEXT, created_at REAL NOT NULL,
                    UNIQUE(provider_base,job_id));
                CREATE TABLE IF NOT EXISTS receipts (
                    receipt_ref TEXT PRIMARY KEY, request_id TEXT NOT NULL, facts TEXT NOT NULL);
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def configure(self, scope, *, limit_usd, default_reserve_usd, authorization_ref):
        if not scope or not authorization_ref:
            raise ValueError('Explicit spend scope and authorization reference required')
        values = (scope, _units(limit_usd, positive=True), _units(default_reserve_usd, positive=True), authorization_ref)
        if values[2] > values[1]:
            raise ValueError('Default reserve exceeds the account limit')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM accounts WHERE scope=?', (scope,)).fetchone()
            if previous and tuple(previous) != values:
                raise ValueError('Spend account authorization drift; existing commitments must be reviewed')
            db.execute('INSERT OR IGNORE INTO accounts VALUES(?,?,?,?)', values)
        return self.snapshot(scope)

    @staticmethod
    def _committed(row):
        if row['status'] == 'reconciled':
            return row['observed_units']
        return max(row['reserve_units'], row['observed_units'] or 0)

    def reserve(self, scope, *, request_id, run_id, input_digest, reserve_usd=None):
        if not all(isinstance(value, str) and value for value in (scope, request_id, run_id, input_digest)):
            raise ValueError('Spend admission requires scope, request, run and exact input identity')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            account = db.execute('SELECT * FROM accounts WHERE scope=?', (scope,)).fetchone()
            if account is None:
                return None  # Explicitly unconfigured, never a claim of zero cost.
            amount = _units(reserve_usd, positive=True) if reserve_usd is not None else account['default_reserve_units']
            previous = db.execute('SELECT * FROM attempts WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if (previous['scope'], previous['run_id'], previous['input_digest'], previous['reserve_units']) != (scope, run_id, input_digest, amount):
                    raise ValueError('Spend request identity conflict')
                return self._attempt(previous)
            charged = sum(self._committed(row) for row in db.execute('SELECT * FROM attempts WHERE scope=?', (scope,)))
            if charged + amount > account['limit_units']:
                raise ValueError('builder_usd_reservation_limit')
            db.execute('INSERT INTO attempts(request_id,scope,run_id,input_digest,reserve_units,status,created_at) VALUES(?,?,?,?,?,?,?)',
                       (request_id, scope, run_id, input_digest, amount, 'reserved', time.time()))
            return self._attempt(db.execute('SELECT * FROM attempts WHERE request_id=?', (request_id,)).fetchone())

    def link_job(self, request_id, *, job_id, provider_base):
        if not job_id or not provider_base:
            raise ValueError('Provider job ownership required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown spend reservation')
            identity = (str(provider_base).rstrip('/'), str(job_id))
            if row['job_id'] and (row['provider_base'], row['job_id']) != identity:
                raise ValueError('Provider job identity conflict')
            db.execute('UPDATE attempts SET provider_base=?,job_id=? WHERE request_id=?', (*identity, request_id))

    def observe(self, request_id, *, receipt_ref, usd=None, basis=None, complete=False, status=None):
        """Join one trusted observation; only a complete measured bill releases reserve.

        Caller owns receipt provenance. Estimates remain visible separately and
        retain the reserve, even if the job is terminal or cancelled.
        """
        if not receipt_ref or basis not in {None, 'estimate', 'metered', 'invoiced'}:
            raise ValueError('Explicit receipt identity and cost basis required')
        if (usd is None) != (basis is None) or complete and usd is None:
            raise ValueError('Complete cost observation requires a USD amount and basis')
        amount = _units(usd) if usd is not None else None
        facts = _wire(dict(usd_units=amount, basis=basis, complete=bool(complete), status=status))
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown spend reservation')
            previous = db.execute('SELECT request_id,facts FROM receipts WHERE receipt_ref=?', (receipt_ref,)).fetchone()
            if previous:
                if tuple(previous) != (request_id, facts):
                    raise ValueError('Spend receipt identity conflict')
                return self._attempt(row)
            rank = {None: 0, 'estimate': 1, 'metered': 2, 'invoiced': 3}
            if amount is not None and row['observed_units'] is not None and rank[basis] == rank[row['cost_basis']] and amount != row['observed_units']:
                raise ValueError('Conflicting cost facts require explicit reconciliation')
            db.execute('INSERT INTO receipts VALUES(?,?,?)', (receipt_ref, request_id, facts))
            if amount is not None and rank[basis] >= rank[row['cost_basis']]:
                next_status = 'reconciled' if complete and basis in {'metered','invoiced'} else 'uncertain'
                db.execute('UPDATE attempts SET observed_units=?,cost_basis=?,status=? WHERE request_id=?',
                           (amount, basis, next_status, request_id))
            elif row['status'] != 'reconciled':
                db.execute('UPDATE attempts SET status=? WHERE request_id=?', ('uncertain', request_id))
            return self._attempt(db.execute('SELECT * FROM attempts WHERE request_id=?', (request_id,)).fetchone())

    def observe_job(self, job, *, provider_base):
        job_id = str(job.get('job_id') or job.get('id') or '')
        with self._db() as db:
            row = db.execute('SELECT request_id FROM attempts WHERE provider_base=? AND job_id=?',
                             (str(provider_base).rstrip('/'), job_id)).fetchone()
        if row is None:
            return None
        protocol = job.get('_protocol') or job.get('telemetry') or {}
        cost = protocol.get('cost') or job.get('cost') or {}
        basis = cost.get('basis') or ('estimate' if 'estimated_usd' in cost else None)
        usd = cost.get('estimated_usd') if basis == 'estimate' else cost.get('usd')
        complete = cost.get('complete') is True and job.get('status') in {'succeeded','failed','cancelled'}
        facts = dict(usd=usd, basis=basis, complete=complete, status=job.get('status'))
        receipt_ref = 'root-cost:' + hashlib.sha256(_wire([provider_base, job_id, facts]).encode()).hexdigest()
        return self.observe(row['request_id'], receipt_ref=receipt_ref, **facts)

    def _attempt(self, row):
        return {key: row[key] for key in ('request_id','scope','run_id','input_digest','status','job_id','provider_base')} | {
            'reserved_usd': _usd(row['reserve_units']),
            'observed_usd': _usd(row['observed_units']) if row['observed_units'] is not None else None,
            'cost_basis': row['cost_basis'], 'committed_usd': _usd(self._committed(row))}

    def snapshot(self, scope, *, run_id=None):
        with self._db() as db:
            account = db.execute('SELECT * FROM accounts WHERE scope=?', (scope,)).fetchone()
            if account is None:
                return {'schema': 'adaos.builder.usd_budget.v1', 'scope': scope, 'status': 'not_configured'}
            all_rows = list(db.execute('SELECT * FROM attempts WHERE scope=? ORDER BY created_at,request_id', (scope,)))
        committed = sum(self._committed(row) for row in all_rows)
        rows = [row for row in all_rows if run_id is None or row['run_id'] == run_id]
        return {'schema': 'adaos.builder.usd_budget.v1', 'scope': scope,
            'status': 'exceeded' if committed > account['limit_units'] else 'within_budget',
            'limit_usd': _usd(account['limit_units']), 'committed_usd': _usd(committed),
            'available_usd': _usd(account['limit_units'] - committed),
            'authorization_ref': account['authorization_ref'],
            'attempts': [self._attempt(row) for row in rows],
            'usage_complete': all(row['status'] == 'reconciled' for row in rows),
            'basis': 'Local USD commitments; unknown and estimated costs retain their reserve. Provider invoices remain independent.'}
