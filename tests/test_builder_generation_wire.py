import copy
import json

import pytest

from adaos.sdk import ai
from adaos.sdk.builder import prototype
from adaos.sdk.llm import llm_client
from adaos.services.builder.generation_plan import compile_generation_plan, admit_generation_input, GenerationInputRejected
from adaos.services.builder.personal_prototype import PersonalBuilderJobs
from adaos.services.builder.provider_jobs import PlannedBuilderJobs
from adaos.services.builder.task_interpretation import admit_interpretation
from adaos.services.builder import prototype_mcp
from test_builder_task_interpretation import prepared


def envelope(transport, *, retrieval='inline'):
    intent, task, proposal = prepared()
    task, brief = admit_interpretation(task, proposal, intent)
    plan = compile_generation_plan(task, model='selected', transport=transport, retrieval=retrieval)
    messages = [{'role': 'system', 'content': 'Prototype stage authority.'},
        {'role': 'developer', 'content': 'Distinct Core-owned instruction.'},
        {'role': 'user', 'content': json.dumps({'stable_builder_context': {
            'semantic_invariants': prototype.semantic_generation_guidance()}})},
        {'role': 'user', 'content': json.dumps({'builder_request': {'instruction': intent['statement'],
            'task_spec_ref': task['task_spec_id'], 'generation_plan_ref': plan['generation_plan_id'],
            'prototype_brief': prototype.model_context(brief, compact=True)}})}]
    text = {'format': {'type': 'json_schema', 'strict': True,
        'schema': prototype.semantic_provider_contract(version='v2', brief=brief)}}
    return plan, messages, text


def test_real_root_and_personal_adapter_normalization_preserves_the_same_task_schema_and_component_facts(tmp_path, monkeypatch):
    from adaos.services import context_control
    service = context_control.ContextControlService(tmp_path)
    monkeypatch.setattr(context_control, 'ContextControlService', lambda: service)
    root_plan, root_messages, text = envelope('root')
    root, _ = llm_client._llm_root_payload(root_messages, model='selected', text=text,
        request_id='root-parity', generation_plan=root_plan)
    captured = []
    monkeypatch.setattr(ai, 'submit', lambda **value: captured.append(value) or {
        'job_id': 'personal-attempt', 'status': 'queued', 'input_digest': 'private-attempt-digest'})
    personal_plan, personal_messages, _ = envelope('personal')
    result = PersonalBuilderJobs({'builder_ai_resource_ref': ai.RESOURCE_REF,
        'builder_subscription_output_policy': 'provider_default', 'builder_ai_expected_binding': 'exact-binding',
        'builder_llm_model': 'selected'}).submit(personal_messages, model='selected', request_id='personal-parity',
            text=text, generation_plan=personal_plan)
    actual = captured[0]
    assert actual['instructions'] == root['instructions']
    assert actual['instructions'].count('Distinct Core-owned instruction.') == 1
    expected = [{'role': item['role'], 'content': item['content'][0]['text']} for item in root['input']]
    # Transport plans have distinct identities, not distinct user meanings.
    for rows in (expected, actual['inputs']):
        for row in rows:
            try: body = json.loads(row['content'])
            except ValueError: continue
            if 'builder_request' in body:
                body['builder_request'].pop('generation_plan_ref')
                row['content'] = json.dumps(body, sort_keys=True)
    assert actual['inputs'] == expected
    assert actual['model'] == root['model'] and actual['text'] == root['text']
    assert 'platform_contracts' in actual['instructions']
    assert result['context_admission']['provider_attempt_input_digest'] == 'private-attempt-digest'
    assert result['context_admission']['status'] == 'admitted'


@pytest.mark.parametrize('fault', [None, 'locator', 'tools', 'pin'])
def test_omitted_guidance_requires_complete_index_exact_pin_and_actual_guide_tools(fault):
    plan, messages, text = envelope('personal', retrieval='model_mcp')
    body = json.loads(messages[2]['content'])
    body['stable_builder_context']['semantic_invariants'].pop('command_ownership')
    index = prototype_mcp.retrieval_index(available=True)
    body['stable_builder_context']['prototype_retrieval'] = index
    tools = [{'type': 'mcp', 'server_label': prototype_mcp.SERVER_LABEL,
        'allowed_tools': list(prototype_mcp.OPENAI_TOOLS)}]
    if fault == 'locator': index['dimensions'] = [row for row in index['dimensions'] if row['id'] != 'command_ownership']
    elif fault == 'tools': tools = [{'type': 'web_search'}]
    elif fault == 'pin': index['library_digest'] = 'sha256:' + '0' * 64
    messages[2]['content'] = json.dumps(body)
    if fault:
        with pytest.raises(GenerationInputRejected):
            admit_generation_input(messages, model='selected', text=text, tools=tools, tool_choice='auto', transport='personal', plan=plan)
    else:
        assert admit_generation_input(messages, model='selected', text=text, tools=tools,
            tool_choice='auto', transport='personal', plan=plan)['status'] == 'admitted'


