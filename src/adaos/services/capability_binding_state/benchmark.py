"""Matched CBS9 benchmark over benchmark-compatible historical telemetry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.capability_binding_state import CBSBenchmarkTelemetry


class CBSBenchmarkError(ValueError):
    pass


_METRICS = {
    "e2e_success": "higher",
    "requirements_resolution_rate": "higher",
    "context_tokens_total": "lower",
    "residual_diff_lines": "lower",
    "manual_interventions": "lower",
    "end_to_end_duration_ms": "lower",
    "package_reuse_rate": "higher",
    "contract_reuse_rate": "higher",
    "independent_reuse_rate": "higher",
    "composition_complexity": "lower",
    "semantic_overlap_rate": "lower",
    "substitution_cost": "lower",
    "migration_count": "lower",
    "regression_count": "lower",
    "marginal_cost": "lower",
    "invariant_pass_rate": "higher",
}

_DIGEST_PREFIX = "sha256:"


def _require_digest(value: Any, *, field: str) -> str:
    token = str(value or "")
    if (
        not token.startswith(_DIGEST_PREFIX)
        or len(token) != len(_DIGEST_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in token[7:])
    ):
        raise CBSBenchmarkError(f"{field} must be a sha256 digest")
    return token


def _evaluation_controls(
    value: Mapping[str, Any] | None,
    *,
    cohort: str,
) -> dict[str, Any]:
    evaluation = dict(value or {})
    required = (
        "input_digest",
        "rubric_digest",
        "target_labels_digest",
        "solution_recipe_digest",
    )
    missing = [field for field in required if not evaluation.get(field)]
    if missing:
        raise CBSBenchmarkError(
            "benchmark evaluation controls are missing: " + ", ".join(missing)
        )
    for field in required:
        evaluation[field] = _require_digest(evaluation[field], field=field)
    visibility = str(evaluation.get("authoring_visibility") or "").strip()
    if visibility not in {"input_only", "input_and_rubric"}:
        raise CBSBenchmarkError(
            "evaluation authoring_visibility must be input_only or input_and_rubric"
        )
    evaluation["authoring_visibility"] = visibility
    evaluation["sealed"] = bool(evaluation.get("sealed"))
    if cohort == "heldout" and (
        not evaluation["sealed"] or visibility != "input_only"
    ):
        raise CBSBenchmarkError(
            "heldout evaluation must be sealed and expose only its input to authoring"
        )
    return evaluation


def _digest(value: Mapping[str, Any]) -> str:
    return canonical_payload_digest(dict(value))


def freeze_benchmark_case(
    *,
    case_ref: str,
    title: str,
    cohort: str,
    workload_digest: str,
    environment_profile_digest: str,
    model_id: str,
    tool_budget: Mapping[str, Any],
    requirement_total: int,
    sequence_index: int = 1,
    inventory_size: int = 0,
    evaluation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze the controls required for a meaningful legacy/CBS pair."""

    if cohort not in {"representative", "heldout"}:
        raise CBSBenchmarkError("cohort must be representative or heldout")
    if requirement_total < 1:
        raise CBSBenchmarkError("benchmark cases require at least one requirement")
    if sequence_index < 1:
        raise CBSBenchmarkError("benchmark sequence_index must be positive")
    if inventory_size < 0:
        raise CBSBenchmarkError("benchmark inventory_size cannot be negative")
    body = {
        "schema": "adaos.cbs.benchmark_case.v1",
        "case_ref": str(case_ref),
        "title": str(title),
        "cohort": cohort,
        "workload_digest": str(workload_digest),
        "environment_profile_digest": str(environment_profile_digest),
        "sequence_index": int(sequence_index),
        "inventory_size": int(inventory_size),
        "evaluation": _evaluation_controls(evaluation, cohort=cohort),
        "controls": {
            "model_id": str(model_id),
            "tool_budget": dict(tool_budget),
            "requirement_total": int(requirement_total),
        },
        "required_variants": ["legacy", "cbs"],
        "metrics": dict(_METRICS),
    }
    return {**body, "case_digest": _digest(body)}


