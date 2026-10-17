from __future__ import annotations

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
    from adaos.services.router import service as module
    router = RouterService(eventbus=LocalEventBus(), base_dir=tmp_path)
    router._notification_context = SimpleNamespace(
        config=SimpleNamespace(node_id="owner", subnet_id="hub", root_settings=SimpleNamespace(base_url="https://root.invalid")),
        settings=SimpleNamespace())
    router._rules = [{"target": {"io_type": "telegram", "node_id": "this"}}]
    monkeypatch.setattr(module, "load_subnet_alias", lambda **_: None)
    sent = []
    monkeypatch.setattr(module.requests, "post", lambda url, **kw: sent.append(kw["json"]) or SimpleNamespace(status_code=202))
    event = Event(type="ui.notify", source="test", ts=1.0, payload={
        "id": "brief.202", "text": "Builder qualification brief",
        "_meta": {"schema": "adaos.builder.chat_notification.v1"}})
    await router._handle_notify_event(event)
    assert len(sent) == 1
    protocol = sent[0]["_protocol"]
    assert protocol["receipt_subject"] == "tg.receipt.hub"
    attempt = telegram_delivery.get_attempt(protocol["delivery_attempt_id"])
    assert attempt["status"] == "claimed"
    receipt_path = next((tmp_path / "state/router/builder-deliveries").glob("*.json"))
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["phase"] == "accepted"
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
    stored = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert stored["phase"] == "delivered" and stored["receipt"]["external_message_ids"] == ["903"]
    await router._handle_notify_event(event)
    assert len(sent) == 1
