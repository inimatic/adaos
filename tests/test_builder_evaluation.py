from __future__ import annotations

import pytest

from adaos.services.builder.evaluation import (
    BuilderEvaluationError,
    build_evaluation_evidence,
    compare_development_approaches,
    plan_run_topology,
)


def test_evaluator_combines_semantic_functional_usability_and_dependency_evidence() -> None:
    result = build_evaluation_evidence(
        "CH-recipes",
        semantic_constraints=[{"status": "passed", "evidence_ref": "constraint:labels"}],
        functional_tests=[{"passed": True, "evidence_ref": "test:add-recipe"}],
        usability_probes=[{"status": "passed", "evidence_ref": "probe:compact"}],
        dependency_impact=[{"status": "passed", "evidence_ref": "dependency:recipe-skill"}],
    )

    assert result["status"] == "passed"
    assert result["summary"] == {
        "required_passed": 2,
        "required_total": 2,
        "confidence": 1.0,
    }
    assert len(result["evidence_refs"]) == 4


def test_evaluator_fails_without_calling_an_additional_model() -> None:
    result = build_evaluation_evidence(
        "CH-recipes",
        semantic_constraints=[{"status": "violated"}],
        functional_tests=[{"status": "passed"}],
    )
    assert result["status"] == "failed"


def test_comparison_report_tracks_the_declared_direct_access_metrics() -> None:
    report = compare_development_approaches(
        [
            {
                "approach": "governed",
                "time_to_diagnosis_seconds": 30,
                "context_token_estimate": 1000,
                "rework_count": 1,
                "missing_tests": 0,
                "review_actions": 2,
                "release_confidence": 0.9,
            },
            {
                "approach": "direct",
                "time_to_diagnosis_seconds": 20,
                "context_token_estimate": 700,
                "rework_count": 3,
                "missing_tests": 2,
                "review_actions": 5,
                "release_confidence": 0.5,
            },
        ]
    )
    assert report["sample_size"] == {"governed": 1, "direct": 1}
    assert report["delta_governed_minus_direct"]["rework_count"] == -2.0
    assert report["delta_governed_minus_direct"]["release_confidence"] == pytest.approx(0.4)
    with pytest.raises(BuilderEvaluationError):
        compare_development_approaches([{"approach": "governed"}])


def test_multi_role_topology_requires_both_need_and_a_single_executor_baseline() -> None:
    without_baseline = plan_run_topology(
        risk_class="publication",
        estimated_minutes=45,
        affected_components=5,
    )
    with_baseline = plan_run_topology(
        risk_class="publication",
        estimated_minutes=45,
        affected_components=5,
        single_executor_baseline={
            "latency_seconds": 120,
            "quality_score": 0.8,
            "cost_units": 1.0,
            "sample_size": 5,
        },
    )
    assert without_baseline["topology"] == "single_executor"
    assert without_baseline["reason_code"] == "baseline_required"
    assert with_baseline["topology"] == "planner_generator_evaluator"
    assert with_baseline["constraints"]["evaluator_cannot_publish"] is True
