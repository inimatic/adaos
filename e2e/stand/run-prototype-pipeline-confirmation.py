"""Four primary-only requests on a revised guide, not a pooled old/new ablation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import read, source_fingerprints

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--source-run', type=Path, required=True)
parser.add_argument('--prior-run', type=Path, action='append', required=True)
parser.add_argument('--host', required=True)
parser.add_argument('--plan-only', action='store_true')
args = parser.parse_args()
repo = Path.cwd().resolve()
output = args.output.resolve()
source = args.source_run.resolve()
prior_runs = {path.resolve() for path in args.prior_run}
if output.exists() or any(not path.is_relative_to(repo / 'e2e/artifacts/builder') for path in [output, source, *prior_runs]):
    parser.error('New owned output and retained source/prior evidence required')
prior = sum(len(list(path.rglob('call-*-request.json'))) for path in prior_runs)
if prior + 4 > 32:
    parser.error('Four primary requests would exceed the approved combined budget')
frozen = source_fingerprints(repo)
cohorts = [('gpt5-inventory', 'gpt-5'), ('astra-inventory', 'gpt-6-astra')]
output.mkdir()
_write_json(output / 'preregistration.json', {'schema': 'adaos.e2e.prototype_pipeline_confirmation.v1',
    'max_paid_requests': 32, 'prior_attempted_requests': prior, 'per_candidate_request_cap': 1,
    'reasoning_effort': 'high', 'candidates': 4, 'runtime_source_fingerprints': frozen,
    'source_run': str(source.relative_to(repo)), 'source_plan': read(source / 'plan.json'),
    'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'cohorts': [{'name': name, 'model': model, 'task': 'inventory-procurement-ru'} for name, model in cohorts],
    'controls': ['same retained instruction, Brief, canvas and provider schema; new guide is a separate revision',
        'same model/high effort, native compiler, tools and public probes within each pair',
        'primary-only for all four candidates; no selective second attempts',
        'no pooling with the earlier repaired cohort; small n, no statistical significance']})


def generate(cohort):
    name, model = cohort
    assert source_fingerprints(repo) == frozen
    command = [sys.executable, 'e2e/stand/run-prototype-context-ablation.py', '--source-run', str(source),
        '--output', str(output / name), '--host', args.host, '--model', model, '--reasoning-effort', 'high',
        '--mcp', 'on', '--context-mode', 'pipeline', '--repetitions', '1', '--primary-only', '--defer-browser']
    if args.plan_only:
        command.append('--plan-only')
    with (output / (name + '-generation.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    print(json.dumps({'cohort': name, 'generation_exit': result.returncode}), flush=True)
    if result.returncode:
        return result.returncode
    plan = read(output / name / 'plan.json')
    original = read(source / 'plan.json')
    for key in ('instruction_sha256', 'brief_sha256', 'provider_schema_sha256', 'initial_webui_sha256'):
        assert plan[key] == original[key], 'Confirmation source parity failed: ' + key
    return 0


with ThreadPoolExecutor(max_workers=2) as pool:
    statuses = list(pool.map(generate, cohorts))
if any(statuses):
    raise SystemExit(1)
