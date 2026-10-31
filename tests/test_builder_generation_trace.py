import copy
import json

import pytest

from adaos.sdk.builder.observability import project_generation_trace
from adaos.services.builder.generation_trace import GenerationTraceRejected
from adaos.services.builder.task_spec import seal
from adaos.services.builder_context import digest
from test_builder_task_pipeline import invocation, Provider
from test_builder_task_interpretation import prepared


def evidence(tmp_path):
    invocation(tmp_path, Provider(prepared()[2]))
    admission = json.loads(
        next(tmp_path.glob("generation-plan-*.json")).read_text(encoding="utf-8")
    )
    interpretation = json.loads(
        next(tmp_path.glob("builder-interpretation-*.json")).read_text(encoding="utf-8")
    )
    plan = admission["generation_plan"]
    wires = [
        {
            "job_id": "generate-job",
            "request_id": "generate-request",
            "context_admission": {
                "generation_plan_ref": plan["generation_plan_id"],
                "parent_generation_plan_ref": None,
                "provider_attempt_input_digest": "actual-input",
            },
        }
    ]

    def observation(job, request):
        return {
            "job_id": job,
            "request_id": request,
            "status": "completed",
            "input_digest": "actual-input",
            "usage": {"input_tokens": 10, "output_tokens": 3},
            "telemetry": {
                "provider_calls": 1,
                "wire": {
                    "model": "selected",
                    "body_sha256": "a" * 64,
                    "body_digest_basis": "serialized_utf8",
                },
            },
        }

    observations = [
        observation("same-job", interpretation["request_id"]),
        observation("generate-job", "generate-request"),
    ]
    runs = [
        {
            "run_id": "interpretation-run",
            "change_id": "CH",
            "status": "succeeded",
            "execution_metrics": {"source_refs": ["llm_job:same-job"]},
        },
        {
            "run_id": "generation-run",
            "change_id": "CH",
            "status": "succeeded",
            "execution_metrics": {
                "source_refs": ["llm_job:generate-job"],
                "provider_calls": 99,
            },
        },
    ]
    return {
        "admission": admission,
        "interpretation": interpretation,
        "wires": wires,
        "observations": observations,
        "runs": runs,
    }


def test_source_plan_wire_observation_and_run_join_never_adds_aggregate_charges(
    tmp_path,
):
    inputs = evidence(tmp_path)
    before = copy.deepcopy(inputs)
    result = project_generation_trace(**inputs)
    assert result["totals"]["provider_calls"] == 2
    assert result["totals"]["known_llm_job_count"] == 2
    assert {row["stage"] for row in result["jobs"]} == {"interpretation", "generation"}
    assert result["logical_model_stages"] == ["interpretation", "generation"]
    assert inputs == before
    assert result["whole_turn_accounting_complete"] is False
    assert result["product_acceptance"] == "not_implied"
    assert any(
        row["code"] == "trace.prospective_origin_missing" for row in result["gaps"]
    )


@pytest.mark.parametrize(
    "missing", ["observation", "counter", "wire", "journal", "run"]
)
def test_missing_evidence_is_explicit_not_a_zero_charge_or_product_success(
    tmp_path, missing
):
    inputs = evidence(tmp_path)
    if missing == "observation":
        inputs["observations"].pop()
    elif missing == "counter":
        inputs["observations"][-1]["telemetry"].pop("provider_calls")
    elif missing == "wire":
        inputs["wires"] = []
    elif missing == "journal":
        inputs["interpretation"] = None
    else:
        inputs["runs"].pop()
    result = project_generation_trace(**inputs)
    assert (
        result["status"] == "partial"
        and result["whole_turn_accounting_complete"] is False
    )
    if missing in {"observation", "counter", "wire"}:
        assert not result["totals"]["provider_counters_complete_for_supplied_jobs"]
        assert "provider_calls" not in result["totals"]
    if missing == "run":
        assert any(
            row["code"] == "trace.canonical_run_missing" for row in result["gaps"]
        )


def test_duplicate_observations_are_idempotent_but_conflicts_cannot_be_merged(tmp_path):
    inputs = evidence(tmp_path)
    inputs["observations"].append(copy.deepcopy(inputs["observations"][-1]))
    assert project_generation_trace(**inputs)["totals"]["provider_calls"] == 2
    inputs["observations"][-1]["usage"]["input_tokens"] = 999
    with pytest.raises(GenerationTraceRejected, match="provider_observation_conflict"):
        project_generation_trace(**inputs)


