from __future__ import annotations

import json

from adaos.e2e.builder import validate_builder_e2e_record
from adaos.e2e.builder_grading import grade_builder_prototype


def _model_result(*, evidence: str = "/ui") -> str:
    return json.dumps(
        {
            "primary_jobs": [
                {
                    "index": 0,
                    "verdict": "supported",
                    "evidence": [evidence],
                    "reason": "The control and action are present.",
                }
            ],
            "representative_states": [
                {
                    "index": 0,
                    "verdict": "unsupported",
                    "evidence": [],
                    "reason": "No empty state is represented.",
                }
            ],
            "prohibited_assumptions": [
                {
                    "index": 0,
                    "verdict": "absent",
                    "evidence": [],
                    "reason": "The unsafe behavior is not present.",
                }
            ],
            "summary": "The main action exists, but the state coverage is incomplete.",
        }
    )


def test_prototype_grader_normalizes_evidence_and_separates_usage() -> None:
    recorded: list[dict] = []

    def submit(messages, **kwargs):
        assert recorded
        assert kwargs["text"]["format"]["type"] == "json_schema"
        assert kwargs["model"] == "gpt-4.1"
        return {"job_id": "job-1", "_client": {"base_url": "https://root"}}

    def wait(job_id, **kwargs):
        assert job_id == "job-1"
        assert kwargs["base_url"] == "https://root"
        return {
            "status": "succeeded",
            "id": "response-1",
            "output_text": _model_result(),
            "response": {
                "usage": {
                    "input_tokens": 900,
                    "input_tokens_details": {"cached_tokens": 400},
                    "output_tokens": 120,
                }
            },
        }

    grade, request = grade_builder_prototype(
        artifact={"ui": {"control": {"action": "save"}}},
        user_turns=["Build a useful editor."],
        requirements={
            "primary_jobs": ["save an item"],
            "representative_states": ["empty"],
        },
        prohibited_assumptions=["save can discard input"],
        locale="en",
        submitter=submit,
        waiter=wait,
        request_recorder=lambda value: recorded.append(dict(value)),
    )

    assert request == recorded[0]
    assert grade["passed"] is False
    assert grade["score"] == 0.75
    assert grade["gate"] == {
        "passed": False,
        "policy": "all_required_outcomes",
        "threshold_passed": False,
        "failed_dimensions": ["representative_state"],
    }
    assert grade["dimensions"]["primary_jobs"]["checks"][0]["evidence"] == [
        "/ui"
    ]
    assert grade["grader_metrics"] == {
        "input_fresh_tokens": 500,
        "input_cached_tokens": 400,
        "generated_tokens": 120,
        "reasoning_tokens": 0,
        "calls": 1,
        "duration_ms": grade["grader_metrics"]["duration_ms"],
    }
    validate_builder_e2e_record("adaos.builder.prototype_grade.v1", grade)


def test_prototype_grader_exposes_hard_gate_failure_above_score_threshold() -> None:
    def submit(_messages, **_kwargs):
        return {"job_id": "job-gate"}

    def wait(_job_id, **_kwargs):
        result = json.loads(_model_result())
        result["representative_states"][0]["verdict"] = "partial"
        result["representative_states"][0]["evidence"] = ["/ui"]
        return {"status": "succeeded", "output_text": json.dumps(result)}

    grade, _request = grade_builder_prototype(
        artifact={"ui": {"control": {"action": "save"}}},
        user_turns=["Build a useful editor."],
        requirements={
            "primary_jobs": ["save an item"],
            "representative_states": ["empty"],
        },
        prohibited_assumptions=[],
        locale="en",
        threshold=0.7,
        submitter=submit,
        waiter=wait,
    )

    assert grade["score"] == 0.875
    assert grade["gate"]["threshold_passed"] is True
    assert grade["gate"]["passed"] is False
    assert grade["gate"]["failed_dimensions"] == ["representative_state"]
    assert grade["passed"] is False
    validate_builder_e2e_record("adaos.builder.prototype_grade.v1", grade)


def test_prototype_grader_rejects_invented_evidence_pointer() -> None:
    def submit(_messages, **_kwargs):
        return {"job_id": "job-2"}

    def wait(_job_id, **_kwargs):
        return {
            "status": "succeeded",
            "output_text": _model_result(evidence="/missing"),
        }

    grade, _request = grade_builder_prototype(
        artifact={"ui": {}},
        user_turns=["Build a useful editor."],
        requirements={
            "primary_jobs": ["save an item"],
            "representative_states": ["empty"],
        },
        prohibited_assumptions=["save can discard input"],
        locale="en",
        submitter=submit,
        waiter=wait,
    )

    check = grade["dimensions"]["primary_jobs"]["checks"][0]
    assert check["verdict"] == "unclear"
    assert check["evidence"] == []
    assert check["invalid_evidence"] == ["/missing"]


def test_prototype_grader_uses_provider_usage_before_tool_usage() -> None:
    def submit(_messages, **_kwargs):
        return {"job_id": "job-3"}

    def wait(_job_id, **_kwargs):
        return {
            "status": "succeeded",
            "output_text": _model_result(),
            "response": {
                "tool_usage": {
                    "image_gen": {"input_tokens": 0, "output_tokens": 0}
                },
                "usage": {
                    "input_tokens": 1605,
                    "input_tokens_details": {"cached_tokens": 1000},
                    "output_tokens": 528,
                },
            },
            "_protocol": {
                "usage": {
                    "input_tokens": 1605,
                    "cached_input_tokens": 1000,
                    "output_tokens": 528,
                }
            },
        }

    grade, _request = grade_builder_prototype(
        artifact={"ui": {"control": {"action": "save"}}},
        user_turns=["Build a useful editor."],
        requirements={
            "primary_jobs": ["save an item"],
            "representative_states": ["empty"],
        },
        prohibited_assumptions=["save can discard input"],
        locale="en",
        submitter=submit,
        waiter=wait,
    )

    assert grade["grader_metrics"]["input_fresh_tokens"] == 605
    assert grade["grader_metrics"]["input_cached_tokens"] == 1000
    assert grade["grader_metrics"]["generated_tokens"] == 528
