"""Deterministic Builder evaluation and optional execution topology policy."""

from __future__ import annotations

import copy
import hashlib
import json
from statistics import median
from typing import Any, Mapping, Sequence

from adaos.services.governed_workflow import validate_workflow_record


_CATEGORIES = (
    ("semantic_constraints", True),
    ("functional_tests", True),
    ("usability_probes", False),
    ("dependency_impact", False),
)
_COMPARISON_METRICS = (
    "time_to_diagnosis_seconds",
    "context_token_estimate",
    "rework_count",
    "missing_tests",
    "review_actions",
    "release_confidence",
)


class BuilderEvaluationError(ValueError):
    """Raised when evaluation evidence is ambiguous or unsafe."""


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check_status(item: Mapping[str, Any]) -> str:
    status = str(item.get("status") or "").strip().lower()
    if status in {"passed", "pass", "succeeded", "success"} or item.get("passed") is True:
        return "passed"
    if status in {"failed", "fail", "error", "violated"} or item.get("passed") is False:
        return "failed"
    return "missing"


def build_evaluation_evidence(
    change_id: str,
    *,
    semantic_constraints: Sequence[Mapping[str, Any]],
    functional_tests: Sequence[Mapping[str, Any]],
    usability_probes: Sequence[Mapping[str, Any]] = (),
    dependency_impact: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Evaluate four evidence classes without requiring another model agent."""

    identifier = str(change_id or "").strip()
    if not identifier:
        raise BuilderEvaluationError("change_id is required")
    supplied = {
        "semantic_constraints": semantic_constraints,
        "functional_tests": functional_tests,
        "usability_probes": usability_probes,
        "dependency_impact": dependency_impact,
    }
    categories: list[dict[str, Any]] = []
    evidence_refs: list[str] = []
    required_total = 0
    required_passed = 0
    for category, required in _CATEGORIES:
        checks = [copy.deepcopy(dict(item)) for item in supplied[category]]
        statuses = [_check_status(item) for item in checks]
        if not checks:
            status = "missing" if required else "not_applicable"
        elif "failed" in statuses:
            status = "failed"
        elif "missing" in statuses:
            status = "missing"
        else:
            status = "passed"
        if required:
            required_total += 1
            required_passed += int(status == "passed")
        for item in checks:
            ref = str(item.get("evidence_ref") or item.get("ref") or "").strip()
            if ref:
                evidence_refs.append(ref)
        categories.append(
            {
                "category": category,
                "status": status,
                "required": required,
                "checks": checks,
            }
        )
    if any(item["required"] and item["status"] == "failed" for item in categories):
        overall = "failed"
    elif required_passed != required_total:
        overall = "incomplete"
    else:
        overall = "passed"
    record = {
        "schema": "adaos.builder.evaluation_evidence.v1",
        "evidence_id": f"builder-evaluation:{identifier}:{_digest(categories)[7:23]}",
        "change_id": identifier,
        "status": overall,
        "categories": categories,
        "summary": {
            "required_passed": required_passed,
            "required_total": required_total,
            "confidence": round(required_passed / required_total, 6) if required_total else 0.0,
        },
        "evidence_refs": list(dict.fromkeys(evidence_refs)),
    }
    return validate_workflow_record("adaos.builder.evaluation_evidence.v1", record)


def compare_development_approaches(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare governed Builder Runs with direct coding on the same metric set."""

    grouped: dict[str, list[Mapping[str, Any]]] = {"governed": [], "direct": []}
    for item in observations:
        approach = str(item.get("approach") or "").strip().lower()
        if approach not in grouped:
            raise BuilderEvaluationError("approach must be governed or direct")
        grouped[approach].append(item)
    if not all(grouped.values()):
        raise BuilderEvaluationError("comparison requires governed and direct observations")
    summaries: dict[str, dict[str, float]] = {}
    for approach, items in grouped.items():
        summaries[approach] = {
            metric: round(
                float(median(float(item[metric]) for item in items if item.get(metric) is not None)),
                6,
            )
            for metric in _COMPARISON_METRICS
            if any(item.get(metric) is not None for item in items)
        }
    common = set(summaries["governed"]) & set(summaries["direct"])
    return {
        "schema": "adaos.builder.development_comparison.v1",
        "sample_size": {key: len(value) for key, value in grouped.items()},
        "metrics": summaries,
        "delta_governed_minus_direct": {
            metric: round(summaries["governed"][metric] - summaries["direct"][metric], 6)
            for metric in sorted(common)
        },
        "metric_definitions": list(_COMPARISON_METRICS),
    }


def plan_run_topology(
    *,
    risk_class: str,
    estimated_minutes: int,
    affected_components: int,
    single_executor_baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Select an optional topology only after a measurable baseline exists."""

    risk = str(risk_class or "read").strip().lower()
    long_or_high_risk = (
        risk in {"workspace_activation", "publication", "destructive"}
        or int(estimated_minutes) >= 30
        or int(affected_components) >= 4
    )
    baseline = copy.deepcopy(dict(single_executor_baseline or {}))
    baseline_ready = all(
        baseline.get(key) is not None
        for key in ("latency_seconds", "quality_score", "cost_units", "sample_size")
    ) and int(baseline.get("sample_size") or 0) > 0
    if not long_or_high_risk or not baseline_ready:
        reason = "single_executor_is_sufficient" if not long_or_high_risk else "baseline_required"
        return {
            "schema": "adaos.builder.run_topology.v1",
            "topology": "single_executor",
            "reason_code": reason,
            "roles": ["generator"],
            "authority": "workflow_transition_policy",
            "baseline": baseline or None,
        }
    return {
        "schema": "adaos.builder.run_topology.v1",
        "topology": "planner_generator_evaluator",
        "reason_code": "long_or_high_risk_with_baseline",
        "roles": ["planner", "generator", "evaluator"],
        "authority": "workflow_transition_policy",
        "baseline": baseline,
        "constraints": {
            "shared_change_id_required": True,
            "separate_run_ids_required": True,
            "evaluator_cannot_publish": True,
            "publication_approval_unchanged": True,
        },
    }


__all__ = [
    "BuilderEvaluationError",
    "build_evaluation_evidence",
    "compare_development_approaches",
    "plan_run_topology",
]
