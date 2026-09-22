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
    "invariant_pass_rate": "higher",
}


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
) -> dict[str, Any]:
    """Freeze the controls required for a meaningful legacy/CBS pair."""

    if cohort not in {"representative", "heldout"}:
        raise CBSBenchmarkError("cohort must be representative or heldout")
    if requirement_total < 1:
        raise CBSBenchmarkError("benchmark cases require at least one requirement")
    body = {
        "schema": "adaos.cbs.benchmark_case.v1",
        "case_ref": str(case_ref),
        "title": str(title),
        "cohort": cohort,
        "workload_digest": str(workload_digest),
        "environment_profile_digest": str(environment_profile_digest),
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
        "source_digests": sorted({str(item) for item in source_digests}),
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
