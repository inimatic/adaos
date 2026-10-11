"""Execute the preregistered single native semantic repair, retaining primaries.

The internal generation helper does not dispatch the outer Builder repair stage.
This adapter executes that same stage for EVERY failed compiler candidate, never
selecting by prospective quality or rewriting its original generation evidence.
"""
import argparse
import copy
from functools import wraps
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from unittest.mock import patch
from dotenv import load_dotenv

from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import (compile_experiment_messages, evaluation_model_controls,
    generation_configuration, native_builder, read, source_fingerprints)
from adaos.services.builder.prototype_mcp import redact_credentials

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', type=Path, required=True)
parser.add_argument('--samples', nargs='+')
args = parser.parse_args()
load_dotenv()
logging.disable(logging.INFO)
from adaos.apps.cli.app import Settings, init_ctx
from adaos.services.agent_context import get_ctx
from adaos.sdk.llm import llm_client
init_ctx(Settings.from_sources())
repo = Path.cwd().resolve()
run = args.run.resolve()
if not run.is_relative_to(repo / 'e2e/artifacts/builder'):
    parser.error('Owned E2E cohort required')
plan, results = read(run / 'plan.json'), read(run / 'results.json')
if plan['context_mode'] not in {'pipeline', 'composition'} or plan['max_attempts'] != 2 or not results['complete']:
    parser.error('Complete preregistered two-attempt pipeline cohort required')
composition = plan['context_mode'] == 'composition'
if composition:
    from adaos.e2e.request_budget import RequestBudget, RequestBudgetExceeded
    budget = RequestBudget(plan['request_budget_db'])
    llm_client._root_llm_base_urls = lambda *_args, **_kwargs: [plan['root_base_url']]
    os.environ['ADAOS_ROOT_LLM_BASE_URL'] = plan['root_base_url']
    os.environ['ADAOS_ROOT_LLM_PREFER_GLOBAL'] = '0'
from adaos.e2e.runtime_amendment import validated_sources
validated_sources(repo, plan, run)
ctx = get_ctx()
handler = Path(ctx.paths.dev_skills_dir()) / 'builder_skill/handlers/main.py'
if 'sha256:' + hashlib.sha256(handler.read_bytes()).hexdigest() != plan['native_builder_sha256']:
    raise ValueError('Frozen native handler drift')
