from __future__ import annotations
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from adaos.services import max_delivery, conversation_store
from adaos.services.agent_context import get_ctx
from adaos.services.chat_io.nlu_bridge import _extract_text_io_input, _extract_action_io_input, register_chat_nlu_bridge
from adaos.services.router import max_projection
from adaos.services.eventbus import LocalEventBus


@pytest.fixture(autouse=True)
def max_hub_context(monkeypatch):
    monkeypatch.setattr(get_ctx(), 'config', SimpleNamespace(subnet_id='hub:max'))


def target():
    return dict(hub_id=str(get_ctx().config.subnet_id), bot_id='538172055', chat_id='42',
                user_id='7', binding_id='binding:owned', webspace_id=None)


def envelope(kind='text'):
    return {'kind': 'io.input', 'event_id': 'event:stable', 'dedup_key': 'max:bot:message:mid.string',
            'payload': {'source': 'max', 'type': kind, **target(), 'update_id': 'mid.string',
                        'payload': {'text': 'Привет', 'action': {'id': 'ia:action'},
                                    'meta': {'msg_id': 'mid.string', 'callback_id': 'callback', 'binding_id': 'binding:owned'}}}}


def test_max_input_preserves_string_ids_and_platform_namespace():
    text, _, meta = _extract_text_io_input(envelope())
    assert text == 'Привет'
    assert meta['reply_to'] == 'mid.string'
    assert meta['binding_id'] == 'binding:owned'
    assert meta['route_id'] == 'max'
    assert meta['request_id'].startswith('max:')
    action, meta = _extract_action_io_input(envelope('action'))
    assert action == 'ia:action'
    assert meta['max_source_message_id'] == 'mid.string'
    assert meta['source_message_ref']['transport'] == 'max'
    assert meta['max_callback_id'] == 'callback'
    assert 'telegram_source_message_id' not in meta


def test_queue_survives_restart_and_rejects_destination_drift():
    first = max_delivery.queue(operation_key='max:test:queue', target=target(), messages=[{'type': 'text', 'text': 'Ответ'}])
    again = max_delivery.queue(operation_key='max:test:queue', target=target(), messages=[{'type': 'text', 'text': 'Ответ'}])
    assert first['attempt_id'] == again['attempt_id']
    with pytest.raises(ValueError, match='idempotency conflict'):
        max_delivery.queue(operation_key='max:test:queue', target={**target(), 'chat_id': '99'}, messages=[{'type': 'text', 'text': 'Ответ'}])
    with pytest.raises(PermissionError):
        max_delivery.queue(operation_key='wrong-hub', target={**target(), 'hub_id': 'other'}, messages=[{'type': 'text', 'text': 'Ответ'}])


