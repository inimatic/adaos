from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any, Callable, Mapping, Sequence

from adaos.services import conversation_store


EVAL_SCHEMA = "adaos.conversation.eval.result.v1"
GOLDEN_DATASET_SCHEMA = "adaos.conversation.golden_dataset.v1"
MIGRATION_GATE_SCHEMA = "adaos.conversation.eval.migration_gate.v1"
EVAL_REPAIR_SUMMARY_SCHEMA = "adaos.conversation.eval.repair_summary.v1"
MODEL_GRADE_SCHEMA = "adaos.conversation.eval.model_grade.v1"
MODEL_GRADER_REQUEST_SCHEMA = "adaos.conversation.eval.model_grader_request.v1"
ModelGrader = Callable[[Mapping[str, Any]], Mapping[str, Any]]
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
    context_packets: list[Mapping[str, Any]] = []
    seen_context_packets: set[str] = set()
    for message in stored_messages:
        for packet in _context_packets_from_container(message):
            key = _stable_json_key(packet)
            if key in seen_context_packets:
                continue
            seen_context_packets.add(key)
            context_packets.append(packet)
    for trace in stored_traces:
        status = str(trace.get("status") or "unknown").strip() or "unknown"
        trace_status_counts[status] = trace_status_counts.get(status, 0) + 1
        policy = trace.get("policy_decision") if isinstance(trace.get("policy_decision"), Mapping) else {}
        for packet in _context_packets_from_container(trace):
            key = _stable_json_key(packet)
            if key in seen_context_packets:
                continue
            seen_context_packets.add(key)
            context_packets.append(packet)
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
        "context_budget": _context_budget_summary(context_packets),
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
    model_grader: ModelGrader | None = None,
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
    if "min_repair_rate" in expectations:
        threshold = _float_or_none(expectations.get("min_repair_rate"))
        if threshold is not None:
            _add_check(
                checks,
                name="min_repair_rate",
                passed=float(metrics["repair_rate"]) >= threshold,
                details={"actual": metrics["repair_rate"], "threshold": threshold},
            )
    if "max_repair_rate" in expectations:
        threshold = _float_or_none(expectations.get("max_repair_rate"))
        if threshold is not None:
            _add_check(
                checks,
                name="max_repair_rate",
                passed=float(metrics["repair_rate"]) <= threshold,
                details={"actual": metrics["repair_rate"], "threshold": threshold},
            )
    if "max_no_match_rate" in expectations:
        threshold = _float_or_none(expectations.get("max_no_match_rate"))
        if threshold is not None:
            no_match_rate = _rate(int(metrics.get("no_match_count") or 0), int(metrics.get("trace_count") or 0))
            _add_check(
                checks,
                name="max_no_match_rate",
                passed=no_match_rate <= threshold,
                details={"actual": no_match_rate, "threshold": threshold},
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
    context_budget = metrics.get("context_budget") if isinstance(metrics.get("context_budget"), Mapping) else {}
    if "min_context_packet_count" in expectations:
        threshold = _float_or_none(expectations.get("min_context_packet_count"))
        if threshold is not None:
            actual = int(context_budget.get("packet_count") or 0)
            _add_check(
                checks,
                name="min_context_packet_count",
                passed=float(actual) >= threshold,
                details={"actual": actual, "threshold": threshold},
            )
    context_token_threshold = _float_or_none(
        expectations.get("max_context_token_estimate_p95", expectations.get("max_context_tokens_p95"))
    )
    if context_token_threshold is not None:
        token_summary = context_budget.get("token_estimate") if isinstance(context_budget.get("token_estimate"), Mapping) else {}
        actual = _float_or_none(token_summary.get("p95") if isinstance(token_summary, Mapping) else None)
        if actual is not None:
            _add_check(
                checks,
                name="max_context_token_estimate_p95",
                passed=actual <= context_token_threshold,
                details={"actual": actual, "threshold": context_token_threshold},
            )
    if "max_context_utilization" in expectations:
        threshold = _float_or_none(expectations.get("max_context_utilization"))
        utilization = context_budget.get("utilization") if isinstance(context_budget.get("utilization"), Mapping) else {}
        actual = _float_or_none(utilization.get("max") if isinstance(utilization, Mapping) else None)
        if threshold is not None and actual is not None:
            _add_check(
                checks,
                name="max_context_utilization",
                passed=actual <= threshold,
                details={"actual": actual, "threshold": threshold},
            )
    if "max_context_budget_exhausted_rate" in expectations:
        threshold = _float_or_none(expectations.get("max_context_budget_exhausted_rate"))
        if threshold is not None:
            actual = _float_or_none(context_budget.get("exhausted_rate")) or 0.0
            _add_check(
                checks,
                name="max_context_budget_exhausted_rate",
                passed=actual <= threshold,
                details={"actual": actual, "threshold": threshold},
            )

    model_grades = _evaluate_model_grades(
        conversation_id=cid,
        thread_id=thread_id,
        messages=stored_messages,
        traces=stored_traces,
        expectations=expectations,
        metrics=metrics,
        grader=model_grader,
    )
    for grade in model_grades:
        grade_id = str(grade.get("id") or "model_grader")
        _add_check(
            checks,
            name=f"model_grader:{grade_id}",
            passed=bool(grade.get("passed")),
            details={
                "id": grade_id,
                "score": grade.get("score"),
                "threshold": grade.get("threshold"),
                "status": grade.get("status"),
                "label": grade.get("label"),
                "reason": grade.get("reason"),
            },
        )

    failures = [check for check in checks if not check.get("passed")]
    result = {
        "schema": EVAL_SCHEMA,
        "conversation_id": cid,
        "thread_id": str(thread_id or "").strip() or None,
        "status": "passed" if not failures else "failed",
        "metrics": metrics,
        "checks": checks,
        "failures": failures,
        "evidence_refs": _evidence_refs(stored_messages, stored_traces),
    }
    if model_grades:
        result["model_grades"] = model_grades
    return result


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


def evaluate_golden_dataset(
    dataset: Mapping[str, Any],
    *,
    model_grader: ModelGrader | None = None,
) -> dict[str, Any]:
    dataset_id = str(dataset.get("id") or "").strip()
    if not dataset_id:
        raise ValueError("golden dataset id is required")
    result = evaluate_golden_conversation(
        conversation_id=str(dataset.get("conversation_id") or ""),
        messages=_mapping_list(dataset.get("messages")),
        traces=_mapping_list(dataset.get("turn_traces")),
        expectations=dataset.get("expectations") if isinstance(dataset.get("expectations"), Mapping) else {},
        model_grader=model_grader,
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
    model_grader: ModelGrader | None = None,
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
            result = evaluate_golden_dataset(dataset, model_grader=model_grader)
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


def _evaluate_model_grades(
    *,
    conversation_id: str,
    thread_id: str | None,
    messages: Sequence[Mapping[str, Any]],
    traces: Sequence[Mapping[str, Any]],
    expectations: Mapping[str, Any],
    metrics: Mapping[str, Any],
    grader: ModelGrader | None,
) -> list[dict[str, Any]]:
    grades: list[dict[str, Any]] = []
    for spec in _model_grade_specs(expectations.get("model_grades")):
        grade_id = str(spec.get("id") or "model_grader").strip() or "model_grader"
        threshold = _float_or_none(spec.get("min_score"))
        request = {
            "schema": MODEL_GRADER_REQUEST_SCHEMA,
            "id": grade_id,
            "conversation_id": conversation_id,
            "thread_id": str(thread_id or "").strip() or None,
            "rubric": spec,
            "messages": _compact_eval_messages(messages),
            "turn_traces": _compact_eval_traces(traces),
            "metrics": dict(metrics),
        }
        try:
            raw = grader(request) if grader is not None else model_grade_conversation(request)
            grades.append(_normalize_model_grade(grade_id, raw, threshold=threshold))
        except Exception as exc:
            grades.append(
                {
                    "schema": MODEL_GRADE_SCHEMA,
                    "id": grade_id,
                    "status": "error",
                    "passed": False,
                    "score": None,
                    "threshold": threshold,
                    "label": "grader_error",
                    "reason": str(exc),
                }
            )
    return grades


def _model_grade_specs(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _compact_eval_messages(messages: Sequence[Mapping[str, Any]], *, limit: int = 40) -> list[dict[str, Any]]:
    selected = list(messages)[-max(1, min(int(limit or 40), 200)) :]
    return [
        {
            "id": str(item.get("id") or item.get("message_id") or ""),
            "seq": int(item.get("seq") or 0),
            "role": str(item.get("from") or item.get("role") or ""),
            "text": str(item.get("text") or ""),
            "channel_id": str(item.get("dialog_channel_id") or item.get("channel_id") or "") or None,
            "agent_id": str(item.get("active_agent_id") or item.get("actor_id") or "") or None,
        }
        for item in selected
    ]


def _compact_eval_traces(traces: Sequence[Mapping[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    selected = list(traces)[-max(1, min(int(limit or 20), 100)) :]
    return [
        {
            "turn_trace_id": str(item.get("turn_trace_id") or ""),
            "status": str(item.get("status") or ""),
            "channel_id": str(item.get("channel_id") or "") or None,
            "agent_id": str(item.get("agent_id") or "") or None,
            "selected_tool": str(item.get("selected_tool") or "") or None,
            "policy_decision": dict(item.get("policy_decision") or {})
            if isinstance(item.get("policy_decision"), Mapping)
            else {},
            "summary": str(item.get("summary") or "") or None,
        }
        for item in selected
    ]


def model_grade_conversation(request: Mapping[str, Any]) -> dict[str, Any]:
    """Run one optional LLM-backed evaluation rubric through the Root LLM proxy."""
    spec = request.get("rubric") if isinstance(request.get("rubric"), Mapping) else {}
    grade_id = str(spec.get("id") or "model_grader").strip() or "model_grader"
    threshold = _float_or_none(spec.get("min_score"))
    prompt_payload = {
        "schema": MODEL_GRADER_REQUEST_SCHEMA,
        "conversation_id": request.get("conversation_id"),
        "thread_id": request.get("thread_id"),
        "rubric": dict(spec),
        "messages": list(request.get("messages") or []),
        "metrics": dict(request.get("metrics") or {}),
    }
    instructions = (
        "You are an AdaOS conversation evaluation grader. "
        "Return only compact JSON with keys score, label, reason, and unresolved_user_request. "
        "Score must be a number from 0 to 1."
    )
    from adaos.sdk.llm.llm_client import send_response

    response = send_response(
        [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True)},
        ],
        model=str(spec.get("model") or "") or None,
        temperature=0.0,
        max_tokens=500,
        timeout=_float_or_none(spec.get("timeout_seconds")) or 30,
    )
    raw_text = str(response.get("output_text") or "").strip()
    parsed = _parse_model_grade_json(raw_text)
    return _normalize_model_grade(grade_id, parsed, threshold=threshold, raw={"output_text": raw_text})


def _parse_model_grade_json(text: str) -> Mapping[str, Any]:
    clean = str(text or "").strip()
    if not clean:
        return {"score": 0.0, "label": "empty_grader_output", "reason": "model returned no JSON"}
    try:
        parsed = json.loads(clean)
        return parsed if isinstance(parsed, Mapping) else {"score": 0.0, "label": "non_object_json", "raw": parsed}
    except Exception:
        start = clean.find("{")
        end = clean.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(clean[start : end + 1])
                if isinstance(parsed, Mapping):
                    return parsed
            except Exception:
                pass
    return {"score": 0.0, "label": "invalid_grader_json", "reason": clean[:500]}


def _normalize_model_grade(
    grade_id: str,
    value: Mapping[str, Any],
    *,
    threshold: float | None,
    raw: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    score = _float_or_none(value.get("score"))
    if score is not None:
        score = max(0.0, min(1.0, score))
    unresolved = bool(value.get("unresolved_user_request"))
    explicit_passed = value.get("passed")
    if isinstance(explicit_passed, bool):
        passed = explicit_passed
    elif score is not None and threshold is not None:
        passed = score >= threshold
    elif score is not None:
        passed = score >= 0.5
    else:
        passed = False
    if unresolved:
        passed = False
    status = "passed" if passed else "failed"
    result = {
        "schema": MODEL_GRADE_SCHEMA,
        "id": str(value.get("id") or grade_id or "model_grader").strip() or "model_grader",
        "status": status,
        "passed": passed,
        "score": score,
        "threshold": threshold,
        "label": str(value.get("label") or value.get("verdict") or status),
        "reason": str(value.get("reason") or value.get("rationale") or ""),
        "unresolved_user_request": unresolved,
    }
    if raw:
        result["raw"] = dict(raw)
    return result


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
    return _numeric_summary(values)


def _numeric_summary(values: Sequence[float]) -> dict[str, Any]:
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


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(float(count) / float(total), 6)


def _stable_json_key(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(value), sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(id(value))


def _context_packets_from_container(container: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    packets: list[Mapping[str, Any]] = []
    roots: list[Mapping[str, Any]] = [container]
    for key in ("_meta", "meta", "payload", "policy_decision", "renderer", "diagnostics"):
        value = container.get(key)
        if isinstance(value, Mapping):
            roots.append(value)
    for root in roots:
        for key in ("context_packet", "llm_context_packet", "retrieval_context"):
            packet = root.get(key)
            if _looks_like_context_packet(packet):
                packets.append(packet)  # type: ignore[arg-type]
    return packets


def _looks_like_context_packet(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    schema = str(value.get("schema") or "").strip()
    if schema in {"adaos.context.packet.v1", "adaos.context_packet.v1"}:
        return True
    return "token_estimate" in value or "budgets" in value or "budget" in value


def _context_budget_summary(packets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    token_estimates: list[float] = []
    max_tokens: list[float] = []
    utilization: list[float] = []
    selected_source_counts: list[float] = []
    skipped_source_counts: list[float] = []
    exhausted_count = 0
    for packet in packets:
        token_estimate = _float_or_none(packet.get("token_estimate") or packet.get("estimated_tokens"))
        budget = packet.get("budgets") if isinstance(packet.get("budgets"), Mapping) else packet.get("budget")
        budget_max = None
        if isinstance(budget, Mapping):
            budget_max = _float_or_none(budget.get("max_tokens") or budget.get("token_limit"))
        if budget_max is None:
            budget_max = _float_or_none(packet.get("max_tokens") or packet.get("token_limit"))
        if token_estimate is not None:
            token_estimates.append(token_estimate)
        if budget_max is not None:
            max_tokens.append(budget_max)
        if token_estimate is not None and budget_max is not None and budget_max > 0:
            utilization.append(token_estimate / budget_max)
        diagnostics = packet.get("diagnostics") if isinstance(packet.get("diagnostics"), Mapping) else {}
        if bool(packet.get("budget_exhausted") or diagnostics.get("budget_exhausted")):
            exhausted_count += 1
        selected_sources = packet.get("selected_sources")
        if selected_sources is None and isinstance(diagnostics, Mapping):
            selected_sources = diagnostics.get("selected_sources")
        skipped_sources = packet.get("skipped_sources")
        if skipped_sources is None and isinstance(diagnostics, Mapping):
            skipped_sources = diagnostics.get("skipped_sources")
        if isinstance(selected_sources, Sequence) and not isinstance(selected_sources, (str, bytes, bytearray)):
            selected_source_counts.append(float(len(selected_sources)))
        if isinstance(skipped_sources, Sequence) and not isinstance(skipped_sources, (str, bytes, bytearray)):
            skipped_source_counts.append(float(len(skipped_sources)))
    packet_count = len(packets)
    return {
        "schema": "adaos.conversation.eval.context_budget.v1",
        "packet_count": packet_count,
        "token_estimate": _numeric_summary(token_estimates),
        "max_tokens": _numeric_summary(max_tokens),
        "utilization": _numeric_summary(utilization),
        "exhausted_count": exhausted_count,
        "exhausted_rate": _rate(exhausted_count, packet_count),
        "selected_source_count": _numeric_summary(selected_source_counts),
        "skipped_source_count": _numeric_summary(skipped_source_counts),
    }


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
