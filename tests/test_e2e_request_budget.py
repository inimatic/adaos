from concurrent.futures import ThreadPoolExecutor
import pytest
from adaos.e2e.request_budget import RequestBudget, RequestBudgetExceeded


def test_fixed_total_reallocation_preserves_every_attempt(tmp_path):
    budget = RequestBudget(tmp_path / 'budget.sqlite')
    ordinal = budget.reserve('primary', 'ambiguous')
    budget.observe(ordinal, status='verified_not_dispatched')
    budget.reallocate({'primary':22,'repair':6,'judge':4}, reason='Two delivery interruptions, no reset of attempts')
    restored = RequestBudget(budget.path)
    assert restored.limits == {'primary':22,'repair':6,'judge':4}
    assert restored.snapshot()['attempted_requests'] == 1
    with pytest.raises(RequestBudgetExceeded): restored.reserve('primary', 'ambiguous')
    with pytest.raises(ValueError): restored.reallocate({'primary':33,'repair':0,'judge':0},reason='Invalid expansion')

def test_atomic_budget_never_exceeds_the_cap_under_parallel_workers(tmp_path):
    path = tmp_path / "budget.sqlite3"
    budget = RequestBudget(path, maximum=8, limits={"primary": 8})
    def claim(index):
        try:
            return RequestBudget(path, maximum=8, limits={"primary": 8}).reserve("primary", str(index))
        except RequestBudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(claim, range(30)))
    assert len([value for value in attempts if value is not None]) == 8
    assert budget.snapshot()["attempted_requests"] == 8

def test_failure_and_duplicate_requests_cannot_release_or_reuse_a_slot(tmp_path):
    budget = RequestBudget(tmp_path / "budget.sqlite3", maximum=2, limits={"primary": 1, "repair": 1})
    slot = budget.reserve("primary", "same")
    budget.observe(slot, job_id="job", status="failed")
    with pytest.raises(RequestBudgetExceeded):
        budget.reserve("primary", "same")
    with pytest.raises(RequestBudgetExceeded):
        budget.reserve("primary", "another")
    budget.reserve("repair", "repair")
    assert budget.snapshot()["counts"] == {"primary": 1, "repair": 1}
    with pytest.raises(ValueError, match="drift"):
        RequestBudget(budget.path, maximum=2, limits={"primary": 2})
