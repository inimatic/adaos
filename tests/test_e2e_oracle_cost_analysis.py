import importlib.util
from pathlib import Path

from adaos.e2e.composition_metrics import usage_record


def analyzer():
    path = Path(__file__).parents[1] / 'e2e/stand/analyze-prototype-oracle-cost.py'
    spec = importlib.util.spec_from_file_location('oracle_cost_analysis_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.analyze


def row(model):
    envelope = {'response': {'usage': {'input_tokens': 1000, 'output_tokens': 200,
        'total_tokens': 1200, 'output_tokens_details': {'reasoning_tokens': 150}}}}
    value = usage_record(envelope, model)
    jobs = [{'usage': value, 'actual_model': model, 'job_id': 'primary', 'timing': {}},
            {'usage': value, 'actual_model': model, 'job_id': 'repair', 'timing': {}}]
    return {'model': model, 'cohort': model + '-inventory', 'task': 'inventory', 'arm': 'oracle',
        'provider_jobs': jobs, 'mcp_primary_calls': 0, 'retrieval_behavior': {},
        'initial_context': {'message_characters': 1050, 'full_message_characters': 1000},
        'corrected_first_pass_compile': True, 'posthoc_rechecked_functional_pass': True,
        'usage_primary': value, 'usage_repair': value,
        'usage': {k: v * 2 for k, v in value.items() if isinstance(v, (int, float))}}


def test_repair_is_not_removed_from_actual_spending():
    result = analyzer()({'samples': [row('gpt-5')], 'cost_basis': {}}, Path('owned-run'))
    summary = result['oracle_summary'][0]
    assert summary['oracle_actual_usd_including_repairs'] == .0065
    assert summary['unnecessary_old_compiler_repair_usd'] == .00325
    assert result['calls'][0]['initial_message_chars'] > result['calls'][0]['full_message_chars']


def test_common_rate_counterfactual_keeps_observed_tokens_not_original_tariff():
    result = analyzer()({'samples': [row('gpt-6-astra')], 'cost_basis': {}}, Path('owned-run'))
    summary = result['oracle_summary'][0]
    assert summary['oracle_primary_usd'] == .02
    assert summary['oracle_primary_same_gpt5_rates_usd'] == .00325
    assert result['calls'][0]['usage']['total_tokens'] == 1200


def test_current_primary_pass_does_not_delete_failed_compiler_history():
    sample = row('gpt-5')
    sample['corrected_first_pass_compile'] = False
    result = analyzer()({'samples': [sample], 'cost_basis': {}}, Path('owned-run'))
    assert result['oracle_summary'][0]['unnecessary_old_compiler_repair_usd'] == 0
    assert result['oracle_summary'][0]['oracle_repair_usd'] == .00325


def test_pending_browser_qualification_is_not_counted_as_failure_or_success():
    sample = row('gpt-5.5')
    sample['posthoc_rechecked_functional_pass'] = None
    result = analyzer()({'samples': [sample], 'cost_basis': {}}, Path('owned-run'))
    comparison = next(item for item in result['all_arms'] if item['arm'] == 'oracle')
    assert comparison['final_posthoc_pass'] == 0
    assert comparison['final_posthoc_evaluated'] == 0
    assert comparison['n'] == 1
    assert result['calls'][0]['final_posthoc_functional_pass'] is None
