from __future__ import annotations

from adaos.services import conversation_store


def test_conversation_store_appends_messages_with_monotonic_seq() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.test",
        webspace_id="desktop",
        owner="core:test",
    )

    first = conversation_store.append_message(
        conversation_id="conv.test",
        webspace_id="desktop",
        channel_id="general",
        owner="core:test",
        role="user",
        text="hello",
        payload={"id": "msg.1", "from": "user", "text": "hello"},
    )
    second = conversation_store.append_message(
        conversation_id="conv.test",
        webspace_id="desktop",
        channel_id="general",
        owner="core:test",
        role="hub",
        text="hi",
        payload={"id": "msg.2", "from": "hub", "text": "hi"},
        actor_id="agent:core:general",
        actor_label="Ада",
    )

    assert first and first["seq"] == 1
    assert second and second["seq"] == 2
    projection = conversation_store.list_projection("conv.test", limit=1)
    assert projection["messages"][0]["id"] == "msg.2"
    assert projection["has_more_before"] is True
    older = conversation_store.list_projection("conv.test", before_cursor=projection["before_cursor"], limit=1)
    assert [item["id"] for item in older["messages"]] == ["msg.1", "msg.2"]


def test_conversation_store_keeps_agent_registry_and_memory() -> None:
    conversation_store.ensure_schema()
    conversation_store.seed_agents(
        [
            {
                "id": "agent:test:nika",
                "label": "Ника",
                "owner": "skill:test",
                "channel_id": "conversational",
                "kind": "skill_agent",
                "aliases": ["Ника", "Nika"],
                "gender": "female",
                "voice": "ru-female",
                "icon": "female-outline",
            }
        ],
        source="test",
    )

    agents = conversation_store.list_agents(channel_id="conversational")
    assert agents[0]["id"] == "agent:test:nika"
    assert "Nika" in agents[0]["aliases"]

    memory_id = conversation_store.remember(
        scope="agent_user",
        owner="skill:test",
        subject_id="agent:test:nika",
        key="style",
        text="prefers concise critique",
        consent_state="granted",
        policy={"visibility": "owner"},
    )
    items = conversation_store.list_memory(scope="agent_user", owner="skill:test", subject_id="agent:test:nika")
    assert memory_id
    assert items[0]["text"] == "prefers concise critique"
    assert items[0]["consent_state"] == "granted"


def test_conversation_store_persists_active_dialog_channel() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_dialog_channel(
        webspace_id="desktop",
        channel_id="conversational",
        label="Conversational",
        owner="skill:conversation_companions",
        conversation_id="conv.skill.conversation_companions.default.desktop",
        active_agent_id="agent:conversation_companions:arseni",
        default_skill="conversation_companions",
        default_tool="talk",
        route_id="voice_chat",
    )

    assert conversation_store.set_active_dialog_channel(
        webspace_id="desktop",
        channel_id="conversational",
        conversation_id="conv.skill.conversation_companions.default.desktop",
        active_agent_id="agent:conversation_companions:arseni",
        meta={"event": "agent_addressed"},
    )

    active = conversation_store.get_active_dialog_channel("desktop")
    assert active is not None
    assert active["channel_id"] == "conversational"
    assert active["conversation_id"] == "conv.skill.conversation_companions.default.desktop"
    assert active["active_agent_id"] == "agent:conversation_companions:arseni"
    assert active["meta"]["event"] == "agent_addressed"


def test_conversation_store_returns_latest_dialog_channel_from_messages() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.latest",
        webspace_id="desktop",
        owner="skill:test",
    )
    conversation_store.append_message(
        conversation_id="conv.latest",
        webspace_id="desktop",
        channel_id="conversational",
        owner="skill:test",
        role="hub",
        text="reply",
        payload={"id": "latest.1", "from": "hub", "text": "reply"},
        actor_id="agent:test:one",
        actor_label="One",
        actor_icon="person-circle-outline",
        route_id="voice_chat",
        ts=10.0,
    )

    latest = conversation_store.latest_dialog_channel_for_webspace("desktop")
    assert latest is not None
    assert latest["channel_id"] == "conversational"
    assert latest["conversation_id"] == "conv.latest"
    assert latest["active_agent_id"] == "agent:test:one"
    assert latest["active_agent_label"] == "One"