def create_benchmark_observation(
    *,
    case: Mapping[str, Any],
    variant: str,
    run_ref: str,
    controls: Mapping[str, Any],
    metrics: Mapping[str, Any],
    source_digests: Iterable[str],
    treatment: Mapping[str, Any] | None = None,
    identity_digests: Iterable[str] = (),
    evidence_digests: Iterable[str] = (),
) -> dict[str, Any]:
    if variant not in {"legacy", "cbs"}:
        raise CBSBenchmarkError("benchmark variant must be legacy or cbs")
    expected = dict(case.get("controls") or {})
    actual = dict(controls)
    if actual != expected:
        raise CBSBenchmarkError("observation controls do not match the frozen case")
    normalized: dict[str, Any] = {}
    for metric in _METRICS:
        value = metrics.get(metric)
        if value is not None and not isinstance(value, (int, float)):
            raise CBSBenchmarkError(f"metric {metric} must be numeric or missing")
        normalized[metric] = value
    sources = sorted({_require_digest(item, field="source_digest") for item in source_digests})
    if not sources:
        raise CBSBenchmarkError("benchmark observation requires source digests")
    identities = sorted(
        {_require_digest(item, field="identity_digest") for item in identity_digests}
    )
    evidence = sorted(
        {_require_digest(item, field="evidence_digest") for item in evidence_digests}
    )
    treatment_value = dict(treatment or {})
    treatment_kind = str(treatment_value.get("kind") or variant).strip()
    if treatment_kind != variant:
        raise CBSBenchmarkError("observation treatment kind must match its variant")
    maturity = treatment_value.get("inventory_maturity") or {}
    if not isinstance(maturity, Mapping):
        raise CBSBenchmarkError("inventory_maturity must be an object")
    maturity_value = {
        str(key): int(count)
        for key, count in maturity.items()
        if int(count) >= 0
    }
    treatment_value = {
        **treatment_value,
        "kind": treatment_kind,
        "inventory_maturity": dict(sorted(maturity_value.items())),
    }
    body = {
        "schema": "adaos.cbs.benchmark_observation.v1",
        "case_ref": case["case_ref"],
        "case_digest": case["case_digest"],
        "variant": variant,
        "run_ref": str(run_ref),
        "workload_digest": case["workload_digest"],
        "environment_profile_digest": case["environment_profile_digest"],
        "controls": actual,
        "metrics": normalized,
        "treatment": treatment_value,
        "source_digests": sources,
        "identity_digests": identities,
        "evidence_digests": evidence,
    }
    return {**body, "observation_digest": _digest(body)}


def _ratio(numerator: Any, denominator: Any) -> float | None:
    if (
        not isinstance(numerator, int)
        or not isinstance(denominator, int)
        or denominator <= 0
    ):
        return None
    return numerator / denominator


