from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import time

import pytest

from adaos.services import conversation_delivery as delivery
from adaos.services import conversation_publication as publication
from adaos.services import conversation_store, durable_delivery
from adaos.services.agent_context import get_ctx


def setup_output(*, routes=2):
    conversation_store.upsert_conversation(conversation_id="test:outputs", owner="skill:test",
        webspace_id="test", policy={"output_route_ids": [f"route:{i}" for i in range(routes)]})
    for i in range(routes):
        durable_delivery.create_reply_route("test:outputs", transport="telegram",
            route_id=f"route:{i}", destination_ref={"hub_id": "test"}, principal_scope=["user:test"])
    return dict(conversation_id="test:outputs", owner="skill:test", webspace_id="test",
                publication_key="semantic:one", text="Completed", route_ids=[f"route:{i}" for i in range(routes)])


@pytest.mark.parametrize("future_intent", [False, True])
def test_idle_delivery_poll_does_not_acquire_sqlite_writer(monkeypatch, future_intent):
    publication.ensure_schema()
    if future_intent:
        receipt = publication.publish(**setup_output(routes=1))
        with get_ctx().sql.connect() as con:
            con.execute("UPDATE conversation_output_intents SET next_attempt_at=? WHERE intent_id=?",
                        (time.time() + 3600, receipt["intent_ids"][0]))
            con.commit()
    statements = []
    sql = get_ctx().sql
    connect = sql.connect

    @contextmanager
    def traced_connect(*args, **kwargs):
        with connect(*args, **kwargs) as con:
            con.set_trace_callback(statements.append)
            yield con

    monkeypatch.setattr(sql, "connect", traced_connect)
    assert delivery.claim_next() is None
    assert delivery.reconcile_once() is False
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_expired_unsent_lease_is_reclaimed_without_pending_intents():
    publication.publish(**setup_output(routes=1))
    first = delivery.claim_next()
    assert first is not None
    recovered = delivery.claim_next(now=first["updated_at"] + 121)
    assert recovered is not None
    assert recovered["intent_id"] == first["intent_id"]
    assert recovered["lease_token"] != first["lease_token"]
    assert recovered["attempt_count"] == 2


def test_atomic_idempotent_publication_independent_targets():
    args = setup_output()
    first = publication.publish(**args)
    assert first == publication.publish(**args)
    assert first["status"] == "committed"
    assert len(conversation_store.list_messages("test:outputs")) == 1
    assert len(first["intent_ids"]) == 2
    class Adapter:
        def send(self, envelope, route, attempt):
            return {"delivered": True, "receipt": {"external_message_ids": ["200"]}}
    assert delivery.dispatch_once(Adapter())
    statuses = publication.output_status(first["publication_id"])
    assert sorted(item["status"] for item in statuses) == ["delivered", "pending"]
    assert delivery.dispatch_once(Adapter())
    assert not delivery.dispatch_once(Adapter())
    assert len(conversation_store.list_messages("test:outputs")) == 1


def test_first_publication_and_replay_keep_canonical_router_scope():
    from types import SimpleNamespace
    args = {**setup_output(routes=0), "channel_id": "builder", "thread_id": "project:retouch"}
    events = []
    bus = SimpleNamespace(publish=events.append)
    first = publication.publish(**args, bus=bus)
    assert publication.publish(**args, bus=bus) == first
    for event in events:
        assert event.payload["conversation_id"] == args["conversation_id"]
        assert event.payload["webspace_id"] == args["webspace_id"]
        assert event.payload["dialog_channel_id"] == "builder"
        assert event.payload["thread_id"] == args["thread_id"]
        meta = event.payload["_meta"]
        assert meta["conversation_owner"] == args["owner"]
        assert meta["webspace_id"] == args["webspace_id"]
        assert meta["dialog_channel_id"] == "builder"
        assert meta["conversation_topic_id"] == args["thread_id"]
    assert len(events) == 2
    assert len(conversation_store.list_messages(args["conversation_id"])) == 1


def test_existing_user_turn_keeps_author_and_actions_without_duplicate():
    args = setup_output(routes=1)
    stored = conversation_store.append_message(conversation_id=args['conversation_id'],
        owner=args['owner'], webspace_id=args['webspace_id'], channel_id='general',
        text=args['text'], role='user', payload={'id':'message:ingress',
            'actions':[{'id':'inspect','label':'Inspect'}]})
    events=[]
    from types import SimpleNamespace
    receipt = publication.publish(**args, existing_message_id=stored['id'],bus=SimpleNamespace(publish=events.append))
    assert publication.publish(**args, existing_message_id=stored['id']) == receipt
    assert receipt['message_id'] == stored['id'] and len(receipt['intent_ids']) == 1
    messages = conversation_store.list_messages(args['conversation_id'])
    assert len(messages) == 1 and messages[0]['from'] == 'user'
    assert messages[0]['actions'] == stored['actions']
    assert events[0].payload['_meta']['output_delivery_owned'] is True


