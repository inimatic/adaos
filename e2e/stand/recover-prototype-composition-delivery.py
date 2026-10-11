"""Recover owned submitted/ambiguous candidates without changing their inputs.

Only a verified lookup NOT FOUND permits a separately budgeted delivery retry.
Any conflict resolves to reading the existing authorized job; never generates a
replacement for a paid answer. Interrupted derived results are journaled first.
"""
import argparse, copy, inspect, json, logging, os, time
from pathlib import Path
from dotenv import load_dotenv
from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import read, native_builder, acceptance
from adaos.e2e.prototype_mcp_eval import mcp_trace
from adaos.e2e.request_budget import RequestBudget
from adaos.e2e.runtime_amendment import validated_sources
from adaos.services.builder_context import digest
from adaos.services.builder.prototype_mcp import redact_credentials

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',type=Path,required=True)
parser.add_argument('--retry-not-dispatched',action='store_true')
parser.add_argument('--cohorts',nargs='+')
parser.add_argument('--direct-worker',action='store_true')
args=parser.parse_args();run=args.run.resolve()
if not run.is_relative_to(Path.cwd()/'e2e/artifacts/builder'):parser.error('Owned E2E required')
load_dotenv();logging.disable(logging.INFO)
from adaos.apps.cli.app import Settings,init_ctx
from adaos.services.agent_context import get_ctx
from adaos.sdk.llm import llm_client
from adaos.services.root.client import RootHttpError
from adaos.services.builder import prototype_mcp
registration=read(run/'preregistration.json');budget=RequestBudget(run/'paid-request-budget.sqlite')
os.environ['ADAOS_ROOT_LLM_BASE_URL']=registration['root_base_url'];os.environ['ADAOS_ROOT_LLM_PREFER_GLOBAL']='0'
os.environ['ADAOS_CONTROL_URL']='http://127.0.0.1:8777'
init_ctx(Settings.from_sources()); llm_client._root_llm_base_urls=lambda *_args,**_kwargs:[registration['root_base_url']]
builder=native_builder(Path(get_ctx().paths.dev_skills_dir())/'builder_skill/handlers/main.py')
http,cfg=llm_client._root_http_client();headers=llm_client._identity_headers(llm_client._current_ctx(),cfg)
delivery_url='https://api.inimatic.com' if args.direct_worker else registration['root_base_url']
if args.direct_worker:
 from adaos.services.root.client import RootHttpClient
 http=RootHttpClient(base_url=delivery_url,verify=http.verify,cert=http.cert)
