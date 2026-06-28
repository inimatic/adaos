from __future__ import annotations

from adaos.services.agent_context import get_ctx
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
    assert first["retention_class"] == "normal"
    assert first["redaction_state"] == "active"
    assert second and second["seq"] == 2
    projection = conversation_store.list_projection("conv.test", limit=1)
    assert projection["messages"][0]["id"] == "msg.2"
    assert projection["messages"][0]["retention_class"] == "normal"
    assert projection["messages"][0]["redaction_state"] == "active"
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
    assert items[0]["retention_class"] == "normal"
    assert items[0]["redaction_state"] == "active"


def test_conversation_store_records_retention_and_redaction_metadata() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.retention",
        webspace_id="desktop",
        owner="core:test",
    )

    stored = conversation_store.append_message(
        conversation_id="conv.retention",
        webspace_id="desktop",
        channel_id="general",
        owner="core:test",
        role="user",
        text="temporary detail",
        payload={"id": "retention.msg.1", "from": "user", "text": "temporary detail"},
        retention_class="ephemeral",
        retention_until=1234.5,
        redaction_state="redacted",
        redacted_at=1235.0,
        redaction_reason="user_request",
    )

    assert stored is not None
    assert stored["retention_class"] == "ephemeral"
    assert stored["retention_until"] == 1234.5
    assert stored["redaction_state"] == "redacted"
    assert stored["redacted_at"] == 1235.0
    assert stored["redaction_reason"] == "user_request"
    projected = conversation_store.list_projection("conv.retention")["messages"][0]
    assert projected["retention_class"] == "ephemeral"
    assert projected["redaction_state"] == "redacted"

    memory_id = conversation_store.remember(
        scope="global_user",
        owner="core",
        key="preference",
        text="prefers short answers",
        retention_class="profile",
        retention_until=9999.0,
        redaction_state="active",
    )
    assert memory_id
    memory = conversation_store.list_memory(scope="global_user", owner="core")[0]
    assert memory["retention_class"] == "profile"
    assert memory["retention_until"] == 9999.0
    assert memory["redaction_state"] == "active"


def test_conversation_store_migrates_retention_and_redaction_columns() -> None:
    sql = get_ctx().sql
    conversation_store._ENSURED_SQL_IDS.discard(id(sql))
    with sql.connect() as con:
        con.execute(
            """
            CREATE TABLE conversation_conversations (
                conversation_id TEXT PRIMARY KEY,
                webspace_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'conversation',
                owner TEXT NOT NULL,
                title TEXT,
                active_agent_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                initiator_json TEXT NOT NULL DEFAULT '{}',
                policy_json TEXT NOT NULL DEFAULT '{}',
                meta_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        con.execute(
            """
            CREATE TABLE conversation_messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                webspace_id TEXT NOT NULL,
                channel_id TEXT,
                owner TEXT,
                actor_id TEXT,
                actor_label TEXT,
                actor_icon TEXT,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                route_id TEXT,
                ts REAL NOT NULL,
                request_id TEXT,
                turn_trace_id TEXT,
                idempotency_key TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                UNIQUE (conversation_id, seq),
                UNIQUE (conversation_id, idempotency_key)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE conversation_memory_items (
                memory_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                owner TEXT NOT NULL,
                subject_id TEXT,
                key TEXT,
                text TEXT,
                value_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL,
                consent_state TEXT NOT NULL DEFAULT 'unknown',
                policy_json TEXT NOT NULL DEFAULT '{}',
                source_ref_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        con.commit()

    assert conversation_store.ensure_schema()
    with sql.connect() as con:
        for table in ("conversation_conversations", "conversation_messages", "conversation_memory_items"):
            columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            assert {
                "retention_class",
                "retention_until",
                "redaction_state",
                "redacted_at",
                "redaction_reason",
            }.issubset(columns)


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
