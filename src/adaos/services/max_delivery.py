"""MAX transport: durable Hub queue and mTLS Root mailbox, with no bot credentials on Hubs."""
from __future__ import annotations

import hashlib
from contextlib import nullcontext
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from adaos.services.agent_context import get_ctx, set_ctx

LOG = logging.getLogger('adaos.max')
BASE = '/v1/hub/communication/max/'


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def ensure_schema() -> None:
    with get_ctx().sql.connect() as con:
        con.executescript('''CREATE TABLE IF NOT EXISTS max_outbound_attempts (
            attempt_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL UNIQUE,
            request_json TEXT NOT NULL, request_digest TEXT NOT NULL,
            status TEXT NOT NULL, next_attempt_at REAL NOT NULL, claimed_at REAL NOT NULL,
            receipt_json TEXT, durable_attempt_id TEXT, last_error TEXT, source_message_id TEXT);
            CREATE INDEX IF NOT EXISTS idx_max_outputs_due ON max_outbound_attempts(status,next_attempt_at);''')
        if 'source_message_id' not in {r[1] for r in con.execute('PRAGMA table_info(max_outbound_attempts)')}:
            con.execute('ALTER TABLE max_outbound_attempts ADD COLUMN source_message_id TEXT')
        con.commit()


def queue(*, operation_key: str, target: Mapping[str, Any], messages: list[dict],
          options: Mapping[str, Any] | None = None, durable_attempt_id: str | None = None,
          connection: sqlite3.Connection | None = None, source_message_id: str | None = None,
          notification_guard: Mapping[str, Any] | None = None) -> dict:
    if connection is None:
        ensure_schema()
    required = ('hub_id', 'bot_id', 'chat_id', 'user_id', 'binding_id')
    destination = {k: str(target.get(k) or '').strip() for k in required}
    destination['webspace_id'] = target.get('webspace_id')
    if not all(destination[k] for k in required) or not operation_key or len(operation_key) > 512:
        raise ValueError('MAX needs an admitted immutable binding')
    if destination['hub_id'] != str(get_ctx().config.subnet_id):
        raise PermissionError('MAX destination belongs to another hub')
    if not messages or len(messages) > 100:
        raise ValueError('MAX message count invalid')
    immutable = {'hub_id': destination['hub_id'], 'target': destination,
                 'operation_key': operation_key, 'messages': messages, **({'options': dict(options)} if options else {})}
    if notification_guard is not None:
        immutable['notification_guard'] = dict(notification_guard)
    digest = hashlib.sha256(_dump([immutable, durable_attempt_id, source_message_id]).encode()).hexdigest()
    with (nullcontext(connection) if connection is not None else get_ctx().sql.connect()) as con:
        con.row_factory = sqlite3.Row
        if connection is None:
            con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM max_outbound_attempts WHERE operation_key=?', (operation_key,)).fetchone()
        if row:
            if row['request_digest'] != digest:
                raise ValueError('MAX operation key idempotency conflict')
            return dict(row)
        attempt_id = 'max-delivery.' + uuid.uuid4().hex
        request = {**immutable, 'delivery_attempt_id': attempt_id}
        con.execute('INSERT INTO max_outbound_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (attempt_id, operation_key, _dump(request), digest, 'held' if durable_attempt_id else 'pending', time.time(), time.time(), None,
                     durable_attempt_id, None, source_message_id))
        if connection is None:
            con.commit()
        return dict(con.execute('SELECT * FROM max_outbound_attempts WHERE attempt_id=?', (attempt_id,)).fetchone())


def complete(receipt: Mapping[str, Any]) -> None:
    path = Path(__file__).resolve().parents[1] / 'abi/max.delivery_receipt.v1.schema.json'
    Draft202012Validator(json.loads(path.read_text(encoding='utf8'))).validate(dict(receipt))
    with get_ctx().sql.connect() as con:
        con.row_factory = sqlite3.Row
        row = con.execute('SELECT * FROM max_outbound_attempts WHERE attempt_id=?', (receipt['delivery_attempt_id'],)).fetchone()
        if not row or row['operation_key'] != receipt['operation_key']:
            raise ValueError('MAX receipt identity mismatch')
        request = json.loads(row['request_json'])
        if receipt['delivered'] and (len(receipt['external_message_ids']) != len(request['messages'])
                                     or not all(receipt['external_message_ids'])):
            raise ValueError('MAX delivery needs exact message proof')
        if row['status'] == 'delivered':
            receipt = json.loads(row['receipt_json'])  # late failure cannot undo positive proof
        status = 'delivered' if receipt['delivered'] else ('needs_review' if receipt['error'] == 'max_outcome_unknown' else 'failed')
        con.execute('UPDATE max_outbound_attempts SET status=?,receipt_json=?,last_error=? WHERE attempt_id=?',
                    (status, _dump(receipt), receipt['error'], row['attempt_id']))
        con.commit()
    if row['durable_attempt_id']:
        from adaos.services import durable_delivery
        if status != 'needs_review':
            durable_delivery.complete_delivery(row['durable_attempt_id'], delivered=receipt['delivered'],
                                              receipt=dict(receipt), error=receipt['error'])
        with get_ctx().sql.connect() as con:
            con.execute('UPDATE conversation_output_intents SET status=?,last_error=?,updated_at=? WHERE attempt_id=?',
                        ('delivered' if receipt['delivered'] else 'needs_review' if status == 'needs_review' else 'dead_letter',
                         receipt['error'], time.time(), row['durable_attempt_id']))
            con.commit()


