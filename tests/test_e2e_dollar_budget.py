import pytest
from adaos.e2e.dollar_budget import DollarBudget


def test_unknown_calls_keep_the_reserved_cost_and_are_not_recycled(tmp_path):
    budget = DollarBudget(tmp_path / 'budget.sqlite', limit_usd=8)
    budget.reserve('first', reserve_usd=5)
    with pytest.raises(ValueError, match='Insufficient'):
        budget.reserve('second', reserve_usd=4)
    with pytest.raises(ValueError, match='Already'):
        budget.reserve('first', reserve_usd=1)
    assert budget.snapshot()['committed_estimated_usd'] == 5


def test_observed_cost_replaces_estimate_and_survives_restart(tmp_path):
    path = tmp_path / 'budget.sqlite'
    budget = DollarBudget(path, limit_usd=8)
    budget.reserve('first', reserve_usd=5)
    budget.observe('first', usd=1.25)
    budget = DollarBudget(path, limit_usd=8)
    budget.reserve('second', reserve_usd=6)
    assert budget.snapshot()['committed_estimated_usd'] == 7.25
    with pytest.raises(ValueError, match='drift'):
        budget.observe('first', usd=2)
    with pytest.raises(ValueError, match='drift'):
        DollarBudget(path, limit_usd=10)
