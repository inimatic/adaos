from __future__ import annotations


def test_committed_publication_projection_cannot_become_a_second_transport_sender():
    from adaos.services.router.telegram_projection import _telegram_output_projection
    assert _telegram_output_projection({"text": "Committed", "from": "hub"},
        {"io_type": "telegram", "chat_id": "test", "output_delivery_owned": True}) is None

import pytest
from types import SimpleNamespace
import hashlib
import json

from adaos.domain import Event
from adaos.services import telegram_delivery
from adaos.services.eventbus import LocalEventBus
from adaos.services.router.service import RouterService


def test_telegram_outbound_attempt_is_idempotent_and_receipted() -> None:
    first = telegram_delivery.claim_outbound(
        "tg-dialog:hub:bot:42:message",
        hub_id="hub",
        bot_id="bot",
        chat_id="42",
        message_count=1,
        response_envelope_id="response:42",
    )
    duplicate = telegram_delivery.claim_outbound(
        "tg-dialog:hub:bot:42:message",
        hub_id="hub",
        bot_id="bot",
        chat_id="42",
        message_count=1,
        response_envelope_id="response:42",
    )
    assert duplicate["attempt_id"] == first["attempt_id"]

    delivered = telegram_delivery.complete_outbound(
        first["attempt_id"],
        delivered=True,
        receipt={"transport": "telegram", "external_message_ids": ["900"]},
        now="2026-08-05T20:00:00+00:00",
    )
    assert delivered["status"] == "delivered"
    assert delivered["receipt"]["external_message_ids"] == ["900"]
    assert telegram_delivery.get_attempt(first["attempt_id"]) == delivered


def test_telegram_outbound_attempt_rejects_operation_key_conflict() -> None:
    telegram_delivery.claim_outbound(
        "tg-dialog:conflict",
        hub_id="hub",
        bot_id="bot",
        chat_id="42",
        message_count=1,
    )
    with pytest.raises(telegram_delivery.TelegramDeliveryError, match="idempotency conflict"):
        telegram_delivery.claim_outbound(
            "tg-dialog:conflict",
            hub_id="hub",
            bot_id="bot",
            chat_id="99",
            message_count=1,
        )


@pytest.mark.asyncio
async def test_router_persists_backend_telegram_receipt(tmp_path) -> None:
    attempt = telegram_delivery.claim_outbound(
        "tg-dialog:receipt",
        hub_id="hub",
        bot_id="bot",
        chat_id="42",
        message_count=1,
    )
    router = RouterService(eventbus=LocalEventBus(), base_dir=tmp_path)
    await router._on_telegram_delivery_receipt(
        Event(
            type="tg.delivery.receipt",
            source="io.nats",
            ts=1.0,
            payload={
                "schema": "adaos.telegram.delivery_receipt.v1",
                "receipt_id": f"tg-receipt:{attempt['attempt_id']}",
                "delivery_attempt_id": attempt["attempt_id"],
                "operation_key": attempt["operation_key"],
                "delivered": True,
                "transport": "telegram",
                "external_message_ids": ["901"],
                "duplicate_suppressed": 0,
                "error": None,
                "completed_at": "2026-08-05T20:00:00+00:00",
            },
        )
    )
    stored = telegram_delivery.get_attempt(attempt["attempt_id"])
    assert stored is not None and stored["status"] == "delivered"
    assert stored["receipt"]["external_message_ids"] == ["901"]


