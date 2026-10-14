import pytest
from adaos.services.builder.model_costs import estimate_observed_model_cost
from adaos.services.builder.semantic_proposer import _response_metrics


def test_standard_cost_uses_observed_cache_and_includes_reasoning_output_once():
    metrics = {'model': 'gpt-5', 'resolved_model': 'gpt-5-2025-08-07', 'llm_calls': 1,
        'input_tokens': 5528, 'cached_input_tokens': 0, 'output_tokens': 5078, 'reasoning_tokens': 3712}
    estimate = estimate_observed_model_cost(metrics)
    assert estimate['estimated_usd'] == pytest.approx(0.05769)
    assert estimate['basis'] == 'estimate' and estimate['tariff']['currency'] == 'USD'
    metrics.update(input_tokens=1000, cached_input_tokens=400, output_tokens=100)
    assert estimate_observed_model_cost(metrics)['estimated_usd'] == pytest.approx(0.0018)


@pytest.mark.parametrize('change', [{'model': 'gpt-6-astra'}, {'cached_input_tokens': None},
    {'llm_calls': 2}, {'repair_attempts': 1}, {'cached_input_tokens': 99999}])
def test_partial_or_unknown_cost_is_never_free_or_guessed(change):
    metrics = {'model': 'gpt-5', 'llm_calls': 1, 'input_tokens': 100, 'cached_input_tokens': 0, 'output_tokens': 20, **change}
    assert estimate_observed_model_cost(metrics) is None


def test_paid_root_observation_projects_standard_estimate_to_builder_run():
    metrics = _response_metrics({'job_id': 'paid', 'model': 'gpt-5', 'response': {
        'model': 'gpt-5-2025-08-07', 'usage': {'input_tokens': 1000, 'output_tokens': 100,
            'input_tokens_details': {'cached_tokens': 400}}}}, 'high')
    assert metrics['cost_usd'] == pytest.approx(0.0018) and metrics['cost_basis'] == 'estimate'
