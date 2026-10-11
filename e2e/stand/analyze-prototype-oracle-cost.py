"""Per-call Oracle accounting, common-rate repricing and historical caveats.

No model calls or application writes. Repairs remain in actual spending even
when deterministic replay shows that the old compiler unnecessarily caused them.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path

from adaos.e2e.builder import _write_json
from adaos.e2e.composition_metrics import RATES, usage_cost_components
from adaos.e2e.prototype_context_ablation import read


def analyze(report, run):
    groups = defaultdict(list)
    calls = []
    comparisons = []
    for row in report['samples']:
        groups[row['model']].append(row)
        if row['arm'] != 'oracle':
            continue
        for i, job in enumerate(row['provider_jobs']):
            usage = job['usage']
            if usage is None:
                raise ValueError('Unknown Oracle job usage cannot be dropped')
            primary = i == 0
            components = usage_cost_components(usage, RATES[row['model']])
            calls.append({'cohort': row['cohort'], 'task': row['task'], 'model': row['model'],
                'actual_model': job['actual_model'], 'job_id': job['job_id'],
                'call_kind': 'primary' if primary else 'repair', 'usage': usage, 'cost_components_usd': components,
                'same_observed_tokens_at_gpt5_rates_usd': round(sum(usage_cost_components(usage, RATES['gpt-5']).values()), 6),
                'unnecessary_old_compiler_repair': not primary and row['corrected_first_pass_compile'] is True,
                'primary_mcp_calls': row['mcp_primary_calls'] if primary else None,
                'primary_retrieval': row['retrieval_behavior'] if primary else None,
                'observed_root_timing': job['timing'],
                'initial_message_chars': row['initial_context']['message_characters'],
                'full_message_chars': row['initial_context']['full_message_characters'],
                'corrected_primary_compile': row['corrected_first_pass_compile'],
                'final_posthoc_functional_pass': row['posthoc_rechecked_functional_pass']})
    summaries = []
    for model, rows in groups.items():
        oracle = [c for c in calls if c['model'] == model]
        primary = [c for c in oracle if c['call_kind'] == 'primary']
        repairs = [c for c in oracle if c['call_kind'] == 'repair']
        summaries.append({'model': model,
            'oracle_primary_usd': round(sum(c['usage']['usd_standard_estimate_without_unreported_cache_writes'] for c in primary), 6),
            'oracle_repair_usd': round(sum(c['usage']['usd_standard_estimate_without_unreported_cache_writes'] for c in repairs), 6),
            'oracle_actual_usd_including_repairs': round(sum(c['usage']['usd_standard_estimate_without_unreported_cache_writes'] for c in oracle), 6),
            'oracle_primary_same_gpt5_rates_usd': round(sum(c['same_observed_tokens_at_gpt5_rates_usd'] for c in primary), 6),
            'primary_tokens': {key: sum(c['usage'][key] for c in primary) for key in
                ('input_tokens', 'cached_input_tokens', 'cache_write_tokens', 'output_tokens', 'reasoning_tokens', 'output_excluding_reasoning_tokens')},
            'primary_cost_components_usd': {key: round(sum(c['cost_components_usd'][key] for c in primary), 6)
                for key in primary[0]['cost_components_usd']},
            'primary_mcp_calls': sum(c['primary_mcp_calls'] for c in primary),
            'unnecessary_old_compiler_repair_usd': round(sum(c['usage']['usd_standard_estimate_without_unreported_cache_writes']
                for c in repairs if c['unnecessary_old_compiler_repair']), 6)})
        for arm in ('full-no-mcp', 'full-mcp', 'core-indexed', 'task-aware', 'oracle'):
            subset = [r for r in rows if r['arm'] == arm]
            comparisons.append({'model': model, 'arm': arm,
                'primary_usd': round(sum((r['usage_primary'] or {})['usd_standard_estimate_without_unreported_cache_writes'] for r in subset), 6),
                'repair_usd': round(sum(r['usage_repair']['usd_standard_estimate_without_unreported_cache_writes'] for r in subset), 6),
                'actual_generation_and_repair_usd': round(sum(r['usage']['usd_standard_estimate_without_unreported_cache_writes'] for r in subset), 6),
                'corrected_first_compile_pass': sum(r['corrected_first_pass_compile'] is True for r in subset),
                'final_posthoc_pass': sum(r['posthoc_rechecked_functional_pass'] is True for r in subset),
                'final_posthoc_evaluated': sum(r['posthoc_rechecked_functional_pass'] is not None for r in subset),
                'n': len(subset),
                'per_task_primary_usd': {r['task']: (r['usage_primary'] or {})['usd_standard_estimate_without_unreported_cache_writes'] for r in subset}})
    within_model = []
    for model, rows in groups.items():
        primary_oracle = [r['usage_primary'] for r in rows if r['arm'] == 'oracle']
        primary_full = [r['usage_primary'] for r in rows if r['arm'] == 'full-no-mcp']
        if primary_full:
            oracle_parts = [usage_cost_components(v, RATES[model]) for v in primary_oracle]
            full_parts = [usage_cost_components(v, RATES[model]) for v in primary_full]
            within_model.append({'model': model, 'comparison': 'Oracle primary minus full-no-MCP primary',
                'component_difference_usd': {key: round(sum(v[key] for v in oracle_parts) - sum(v[key] for v in full_parts), 6)
                    for key in oracle_parts[0]},
                'causal_claim': False, 'scope': 'Observed accounting decomposition; not a randomized intervention on MCP or reasoning.'})
    return {'schema': 'adaos.e2e.oracle_cost_analysis.v1', 'source': str(run / 'report.json'),
        'calls': calls, 'oracle_summary': summaries, 'all_arms': comparisons,
        'within_model_primary_decomposition': within_model,
        'cost_basis': report['cost_basis'], 'new_llm_calls': 0,
        'interpretation_limits': ['Common-rate repricing isolates prices for observed tokens, not counterfactual generation behavior.',
            'Unnecessary compiler-repair costs were actually spent; removing them is only a corrected-pipeline counterfactual.',
            'Corrected primary compiler success does not establish primary functional success when browsers tested a repaired candidate.',
            'MCP call count does not prove confusion; retrieved UX fragments were omitted in the oracle.',
            'Historical n=1 per cell and ordered cache exposure do not establish causal context ranking.',
            'Oracle sufficient-set hypothesis does not mean its inline content is smaller than full in every task.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_relative_to(Path.cwd() / 'e2e/artifacts/builder'):
        parser.error('Owned E2E evidence required')
    result = analyze(read(run / 'report.json'), run)
    _write_json(run / 'oracle-cost-analysis.json', result)
    for summary in result['oracle_summary']:
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
