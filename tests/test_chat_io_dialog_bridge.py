from __future__ import annotations

from types import SimpleNamespace

import pytest

from adaos.domain import Event
from adaos.services.eventbus import LocalEventBus
from adaos.services.chat_io import nlu_bridge
from adaos.services import conversation_interactions


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


def _telegram_action_envelope(token: str) -> dict:
    return {
        "event_id": "event-action-101",
        "kind": "io.input",
        "dedup_key": "tg:bot:42:101",
        "payload": {
            "type": "action",
            "source": "telegram",
            "bot_id": "main-bot",
            "hub_id": "sn-test",
            "chat_id": "42",
            "user_id": "7",
            "update_id": "101",
            "payload": {
                "action": {"id": token},
                "meta": {"msg_id": 56},
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


async def test_raw_http_fallback_input_uses_same_dialog_contract(monkeypatch) -> None:
    bus = LocalEventBus()
    dialog_events: list[Event] = []
    claims: list[dict] = []

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
        lambda _key: {"status": "dispatched"},
    )
    bus.subscribe("dialog.user_message", lambda event: dialog_events.append(event))
    nlu_bridge.register_chat_nlu_bridge(bus)

    # HTTP /io/bus/tg.input receives ChatInputEvent without an outer envelope.
    raw_input = _telegram_envelope()["payload"]
    bus.publish(
        Event(
            type="tg.input.sn-test",
            source="test.http_fallback",
            ts=2.0,
            payload=raw_input,
        )
    )
    assert await bus.wait_for_idle(timeout=1.0)

    assert len(dialog_events) == 1
    assert dialog_events[0].payload["text"] == "Строитель, покажи текущий проект"
    assert dialog_events[0].payload["webspace_id"] == "dev1-dev"
    assert claims[0]["idempotency_key"] == "transport:telegram:main-bot:42:100"


async def test_telegram_callback_resumes_durable_interaction_without_nlu(monkeypatch) -> None:
    bus = LocalEventBus()
    monkeypatch.setattr(
        nlu_bridge,
        "get_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(subnet_id="sn-test"), bus=bus),
    )
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.telegram.interaction",
        owner="skill:builder",
        prompt="Choose",
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [{"value": "prototype", "label": "Prototype", "description": None}],
            "sensitive": False,
        },
        actions=[
            {
                "action_id": "prototype",
                "label": "Prototype",
                "command": "builder.change.route",
                "value": "prototype",
                "risk": "local_reversible",
                "confirmation_required": False,
            }
        ],
        interaction_id="interaction.telegram.callback",
    )
    profile = conversation_interactions.standard_capability_profile("telegram")
    presentation = conversation_interactions.negotiate_presentation(interaction, profile)
    token = presentation["actions"][0]["token"]
    responses: list[Event] = []
    user_messages: list[Event] = []
    bus.subscribe("conversation.interaction.responded", lambda event: responses.append(event))
    bus.subscribe("dialog.user_message", lambda event: user_messages.append(event))
    nlu_bridge.register_chat_nlu_bridge(bus)

    bus.publish(
        Event(
            type="tg.input.sn-test",
            source="test",
            ts=3.0,
            payload=_telegram_action_envelope(token),
        )
    )
    assert await bus.wait_for_idle(timeout=1.0)

    assert user_messages == []
    assert len(responses) == 1
    assert responses[0].payload["response"]["source"] == "action"
    assert responses[0].payload["response"]["values"]["choice"] == "prototype"


async def test_telegram_exact_button_label_uses_same_interaction_without_builder_or_nlu(monkeypatch) -> None:
    bus = LocalEventBus()
    monkeypatch.setattr(
        nlu_bridge,
        "get_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(subnet_id="sn-test"), bus=bus),
    )
    interaction = conversation_interactions.create_interaction(
        conversation_id="conv.telegram.text-action",
        owner="skill:builder",
        prompt="Choose",
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [{"value": "process", "label": "Показать процесс", "description": None}],
            "sensitive": False,
        },
        actions=[
            {
                "action_id": "process",
                "label": "Показать процесс",
                "command": "builder.process.inspect",
                "value": "process",
                "risk": "read",
                "confirmation_required": False,
            }
        ],
        interaction_id="interaction.telegram.text-action",
    )
    conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("telegram"),
    )
    monkeypatch.setattr(
        nlu_bridge.conversation_store,
        "get_active_dialog_channel",
        lambda webspace_id: {
            "webspace_id": webspace_id,
            "channel_id": "builder",
            "conversation_id": "conv.telegram.text-action",
        },
    )
    responses: list[Event] = []
    user_messages: list[Event] = []
    bus.subscribe("conversation.interaction.responded", lambda event: responses.append(event))
    bus.subscribe("dialog.user_message", lambda event: user_messages.append(event))
    nlu_bridge.register_chat_nlu_bridge(bus)
    envelope = _telegram_envelope(dedup_key="tg:bot:42:102")
    envelope["payload"]["update_id"] = "102"
    envelope["payload"]["payload"]["text"] = "Показать процесс"

    bus.publish(Event(type="tg.input.sn-test", source="test", ts=4.0, payload=envelope))
    assert await bus.wait_for_idle(timeout=1.0)

    assert user_messages == []
    assert len(responses) == 1
    assert responses[0].source == "chat_io.interaction_text_fallback"
    assert responses[0].payload["response"]["source"] == "action"
    assert responses[0].payload["response"]["values"]["command"] == "builder.process.inspect"
    assert responses[0].payload["response"]["metadata"]["text_fallback"] is True
