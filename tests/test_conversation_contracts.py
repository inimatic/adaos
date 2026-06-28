from __future__ import annotations

import pytest

from adaos.domain.conversation import (
    ActorRef,
    ContentPart,
    Conversation,
    ConversationContractError,
    ConversationMessage,
    DialogAct,
    DialogChannel,
    DialogFrame,
    DialogPolicyState,
    DialogTurn,
    HistoryPolicy,
    Initiator,
    MemoryItem,
    ResponseEnvelope,
    RetentionPolicy,
    SourceRef,
    TurnTrace,
    actor_kind,
    conversation_contract_snapshot,
    validate_actor_id,
)


def test_conversation_contract_snapshot_covers_phase0_records() -> None:
    snapshot = conversation_contract_snapshot()

    assert snapshot["schema_version"] == "adaos.conversation.contract.v1"
    assert "Conversation" in snapshot["records"]
    assert "ConversationMessage" in snapshot["records"]
    assert "DialogTurn" in snapshot["records"]
    assert "ResponseEnvelope" in snapshot["records"]
    assert snapshot["default_policies"]["history"]["cross_skill_use"] == "deny_by_default"
    assert snapshot["projection_rules"]["canonical_store"] == "node_conversation_store"


def test_actor_id_contract_accepts_canonical_actors_and_rejects_ambiguous_ids() -> None:
    assert actor_kind("core:general_assistant") == "core"
    assert actor_kind("skill:conversation_companions") == "skill"
    assert actor_kind("agent:conversation_companions:nika") == "agent"
    assert actor_kind("user:local") == "user"
    assert actor_kind("node:homepoint") == "node"
    assert actor_kind("endpoint:living_room") == "endpoint"
    assert actor_kind("transport:telegram:chat-123") == "transport"

    with pytest.raises(ConversationContractError):
        validate_actor_id("conversation_companions")


def test_conversation_and_message_contract_round_trip_to_canonical_json() -> None:
    initiator = Initiator(
        actor_id="user:local",
        source="web",
        reason="opened_builder_channel",
    )
    conversation = Conversation(
        id="conv.builder.default",
        node_id="node.local",
        kind="builder",
        owner="skill:builder",
        logical_owner="skill:builder",
        surface="builder",
        webspace_id="desktop",
        title="Builder",
        created_by=initiator,
        participants=(ActorRef("user:local"), ActorRef("skill:builder")),
    )
    message = ConversationMessage(
        id="msg.1",
        node_id="node.local",
        conversation_id=conversation.id,
        seq=1,
        role="assistant",
        from_actor=ActorRef("skill:builder"),
        content=(ContentPart(type="text", text="What should we change?"),),
        initiator=Initiator(actor_id="skill:builder", reason="draft_ready"),
        meta={"dialog_channel_id": "builder"},
    )

    conv_json = conversation.to_dict()
    msg_json = message.to_dict()

    assert conv_json["owner"] == "skill:builder"
    assert conv_json["created_by"]["actor_id"] == "user:local"
    assert conv_json["history_policy"]["cross_skill_use"] == "deny_by_default"
    assert msg_json["from"]["actor_id"] == "skill:builder"
    assert msg_json["content"][0]["text"] == "What should we change?"
    assert message.text == "What should we change?"


def test_dialog_turn_frame_response_and_trace_contracts() -> None:
    policy_state = DialogPolicyState(
        conversation_id="conv.skill.conversation_companions.default",
        owner="skill:conversation_companions",
        surface="skill:conversation_companions",
        channel_id="conversational",
        active_agent_id="agent:conversation_companions:nika",
    )
    frame = DialogFrame(
        id="frame.recipe",
        kind="slot_collection",
        required_slots=("dish",),
        slots={"dish": "borscht"},
    )
    turn = DialogTurn(
        id="turn.1",
        conversation_id=policy_state.conversation_id,
        turn_trace_id="trace.1",
        policy_state=policy_state,
        active_frame_id=frame.id,
    )
    response = ResponseEnvelope(
        conversation_id=policy_state.conversation_id,
        request_id="req.1",
        content=(ContentPart(type="text", text="Ника: начнем с рисков."),),
        dialog_acts=(DialogAct(type="reply", actor_id="agent:conversation_companions:nika"),),
        render_targets=("text_tail", "speech_text"),
    )
    trace = TurnTrace(
        turn_trace_id=turn.turn_trace_id,
        conversation_id=turn.conversation_id,
        webspace_id="desktop",
        channel_id="conversational",
        agent_id="agent:conversation_companions:nika",
        selected_tool="conversation_companions.talk",
        policy_decision={"routing_reason": "active_channel"},
    )

    assert turn.to_dict()["policy_state"]["active_agent_id"] == "agent:conversation_companions:nika"
    assert response.to_dict()["dialog_acts"][0]["actor_id"] == "agent:conversation_companions:nika"
    assert trace.to_dict()["policy_decision"]["routing_reason"] == "active_channel"


def test_memory_retention_and_projection_safety_defaults() -> None:
    memory = MemoryItem(
        id="mem.1",
        node_id="node.local",
        owner="skill:conversation_companions",
        scope="agent_user",
        agent_id="agent:conversation_companions:nika",
        user_id="user:local",
        conversation_id="conv.skill.conversation_companions.default",
        kind="preference",
        text="User prefers short replies from Nika.",
        source_refs=(SourceRef(message_id="msg.1", seq=1),),
        confidence=0.82,
        consent="skill_scoped",
        visibility="owner_only",
    )

    assert memory.to_dict()["visibility"] == "owner_only"
    assert RetentionPolicy().to_dict()["redaction"] == "policy_controlled"
    with pytest.raises(ConversationContractError):
        HistoryPolicy(cross_skill_use="allow")
    with pytest.raises(ConversationContractError):
        MemoryItem(
            id="mem.bad",
            node_id="node.local",
            owner="skill:conversation_companions",
            scope="agent_user",
            text="bad confidence",
            confidence=1.5,
            consent="skill_scoped",
            visibility="owner_only",
        )


def test_dialog_channel_requires_owner_conversation_and_surface() -> None:
    channel = DialogChannel(
        webspace_id="desktop",
        channel_id="conversational",
        conversation_id="conv.skill.conversation_companions.default",
        owner="skill:conversation_companions",
        surface="skill:conversation_companions",
        active_agent_id="agent:conversation_companions:arseni",
    )

    assert channel.to_dict()["active_agent_id"] == "agent:conversation_companions:arseni"
