from __future__ import annotations

import json
import copy

import pytest

from adaos.e2e.builder import validate_builder_e2e_record
from adaos.e2e.builder_grading import _evidence_pointers, grade_builder_prototype


def test_grader_evidence_schema_cannot_invent_or_shift_paths() -> None:
    from jsonschema import Draft202012Validator
    from adaos.e2e.builder_grading import _model_result_schema

    pointers = [f"/webui/ui/application/desktop/pageSchema/widgets/{index}" for index in range(510)]
    schema = _model_result_schema(pointers)
    validator = Draft202012Validator(schema["$defs"]["evidence"]["properties"]["pointer"])
    assert all(validator.is_valid(pointer) for pointer in pointers)
    assert not validator.is_valid("/webui/ui/application/pageSchema/widgets/5")
    assert not validator.is_valid(pointers[-1] + "/made-up")
    assert len(validator.schema["anyOf"]) == 3


def test_grader_rejects_oversized_evidence_before_inference() -> None:
    import pytest
    from adaos.e2e.builder_grading import _model_result_schema

    with pytest.raises(ValueError, match="bounded grader"):
        _model_result_schema([f"/{index}" for index in range(951)])


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
        assert "Acceptance stage is PROTOTYPE" in messages[0]["content"]
        assert "an obligation cannot excuse their absence" in messages[0]["content"]
        assert "Do not broaden one mutation into all mutations" in messages[0]["content"]
        assert "not an exhaustive imagined product" in messages[0]["content"]
        assert "cannot compensate for broken controls or block a working path" in messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        assert payload["evidence_pointers"] == ["/ui", "/ui/control"]
        assert payload["rubric"]["primary_jobs"] == [
            {
                "statement": "save an item",
                "acceptance": "A save action consumes the editor values.",
                "exclusions": ["Delete behavior is graded separately."],
            }
        ]
        assert kwargs["text"]["format"]["type"] == "json_schema"
        pointer_schema = kwargs["text"]["format"]["schema"]["$defs"]["evidence"]
        assert pointer_schema["properties"]["pointer"]["enum"] == payload["evidence_pointers"]
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
        assert kwargs["prompt_cache_key"] == "adaos-builder-e2e-prototype-grader-v14"
        assert kwargs["max_tokens"] == 32768
        assert kwargs["max_tokens"] == recorded[0]["generation_options"]["max_tokens"]
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
            "primary_jobs": [
                {
                    "statement": "save an item",
                    "acceptance": "A save action consumes the editor values.",
                    "exclusions": ["Delete behavior is graded separately."],
                }
            ],
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


def test_evidence_index_exposes_provider_policies_without_schema_property_noise() -> None:
    prefix = "/webui/ui/application/desktop/pageSchema"
    page_schema = {
        "meta": {"builder": {"prototype_resource_policies": {"prototype.items": {
            "read_only_when": {"field_ref": "status", "operator": "equals", "value": "Archived"},
        }}}},
        "widgets": [{"id": "editor", "type": "form", "inputs": {
            "readOnlyIf": {"eq": ["status", "Archived"]},
            "schema": {"type": "object", "properties": {"status": {"type": "string"}}},
        }}],
    }
    artifact = {"webui": {"ui": {"application": {"desktop": {"pageSchema": page_schema}}}}}
    pointers = _evidence_pointers(artifact)
    assert prefix + "/meta/builder/prototype_resource_policies/prototype.items/read_only_when" in pointers
    assert prefix + "/widgets/0/inputs/readOnlyIf" in pointers
    assert prefix + "/widgets/0" in pointers
    assert prefix + "/widgets/0/inputs/schema/properties/status" not in pointers


@pytest.mark.parametrize("response", [
    {"status": "failed", "error": {"code": "insufficient_quota", "message": "credit_balance_exhausted"}},
    {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
     "output_text": '{"primary_jobs": [', "usage": {"output_tokens": 1800}},
])
def test_failed_grader_retains_job_partial_output_and_usage(response: dict) -> None:
    recorded = []
    with pytest.raises(ValueError, match="job_id=job-failed"):
        grade_builder_prototype(
            artifact={"ui": {}}, user_turns=[], requirements={}, prohibited_assumptions=[], locale="en",
            submitter=lambda *a, **k: {"job_id": "job-failed"}, waiter=lambda *a, **k: response,
            response_recorder=lambda value: recorded.append(copy.deepcopy(value)),
        )
    assert recorded[0]["submission"] == {"job_id": "job-failed"}
    assert "response" not in recorded[0]
    assert recorded[-1]["response"] == response
    assert recorded[-1]["metrics"]["generated_tokens"] == response.get("usage", {}).get("output_tokens", 0)


def test_grader_output_budget_changes_request_identity() -> None:
    records = []
    for budget in (1800, 32768):
        grade_builder_prototype(
            artifact={"ui": {}}, user_turns=[], requirements={}, prohibited_assumptions=[], locale="en",
            max_output_tokens=budget,
            submitter=lambda *a, **k: {"job_id": "job"},
            waiter=lambda *a, **k: {"status": "succeeded", "output_text": "{}"},
            request_recorder=lambda value: records.append(copy.deepcopy(value)),
        )
    assert records[0]["request_id"] != records[1]["request_id"]
    assert [value["generation_options"]["max_tokens"] for value in records] == [1800, 32768]


def test_grader_wait_failure_keeps_submitted_job_identity() -> None:
    recorded = []
    def wait(*args, **kwargs):
        raise TimeoutError("poll failed")
    with pytest.raises(TimeoutError):
        grade_builder_prototype(
            artifact={"ui": {}}, user_turns=[], requirements={}, prohibited_assumptions=[], locale="en",
            submitter=lambda *a, **k: {"job_id": "job-wait"}, waiter=wait,
            response_recorder=lambda value: recorded.append(copy.deepcopy(value)),
        )
    assert recorded[-1]["job_id"] == "job-wait"
    assert recorded[-1]["wait_error"] == {"type": "TimeoutError", "message": "poll failed"}


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
