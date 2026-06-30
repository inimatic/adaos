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
        renderer={
            "receiver": "dialog.visible_tail",
            "context_packet": {
                "schema": "adaos.context.packet.v1",
                "token_estimate": 96,
                "budgets": {"max_tokens": 1200, "max_messages": 12, "max_segments": 2, "max_memory_items": 4},
                "diagnostics": {
                    "budget_exhausted": False,
                    "selected_sources": [{"type": "message", "message_id": "eval.msg.1"}],
                    "skipped_sources": [],
                },
            },
        },
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
            "max_repair_rate": 0.0,
            "max_no_match_rate": 0.0,
            "max_latency_ms_p95": 10_000,
            "min_context_packet_count": 1,
            "max_context_token_estimate_p95": 120,
            "max_context_utilization": 0.1,
            "max_context_budget_exhausted_rate": 0.0,
        },
    )

    assert result["schema"] == "adaos.conversation.eval.result.v1"
    assert result["status"] == "passed"
    assert result["metrics"]["message_count"] == 2
    assert result["metrics"]["turn_count"] == 1
    assert result["metrics"]["success_rate"] == 1.0
    assert result["metrics"]["fallback_rate"] == 0.0
    assert result["metrics"]["context_budget"]["packet_count"] == 1
    assert result["metrics"]["context_budget"]["token_estimate"]["p95"] == 96.0
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


def test_conversation_eval_reports_context_budget_failures() -> None:
    conversation_id = _seed_eval_conversation()

    result = conversation_eval.evaluate_golden_conversation(
        conversation_id=conversation_id,
        expectations={
            "min_context_packet_count": 2,
            "max_context_token_estimate_p95": 10,
            "max_context_utilization": 0.01,
            "max_context_budget_exhausted_rate": 0.0,
        },
    )

    assert result["status"] == "failed"
    failed_names = [item["name"] for item in result["failures"]]
    assert failed_names == [
        "min_context_packet_count",
        "max_context_token_estimate_p95",
        "max_context_utilization",
    ]


def test_conversation_eval_runs_optional_model_grader_callback() -> None:
    fixture = _load_fixture("conversation_companions_agent_handoff.json")
    calls: list[dict] = []

    def _grader(request):
        calls.append(dict(request))
        return {"score": 0.94, "label": "good_answer", "reason": "addresses the request"}

    expectations = dict(fixture["expectations"])
    expectations["model_grades"] = [
        {
            "id": "answer_quality",
            "rubric": "Score whether the answer is useful and directly addresses the user.",
            "min_score": 0.9,
        }
    ]

    result = conversation_eval.evaluate_golden_conversation(
        conversation_id=fixture["conversation_id"],
        messages=fixture["messages"],
        traces=fixture["turn_traces"],
        expectations=expectations,
        model_grader=_grader,
    )

    assert result["status"] == "passed"
    assert result["model_grades"][0]["schema"] == "adaos.conversation.eval.model_grade.v1"
    assert result["model_grades"][0]["id"] == "answer_quality"
    assert result["model_grades"][0]["score"] == 0.94
    assert any(item["name"] == "model_grader:answer_quality" for item in result["checks"])
    assert calls and calls[0]["schema"] == "adaos.conversation.eval.model_grader_request.v1"
    assert calls[0]["messages"][-1]["text"].startswith("Nika:")


def test_conversation_eval_model_grader_failure_blocks_result() -> None:
    fixture = _load_fixture("conversation_companions_agent_handoff.json")

    def _grader(_request):
        return {
            "score": 0.8,
            "label": "unresolved",
            "reason": "the user's request remains unresolved",
            "unresolved_user_request": True,
        }

    result = conversation_eval.evaluate_golden_conversation(
        conversation_id=fixture["conversation_id"],
        messages=fixture["messages"],
        traces=fixture["turn_traces"],
        expectations={
            "model_grades": [
                {
                    "id": "unresolved_user_request",
                    "rubric": "Fail if the last user request remains unresolved.",
                    "min_score": 0.7,
                }
            ]
        },
        model_grader=_grader,
    )

    assert result["status"] == "failed"
    assert result["model_grades"][0]["passed"] is False
    assert result["model_grades"][0]["unresolved_user_request"] is True
    assert [item["name"] for item in result["failures"]] == ["model_grader:unresolved_user_request"]


