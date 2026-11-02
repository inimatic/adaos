"""Preregistered fresh S1/S2 qualification through the ordinary Builder chat.

The durable 48-call allowance belongs to this separate user-authorized series.
No historical budget reset, direct provider submission, API fallback or replay
after ambiguous chat delivery. Run cells serially; retain every failed outcome.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from .builder_creation_episode import attempts, runtime_identity, TERMINAL
from .request_budget import RequestBudget

MODEL = 'gpt-5.6-luna'
PROFILES = {'S1': ('s1-facts-inline-bounded-v1', 2), 'S2': ('s2-inline-semantic-json-bounded-v1', 3)}
ORDER = [('equipment-en','S1',1),('equipment-en','S2',1),('equipment-en','S2',2),('equipment-en','S1',2),
    ('sensors-ru','S2',1),('sensors-ru','S1',1),('sensors-ru','S1',2),('sensors-ru','S2',2)]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)


def source_pins(root, dev, api):
    paths=['src/adaos/services/builder/'+name+'.py' for name in ('task_pipeline','generation_profiles',
        'semantic_input','input_composition','source_field_bindings','deterministic_task','semantic_prototype',
        'task_acceptance','task_interpretation','generation_plan','personal_prototype','repair_context')]
    paths+=['src/adaos/services/ai/service.py','src/adaos/services/ai/chatgpt.py',
        'src/adaos/e2e/semantic_route_trial.py','e2e/stand/browser/builder-creation-request.mjs',
        'e2e/stand/browser/builder-creation-facts.mjs','e2e/stand/browser/prototype-navigation.mjs']
    paths += [str(p.relative_to(root)).replace('\\','/') for p in sorted((root/'e2e/builder/development/creation').glob('facts-*'))]
    return {'core':api('GET','/health/ready')['adaos'],
        'evaluation_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
        'client_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root/'src/adaos/integrations/adaos-client',text=True).strip(),
        'builder':runtime_identity(dev,'builder_skill'),'control':runtime_identity(dev,'builder_sdk_control_skill'),
        'files':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in paths}}


def execute_cell(root, bundle, host, ordinal, *, budget_path, budget_offset=0):
    from adaos.apps.cli.active_control import resolve_control_token
    from adaos.services.agent_context import get_ctx
    from adaos.services.builder.prototype_publications import PrototypePublications
    import requests
    root=Path(root).resolve(); bundle=Path(bundle).resolve()
    if not bundle.is_relative_to((root/'e2e/artifacts/builder').resolve()):
        raise ValueError('A standard retained Builder artifact directory is required')
    budget_path=Path(budget_path).resolve()
    if not budget_path.is_relative_to((root/'e2e/artifacts/builder').resolve()) or type(budget_offset) is not int or budget_offset<0:
        raise ValueError('The same retained global allowance and explicit nonnegative cohort offset are required')
    if os.getenv('ENV_TYPE')!='dev' or type(ordinal) is not int or not 0<=ordinal<len(ORDER):
        raise ValueError('Explicit DEV and one declared cell ordinal are required')
    dev=Path(get_ctx().paths.dev_dir()).resolve(); state=Path(get_ctx().paths.state_dir()).resolve()
    session=requests.Session();session.trust_env=False
    session.headers['X-AdaOS-Token']=resolve_control_token(base_url='http://127.0.0.1:8777')
    def api(method,path,payload=None):
        response=session.request(method,'http://127.0.0.1:8777'+path,json=payload,timeout=120)
        response.raise_for_status();return response.json()
    def call(name,args):
        return api('POST','/api/tools/call',{'tool':'builder_sdk_control_skill:'+name,
            'arguments':args,'context':{'webspace_id':host,'current_scenario':'builder'}})['result']
    call('get_model_settings',{'object_type':'scenario','object_id':'builder'})
    api('POST','/api/tools/call',{'tool':'builder_skill:get_workspace_binding','arguments':{'webspace_id':host},
        'context':{'webspace_id':host,'current_scenario':'builder'}})
    before=attempts(state)
    if any(row['status'] not in TERMINAL for row in before.values()):
        raise ValueError('Active AI work prevents a serial trial')
    pins=source_pins(root,dev,api)
    registration={'schema':'adaos.evidence.semantic_route_trial.v1','authorization':'user-20261102-up-to48-personal-account-only',
        'prior_budget':{'used':13,'maximum':16,'separate':True},'maximum_new_provider_calls':48,
        'fresh_route_allowance':20,'remaining_allowance_after_cohort':48-budget_offset-20,
        'shared_budget_ref':str(budget_path),'prior_reserved_allowance':budget_offset,
        'interpretation_transport':'Explicit source-bound JSON string envelope; Core restores only its root constants before full canonical/source admission. Not a typed-provider grammar success or API fallback',
        'order':ORDER,'model':MODEL,'effort':'unchanged native low, verify actual wire',
        'profiles':PROFILES,'pins':pins,'default_promotion':False,
        'scope':'Fresh closed-facts tasks; source-pinned diagnostic comparison, not general NL S1 or statistical reliability proof',
        'stopping':'No manual candidate edits or chat retries. Source/account/budget drift stops further calls; all model/interpretation failures remain outcomes.'}
    bundle.mkdir(parents=True,exist_ok=True)
    registered=bundle/'preregistration.json'
    # JSON reloading converts tuples to arrays; compare canonical JSON objects.
    registration=json.loads(json.dumps(registration))
    if registered.exists():
        if read(registered)!=registration:raise ValueError('Preregistered source/configuration drift')
    else:
        if ordinal!=0:raise ValueError('A new qualification must start at cell zero')
        save(registered,registration)
    task,arm,repetition=ORDER[ordinal];profile,cap=PROFILES[arm]
    expected=read(root/'e2e/builder/development/creation/facts-expectations.json')[task]
    prompt=(root/'e2e/builder/development/creation'/expected['prompt']).read_text(encoding='utf-8')
    project='test_creation_'+hashlib.sha256(f'{bundle.name}:{ordinal}:{task}:{arm}:{repetition}'.encode()).hexdigest()[:18]
    if (dev/'scenarios'/project).exists() or (dev/'projects'/project).exists():
        raise ValueError('Fresh project already exists; never resubmit a retained cell')
    output=bundle/'cells'/f'{ordinal:02}-{arm}-{task}-r{repetition}'
    output.mkdir(parents=True,exist_ok=False)
    budget=RequestBudget(budget_path,maximum=48,limits={'allowance':48})
    expected_slots=budget_offset+sum(PROFILES[row[1]][1] for row in ORDER[:ordinal])
    if budget.snapshot()['attempted_requests']!=expected_slots:
        raise ValueError('Allowance cells cannot be skipped or replayed')
    for path in (bundle/'cells').glob('*/result.json'):
        if read(path).get('accounting_findings'):raise ValueError('Prior accounting failure blocks further inference')
    slots=[budget.reserve('allowance',f'{project}:{i}') for i in range(cap)]
    save(output/'allowance.json',{'slots':slots,'maximum_provider_calls':cap,'reserved_not_observed_usage':True})
    args={'object_type':'project','object_id':project}
    created=call('create_project',{'object_type':'scenario','object_id':project,
        'title':f'[TEST] CE {task} {arm} repeat {repetition}','description':'Owned synthetic source-facts qualification.',
        'webspace_id':host})
    call('set_llm_profile',{**args,'model':'chatgpt/'+MODEL})
    configured=call('set_prototype_generation_profile',{**args,'profile':profile})
    selected=call('select_preview',{**args,'webspace_id':host})
    settings=call('get_model_settings',args)
    prefs=call('get_prompt_context',{'object_type':'scenario','object_id':project})
    if settings.get('prototype_resource')!='ai-resource:personal-chatgpt' or settings.get('prototype_model')!=MODEL:
        raise ValueError('Only the explicitly selected personal ChatGPT account/model is permitted')
    if prefs.get('prototype_generation_profile')!=profile or configured['metadata']['builder_provider_call_limit']!=cap:
        raise ValueError('Whole-turn cap/profile did not persist')
    save(output/'setup.json',{'created':created,'selected':selected,'settings':settings,'profile':configured})
    save(output/'input.json',{'project':project,'model':MODEL,'builderHost':host,'prompt':prompt,
        'timeout_seconds':720,'state_dir':str(state),'dev_dir':str(dev)})
    env={key:value for key,value in os.environ.items() if not key.startswith('ADAOS_E2E_')}
    env.update(ADAOS_E2E_HUB_URL='http://127.0.0.1:8777',ADAOS_E2E_HUB_TOKEN=session.headers['X-AdaOS-Token'],
        ADAOS_E2E_CLIENT_URL='http://127.0.0.1:8100/',ADAOS_E2E_SUBNET_ID=dev.name,
        ADAOS_E2E_CREATION_INPUT=str(output/'input.json'),ADAOS_E2E_OUTPUT=str(output/'chat'))
    def browser(name,timeout):
        try:
            value=subprocess.run(['node',str(root/'e2e/stand/browser'/name)],cwd=root,env=env,
                capture_output=True,timeout=timeout)
            raw=value.stdout+value.stderr;code=value.returncode
        except subprocess.TimeoutExpired as exc:
            raw=(exc.stdout or b'')+(exc.stderr or b'')+b'\nProbe timeout: never retry ambiguous submission.\n';code=124
        with (output/(name+'.log')).open('xb') as stream:stream.write(raw)
        return code
    started=time.perf_counter()
    browser('builder-creation-request.mjs',900)
    chat=read(output/'chat/chat-report.json') if (output/'chat/chat-report.json').exists() else {'submitted':None,'ok':False}
    workflow=call('get_workflow',args);save(output/'workflow.json',workflow)
    roots=[dev/'scenarios'/project/'llm_jobs']
    for directory in (state/'builder/generation_journals').glob('*'):
        if (directory/'owner.json').exists() and read(directory/'owner.json').get('source_scope',{}).get('object_id')==project:roots.append(directory)
    corpus=[]
    for index,directory in enumerate(roots):
        for path in sorted(directory.glob('*.json')):
            value=read(path);corpus.append(json.dumps(value))
            save(output/'journals'/str(index)/path.name,value)
    corpus='\n'.join(corpus);after=attempts(state)
    owned=[row for key,row in after.items() if key not in before and
        (json.dumps(key) in corpus or json.dumps(row['request_id']) in corpus)]
    foreign=[key for key in after if key not in before and key not in {row['id'] for row in owned}]
    findings=[]
    count=sum(row['telemetry'].get('provider_calls',0) for row in owned)
    if chat.get('submitted') and not owned:findings.append('submitted_without_attempt_receipt')
    if foreign:findings.append('unjoined_new_attempts')
    if count>cap:findings.append('whole_turn_cap_exceeded')
    for row in owned:
        if row['status'] not in TERMINAL:findings.append('active_attempt_remains')
        if row['telemetry'].get('tool_calls'):findings.append('unexpected_tool_calls')
        if row['telemetry'].get('wire',{}).get('model')!=MODEL:findings.append('actual_wire_model_not_pinned')
        if row['telemetry'].get('wire',{}).get('reasoning_effort')!='low':findings.append('actual_wire_effort_not_pinned')
        if 'provider_calls' not in row['telemetry']:findings.append('provider_accounting_missing')
    for slot,row in zip(slots,owned):budget.observe(slot,job_id=row['id'],status=row['status'])
    save(output/'provider-attempts.json',{'attempts':owned,'unjoined':foreign,'findings':findings})
    prototype=workflow.get('prototype') or {};oracle={'ok':False,'not_run':'No accepted Alpha'}
    if prototype.get('head_revision')=='001' and prototype.get('publication_ref'):
        publication=PrototypePublications(state).load(prototype['publication_ref'])
        if publication['seed']['project_ref']!='scenario:'+project:raise ValueError('Foreign accepted publication')
        brief=publication.get('brief') or publication.get('seed',{}).get('brief')
        # Brief location is explicitly sourced from the native prepared plan,
        # never reconstructed from the model's accepted values.
        plans=[read(path) for path in output.glob('journals/*/generation-plan-*.json')]
        contracts=[plan['brief']['task_contract'] for plan in plans if plan.get('brief',{}).get('task_contract')]
        if not contracts:raise ValueError('No admitted source contract in owned native journals')
        save(output/'oracle-input.json',{'project':project,'expected':expected,'task_contract':contracts[-1],
            'preview_webspace_id':host+'-dev','accepted':{'project':project,'publication_ref':prototype['publication_ref'],
                'webui':publication['compiled']['webui'],'semantic_document':publication['document']}})
        env.update(ADAOS_E2E_CREATION_INPUT=str(output/'oracle-input.json'),ADAOS_E2E_OUTPUT=str(output/'oracle'))
        browser('builder-creation-facts.mjs',300)
        if (output/'oracle/functional-browser.json').exists():oracle=read(output/'oracle/functional-browser.json')
    result={'schema':'adaos.evidence.semantic_route_cell.v1','ordinal':ordinal,'task':task,'arm':arm,'repetition':repetition,
        'project':project,'submitted':chat.get('submitted'),'accepted_alpha':prototype.get('head_revision')=='001',
        'chat_ok':chat.get('ok'),'browser_ok':oracle.get('ok'),'actual_provider_calls':count,'reserved_allowance':cap,
        'first_pass_success':bool(chat.get('ok') and oracle.get('ok') and count==(1 if arm=='S1' else 2)),
        'within_budget_success':bool(chat.get('ok') and oracle.get('ok') and not findings),
        'generation_ms':chat.get('elapsed_ms'),'elapsed_ms_including_failed_outcome':round((time.perf_counter()-started)*1000),
        'accounting_findings':findings,'chat_failure':chat.get('failure'),'provider_attempts':owned,
        'manual_candidate_edits':0,'api_fallback':False}
    save(output/'result.json',result)
    save(bundle/f'checkpoint-{ordinal:02}.json',{'reserved_allowance':budget.snapshot()['attempted_requests'],
        'observed_calls':sum(read(path)['actual_provider_calls'] for path in (bundle/'cells').glob('*/result.json')),
        'latest':{key:value for key,value in result.items() if key!='provider_attempts'}})
    return result