for cohort in registration['cohorts']:
 if args.cohorts and cohort['id'] not in args.cohorts:continue
 directory=run/cohort['id'];plan=read(directory/'plan.json');validated_sources(Path.cwd(),plan,directory)
 brief=read(directory/'source-brief.json')
 for order,arm in enumerate(plan['order'],1):
  sample=directory/f'{order:02d}-{arm}'
  if not (sample/'call-01-request.json').exists() or (sample/'call-01-terminal.json').exists():continue
  started=time.perf_counter();request=read(sample/'call-01-request.json');session=read(sample/'session.json')
  assert digest(request['messages'])==read(sample/'delivery-01.json')['output_sha256']
  controls=copy.deepcopy(request['options']);assert controls['model']==cohort['model'] and controls['provider_max_attempts']==1
  submitted_path=sample/'call-01-submission.json'
  if submitted_path.exists():submitted=read(submitted_path)
  else:
   payload,_=llm_client._llm_root_payload(request['messages'],**{k:v for k,v in controls.items() if k in inspect.signature(llm_client._llm_root_payload).parameters})
   try: submitted=http.request('POST','/v1/llm/jobs/lookup',json=payload,headers=headers,timeout=30)
   except RootHttpError as error:
    body=error.payload or {};upstream=body.get('upstream',{}) if isinstance(body,dict) else {}
    inner=upstream.get('json') or body
    if error.status_code==409 and inner.get('job_id'):
     submitted=llm_client.get_response_job(inner['job_id'],base_url=registration['root_base_url'],timeout=30)
     assert submitted.get('request_id')==controls['request_id']
    elif error.status_code==404 and inner.get('error')=='llm_job_not_found':
     _write_json(sample/'delivery-not-dispatched.json',{'lookup_status':404,'request_id':controls['request_id'],
      'at_epoch_seconds':time.time(),'prior_reserved_attempt_retained':True,'no_provider_job':True})
     original_slot=read(sample/'budget-primary.json')['ordinal'];budget.observe(original_slot,status='verified_not_dispatched')
     if not args.retry_not_dispatched:raise RuntimeError('No job; explicit budgeted delivery recovery not enabled')
     slot=budget.reserve('primary',str(sample.relative_to(Path.cwd()))+':delivery-recovery')
     _write_json(sample/'budget-delivery-recovery.json',{'ordinal':slot})
     os.environ['ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED']='0' if arm=='full-no-mcp' else '1'
     if arm!='full-no-mcp':controls.update(prototype_mcp.generation_options())
     controls['timeout']=60
     _write_json(sample/'delivery-recovery-request.json',redact_credentials({'messages':request['messages'],'options':controls}))
     from unittest.mock import patch
     with patch.object(llm_client,'_root_llm_base_urls',lambda *_args,**_kwargs:[delivery_url]):
      submitted=llm_client.submit_response_job(request['messages'],**controls)
     budget.observe(slot,job_id=submitted.get('job_id'),status=submitted.get('status','submitted'))
     print(json.dumps({'stage':'delivery_recovered_submission','sample':sample.name,'job':submitted.get('job_id')}),flush=True)
    else:raise
   _write_json(submitted_path,redact_credentials(submitted))
  envelope=llm_client.get_response_job(submitted['job_id'],base_url=delivery_url,timeout=30)
  if envelope.get('status') in {'running','queued'}:
   envelope=llm_client.wait_response_job(submitted['job_id'],base_url=delivery_url,timeout_s=1200,request_timeout=15)
  _write_json(sample/'call-01-terminal.json',redact_credentials(envelope))
  assert envelope.get('telemetry',{}).get('provider_max_attempts')==1 and envelope.get('telemetry',{}).get('provider_attempts')==1
  raw=envelope.get('output_text') or ''
  try:
   if envelope.get('status')!='succeeded':raise RuntimeError('Provider failed: '+str(envelope.get('error')))
   candidate=builder._parse_llm_webui_transform_output(output_text=raw,
    previous_preview=session['initial_webui'].get('preview_state') or {},before_webui=session['initial_webui'],
    prototype_brief=brief,project_ref=session['project_ref'],output_mode='semantic_v2',request_id=controls['request_id'],job_id=submitted['job_id'])
  except Exception as error:candidate={'ok':False,'last_response':raw,'error':'retained_response_parse_failed','detail':str(error)}
  for name in ('result.json','generation-result.json','primary-generation-result.json'):
   if (sample/name).exists():_write_json(sample/('interrupted-'+name),read(sample/name))
  previous=read(sample/'result.json') if (sample/'result.json').exists() else {}
  structural=acceptance(candidate,brief,task=plan['task'])
  row={**previous,'order':order,'arm':arm,'repetition':1,'scenario_id':session['scenario_id'],
   'generation_ok':candidate.get('ok') is True,'first_pass':candidate.get('ok') is True,'calls':1,
   'generation_seconds':round(previous.get('generation_seconds',0)+time.perf_counter()-started,3),
   'timing_censored_by_interruption':True,'structural':structural,'browser':{'deferred':True,'ok':False},
   'recovered_without_replacing_paid_candidate':True,'failure':{'detail':candidate.get('detail')} if not candidate.get('ok') else None,
   'telemetry':[builder._llm_job_telemetry(envelope)]}
  _write_json(sample/'primary-generation-result.json',candidate);_write_json(sample/'generation-result.json',candidate)
  _write_json(sample/'structural-acceptance.json',structural);_write_json(sample/'result.json',row)
  _write_json(sample/'native-mcp-traces.json',[mcp_trace(envelope)])
  print(json.dumps({'stage':'retained_candidate_recovered','cohort':cohort['id'],'sample':sample.name,'ok':candidate.get('ok')}),flush=True)
 existing=[read(directory/f'{i:02d}-{a}'/'result.json') for i,a in enumerate(plan['order'],1) if (directory/f'{i:02d}-{a}'/'result.json').exists()]
 _write_json(directory/'results.json',{'samples':existing,'complete':len(existing)==5,'delivery_recovery':True,'original_interrupted_results_preserved':True})
_write_json(run/'paid-request-budget.json',budget.snapshot())