def test_projection_chunks_text_and_retains_action_authority():
    meta = {**target(), 'io_type': 'max', 'request_id': 'request'}
    payload = {'id': 'response:max', 'text': 'Я' * 6100, 'actions': [{'label': 'Подтвердить', 'token': 'ia:owned'}]}
    assert max_projection.project(payload, meta)
    assert max_projection.project(payload, meta)
    assert not max_projection.project(payload, {**meta, 'output_delivery_owned': True})
    with get_ctx().sql.connect() as con:
        rows = con.execute('SELECT request_json FROM max_outbound_attempts').fetchall()
    requests = [json.loads(row[0]) for row in rows if 'Я' in row[0]]
    assert len(requests) == 1
    assert len(requests[0]['messages']) == 3
    assert requests[0]['messages'][0]['keyboard']['inline_keyboard'][0][0]['callback_data'] == 'ia:owned'
    chunks = max_projection.text_chunks('😀' * 2500)
    assert ''.join(chunks) == '😀' * 2500
    assert all(len(part.encode('utf-16-le')) // 2 <= 3000 for part in chunks)


def receipt(item, **changes):
    return {'schema': 'adaos.max.delivery_receipt.v1', 'receipt_id': 'max-receipt:' + item['attempt_id'],
            'delivery_attempt_id': item['attempt_id'], 'operation_key': item['operation_key'],
            'transport': 'max', 'delivered': True, 'external_message_ids': ['mid.provider'],
            'error': None, 'completed_at': '2026-11-06T12:00:00Z', **changes}


def test_only_exact_provider_proof_completes_output_and_uncertainty_stops_replay():
    item = max_delivery.queue(operation_key='max:test:proof', target=target(), messages=[{'type': 'text', 'text': 'Готово'}])
    with pytest.raises(ValueError):
        max_delivery.complete(receipt(item, operation_key='another-operation'))
    with pytest.raises(ValueError):
        max_delivery.complete(receipt(item, external_message_ids=[]))
    max_delivery.complete(receipt(item, delivered=False, external_message_ids=[], error='max_outcome_unknown'))
    with get_ctx().sql.connect() as con:
        assert con.execute('SELECT status FROM max_outbound_attempts WHERE attempt_id=?', (item['attempt_id'],)).fetchone()[0] == 'needs_review'
    max_delivery.complete(receipt(item))
    max_delivery.complete(receipt(item, delivered=False, external_message_ids=[], error='max_rejected_403'))
    with get_ctx().sql.connect() as con:
        assert con.execute('SELECT status FROM max_outbound_attempts WHERE attempt_id=?', (item['attempt_id'],)).fetchone()[0] == 'delivered'


def test_mailbox_ack_follows_durable_core_dispatch_and_duplicate_is_suppressed(monkeypatch):
    bus = LocalEventBus()
    monkeypatch.setattr('adaos.services.chat_io.nlu_bridge._bound_conversation_id', lambda *args: '')
    register_chat_nlu_bridge(bus)
    seen = []
    bus.subscribe('dialog.user_message', lambda event: seen.append(event))
    class Client:
        def __init__(self): self.acks = []
        def request(self, method, path, **kwargs):
            if path.endswith('/input/poll'):
                return {'ok': True, 'events': [{'key': 'queue:owned', 'envelope': envelope()}]}
            self.acks.append(kwargs['json']['key'])
            return {'ok': True}
    client = Client()
    assert max_delivery.poll_inputs(client, bus) == 1
    assert max_delivery.poll_inputs(client, bus) == 1
    assert len(seen) == 1
    assert client.acks == ['queue:owned', 'queue:owned']
    assert conversation_store.get_transport_ingress('transport:max:bot:message:mid.string')['status'] == 'dispatched'


def test_max_capabilities_admit_buttons_without_claiming_media_or_miniapps():
    from adaos.services.conversation_interactions import standard_capability_profile
    profile = standard_capability_profile('max', persist=False)
    assert profile['capabilities']['buttons']
    assert not profile['capabilities']['file_upload']
    assert not profile['capabilities']['miniapp']


@pytest.mark.anyio
async def test_mailbox_accepts_and_acks_next_turn_while_first_response_is_waiting(monkeypatch):
    from adaos.domain import Event

    bus = LocalEventBus()
    monkeypatch.setattr('adaos.services.chat_io.nlu_bridge._bound_conversation_id', lambda *args: '')
    register_chat_nlu_bridge(bus)
    slow_started, release, fast_done = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def dialog(event):
        if event.payload['text'] == 'slow':
            slow_started.set()
            await release.wait()
        else:
            fast_done.set()

    bus.subscribe('dialog.user_message', dialog)
    # Bind the process bus to its runtime loop before polling from the MAX thread.
    bus.publish(Event(type='unit.owner_loop', payload={}, source='test', ts=0))

    class Client:
        def __init__(self): self.acks, self.polls = [], 0
        def request(self, method, path, **kwargs):
            if path.endswith('/input/poll'):
                self.polls += 1
                turn = deepcopy(envelope())
                turn['dedup_key'] += ':' + str(self.polls)
                turn['payload']['payload']['text'] = 'slow' if self.polls == 1 else 'fast'
                return {'ok': True, 'events': [{'key': 'queue:' + str(self.polls), 'envelope': turn}]}
            self.acks.append(kwargs['json']['key'])
            return {'ok': True}

    client = Client()
    try:
        assert await asyncio.to_thread(max_delivery.poll_inputs, client, bus) == 1
        await asyncio.wait_for(slow_started.wait(), 5)
        assert await asyncio.to_thread(max_delivery.poll_inputs, client, bus) == 1
        await asyncio.wait_for(fast_done.wait(), 5)
        assert not release.is_set()
        assert client.acks == ['queue:1', 'queue:2']
    finally:
        release.set()
        assert await bus.wait_for_idle(5)


def test_publication_and_max_origin_reply_commit_together(monkeypatch):
    from adaos.services import conversation_publication as publication
    conversation_store.upsert_conversation(conversation_id='max:publication', owner='skill:test', webspace_id='test')
    args = dict(conversation_id='max:publication', owner='skill:test', webspace_id='test',
                publication_key='max:atomic', text='Готово', meta={**target(), 'io_type': 'max'},
                message_payload={'actions': [{'label': 'Продолжить', 'token': 'ia:continue'}]})
    first = publication.publish(**args)
    assert publication.publish(**args) == first
    with get_ctx().sql.connect() as con:
        rows = con.execute('SELECT request_json FROM max_outbound_attempts').fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])['messages'][0]['keyboard']['inline_keyboard'][0][0]['callback_data'] == 'ia:continue'
    original = max_delivery.queue
    def fail_after_queue(**kwargs):
        original(**kwargs)
        raise RuntimeError('interrupted before publication commit')
    monkeypatch.setattr(max_delivery, 'queue', fail_after_queue)
    with pytest.raises(RuntimeError):
        publication.publish(**{**args, 'publication_key': 'max:rollback'})
    with get_ctx().sql.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM max_outbound_attempts').fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM conversation_publications').fetchone()[0] == 1