def test_shared_history_projects_to_exact_owned_local_view_without_moving_messages():
    from types import SimpleNamespace
    conversation_store.upsert_conversation(conversation_id='shared', owner='skill:test', webspace_id='global')
    for ws in ('origin', 'other'):
        conversation_store.upsert_dialog_channel(webspace_id=ws, channel_id='builder',
            owner='skill:test', conversation_id='shared')
    events = []
    args = dict(conversation_id='shared', owner='skill:test', webspace_id='global',
                publication_key='result', text='Completed', thread_id='project:one',
                channel_id='builder', projection_webspace_id='origin', bus=SimpleNamespace(publish=events.append))
    receipt = publication.publish(**args)
    assert publication.publish(**args) == receipt
    assert len(events) == 2
    assert all(event.payload['webspace_id'] == 'origin' and event.payload['_meta']['webspace_id'] == 'origin' for event in events)
    assert conversation_store.get_conversation('shared')['webspace_id'] == 'global'
    stored = conversation_store.list_messages('shared')
    assert len(stored) == 1 and stored[0]['webspace_id'] == 'global'
    assert all(publication.is_committed_projection_event(event) for event in events)
    import copy
    for key, value in [('text','Forged'), ('thread_id','project:other'), ('webspace_id','unowned')]:
        altered = copy.deepcopy(events[0])
        altered.payload[key] = value
        assert not publication.is_committed_projection_event(altered)
    from dataclasses import replace
    altered = replace(events[0], source='skill:untrusted')
    assert not publication.is_committed_projection_event(altered)
    with pytest.raises(ValueError, match='idempotency conflict'):
        publication.publish(**{**args, 'projection_webspace_id': 'other'})


@pytest.mark.parametrize('owner,conversation,status', [('skill:other','shared','active'), ('skill:test','foreign','active'), ('skill:test','shared','paused')])
def test_projection_rejects_unowned_foreign_and_inactive_channel(owner, conversation, status):
    conversation_store.upsert_conversation(conversation_id='shared', owner='skill:test', webspace_id='global')
    conversation_store.upsert_dialog_channel(webspace_id='origin', channel_id='builder', owner=owner,
        conversation_id=conversation, status=status)
    with pytest.raises(ValueError, match='owned active channel'):
        publication.publish(conversation_id='shared', owner='skill:test', webspace_id='global',
            publication_key='result', text='Completed', channel_id='builder', projection_webspace_id='origin')
    assert conversation_store.list_messages('shared') == []


@pytest.mark.parametrize('change', ['content', 'thread', 'redaction', 'conversation'])
def test_existing_turn_must_match_owned_active_content(change):
    args = setup_output(routes=1)
    stored = conversation_store.append_message(conversation_id=args['conversation_id'],
        owner=args['owner'], webspace_id=args['webspace_id'], channel_id='general',
        text=args['text'], role='hub', payload={'id':'message:stored'})
    if change == 'content': args['text'] = 'Different'
    if change == 'thread': args['thread_id'] = 'another-thread'
    if change in {'redaction', 'conversation'}:
        column, value = ('redaction_state','redacted') if change == 'redaction' else ('conversation_id','foreign')
        with get_ctx().sql.connect() as con:
            con.execute(f'UPDATE conversation_messages SET {column}=? WHERE message_id=?', (value,stored['id']))
            con.commit()
    with pytest.raises(ValueError, match='existing message'):
        publication.publish(**args, existing_message_id=stored['id'])
    with get_ctx().sql.connect() as con:
        assert con.execute('SELECT count(*) FROM conversation_output_intents').fetchone()[0] == 0


def test_discovered_pairing_uses_frozen_explicit_transport_address(monkeypatch):
    from types import SimpleNamespace
    from adaos.services import telegram_delivery
    import requests
    args=setup_output(routes=0)
    durable_delivery.create_reply_route(args['conversation_id'],transport='telegram',route_id='frozen',
        destination_ref={'hub_id':'test','routing_mode':'hub_pairing','bot_id':'observed-bot','chat_id':'observed-chat'},
        principal_scope=['user:test'])
    conversation_store.upsert_conversation(conversation_id=args['conversation_id'],owner=args['owner'],
        webspace_id=args['webspace_id'],policy={'output_route_ids':['frozen']})
    receipt=publication.publish(**{**args,'route_ids':['frozen']})
    sent=[]
    monkeypatch.setattr(requests,'post',lambda url,**kw:sent.append(kw['json']) or SimpleNamespace(status_code=202))
    assert delivery.dispatch_once()
    assert len(sent)==1 and sent[0]['bot_id']=='observed-bot' and sent[0]['chat_id']=='observed-chat'
    attempt=telegram_delivery.get_attempt(sent[0]['_protocol']['delivery_attempt_id'])
    assert 'routing_mode' not in attempt and attempt['bot_id']=='observed-bot'
    assert publication.output_status(receipt['publication_id'])[0]['status']=='accepted_by_root'


