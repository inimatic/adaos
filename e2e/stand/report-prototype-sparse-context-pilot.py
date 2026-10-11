"""Account every additional attempt and keep transport/intervention cohorts separate."""
import argparse
from pathlib import Path
from adaos.e2e.builder import _write_json
from adaos.e2e.composition_metrics import execution_seconds, usage_record
from adaos.e2e.prototype_context_ablation import read
from adaos.e2e.request_budget import RequestBudget
from adaos.e2e.dollar_budget import DollarBudget

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', type=Path, required=True)
parser.add_argument('--browser-label', default='release')
args = parser.parse_args()
run = args.run.resolve()
if not run.is_relative_to(Path.cwd() / 'e2e/artifacts/builder'):
    parser.error('Owned additional E2E run required')
rows = []
seen = set()
qualification = run / ('browser-posthoc-' + args.browser_label) / 'results.json'
browser = read(qualification) if qualification.exists() else {'complete': False, 'samples': []}
qualified = {row['candidate']: row for row in browser['samples']}
for terminal_path in sorted(run.glob('*/*/terminal.json')):
    directory = terminal_path.parent
    terminal = read(terminal_path)
    if terminal['job_id'] in seen:
        raise ValueError('Duplicate provider job')
    seen.add(terminal['job_id'])
    receipt = read(directory / 'result.json') if (directory / 'result.json').exists() else {}
    model = receipt.get('model') or ('gpt-6-astra' if directory.name.startswith('gpt-6-astra') else 'gpt-5')
    request = read(directory / 'request.json')
    assert request['options']['model'] == model and request['options']['provider_max_attempts'] == 1
    usage = usage_record(terminal, model)
    cohort = ('dependency_intervention' if directory.name.endswith('-v4') else 'explicit_discovery_retry'
        if directory.name.endswith('-v3') else 'primary_named_schema' if directory.name.endswith('-v2') else 'invalid_adapter_schema')
    candidate = read(directory / 'candidate.json') if (directory / 'candidate.json').exists() else {}
    checked = qualified.get(directory.name)
    rows.append({'candidate': directory.name, 'task': directory.parent.name, 'model': model,
        'arm': receipt.get('arm') or ('task-aware' if 'task-aware' in directory.name else 'full-mcp'),
        'cohort': cohort, 'job_id': terminal['job_id'], 'status': terminal['status'], 'usage': usage,
        'native_compile_pass': receipt.get('native_compile_pass'),
        'scored_as_model_quality': usage is not None and receipt.get('native_compile_pass') is not None,
        'failure_stage': receipt.get('failure_stage') or ('invalid_adapter_schema' if cohort == 'invalid_adapter_schema' else None),
        'compiler_error': candidate.get('detail'), 'mcp': receipt.get('mcp'), 'retrieval': receipt.get('retrieval'),
        'root_execution_seconds': execution_seconds((terminal.get('telemetry') or {}).get('timing') or {}),
        'browser_checked': checked is not None, 'functional_browser_pass': checked.get('ok') if checked else None,
        'browser_probes': checked.get('probes') if checked else None,
        'initial_context': receipt.get('initial_context')})
ledger = DollarBudget(run / 'dollar-budget.sqlite', limit_usd=8).snapshot()
known = sum(row['usage']['usd_standard_estimate_without_unreported_cache_writes'] for row in rows if row['usage'])
requests = RequestBudget(run / 'request-budget.sqlite', maximum=8, limits={'primary': 8, 'repair': 0, 'judge': 0}).snapshot()
assert len(rows) == 8 and len(seen) == 8
assert abs(known - sum(reservation['observed_usd'] or 0 for reservation in ledger['reservations'])) < 1e-6
report = {'schema': 'adaos.e2e.sparse_context_report.v1', 'samples': rows,
    'qualification_complete': browser['complete'], 'paid_attempts': len(rows),
    'successful_model_responses': sum(row['status'] == 'succeeded' for row in rows),
    'native_compile_pass': sum(row['native_compile_pass'] is True for row in rows),
    'functional_browser_pass': sum(row['functional_browser_pass'] is True for row in rows),
    'usd_known_estimate': round(known, 6), 'dollar_budget': ledger, 'request_budget': requests,
    'amendments': {path.name: read(path) for path in sorted(run.glob('*amendment*.json'))},
    'limitations': ['One compact held-out task actually generated; the second planned task was not run.',
        'Transport recovery and prospective dependency interventions are separate, not extra independent repetitions.',
        'Unreported provider usage stays reserved, never reported as zero cost.',
        'Browser qualification evaluates every retained compilable answer, not the initially failed answers.',
        'Different task/output/tool limits from rich main series; do not pool or claim statistical superiority.']}
_write_json(run / 'report.json', report)
print({key: report[key] for key in ('paid_attempts', 'successful_model_responses', 'native_compile_pass',
    'functional_browser_pass', 'qualification_complete', 'usd_known_estimate')})
