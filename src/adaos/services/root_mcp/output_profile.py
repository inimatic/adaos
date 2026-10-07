from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from .model import RootMcpToolContract


DEFAULT_REVIEW_CHARS = 16_000
DEFAULT_OPTIMIZE_CHARS = 64_000
CONTEXT_PRESSURE_THRESHOLDS = {
    "observe": 0.70,
    "deterministic_trim": 0.85,
    "model_compact": 0.92,
    "reserve": 0.97,
}

def measure_output(
    value: Any,
    *,
    review_chars: int = DEFAULT_REVIEW_CHARS,
    optimize_chars: int = DEFAULT_OPTIMIZE_CHARS,
) -> dict[str, Any]:
    """Measure the canonical compact JSON representation of one MCP result."""

    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    encoded = text.encode("utf-8")
    chars = len(text)
    review_limit = max(1, int(review_chars))
    optimize_limit = max(review_limit, int(optimize_chars))
    if chars > optimize_limit:
        signal = "optimize"
    elif chars > review_limit:
        signal = "review"
    else:
        signal = "ok"
    return {
        "serialized_chars": chars,
        "serialized_bytes": len(encoded),
        "estimated_tokens": max(1, math.ceil(len(encoded) / 4)),
        "output_digest": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        "optimization_signal": signal,
    }


def profile_audit_events(
    events: Iterable[Mapping[str, Any]],
    *,
    top_n: int = 10,
) -> dict[str, Any]:
    """Aggregate measured Root MCP audit rows without retaining response bodies."""

    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "measured_calls": 0,
            "total_chars": 0,
            "max_chars": 0,
            "max_bytes": 0,
            "latest_chars": None,
            "latest_bytes": None,
            "latest_signal": None,
            "signals": {"ok": 0, "review": 0, "optimize": 0},
            "characters": [],
            "tokens": [],
            "latencies_ms": [],
            "cache_hits": 0,
            "cache_observations": 0,
        }
    )
    event_count = 0
    measured_event_count = 0
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_count += 1
        tool_id = str(event.get("tool_id") or "unknown").strip() or "unknown"
        row = groups[tool_id]
        row["calls"] += 1
        summary = event.get("result_summary")
        if not isinstance(summary, Mapping):
            continue
        chars = summary.get("serialized_chars")
        output_bytes = summary.get("serialized_bytes")
        if not isinstance(chars, int) or chars < 0:
            continue
        measured_event_count += 1
        row["measured_calls"] += 1
        if row["latest_chars"] is None:
            # Root MCP audit reads are newest-first. Preserve the most recent
            # measurement while retaining the historical maximum separately.
            row["latest_chars"] = chars
            row["latest_bytes"] = output_bytes if isinstance(output_bytes, int) else None
            row["latest_signal"] = str(
                summary.get("optimization_signal") or "ok"
            ).strip().lower()
        row["total_chars"] += chars
        row["characters"].append(chars)
        tokens = summary.get("estimated_tokens")
        if isinstance(tokens, int) and tokens >= 0:
            row["tokens"].append(tokens)
        latency_ms = summary.get("latency_ms")
        if isinstance(latency_ms, (int, float)) and float(latency_ms) >= 0:
            row["latencies_ms"].append(float(latency_ms))
        cache_hit = summary.get("cache_hit")
        if isinstance(cache_hit, bool):
            row["cache_observations"] += 1
            if cache_hit:
                row["cache_hits"] += 1
        row["max_chars"] = max(int(row["max_chars"]), chars)
        if isinstance(output_bytes, int):
            row["max_bytes"] = max(int(row["max_bytes"]), output_bytes)
        signal = str(summary.get("optimization_signal") or "ok").strip().lower()
        if signal not in row["signals"]:
            signal = "ok"
        row["signals"][signal] += 1

    tools: list[dict[str, Any]] = []

    def percentile(values: Sequence[int | float], fraction: float) -> int | float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
        value = ordered[index]
        return round(float(value), 1) if isinstance(value, float) else int(value)

    for tool_id, raw in groups.items():
        measured = int(raw["measured_calls"])
        tools.append(
            {
                "tool_id": tool_id,
                "calls": int(raw["calls"]),
                "measured_calls": measured,
                "max_chars": int(raw["max_chars"]),
                "max_bytes": int(raw["max_bytes"]),
                "latest_chars": raw["latest_chars"],
                "latest_bytes": raw["latest_bytes"],
                "latest_signal": raw["latest_signal"],
                "average_chars": (
                    round(int(raw["total_chars"]) / measured, 1) if measured else None
                ),
                "p50_chars": percentile(raw["characters"], 0.50),
                "p95_chars": percentile(raw["characters"], 0.95),
                "p50_tokens": percentile(raw["tokens"], 0.50),
                "p95_tokens": percentile(raw["tokens"], 0.95),
                "p50_latency_ms": percentile(raw["latencies_ms"], 0.50),
                "p95_latency_ms": percentile(raw["latencies_ms"], 0.95),
                "cache_hit_rate": (
                    round(int(raw["cache_hits"]) / int(raw["cache_observations"]), 4)
                    if int(raw["cache_observations"])
                    else None
                ),
                "recommended_budget_chars": percentile(raw["characters"], 0.95),
                "recommended_budget_tokens": percentile(raw["tokens"], 0.95),
                "signals": dict(raw["signals"]),
            }
        )
    tools.sort(key=lambda item: (-int(item["max_chars"]), item["tool_id"]))
    bounded_top = max(1, min(int(top_n), 100))
    return {
        "schema": "adaos.mcp.output_profile.v1",
        "event_count": event_count,
        "measured_event_count": measured_event_count,
        "tool_count": len(tools),
        "top": tools[:bounded_top],
    }


