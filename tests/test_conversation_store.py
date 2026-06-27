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
