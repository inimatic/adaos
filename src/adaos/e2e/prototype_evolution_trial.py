"""Budgeted ordinary-chat evolution of one model-created, owned Prototype.

Failed outcomes and reservations are immutable. Oracles never enter model input.
Only this owned application is mutable; no global model/profile preference writes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from .builder_creation_episode import attempts, TERMINAL
from .request_budget import RequestBudget
from .semantic_route_trial import MODEL, source_pins, read, save, execution_registration

LIMITS = {'exploration': 21, 'reasoning': 18, 'diagnostic': 6}
PROFILES = {'low': 's2-inline-no-repair-v1',
            'medium': 's2-inline-no-repair-medium-v1', 'high': 's2-inline-no-repair-high-v1'}
IMPLEMENTED_CHECKS = {'labels','column_order','relative_column_order','search','priority_filter','accept',
                      'teams','related_names','details','tabs','selection_isolation'}


def expected_records(ladder, level):
    records = copy.deepcopy(ladder['records'])
    for step in ladder['steps'][:level + 1]:
        records += copy.deepcopy(step.get('extra_records', []))
        for record in records:
            record['fields'].update(step.get('extra_fields', {}).get(record['subject'], {}))
    return records


def confirmed_turn(path):
    """A read-only oracle correction never rewrites the original observation."""
    path = Path(path)
    turn = read(path)
    if turn.get('functional_success'):
        return True
    receipt_path = path.parent/'oracle-revalidation-accepted.json'
    if not receipt_path.exists():
        return False
    receipt = read(receipt_path)
    return (turn.get('chat_ok') and not turn.get('accounting_findings')
        and receipt.get('model_calls') == 0 and receipt.get('oracle_ok') is True
        and receipt.get('original_result_sha256') == hashlib.sha256(path.read_bytes()).hexdigest()
        and receipt.get('same_accepted_revision') == turn.get('actual_revision')
        and receipt.get('cause') in {'harness_enum_capitalization_false_negative',
                                     'harness_css_caption_casing_false_negative'})


def execute_turn(root, bundle, host, level, *, client_source, client_url,
                 effort='low', phase='exploration', trial=None):
    from adaos.apps.cli.active_control import resolve_control_token
    from adaos.services.agent_context import get_ctx
    from adaos.services.builder.prototype_publications import PrototypePublications
    import requests
    root, bundle = Path(root).resolve(), Path(bundle).resolve()
    if (not bundle.is_relative_to((root/'e2e/artifacts/builder').resolve())
            or os.getenv('ENV_TYPE') != 'dev' or effort not in PROFILES or phase not in LIMITS):
        raise ValueError('Explicit owned DEV evidence, effort and allowance phase are required')
    fixture = root/'e2e/builder/development/evolution/work-queue.v1.json'
    ladder = read(fixture)
    if type(level) is not int or not 0 <= level < len(ladder['steps']):
        raise ValueError('Unknown preregistered complexity level')
    if set(ladder['steps'][level]['checks']) - IMPLEMENTED_CHECKS:
        raise ValueError('Implement and freeze the independent next-level oracle before any paid call')
    if phase == 'exploration' and (effort != 'low' or trial is not None):
        raise ValueError('The exploratory staircase is low-only, one source turn per level')
    dev, state = Path(get_ctx().paths.dev_dir()).resolve(), Path(get_ctx().paths.state_dir()).resolve()
    session = requests.Session(); session.trust_env = False
    session.headers['X-AdaOS-Token'] = resolve_control_token(base_url='http://127.0.0.1:8777')
    def api(method, path, payload=None):
        response = session.request(method, 'http://127.0.0.1:8777'+path, json=payload, timeout=120)
        response.raise_for_status(); return response.json()
    def call(name, args):
        return api('POST', '/api/tools/call', {'tool':'builder_sdk_control_skill:'+name,
            'arguments':args, 'context':{'webspace_id':host,'current_scenario':'builder'}})['result']
    call('get_model_settings', {'object_type':'scenario','object_id':'builder'})
    before = attempts(state)
    if any(row['status'] not in TERMINAL for row in before.values()):
        raise ValueError('Active inference prevents a serial source-pinned trial')
    pins = source_pins(root, dev, api, Path(client_source))
    for path in (Path(__file__), fixture, root/'e2e/stand/browser/builder-evolution.mjs',
                 root/'e2e/stand/browser/prototype-source-scalar.mjs',
                 *(root/'src/adaos/services/builder'/name for name in (
                    'semantic_ui_constraints.py','task_assertions.py','interpretation_revalidation.py',
                    'task_spec.py','source_obligations.py','source_relations.py','model_controls.py',
                    'generation_authoring.py','generation_journals.py'))):
        pins['files'][str(path.relative_to(root)).replace('\\','/')] = hashlib.sha256(path.read_bytes()).hexdigest()
    project = 'test_creation_'+hashlib.sha256((bundle.name+':repair-queue').encode()).hexdigest()[:18]
    args = {'object_type':'project','object_id':project}
    bundle.mkdir(parents=True, exist_ok=True)
    registration = {'schema':'adaos.evidence.prototype_evolution_trial.v1',
        'authorization':'user-20261102-up-to45-ChatGPT-account-evolution-and-reasoning',
        'maximum_calls':45,'limits':LIMITS,'model':MODEL,'api_fallback':False,'repair':False,
        'project':project,'host':host,'pins':pins,'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),
        'client_source':str(client_source),'client_url':client_url,
        'method':'Escalate one actual app, checking previous obligations. Attribute first failure before model-bound claims. Native effort arms are end-to-end logical-task comparisons unless exact reasoning-only wire eligibility independently passes.',
        'stopping':'Never repeat ambiguous submission. No product/input changes inside a cohort. Known pipeline failures are not reasoning benchmarks. Any fixes start separately pinned cohorts; every failed outcome stays.',
        'preservation_scope':'Source fixtures and prior authored behavior across revisions; prototype runtime action probes are local and do not assert production data-migration durability.'}
    registration = json.loads(json.dumps(registration))
    prereg = bundle/'preregistration.json'
    if prereg.exists():
        previous = read(prereg)
        amendments=sorted(bundle.glob('harness-amendment-*.json'))
        if amendments:previous=read(amendments[-1])['registration']
        cohorts=sorted(bundle.glob('cohort-amendment-*.json'))
        if cohorts:previous=read(cohorts[-1])['registration']
        if execution_registration(previous) != execution_registration(registration):
            raise ValueError('Preregistered source/runtime/task drift; no new inference')
    elif level == 0 and phase == 'exploration': save(prereg, registration)
    else: raise ValueError('Start the owned app at level zero')
    output = bundle/'turns'/f'{phase}-{level:02}-{effort}{"-"+trial if trial else ""}'
    output.mkdir(parents=True, exist_ok=False)
    if phase == 'exploration':
        for index in range(level):
            prior_path=bundle/'turns'/f'exploration-{index:02}-low'/'result.json'
            if not confirmed_turn(prior_path):
                revalidated = False
                adopted=bundle/f'accepted-level-{index:02}.json'
                if not revalidated and adopted.exists():
                    adoption=read(adopted);replacement=(bundle/adoption['result_ref']).resolve()
                    if not replacement.is_relative_to(bundle) or replacement.name!='result.json':
                        raise ValueError('Foreign continuation result cannot advance an owned app')
                    retry=read(replacement)
                    revalidated=(adoption['sha256']==hashlib.sha256(replacement.read_bytes()).hexdigest()
                        and confirmed_turn(replacement) and retry['project']==project
                        and retry['level']==index and retry['actual_revision']==f'{index+1:03}')
                if not revalidated:
                    raise ValueError('Cannot escalate an unaccepted or failed previous level')
    exists = (dev/'projects'/project).exists() or (dev/'scenarios'/project).exists()
    if level == 0 and phase == 'exploration':
        if exists: raise ValueError('Never reseed an existing owned application')
        created = call('create_project', {'object_type':'scenario','object_id':project,
            'title':'[TEST] Repair queue · complexity and evolution',
            'description':'User-authorized model-created prototype evolution experiment, local synthetic data.',
            'webspace_id':host})
        save(output/'creation.json', created)
        call('set_llm_profile', {**args,'model':'chatgpt/'+MODEL})
    elif not exists: raise ValueError('The actual prior application is missing')
    workflow_before = call('get_workflow', args)
    base_revision = (workflow_before.get('prototype') or {}).get('head_revision')
    save(output/'workflow-before.json', workflow_before)
    if phase == 'exploration' and base_revision != (f'{level:03}' if level else None):
        raise ValueError('Unexpected owned revision; no model call')
    if phase != 'exploration':
        # Comparison runs require a separately reviewed immutable logical task/base.
        basis = read(bundle/f'{phase}-basis-{level:02}.json')
        if basis['level'] != level or basis['base_revision'] != base_revision:
            raise ValueError('Comparison basis changed; do not confuse continued evolution with an effort treatment')
    profile = ladder['steps'][level].get('profile') if level == 0 else PROFILES[effort]
    if level == 0 and effort != 'low': raise ValueError('Seed uses the unchanged low control')
    configured = call('set_prototype_generation_profile', {**args,'profile':profile})
    selected = call('select_preview', {**args,'webspace_id':host})
    settings = call('get_model_settings', args)
    if settings.get('prototype_model') != MODEL or settings.get('prototype_resource') != 'ai-resource:personal-chatgpt':
        raise ValueError('Only the selected ChatGPT account/model is authorized')
    cap = 1 if level == 0 else 2
    if configured['metadata']['builder_provider_call_limit'] != cap: raise ValueError('Wrong whole-turn cap')
    budget = RequestBudget(bundle/'allowance-45.sqlite', maximum=45, limits=LIMITS)
    slots = [budget.reserve(phase, output.name+f':{index}') for index in range(cap)]
    save(output/'allowance.json', {'slots':slots,'cap':cap,'reserved_before_browser_submission':True})
    save(output/'setup.json', {'settings':settings,'configured':configured,'selected':selected})
    prompt = ladder['steps'][level]['prompt']
    if phase != 'exploration':
        prompt=basis.get('source_statement',prompt)
    expected_revision = f'{int(base_revision or "000")+1:03}'
    save(output/'input.json', {'project':project,'model':MODEL,'builderHost':host,'prompt':prompt,
        'base_revision':base_revision,'expected_revision':expected_revision,'timeout_seconds':720,
        'state_dir':str(state),'dev_dir':str(dev)})
    env = {key:value for key,value in os.environ.items() if not key.startswith('ADAOS_E2E_')}
    env.update(ADAOS_E2E_HUB_URL='http://127.0.0.1:8777',ADAOS_E2E_HUB_TOKEN=session.headers['X-AdaOS-Token'],
        ADAOS_E2E_CLIENT_URL=client_url,ADAOS_E2E_SUBNET_ID=dev.name,
        ADAOS_E2E_CREATION_INPUT=str(output/'input.json'),ADAOS_E2E_OUTPUT=str(output/'chat'))
    def browser(name, timeout):
        try:
            process = subprocess.run(['node',str(root/'e2e/stand/browser'/name)],cwd=root,env=env,
                capture_output=True,timeout=timeout)
            raw, code = process.stdout+process.stderr, process.returncode
        except subprocess.TimeoutExpired as error:
            raw, code = (error.stdout or b'')+(error.stderr or b'')+b'\nObserver timeout; never resubmit.\n',124
        with (output/(name+'.log')).open('xb') as stream:stream.write(raw)
        return code
    started = time.perf_counter()
    browser('builder-creation-request.mjs', 900)
    chat = read(output/'chat/chat-report.json') if (output/'chat/chat-report.json').exists() else {'ok':False,'submitted':None}
    after = attempts(state)
    roots = [dev/'scenarios'/project/'llm_jobs']
    roots += [directory for directory in (state/'builder/generation_journals').glob('*')
        if (directory/'owner.json').exists() and read(directory/'owner.json').get('source_scope',{}).get('object_id') == project]
    corpus = []
    for index,directory in enumerate(roots):
        for path in sorted(directory.glob('*.json')):
            value = read(path); corpus.append(json.dumps(value));save(output/'journals'/str(index)/path.name,value)
    corpus = '\n'.join(corpus)
    owned = [row for key,row in after.items() if key not in before and
        (json.dumps(key) in corpus or json.dumps(row['request_id']) in corpus)]
    foreign = [key for key in after if key not in before and key not in {row['id'] for row in owned}]
    findings = ['unjoined_attempts'] if foreign else []
    count = sum(row['telemetry'].get('provider_calls',0) for row in owned)
    if count > cap:findings.append('whole_turn_cap_exceeded')
    for row in owned:
        wire = row['telemetry'].get('wire',{})
        if row['status'] not in TERMINAL:findings.append('active_attempt_remains')
        if wire.get('model') != MODEL or wire.get('reasoning_effort') != effort:findings.append('actual_model_effort_drift')
        if row['telemetry'].get('tool_calls') or wire.get('tool_types'):findings.append('unexpected_tools')
    for slot,row in zip(slots,owned):budget.observe(slot,job_id=row['id'],status=row['status'])
    save(output/'provider-attempts.json', {'attempts':owned,'unjoined':foreign,'findings':findings})
    workflow = call('get_workflow', args);save(output/'workflow-after.json',workflow)
    prototype = workflow.get('prototype') or {}
    oracle = {'ok':False,'not_run':'No newly accepted Alpha revision'}
    if prototype.get('head_revision') == expected_revision and prototype.get('publication_ref'):
        publication = PrototypePublications(state).load(prototype['publication_ref'])
        assert publication['seed']['project_ref'] == 'scenario:'+project
        new_jobs = {row['id'] for row in owned}
        refs = {read(path).get('execution_run_binding',{}).get('generation_plan_ref')
            for path in output.glob('journals/*/wire-*.json') if read(path).get('job_id') in new_jobs}
        plans = [read(path) for path in output.glob('journals/*/generation-plan-*.json')
            if read(path)['generation_plan']['generation_plan_id'] in refs]
        if len(plans) != 1:raise ValueError('One exact owned new generation plan must define the source contract')
        oracle_input = {'project':project,'revision':expected_revision,'level':level,
            'preview_webspace_id':host+'-dev','records':expected_records(ladder,level),
            'checks':ladder['steps'][level]['checks'],'task_contract':plans[0]['brief']['task_contract'],
            'accepted':{'semantic_document':publication['document'],'webui':publication['compiled']['webui']}}
        save(output/'oracle-input.json',oracle_input)
        env.update(ADAOS_E2E_CREATION_INPUT=str(output/'oracle-input.json'),ADAOS_E2E_OUTPUT=str(output/'oracle'))
        browser('builder-evolution.mjs', 360)
        if (output/'oracle/result.json').exists():oracle = read(output/'oracle/result.json')
    result = {'schema':'adaos.evidence.prototype_evolution_turn.v1','project':project,'level':level,
        'step':ladder['steps'][level]['id'],'phase':phase,'effort':effort,'base_revision':base_revision,
        'expected_revision':expected_revision,'actual_revision':prototype.get('head_revision'),
        'submitted':chat.get('submitted'),'chat_ok':chat.get('ok'),'browser_ok':oracle.get('ok'),
        'functional_success':bool(chat.get('ok') and oracle.get('ok') and not findings),
        'provider_calls':count,'reserved_allowance':cap,'accounting_findings':findings,
        'generation_ms':chat.get('elapsed_ms'),'whole_outcome_ms':round((time.perf_counter()-started)*1000),
        'chat_failure':chat.get('failure'),'oracle_errors':oracle.get('errors'),
        'manual_candidate_edits':0,'api_fallback':False,'repair':False,
        'model_bound_proven':False}
    save(output/'result.json',result)
    save(bundle/('checkpoint-'+output.name+'.json'),{'reserved':budget.snapshot()['attempted_requests'],'turn':result})
    return result