def test_crash_inside_publication_rolls_back_message_and_intents(monkeypatch):
    args = setup_output()
    original = durable_delivery.enqueue_response
    count = 0
    def fail_second(*a, **kw):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("crash")
        return original(*a, **kw)
    monkeypatch.setattr(durable_delivery, "enqueue_response", fail_second)
    with pytest.raises(RuntimeError, match="crash"):
        publication.publish(**args)
    assert conversation_store.list_messages("test:outputs") == []
    with get_ctx().sql.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM conversation_response_outbox").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM conversation_output_intents").fetchone()[0] == 0


def test_changed_payload_and_cross_conversation_route_rejected():
    args = setup_output()
    publication.publish(**args)
    with pytest.raises(ValueError, match="idempotency conflict"):
        publication.publish(**{**args, "text": "Different"})
    durable_delivery.create_reply_route("other", transport="telegram", route_id="foreign",
        destination_ref={}, principal_scope=["user:test"])
    with pytest.raises(ValueError, match="belongs to another"):
        publication.publish(**{**args, "publication_key": "two", "route_ids": ["foreign"]})
    assert len(conversation_store.list_messages("test:outputs")) == 1


def test_root_acceptance_and_expired_lease_do_not_claim_delivery_or_resend():
    receipt = publication.publish(**setup_output(routes=1))
    class Accepted:
        def send(self, *args):
            return {"accepted": True}
    delivery.dispatch_once(Accepted())
    assert publication.output_status(receipt["publication_id"])[0]["status"] == "accepted_by_root"
    assert delivery.claim_next(now=time.time() + 121) is None
    assert publication.output_status(receipt["publication_id"])[0]["status"] == "reconciling"


@pytest.mark.parametrize("policy", [{"output_route_ids": []}, {}])
def test_revocation_and_policy_reset_suppress_unsent_targets(policy):
    receipt = publication.publish(**setup_output(routes=1))
    with get_ctx().sql.connect() as con:
        con.execute("UPDATE conversation_conversations SET policy_json=? WHERE conversation_id=?",
                    (json.dumps(policy), "test:outputs"))
        con.commit()
    class Forbidden:
        def send(self, *args):
            raise AssertionError("revoked output sent")
    delivery.dispatch_once(Forbidden())
    assert publication.output_status(receipt["publication_id"])[0]["status"] == "suppressed"


def test_expired_external_route_does_not_suppress_canonical_message():
    args = setup_output(routes=1)
    with get_ctx().sql.connect() as con:
        row = con.execute("SELECT payload_json FROM conversation_reply_routes WHERE route_id='route:0'").fetchone()
        route = json.loads(row[0])
        route["status"] = "revoked"
        con.execute("UPDATE conversation_reply_routes SET status='revoked',payload_json=? WHERE route_id='route:0'", (json.dumps(route),))
        con.commit()
    receipt = publication.publish(**args)
    assert receipt["status"] == "committed" and receipt["intent_ids"] == []
    assert len(conversation_store.list_messages("test:outputs")) == 1


def test_projection_failure_cannot_uncommit_and_redacted_retry_never_reemits():
    from types import SimpleNamespace
    args = setup_output(routes=1)
    def offline(_):
        raise RuntimeError("projection unavailable")
    first = publication.publish(**args, bus=SimpleNamespace(publish=offline))
    events = []
    assert publication.publish(**args, bus=SimpleNamespace(publish=events.append)) == first
    assert len(events) == 1
    conversation_store.redact_messages([first["message_id"]])
    assert publication.publish(**args, bus=SimpleNamespace(publish=events.append)) == first
    assert len(events) == 1
    class Forbidden:
        def send(self, *args):
            raise AssertionError("redacted output sent")
    delivery.dispatch_once(Forbidden())
    assert publication.output_status(first["publication_id"])[0]["status"] == "suppressed"


def test_worker_transport_delay_does_not_block_event_loop():
    from adaos.services.agent_context import set_ctx
    publication.publish(**setup_output(routes=1))
    ctx = get_ctx()
    class Slow:
        def send(self, *args):
            time.sleep(0.15)
            return {"accepted": True}
    def work():
        set_ctx(ctx)
        return delivery.dispatch_once(Slow())
    async def check():
        job = asyncio.create_task(asyncio.to_thread(work))
        await asyncio.sleep(0.02)
        assert not job.done()
        await job
    asyncio.run(check())
