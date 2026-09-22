import json
from pathlib import Path

import pytest

from adaos.domain.capability_binding_state import CBSBenchmarkTelemetry
from adaos.services.capability_binding_state import (
    CBSBenchmarkError,
    build_benchmark_report,
    create_benchmark_observation,
    freeze_benchmark_case,
    load_cbs_telemetry,
    observation_from_cbs_telemetry,
)


DIGESTS = ["sha256:" + token * 64 for token in "123456789abcdef"]


def _telemetry() -> CBSBenchmarkTelemetry:
    return CBSBenchmarkTelemetry.create(
        run_id="cbs-run",
        semantic_revision_digest=DIGESTS[0],
        environment_profile_digest=DIGESTS[1],
        requirement_digest=DIGESTS[2],
        contract_digests=(DIGESTS[3],),
        package_digests=(DIGESTS[4],),
        application_resolution_digest=DIGESTS[5],
        resolution_plan_digest=DIGESTS[6],
        workspace_lock_digest=DIGESTS[7],
        requirement_total=1,
        requirement_resolved=1,
        resolver_candidate_count=2,
        binding_selection_cost={
            "enumerated": 2,
            "package_resolutions": 1,
            "evidence_checks": 2,
        },
        context_tokens={"input": 100, "output": 20},
        residual_implementation={"refs": [], "diff_lines": 0},
        package_reuse={"selected": 2, "reused": 1},
        contract_reuse={"selected": 1, "reused": 1},
        resolution_duration_ms=10,
        activation_duration_ms=20,
        manual_interventions=0,
        e2e_result="passed",
        invariant_results={
            "materialization_independence": True,
            "package_topology_independence": True,
            "state_continuity": True,
            "application_independence": True,
            "atomic_rebinding": True,
        },
        started_at="2026-09-23T10:00:00+00:00",
        completed_at="2026-09-23T10:00:01+00:00",
    )


def _case(cohort: str = "representative") -> dict:
    return freeze_benchmark_case(
        case_ref="benchmark-case:flowboard/crud",
        title="Flowboard CRUD",
        cohort=cohort,
        workload_digest=DIGESTS[0],
        environment_profile_digest=DIGESTS[1],
        model_id="none",
        tool_budget={"max_calls": 20},
        requirement_total=1,
    )


def test_unmatched_history_is_reported_as_missing_not_zero() -> None:
    case = _case()
    cbs = observation_from_cbs_telemetry(
        case, _telemetry(), model_id="none", tool_budget={"max_calls": 20}
    )

    report = build_benchmark_report([case], [cbs])

    result = report["cases"][0]
    assert result["matched"] is False
    assert result["missing_variants"] == ["legacy"]
    metric = result["metrics"]["manual_interventions"]
    assert metric["values"] == {"legacy": None, "cbs": 0}
    assert metric["delta_cbs_minus_legacy"] is None
    assert report["causal_claim_admissible"] is False


def test_matched_pair_has_deltas_and_heldout_accounting() -> None:
    case = _case(cohort="heldout")
    controls = dict(case["controls"])
    legacy = create_benchmark_observation(
        case=case,
        variant="legacy",
        run_ref="legacy-run",
        controls=controls,
        metrics={
            "e2e_success": 1,
            "requirements_resolution_rate": 1,
            "context_tokens_total": 180,
            "residual_diff_lines": 30,
            "manual_interventions": 2,
            "end_to_end_duration_ms": 50,
            "package_reuse_rate": 0,
            "contract_reuse_rate": 0,
            "invariant_pass_rate": 0.4,
        },
        source_digests=[DIGESTS[8]],
    )
    cbs = observation_from_cbs_telemetry(
        case, _telemetry(), model_id="none", tool_budget={"max_calls": 20}
    )

    report = build_benchmark_report([case], [legacy, cbs])

    assert report["matched_case_count"] == 1
    assert report["heldout_matched"] == 1
    assert report["causal_claim_admissible"] is True
    assert report["cases"][0]["metrics"]["manual_interventions"]["improved"] is True


def test_control_mismatch_is_rejected() -> None:
    with pytest.raises(CBSBenchmarkError, match="controls"):
        observation_from_cbs_telemetry(
            _case(), _telemetry(), model_id="different", tool_budget={"max_calls": 20}
        )


def test_loader_validates_and_deduplicates_telemetry(tmp_path: Path) -> None:
    record = _telemetry()
    (tmp_path / "one.json").write_text(json.dumps(record.to_dict()), encoding="utf-8")
    (tmp_path / "two.json").write_text(json.dumps(record.to_dict()), encoding="utf-8")
    (tmp_path / "other.json").write_text(
        '{"schema":"something.else"}', encoding="utf-8"
    )

    loaded = load_cbs_telemetry([tmp_path])

    assert [item.digest for item in loaded] == [record.digest]
