from __future__ import annotations

import pytest

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
