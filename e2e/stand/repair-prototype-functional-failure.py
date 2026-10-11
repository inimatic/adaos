"""One supplemental native repair from an independent functional finding.

The matched primary/repair series remains immutable. This intervention has its
own receipts and shares both paid-request and dollar ledgers with that series.
It is not a replacement score for a failed primary or a new matched model arm.
"""
import argparse
import hashlib
import inspect
import json
import logging
import os
import time
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

from adaos.e2e.builder import _write_json
from adaos.e2e.composition_metrics import usage_record
from adaos.e2e.dollar_budget import DollarBudget
from adaos.e2e.prototype_context_ablation import (compile_experiment_messages, evaluation_model_controls,
    generation_configuration, native_builder, read, source_fingerprints)
from adaos.e2e.prototype_task_acceptance import task_mutation_contract
from adaos.e2e.request_budget import RequestBudget
from adaos.services.builder.prototype_mcp import redact_credentials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--cohort', required=True)
    parser.add_argument('--sample', required=True)
    parser.add_argument('--label', default='v6')
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    repo, run = Path.cwd().resolve(), args.run.resolve()
    if not args.label.replace('-', '').isalnum() or len(args.label) > 32:
        parser.error('Simple bounded intervention label required')
    if not run.is_relative_to(repo/'e2e/artifacts/builder'):
        parser.error('Owned series required')
    registration = read(run/'preregistration.json')
    cohort = next(item for item in registration['cohorts'] if item['id'] == args.cohort)
    directory = run/cohort['id']
    original = (directory/args.sample).resolve()
    if original.parent != directory:
        parser.error('Exact direct child sample required')
    plan, brief = read(directory/'plan.json'), read(directory/'source-brief.json')
    rows = read(directory/'results.json')['samples']
    row = next(item for item in rows if f"{item['order']:02d}-{item['arm']}" == args.sample)
    if len(list(original.glob('call-*-request.json'))) != 1:
        raise ValueError('Only a primary with no prior native repair is eligible')
    candidate = read(original/'generation-result.json')
    findings = task_mutation_contract(candidate.get('semantic_document') or {}, brief, task=plan['task'])
    if not candidate.get('ok') or findings['ok']:
        raise ValueError('A compiled candidate with a independently proven task failure is required')
    output = run/('posthoc-functional-repair-'+args.label)/cohort['id']/args.sample
    output.mkdir(parents=True, exist_ok=True)
    configuration = {'schema':'adaos.e2e.functional_repair.v1', 'original_sample':str(original.relative_to(repo)),
        'original_candidate_sha256':hashlib.sha256((original/'generation-result.json').read_bytes()).hexdigest(),
        'findings':findings, 'sources':source_fingerprints(repo),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'maximum_new_calls':1, 'model':cohort['model'], 'reasoning_effort':'high', 'label':args.label,
        'context_arm':row['arm'], 'primary_series_unchanged':True,
        'scope':'Supplemental post-hoc intervention; do not replace the matched first/final primary series score'}
    if (output/'configuration.json').exists() and read(output/'configuration.json') != configuration:
        raise ValueError('Supplemental repair plan drift')
    _write_json(output/'configuration.json', configuration)
    if args.plan_only:
        print(json.dumps({'eligible':True,'new_llm_calls':0,'output':str(output)})); return
    if (output/'result.json').exists():
        print(json.dumps({'complete':True,'result':read(output/'result.json')['ok'],'new_llm_calls':0})); return
    if (output/'budget.json').exists():
        raise RuntimeError('Retained/ambiguous submission: recover its job; NEVER automatically submit again')
    load_dotenv(); logging.disable(logging.INFO)
    if os.getenv('ENV_TYPE') != 'dev':
        parser.error('DEV only')
    from adaos.apps.cli.app import Settings, init_ctx
    from adaos.services.agent_context import get_ctx
    from adaos.sdk.llm import llm_client
    init_ctx(Settings.from_sources())
    builder = native_builder(Path(get_ctx().paths.dev_skills_dir())/'builder_skill/handlers/main.py')
    environment, options, meta = generation_configuration(cohort['model'], 'high')
    os.environ.update(environment)
    os.environ['ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED'] = '1'
    evaluation_model_controls(builder, cohort['model'], 'high')
    meta.update(locale='ru', prototype_brief=brief, builder_e2e_generation_contract='semantic.v2', builder_semantic_compiler=True)
    session = read(original/'session.json')
    baseline = session['initial_webui']
    primary = read(original/'call-01-terminal.json')
    original_request = read(original/'call-01-request.json')
    native_messages, native_submit, native_wait = builder._builder_llm_messages, llm_client.submit_response_job, llm_client.wait_response_job
    native_root_submit = llm_client._submit_root_job
    budget = RequestBudget(run/'paid-request-budget.sqlite')
    dollars = DollarBudget(run/'dollar-budget.sqlite', limit_usd=15)
    calls = []

    def messages(request, prompt):
        raw, purposes = native_messages(request, prompt)
        projected, receipt = compile_experiment_messages(raw, strategy=row['arm'], generation_options=options,
            context_mode=plan['context_mode'], mcp_available=True)
        _write_json(output/'delivery.json',receipt)
        return projected, purposes

    def observe(envelope):
        _write_json(output/'terminal.json',redact_credentials(envelope))
        budget.observe(calls[0]['ordinal'],job_id=envelope.get('job_id'),status=envelope.get('status','terminal'))
        usage=usage_record(envelope,cohort['model'])
        if usage is not None:
            dollars.observe(calls[0]['dollar_key'],usd=usage['usd_standard_estimate_without_unreported_cache_writes'])
            _write_json(output/'usage.json',usage)
        _write_json(output/'dollar-budget.json',dollars.snapshot())
        if dollars.snapshot()['remaining_estimated_usd'] < 0:
            raise RuntimeError('Stop: observed standard-rate ceiling exceeded')
        if envelope.get('status') == 'succeeded':
            response=envelope.get('response') or {}
            if not str(response.get('model')).startswith(cohort['model']) or response.get('reasoning',{}).get('effort') != 'high':
                raise ValueError('Supplemental provider model/effort substitution')
            if envelope.get('telemetry',{}).get('provider_attempts') != 1:
                raise ValueError('Supplemental provider retry cap not honored')
        return envelope

    def submit(value, **kwargs):
        if calls or (output/'budget.json').exists():
            raise RuntimeError('One supplemental paid call cap')
        kwargs.update(provider_max_attempts=1,provider_timeout_s=900,timeout=60)
        inspect.signature(native_submit).bind(value, **kwargs)
        if kwargs.get('model') != cohort['model'] or kwargs.get('reasoning') != {'effort':'high'}:
            raise ValueError('Supplemental native model/effort drift before paid submission')
        key=kwargs['request_id']
        dollars.reserve(key,reserve_usd=registration['dollar_reservation'][cohort['model']])
        slot=budget.reserve('repair',str(output.relative_to(repo))+':functional-repair')
        receipt={'ordinal':slot,'dollar_key':key}; calls.append(receipt)
        _write_json(output/'budget.json',receipt)
        _write_json(output/'request.json',redact_credentials({'messages':value,'options':kwargs}))
        envelope=native_submit(value,**kwargs)
        _write_json(output/'submission.json',redact_credentials(envelope))
        budget.observe(slot,job_id=envelope.get('job_id'),status=envelope.get('status','submitted'))
        print(json.dumps({'submitted':envelope.get('job_id'),'model':cohort['model']}),flush=True)
        return observe(envelope) if envelope.get('status') in {'succeeded','failed','cancelled'} else envelope

    def wait(job_id, **kwargs):
        kwargs.update(request_timeout=15,base_url=registration['provider_worker'])
        return observe(native_wait(job_id,**kwargs))

    def root_submit(payload, **kwargs):
        return native_root_submit({**payload, 'service_tier':'default'}, **kwargs)

    started=time.perf_counter()
    validation={'ok':False,'error':'independent_task_contract_failed',**findings,
                'detail':'Accepted user task is not executable: create a purchase request and approve that same request, not just add its child line.'}
    with patch.object(builder,'_builder_llm_messages',messages), patch.object(llm_client,'submit_response_job',submit), \
         patch.object(llm_client,'wait_response_job',wait), \
         patch.object(llm_client,'_submit_root_job',root_submit), \
         patch.object(llm_client,'_root_llm_base_urls',lambda *_a,**_k:[registration['provider_worker']]):
        corrected=builder._repair_llm_semantic_transform_output(session=session,
            instruction=(directory/'instruction.txt').read_text(encoding='utf-8'),
            previous_preview=baseline.get('preview_state') or {},output_text=primary['output_text'],validation_error=validation,
            prototype_brief=brief,project_ref=session['project_ref'],
            request_id=original_request['options']['request_id']+':functional-'+args.label,job_id=primary['job_id'],
            output_mode='semantic_v2',_meta=meta)
    if not calls or not (output/'terminal.json').exists():
        raise RuntimeError('No terminal receipt: do not resubmit automatically')
    _write_json(output/'generation-result.json',corrected)
    _write_json(output/'session.json',session)
    checked=task_mutation_contract(corrected.get('semantic_document') or {},brief,task=plan['task'])
    result={'ok':corrected.get('ok') is True and checked['ok'],'compiler_ok':corrected.get('ok'),
            'task_contract':checked,'elapsed_seconds':round(time.perf_counter()-started,3),'new_llm_calls':len(calls),
            'primary_series_unchanged':True,'scope':configuration['scope']}
    _write_json(output/'result.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
