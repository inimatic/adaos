"""Read retained extension receipts and reconcile dollars without model calls."""
import argparse
import json
from pathlib import Path

from adaos.e2e.builder import _write_json
from adaos.e2e.composition_metrics import usage_record
from adaos.e2e.dollar_budget import DollarBudget
from adaos.e2e.prototype_context_ablation import read
from adaos.e2e.request_budget import RequestBudget


def candidate_outcome(directory):
    result_path = directory / 'result.json'
    result = read(result_path) if result_path.exists() else {}
    final_path = directory / 'planned-repair-result.json'
    if not final_path.exists():
        final_path = directory / 'generation-result.json'
    final = read(final_path) if final_path.exists() else {}
    return {'first_compile_pass': result.get('generation_ok'),
        'first_structural_pass': result.get('structural', {}).get('ok'),
        'final_compile_pass': final.get('ok'), 'final_candidate_source': final_path.name if final_path.exists() else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_relative_to(Path.cwd() / 'e2e/artifacts/builder'):
        parser.error('Owned E2E artifacts required')
    registration = read(run / 'preregistration.json')
    dollars = DollarBudget(run / 'dollar-budget.sqlite', limit_usd=registration['additional_usd_limit'])
    samples = []
    candidates = {}
    jobs = set()
    for cohort in registration['cohorts']:
        directory = run / cohort['id']
        for terminal_path in sorted(directory.glob('*/call-*-terminal.json')):
            terminal = read(terminal_path)
            if terminal['job_id'] in jobs:
                raise ValueError('Duplicate job receipt')
            jobs.add(terminal['job_id'])
            request = read(terminal_path.with_name(terminal_path.name.replace('-terminal', '-request')))
            usage = usage_record(terminal, cohort['model'])
            if usage is not None:
                dollars.observe(request['options']['request_id'], usd=usage['usd_standard_estimate_without_unreported_cache_writes'])
            result_path = terminal_path.parent / 'result.json'
            result = read(result_path) if result_path.exists() else {}
            key = (cohort['id'], terminal_path.parent.name)
            candidates[key] = {'cohort': cohort['id'], 'sample': terminal_path.parent.name,
                **candidate_outcome(terminal_path.parent)}
            samples.append({'cohort': cohort['id'], 'sample': terminal_path.parent.name,
                'call': terminal_path.stem, 'status': terminal['status'], 'usage': usage,
                'sample_primary_compile_pass': result.get('generation_ok'),
                'sample_primary_structural_pass': result.get('structural', {}).get('ok'),
                'provider_model': (terminal.get('response') or {}).get('model'),
                'service_tier': (terminal.get('response') or {}).get('service_tier')})
    value = {'retained_terminal_calls': len(samples), 'samples': samples, 'candidates': list(candidates.values()),
        'dollar_budget': dollars.snapshot(), 'request_budget': RequestBudget(run / 'paid-request-budget.sqlite').snapshot()}
    _write_json(run / 'progress.json', value)
    _write_json(run / 'dollar-budget.json', value['dollar_budget'])
    print(json.dumps({'terminal_calls': len(samples),
        'distinct_candidates': len(candidates),
        'first_compile_pass': sum(s['first_compile_pass'] is True for s in candidates.values()),
        'first_structural_pass': sum(s['first_structural_pass'] is True for s in candidates.values()),
        'final_compile_pass': sum(s['final_compile_pass'] is True for s in candidates.values()),
        'committed_estimated_usd': value['dollar_budget']['committed_estimated_usd'],
        'held_reservations': sum(r['observed_usd'] is None for r in value['dollar_budget']['reservations']),
        'attempts': len(value['request_budget']['attempts'])}), flush=True)


if __name__ == '__main__':
    main()
