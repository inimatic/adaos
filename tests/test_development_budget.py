from adaos.domain.development_budget import (
    DEFAULT_BILLABLE_TOKEN_FLOOR,
    execution_billable_token_limit,
    with_effective_billable_token_limit,
)


def test_fresh_budget_derives_provider_context_floor() -> None:
    budget = {
        "max_model_tokens": 12_000,
        "token_budget_metric": "fresh_plus_output",
    }

    assert execution_billable_token_limit(budget) == DEFAULT_BILLABLE_TOKEN_FLOOR
    assert with_effective_billable_token_limit(budget) == {
        **budget,
        "max_billable_tokens": DEFAULT_BILLABLE_TOKEN_FLOOR,
    }


def test_explicit_billable_limit_remains_authoritative() -> None:
    budget = {
        "max_model_tokens": 12_000,
        "max_billable_tokens": 96_000,
        "token_budget_metric": "fresh_plus_output",
    }

    assert execution_billable_token_limit(budget) == 96_000
