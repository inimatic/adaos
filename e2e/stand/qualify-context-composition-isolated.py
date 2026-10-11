"""Uniform post-hoc qualification in NEW owned fixtures and isolated workspaces.

Retained model text and Brief never change. Each candidate gets a fresh scenario
namespace, preventing old browser mutations from contaminating a later arm.
The user's existing Builder/Preview is never selected or rewritten here.
"""
import argparse, hashlib, json, logging, os, subprocess, time
import requests
from pathlib import Path
from unittest.mock import patch
from dotenv import load_dotenv
from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import acceptance, browser_review, native_builder, read, remap, source_fingerprints
from adaos.e2e.prototype_task_acceptance import task_mutation_contract

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',type=Path,required=True)
parser.add_argument('--cohort',required=True)
parser.add_argument('--label',default='final')
parser.add_argument('--amendment', default='uniform-replay-amendment.json')
args=parser.parse_args();run=args.run.resolve()
if not run.is_relative_to(Path.cwd()/'e2e/artifacts/builder'):parser.error('Owned E2E required')
registration=read(run/'preregistration.json')
cohort=next(c for c in registration['cohorts'] if c['id']==args.cohort)
amendment_path=(run/args.amendment).resolve()
if amendment_path.parent != run:parser.error('Amendment must be a receipt in the run directory')
amendment=read(amendment_path)
fingerprints=source_fingerprints(Path.cwd())
if fingerprints!=amendment['after_sources'] or args.label!=amendment['browser_label']:raise ValueError('Uniform qualification source/label drift')
if any('sha256:'+hashlib.sha256(Path(p).read_bytes()).hexdigest()!=sha for p,sha in amendment['extra_sources'].items()):raise ValueError('Uniform extra-source drift')
load_dotenv();logging.disable(logging.INFO)
if os.getenv('ENV_TYPE')!='dev':parser.error('DEV only')
from adaos.apps.cli.app import Settings,init_ctx
from adaos.services.agent_context import get_ctx
from adaos.apps.cli.active_control import resolve_control_token
from adaos.sdk.developer import compositions,projects
from adaos.sdk.builder import preview,workflow
from adaos.sdk.llm import llm_client
init_ctx(Settings.from_sources());ctx=get_ctx()
os.environ['ADAOS_CONTROL_URL']='http://127.0.0.1:8777'
os.environ['ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED']='0'
directory=run/cohort['id']; output=directory/('browser-posthoc-'+args.label);output.mkdir(exist_ok=True)
plan=read(directory/'plan.json');brief=read(directory/'source-brief.json');results=read(directory/'results.json')
expected_samples=len(plan['order'])
assert results['complete'] and len(results['samples'])==expected_samples
handler=Path(ctx.paths.dev_skills_dir())/'builder_skill/handlers/main.py'
if 'sha256:'+hashlib.sha256(handler.read_bytes()).hexdigest()!=plan['native_builder_sha256']:raise ValueError('Native Builder drift')
builder=native_builder(handler)
host='test_ctxqual_'+hashlib.sha256((str(run)+cohort['id']+args.label).encode()).hexdigest()[:14]
host_receipt=output/'owned-host.json'
owner=requests.Session();owner.trust_env=False
owner.headers['Authorization']='Bearer '+resolve_control_token(base_url='http://127.0.0.1:8777')
listed=owner.get('http://127.0.0.1:8777/api/node/yjs/webspaces',timeout=30)
listed.raise_for_status();existing={str(item['id']) for item in listed.json()['items']}
if host in existing and not host_receipt.exists():raise ValueError('Unowned existing qualification host')
if host not in existing:
 created_host=owner.post('http://127.0.0.1:8777/api/node/yjs/webspaces',json={'id':host,
  'title':'[TEST] Context qualification '+cohort['id'],'scenario_id':'builder','dev':False},timeout=150)
 created_host.raise_for_status();assert created_host.json().get('ok')
 _write_json(host_receipt,{'host':host,'test':True,'creation_via_hub_owner':True,'existing_user_preview_untouched':True})
preview.ensure(host,runtime_scenario_id='web_desktop',wait_for_rebuild=True)
health=subprocess.run(['node','e2e/stand/browser/prototype-client-health.mjs'],capture_output=True,text=True,encoding='utf-8',timeout=60,check=True)
_write_json(output/'client-health.json',json.loads(health.stdout))
_write_json(output/'configuration.json',{'label':args.label,'compiler_label':amendment['final_compiler_label'],
 'host':host,'fresh_fixtures':True,'sources':fingerprints,'new_llm_calls':0,'probes_changed':amendment.get('probe_changes',[])})