def context_pressure_stage(*, used_tokens: int, capacity_tokens: int) -> dict[str, Any]:
    """Return the deterministic context-margin stage for one request."""

    capacity = max(1, int(capacity_tokens))
    used = max(0, int(used_tokens))
    ratio = used / capacity
    if ratio >= CONTEXT_PRESSURE_THRESHOLDS["reserve"]:
        stage = "reserve"
        actions = ["deny_new_tool_call", "preserve_reasoning_result_recovery_reserve"]
    elif ratio >= CONTEXT_PRESSURE_THRESHOLDS["model_compact"]:
        stage = "model_compact"
        actions = ["prove_algorithmic_trim_exhausted", "admit_evaluated_model_compaction"]
    elif ratio >= CONTEXT_PRESSURE_THRESHOLDS["deterministic_trim"]:
        stage = "deterministic_trim"
        actions = ["paginate", "apply_field_masks", "drop_stale_results", "dedupe_by_digest"]
    elif ratio >= CONTEXT_PRESSURE_THRESHOLDS["observe"]:
        stage = "observe"
        actions = ["measure_source_contribution", "identify_p95_offenders"]
    else:
        stage = "normal"
        actions = []
    return {
        "schema": "adaos.context.pressure.v1",
        "stage": stage,
        "used_tokens": used,
        "capacity_tokens": capacity,
        "utilization": round(ratio, 6),
        "thresholds": dict(CONTEXT_PRESSURE_THRESHOLDS),
        "actions": actions,
    }


def evaluate_must_keep_admission(
    *,
    source_id: str,
    required_obligations: Sequence[str],
    retained_obligations: Sequence[str],
    estimated_tokens: int,
    used_tokens: int,
    capacity_tokens: int,
) -> dict[str, Any]:
    """Admit a model-facing slice only when its security closure remains intact.

    Deterministic trimming may remove optional detail, but it cannot silently
    remove a must-keep obligation.  The 97% reserve is also a hard boundary: a
    closure that would cross it is deferred to a digest-bound continuation.
    """

    capacity = max(1, int(capacity_tokens))
    used = max(0, int(used_tokens))
    estimated = max(0, int(estimated_tokens))
    pressure = context_pressure_stage(
        used_tokens=used,
        capacity_tokens=capacity,
    )
    required = sorted({str(item).strip() for item in required_obligations if str(item).strip()})
    retained = sorted({str(item).strip() for item in retained_obligations if str(item).strip()})
    missing = sorted(set(required) - set(retained))
    reserve_boundary = int(capacity * CONTEXT_PRESSURE_THRESHOLDS["reserve"])
    available_before_reserve = max(0, reserve_boundary - used)
    if missing:
        admitted = False
        reason = "must_keep_obligation_missing"
    elif pressure["stage"] == "reserve":
        admitted = False
        reason = "reasoning_result_recovery_reserve_active"
    elif estimated > available_before_reserve:
        admitted = False
        reason = "must_keep_closure_cannot_fit"
    else:
        admitted = True
        reason = "must_keep_closure_admitted"
    closure_payload = json.dumps(
        required,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema": "adaos.context.must_keep_admission.v1",
        "source_id": str(source_id or "unknown"),
        "pressure": pressure,
        "required": required,
        "retained": retained,
        "missing": missing,
        "required_digest": "sha256:" + hashlib.sha256(closure_payload).hexdigest(),
        "estimated_tokens": estimated,
        "available_before_reserve_tokens": available_before_reserve,
        "admitted": admitted,
        "reason": reason,
        "continuation_required": not admitted,
    }


