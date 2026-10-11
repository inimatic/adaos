"""Transport-only continuation on the SAME central provider worker.

MCP leases/library stay on the original RU plane. Planned prompts, schema,
model controls and ledger remain byte-identical. No cross-plane retry: the
job route is pinned to the actual worker before submission and polling.
"""
import argparse, hashlib, runpy, sys, time
from pathlib import Path
from unittest.mock import patch
from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import read
from adaos.sdk.llm import llm_client

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',type=Path,required=True)
parser.add_argument('--cohort')
parser.add_argument('--stage',choices=('generate','repair','judge'),default='generate')
parser.add_argument('--samples',nargs='+')
args=parser.parse_args();run=args.run.resolve()
if not run.is_relative_to(Path.cwd()/'e2e/artifacts/builder'):parser.error('Owned DEV series required')
registration=read(run/'preregistration.json');worker='https://api.inimatic.com'
receipt={'scope':'transport only; same provider worker, no semantic/context/schema/criterion change',
 'original_entry':registration['root_base_url'],'direct_worker':worker,'mcp_plane_unchanged':True,
 'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'stage':args.stage,'cohort':args.cohort,
 'at_epoch_seconds':time.time(),'reason':'Repeated gateway POST and poll timeouts; bypass only the RU relay, never resubmit an accepted job'}
_write_json(run/f"direct-worker-{args.stage}-{args.cohort or 'series'}.json",receipt)
native_submit,native_get=llm_client._submit_root_job,llm_client.get_response_job
def submit(*values,**options):
 with patch.object(llm_client,'_root_llm_base_urls',lambda *_args,**_kwargs:[worker]):return native_submit(*values,**options)
def get(job_id,**options):
 options['base_url']=worker
 return native_get(job_id,**options)
if args.stage=='judge':
 script='e2e/stand/judge-prototype-context-composition.py';arguments=['--run',str(run)]
else:
 cohort=next(c for c in registration['cohorts'] if c['id']==args.cohort)
 directory=run/cohort['id']
 if args.stage=='repair':
  script='e2e/stand/repair-prototype-pipeline-cohort.py';arguments=['--run',str(directory)]
  if args.samples:arguments+=['--samples',*args.samples]
 else:
  script='e2e/stand/run-prototype-context-ablation.py'
  arguments=['--source-run','e2e/artifacts/builder/prototype-pipeline-confirmation-20261010-01/gpt5-inventory',
   '--output',str(directory),'--model',cohort['model'],'--reasoning-effort','high','--repetitions','1','--mcp','on',
   '--context-mode','composition','--arms',*cohort['arms'],'--oracle-file','e2e/builder/development/context-ablation/prototype-guidance-oracles.json',
   '--request-budget-db',str(run/'paid-request-budget.sqlite'),'--root-base-url',registration['root_base_url'],
   '--defer-browser','--host',registration['host'],'--resume']
  if cohort['task']=='support':arguments+=['--task-file','e2e/builder/development/context-ablation/support-desk-ru.json']
sys.argv=[script,*arguments]
with patch.object(llm_client,'_submit_root_job',submit),patch.object(llm_client,'get_response_job',get):
 runpy.run_path(script,run_name='__main__')
