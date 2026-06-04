from __future__ import annotations

import os
import time
from typing import Any, Mapping

from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings

_MAX_DEFERRED_ENRICHMENT = max(1, int(os.getenv("ADAOS_NLU_TEACHER_DEFERRED_MAX", "250") or "250"))
_REDACT_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer",
    "mcp_bearer",
    "secret",
    "session_token",
    "token",
}

RETENTION_POLICY_VERSION = "nlu.teacher.retention.v1"
PROMOTION_POLICY_VERSION = "nlu.teacher.promotion.v1"
THREAT_MODEL_VERSION = "nlu.teacher.threat_model.v1"
BUDGET_POLICY_VERSION = "nlu.teacher.budget.v1"

RETENTION_POLICY = {
    "version": RETENTION_POLICY_VERSION,
    "raw_utterance": {"scope": "local_state", "default_ttl_days": 30},
    "stt_text": {"scope": "local_state", "default_ttl_days": 30},
    "normalized_text": {"scope": "local_state", "default_ttl_days": 90},
    "llm_prompt_context": {"scope": "audit_hashes_by_default", "default_ttl_days": 14},
    "trace": {"scope": "local_state", "default_ttl_days": 90},
    "candidates": {"scope": "local_overlay", "default_ttl_days": 180},
    "feedback": {"scope": "local_overlay", "default_ttl_days": 180},
}

PROMOTION_PRIVACY_GATES = {
    "version": PROMOTION_POLICY_VERSION,
    "default_state": "local_learned",
    "public_export_requires_operator_approval": True,
    "private_fields": [
        "raw_utterance",
        "local_entity_name",
        "device_name",
        "user_alias",
        "personal_example",
        "mcp_session_scope",
    ],
    "private_fields_action": "block_public_promotion",
}

THREAT_MODEL_CHECKLIST = {
    "version": THREAT_MODEL_VERSION,
    "checks": [
        "prompt_injection_markers",
        "malicious_or_untrusted_descriptors",
        "alias_hijacking",
        "overbroad_destructive_templates",
        "cross_subnet_mcp_scope_confusion",
        "unexpected_mcp_target_scope",
    ],
}

BUDGET_POLICY = {
    "version": BUDGET_POLICY_VERSION,
    "fallback_behavior": "store_miss_for_later_batch_enrichment",
    "fast_path_dependency": "none",
    "root_openai_dependency": "teacher_enrichment_only",
}


def _now(now: float | None = None) -> float:
    return time.time() if now is None else float(now)


def _clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            token = str(key)
            if token.casefold() in _REDACT_KEYS or any(marker in token.casefold() for marker in ("token", "secret", "bearer")):
                out[token] = "<redacted>"
            else:
                out[token] = _clean(item)
        return out
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def policy_snapshot() -> dict[str, Any]:
    return {
        "retention": dict(RETENTION_POLICY),
        "promotion_privacy": dict(PROMOTION_PRIVACY_GATES),
        "threat_model": dict(THREAT_MODEL_CHECKLIST),
        "budget": dict(BUDGET_POLICY),
    }


