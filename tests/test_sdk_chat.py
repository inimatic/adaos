from __future__ import annotations

from adaos.domain import Event
from adaos.sdk import chat
from adaos.services import conversation_response, conversation_store
from adaos.services.eventbus import LocalEventBus


def test_chat_send_materializes_visible_message_and_ledger() -> None:
    bus = LocalEventBus()
    seen: list[Event] = []
    bus.subscribe("io.out.chat.append", lambda ev: seen.append(ev))

    result = chat.send(
        "visible reply",
        conversation_id="conv.chat",
        webspace_id="desktop",
        channel_id="general",
        owner="core:general_assistant",
        actor_id="agent:core:general",
        actor_label="Ada",
        bus=bus,
    )

    assert result["materialized"] is True
    assert seen and seen[0].payload["text"] == "visible reply"
    assert seen[0].payload["_meta"]["route_id"] == "dialog"
    history = chat.history("conv.chat", limit=5)
    assert history["messages"][0]["text"] == "visible reply"
    assert history["messages"][0]["active_agent_id"] == "agent:core:general"


def test_chat_start_thread_and_history_filter_messages() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.threaded",
        webspace_id="desktop",
        owner="skill:test",
    )
    thread = chat.start_thread("conv.threaded", thread_id="thread.one", title="One")
    assert thread is not None
    assert thread["thread_id"] == "thread.one"

    chat.send(
        "thread message",
        conversation_id="conv.threaded",
        thread_id="thread.one",
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:test",
        bus=None,
    )
    chat.send(
        "main message",
        conversation_id="conv.threaded",
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:test",
        bus=None,
    )

    thread_history = chat.history("conv.threaded", thread_id="thread.one")
    full_history = chat.history("conv.threaded")
    assert [item["text"] for item in thread_history["messages"]] == ["thread message"]
    assert [item["text"] for item in full_history["messages"]] == ["thread message", "main message"]


def test_response_envelope_materializes_chat_and_speech() -> None:
    bus = LocalEventBus()
    chat_events: list[Event] = []
    say_events: list[Event] = []
    bus.subscribe("io.out.chat.append", lambda ev: chat_events.append(ev))
    bus.subscribe("io.out.say", lambda ev: say_events.append(ev))

    result = conversation_response.materialize_tool_result(
        {
            "ok": True,
            "response_envelope": {
                "conversation_id": "conv.envelope",
                "content": [{"type": "text", "text": "Envelope reply"}],
                "render_targets": ["text_tail", "speech_text"],
                "speech_text": "Speak this",
            },
        },
        webspace_id="desktop",
        conversation_id="conv.envelope",
        channel_id="conversational",
        owner="skill:test",
        bus=bus,
        route_id="voice_chat",
        actor_id="agent:test:nika",
        actor_label="Nika",
        raw_meta={"request_id": "req.1"},
        payload_meta={"turn_trace_id": "trace.1"},
    )

    assert result["materialized"] is True
    assert chat_events[0].payload["text"] == "Envelope reply"
    assert chat_events[0].payload["_meta"]["turn_trace_id"] == "trace.1"
    assert say_events[0].payload["text"] == "Speak this"
    projection = conversation_store.list_projection("conv.envelope")
    assert projection["messages"][0]["text"] == "Envelope reply"


def test_response_planner_infers_structured_targets() -> None:
    envelope = conversation_response.normalize_response_envelope(
        {
            "content": [
                {"type": "text", "text": "Review the draft"},
                {"type": "card", "data": {"title": "Draft"}},
                {"type": "pending_action", "data": {"id": "pa.1"}},
            ],
            "speech_text": "Review the draft",
            "pending_action": {"id": "pa.1"},
        },
        conversation_id="conv.plan",
        meta={"response_policy": "structure_inferred"},
    )

    assert envelope["render_targets"] == ("text_tail", "speech_text", "card", "pending_action")
    assert envelope["response_plan"]["schema"] == "adaos.conversation.response_plan.v1"
    assert "structured_card_content" in envelope["response_plan"]["reason"]
    assert "pending_action_content" in envelope["response_plan"]["reason"]


def test_response_planner_adds_speech_for_voice_policy() -> None:
    bus = LocalEventBus()
    chat_events: list[Event] = []
    say_events: list[Event] = []
    bus.subscribe("io.out.chat.append", lambda ev: chat_events.append(ev))
    bus.subscribe("io.out.say", lambda ev: say_events.append(ev))

    result = conversation_response.materialize_response(
        {"content": [{"type": "text", "text": "Need one detail"}]},
        webspace_id="desktop",
        conversation_id="conv.voice.plan",
        channel_id="conversational",
        owner="skill:test",
        bus=bus,
        route_id="voice_chat",
        meta={"response_policy": "ask"},
    )

    assert result["render_targets"] == ("text_tail", "speech_text")
    assert result["envelope"]["response_plan"]["reason"] == "text_content+voice_policy:ask"
    assert chat_events and chat_events[0].payload["text"] == "Need one detail"
    assert say_events and say_events[0].payload["text"] == "Need one detail"