def root_client():
    from adaos.services.root.client import RootHttpClient
    ctx = get_ctx()
    config = ctx.config
    if config is None:
        from adaos.services.node_config import load_config
        config = load_config(ctx=ctx)
    client = RootHttpClient.from_hub_config(config, fallback_base_url=ctx.settings.api_base)
    if client is None:
        raise PermissionError('MAX requires the Hub mTLS identity')
    return client


def dispatch_once(client: Any) -> bool:
    from adaos.services.root.client import RootHttpError
    ensure_schema()
    with get_ctx().sql.connect() as con:
        con.row_factory = sqlite3.Row
        con.execute('BEGIN IMMEDIATE')
        row = con.execute("SELECT * FROM max_outbound_attempts WHERE status IN ('pending','accepted') AND next_attempt_at<=? ORDER BY next_attempt_at LIMIT 1",
                          (time.time(),)).fetchone()
        if not row:
            return False
        con.execute('UPDATE max_outbound_attempts SET next_attempt_at=? WHERE attempt_id=?', (time.time() + 30, row['attempt_id']))
        con.commit()
    try:
        if time.time() - row['claimed_at'] > 86400:
            _local_failure(row, 'max_outcome_unknown')
            return True
        if row['status'] == 'pending':
            request = json.loads(row['request_json'])
            if request.get('notification_guard'):
                from adaos.services.notifications.relay import authorize_attempt, require_root_guard
                proof = authorize_attempt(row['durable_attempt_id'])
                if not proof or not proof['allowed']:
                    if proof and proof.get('next_eligible_at'):
                        with get_ctx().sql.connect() as con:
                            con.execute('UPDATE max_outbound_attempts SET next_attempt_at=? WHERE attempt_id=?',
                                        (proof['next_eligible_at'], row['attempt_id']))
                            con.commit()
                    else:
                        _local_failure(row, 'notification_suppressed:' + (proof or {}).get('reason', 'missing_source'))
                    return True
                require_root_guard(client)
            if row['source_message_id']:
                with get_ctx().sql.connect() as con:
                    source = con.execute('SELECT redaction_state FROM conversation_messages WHERE message_id=?', (row['source_message_id'],)).fetchone()
                if not source or source[0] != 'active':
                    _local_failure(row, 'max_source_removed')
                    return True
            response = client.request('POST', BASE + ('send-notification' if request.get('notification_guard') else 'send'), json=request, timeout=5)
            if response.get('ok') is not True or response.get('accepted') is not True:
                raise ValueError('MAX Root did not durably accept output')
            with get_ctx().sql.connect() as con:
                con.execute("UPDATE max_outbound_attempts SET status='accepted',next_attempt_at=? WHERE attempt_id=? AND status='pending'", (time.time() + 1, row['attempt_id']))
                con.execute("""UPDATE conversation_output_intents SET status='accepted_by_root',updated_at=?
                    WHERE attempt_id=? AND status IN ('leased','queued_for_relay','reconciling')""",
                    (time.time(), row['durable_attempt_id']))
                con.commit()
        else:
            response = client.request('POST', BASE + 'receipt/status', json={'operation_key': row['operation_key']}, timeout=5)
            if response.get('schema') != 'adaos.max.receipt_status.v1' or response.get('ok') is not True:
                raise ValueError('MAX invalid receipt query response')
            if response.get('found') is True:
                complete(response['receipt'])
    except RootHttpError as exc:
        if row['status'] == 'pending' and exc.status_code in {400, 401, 403, 410, 422}:
            _local_failure(row, 'max_rejected_' + str(exc.status_code))
    except Exception:
        # Only the same Root operation may be resubmitted. Root retains both its
        # request digest and provider reservation; a network timeout cannot turn
        # into another provider send. Accepted requests are queried, never resent.
        LOG.debug('MAX delivery awaiting exact Root proof', exc_info=True)
    return True


