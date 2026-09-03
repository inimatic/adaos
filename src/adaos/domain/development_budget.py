from __future__ import annotations

from typing import Any, Mapping


DEFAULT_BILLABLE_TOKEN_MULTIPLIER = 8
DEFAULT_BILLABLE_TOKEN_FLOOR = 200_000
DEFAULT_PROMPT_TOKEN_MIN_RESERVE = 1_024
DEFAULT_PROMPT_TOKEN_MAX_RESERVE = 8_192


def execution_token_metric(value: Mapping[str, Any] | None) -> str:
    budget = value if isinstance(value, Mapping) else {}
    return (
        "fresh_plus_output"
        if str(budget.get("token_budget_metric") or "").strip()
        == "fresh_plus_output"
        else "model_tokens"
    )


def execution_model_token_limit(value: Mapping[str, Any] | None) -> int:
    budget = value if isinstance(value, Mapping) else {}
    try:
        return max(
            0,
            int(budget.get("max_model_tokens") or budget.get("max_tokens") or 0),
        )
    except (TypeError, ValueError):
        return 0


def execution_billable_token_limit(value: Mapping[str, Any] | None) -> int:
    budget = value if isinstance(value, Mapping) else {}
    try:
        explicit = int(
            budget.get("max_billable_tokens")
            or budget.get("max_total_tokens")
            or 0
        )
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit
    model_limit = execution_model_token_limit(budget)
    if model_limit <= 0:
        return 0
    if execution_token_metric(budget) == "fresh_plus_output":
        return max(
            DEFAULT_BILLABLE_TOKEN_FLOOR,
            model_limit * DEFAULT_BILLABLE_TOKEN_MULTIPLIER,
        )
    return model_limit


def execution_prompt_token_limit(model_token_limit: int) -> int:
    limit = max(0, int(model_token_limit or 0))
    if limit <= 0:
        return 0
    reserve = min(
        DEFAULT_PROMPT_TOKEN_MAX_RESERVE,
        max(DEFAULT_PROMPT_TOKEN_MIN_RESERVE, limit // 10),
    )
    return max(1, limit - reserve)


def with_effective_billable_token_limit(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    budget = dict(value)
    limit = execution_billable_token_limit(budget)
    if limit > 0:
        budget.setdefault("max_billable_tokens", limit)
    return budget


__all__ = [
    "DEFAULT_BILLABLE_TOKEN_MULTIPLIER",
    "DEFAULT_BILLABLE_TOKEN_FLOOR",
    "DEFAULT_PROMPT_TOKEN_MAX_RESERVE",
    "DEFAULT_PROMPT_TOKEN_MIN_RESERVE",
    "execution_billable_token_limit",
    "execution_model_token_limit",
    "execution_prompt_token_limit",
    "execution_token_metric",
    "with_effective_billable_token_limit",
]
