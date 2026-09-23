import json

from adaos.domain.capability_binding_state import CBSBenchmarkTelemetry
from adaos.services.capability_binding_state.benchmark_program import (
    run_bounded_cbs9_benchmark,
)


def test_bounded_program_is_matched_and_publishes_negative_result(tmp_path) -> None:
    bundle = run_bounded_cbs9_benchmark(tmp_path)

    assert bundle["schema"] == "adaos.cbs.benchmark_bundle.v1"
    assert len(bundle["cases"]) == 3
    assert len(bundle["observations"]) == 6
    assert bundle["report"]["matched_case_count"] == 3
    assert bundle["report"]["representative_matched"] == 2
    assert bundle["report"]["heldout_matched"] == 1
    assert bundle["report"]["causal_claim_admissible"] is True
    assert bundle["report"]["primary_claim"]["status"] == "not_supported"
    assert bundle["report"]["maturity_distribution"]["cbs"] == {
        "application_local": 7
    }
    assert bundle["historical_context"] == {
        "observation_count": 0,
        "telemetry_digests": [],
        "matched_legacy_arms": 0,
        "use": "unmatched context only",
    }
    drive = bundle["execution"]["benchmark-case:adaos-drive/trial-acceptance"]
    assert drive["runs"]["legacy"]["reason"] == "ambiguous_runtime_selection"
    assert drive["runs"]["cbs"]["passed"] is True
    assert all(
        run["passed"]
        for case_ref, case in bundle["execution"].items()
        for variant, run in case["runs"].items()
        if (case_ref, variant)
        != ("benchmark-case:adaos-drive/trial-acceptance", "legacy")
    )


def test_bounded_program_does_not_expose_heldout_recipe_to_authoring(tmp_path) -> None:
    bundle = run_bounded_cbs9_benchmark(tmp_path)
    heldout = next(case for case in bundle["cases"] if case["cohort"] == "heldout")

    assert heldout["evaluation"]["sealed"] is True
    assert heldout["evaluation"]["authoring_visibility"] == "input_only"
    assert "recipe" not in bundle["execution"][heldout["case_ref"]]["authoring_input"]


def test_bounded_program_consumes_historical_telemetry_without_rewriting(tmp_path) -> None:
    digests = ["sha256:" + token * 64 for token in "12345678"]
    telemetry = CBSBenchmarkTelemetry.create(
        run_id="historical-cbs5",
        semantic_revision_digest=digests[0],
        environment_profile_digest=digests[1],
        requirement_digest=digests[2],
        contract_digests=(digests[3],),
        package_digests=(digests[4],),
        application_resolution_digest=digests[5],
        resolution_plan_digest=digests[6],
        workspace_lock_digest=digests[7],
        requirement_total=1,
        requirement_resolved=1,
        resolver_candidate_count=1,
        binding_selection_cost={
            "enumerated": 1,
            "package_resolutions": 1,
            "evidence_checks": 1,
        },
        context_tokens={"input": 0, "output": 0},
        residual_implementation={"refs": [], "diff_lines": 0},
        package_reuse={"selected": 1, "reused": 0},
        contract_reuse={"selected": 1, "reused": 0},
        resolution_duration_ms=1,
        activation_duration_ms=1,
        manual_interventions=0,
        e2e_result="passed",
        invariant_results={
            "materialization_independence": True,
            "package_topology_independence": True,
            "state_continuity": True,
            "application_independence": True,
            "atomic_rebinding": True,
        },
        started_at="2026-09-22T12:00:00+00:00",
        completed_at="2026-09-22T12:00:01+00:00",
    )
    telemetry_path = tmp_path / "history.json"
    telemetry_path.write_text(json.dumps(telemetry.to_dict()), encoding="utf-8")

    bundle = run_bounded_cbs9_benchmark(
        tmp_path / "run",
        historical_telemetry_paths=(telemetry_path,),
    )

    assert bundle["historical_telemetry"] == [telemetry.to_dict()]
    assert bundle["historical_context"]["telemetry_digests"] == [telemetry.digest]
    assert bundle["historical_context"]["matched_legacy_arms"] == 0
