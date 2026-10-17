from copy import deepcopy
import pytest
from jsonschema import ValidationError
from adaos.services.builder.run_observability import execution_metrics_from_llm, project_run, validate_execution_metrics
from adaos.services.builder.workflow import _normalize_run, BuilderWorkflowError


def test_missing_metrics_are_unknown_not_free_or_unrepaired():
    row = project_run({'run_id': 'run:one', 'activity': 'prototype_revision_recorded', 'output_refs': ['ui_revision:007']})
    assert row['revision'] == '007'
    assert 'run:one' in row['title']
    assert row['cost_label'] == row['repair_attempts_label'] == row['model_label'] == '—'
    assert row['measurement_label'] == 'Not recorded'


def test_root_usage_is_not_double_counted_and_repairs_have_independent_sources():
    result = {'model': 'gpt-6-astra', 'telemetry': {'root_job_id': 'primary', 'repair': {'root_job_id': 'repair'},
        'usage': {'input_tokens': 1000, 'cached_input_tokens': 300, 'output_tokens': 700, 'reasoning_tokens': 500},
        'timing': {'queue_ms': 23, 'execution_ms': 456}, 'wait_elapsed_ms': 600}}
    original = deepcopy(result)
    metrics = execution_metrics_from_llm(result, reasoning_effort='high')
    assert metrics['output_tokens'] == 700
    assert metrics['reasoning_tokens'] == 500
    assert metrics['llm_calls'] == 2 and metrics['repair_attempts'] == 1
    assert 'cost_usd' not in metrics
    assert metrics['root_execution_ms'] == 456
    assert metrics['client_wait_ms'] == 600
    assert result == original
    row = _normalize_run({'run_id': 'one', 'execution_metrics': metrics}, change_id='change')
    assert row['execution_metrics'] == metrics


def test_explicit_estimate_preserves_its_basis():
    metrics = execution_metrics_from_llm({'cost': {'estimated_usd': 0.0000001}})
    row = project_run({'execution_metrics': metrics})
    assert row['cost_label'] != '$0 (estimate)'
    assert '(estimate)' in row['cost_label']


@pytest.mark.parametrize('bad', [{'source': 'invented'}, {'source': 'builder.llm_result', 'input_tokens': -1},
    {'source': 'core.semantic_graph', 'llm_calls': True}, {'source': 'builder.llm_result', 'cost_usd': 0},
    {'source': 'builder.llm_result', 'raw_prompt': 'private'},
    {'source': 'builder.llm_result', 'root_execution_ms': float('inf')},
    {'source': 'builder.llm_result', 'cost_usd': float('nan'), 'cost_basis': 'estimate'},
    {'source': 'builder.llm_result', 'cost_usd': 0, 'cost_basis': 'zero_model', 'llm_calls': 1}])
def test_invalid_or_unbounded_execution_evidence_is_rejected(bad):
    with pytest.raises(ValidationError):
        validate_execution_metrics(bad)
    with pytest.raises(BuilderWorkflowError):
        _normalize_run({'run_id': 'one', 'execution_metrics': bad}, change_id='change')


def test_core_zero_model_is_explicit_not_an_inferred_missing_value():
    metrics = {'source': 'core.semantic_graph', 'llm_calls': 0, 'cost_usd': 0, 'cost_basis': 'zero_model', 'repair_attempts': 0}
    row = project_run({'run_id': 'tx', 'execution_metrics': validate_execution_metrics(metrics)})
    assert row['model_label'] == 'Core (no LLM)'
    assert row['cost_label'] == '$0 (zero_model)'


def test_partial_repair_cost_never_looks_like_a_complete_total():
    result = {'model': 'gpt-5', 'telemetry': {'root_job_id': 'primary', 'cost': {'estimated_usd': 1.2},
              'provider': {'model': 'gpt-5-2025-08-07'}, 'repair': {'root_job_id': 'repair'}}}
    metrics = execution_metrics_from_llm(result)
    assert 'cost_usd' not in metrics
    assert metrics['resolved_model'] == 'gpt-5-2025-08-07'
    result['telemetry']['repair']['cost'] = {'estimated_usd': .8}
    metrics = execution_metrics_from_llm(result)
    assert metrics['cost_usd'] == 2 and metrics['cost_basis'] == 'estimate'


def test_disjoint_attempts_do_not_double_count_native_aggregate_and_repeated_jobs():
    primary = {'root_job_id': 'a', 'usage': {'input_tokens': 100, 'cached_input_tokens': 30,
               'output_tokens': 80, 'reasoning_tokens': 50}, 'cost': {'estimated_usd': .2}}
    repair = {'root_job_id': 'b', 'usage': {'input_tokens': 200, 'cached_input_tokens': 40,
              'output_tokens': 90, 'reasoning_tokens': 60}, 'cost': {'estimated_usd': .3}}
    metrics = execution_metrics_from_llm({'telemetry': {'usage': {'input_tokens': 300, 'output_tokens': 170},
        'attempt_observations': [primary, repair, repair]}})
    assert metrics['llm_calls'] == 2 and metrics['input_tokens'] == 300 and metrics['output_tokens'] == 170
    assert metrics['cached_input_tokens'] == 70 and metrics['reasoning_tokens'] == 110
    assert metrics['cost_usd'] == .5 and metrics['usage_complete']
    assert len(metrics['attempt_observations']) == 2