def test_hub_pairing_claim_binds_content_and_retains_late_delivery() -> None:
    arguments = dict(hub_id="hub", routing_mode="hub_pairing", message_count=1,
                     content_digest="sha256:" + hashlib.sha256(b"brief").hexdigest())
    attempt = telegram_delivery.claim_outbound("builder-notify:late", **arguments)
    assert attempt["bot_id"] is None and attempt["chat_id"] is None
    with pytest.raises(telegram_delivery.TelegramDeliveryError, match="idempotency conflict"):
        telegram_delivery.claim_outbound("builder-notify:late", **{**arguments, "content_digest": "sha256:" + "0" * 64})
    telegram_delivery.complete_outbound(attempt["attempt_id"], delivered=False, error="transport_timeout")
    with pytest.raises(telegram_delivery.TelegramDeliveryError, match="identifiers"):
        telegram_delivery.complete_outbound(attempt["attempt_id"], delivered=True, receipt={"external_message_ids": []})
    reconciled = telegram_delivery.complete_outbound(
        attempt["attempt_id"], delivered=True, receipt={"external_message_ids": ["902"]})
    assert reconciled["status"] == "delivered"
    assert reconciled["receipt_history"][0]["error"] == "transport_timeout"


@pytest.mark.asyncio
async def test_builder_root_acceptance_is_distinct_from_bot_delivery(tmp_path, monkeypatch) -> None:
    from adaos.services import conversation_publication, conversation_delivery, conversation_store, durable_delivery
    from adaos.services.agent_context import get_ctx
    import requests
    router = RouterService(eventbus=LocalEventBus(), base_dir=tmp_path)
    get_ctx().config = SimpleNamespace(root_settings=SimpleNamespace(base_url="https://root.invalid"))
    conversation_store.upsert_conversation(conversation_id="builder:202", owner="skill:builder_skill", webspace_id="test",
        policy={"output_route_ids": ["route:202"]})
    durable_delivery.create_reply_route("builder:202", transport="telegram", route_id="route:202",
        destination_ref={"hub_id": "hub", "routing_mode": "hub_pairing"}, principal_scope=["user:local"])
    committed = conversation_publication.publish(publication_key="brief.202", conversation_id="builder:202",
        owner="skill:builder_skill", webspace_id="test", text="Builder qualification brief", route_ids=["route:202"])
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, **kw: sent.append(kw["json"]) or SimpleNamespace(status_code=202))
    import asyncio
    await asyncio.to_thread(conversation_delivery.dispatch_once)
    assert len(sent) == 1
    protocol = sent[0]["_protocol"]
    assert protocol["receipt_subject"] == "tg.receipt.hub"
    attempt = telegram_delivery.get_attempt(protocol["delivery_attempt_id"])
    assert attempt["status"] == "claimed"
    assert conversation_publication.output_status(committed["publication_id"])[0]["status"] == "accepted_by_root"
    payload = {
        "schema": "adaos.telegram.delivery_receipt.v1", "receipt_id": "root.receipt.202",
        "delivery_attempt_id": attempt["attempt_id"], "operation_key": "wrong.operation",
        "delivered": True, "transport": "telegram", "external_message_ids": ["903"],
        "duplicate_suppressed": 0, "error": None, "completed_at": "2026-10-17T07:00:00Z",
    }
    async def receive(value):
        await router._on_telegram_delivery_receipt(Event(type="tg.delivery.receipt", source="io.nats", ts=2.0, payload=value))
    await receive(payload)
    assert telegram_delivery.get_attempt(attempt["attempt_id"])["status"] == "claimed"
    payload["operation_key"] = attempt["operation_key"]
    await receive({**payload, "external_message_ids": [], "duplicate_suppressed": 1})
    assert telegram_delivery.get_attempt(attempt["attempt_id"])["status"] == "claimed"
    await receive(payload)
    stored = telegram_delivery.get_attempt(attempt["attempt_id"])
    assert stored["status"] == "delivered" and stored["receipt"]["external_message_ids"] == ["903"]
    await asyncio.to_thread(conversation_delivery.dispatch_once)
    assert len(sent) == 1
    assert not (tmp_path / "state/router/builder-deliveries").exists()


def _builder_claim(key, **overrides):
    arguments = dict(hub_id="hub", routing_mode="hub_pairing", message_count=1,
                     content_digest="sha256:" + hashlib.sha256(b"identical stage").hexdigest())
    return telegram_delivery.claim_outbound(key, **{**arguments, **overrides})


