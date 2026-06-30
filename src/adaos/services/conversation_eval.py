from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from adaos.services import conversation_store


EVAL_SCHEMA = "adaos.conversation.eval.result.v1"
GOLDEN_DATASET_SCHEMA = "adaos.conversation.golden_dataset.v1"
MIGRATION_GATE_SCHEMA = "adaos.conversation.eval.migration_gate.v1"
EVAL_REPAIR_SUMMARY_SCHEMA = "adaos.conversation.eval.repair_summary.v1"
DEFAULT_REQUIRED_GOLDEN_DATASET_IDS = (
    "general_no_match_repair",
    "conversation_companions_agent_handoff",
    "builder_review_handoff",
    "builder_first_idea_preview_correction",
    "teacher_candidate_repair",
)


def collect_metrics(
    *,
    conversation_id: str,
    thread_id: str | None = None,
    messages: Sequence[Mapping[str, Any]] | None = None,
    traces: Sequence[Mapping[str, Any]] | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")
    stored_messages = (
        [dict(item) for item in messages]
        if messages is not None
        else conversation_store.list_messages(cid, thread_id=thread_id, limit=limit)
    )
    stored_traces = (
        [dict(item) for item in traces]
        if traces is not None
        else conversation_store.list_turn_traces(conversation_id=cid, limit=limit)
    )

    role_counts: dict[str, int] = {}
    agent_ids: set[str] = set()
    channel_ids: set[str] = set()
    for message in stored_messages:
        role = str(message.get("from") or message.get("role") or "unknown").strip() or "unknown"
        role_counts[role] = role_counts.get(role, 0) + 1
        agent_id = str(message.get("active_agent_id") or message.get("actor_id") or "").strip()
        if agent_id:
            agent_ids.add(agent_id)
        channel_id = str(message.get("dialog_channel_id") or message.get("channel_id") or "").strip()
        if channel_id:
            channel_ids.add(channel_id)

    trace_status_counts: dict[str, int] = {}
    fallback_count = 0
    repair_count = 0
    no_match_count = 0
    latencies: list[float] = []
    for trace in stored_traces:
        status = str(trace.get("status") or "unknown").strip() or "unknown"
        trace_status_counts[status] = trace_status_counts.get(status, 0) + 1
        policy = trace.get("policy_decision") if isinstance(trace.get("policy_decision"), Mapping) else {}
        if _is_fallback_trace(trace, policy):
            fallback_count += 1
        if _is_repair_trace(policy):
            repair_count += 1
        if _is_no_match_trace(trace, policy):
            no_match_count += 1
        created = _float_or_none(trace.get("created_at"))
        completed = _float_or_none(trace.get("completed_at"))
        if created is not None and completed is not None and completed >= created:
            latencies.append((completed - created) * 1000.0)
        agent_id = str(trace.get("agent_id") or policy.get("selected_agent") or policy.get("selected_agent_id") or "").strip()
        if agent_id:
            agent_ids.add(agent_id)
        channel_id = str(trace.get("channel_id") or policy.get("selected_channel") or policy.get("dialog_channel_id") or "").strip()
        if channel_id:
            channel_ids.add(channel_id)

    trace_count = len(stored_traces)
    completed_count = sum(
        count
        for status, count in trace_status_counts.items()
        if status in {"completed", "routed", "materialized", "ok"}
    )
    success_rate = completed_count / trace_count if trace_count else 1.0
    fallback_rate = fallback_count / trace_count if trace_count else 0.0
    repair_rate = repair_count / trace_count if trace_count else 0.0
    return {
        "schema": "adaos.conversation.eval.metrics.v1",
        "conversation_id": cid,
        "thread_id": str(thread_id or "").strip() or None,
        "message_count": len(stored_messages),
        "turn_count": role_counts.get("user", 0),
        "assistant_message_count": role_counts.get("hub", 0) + role_counts.get("assistant", 0),
        "role_counts": role_counts,
        "trace_count": trace_count,
        "trace_status_counts": trace_status_counts,
        "success_rate": round(success_rate, 6),
        "fallback_count": fallback_count,
        "fallback_rate": round(fallback_rate, 6),
        "repair_count": repair_count,
        "repair_rate": round(repair_rate, 6),
        "no_match_count": no_match_count,
        "latency_ms": _latency_summary(latencies),
        "agent_ids": sorted(agent_ids),
        "channel_ids": sorted(channel_ids),
    }


def evaluate_golden_conversation(
    *,
    conversation_id: str,
    expectations: Mapping[str, Any],
    thread_id: str | None = None,
    messages: Sequence[Mapping[str, Any]] | None = None,
    traces: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")
    stored_messages = (
        [dict(item) for item in messages]
        if messages is not None
        else conversation_store.list_messages(cid, thread_id=thread_id, limit=5000)
    )
    stored_traces = (
        [dict(item) for item in traces]
        if traces is not None
        else conversation_store.list_turn_traces(conversation_id=cid, limit=5000)
    )
    metrics = collect_metrics(
        conversation_id=cid,
        thread_id=thread_id,
        messages=stored_messages,
        traces=stored_traces,
    )
    checks: list[dict[str, Any]] = []
    text_blob = "\n".join(str(item.get("text") or "") for item in stored_messages)

    for phrase in _string_list(expectations.get("required_text")):
        _add_check(
            checks,
            name="required_text",
            passed=phrase in text_blob,
            details={"phrase": phrase},
        )
    for phrase in _string_list(expectations.get("forbidden_text")):
        _add_check(
            checks,
            name="forbidden_text",
            passed=phrase not in text_blob,
            details={"phrase": phrase},
        )
    for agent_id in _string_list(expectations.get("required_agents")):
        _add_check(
            checks,
            name="required_agent",
            passed=agent_id in set(metrics["agent_ids"]),
            details={"agent_id": agent_id},
        )
    for channel_id in _string_list(expectations.get("required_channels")):
        _add_check(
            checks,
            name="required_channel",
            passed=channel_id in set(metrics["channel_ids"]),
            details={"channel_id": channel_id},
        )

    if "min_success_rate" in expectations:
        threshold = _float_or_none(expectations.get("min_success_rate"))
        if threshold is not None:
            _add_check(
                checks,
                name="min_success_rate",
                passed=float(metrics["success_rate"]) >= threshold,
                details={"actual": metrics["success_rate"], "threshold": threshold},
            )
    if "max_fallback_rate" in expectations:
        threshold = _float_or_none(expectations.get("max_fallback_rate"))
        if threshold is not None:
            _add_check(
                checks,
                name="max_fallback_rate",
                passed=float(metrics["fallback_rate"]) <= threshold,
                details={"actual": metrics["fallback_rate"], "threshold": threshold},
            )
    if "max_latency_ms_p95" in expectations:
        threshold = _float_or_none(expectations.get("max_latency_ms_p95"))
        actual = _float_or_none(metrics.get("latency_ms", {}).get("p95") if isinstance(metrics.get("latency_ms"), Mapping) else None)
        if threshold is not None and actual is not None:
            _add_check(
                checks,
                name="max_latency_ms_p95",
                passed=actual <= threshold,
                details={"actual": actual, "threshold": threshold},
            )

    failures = [check for check in checks if not check.get("passed")]
    return {
        "schema": EVAL_SCHEMA,
        "conversation_id": cid,
        "thread_id": str(thread_id or "").strip() or None,
        "status": "passed" if not failures else "failed",
        "metrics": metrics,
        "checks": checks,
        "failures": failures,
        "evidence_refs": _evidence_refs(stored_messages, stored_traces),
    }


def load_golden_dataset(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    dataset = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(dataset, Mapping):
        raise ValueError(f"golden dataset must be an object: {source}")
    if dataset.get("schema_version") != GOLDEN_DATASET_SCHEMA:
        raise ValueError(
            f"unsupported golden dataset schema for {source}: {dataset.get('schema_version')!r}"
        )
    missing = [
        key
        for key in ("id", "conversation_id", "messages", "turn_traces", "expectations")
        if key not in dataset
    ]
    if missing:
        raise ValueError(f"golden dataset {source} is missing required keys: {', '.join(missing)}")
    return dict(dataset)


def evaluate_golden_dataset(dataset: Mapping[str, Any]) -> dict[str, Any]:
    dataset_id = str(dataset.get("id") or "").strip()
    if not dataset_id:
        raise ValueError("golden dataset id is required")
    result = evaluate_golden_conversation(
        conversation_id=str(dataset.get("conversation_id") or ""),
        messages=_mapping_list(dataset.get("messages")),
        traces=_mapping_list(dataset.get("turn_traces")),
        expectations=dataset.get("expectations") if isinstance(dataset.get("expectations"), Mapping) else {},
    )
    result["dataset_id"] = dataset_id
    description = str(dataset.get("description") or "").strip()
    if description:
        result["description"] = description
    return result


def run_golden_migration_gate(
    *,
    fixture_dir: str | Path | None = None,
    fixture_paths: Sequence[str | Path] | None = None,
    required_dataset_ids: Sequence[str] | None = DEFAULT_REQUIRED_GOLDEN_DATASET_IDS,
) -> dict[str, Any]:
    paths = _golden_fixture_paths(fixture_dir=fixture_dir, fixture_paths=fixture_paths)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for path in paths:
        source = Path(path)
        try:
            dataset = load_golden_dataset(source)
            dataset_id = str(dataset.get("id") or source.stem)
            seen_ids.add(dataset_id)
            result = evaluate_golden_dataset(dataset)
            result["source_path"] = str(source)
        except Exception as exc:
            dataset_id = source.stem
            seen_ids.add(dataset_id)
            result = {
                "schema": EVAL_SCHEMA,
                "dataset_id": dataset_id,
                "source_path": str(source),
                "status": "failed",
                "failures": [
                    {
                        "name": "fixture_load",
                        "passed": False,
                        "details": {"error": str(exc)},
                    }
                ],
            }
        results.append(result)
        if result.get("status") != "passed":
            failures.append(
                {
                    "dataset_id": str(result.get("dataset_id") or dataset_id),
                    "source_path": str(result.get("source_path") or source),
                    "failures": list(result.get("failures") or []),
                }
            )

    required = {str(item).strip() for item in required_dataset_ids or [] if str(item or "").strip()}
    for dataset_id in sorted(required - seen_ids):
        failures.append(
            {
                "dataset_id": dataset_id,
                "source_path": None,
                "failures": [
                    {
                        "name": "required_dataset",
                        "passed": False,
                        "details": {"dataset_id": dataset_id, "reason": "missing"},
                    }
                ],
            }
        )

    passed_count = sum(1 for item in results if item.get("status") == "passed")
    status = "passed" if results and not failures else "failed"
    return {
        "schema": MIGRATION_GATE_SCHEMA,
        "status": status,
        "fixture_count": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count + len(required - seen_ids),
        "required_dataset_ids": sorted(required),
        "datasets": results,
        "failures": failures,
    }


def eval_repair_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    schema = str(result.get("schema") or "").strip()
    if schema == MIGRATION_GATE_SCHEMA:
        failures = _mapping_list(result.get("failures"))
        dataset_refs = [_dataset_failure_ref(item) for item in failures]
        failed_count = int(result.get("failed_count") or len(dataset_refs))
        return {
            "schema": EVAL_REPAIR_SUMMARY_SCHEMA,
            "source_schema": MIGRATION_GATE_SCHEMA,
            "status": str(result.get("status") or "unknown"),
            "fixture_count": int(result.get("fixture_count") or 0),
            "passed_count": int(result.get("passed_count") or 0),
            "failed_count": failed_count,
            "dataset_refs": dataset_refs,
            "source_refs": _gate_source_refs(result),
        }
    if schema == EVAL_SCHEMA:
        failures = _mapping_list(result.get("failures"))
        return {
            "schema": EVAL_REPAIR_SUMMARY_SCHEMA,
            "source_schema": EVAL_SCHEMA,
            "status": str(result.get("status") or "unknown"),
            "conversation_id": str(result.get("conversation_id") or ""),
            "thread_id": str(result.get("thread_id") or "") or None,
            "failed_count": len(failures),
            "dataset_refs": [
                {
                    "dataset_id": str(result.get("dataset_id") or ""),
                    "conversation_id": str(result.get("conversation_id") or ""),
                    "thread_id": str(result.get("thread_id") or "") or None,
                    "failure_names": [str(item.get("name") or "") for item in failures if isinstance(item, Mapping)],
                }
            ],
            "source_refs": _eval_source_refs(result),
        }
    raise ValueError(f"unsupported eval result schema: {schema!r}")


def publish_eval_repair_pending_action(
    result: Mapping[str, Any],
    *,
    webspace_id: str = "default",
    action_id: str | None = None,
) -> dict[str, Any]:
    summary = eval_repair_summary(result)
    if str(summary.get("status") or "").lower() == "passed" or int(summary.get("failed_count") or 0) <= 0:
        return {
            "ok": True,
            "published": False,
            "reason": "eval_passed",
            "summary": summary,
        }

    from adaos.services import pending_actions

    risk = _eval_repair_action_risk(summary)
    failed_count = int(summary.get("failed_count") or 0)
    title = "Review conversation eval failures"
    text_summary = f"{failed_count} conversation evaluation failure(s) need Builder repair triage."
    action = pending_actions.publish_pending_action(
        webspace_id=webspace_id,
        action_id=action_id,
        kind="builder.eval_repair.review",
        title=title,
        summary=text_summary,
        request_text=text_summary,
        producer={"type": "system", "system_id": "conversation_eval"},
        owner_scope={"webspace_id": webspace_id, "owner": "skill:builder_skill"},
        domain_ref={
            "schema": "adaos.builder.eval_repair_ref.v1",
            "source_schema": summary.get("source_schema"),
            "failed_count": failed_count,
            "dataset_ids": [
                str(item.get("dataset_id") or "")
                for item in summary.get("dataset_refs", [])
                if isinstance(item, Mapping) and str(item.get("dataset_id") or "")
            ],
        },
        allowed_actions=[
            {"id": "preview", "label": "Preview Evidence", "terminal": False},
            {"id": "create_repair_tasks", "label": "Create Repair Tasks", "terminal": True},
            {"id": "postpone", "label": "Later", "terminal": False},
            {"id": "refuse", "label": "Dismiss", "terminal": True},
        ],
        default_text_binding=False,
        response_topic="builder.eval_repair.response",
        priority=80,
        metadata={
            "schema": "adaos.builder.eval_repair.pending_action_metadata.v1",
            "eval_summary": summary,
            "source_refs": summary.get("source_refs", []),
            "approval_policy": {
                "action_risk": risk,
                "requires_human_review": True,
                "reason": "repair tasks may generate or apply runtime changes",
            },
        },
    )
    return {"ok": True, "published": True, "pending_action": action, "summary": summary}


def _is_fallback_trace(trace: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    haystack = " ".join(
        str(value or "").lower()
        for value in (
            trace.get("selected_tool"),
            trace.get("summary"),
            policy.get("reason"),
            policy.get("dialog_policy_reason"),
            policy.get("fallback"),
            policy.get("result_status"),
            policy.get("diagnostic"),
        )
    )
    return "fallback" in haystack or "low_confidence" in haystack or "not_obtained" in haystack


def _is_no_match_trace(trace: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    haystack = " ".join(
        str(value or "").lower()
        for value in (
            trace.get("selected_tool"),
            trace.get("summary"),
            policy.get("reason"),
            policy.get("diagnostic"),
        )
    )
    return "no_match" in haystack or "not_obtained" in haystack or "low_confidence" in haystack


def _is_repair_trace(policy: Mapping[str, Any]) -> bool:
    repair = str(policy.get("dialog_repair_state") or policy.get("repair_state") or "").strip().lower()
    return bool(repair and repair != "none")


def _latency_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None}
    ordered = sorted(float(item) for item in values)
    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "median": round(float(median(ordered)), 3),
        "p95": round(_percentile(ordered, 0.95), 3),
        "max": round(ordered[-1], 3),
    }


def _percentile(ordered: Sequence[float], fraction: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    index = (len(ordered) - 1) * max(0.0, min(1.0, fraction))
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return float(ordered[lower]) * (1.0 - weight) + float(ordered[upper]) * weight


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence):
        return [str(item) for item in value if str(item or "")]
    return []


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _dataset_failure_ref(value: Mapping[str, Any]) -> dict[str, Any]:
    failures = _mapping_list(value.get("failures"))
    return {
        "dataset_id": str(value.get("dataset_id") or ""),
        "source_path": str(value.get("source_path") or "") or None,
        "failure_names": [str(item.get("name") or "") for item in failures if str(item.get("name") or "")],
        "failures": [dict(item) for item in failures[:10]],
    }


def _gate_source_refs(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    datasets = _mapping_list(result.get("datasets"))
    for dataset in datasets:
        if str(dataset.get("status") or "") == "passed":
            continue
        refs.append(
            {
                "type": "conversation_eval_dataset",
                "dataset_id": str(dataset.get("dataset_id") or ""),
                "source_path": str(dataset.get("source_path") or "") or None,
                "conversation_id": str(dataset.get("conversation_id") or "") or None,
                "thread_id": str(dataset.get("thread_id") or "") or None,
                "evidence_refs": _mapping_list(dataset.get("evidence_refs"))[:20],
            }
        )
    seen = {str(item.get("dataset_id") or "") for item in refs}
    for failure in _mapping_list(result.get("failures")):
        dataset_id = str(failure.get("dataset_id") or "")
        if dataset_id in seen:
            continue
        refs.append(
            {
                "type": "conversation_eval_dataset",
                "dataset_id": dataset_id,
                "source_path": str(failure.get("source_path") or "") or None,
                "evidence_refs": [],
            }
        )
    return refs


def _eval_source_refs(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = [
        {
            "type": "conversation_eval_result",
            "dataset_id": str(result.get("dataset_id") or "") or None,
            "conversation_id": str(result.get("conversation_id") or "") or None,
            "thread_id": str(result.get("thread_id") or "") or None,
        }
    ]
    refs.extend(_mapping_list(result.get("evidence_refs"))[:20])
    return refs


def _eval_repair_action_risk(summary: Mapping[str, Any]) -> dict[str, Any]:
    try:
        from adaos.services.conversation_safety import classify_action_risk

        result = classify_action_risk(
            {
                "tool": "builder.eval_repair.create_tasks",
                "side_effect_class": "local_write",
                "target": "Builder repair tasks from conversation evaluation failures",
                "source_refs": summary.get("source_refs", []),
            }
        )
        return dict(result) if isinstance(result, Mapping) else {"risk_class": "local_write"}
    except Exception:
        return {
            "schema": "adaos.conversation.action_risk.v1",
            "risk_class": "local_write",
            "requires_approval": True,
            "reason": "fallback risk for eval repair task creation",
        }


def _golden_fixture_paths(
    *,
    fixture_dir: str | Path | None,
    fixture_paths: Sequence[str | Path] | None,
) -> list[Path]:
    if fixture_paths is not None:
        return [Path(item) for item in fixture_paths]
    root = Path(fixture_dir) if fixture_dir is not None else _default_golden_fixture_dir()
    if not root.exists():
        raise FileNotFoundError(f"golden fixture directory does not exist: {root}")
    return sorted(root.glob("*.json"))


def _default_golden_fixture_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "conversation"


def _add_check(checks: list[dict[str, Any]], *, name: str, passed: bool, details: Mapping[str, Any]) -> None:
    checks.append({"name": name, "passed": bool(passed), "details": dict(details)})


def _evidence_refs(messages: Sequence[Mapping[str, Any]], traces: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for message in messages[-20:]:
        refs.append(
            {
                "type": "conversation_message",
                "conversation_id": str(message.get("conversation_id") or ""),
                "message_id": str(message.get("id") or message.get("message_id") or ""),
                "seq": int(message.get("seq") or 0),
            }
        )
    for trace in traces[-20:]:
        refs.append(
            {
                "type": "turn_trace",
                "conversation_id": str(trace.get("conversation_id") or ""),
                "turn_trace_id": str(trace.get("turn_trace_id") or ""),
                "status": str(trace.get("status") or ""),
            }
        )
    return refs