def evaluate_source_budget_change(
    *,
    source_id: str,
    current_budget: int,
    observed_p95: int | None,
    baseline_quality: float,
    candidate_quality: float,
    quality_floor: float,
    minimum_quality_gain: float = 0.005,
    maximum_quality_regression: float = 0.0,
) -> dict[str, Any]:
    """Gate a p95-derived source budget change on independent eval quality.

    Raising a budget needs a measurable quality gain.  Lowering it needs proof
    that the quality floor is preserved without an unapproved regression.
    """

    current = max(1, int(current_budget))
    target = max(1, int(observed_p95)) if observed_p95 is not None else None
    baseline = float(baseline_quality)
    candidate = float(candidate_quality)
    floor = float(quality_floor)
    if target is None:
        direction = "unknown"
        approved = False
        reason = "p95_measurement_required"
    elif target == current:
        direction = "unchanged"
        approved = candidate >= floor
        reason = "quality_floor_met" if approved else "quality_floor_failed"
    elif target > current:
        direction = "increase"
        approved = candidate >= floor and candidate - baseline >= float(minimum_quality_gain)
        reason = "measurable_quality_gain" if approved else "quality_gain_not_proven"
    else:
        direction = "decrease"
        approved = (
            candidate >= floor
            and baseline - candidate <= max(0.0, float(maximum_quality_regression))
        )
        reason = "quality_floor_preserved" if approved else "quality_regression_not_approved"
    return {
        "schema": "adaos.context.source_budget_gate.v1",
        "source_id": str(source_id or "unknown"),
        "basis": "observed_p95",
        "current_budget": current,
        "candidate_budget": target,
        "direction": direction,
        "quality": {
            "baseline": baseline,
            "candidate": candidate,
            "floor": floor,
            "minimum_gain": float(minimum_quality_gain),
            "maximum_regression": max(0.0, float(maximum_quality_regression)),
        },
        "approved": approved,
        "effective_budget": target if approved and target is not None else current,
        "reason": reason,
    }


def evaluate_release_slo(
    tools: Sequence[Mapping[str, Any]],
    *,
    p95_chars_max: int,
    p95_tokens_max: int,
    p95_latency_ms_max: float,
    cache_hit_rate_min: float,
    must_keep_recall: Mapping[str, float],
    must_keep_recall_min: float,
) -> dict[str, Any]:
    """Evaluate the independent output/latency/cache/recall release gate."""

    rows: list[dict[str, Any]] = []
    for raw in tools:
        tool_id = str(raw.get("tool_id") or "unknown")
        metrics = {
            "p95_chars": raw.get("p95_chars"),
            "p95_tokens": raw.get("p95_tokens"),
            "p95_latency_ms": raw.get("p95_latency_ms"),
            "cache_hit_rate": raw.get("cache_hit_rate"),
            "must_keep_recall": must_keep_recall.get(tool_id),
        }
        violations: list[str] = []
        for name in ("p95_chars", "p95_tokens", "p95_latency_ms", "cache_hit_rate", "must_keep_recall"):
            if (
                not isinstance(metrics[name], (int, float))
                or isinstance(metrics[name], bool)
                or not math.isfinite(metrics[name])
            ):
                violations.append(f"{name}_missing")
        if isinstance(metrics["p95_chars"], (int, float)) and metrics["p95_chars"] > p95_chars_max:
            violations.append("p95_chars_exceeded")
        if isinstance(metrics["p95_tokens"], (int, float)) and metrics["p95_tokens"] > p95_tokens_max:
            violations.append("p95_tokens_exceeded")
        if isinstance(metrics["p95_latency_ms"], (int, float)) and metrics["p95_latency_ms"] > p95_latency_ms_max:
            violations.append("p95_latency_exceeded")
        if isinstance(metrics["cache_hit_rate"], (int, float)) and metrics["cache_hit_rate"] < cache_hit_rate_min:
            violations.append("cache_hit_rate_below_slo")
        if isinstance(metrics["must_keep_recall"], (int, float)) and metrics["must_keep_recall"] < must_keep_recall_min:
            violations.append("must_keep_recall_below_slo")
        rows.append({"tool_id": tool_id, "metrics": metrics, "violations": violations, "passed": not violations})
    return {
        "schema": "adaos.context.release_slo.v1",
        "passed": bool(rows) and all(row["passed"] for row in rows),
        "tool_count": len(rows),
        "targets": {
            "p95_chars_max": int(p95_chars_max),
            "p95_tokens_max": int(p95_tokens_max),
            "p95_latency_ms_max": float(p95_latency_ms_max),
            "cache_hit_rate_min": float(cache_hit_rate_min),
            "must_keep_recall_min": float(must_keep_recall_min),
        },
        "items": rows,
    }


