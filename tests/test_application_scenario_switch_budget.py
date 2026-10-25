import pytest

from adaos.services.scenario.webspace_components.task_scheduling import _scenario_switch_background_timeout_s


@pytest.mark.parametrize("configured,expected", [
    (None, 120), ("20", 20), ("60", 60), ("0", 2), ("999", 120),
    ("invalid", 120), ("nan", 120), ("inf", 120),
])
def test_background_budget_is_bounded_and_distinct_from_http_read_budget(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv("ADAOS_SCENARIO_SWITCH_BACKGROUND_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("ADAOS_SCENARIO_SWITCH_BACKGROUND_TIMEOUT_S", configured)
    assert _scenario_switch_background_timeout_s() == expected
