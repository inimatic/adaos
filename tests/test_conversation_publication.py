from __future__ import annotations

import asyncio
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
