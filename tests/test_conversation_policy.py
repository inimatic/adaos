from __future__ import annotations

from uuid import uuid4

from adaos.services import conversation_policy, conversation_store


def test_conversation_policy_inspects_turn_trace() -> None:
    suffix = uuid4().hex[:8]
    conversation_id = f"conv.policy.{suffix}"
    webspace_id = f"policy-ws-{suffix}"
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id=webspace_id,
        owner="skill:conversation_companions",
    )
    trace_id = conversation_store.start_turn_trace(
        webspace_id=webspace_id,
        conversation_id=conversation_id,
        channel_id="conversational",
        agent_id="agent:conversation_companions:nika",
        selected_tool="conversation_companions.talk",
        policy_decision={
            "reason": "addressed_agent",
            "selected_channel": "conversational",
            "selected_agent_id": "agent:conversation_companions:nika",
            "materialization_status": "materialized",
            "repair_state": "none",
        },
        renderer={"receiver": "dialog.visible_tail", "projection": "voice_chat.messages"},
        message_id=f"policy.msg.{suffix}",
    )
    assert trace_id
    assert conversation_store.finish_turn_trace(trace_id, status="completed", summary="Rendered")

    result = conversation_policy.inspect_turn_policy(turn_trace_id=trace_id)

    assert result["schema"] == conversation_policy.POLICY_INSPECTION_SCHEMA
    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["selected"]["channel_id"] == "conversational"
    assert result["selected"]["agent_id"] == "agent:conversation_companions:nika"
    assert result["selected"]["tool"] == "conversation_companions.talk"
    assert result["selected"]["renderer"] == {"receiver": "dialog.visible_tail", "projection": "voice_chat.messages"}
    assert result["explanation"]["reasons"] == ["addressed_agent"]
    assert result["explanation"]["materialization_status"] == "materialized"
    assert result["source_refs"] == [
        {"type": "turn_trace", "turn_trace_id": trace_id, "conversation_id": conversation_id}
    ]

    latest = conversation_policy.inspect_last_turn_policy(webspace_id=webspace_id, conversation_id=conversation_id)
    assert latest["turn_trace_id"] == trace_id


def test_conversation_policy_reports_missing_trace() -> None:
    result = conversation_policy.inspect_turn_policy(turn_trace_id="trace.missing")

    assert result == {
        "schema": conversation_policy.POLICY_INSPECTION_SCHEMA,
        "ok": False,
        "status": "not_found",
        "turn_trace_id": "trace.missing",
        "webspace_id": None,
        "conversation_id": None,
    }
