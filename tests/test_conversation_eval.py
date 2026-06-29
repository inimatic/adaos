from __future__ import annotations

import json
from pathlib import Path

from adaos.services import conversation_eval, conversation_store


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "conversation"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _seed_eval_conversation() -> str:
    conversation_id = "conv.eval"
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id="desktop",
        owner="skill:conversation_companions",
        kind="conversation",
    )
    conversation_store.append_message(
        conversation_id=conversation_id,
        webspace_id="desktop",
        channel_id="conversational",
        owner="skill:conversation_companions",
        role="user",
        text="Поговорим",
        payload={"id": "eval.msg.1", "from": "user", "text": "Поговорим"},
        actor_id="user:default",
    )
    conversation_store.append_message(
        conversation_id=conversation_id,
        webspace_id="desktop",
        channel_id="conversational",
        owner="skill:conversation_companions",
        role="hub",
        text="Я Арсений. Готов к разговору.",
        payload={"id": "eval.msg.2", "from": "hub", "text": "Я Арсений. Готов к разговору."},
        actor_id="agent:conversation_companions:arseni",
        actor_label="Арсений",
    )
    trace_id = conversation_store.start_turn_trace(
        webspace_id="desktop",
        conversation_id=conversation_id,
        channel_id="conversational",
        agent_id="agent:conversation_companions:arseni",
        selected_tool="conversation_companions.talk",
        policy_decision={"reason": "dialog_followup", "repair_state": "none"},
        renderer={"receiver": "dialog.visible_tail"},
        message_id="eval.msg.2",
    )
    assert trace_id
    assert conversation_store.finish_turn_trace(trace_id, status="completed", summary="Rendered")
    return conversation_id


def test_conversation_eval_scores_golden_conversation() -> None:
    conversation_id = _seed_eval_conversation()

    result = conversation_eval.evaluate_golden_conversation(
        conversation_id=conversation_id,
        expectations={
            "required_text": ["Я Арсений"],
            "forbidden_text": ["low_confidence"],
            "required_agents": ["agent:conversation_companions:arseni"],
            "required_channels": ["conversational"],
            "min_success_rate": 1.0,
            "max_fallback_rate": 0.0,
            "max_latency_ms_p95": 10_000,
        },
    )

    assert result["schema"] == "adaos.conversation.eval.result.v1"
    assert result["status"] == "passed"
    assert result["metrics"]["message_count"] == 2
    assert result["metrics"]["turn_count"] == 1
    assert result["metrics"]["success_rate"] == 1.0
    assert result["metrics"]["fallback_rate"] == 0.0
    assert result["failures"] == []
    assert any(item["type"] == "turn_trace" for item in result["evidence_refs"])


def test_conversation_eval_reports_failures() -> None:
    conversation_id = _seed_eval_conversation()

    result = conversation_eval.evaluate_golden_conversation(
        conversation_id=conversation_id,
        expectations={
            "required_text": ["Ника"],
            "forbidden_text": ["Арсений"],
            "required_agents": ["agent:conversation_companions:nika"],
        },
    )

    assert result["status"] == "failed"
    failed_names = [item["name"] for item in result["failures"]]
    assert failed_names == ["required_text", "forbidden_text", "required_agent"]


def test_conversation_eval_counts_fallback_repair_and_no_match_traces() -> None:
    conversation_id = _seed_eval_conversation()
    trace_id = conversation_store.start_turn_trace(
        webspace_id="desktop",
        conversation_id=conversation_id,
        channel_id="general",
        agent_id="agent:core:general",
        selected_tool="voice_chat_skill.handle_text",
        policy_decision={"reason": "nlu_fallback", "repair_state": "correction"},
        renderer={"receiver": "dialog.visible_tail"},
        summary="low_confidence not_obtained fallback",
    )
    assert trace_id
    assert conversation_store.finish_turn_trace(trace_id, status="completed")

    metrics = conversation_eval.collect_metrics(conversation_id=conversation_id)

    assert metrics["trace_count"] == 2
    assert metrics["fallback_count"] == 1
    assert metrics["no_match_count"] == 1
    assert metrics["repair_count"] == 1
    assert metrics["latency_ms"]["count"] == 2


def test_conversation_eval_replays_initial_golden_datasets() -> None:
    fixture_names = [
        "general_no_match_repair.json",
        "conversation_companions_agent_handoff.json",
        "builder_review_handoff.json",
        "teacher_candidate_repair.json",
    ]
    for name in fixture_names:
        fixture = _load_fixture(name)
        result = conversation_eval.evaluate_golden_conversation(
            conversation_id=fixture["conversation_id"],
            messages=fixture["messages"],
            traces=fixture["turn_traces"],
            expectations=fixture["expectations"],
        )
        assert result["status"] == "passed", {
            "fixture": name,
            "failures": result["failures"],
            "metrics": result["metrics"],
        }
