"""Offline context-miss aggregation; observations never become authority."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from adaos.services.builder_context import digest


LEARNING_SCHEMA = "adaos.context.learning_rule.v1"
EVAL_SCHEMA = "adaos.context.learning_evaluation.v1"


def aggregate_context_misses(receipts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Group exact contract/scope signatures, not model-written anecdotes.

    Repeated retries of the same task count once. A group is only review input;
    neither semantic generalization nor automatic rule promotion is claimed.
    """
    groups: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str]] = set()
    for receipt in receipts:
        # A Builder run/repair is not a new learning task. The Change identity
        # must be retained explicitly so retries cannot manufacture recurrence.
        task = str(receipt.get("logical_task_ref") or "")
        if not task:
            continue
        for miss in receipt.get("context_misses") or []:
            refs = sorted(set(str(ref) for ref in miss.get("contract_refs") or [] if ref))
            scope = dict(miss.get("scope") or {})
            if (not refs or not scope.get("platform_version")
                    or any(value is None or value == "" for value in scope.values())):
                # Unknown contract/version is diagnostic backlog, not a rule.
                continue
            identity = digest({"contracts": refs, "scope": scope, "category": miss.get("category")})
            if (task, identity) in seen:
                continue
            seen.add((task, identity))
            group = groups.setdefault(identity, {
                "signature": identity, "contract_refs": refs, "scope": scope,
                "task_refs": [], "source_refs": [], "status": "observation_only",
            })
            group["task_refs"].append(task)
            ref = receipt.get("receipt_ref")
            if ref:
                group["source_refs"].append(ref)
    for group in groups.values():
        group["task_refs"].sort()
        group["source_refs"] = sorted(set(group["source_refs"]))
        group["distinct_task_count"] = len(group["task_refs"])
        group["review_eligible"] = len(group["task_refs"]) >= 2 and bool(group["source_refs"])
    return [groups[key] for key in sorted(groups)]


def validate_learning_rule(content: Mapping[str, Any], evaluations: Sequence[Mapping[str, Any]]) -> None:
    """Require scoped, falsifiable generalization with independent held-out evidence."""
    if content.get("schema") != LEARNING_SCHEMA:
        raise ValueError("unsupported context learning rule")
    scope = dict(content.get("scope") or {})
    observed_refs = content.get("observed_task_refs")
    if not isinstance(observed_refs, list) or not all(isinstance(ref, str) and ref.strip() for ref in observed_refs):
        raise ValueError("learning observed_task_refs must be non-empty task identities")
    observed = set(observed_refs)
    if len(observed) < 2:
        raise ValueError("context learning requires at least two distinct observed tasks")
    if not all(content.get(key) for key in ("invariant", "applies_when", "excludes", "expires_at")):
        raise ValueError("learning requires invariant, applicability, exclusions and expiry")
    if not scope.get("platform_version") or not scope.get("contract_refs"):
        raise ValueError("learning scope requires platform version and exact contracts")
    if (not isinstance(scope["contract_refs"], list)
            or not all(isinstance(ref, str) and ref.strip() for ref in scope["contract_refs"])
            or any(value is None or value == "" for value in scope.values())):
        raise ValueError("learning scope must contain known contract/version identities")
    expires = datetime.fromisoformat(str(content["expires_at"]).replace("Z", "+00:00"))
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise ValueError("learning expiry must be a future timezone-aware instant")
    for evaluation in evaluations:
        held_out_refs = evaluation.get("held_out_task_refs")
        if not isinstance(held_out_refs, list) or not all(isinstance(ref, str) and ref.strip() for ref in held_out_refs):
            continue
        held_out = set(held_out_refs)
        if (evaluation.get("schema") == EVAL_SCHEMA
                and evaluation.get("authority") == "independent_validator"
                and evaluation.get("rule_digest") == digest(content)
                and held_out and not held_out.intersection(observed)
                and evaluation.get("generalization_passed") is True
                and evaluation.get("counterexamples_passed") is True
                and evaluation.get("authority_violations") == 0
                and evaluation.get("evidence_refs")):
            return
    raise ValueError("context learning requires a passing independent held-out and counterexample evaluation")
