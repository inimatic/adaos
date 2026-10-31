import copy

import pytest

from test_builder_generation_trace import evidence
from adaos.sdk.builder.observability import project_generation_trace
from adaos.services.builder.generation_trace import GenerationTraceRejected


def scoped_evidence(tmp_path):
    inputs = evidence(tmp_path)
    inputs["execution_snapshot"] = {
        "schema": "adaos.ai.execution_scope_snapshot.v1",
        "observed_at": "2026-10-31T00:00:00+00:00",
        "source": "core.ai.attempts",
        "anchor_job_id": "generate-job",
        "consumer": "skill:builder_skill",
        "scope": "trusted_invocation_snapshot",
        "turn_scope": "a" * 64,
        "observations": copy.deepcopy(inputs["observations"]),
        "job_set_complete_as_of_read": True,
        "submission_closed": False,
        "whole_turn_accounting_complete": False,
    }
    return inputs


def test_default_projection_does_not_add_a_snapshot_or_change_its_identity(tmp_path):
    inputs = evidence(tmp_path)
    before = project_generation_trace(**inputs)
    after = project_generation_trace(**inputs, execution_snapshot=None)
    assert after == before and "execution_scope_snapshot" not in after


def test_registry_and_explicit_observations_deduplicate_without_claiming_closure(
    tmp_path,
):
    inputs = scoped_evidence(tmp_path)
    before = copy.deepcopy(inputs)
    report = project_generation_trace(**inputs)
    assert report["totals"]["provider_calls"] == 2
    assert report["execution_scope_snapshot"]["jobs_not_in_generation_journal"] == []
    assert report["whole_turn_accounting_complete"] is False
    assert any(
        row["code"] == "trace.enclosing_turn_manifest_missing" for row in report["gaps"]
    )
    assert inputs == before


def test_registry_snapshot_detects_a_real_invocation_job_missing_from_plan_journal(
    tmp_path,
):
    inputs = scoped_evidence(tmp_path)
    extra = copy.deepcopy(inputs["observations"][-1])
    extra.update(job_id="unbound-job", request_id="unbound-request")
    inputs["execution_snapshot"]["observations"].append(extra)
    inputs["observations"] = []
    report = project_generation_trace(**inputs)
    assert report["execution_scope_snapshot"]["jobs_not_in_generation_journal"] == [
        "unbound-job"
    ]
    assert {
        "code": "trace.registered_job_not_in_generation_journal",
        "ref": "unbound-job",
    } in report["gaps"]
    assert report["totals"]["provider_calls"] == 2  # Not a fabricated plan attribution.
    assert report["out_of_scope_observation_job_refs"] == ["unbound-job"]


def test_plan_jobs_outside_current_registry_scope_remain_a_gap(tmp_path):
    inputs = scoped_evidence(tmp_path)
    inputs["execution_snapshot"]["observations"].pop(0)
    result = project_generation_trace(**inputs)
    assert {
        "code": "trace.generation_job_not_in_invocation_snapshot",
        "ref": "same-job",
    } in result["gaps"]
    assert result["whole_turn_accounting_complete"] is False


def test_unqualified_anchor_cannot_be_promoted_to_a_trusted_turn(tmp_path):
    inputs = scoped_evidence(tmp_path)
    snapshot = inputs["execution_snapshot"]
    snapshot.update(scope="single_unqualified_attempt", turn_scope=None)
    snapshot["observations"] = snapshot["observations"][-1:]
    result = project_generation_trace(**inputs)
    assert {"code": "trace.invocation_scope_unqualified"} in result["gaps"]
    snapshot["observations"] = copy.deepcopy(inputs["observations"])
    with pytest.raises(GenerationTraceRejected, match="unqualified_expansion"):
        project_generation_trace(**inputs)


@pytest.mark.parametrize(
    "fault",
    [
        "consumer",
        "scope",
        "anchor",
        "duplicate",
        "missing_anchor",
        "closure",
        "time",
        "private",
    ],
)
def test_snapshot_conflicts_or_unbounded_private_metadata_are_not_admitted(
    tmp_path, fault
):
    inputs = scoped_evidence(tmp_path)
    snapshot = inputs["execution_snapshot"]
    if fault == "consumer":
        snapshot["consumer"] = "skill:other"
    elif fault == "scope":
        snapshot["turn_scope"] = "invalid"
    elif fault == "anchor":
        snapshot["anchor_job_id"] = "foreign-job"
    elif fault == "duplicate":
        snapshot["observations"].append(snapshot["observations"][-1])
    elif fault == "missing_anchor":
        snapshot["observations"].pop()
    elif fault == "closure":
        snapshot["submission_closed"] = True
    elif fault == "time":
        snapshot["observed_at"] = "private raw provider prose"
    else:
        snapshot["credentials"] = "private"
    with pytest.raises(GenerationTraceRejected):
        project_generation_trace(**inputs)


def test_conflicting_registry_and_explicit_measurements_are_not_silently_selected(
    tmp_path,
):
    inputs = scoped_evidence(tmp_path)
    inputs["execution_snapshot"]["observations"][-1]["telemetry"]["provider_calls"] = 2
    with pytest.raises(GenerationTraceRejected, match="observation_conflict"):
        project_generation_trace(**inputs)


def test_snapshot_projection_does_not_echo_private_telemetry_or_invalid_envelopes(
    tmp_path,
):
    inputs = scoped_evidence(tmp_path)
    inputs["execution_snapshot"]["observations"][0]["telemetry"]["credentials"] = (
        "PRIVATE"
    )
    assert "PRIVATE" not in str(project_generation_trace(**inputs))
    inputs["execution_snapshot"]["credentials"] = "PRIVATE"
    with pytest.raises(GenerationTraceRejected) as caught:
        project_generation_trace(**inputs)
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_provider_continuations_are_not_counted_as_extra_logical_stages(tmp_path):
    inputs = scoped_evidence(tmp_path)
    inputs["observations"][-1]["telemetry"]["provider_calls"] = 2
    inputs["execution_snapshot"]["observations"][-1]["telemetry"]["provider_calls"] = 2
    result = project_generation_trace(**inputs)
    assert result["totals"]["provider_calls"] == 3
    assert result["totals"]["known_llm_job_count"] == 2
    assert result["logical_model_stages"] == ["interpretation", "generation"]
