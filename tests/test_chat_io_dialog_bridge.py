from __future__ import annotations

from types import SimpleNamespace

import pytest

from adaos.domain import Event
from adaos.services.eventbus import LocalEventBus
from adaos.services.chat_io import nlu_bridge


pytestmark = pytest.mark.anyio


def _telegram_envelope(*, dedup_key: str = "tg:bot:42:100") -> dict:
    return {
        "event_id": "event-100",
        "kind": "io.input",
        "dedup_key": dedup_key,
        "meta": {"trace_id": "trace-100"},
        "payload": {
            "type": "text",
            "source": "telegram",
            "bot_id": "main-bot",
            "hub_id": "sn-test",
            "chat_id": "42",
            "user_id": "7",
            "update_id": "100",
            "route": {
                "via": "session",
                "webspace_id": "dev1-dev",
                "dialog_channel_id": "builder",
            },
            "payload": {
                "text": "Строитель, покажи текущий проект",
                "meta": {"msg_id": 55, "lang": "ru"},
            },
        },
    }


async def test_telegram_text_enters_canonical_dialog_with_utf8_and_route(monkeypatch) -> None:
    bus = LocalEventBus()
    claims: list[dict] = []
    dispatched: list[str] = []
    dialog_events: list[Event] = []
    nlu_events: list[Event] = []

    monkeypatch.setattr(
        nlu_bridge,
        "get_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(subnet_id="sn-test"), bus=bus),
    )
    monkeypatch.setattr(
        nlu_bridge.conversation_store,
        "claim_transport_ingress",
        lambda **kwargs: claims.append(dict(kwargs)) or {"ok": True, "claimed": True},
    )
    monkeypatch.setattr(
        nlu_bridge.conversation_store,
        "mark_transport_ingress_dispatched",
        lambda key: dispatched.append(key) or {"status": "dispatched"},
    )
    bus.subscribe("dialog.user_message", lambda event: dialog_events.append(event))
    bus.subscribe("nlp.intent.detect.request", lambda event: nlu_events.append(event))
    nlu_bridge.register_chat_nlu_bridge(bus)

    bus.publish(
        Event(
            type="tg.input.sn-test",
            source="test",
            ts=1.0,
            payload=_telegram_envelope(),
        )
    )
    assert await bus.wait_for_idle(timeout=1.0)

    assert nlu_events == []
    assert len(dialog_events) == 1
    payload = dialog_events[0].payload
    assert payload["text"] == "Строитель, покажи текущий проект"
    assert payload["webspace_id"] == "dev1-dev"
    assert payload["_meta"]["route_id"] == "telegram"
    assert payload["_meta"]["dialog_channel_id"] == "builder"
    assert payload["_meta"]["lang"] == "ru"
    assert payload["_meta"]["reply_to"] == 55
    assert payload["_meta"]["channel_capabilities"]["rich_views"] is False
    assert claims[0]["idempotency_key"] == "transport:tg:bot:42:100"
    assert dispatched == ["transport:tg:bot:42:100"]


async def test_duplicate_telegram_update_is_not_dispatched_again(monkeypatch) -> None:
    bus = LocalEventBus()
    dialog_events: list[Event] = []
    attempts = 0

    monkeypatch.setattr(
        nlu_bridge,
        "get_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(subnet_id="sn-test"), bus=bus),
    )

    def _claim(**_kwargs):
        nonlocal attempts
        attempts += 1
        return {"ok": True, "claimed": attempts == 1, "duplicate": attempts > 1}

    monkeypatch.setattr(nlu_bridge.conversation_store, "claim_transport_ingress", _claim)
    monkeypatch.setattr(
        nlu_bridge.conversation_store,
        "mark_transport_ingress_dispatched",
        lambda _key: {"status": "dispatched"},
    )
    bus.subscribe("dialog.user_message", lambda event: dialog_events.append(event))
    nlu_bridge.register_chat_nlu_bridge(bus)
    event = Event(
        type="tg.input.sn-test",
        source="test",
        ts=1.0,
        payload=_telegram_envelope(),
    )

    bus.publish(event)
    bus.publish(event)
    assert await bus.wait_for_idle(timeout=1.0)

    assert attempts == 2
    assert len(dialog_events) == 1