def context_curator_decision(
    *,
    source_id: str,
    calls_since_last_run: int,
    expected_saved_tokens: int,
    estimated_run_cost_tokens: int,
    algorithmic_recipe: Mapping[str, Any] | None,
    eval_passed: bool,
) -> dict[str, Any]:
    """Admit the Curator as a rare learning/control-plane process.

    It does not compress each prompt.  It diagnoses an offender, evaluates an
    algorithmic recipe, and only then proposes a durable source-contract patch.
    """

    calls = max(0, int(calls_since_last_run))
    saved = max(0, int(expected_saved_tokens))
    cost = max(1, int(estimated_run_cost_tokens))
    roi = saved / cost
    rare_interval_met = calls >= 20
    exceptional_roi = roi >= 4.0
    admitted = bool(algorithmic_recipe) and (rare_interval_met or exceptional_roi)
    adopt = admitted and bool(eval_passed)
    return {
        "schema": "adaos.context.curator_decision.v1",
        "source_id": str(source_id or "unknown"),
        "mode": "control_plane_learning",
        "calls_since_last_run": calls,
        "expected_saved_tokens": saved,
        "estimated_run_cost_tokens": cost,
        "expected_roi": round(roi, 4),
        "admitted": admitted,
        "eval_passed": bool(eval_passed),
        "adopt_recipe": adopt,
        "recipe": dict(algorithmic_recipe or {}),
        "contract_patch": dict(algorithmic_recipe or {}) if adopt else None,
        "reason": (
            "recipe_evaluated_and_ready"
            if adopt
            else "eval_gate_failed"
            if admitted
            else "rare_trigger_not_met"
        ),
    }


def audit_search_contracts(
    contracts: Sequence[RootMcpToolContract],
) -> dict[str, Any]:
    """Find collection MCP contracts that cannot bound and continue results.

    Every unbounded collection is blocking. There is deliberately no legacy
    allowlist: age does not make an output safe for Builder context.
    """

    rows: list[dict[str, Any]] = []
    for contract in contracts:
        operation = contract.id.rsplit(".", 1)[-1].lower()
        if not operation.startswith(("list", "search", "query")):
            continue
        schema = contract.input_schema if isinstance(contract.input_schema, Mapping) else {}
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        bound_name = next(
            (name for name in ("limit", "top_k", "page_size") if name in properties),
            None,
        )
        cursor_name = next(
            (
                name
                for name in ("cursor", "page_token", "pagination_token", "offset")
                if name in properties
            ),
            None,
        )
        signals: list[str] = []
        if bound_name is None:
            signals.append("missing_top_k")
        else:
            bound_schema = properties.get(bound_name)
            if not isinstance(bound_schema, Mapping) or not isinstance(
                bound_schema.get("maximum"), int
            ):
                signals.append("unbounded_top_k")
        if cursor_name is None:
            signals.append("missing_pagination")
        rows.append(
            {
                "tool_id": contract.id,
                "bound_argument": bound_name,
                "pagination_argument": cursor_name,
                "signals": signals,
                "legacy_debt": False,
                "blocking": bool(signals),
                "status": "review" if signals else "ok",
            }
        )
    rows.sort(key=lambda item: (item["status"] == "ok", item["tool_id"]))
    return {
        "schema": "adaos.mcp.search_contract_audit.v1",
        "search_tool_count": len(rows),
        "review_count": sum(1 for item in rows if item["signals"]),
        "blocking_count": sum(1 for item in rows if item["blocking"]),
        "legacy_debt_count": sum(1 for item in rows if item["legacy_debt"]),
        "items": rows,
    }


__all__ = [
    "DEFAULT_OPTIMIZE_CHARS",
    "DEFAULT_REVIEW_CHARS",
    "CONTEXT_PRESSURE_THRESHOLDS",
    "audit_search_contracts",
    "context_curator_decision",
    "context_pressure_stage",
    "evaluate_must_keep_admission",
    "evaluate_release_slo",
    "evaluate_source_budget_change",
    "measure_output",
    "profile_audit_events",
]