def observation_from_cbs_telemetry(
    case: Mapping[str, Any],
    telemetry: CBSBenchmarkTelemetry | Mapping[str, Any],
    *,
    model_id: str,
    tool_budget: Mapping[str, Any],
) -> dict[str, Any]:
    value = telemetry.to_dict() if hasattr(telemetry, "to_dict") else dict(telemetry)
    if value.get("schema") != "adaos.cbs.benchmark_telemetry.v1":
        raise CBSBenchmarkError("source is not CBS benchmark-compatible telemetry")
    if value.get("semantic_revision_digest") != case.get("workload_digest"):
        raise CBSBenchmarkError("telemetry workload does not match the frozen case")
    if value.get("environment_profile_digest") != case.get(
        "environment_profile_digest"
    ):
        raise CBSBenchmarkError("telemetry environment does not match the frozen case")
    invariants = value.get("invariant_results") or {}
    metrics = {
        "e2e_success": 1 if value.get("e2e_result") == "passed" else 0,
        "requirements_resolution_rate": _ratio(
            value.get("requirement_resolved"), value.get("requirement_total")
        ),
        "context_tokens_total": (
            int(value["context_tokens"]["input"])
            + int(value["context_tokens"]["output"])
            if isinstance(value.get("context_tokens"), Mapping)
            and isinstance(value["context_tokens"].get("input"), int)
            and isinstance(value["context_tokens"].get("output"), int)
            else None
        ),
        "residual_diff_lines": (value.get("residual_implementation") or {}).get(
            "diff_lines"
        ),
        "manual_interventions": value.get("manual_interventions"),
        "end_to_end_duration_ms": (
            int(value["resolution_duration_ms"]) + int(value["activation_duration_ms"])
            if isinstance(value.get("resolution_duration_ms"), int)
            and isinstance(value.get("activation_duration_ms"), int)
            else None
        ),
        "package_reuse_rate": _ratio(
            (value.get("package_reuse") or {}).get("reused"),
            (value.get("package_reuse") or {}).get("selected"),
        ),
        "contract_reuse_rate": _ratio(
            (value.get("contract_reuse") or {}).get("reused"),
            (value.get("contract_reuse") or {}).get("selected"),
        ),
        "invariant_pass_rate": _ratio(
            sum(result is True for result in invariants.values()), len(invariants)
        ),
    }
    controls = {
        "model_id": str(model_id),
        "tool_budget": dict(tool_budget),
        "requirement_total": int(value["requirement_total"]),
    }
    return create_benchmark_observation(
        case=case,
        variant="cbs",
        run_ref=str(value["run_id"]),
        controls=controls,
        metrics=metrics,
        source_digests=[str(value["telemetry_digest"])],
        treatment={
            "kind": "cbs",
            "inventory_maturity": {},
            "telemetry_schema": value["schema"],
        },
        identity_digests=(
            str(value["semantic_revision_digest"]),
            str(value["application_resolution_digest"]),
            str(value["resolution_plan_digest"]),
            str(value["workspace_lock_digest"]),
        ),
        evidence_digests=(str(value["telemetry_digest"]),),
    )


def load_cbs_telemetry(paths: Iterable[Path]) -> list[CBSBenchmarkTelemetry]:
    """Load only validated telemetry records; ignore unrelated JSON artifacts."""

    records: list[CBSBenchmarkTelemetry] = []
    seen: set[str] = set()
    candidates: list[Path] = []
    for raw in paths:
        path = Path(raw)
        candidates.extend(path.rglob("*.json") if path.is_dir() else [path])
    for path in sorted(set(candidates)):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            not isinstance(value, Mapping)
            or value.get("schema") != "adaos.cbs.benchmark_telemetry.v1"
        ):
            continue
        record = CBSBenchmarkTelemetry.from_mapping(value)
        if record.digest not in seen:
            records.append(record)
            seen.add(record.digest)
    return records