def _duplicate_ack(attempt):
    return {"operation_key": attempt["operation_key"], "external_message_ids": [],
            "duplicate_suppressed": 1, "transport": "telegram"}


@pytest.mark.asyncio
async def test_builder_duplicate_keeps_ack_separate_from_actual_delivery_and_preserves_reference(tmp_path):
    original = _builder_claim("builder-notify:original")
    telegram_delivery.complete_outbound(original["attempt_id"], delivered=True,
        receipt={"operation_key": original["operation_key"], "external_message_ids": ["905"]})
    duplicate = _builder_claim("builder-notify:duplicate")
    telegram_delivery.complete_outbound(duplicate["attempt_id"], delivered=False, error="http_timeout")
    router = RouterService(eventbus=LocalEventBus(), base_dir=tmp_path)
    await router._on_telegram_delivery_receipt(Event(type="tg.delivery.receipt", source="io.nats", ts=1,
        payload={"schema": "adaos.telegram.delivery_receipt.v1", "receipt_id": "duplicate.receipt",
                 "delivery_attempt_id": duplicate["attempt_id"], "delivered": True,
                 **_duplicate_ack(duplicate), "error": None, "completed_at": "2026-10-20T00:00:00Z"}))
    stored = telegram_delivery.get_attempt(duplicate["attempt_id"])
    # Equal text from a different operation is not proof of this delivery.
    assert stored["status"] == "failed"
    assert not stored.get("duplicate_of")
    assert not (tmp_path / "state/router/builder-deliveries").exists()


@pytest.mark.parametrize("overrides", [
    {"hub_id": "other-hub"}, {"content_digest": "sha256:" + "0" * 64}, {"message_count": 2},
])
def test_builder_duplicate_cannot_borrow_receipt_from_different_route_or_content(overrides):
    original = _builder_claim("builder-notify:proof", **overrides)
    telegram_delivery.complete_outbound(original["attempt_id"], delivered=True,
        receipt={"operation_key": original["operation_key"], "external_message_ids": ["906"]})
    duplicate = _builder_claim("builder-notify:unproven")
    with pytest.raises(telegram_delivery.TelegramDeliveryError, match="no matching proven"):
        telegram_delivery.reconcile_builder_duplicate(duplicate["attempt_id"], receipt=_duplicate_ack(duplicate))
    assert telegram_delivery.get_attempt(duplicate["attempt_id"])["status"] == "claimed"


@pytest.mark.parametrize("patch", [{"operation_key": "wrong"}, {"duplicate_suppressed": 0},
                                    {"external_message_ids": ["907"]}])
def test_duplicate_ack_must_be_bound_and_actually_suppressed(patch):
    duplicate = _builder_claim("builder-notify:invalid-ack")
    with pytest.raises(telegram_delivery.TelegramDeliveryError, match="not bound"):
        telegram_delivery.reconcile_builder_duplicate(duplicate["attempt_id"],
            receipt={**_duplicate_ack(duplicate), **patch})


def test_duplicate_reference_is_builder_only_and_requires_matching_receipt_operation():
    original = _builder_claim("builder-notify:wrong-proof")
    telegram_delivery.complete_outbound(original["attempt_id"], delivered=True,
        receipt={"external_message_ids": ["908"]})
    duplicate = _builder_claim("builder-notify:no-bound-proof")
    with pytest.raises(telegram_delivery.TelegramDeliveryError, match="no matching proven"):
        telegram_delivery.reconcile_builder_duplicate(duplicate["attempt_id"], receipt=_duplicate_ack(duplicate))
    other = _builder_claim("tg-dialog:other")
    with pytest.raises(telegram_delivery.TelegramDeliveryError, match="Only single Builder"):
        telegram_delivery.reconcile_builder_duplicate(other["attempt_id"], receipt=_duplicate_ack(other))