def test_missing_repair_usage_stays_unknown_with_expandable_known_facts():
    result = {'telemetry': {'attempt_observations': [
        {'root_job_id': 'a', 'usage': {'input_tokens': 100, 'output_tokens': 50}, 'cost': {'estimated_usd': .2}},
        {'root_job_id': 'b'}]}}
    metrics = execution_metrics_from_llm(result)
    assert not metrics['usage_complete'] and 'cost_usd' not in metrics and 'input_tokens' not in metrics
    assert metrics['attempt_observations'][0]['cost_usd'] == .2
    assert project_run({'execution_metrics': metrics})['measurement_label'] == 'Partial'


def test_conflicting_attempt_receipts_do_not_look_complete_or_charge_twice():
    metrics = execution_metrics_from_llm({'telemetry': {'attempt_observations': [
        {'root_job_id': 'same', 'usage': {'input_tokens': 100, 'output_tokens': 50}},
        {'root_job_id': 'same', 'usage': {'input_tokens': 200, 'output_tokens': 50}}]}})
    assert metrics['llm_calls'] == 1 and not metrics['usage_complete']
    assert metrics['attempt_observations'][0]['status'] == 'conflict'


def test_late_complementary_receipt_completes_one_attempt_without_new_charge():
    observed = {'root_job_id': 'same', 'usage': {'input_tokens': 100, 'output_tokens': 50}}
    late = {**observed, 'usage': {**observed['usage'], 'cached_input_tokens': 30}, 'cost': {'estimated_usd': .3},
            'timing': {'execution_ms': 200, 'queue_ms': 20}}
    metrics = execution_metrics_from_llm({'telemetry': {'attempt_observations': [observed, late, late]}})
    assert metrics['llm_calls'] == 1 and metrics['cost_usd'] == .3 and metrics['usage_complete']
    assert metrics['root_execution_ms'] == 200 and metrics['root_queue_ms'] == 20


@pytest.mark.parametrize('values', [{'input_tokens': 100, 'cached_input_tokens': 101},
                                  {'output_tokens': 50, 'reasoning_tokens': 51}])
def test_overlapping_token_subsets_cannot_exceed_the_total(values):
    with pytest.raises(ValidationError, match='exceeds'):
        validate_execution_metrics({'source': 'builder.llm_result', **values})
def test_automation_missing_receipt_is_unknown_and_only_proven_model_free_work_is_zero():
    from adaos.services.builder.automation import BuilderAutomationService
    task = {"created_at": "2026-10-17T00:00:00Z"}
    unknown = BuilderAutomationService._budget_usage_projection(status="failed", task=task, local_run={},
        declared_budget={"max_model_tokens": 1000})
    assert unknown["usage_state"] == "unknown"
    assert unknown["observed"]["model_tokens"] is None
    assert unknown["observed"]["budget_tokens"] is None
    assert unknown["observed"]["attempts"] is None
    model_free = BuilderAutomationService._budget_usage_projection(status="completed", task=task, local_run={},
        declared_budget={"max_model_tokens": 1000}, execution_strategy="validation_only")
    assert model_free["usage_state"] == "zero_model"
    assert model_free["observed"]["model_tokens"] == model_free["observed"]["budget_tokens"] == 0


def test_automation_does_not_fill_absent_reasoning_or_cached_usage_with_zero():
    from adaos.services.builder.automation import BuilderAutomationService
    result = BuilderAutomationService._budget_usage_projection(status="failed", task={}, local_run={},
        usage_receipt={"input_tokens": 5, "output_tokens": 2, "model_tokens": 7},
        declared_budget={"max_model_tokens": 1000, "token_budget_metric": "fresh_plus_output"})
    assert result["observed"]["model_tokens"] == 7
    assert result["observed"]["cached_input_tokens"] is None
    assert result["observed"]["reasoning_tokens"] is None
    assert result["observed"]["budget_tokens"] is None
    assert result["status"] == "unknown" and result["usage_state"] == "partial"


@pytest.mark.parametrize('observations', [[], [{'root_job_id': 'primary',
    'usage': {'input_tokens': 100, 'output_tokens': 50}, 'cost': {'estimated_usd': .2}}]])
def test_known_repair_without_observation_prevents_complete_accounting(observations):
    metrics = execution_metrics_from_llm({'telemetry': {'root_job_id': 'primary',
        'repair': {'root_job_id': 'repair'}, 'attempt_observations': observations}})
    assert metrics['llm_calls'] == 2 and metrics['repair_attempts'] == 1
    assert not metrics['usage_complete'] and 'cost_usd' not in metrics
    assert set(metrics['source_refs']) == {'llm_job:primary', 'llm_job:repair'}


def test_partial_fresh_usage_cannot_hide_proven_billable_overrun():
    from adaos.services.builder.automation import BuilderAutomationService
    result = BuilderAutomationService._budget_usage_projection(status='failed', task={}, local_run={},
        usage_receipt={'input_tokens': 180, 'output_tokens': 20, 'model_tokens': 200},
        declared_budget={'max_model_tokens': 1000, 'max_billable_tokens': 100,
                         'token_budget_metric': 'fresh_plus_output'})
    assert result['usage_state'] == 'partial'
    assert result['observed']['budget_tokens'] is None
    assert result['status'] == result['billable_status'] == 'exceeded'
    assert result['billable_overrun_tokens'] == 100


