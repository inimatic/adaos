"""Five-arm, two-task, two-model pilot with a durable <=32 paid-attempt budget.

Run plan, then generate, repair, qualify and judge/report explicitly. Generation
uses at most two concurrent cohorts; shared Preview qualification is serial.
All primary outputs are frozen before spending the deterministic repair reserve.
One observation per cell is a diagnostic pilot, NOT statistical proof.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import sys
from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import source_fingerprints, read
from adaos.e2e.request_budget import RequestBudget

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--stage', choices=('plan','generate','repair','qualify'), required=True)
parser.add_argument('--host', default='desktop-codex-navigation')
args=parser.parse_args()
repo=Path.cwd().resolve(); output=args.output.resolve()
if not output.is_relative_to(repo/'e2e/artifacts/builder'):
    parser.error('Owned E2E output required')
output.mkdir(parents=True,exist_ok=True)
budget=RequestBudget(output/'paid-request-budget.sqlite')
arms=['full-no-mcp','full-mcp','core-indexed','task-aware','oracle']
orders=[arms, list(reversed(arms)), [*arms[3:],*arms[:3]], [arms[2],arms[4],arms[0],arms[3],arms[1]]]
cohorts=[]
for i,(model,task) in enumerate([('gpt-5','inventory'),('gpt-6-astra','inventory'),('gpt-5','support'),('gpt-6-astra','support')]):
    cohorts.append({'id':('gpt5' if model=='gpt-5' else 'astra')+'-'+task,'model':model,'task':task,'arms':orders[i]})
configuration={'schema':'adaos.e2e.context_composition_series.v1','cohorts':cohorts,'host':args.host,
    'reasoning_effort':'high','maximum_paid_attempts':32,'allocation':budget.limits,'provider_max_attempts':1,
    'root_base_url':'https://ru.api.inimatic.com','parallel_cohorts':2,'repairs':'one native repair per failed compiler candidate; deterministic cohort/arm order, censored if reserve exhausted',
    'scope':'Prototype native helper, compiler, revision/resources, warm Client observation and desktop/mobile functional probes; NOT chat dispatch or Automation',
    'primary_endpoint':'first-pass native compiler + task structural admission',
    'final_endpoint':'native repair admission + task structural + functional browser',
    'ux_endpoint':'blinded fixed-rubric paired model-judge proxy with swapped positions; human alignment NOT established',
    'oracle':'frozen sufficient-rule HYPOTHESIS; diagnostic only, not an oracle built from generated answers',
    'limitation':'n=1 per model/task/arm; no population significance or production-default decision',
    'runtime_source_fingerprints':source_fingerprints(repo)}
config_path=output/'preregistration.json'
if config_path.exists():
    previous = read(config_path)
    from adaos.e2e.runtime_amendment import validated_sources
    validated_sources(repo,previous,output/'gpt5-inventory')
    current = dict(configuration)
    current['runtime_source_fingerprints'] = previous['runtime_source_fingerprints']
    current['allocation'] = previous['allocation']
    if previous!=current: raise ValueError('Preregistered source/configuration drift')
elif args.stage!='plan': parser.error('Run plan before any paid generation')
else: _write_json(config_path,configuration)

def execute(command,log):
    with log.open('w',encoding='utf-8') as sink:
        result=subprocess.run(command,stdout=sink,stderr=subprocess.STDOUT)
    print(json.dumps({'stage':args.stage,'log':str(log.relative_to(repo)),'exit_code':result.returncode}),flush=True)
    if result.returncode: raise RuntimeError('Stage failed; stop/recover evidence at '+str(log))

def generate(cohort):
    run=output/cohort['id']
    command=[sys.executable,'e2e/stand/run-prototype-context-ablation.py','--source-run',
      'e2e/artifacts/builder/prototype-pipeline-confirmation-20261010-01/gpt5-inventory',
      '--output',str(run),'--model',cohort['model'],'--reasoning-effort','high','--repetitions','1',
      '--mcp','on','--context-mode','composition','--arms',*cohort['arms'],
      '--oracle-file','e2e/builder/development/context-ablation/prototype-guidance-oracles.json',
      '--request-budget-db',str(budget.path),'--root-base-url',configuration['root_base_url'],
      '--defer-browser','--host',args.host]
    if cohort['task']=='support':command+=['--task-file','e2e/builder/development/context-ablation/support-desk-ru.json']
    if args.stage=='plan':command+=['--plan-only']
    if run.exists():command+=['--resume']
    execute(command,output/(cohort['id']+'-'+args.stage+'.log'))

if args.stage=='plan':
    for cohort in cohorts:generate(cohort)
    for task in ('inventory','support'):
        plans=[read(output/c['id']/'plan.json') for c in cohorts if c['task']==task]
        for key in ('instruction_sha256','brief_sha256','provider_schema_sha256','initial_webui_sha256','library_digest'):
            if len({p[key] for p in plans})!=1:raise ValueError('Model-pair frozen input mismatch: '+key)
    _write_json(output/'plan-summary.json',{c['id']:{a:read(output/c['id']/'plan.json')['planned'][a]['estimated_tokens'] for a in arms} for c in cohorts})
elif args.stage=='generate':
    # Do not eagerly queue later cohorts after a delivery failure.
    with ThreadPoolExecutor(max_workers=2) as pool:
        for offset in (0,2):
            for future in as_completed([pool.submit(generate,c) for c in cohorts[offset:offset+2]]):future.result()
else:
    schedule = [(cohort,None) for cohort in cohorts] if args.stage=='qualify' else [
        (cohort,f"{order+1:02d}-{cohort['arms'][order]}") for order in range(5) for cohort in cohorts]
    for cohort in cohorts:
        run=output/cohort['id']
        if not read(run/'results.json')['complete']:raise ValueError('All primary candidates must be frozen first')
    for cohort,sample in schedule:
        run=output/cohort['id']
        if args.stage=='repair':
            command=[sys.executable,'e2e/stand/repair-prototype-pipeline-cohort.py','--run',str(run),'--samples',sample]
        else:
            command=[sys.executable,'e2e/stand/qualify-prototype-context-ablation.py','--run',str(run),'--host',args.host]
            if (run/'browser-requalification-mcp').exists():command+=['--resume']
        execute(command,output/(cohort['id']+'-'+args.stage+('-'+sample if sample else '')+'.log'))
_write_json(output/'paid-request-budget.json',budget.snapshot())
