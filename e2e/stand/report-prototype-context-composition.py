"""Reconcile five-arm outcomes, all attempted usage, MCP intervals and delivery.

Standard-rate estimates are not Root invoices. Unknown cache-write charges or
service-tier overrides are explicitly not inferred. Cached/reasoning tokens are
subsets of input/output, NEVER additional tokens. No new LLM requests here.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import acceptance, read
from adaos.e2e.prototype_mcp_eval import mcp_trace
from adaos.e2e.request_budget import RequestBudget
from adaos.e2e.composition_metrics import RATES, execution_seconds, qualification_outcome, reconcile_probe_rechecks, retrieval_behavior, sum_execution_seconds, usage_record
from adaos.services.builder.prototype_mcp import library_snapshot

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',type=Path,required=True)
parser.add_argument('--partial',action='store_true')
parser.add_argument('--browser-label', default='', help='Separate uniform post-hoc browser qualification label')
parser.add_argument('--compiler-label', default='', help='Separate deterministic replay label')
parser.add_argument('--amendment', default='uniform-replay-amendment.json')
args=parser.parse_args(); run=args.run.resolve()
if not run.is_relative_to(Path.cwd()/'e2e/artifacts/builder'):parser.error('Owned E2E series required')
registration=read(run/'preregistration.json')
rates=RATES
jobs=set(); rows=[]; groups=defaultdict(list); unknown=[]; browser_complete=[]
browser_directory='browser-posthoc-'+args.browser_label if args.browser_label else 'browser-requalification-mcp'
amendment_path=(run/args.amendment).resolve()
if amendment_path.parent != run:parser.error('Amendment must be a receipt in the run directory')
uniform=read(amendment_path) if amendment_path.exists() else {}
primary_replay_path=run/('compiler-replay-'+uniform.get('primary_compiler_label',''))/'report.json'
all_fragments=[fragment['id'] for fragment in library_snapshot()['fragments']]
corrected_primary={(r['cohort'],r['sample']):r for r in read(primary_replay_path)['samples']} if primary_replay_path.exists() else {}
recheck_batches = []
for path in sorted(run.glob('browser-failure-rechecks*/results.json')):
    evidence = read(path)
    checked = []
    for row in evidence['checks']:
        folder = run / row['probe_artifact'] if row.get('probe_artifact') else path.parent / row['cohort'] / row['sample'] / row['probe']
        if not folder.resolve().is_relative_to(run):
            raise ValueError('Recheck evidence escaped owned run')
        checked.append({**row, 'valid_diagnostic': (folder / 'review.json').exists(),
            'artifact': str(folder.relative_to(run))})
    # Legacy two-case diagnostic scripts wrote only after each completed probe;
    # both expected case receipts are required to establish a complete batch.
    complete = evidence.get('complete', len(checked) == 2 and {(r['cohort'], r['sample']) for r in checked}
        == {('gpt5-support', '04-full-mcp'), ('astra-inventory', '01-oracle')})
    recheck_batches.append({'label': path.parent.name, 'complete': complete, 'checks': checked})

for cohort in registration['cohorts']:
    directory=run/cohort['id']; result_path=directory/'results.json'
    if not result_path.exists():
        if args.partial:continue
        parser.error('Incomplete generation')
    results=read(result_path); plan=read(directory/'plan.json'); brief=read(directory/'source-brief.json')
    qualified=directory/browser_directory/'results.json'
    browser=read(qualified) if qualified.exists() else {'samples':[],'complete':False}
    browser_complete.append(browser['complete'])
    if not args.partial and (not results['complete'] or not browser['complete']):parser.error('Generation and browser qualification must be complete')
    by_sample={item['sample']:item for item in browser['samples']}
    for row in results['samples']:
        sample_id=f"{row['order']:02d}-{row['arm']}"; sample=directory/sample_id
        candidate_path=sample/'planned-repair-result.json'
        candidate=read(candidate_path) if candidate_path.exists() else read(sample/'generation-result.json')
        replay_path=run/('compiler-replay-'+args.compiler_label)/cohort['id']/sample_id/'generation-result.json'
        if args.compiler_label and replay_path.exists():candidate=read(replay_path)
        structural=acceptance(candidate,brief,task=plan['task'])
        timings=[]; traces=[]; usage=[]; attempted=len(list(sample.glob('call-*-request.json')))
        retained=[]
        for terminal in sorted(sample.glob('call-*-terminal.json')):
            envelope=read(terminal); request=read(terminal.with_name(terminal.name.replace('-terminal','-request')))
            submission_path=terminal.with_name(terminal.name.replace('-terminal','-submission'))
            submission=read(submission_path) if submission_path.exists() else {}
            if envelope['job_id'] in jobs:raise ValueError('Duplicate job would double count cost')
            jobs.add(envelope['job_id'])
            assert request['options']['model']==cohort['model'] and request['options']['reasoning']=={'effort':'high'}
            assert request['options']['provider_max_attempts']==1
            actual=(envelope.get('response') or {}).get('model')
            if envelope.get('status')=='succeeded' and not str(actual).startswith(cohort['model']):raise ValueError('Provider model substitution')
            value=usage_record(envelope,cohort['model'])
            if value:usage.append(value)
            else:unknown.append(envelope['job_id'])
            timing=envelope.get('telemetry',{}).get('timing',{})
            timings.append(timing); traces.append(mcp_trace(envelope))
            retained.append({'job_id':envelope['job_id'],'status':envelope['status'],'actual_model':actual,
                'actual_service_tier':(envelope.get('response') or {}).get('service_tier'),
                'actual_reasoning':(envelope.get('response') or {}).get('reasoning'),
                'short_context_certified_by_aggregate_input':value['input_tokens']<=272000 if value else None,
                'provider_attempts':envelope.get('telemetry',{}).get('provider_attempts'),'timing':timing,'usage':value,
                'submission_client':submission.get('_client',{}),
                'delivery_entry':submission.get('_client',{}).get('base_url'),
                'queue_wait_ms':timing.get('queue_ms')})
        numeric=('input_tokens','cached_input_tokens','cache_write_tokens','output_tokens','reasoning_tokens','output_excluding_reasoning_tokens','total_tokens','usd_standard_estimate_without_unreported_cache_writes','usd_uncached_same_tokens_counterfactual')
        totals={key:sum(v[key] for v in usage) for key in numeric}
        primary_usage=retained[0]['usage'] if retained else None
        repair_usage={key:sum((r['usage'] or {}).get(key,0) for r in retained[1:]) for key in numeric}
        total_wall=row['generation_seconds']
        repair_timing=sample/'repair-timings.json'
        if repair_timing.exists():total_wall+=read(repair_timing)['generation_seconds']
        qualified_row=by_sample.get(sample_id,{})
        targeted = [{'complete': batch['complete'], 'checks': [check for check in batch['checks']
            if check['cohort'] == cohort['id'] and check['sample'] == sample_id]} for batch in recheck_batches]
        rechecked_probes = reconcile_probe_rechecks(qualified_row.get('probes', {}), targeted)
        outcome = qualification_outcome(qualified_row, structural_ok=structural['ok'])
        browser_verdict = outcome['browser_pass']
        functional_verdict = outcome['functional_e2e_pass']
        rechecked_verdict = (all(code == 0 for code in rechecked_probes.values()) and structural['ok']) if rechecked_probes else functional_verdict
        delivery=qualified_row.get('timings',{})
        materialization_path=directory/browser_directory/sample_id/'preview-materialization.json'
        owner=read(materialization_path).get('materialization',{}) if materialization_path.exists() else {}
        review_path=directory/browser_directory/sample_id/'prototype-review'/'review.json'
        reviews=read(review_path).get('samples',[]) if review_path.exists() else []
        usability={item['layout']:item.get('usability') for item in reviews}
        usability_complete=set(usability)=={'wide','compact'} and all(isinstance(value,dict) for value in usability.values())
        composition=plan['planned'][row['arm']]
        selections=composition.get('task_selections',[])
        inline_fragments=[identity for selection in selections for identity in selection['inline_fragment_ids']] if selections else all_fragments
        warm=delivery.get('publish_to_visible_seconds')
        censored_time=row.get('timing_censored_by_interruption',False)
        root_seconds=sum_execution_seconds(timings)
        root_primary_seconds=execution_seconds(timings[0]) if timings else None
        root_repair_seconds=sum_execution_seconds(timings[1:])
        critical_path=round(total_wall+delivery.get('revision_and_resource_seconds',0)+warm,3) if warm is not None and not censored_time else None
        record={'cohort':cohort['id'],'task':plan['task'],'model':cohort['model'],'sample':sample_id,'arm':row['arm'],
            'first_pass_compile':row['first_pass'],'first_pass_structural':row['structural']['ok'],
            'corrected_first_pass_compile':corrected_primary.get((cohort['id'],sample_id),{}).get('generation_ok'),
            'corrected_first_pass_structural':corrected_primary.get((cohort['id'],sample_id),{}).get('structural_ok'),
            'final_compile':candidate.get('ok') is True,'final_structural':structural['ok'],
            'browser_pass':browser_verdict,'functional_e2e_pass':functional_verdict,
            'usability_floor_pass':all(value.get('ok') is True for value in usability.values()) if usability_complete else None,
            'usability_floor_by_layout':usability,
            'posthoc_rechecked_probes': rechecked_probes,
            'posthoc_rechecked_functional_pass': rechecked_verdict,
            'qualification_pending':outcome['qualification_pending'],
            'qualification_infrastructure_error':outcome['qualification_infrastructure_error'],
            'repair_attempted':attempted>1,'repair_budget_censored':(sample/'repair-budget-censored.json').exists(),
            'attempted_requests':attempted,'usage':totals,'usage_primary':primary_usage,'usage_repair':repair_usage,'usage_complete':len(usage)==attempted,
            'generator_wall_seconds':None if censored_time else total_wall,'timing_censored_by_interruption':censored_time,
            'root_execution_seconds':root_seconds,
            'root_primary_execution_seconds':root_primary_seconds,
            'root_repair_execution_seconds':root_repair_seconds,
            'non_root_generator_overhead_seconds':None if censored_time or root_seconds is None else round(total_wall-root_seconds,3),
            'time_to_first_output_ms':timings[0].get('time_to_first_output_ms') if timings else None,
            'time_to_first_provider_event_ms':timings[0].get('time_to_first_provider_event_ms') if timings else None,
            'mcp_calls':sum(t['call_count'] for t in traces),'mcp_errors':sum(t['errors'] for t in traces),
            'mcp_primary_calls':traces[0]['call_count'] if traces else None,
            'retrieval_behavior':retrieval_behavior(traces[0],inline_fragments) if traces else None,
            'mcp_observed_interval_ms':sum(t.get('mcp_observed_interval_ms') or 0 for t in traces),
            'mcp_trace':traces,'provider_jobs':retained,'delivery_timings':delivery,
            'owner_stage_timings':{key:owner.get(key) for key in ('timings_ms','semantic_rebuild_timings_ms','ydoc_timings_ms','phase_timings_ms')},
            'initial_context':{'message_characters':composition['output_chars'],'full_message_characters':composition['input_chars'],
                'provider_input_estimated_tokens':composition['estimated_tokens'],'scope':'Character size exact; initial provider tokens estimated, not native usage after MCP',
                'inline_fragments':inline_fragments,
                'indexed_fragments':[identity for selection in selections for identity in selection['retrievable_fragment_ids']],
                'selection_reasons':[reason for selection in selections for reason in selection['selection_reasons']],
                'exceeds_full_message_characters':composition['output_chars']>composition['input_chars']},
            'reconstructed_generation_to_warm_visible_seconds':critical_path,
            'quality_verification_seconds':qualified_row.get('qualification_seconds'),
            'shape':{key:len((candidate.get('semantic_document') or {}).get(key) or []) for key in ('resources','views','commands','representative_states')},
            'failure':candidate.get('detail') if not candidate.get('ok') else None,'structural':structural,
            'delivery_entries':[job['delivery_entry'] for job in retained],
            'browser_failure':{key:value for key,value in qualified_row.items() if key in ('error','detail','probes')}}
        rows.append(record); groups[cohort['model'],row['arm']].append(record)

summary=[]
for (model,arm),items in groups.items():
    totals={key:sum(i['usage'][key] for i in items) for key in items[0]['usage']}
    primary_totals={key:sum((i['usage_primary'] or {}).get(key,0) for i in items) for key in totals}
    repair_totals={key:sum(i['usage_repair'][key] for i in items) for key in totals}
    successes=sum(i['functional_e2e_pass'] is True for i in items)
    summary.append({'model':model,'arm':arm,'n':len(items),'first_pass_compile':sum(i['first_pass_compile'] for i in items),
        'first_pass_structural':sum(i['first_pass_structural'] for i in items),'final_compile':sum(i['final_compile'] for i in items),
        'corrected_first_pass_compile':sum(i['corrected_first_pass_compile'] is True for i in items) if any(i['corrected_first_pass_compile'] is not None for i in items) else None,
        'corrected_first_pass_structural':sum(i['corrected_first_pass_structural'] is True for i in items) if any(i['corrected_first_pass_structural'] is not None for i in items) else None,
        'functional_e2e_pass':successes,'functional_e2e_evaluated':sum(i['functional_e2e_pass'] is not None for i in items),
        'usability_floor_pass':sum(i['usability_floor_pass'] is True for i in items),
        'usability_floor_evaluated':sum(i['usability_floor_pass'] is not None for i in items),
        'usage':totals,'usage_primary':primary_totals,'usage_repair':repair_totals,'usage_complete':all(i['usage_complete'] for i in items),
        'posthoc_rechecked_functional_pass':sum(i['posthoc_rechecked_functional_pass'] is True for i in items),
        'attempts':sum(i['attempted_requests'] for i in items),'mcp_calls':sum(i['mcp_calls'] for i in items),
        'mcp_errors':sum(i['mcp_errors'] for i in items),'mean_root_execution_seconds':mean([i['root_execution_seconds'] for i in items if i['root_execution_seconds'] is not None]) if any(i['root_execution_seconds'] is not None for i in items) else None,
        'mean_root_primary_execution_seconds':mean([i['root_primary_execution_seconds'] for i in items if i['root_primary_execution_seconds'] is not None]) if any(i['root_primary_execution_seconds'] is not None for i in items) else None,
        'mean_generator_seconds':mean([i['generator_wall_seconds'] for i in items if i['generator_wall_seconds'] is not None]) if any(i['generator_wall_seconds'] is not None for i in items) else None,
        'usd_per_observed_functional_success_including_failures':totals['usd_standard_estimate_without_unreported_cache_writes']/successes if successes else None})
judges=[]
for terminal in sorted((run/'blind-ux').glob('*/terminal.json')):
    envelope=read(terminal); value=usage_record(envelope,'gpt-6-astra')
    judges.append({'job_id':envelope.get('job_id'),'usage':value})
ledger=RequestBudget(run/'paid-request-budget.sqlite').snapshot()
amendments={name:read(run/name) for name in ('transport-amendment.json','delivery-amendment-02.json','uniform-replay-amendment.json') if (run/name).exists()}
if amendment_path.exists():amendments[amendment_path.name]=uniform
for name in ('context-routing-amendment-01.json', 'probe-selector-amendment-01.json', 'client-focus-amendment-01.json',
             'probe-composition-amendment-01.json', 'probe-choice-amendment-02.json', 'runtime-recovery-amendment-03.json',
             'probe-public-acceptance-amendment-04.json', 'probe-composition-taxonomy-amendment-05.json',
             'probe-uniform-supplier-comparison-amendment-06.json'):
    if (run/name).exists(): amendments[name] = read(run/name)
judge_results=read(run/'blind-ux/results.json') if (run/'blind-ux/results.json').exists() else None
phase_values=defaultdict(list)
for row in rows:
    for key in ('revision_and_resource_seconds','preview_publication_seconds','warm_navigation_to_visible_seconds','publish_to_visible_seconds','functional_probes_seconds'):
        value=row['delivery_timings'].get(key)
        if value is not None:phase_values[key].append(value)
    for key in ('root_primary_execution_seconds','root_repair_execution_seconds','non_root_generator_overhead_seconds'):
        if row[key] is not None:phase_values[key].append(row[key])
    for key in ('time_to_first_provider_event_ms', 'time_to_first_output_ms'):
        if row[key] is not None:phase_values[key.removesuffix('_ms')+'_seconds'].append(row[key]/1000)
phase_summary={key:{'n':len(values),'min_seconds':min(values),'median_seconds':median(values),'mean_seconds':mean(values),'max_seconds':max(values)}
    for key,values in phase_values.items()}
expected_samples=registration.get('maximum_primary_attempts',20)
report={'schema':'adaos.e2e.context_composition_report.v1','registration':registration,'complete':len(rows)==expected_samples and len(browser_complete)==len(registration['cohorts']) and all(browser_complete) and all(r['functional_e2e_pass'] is not None for r in rows),
    'samples':rows,'summary':summary,'paid_request_budget':ledger,'missing_usage_jobs':unknown,'judge_costs_separate':judges,
    'evidence_labels':{'browser':args.browser_label,'compiler':args.compiler_label},'amendments':amendments,'blind_ux':judge_results,
    'latency_phase_summary':phase_summary,
    'posthoc_targeted_rechecks': {'batches': recheck_batches, 'primary_uniform_functional_pass': sum(r['functional_e2e_pass'] is True for r in rows),
        'posthoc_functional_pass': sum(r['posthoc_rechecked_functional_pass'] is True for r in rows),
        'scope': 'Targeted post-hoc diagnostics, NOT a new uniform series. Original failures and every completed recheck remain visible; latest complete batch, all repetitions must pass.'},
    'usd_primary_estimate':round(sum((r['usage_primary'] or {}).get('usd_standard_estimate_without_unreported_cache_writes',0) for r in rows),6),
    'usd_repair_estimate':round(sum(r['usage_repair']['usd_standard_estimate_without_unreported_cache_writes'] for r in rows),6),
    'usd_generation_and_repair_estimate':round(sum(r['usage']['usd_standard_estimate_without_unreported_cache_writes'] for r in rows),6),
    'usd_judge_estimate':round(sum((r['usage'] or {}).get('usd_standard_estimate_without_unreported_cache_writes',0) for r in judges),6),
    'cost_basis':{'url':'https://developers.openai.com/api/docs/pricing','retrieved_on':'2026-10-11','rates_usd_per_million':rates,
       'scope':'Standard short-context estimate, not Root invoice; unreported cache-write surcharge and service-tier overrides NOT inferred'},
    'timing_caveats':['Provider execution includes model, API/transport and tools. Subtracting observed MCP union does not yield pure model compute.',
        'First provider event is not first visible output. Legacy ttft_ms is not used as a user-visible token latency.',
        'Warm Client observation measures identity + visible hosts + fonts + frames, not all async data. Delivery observer setup excluded.',
        'Reconstructed critical path sums sequential phases measured at separate times, NOT chat-request-to-user-acceptance telemetry.',
        'Missing timing is null; never infer historical timing from zero. Functional probe wall time reported separately.'],
    'inference_caveats':[f"n={registration.get('repetitions',1)} per cell; no significance, no general architecture ranking.",'Compare first-pass, final functionality, output/reasoning and ALL attempted cost separately.',
        'Oracle is a frozen sufficient-fragment hypothesis, not established task sufficiency.','Blind model UX judge is an uncalibrated proxy; no human approval or human-alignment claim.',
        'Usability floors check public-control names, compact targets and page overflow; NOT complete accessibility or human usability proof.']}
_write_json(run/'report.json',report); _write_json(run/'paid-request-budget.json',ledger)
for item in summary:
    print(json.dumps({**{key:item[key] for key in ('model', 'arm', 'n', 'first_pass_compile', 'first_pass_structural',
        'final_compile', 'functional_e2e_pass', 'functional_e2e_evaluated', 'usability_floor_pass', 'usability_floor_evaluated', 'mcp_calls')},
        'usd_generation_and_repair_estimate': round(item['usage']['usd_standard_estimate_without_unreported_cache_writes'], 6)}))
if not args.partial and not report['complete']:
    parser.error('Browser execution completed but functionality remains unevaluated after an infrastructure error; see report.json')