def ensure_teacher_policy_snapshot(teacher: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(teacher)
    policies = coerce_dict(out.get("policies"))
    base = policy_snapshot()
    for key, value in base.items():
        policies.setdefault(key, value)
    out["policies"] = policies
    return out


def _portability_for_candidate(candidate: Mapping[str, Any]) -> str:
    target = candidate.get("target") if isinstance(candidate.get("target"), Mapping) else {}
    target_type = str(target.get("type") or "").strip()
    kind = str(candidate.get("kind") or "").strip()
    if kind == "entity_alias":
        return "user-local"
    if target_type == "skill":
        return "skill-global"
    if target_type == "scenario":
        return "scenario-local"
    if target_type == "system":
        return "system-global"
    return "workspace-local"


def governance_for_candidate(
    candidate: Mapping[str, Any],
    *,
    webspace_id: str,
    meta: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    meta_map = coerce_dict(meta)
    llm = candidate.get("llm") if isinstance(candidate.get("llm"), Mapping) else {}
    audit = llm.get("audit") if isinstance(llm.get("audit"), Mapping) else {}
    mcp_audit = audit.get("mcp") if isinstance(audit.get("mcp"), Mapping) else {}
    target = candidate.get("target") if isinstance(candidate.get("target"), Mapping) else {}
    created_at = _now(now)
    portability = _portability_for_candidate(candidate)

    return {
        "promotion": {
            "state": str(candidate.get("promotion_state") or "local_learned"),
            "portability": portability,
            "public_export_allowed": False,
            "privacy_gate": "operator_approval_required",
        },
        "provenance": {
            "created_at": created_at,
            "source": "nlu_teacher",
            "request_id": candidate.get("request_id"),
            "thread_id": candidate.get("thread_id"),
            "webspace_id": webspace_id,
            "owner": dict(target) if target else None,
            "model": llm.get("model"),
            "decision": llm.get("decision"),
            "request_hash": audit.get("request_hash"),
            "context_hash": audit.get("context_hash"),
            "prompt_hash": audit.get("prompt_hash"),
            "mcp": _clean(
                {
                    "enabled": mcp_audit.get("enabled"),
                    "mode": mcp_audit.get("mode"),
                    "status": mcp_audit.get("status"),
                    "source": mcp_audit.get("source"),
                    "tool_count": mcp_audit.get("tool_count"),
                    "allowed_tools": mcp_audit.get("allowed_tools"),
                    "tools_hash": mcp_audit.get("tools_hash"),
                }
            ),
            "route_id": meta_map.get("route_id") or meta_map.get("route"),
            "channel": meta_map.get("channel"),
            "device_id": meta_map.get("device_id"),
            "mcp_session_scope_recorded": bool(mcp_audit),
            "mcp_bearer_embedded": False,
        },
        "privacy": {
            "retention_policy": RETENTION_POLICY_VERSION,
            "promotion_policy": PROMOTION_POLICY_VERSION,
            "raw_utterance_scope": "local_state",
            "public_promotion_requires_review": True,
            "contains_private_scope": portability in {"session-local", "user-local", "workspace-local", "scenario-local"},
        },
    }


def apply_candidate_governance(
    candidate: Mapping[str, Any],
    *,
    webspace_id: str,
    meta: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    out = dict(candidate)
    governance = governance_for_candidate(out, webspace_id=webspace_id, meta=meta, now=now)
    for key, value in governance.items():
        existing = out.get(key) if isinstance(out.get(key), Mapping) else {}
        merged = dict(value)
        merged.update(dict(existing))
        out[key] = merged
    return out


def _budget_obj(teacher: Mapping[str, Any]) -> dict[str, Any]:
    current = teacher.get("budget") if isinstance(teacher.get("budget"), Mapping) else {}
    out = dict(current)
    out.setdefault("policy", dict(BUDGET_POLICY))
    out.setdefault("counters", {})
    out.setdefault("by_reason", {})
    out.setdefault("recent", [])
    return out


def record_budget_event(
    teacher: Mapping[str, Any],
    *,
    status: str,
    request_id: str | None = None,
    model: str | None = None,
    reason: str | None = None,
    duration_s: float | None = None,
    token_usage: Mapping[str, Any] | None = None,
    cache: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    out = ensure_teacher_policy_snapshot(teacher)
    budget = _budget_obj(out)
    counters = dict(budget.get("counters") or {})
    status_key = str(status or "unknown").strip() or "unknown"
    counters["total"] = int(counters.get("total") or 0) + 1
    counters[status_key] = int(counters.get(status_key) or 0) + 1
    if token_usage:
        try:
            counters["tokens_total"] = int(counters.get("tokens_total") or 0) + int(token_usage.get("total_tokens") or 0)
        except Exception:
            pass
    budget["counters"] = counters

    if reason:
        by_reason = dict(budget.get("by_reason") or {})
        by_reason[str(reason)] = int(by_reason.get(str(reason)) or 0) + 1
        budget["by_reason"] = by_reason

    item = {
        "ts": _now(now),
        "request_id": request_id,
        "status": status_key,
        "reason": reason,
        "model": model,
        "duration_s": duration_s,
        "token_usage": dict(token_usage or {}) if token_usage else None,
        "cache": dict(cache or {}) if cache else None,
    }
    item = {key: value for key, value in item.items() if value not in (None, "", {}, [])}
    recent = [dict(x) for x in iter_mappings(budget.get("recent"))]
    recent.append(item)
    budget["recent"] = recent[-80:]
    budget["last_event"] = item
    out["budget"] = budget
    return out


def append_deferred_enrichment(
    teacher: Mapping[str, Any],
    *,
    request_id: str,
    text: str,
    reason: str,
    error: str | None = None,
    log_id: str | None = None,
    meta: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    out = ensure_teacher_policy_snapshot(teacher)
    queue = [dict(x) for x in iter_mappings(out.get("deferred_enrichment_queue"))]
    item = {
        "id": f"deferred.{request_id}",
        "ts": _now(now),
        "status": "pending",
        "request_id": request_id,
        "text": text,
        "reason": reason,
        "error": error,
        "log_id": log_id,
        "_meta": _clean(dict(meta or {})),
    }
    item = {key: value for key, value in item.items() if value not in (None, "", {}, [])}
    queue = [row for row in queue if row.get("request_id") != request_id]
    queue.append(item)
    out["deferred_enrichment_queue"] = queue[-_MAX_DEFERRED_ENRICHMENT:]
    return record_budget_event(
        out,
        status="deferred",
        request_id=request_id,
        reason=reason,
        now=now,
    )