def _local_failure(row: Mapping[str, Any], error: str) -> None:
    from datetime import datetime, timezone
    complete({'schema': 'adaos.max.delivery_receipt.v1', 'receipt_id': 'max-local:' + row['attempt_id'],
              'delivery_attempt_id': row['attempt_id'], 'operation_key': row['operation_key'],
              'transport': 'max', 'delivered': False, 'external_message_ids': [], 'error': error,
              'completed_at': datetime.now(timezone.utc).isoformat()})


def reconcile_local() -> None:
    """Finish an interrupted receipt application without repeating any provider effect."""
    with get_ctx().sql.connect() as con:
        try:
            rows = con.execute("""SELECT t.receipt_json FROM max_outbound_attempts t
                JOIN conversation_output_intents i ON i.attempt_id=t.durable_attempt_id
                WHERE t.receipt_json IS NOT NULL AND i.status IN ('leased','queued_for_relay','accepted_by_root','reconciling') LIMIT 10""").fetchall()
        except sqlite3.OperationalError:
            return  # publication tables may not exist during bootstrap
    for row in rows:
        complete(json.loads(row[0]))


def poll_inputs(client: Any, bus: Any) -> int:
    from adaos.domain import Event
    ctx = get_ctx()
    response = client.request('POST', BASE + 'input/poll', json={}, timeout=5)
    if response.get('ok') is not True or not isinstance(response.get('events'), list) or len(response['events']) > 10:
        raise ValueError('MAX invalid mailbox response')
    acknowledged = 0
    for item in response['events']:
        env = item['envelope']
        p = env.get('payload', {})
        if p.get('source') != 'max' or str(p.get('hub_id')) != str(ctx.config.subnet_id):
            raise PermissionError('MAX input Hub identity mismatch')
        bus.publish(Event(type=f'max.input.{ctx.config.subnet_id}', payload=env, source='max.root.mailbox', ts=time.time()))
        # The synchronous bridge owns its claim and action admission. An event
        # left claimed by a crash is retained for review, never blindly replayed.
        with ctx.sql.connect() as con:
            row = con.execute('SELECT status FROM conversation_transport_ingress WHERE idempotency_key=?',
                              ('transport:' + str(env.get('dedup_key')),)).fetchone()
        if row and row[0] == 'dispatched':
            client.request('POST', BASE + 'input/ack', json={'key': item['key']}, timeout=5)
            acknowledged += 1
    return acknowledged


class MaxAdapter:
    def send(self, envelope: dict, route: dict, attempt: dict) -> dict:
        from adaos.services.router.max_projection import messages_for
        from adaos.services import conversation_store
        ctx = get_ctx()
        source = conversation_store.get_message(envelope['payload'].get('data', {}).get('message_id', '')) or {}
        item = queue(operation_key='max-envelope:' + attempt['idempotency_key'], target=route['destination_ref'],
                     messages=messages_for({'text': envelope['payload']['text'], 'actions': source.get('actions', [])}),
                     durable_attempt_id=attempt['attempt_id'], source_message_id=envelope['payload'].get('data', {}).get('message_id'),
                     notification_guard=envelope['payload'].get('data', {}).get('relay_guard'))
        with ctx.sql.connect() as con:
            changed = con.execute("UPDATE conversation_output_intents SET transport_attempt_id=? WHERE attempt_id=? AND status='leased' AND lease_token=? AND lease_until>?",
                                  (item['attempt_id'], attempt['attempt_id'], attempt['_output_lease_token'], time.time()))
            if changed.rowcount != 1:
                raise ValueError('MAX output dispatch lease lost')
            con.execute("UPDATE max_outbound_attempts SET status='pending' WHERE attempt_id=? AND status='held'", (item['attempt_id'],))
            con.commit()
        return {'accepted': True, 'accepted_state': 'queued_for_relay'}


class MaxWorker:
    def __init__(self, bus: Any):
        self.ctx, self.bus = get_ctx(), bus
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        def run():
            set_ctx(self.ctx)
            ensure_schema()
            client = None
            while not self.stop_event.is_set():
                delay = 2
                try:
                    flag = self.ctx.paths.state_dir() / 'max-channel.json'
                    if flag.is_file() and json.loads(flag.read_text(encoding='utf8')).get('enabled') is True:
                        client = client or root_client()
                        try:
                            poll_inputs(client, self.bus)
                        except Exception:
                            LOG.debug('MAX input mailbox awaiting recovery', exc_info=True)
                        dispatch_once(client)
                        reconcile_local()
                except Exception:
                    LOG.debug('MAX mailbox unavailable', exc_info=True)
                    client = None
                    delay = 10
                self.stop_event.wait(delay)
        self.thread = threading.Thread(target=run, name='max-communication', daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
