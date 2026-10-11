"""Prospective two-model extension of the frozen five-arm Prototype pilot.

Native Builder inputs and controls are retained. Paid jobs use the central
worker from the outset; public MCP issuance remains on the RU plane. Dollar
reservations are estimates, not provider-enforced invoice ceilings.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import time
from unittest.mock import patch

from adaos.e2e.builder import _write_json
from adaos.e2e.composition_metrics import RATES, usage_record
from adaos.e2e.dollar_budget import DollarBudget
from adaos.e2e.prototype_context_ablation import read, source_fingerprints
from adaos.e2e.request_budget import RequestBudget
from adaos.sdk.llm import llm_client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--stage', choices=('plan', 'generate', 'repair'), required=True)
    parser.add_argument('--cohort')
    parser.add_argument('--samples', nargs='+')
    parser.add_argument('--profile', choices=('five-arm', 'compact-pair'), default='five-arm')
    args = parser.parse_args()
    repo, run = Path.cwd().resolve(), args.run.resolve()
    if not run.is_relative_to(repo / 'e2e/artifacts/builder'):
        parser.error('Owned E2E artifacts required')
    run.mkdir(parents=True, exist_ok=True)
    compact = args.profile == 'compact-pair'
    arms = ['full-mcp', 'task-aware-compact'] if compact else ['full-no-mcp', 'full-mcp', 'core-indexed', 'task-aware', 'oracle']
    orders = [arms, list(reversed(arms)), arms, list(reversed(arms))] if compact else [arms, list(reversed(arms)), [*arms[3:], *arms[:3]], [arms[2], arms[4], arms[0], arms[3], arms[1]]]
    cohorts = [{'id': prefix + '-' + task, 'model': model, 'task': task, 'arms': orders[i]}
        for i, (prefix, model, task) in enumerate([
            ('gpt55', 'gpt-5.5', 'inventory'), ('sol56', 'gpt-5.6-sol', 'inventory'),
            ('gpt55', 'gpt-5.5', 'support'), ('sol56', 'gpt-5.6-sol', 'support')])]
    ledger = RequestBudget(run / 'paid-request-budget.sqlite')
    dollars = DollarBudget(run / 'dollar-budget.sqlite', limit_usd=15)
    registration_path = run / 'preregistration.json'
    registration = {'schema': 'adaos.e2e.context_composition_model_extension.v1', 'cohorts': cohorts,
        'host': 'test_ctxcompact_builder_20261011' if compact else 'desktop-codex-navigation', 'reasoning_effort': 'high', 'additional_usd_limit': 15,
        'maximum_primary_attempts': 16 if compact else 20, 'maximum_repair_attempts': 4, 'judge_requests': 4 if compact else 0,
        'root_base_url': 'https://ru.api.inimatic.com', 'provider_worker': 'https://api.inimatic.com',
        'provider_max_attempts': 1, 'provider_timeout_s': 900, 'max_output_tokens': 128000,
        'max_tool_calls': 40, 'parallel_cohorts': 2, 'service_tier': 'default',
        'short_context_evidence': 'Aggregate native input usage <=272000 is a sufficient upper bound on each prompt; reject unproven short pricing.',
        'dollar_reservation': {'gpt-5.5': 4.1, 'gpt-5.6-sol': 2.9},
        'historical_reference': 'e2e/artifacts/builder/prototype-composition-series-20261011-02',
        'pipeline_epoch': 'Prospective current corrected compiler/client; historical repaired outcomes are not a fresh paired model baseline.',
        'endpoints': ['first native compiler + frozen Brief structural gates', 'final same desktop/mobile functional probes', 'all attempted token/USD cost, repairs separate'],
        'limitations': ['n=1 per cell; diagnostic pilot, not statistical evidence', 'Standard rate estimate, not Root invoice', 'No new paid UX judge'],
        'runtime_source_fingerprints': source_fingerprints(repo),
        'extra_sources': {p: 'sha256:' + hashlib.sha256((repo / p).read_bytes()).hexdigest()
            for p in ('e2e/stand/run-prototype-model-extension.py', 'src/adaos/e2e/composition_metrics.py', 'src/adaos/e2e/dollar_budget.py')}}
    if compact:
        registration.update(profile=args.profile, repetitions=2,
            baseline='Contemporaneous full+MCP only; same task, model, effort, provider schema, native compiler and MCP library.',
            context_comparison='Whole-fragment bounded task-aware projection with a complete decision-oriented index.',
            endpoints=['first native compiler + frozen Brief gates', 'same desktop/mobile functional and usability probes',
                'all attempted input/cached/output/reasoning tokens, USD and MCP calls', 'provider vs system phase timings',
                'blind screenshots (proxy only), position-swapped if budget permits'],
            limitations=['Two tasks, two repetitions per model/arm; diagnostic pilot, not broad generalization',
                'Standard rate estimate, not Root invoice; unknown charges remain reserved',
                'Automated usability checks and screenshot judge are not human usability calibration'])
    if registration_path.exists():
        if read(registration_path) != registration:
            raise ValueError('Prospective extension source/configuration drift')
    elif args.stage != 'plan':
        parser.error('Plan before paid requests')
    else:
        ledger.reallocate({'primary': 16 if compact else 20, 'repair': 4, 'judge': 4 if compact else 0},
            reason='Prospective $15 compact paired series' if compact else 'Prospective $15 model extension; no paid judges')
        _write_json(registration_path, registration)

    def execute(cohort):
        log = run / (cohort['id'] + '-' + args.stage + '.log')
        command = [sys.executable, __file__, '--run', str(run), '--stage', args.stage, '--cohort', cohort['id'], '--profile', args.profile]
        with log.open('a', encoding='utf-8') as sink:
            result = subprocess.run(command, stdout=sink, stderr=subprocess.STDOUT)
        print(json.dumps({'cohort': cohort['id'], 'stage': args.stage, 'exit_code': result.returncode, 'log': str(log.relative_to(repo))}), flush=True)
        if result.returncode:
            raise RuntimeError('Cohort stopped; inspect retained evidence, never blindly resubmit')

    if not args.cohort:
        if args.stage == 'plan':
            for cohort in cohorts:
                execute(cohort)
            parity = []
            for task in ('inventory', 'support'):
                pair = [run / c['id'] for c in cohorts if c['task'] == task]
                plans = [read(p / 'plan.json') for p in pair]
                for key in ('instruction_sha256', 'brief_sha256', 'provider_schema_sha256', 'initial_webui_sha256', 'library_digest'):
                    assert plans[0][key] == plans[1][key], key
                for arm in arms:
                    packets = [read(p / ('planned-' + arm + '-input.json')) for p in pair]
                    assert packets[0]['messages'] == packets[1]['messages'], (task, arm)
                    parity.append({'task': task, 'arm': arm, 'paired_messages_identical': True,
                        'initial_tokens_estimate': plans[0]['planned'][arm]['estimated_tokens']})
            _write_json(run / 'plan-summary.json', {'parity': parity, 'sources_frozen': True})
        elif args.stage == 'generate':
            with ThreadPoolExecutor(max_workers=2) as pool:
                for offset in (0, 2):
                    for future in as_completed([pool.submit(execute, c) for c in cohorts[offset:offset + 2]]):
                        future.result()
        else:
            assert all(read(run / c['id'] / 'results.json')['complete'] for c in cohorts)
            for cohort in cohorts:
                execute(cohort)
        _write_json(run / 'dollar-budget.json', dollars.snapshot())
        _write_json(run / 'paid-request-budget.json', ledger.snapshot())
        return

    cohort = next(c for c in cohorts if c['id'] == args.cohort)
    directory = run / cohort['id']
    if args.stage == 'repair':
        script = 'e2e/stand/repair-prototype-pipeline-cohort.py'
        arguments = ['--run', str(directory)]
        if args.samples:
            arguments += ['--samples', *args.samples]
    else:
        script = 'e2e/stand/run-prototype-context-ablation.py'
        arguments = ['--source-run', 'e2e/artifacts/builder/prototype-pipeline-confirmation-20261010-01/gpt5-inventory',
            '--output', str(directory), '--model', cohort['model'], '--reasoning-effort', 'high', '--repetitions', '2' if compact else '1',
            '--mcp', 'on', '--context-mode', 'composition', '--arms', *cohort['arms'],
            '--request-budget-db', str(ledger.path), '--root-base-url', registration['root_base_url'],
            '--defer-browser', '--host', registration['host']]
        if not compact:
            arguments += ['--oracle-file', 'e2e/builder/development/context-ablation/prototype-guidance-oracles.json']
        if cohort['task'] == 'support':
            arguments += ['--task-file', 'e2e/builder/development/context-ablation/support-desk-ru.json']
        if args.stage == 'plan':
            arguments += ['--plan-only']
        if directory.exists():
            arguments += ['--resume']
    native_submit = llm_client._submit_root_job
    native_get = llm_client.get_response_job
    worker = registration['provider_worker']
    job_keys = {}

    def observe(envelope, key=None):
        key = key or job_keys.get(envelope.get('job_id'))
        if key and envelope.get('status') in {'succeeded', 'failed', 'cancelled'}:
            usage = usage_record(envelope, cohort['model'])
            if usage is not None:
                dollars.observe(key, usd=usage['usd_standard_estimate_without_unreported_cache_writes'])
                print(json.dumps({'stage': 'accounted', 'model': cohort['model'], 'job': envelope.get('job_id'),
                    'usd': usage['usd_standard_estimate_without_unreported_cache_writes'],
                    'short_context_certified': usage['input_tokens'] <= 272000}), flush=True)
                if dollars.snapshot()['remaining_estimated_usd'] < 0:
                    raise RuntimeError('Budget exceeded estimated ceiling; stop further paid requests')
        return envelope

    def submit(payload, **options):
        if payload.get('model') != cohort['model'] or payload.get('reasoning') != {'effort': 'high'}:
            raise ValueError('Explicit model/effort changed before provider submission')
        key = options['request_id']
        reserve = registration['dollar_reservation'][cohort['model']]
        deadline = time.monotonic() + 1200
        while True:
            try:
                dollars.reserve(key, reserve_usd=reserve)
                break
            except ValueError as error:
                if str(error) != 'Insufficient additional dollar budget':
                    raise
                snapshot = dollars.snapshot()
                if not any(r['observed_usd'] is None for r in snapshot['reservations']) or time.monotonic() >= deadline:
                    raise
                time.sleep(2)
        payload = {**payload, 'service_tier': 'default'}
        with patch.object(llm_client, '_root_llm_base_urls', lambda *_a, **_k: [worker]):
            envelope = native_submit(payload, **options)
        job_keys[envelope.get('job_id')] = key
        return observe(envelope, key)

    def get(job_id, **options):
        options['base_url'] = worker
        return observe(native_get(job_id, **options))

    sys.argv = [script, *arguments]
    with patch.object(llm_client, '_submit_root_job', submit), patch.object(llm_client, 'get_response_job', get):
        runpy.run_path(script, run_name='__main__')


if __name__ == '__main__':
    main()