def build_benchmark_report(
    cases: Iterable[Mapping[str, Any]], observations: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Compare matched arms only and expose every missing datum explicitly."""

    case_values = sorted(
        (dict(case) for case in cases), key=lambda item: str(item["case_ref"])
    )
    by_case: dict[str, dict[str, Mapping[str, Any]]] = {}
    for observation in observations:
        value = dict(observation)
        key = str(value.get("case_ref") or "")
        variant = str(value.get("variant") or "")
        if variant in by_case.setdefault(key, {}):
            raise CBSBenchmarkError(f"duplicate {variant} observation for {key}")
        by_case[key][variant] = value

    results: list[dict[str, Any]] = []
    for case in case_values:
        arms = by_case.get(str(case["case_ref"]), {})
        required = list(case.get("required_variants") or ("legacy", "cbs"))
        missing_arms = [variant for variant in required if variant not in arms]
        metrics: dict[str, Any] = {}
        for metric, direction in _METRICS.items():
            values = {
                variant: (arms.get(variant, {}).get("metrics") or {}).get(metric)
                for variant in required
            }
            absent = [variant for variant, value in values.items() if value is None]
            comparison: dict[str, Any] = {
                "direction": direction,
                "values": values,
                "status": "missing" if absent else "observed",
            }
            if absent:
                comparison["missing_variants"] = absent
                comparison["delta_cbs_minus_legacy"] = None
                comparison["improved"] = None
            else:
                delta = values["cbs"] - values["legacy"]
                comparison["delta_cbs_minus_legacy"] = delta
                comparison["improved"] = (
                    delta > 0 if direction == "higher" else delta < 0
                )
            metrics[metric] = comparison
        results.append(
            {
                "case_ref": case["case_ref"],
                "case_digest": case["case_digest"],
                "cohort": case["cohort"],
                "matched": not missing_arms,
                "missing_variants": missing_arms,
                "observations": {
                    variant: arms[variant].get("observation_digest")
                    for variant in sorted(arms)
                },
                "metrics": metrics,
            }
        )
    matched = [item for item in results if item["matched"]]
    matched_refs = {str(item["case_ref"]) for item in matched}
    matched_cases = [
        case for case in case_values if str(case["case_ref"]) in matched_refs
    ]
    maturity_distribution: dict[str, dict[str, int]] = {
        "legacy": {},
        "cbs": {},
    }
    for case in matched_cases:
        for variant in ("legacy", "cbs"):
            treatment = (
                by_case.get(str(case["case_ref"]), {})
                .get(variant, {})
                .get("treatment")
                or {}
            )
            for maturity, count in (treatment.get("inventory_maturity") or {}).items():
                maturity_distribution[variant][str(maturity)] = (
                    maturity_distribution[variant].get(str(maturity), 0) + int(count)
                )
    ordered = sorted(
        matched_cases,
        key=lambda item: (int(item.get("sequence_index") or 0), str(item["case_ref"])),
    )
    series: list[dict[str, Any]] = []
    for case in ordered:
        arms = by_case[str(case["case_ref"])]
        series.append(
            {
                "case_ref": case["case_ref"],
                "sequence_index": case.get("sequence_index"),
                "inventory_size": case.get("inventory_size"),
                "legacy": (arms["legacy"].get("metrics") or {}).get("marginal_cost"),
                "cbs": (arms["cbs"].get("metrics") or {}).get("marginal_cost"),
                "legacy_regressions": (arms["legacy"].get("metrics") or {}).get(
                    "regression_count"
                ),
                "cbs_regressions": (arms["cbs"].get("metrics") or {}).get(
                    "regression_count"
                ),
            }
        )
    complete_series = [
        item
        for item in series
        if all(
            isinstance(item[field], (int, float))
            for field in (
                "legacy",
                "cbs",
                "legacy_regressions",
                "cbs_regressions",
            )
        )
    ]
    claim_status = "insufficient_data"
    claim_reasons: list[str] = []
    if len(complete_series) >= 2:
        first = complete_series[0]
        last = complete_series[-1]
        declining = last["cbs"] < first["cbs"]
        no_regression_increase = sum(item["cbs_regressions"] for item in complete_series) <= sum(
            item["legacy_regressions"] for item in complete_series
        )
        beats_legacy_last = last["cbs"] < last["legacy"]
        if declining and no_regression_increase and beats_legacy_last:
            claim_status = "supported_bounded"
        else:
            claim_status = "not_supported"
        if not declining:
            claim_reasons.append("CBS marginal cost did not decline across the frozen sequence")
        if not beats_legacy_last:
            claim_reasons.append("CBS did not beat the matched legacy arm on the final case")
        if not no_regression_increase:
            claim_reasons.append("CBS increased the matched regression count")
    else:
        claim_reasons.append("at least two complete matched sequence points are required")
    body = {
        "schema": "adaos.cbs.benchmark_report.v1",
        "case_count": len(results),
        "matched_case_count": len(matched),
        "representative_matched": sum(
            item["matched"] and item["cohort"] == "representative" for item in results
        ),
        "heldout_matched": sum(
            item["matched"] and item["cohort"] == "heldout" for item in results
        ),
        "causal_claim_admissible": bool(matched) and len(matched) == len(results),
        "maturity_distribution": maturity_distribution,
        "marginal_cost_series": series,
        "primary_claim": {
            "statement": (
                "marginal cost declines as verified reusable inventory grows without "
                "increasing semantic or E2E regression rate"
            ),
            "status": claim_status,
            "reasons": claim_reasons,
        },
        "cases": results,
    }
    return {**body, "report_digest": _digest(body)}


__all__ = [
    "CBSBenchmarkError",
    "build_benchmark_report",
    "create_benchmark_observation",
    "freeze_benchmark_case",
    "load_cbs_telemetry",
    "observation_from_cbs_telemetry",
]