def test_conversation_eval_replays_initial_golden_datasets() -> None:
    fixture_names = [
        "general_no_match_repair.json",
        "conversation_companions_agent_handoff.json",
        "builder_review_handoff.json",
        "builder_first_idea_preview_correction.json",
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


def test_conversation_eval_golden_migration_gate_passes_initial_suite() -> None:
    result = conversation_eval.run_golden_migration_gate(fixture_dir=FIXTURE_DIR)

    assert result["schema"] == "adaos.conversation.eval.migration_gate.v1"
    assert result["status"] == "passed"
    assert result["fixture_count"] >= 5
    assert result["failed_count"] == 0
    assert result["failures"] == []
    assert {
        "general_no_match_repair",
        "conversation_companions_agent_handoff",
        "builder_review_handoff",
        "builder_first_idea_preview_correction",
        "teacher_candidate_repair",
    }.issubset({item["dataset_id"] for item in result["datasets"]})


def test_conversation_eval_golden_migration_gate_blocks_missing_required_dataset() -> None:
    result = conversation_eval.run_golden_migration_gate(
        fixture_paths=[FIXTURE_DIR / "general_no_match_repair.json"],
        required_dataset_ids=["general_no_match_repair", "builder_review_handoff"],
    )

    assert result["status"] == "failed"
    assert result["failed_count"] == 1
    assert result["failures"] == [
        {
            "dataset_id": "builder_review_handoff",
            "source_path": None,
            "failures": [
                {
                    "name": "required_dataset",
                    "passed": False,
                    "details": {"dataset_id": "builder_review_handoff", "reason": "missing"},
                }
            ],
        }
    ]


def test_conversation_eval_publishes_failed_gate_repair_pending_action(monkeypatch) -> None:
    gate = conversation_eval.run_golden_migration_gate(
        fixture_paths=[FIXTURE_DIR / "general_no_match_repair.json"],
        required_dataset_ids=["general_no_match_repair", "builder_review_handoff"],
    )
    published: list[dict] = []

    import adaos.services.pending_actions as pending_actions

    def _publish_pending_action(**kwargs):
        published.append(dict(kwargs))
        return {
            "id": "pa.eval.repair",
            "kind": kwargs["kind"],
            "domain_ref": kwargs["domain_ref"],
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(pending_actions, "publish_pending_action", _publish_pending_action)

    result = conversation_eval.publish_eval_repair_pending_action(gate, webspace_id="builder-eval")

    assert result["ok"] is True
    assert result["published"] is True
    assert result["pending_action"]["kind"] == "builder.eval_repair.review"
    assert published[0]["response_topic"] == "builder.eval_repair.response"
    assert published[0]["owner_scope"]["owner"] == "skill:builder_skill"
    assert published[0]["domain_ref"]["dataset_ids"] == ["builder_review_handoff"]
    metadata = published[0]["metadata"]
    assert metadata["schema"] == "adaos.builder.eval_repair.pending_action_metadata.v1"
    assert metadata["eval_summary"]["schema"] == "adaos.conversation.eval.repair_summary.v1"
    assert metadata["eval_summary"]["failed_count"] == 1
    assert metadata["eval_summary"]["dataset_refs"][0]["dataset_id"] == "builder_review_handoff"
    assert metadata["approval_policy"]["requires_human_review"] is True
    assert metadata["approval_policy"]["action_risk"]["schema"] == "adaos.conversation.action_risk.v1"


def test_conversation_eval_skips_pending_action_for_passed_gate(monkeypatch) -> None:
    gate = conversation_eval.run_golden_migration_gate(fixture_dir=FIXTURE_DIR)
    published: list[dict] = []

    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(pending_actions, "publish_pending_action", lambda **kwargs: published.append(dict(kwargs)))

    result = conversation_eval.publish_eval_repair_pending_action(gate, webspace_id="builder-eval")

    assert result["ok"] is True
    assert result["published"] is False
    assert result["reason"] == "eval_passed"
    assert published == []