@pytest.mark.parametrize(
    "fault", ["request", "input", "source", "journal_context", "aggregate"]
)
def test_different_requests_and_unverified_context_cannot_be_relabelled(
    tmp_path, fault
):
    inputs = evidence(tmp_path)
    if fault == "request":
        inputs["observations"][-1]["request_id"] = "other"
    elif fault == "input":
        inputs["observations"][-1]["input_digest"] = "other"
    elif fault == "source":
        plan = inputs["admission"]["generation_plan"]
        inputs["admission"]["generation_plan"] = seal(
            {**plan, "source_digest": "sha256:" + "0" * 64},
            id_key="generation_plan_id",
            prefix="generation-plan:",
        )
    elif fault == "journal_context":
        inputs["interpretation"]["messages"][0]["content"] = "changed"
    else:
        inputs["observations"][-1]["telemetry"]["repair"] = {"root_job_id": "another"}
    with pytest.raises(GenerationTraceRejected):
        project_generation_trace(**inputs)


def test_historical_interpretation_is_not_charged_to_an_s1_replay(tmp_path):
    inputs = evidence(tmp_path)
    plan = inputs["admission"]["generation_plan"]
    plan = seal(
        {**plan, "route": "single_stage", "stages": [plan["stages"][-1]]},
        id_key="generation_plan_id",
        prefix="generation-plan:",
    )
    inputs["admission"]["generation_plan"] = plan
    inputs["admission"]["interpretation_receipt"] = {
        "provider_calls": 0,
        "recovered_interpretation_job_id": "same-job",
    }
    inputs["wires"][0]["context_admission"]["generation_plan_ref"] = plan[
        "generation_plan_id"
    ]
    result = project_generation_trace(**inputs)
    assert result["totals"]["provider_calls"] == 1
    assert result["historical_interpretation_job_refs"] == ["same-job"]
    assert result["out_of_scope_observation_job_refs"] == ["same-job"]


def test_duplicate_canonical_summary_is_visible_but_does_not_double_provider_total(
    tmp_path,
):
    inputs = evidence(tmp_path)
    inputs["runs"].append(
        {
            "run_id": "legacy-aggregate",
            "change_id": "CH",
            "execution_metrics": {
                "source_refs": ["llm_job:generate-job"],
                "provider_calls": 1,
            },
        }
    )
    result = project_generation_trace(**inputs)
    assert result["totals"]["provider_calls"] == 2
    assert any(
        row["code"] == "trace.canonical_job_duplicated" for row in result["gaps"]
    )


def test_identical_canonical_snapshots_are_idempotent_but_changed_ones_need_selection(
    tmp_path,
):
    inputs = evidence(tmp_path)
    inputs["runs"].append(copy.deepcopy(inputs["runs"][-1]))
    assert not any(
        row["code"] == "trace.canonical_job_duplicated"
        for row in project_generation_trace(**inputs)["gaps"]
    )
    inputs["runs"][-1]["status"] = "failed"
    with pytest.raises(GenerationTraceRejected, match="canonical_snapshot_conflict"):
        project_generation_trace(**inputs)


def test_raw_response_and_private_telemetry_are_not_copied_into_trace(tmp_path):
    inputs = evidence(tmp_path)
    inputs["observations"][-1]["output_text"] = "PRIVATE_RESPONSE"
    inputs["observations"][-1]["telemetry"]["headers"] = {
        "Authorization": "PRIVATE_TOKEN"
    }
    inputs["admission"]["private_environment"] = "PRIVATE_CONFIG"
    result = project_generation_trace(**inputs)
    assert "PRIVATE_" not in json.dumps(result)


def test_repair_wire_requires_exact_parent_job_and_plan(tmp_path):
    inputs = evidence(tmp_path)
    parent = inputs["admission"]["generation_plan"]
    child = seal(
        {
            **parent,
            "context_stage": "repair",
            "parent_generation_plan_ref": parent["generation_plan_id"],
            "parent_request_id": "generate-request",
            "parent_provider_job_id": "generate-job",
            "stages": [{**parent["stages"][-1], "name": "repair"}],
        },
        id_key="generation_plan_id",
        prefix="generation-plan:",
    )
    inputs["repairs"] = [{"generation_plan": child}]
    inputs["wires"].append(
        {
            "job_id": "repair-job",
            "request_id": "repair-request",
            "context_admission": {
                "generation_plan_ref": child["generation_plan_id"],
                "parent_generation_plan_ref": parent["generation_plan_id"],
                "parent_request_id": "generate-request",
                "provider_attempt_input_digest": "actual-input",
            },
        }
    )
    inputs["observations"].append(
        {
            **copy.deepcopy(inputs["observations"][-1]),
            "job_id": "repair-job",
            "request_id": "repair-request",
        }
    )
    assert project_generation_trace(**inputs)["totals"]["provider_calls"] == 3
    child = seal(
        {**child, "parent_provider_job_id": "unknown-job"},
        id_key="generation_plan_id",
        prefix="generation-plan:",
    )
    inputs["repairs"] = [{"generation_plan": child}]
    inputs["wires"][-1]["context_admission"]["generation_plan_ref"] = child[
        "generation_plan_id"
    ]
    with pytest.raises(GenerationTraceRejected, match="repair_parent_job_conflict"):
        project_generation_trace(**inputs)


