"""Read-only tariff/token decomposition of a retained GPT-5/Astra Oracle series.

Repricing fixed observed tokens is NOT a prediction of another model's behavior.
Success-only selection is shown separately from complete process accounting.
No provider requests or application changes are made.
"""
import argparse
import hashlib
import json
from pathlib import Path
from adaos.e2e.builder import _write_json
from adaos.e2e.composition_metrics import RATES, usage_cost_components
from adaos.e2e.prototype_context_ablation import read

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', type=Path, required=True)
args=parser.parse_args();run=args.run.resolve()
if not run.is_relative_to(Path.cwd()/'e2e/artifacts/builder'):parser.error('Owned experiment required')
report=read(run/'report.json')
names=('gpt-5','gpt-6-astra')
selected=[item for item in report['samples'] if item['arm']=='oracle' and item['model'] in names]
if {item['model'] for item in selected}!=set(names):parser.error('Both model receipts required')
fields=('input_tokens','cached_input_tokens','cache_write_tokens','reasoning_tokens','output_excluding_reasoning_tokens')
totals={model:{field:sum(item['usage'][field] for item in selected if item['model']==model) for field in fields} for model in names}
matrix={model:{tariff:round(sum(usage_cost_components(totals[model],RATES[tariff]).values()),6)
    for tariff in names} for model in names}
g,a=names
rate_contribution=((matrix[g][a]-matrix[g][g])+(matrix[a][a]-matrix[a][g]))/2
quantity_contribution=((matrix[a][g]-matrix[g][g])+(matrix[a][a]-matrix[g][a]))/2
cells=[{'cohort':item['cohort'],'model':item['model'],'sample':item['sample'],
    'first_compile_pass':item['first_pass_compile'],'functional_e2e_pass':item['functional_e2e_pass'],
    'primary_usd':item['usage_primary']['usd_standard_estimate_without_unreported_cache_writes'],
    'repair_usd':item['usage_repair']['usd_standard_estimate_without_unreported_cache_writes'],
    'total_usd':item['usage']['usd_standard_estimate_without_unreported_cache_writes'],
    'components':usage_cost_components(item['usage'],RATES[item['model']])} for item in selected]
result={'schema':'adaos.e2e.oracle_cost_analysis.v1','new_llm_calls':0,
    'source_report_sha256':hashlib.sha256((run/'report.json').read_bytes()).hexdigest(),
    'cost_basis':report['cost_basis'],'cells':cells,'observed_token_totals':totals,
    'fixed_token_tariff_matrix_usd':matrix,
    'symmetric_accounting_decomposition_usd':{'tariff':round(rate_contribution,6),
        'observed_token_mix':round(quantity_contribution,6),
        'total_difference':round(matrix[a][a]-matrix[g][g],6)},
    'interpretation':['All failed primaries and repairs are included in token totals and decomposition.',
        'Positive tariff contribution and negative token-mix contribution mean the price gap is not caused by Astra emitting more total billable output.',
        'Dropping failed primary runs selects different tasks: GPT-5 inventory versus Astra support. That success-only price comparison is not task matched.',
        'Historical functionality retains its original probe/runtime epoch; no retrospective fresh uniform browser qualification is claimed.',
        'n=1 per task/model; no causal model capability ranking. Repriced fixed token usage is accounting, not a model-behavior counterfactual.']}
_write_json(run/'oracle-cost-analysis.json',result)
print(json.dumps({'fixed_token_tariff_matrix_usd':matrix,'decomposition':result['symmetric_accounting_decomposition_usd'],'new_llm_calls':0}))
