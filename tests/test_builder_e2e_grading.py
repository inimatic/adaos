from __future__ import annotations

import json

from adaos.e2e.builder import validate_builder_e2e_record
from adaos.e2e.builder_grading import _evidence_pointers, grade_builder_prototype


def _model_result(*, evidence: str = "/ui") -> str:
    return json.dumps(
        {
            "primary_jobs": [
                {
                    "index": 0,
                    "verdict": "supported",
                    "evidence": [{"pointer": evidence}],
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
                    "verdict": "not_violated",
                    "evidence": [],
                    "reason": "The unsafe behavior is not present.",
                }
            ],
        }
    )


def test_evidence_index_includes_deep_semantic_state_containers() -> None:
    artifact = {
        "webui": {
            "ui": {
                "application": {
                    "desktop": {
                        "pageSchema": {
                            "widgets": [
                                {
                                    "id": "items",
                                    "type": "ui.list",
                                    "inputs": {
                                        "emptyState": {"message": "No items"}
                                    },
                                }
                            ]
                        }
                    }
                }
            }
        }
    }

    assert (
        "/webui/ui/application/desktop/pageSchema/widgets/0/inputs/emptyState"
        in _evidence_pointers(artifact)
    )


def test_evidence_index_includes_builder_capability_gaps() -> None:
    artifact = {
        "webui": {
            "ui": {
                "application": {
                    "desktop": {
                        "pageSchema": {
                            "meta": {
                                "builder": {
                                    "capability_gaps": [
                                        {
                                            "requirement_ref": "job:01",
                                            "code": "runtime_unavailable",
                                            "detail": "Not implemented.",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    assert (
        "/webui/ui/application/desktop/pageSchema/meta/builder/capability_gaps"
        in _evidence_pointers(artifact)
    )


def test_prototype_grader_normalizes_evidence_and_separates_usage() -> None:
    recorded: list[dict] = []

    def submit(messages, **kwargs):
        assert recorded
        assert "Do not broaden one mutation into all mutations" in messages[0]["content"]
        assert kwargs["text"]["format"]["type"] == "json_schema"
        pointer_schema = kwargs["text"]["format"]["schema"]["$defs"]["evidence"]
        assert pointer_schema["properties"]["pointer"]["pattern"].startswith("^/")
        assert "enum" not in pointer_schema["properties"]["pointer"]
        assert kwargs["model"] == "gpt-4.1"
        assumption_schema = kwargs["text"]["format"]["schema"]["properties"][
            "prohibited_assumptions"
        ]
        assert assumption_schema["items"]["properties"]["verdict"]["enum"] == [
            "not_violated",
            "violated",
            "unclear",
        ]
        assert "Mere absence is not uncertainty" in messages[0]["content"]
        assert kwargs["prompt_cache_key"] == "adaos-builder-e2e-prototype-grader-v8"
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
    assert grade["summary"] == (
        "Primary jobs supported: 1/1; representative states supported: 0/1; "
        "prohibited assumptions absent: 1/1."
    )
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
        result["representative_states"][0]["evidence"] = [{"pointer": "/ui"}]
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


def test_prototype_grader_accepts_revision_bound_record_evidence() -> None:
    artifact = {
        "schema": "adaos.builder.prototype_evaluation_artifact.v1",
        "project_ref": "project:demo",
        "revision": "002",
        "webui": {"ui": {}},
        "prototype_resources": [
            {
                "resource_type": "prototype.project.demo.items",
                "records": [{"id": "item-1", "status": "blocked"}],
            }
        ],
    }

    def submit(_messages, **_kwargs):
        return {"job_id": "job-record-evidence"}

    def wait(_job_id, **_kwargs):
        result = json.loads(_model_result())
        result["primary_jobs"][0]["evidence"] = [
            {"pointer": "/prototype_resources/0/records/0"}
        ]
        return {"status": "succeeded", "output_text": json.dumps(result)}

    grade, request = grade_builder_prototype(
        artifact=artifact,
        user_turns=["Show blocked items."],
        requirements={"primary_jobs": ["show blocked items"]},
        prohibited_assumptions=[],
        locale="en",
        submitter=submit,
        waiter=wait,
    )

    assert grade["dimensions"]["primary_jobs"]["checks"][0]["evidence"] == [
        "/prototype_resources/0/records/0"
    ]
    assert '"prototype_resources"' in request["messages"][1]["content"]