def test_native_wire_receipt_keeps_job_identity_and_never_records_raw_tool_credentials(tmp_path):
    class Provider:
        def submit(self, messages, **options):
            assert options['provider_max_attempts'] == 1
            return {'job_id': 'same-job', 'context_admission': {'effective_input_digest': 'sha256:wire', 'tool_count': 1}}
    plan, _, _ = envelope('personal')
    jobs = PlannedBuilderJobs(Provider(), plan, tmp_path)
    first = jobs.submit([], request_id='native-task', tools=[{'authorization': 'PRIVATE'}])
    assert jobs.submit([], request_id='native-task', tools=[{'authorization': 'PRIVATE'}]) == first
    files = list(tmp_path.glob('wire-*.json'))
    assert len(files) == 1
    value = json.loads(files[0].read_text())
    assert value['job_id'] == 'same-job' and value['request_id'] == 'native-task'
    assert 'PRIVATE' not in files[0].read_text()


def test_public_provider_facade_passes_the_native_journal_owner(tmp_path, monkeypatch):
    from adaos.services.builder import provider_jobs
    calls = []
    monkeypatch.setattr(provider_jobs, 'provider_jobs', lambda metadata, **kwargs: calls.append((metadata, kwargs)) or 'delegate')
    assert prototype.provider_jobs({'selected': 'personal'}, journal_dir=tmp_path) == 'delegate'
    assert calls == [({'selected': 'personal'}, {'journal_dir': tmp_path})]


@pytest.mark.parametrize('fault', [None, 'missing', 'broadened'])
def test_adapters_cannot_omit_or_widen_a_core_write_scope(fault):
    from adaos.services.builder.task_spec import seal
    plan, messages, text = envelope('personal')
    scope = {'schema':'adaos.builder.fixture_write_scope.v1','writes':[{'field_ref':'cpu','after':65}]}
    plan = seal({**plan,'write_scope':scope},id_key='generation_plan_id',prefix='generation-plan:')
    body = json.loads(messages[-1]['content'])
    body['builder_request'].update(generation_plan_ref=plan['generation_plan_id'],write_scope=copy.deepcopy(scope))
    if fault == 'missing':body['builder_request'].pop('write_scope')
    elif fault == 'broadened':body['builder_request']['write_scope']['writes'].append({'field_ref':'disk','after':0})
    messages[-1]['content'] = json.dumps(body)
    if fault:
        with pytest.raises(GenerationInputRejected,match='write_scope_conflict'):
            admit_generation_input(messages,model='selected',text=text,transport='personal',plan=plan)
    else:
        assert admit_generation_input(messages,model='selected',text=text,transport='personal',plan=plan)['status']=='admitted'


@pytest.mark.parametrize('fault',[None,'body','schema'])
def test_locale_closure_matches_the_actual_provider_schema(fault):
    from adaos.services.builder.task_spec import seal
    intent,task,proposal=prepared()
    _,brief=admit_interpretation(task,proposal,intent)
    plan,messages,text=envelope('personal')
    plan=seal({**plan,'output_locales':['ru']},id_key='generation_plan_id',prefix='generation-plan:')
    body=json.loads(messages[-1]['content'])
    body['builder_request'].update(generation_plan_ref=plan['generation_plan_id'],output_locales=['ru'])
    text['format']['schema']=prototype.semantic_provider_contract(version='v2',brief=brief,locales=('ru',))
    if fault=='body':body['builder_request']['output_locales']=['en','ru']
    elif fault=='schema':text['format']['schema']=prototype.semantic_provider_contract(version='v2',brief=brief)
    messages[-1]['content']=json.dumps(body)
    if fault:
        with pytest.raises(GenerationInputRejected,match='output_locale_conflict'):
            admit_generation_input(messages,model='selected',text=text,transport='personal',plan=plan)
    else:assert admit_generation_input(messages,model='selected',text=text,transport='personal',plan=plan)['status']=='admitted'


@pytest.mark.parametrize('fault',[False,True])
def test_root_final_adapter_preserves_the_exact_admitted_schema_digest(tmp_path,monkeypatch,fault):
    from adaos.services import context_control
    from adaos.services.builder.task_spec import seal
    from adaos.services.builder_context import digest
    service=context_control.ContextControlService(tmp_path)
    monkeypatch.setattr(context_control,'ContextControlService',lambda:service)
    plan,messages,text=envelope('root')
    plan=seal({**plan,'output_schema_digest':digest(text['format']['schema'])},id_key='generation_plan_id',prefix='generation-plan:')
    body=json.loads(messages[-1]['content'])
    body['builder_request']['generation_plan_ref']=plan['generation_plan_id']
    messages[-1]['content']=json.dumps(body)
    if fault:text['format']['schema']['$defs']['id']={'type':'string','enum':['unexpected-id']}
    if fault:
        with pytest.raises(GenerationInputRejected,match='output_schema_conflict'):
            llm_client._llm_root_payload(messages,model='selected',text=text,generation_plan=plan)
    else:
        payload,_=llm_client._llm_root_payload(messages,model='selected',text=text,generation_plan=plan)
        assert digest(payload['text']['format']['schema'])==plan['output_schema_digest']