def test_provider_continuation_does_not_become_a_third_model_stage_or_repair(tmp_path):
    inputs = evidence(tmp_path)
    inputs["observations"][-1]["telemetry"]["provider_calls"] = 2
    result = project_generation_trace(**inputs)
    assert result["totals"]["provider_calls"] == 3
    assert result["totals"]["known_llm_job_count"] == 2
    assert result["totals"]["known_repair_job_count"] == 0
    assert result["logical_model_stages"] == ["interpretation", "generation"]
    assert result["route"] == "two_stage" and result["retrieval"] == "inline"
    assert result["context_strategy"] == "full"


def add_origin(inputs):
    admission = inputs["admission"]
    task, plan = admission["task_spec"], admission["generation_plan"]
    origin = seal(
        {
            "schema": "adaos.builder.generation_origin.v1",
            "object_type": "scenario",
            "object_id": "qualification",
            "project_ref": task["scope"]["project_ref"],
            "change_id": "CH",
            "origin_generation": 0,
            "manifest_digest": None,
            "source_digest": task["source"]["digest"],
            "source_message_ref": task["source"]["provenance"].get("ref"),
            "task_spec_ref": task["task_spec_id"],
            "generation_plan_ref": plan["generation_plan_id"],
            "execution_group_ref": "task-execution:"
            + digest(
                {
                    "project": task["scope"]["project_ref"],
                    "source": task["source"]["digest"],
                }
            )[7:31],
        },
        id_key="origin_id",
        prefix="generation-origin:",
    )
    admission["execution_origin"] = origin
    inputs["wires"][0]["execution_origin"] = origin


def test_origin_missing_is_a_gap_but_a_different_canonical_owner_is_a_conflict(
    tmp_path,
):
    inputs = evidence(tmp_path)
    add_origin(inputs)
    result = project_generation_trace(**inputs)
    assert not any(
        row["code"] == "trace.prospective_origin_missing" for row in result["gaps"]
    )
    inputs["wires"][0].pop("execution_origin")
    assert any(
        row["code"] == "trace.wire_origin_missing"
        for row in project_generation_trace(**inputs)["gaps"]
    )
    inputs["runs"][-1]["change_id"] = "other-change"
    with pytest.raises(GenerationTraceRejected, match="canonical_change_conflict"):
        project_generation_trace(**inputs)


@pytest.mark.parametrize(
    "basis,sha",
    [
        ("unknown", "a" * 64),
        ("serialized_utf8", None),
        ("serialized_utf8", "not-a-sha"),
    ],
)
def test_unmarked_or_invalid_wire_hash_is_not_exact_byte_evidence(tmp_path, basis, sha):
    inputs = evidence(tmp_path)
    inputs["observations"][-1]["telemetry"]["wire"].update(
        body_digest_basis=basis, body_sha256=sha
    )
    assert any(
        row["code"] == "trace.exact_wire_body_pin_missing"
        for row in project_generation_trace(**inputs)["gaps"]
    )


def test_only_compatible_explicit_cost_bases_can_be_added(tmp_path):
    inputs = evidence(tmp_path)
    for row in inputs["observations"]:
        row["telemetry"]["cost"] = {"basis": "estimate", "estimated_usd": 0.25}
    assert project_generation_trace(**inputs)["totals"]["cost_usd"] == 0.5
    inputs["observations"][-1]["telemetry"]["cost"] = {"basis": "metered", "usd": 0.25}
    assert "cost_usd" not in project_generation_trace(**inputs)["totals"]
    inputs["observations"][-1]["telemetry"].pop("cost")
    assert "cost_usd" not in project_generation_trace(**inputs)["totals"]


def test_observed_provider_budget_excess_is_visible_without_hiding_the_calls(tmp_path):
    inputs = evidence(tmp_path)
    inputs["observations"][-1]["telemetry"]["provider_calls"] = 8
    result = project_generation_trace(**inputs)
    assert result["totals"]["provider_calls"] == 9
    assert any(
        row["code"] == "trace.known_provider_budget_exceeded" for row in result["gaps"]
    )


def test_missing_and_malformed_receipts_do_not_echo_their_private_contents():
    with pytest.raises(GenerationTraceRejected, match="trace.invalid_input") as error:
        project_generation_trace(admission={"PRIVATE_INPUT": "PRIVATE_TOKEN"})
    assert "PRIVATE_" not in str(error.value)


@pytest.mark.parametrize("key", ["model", "input_digest", "status"])
def test_wrongly_shaped_metadata_is_not_stringified_into_public_diagnostics(
    tmp_path, key
):
    inputs = evidence(tmp_path)
    inputs["observations"][-1][key] = {"authorization": "PRIVATE_TOKEN"}
    with pytest.raises(
        GenerationTraceRejected, match="observation_shape_invalid"
    ) as error:
        project_generation_trace(**inputs)
    assert "PRIVATE_" not in str(error.value)