builder = native_builder(handler)
environment, options, meta = generation_configuration(plan['model'], plan['effort'])
os.environ.update(environment)
os.environ['ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED'] = '1'
os.environ['ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY'] = 'full'
evaluation_model_controls(builder, plan['model'], plan['effort'])
brief = read(run / 'source-brief.json')
instruction = (run / 'instruction.txt').read_text(encoding='utf-8')
meta.update(locale='ru', prototype_brief=brief, builder_e2e_generation_contract='semantic.v2', builder_semantic_compiler=True)
configuration = {'schema': 'adaos.e2e.prototype_pipeline_repair_stage.v1',
    'max_new_calls_per_failed_compiler_candidate': 1,
    'eligibility': 'all original compiler failures with a complete provider candidate and fewer than two requests',
    'original_outputs_preserved': True, 'implementation_unchanged': True,
    'executor_correction': 'Use the native outer dispatch semantic-repair stage omitted by the generation-helper adapter',
    'adapter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
target_plan = run / 'repair-stage.json'
if target_plan.exists() and read(target_plan) != configuration:
    raise ValueError('Repair stage configuration drift')
_write_json(target_plan, configuration)
native_messages, native_submit, native_wait = builder._builder_llm_messages, llm_client.submit_response_job, llm_client.wait_response_job
for row in results['samples']:
    sample = run / f"{row['order']:02d}-{row['arm']}"
    if args.samples and sample.name not in args.samples:
        continue
    if row['generation_ok'] or (sample / 'planned-repair-result.json').exists():
        continue
    if len(list(sample.glob('call-*-request.json'))) >= 2:
        continue
    primary = read(sample / 'call-01-terminal.json')
    raw = primary.get('output_text') or ''
    if primary.get('status') != 'succeeded' or not raw:
        continue
    session = read(sample / 'session.json')
    baseline = session['initial_webui']
    try:
        builder._parse_llm_webui_transform_output(output_text=raw,
            previous_preview=baseline.get('preview_state') or {}, before_webui=baseline,
            prototype_brief=brief, project_ref=session['project_ref'], output_mode='semantic_v2')
    except Exception as error:
        validation = {'ok': False, 'error': 'semantic_candidate_invalid', 'detail': str(error),
            'findings': getattr(error, 'findings', [])}
    else:
        raise ValueError('An original compiler failure unexpectedly changed without a repair')
    _write_json(sample / 'planned-repair-findings.json', validation)
    calls = []
    os.environ['ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED'] = '0' if row['arm'] == 'full-no-mcp' else '1'

    def project(request, prompt):
        messages, purposes = native_messages(request, prompt)
        projected, receipt = compile_experiment_messages(messages, strategy=row['arm'], generation_options=options,
            context_mode=plan['context_mode'], mcp_available=row['arm'] != 'full-no-mcp',
            oracle_fragment_ids=plan.get('oracle_fragment_ids'))
        _write_json(sample / 'delivery-02.json', receipt)
        return projected, purposes

    @wraps(native_submit)
    def submit(messages, **kwargs):
        if calls or len(list(sample.glob('call-*-request.json'))) >= 2:
            raise RuntimeError('Per-candidate two-request cap: refusing another model')
        if composition:
            kwargs['provider_max_attempts'] = 1
            kwargs['timeout'] = 60
            slot = budget.reserve('repair', str(sample.relative_to(repo)) + ':repair')
            _write_json(sample / 'budget-repair.json', {'ordinal':slot, 'kind':'repair'})
            calls.append(slot)
        else:
            calls.append(True)
        _write_json(sample / 'call-02-request.json', redact_credentials({'messages': messages, 'options': kwargs}))
        response = native_submit(messages, **kwargs)
        if composition:
            budget.observe(slot, job_id=response.get('job_id'), status=response.get('status', 'submitted'))
        _write_json(sample / 'call-02-submission.json', redact_credentials(response))
        if response.get('status') == 'succeeded':
            _write_json(sample / 'call-02-terminal.json', redact_credentials(response))
        print(json.dumps({'stage': 'planned_repair_submitted', 'sample': sample.name, 'job': response.get('job_id')}), flush=True)
        return response

    @wraps(native_wait)
    def wait(job_id, **kwargs):
        if composition:kwargs['request_timeout'] = 15
        response = native_wait(job_id, **kwargs)
        _write_json(sample / 'call-02-terminal.json', redact_credentials(response))
        if composition:
            if response.get('telemetry', {}).get('provider_max_attempts') != 1 or response.get('telemetry', {}).get('provider_attempts') != 1:
                raise ValueError('Repair provider attempt cap not honored')
            budget.observe(calls[-1], job_id=job_id, status=response.get('status', 'terminal'))
        return response

    request = read(sample / 'call-01-request.json')
    if composition and budget.snapshot()['counts']['repair'] >= budget.limits['repair']:
        _write_json(sample / 'repair-budget-censored.json', {'eligible':True, 'reason':'Explicit fixed-total budget allocation; repair reserve exhausted', 'not_a_model_failure':True})
        continue
    started = time.perf_counter()
    with patch.object(builder, '_builder_llm_messages', project), patch.object(llm_client, 'submit_response_job', submit), patch.object(llm_client, 'wait_response_job', wait):
        corrected = builder._repair_llm_semantic_transform_output(session=session, instruction=instruction,
            previous_preview=baseline.get('preview_state') or {}, output_text=raw, validation_error=validation,
            prototype_brief=brief, project_ref=session['project_ref'], request_id=request['options']['request_id'],
            job_id=primary['job_id'], output_mode='semantic_v2', _meta=meta)
    _write_json(sample / 'planned-repair-result.json', corrected)
    _write_json(sample / 'repair-timings.json', {'generation_seconds':round(time.perf_counter()-started,3), 'calls':len(calls)})
    if calls and not (sample / 'call-02-terminal.json').exists():
        raise RuntimeError('Ambiguous repair delivery; recover retained request before continuing')
    print(json.dumps({'stage': 'planned_repair_complete', 'sample': sample.name, 'ok': corrected.get('ok'), 'new_calls': len(calls)}), flush=True)