rows=[]
with patch.object(llm_client,'submit_response_job',side_effect=AssertionError('Qualification may never submit a model')):
 for row in results['samples']:
  if source_fingerprints(Path.cwd())!=fingerprints or any('sha256:'+hashlib.sha256(Path(p).read_bytes()).hexdigest()!=sha for p,sha in amendment['extra_sources'].items()):raise ValueError('Source drift during uniform qualification')
  sample=f"{row['order']:02d}-{row['arm']}";target=output/sample;target.mkdir(exist_ok=True)
  if (target/'result.json').exists():rows.append(read(target/'result.json'));continue
  started=time.perf_counter()
  replay=run/('compiler-replay-'+amendment['final_compiler_label'])/cohort['id']/sample
  candidate=read(replay/'generation-result.json')
  if not candidate.get('ok'):
   checked={'ok':False,'skipped':'uniform_compiler_replay_failed'}
  else:
   source_session=read(directory/sample/'session.json')
   scenario='test_ctxfinal_'+hashlib.sha256((str(run)+cohort['id']+sample+args.label).encode()).hexdigest()[:18]
   creation_path=target/'creation.json'
   if creation_path.exists():created=read(creation_path)
   else:
    if projects.resolve_root('scenario',scenario,required=False).exists() or compositions.resolve_root(scenario,required=False).exists():raise ValueError('Unowned fixture namespace')
    created=compositions.create_with_primary_component(scenario,kind='scenario',template='scenario_default',
     title=source_session['title'],description='Isolated uniform context-composition qualification',tags=['test','context-composition'])
    _write_json(creation_path,created)
   baseline=remap(source_session['initial_webui'],source_session['scenario_id'],scenario)
   root=projects.resolve_root('scenario',scenario)
   session_path=target/'session.json'
   if session_path.exists():session=read(session_path)
   else:
    builder._write_webui_payload(str(root),baseline)
    change=workflow.transition('scenario',scenario,'plan_change_set',actor='agent:builder-e2e',metadata={
     'change_set_id':scenario,'request':source_session['source_idea'],'issues':[{'issue_id':'context-qualification',
     'title':'Uniform retained candidate qualification','lane':'prototype','acceptance_criteria':['Same frozen Brief and functional gates; new owned fixtures only.']}]})['workflow']
    session={**source_session,'id':'scenario.'+scenario,'scenario_id':scenario,'project_ref':'project:'+scenario,
     'artifact_root':str(root),'initial_webui':baseline,'webui_payload':baseline,'active_change_id':change['change']['change_id'],'patches':[]}
    _write_json(session_path,session)
   text=(replay/'effective-candidate.txt').read_text(encoding='utf-8')
   fresh=builder._parse_llm_webui_transform_output(output_text=text,previous_preview=baseline.get('preview_state') or {},
    before_webui=baseline,prototype_brief=brief,project_ref=session['project_ref'],output_mode='semantic_v2')
   if fresh['semantic_document']!=candidate['semantic_document']:raise ValueError('Fresh namespace changed model semantics')
   _write_json(target/'structural-acceptance.json',acceptance(fresh,brief,task=plan['task']))
   task_contract=task_mutation_contract(fresh['semantic_document'],brief,task=plan['task'])
   _write_json(target/'task-mutation-contract.json',task_contract)
   probes=['prototype-review','prototype-capabilities','prototype-interactions'] if plan['task']=='support-desk-ru' else None
   try:
    checked=browser_review(builder,session,fresh,target,created,Path(ctx.paths.dev_dir()).name,run.name,
     host=host,selected_probes=probes,observe_delivery=True)
   except Exception as error:checked={'ok':False,'error':type(error).__name__,'detail':str(error)}
  record={'sample':sample,'arm':row['arm'],**checked,'qualification_seconds':round(time.perf_counter()-started,3)}
  if candidate.get('ok'):
   record['task_mutation_contract']=task_contract
   record['ok']=record.get('ok') is True and task_contract['ok']
  _write_json(target/'result.json',record);rows.append(record)
  _write_json(output/'results.json',{'complete':False,'samples':rows,'new_llm_calls':0})
  print(json.dumps({'cohort':cohort['id'],**record},ensure_ascii=False),flush=True)
_write_json(output/'results.json',{'complete':len(rows)==expected_samples,'samples':rows,'new_llm_calls':0,'fresh_fixtures':True,
 'protected_user_preview_never_selected':True,'retention':'Owned unapproved test scenarios and hosts retained for reproducibility'})