def test_selected_max_route_uses_committed_delivery_and_exact_receipt():
    from adaos.services import conversation_publication as publication, conversation_delivery, durable_delivery
    conversation_store.upsert_conversation(conversation_id='max:route', owner='skill:test', webspace_id='test',
                                          policy={'output_route_ids': ['max:owned-route']})
    durable_delivery.create_reply_route('max:route', transport='max', route_id='max:owned-route',
                                       destination_ref=target(), principal_scope=['user:test'])
    result = publication.publish(conversation_id='max:route', owner='skill:test', webspace_id='test',
                                 publication_key='max:selected', text='Готово', route_ids=['max:owned-route'])
    assert conversation_delivery.dispatch_once()
    with get_ctx().sql.connect() as con:
        con.row_factory = __import__('sqlite3').Row
        item = dict(con.execute('SELECT * FROM max_outbound_attempts').fetchone())
    assert item['status'] == 'pending'
    assert publication.output_status(result['publication_id'])[0]['status'] == 'queued_for_relay'
    class Root:
        def request(self, method, path, **kwargs):
            assert path.endswith('/send')
            return {'ok': True, 'accepted': True}
    assert max_delivery.dispatch_once(Root())
    assert publication.output_status(result['publication_id'])[0]['status'] == 'accepted_by_root'
    max_delivery.complete(receipt(item))
    assert publication.output_status(result['publication_id'])[0]['status'] == 'delivered'
    with get_ctx().sql.connect() as con:
        proof = json.loads(con.execute('SELECT payload_json FROM conversation_delivery_attempts WHERE attempt_id=?',
                                     (item['durable_attempt_id'],)).fetchone()[0])['receipt']
    assert proof['root_accepted'] is True and proof['external_message_ids'] == ['mid.provider']


def test_redacted_publication_is_not_submitted_to_max_root():
    from adaos.services import conversation_publication as publication
    conversation_store.upsert_conversation(conversation_id='max:redacted', owner='skill:test', webspace_id='test')
    result = publication.publish(conversation_id='max:redacted', owner='skill:test', webspace_id='test',
                                 publication_key='max:redacted', text='Removed', meta={**target(), 'io_type': 'max'})
    with get_ctx().sql.connect() as con:
        con.execute("UPDATE conversation_messages SET redaction_state='redacted' WHERE message_id=?", (result['message_id'],))
        con.commit()
    class NoSend:
        def request(self, *args, **kwargs): raise AssertionError('redacted text left the Hub')
    assert max_delivery.dispatch_once(NoSend())
    with get_ctx().sql.connect() as con:
        row = con.execute('SELECT status,last_error FROM max_outbound_attempts').fetchone()
    assert row[0] == 'failed'
    assert row[1] == 'max_source_removed'
