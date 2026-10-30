import copy
import json

import pytest

from adaos.services.builder.task_pipeline import prepare_generation_request
from adaos.services.builder.task_interpretation import TaskInterpretationRejected
from test_builder_task_interpretation import prepared


class Provider:
    def __init__(self, proposal, fail_observation=False):
        self.proposal = proposal
        self.submissions = []
        self.observations = []
        self.fail_observation = fail_observation
    def submit(self, messages, **options):
        self.submissions.append((messages, options))
        return {'job_id': 'same-job', 'status': 'queued'}
    def wait(self, job_id, **options):
        self.observations.append(job_id)
        if self.fail_observation:
            raise TimeoutError('Observer disconnected')
        return {'job_id': job_id, 'status': 'succeeded', 'output_text': json.dumps(self.proposal),
                'telemetry': {'provider_calls': 1, 'usage': {'input_tokens': 12, 'output_tokens': 34}}}


def invocation(tmp_path, provider):
    intent, task, _ = prepared()
    request = {'output_mode': 'semantic_v2', 'prototype_brief': {}, 'base_request': {},
               'dynamic_request': {'instruction': intent['statement'], 'scenario_id': 'qualification'}}
    return prepare_generation_request(request, metadata={'builder_task_pipeline': '1',
        'builder_ai_resource_ref': 'ai-resource:personal-chatgpt', 'prototype_intent': intent},
        model='exact-selected-model', reasoning={'effort': 'low'}, provider=provider, journal_dir=tmp_path)


def test_two_stage_preparation_binds_exact_task_and_replays_without_second_interpretation(tmp_path):
    _, _, proposal = prepared()
    provider = Provider(proposal)
    request = invocation(tmp_path, provider)
    assert request['generation_plan']['route'] == 'two_stage'
    assert request['interpretation_receipt']['provider_calls'] == 1
    assert request['interpretation_receipt']['usage']['input_tokens'] == 12
    assert len(provider.submissions) == 1
    assert provider.submissions[0][1]['tools'] == []
    assert request['base_request']['selected_ui_capabilities']['qualification']['prototype_brief'] == request['prototype_brief']
    repeated = invocation(tmp_path, provider)
    assert repeated == request
    assert len(provider.submissions) == 1 and provider.observations == ['same-job']


@pytest.mark.parametrize('existing',[[],['en','ru']])
def test_locale_closure_is_recomputed_from_admitted_meaning_and_baseline_not_a_seed_default(tmp_path,existing):
    from adaos.services.builder_intent import capture_intent
    from adaos.sdk.builder.prototype import semantic_revision_context
    from test_builder_task_assertions import amendment
    from test_builder_task_preservation import prepared_change
    previous,_=prepared_change()
    _,_,initial=prepared()
    base=invocation(tmp_path,Provider(initial))['prototype_brief']
    intent,_,proposal,_=amendment(base)
    previous['title']={code:'Existing' for code in existing} or {'ru':'Исходное'}
    if not existing:
        def ru_only(node):
            if isinstance(node,dict):
                if set(node)<= {'key','en','ru'} and ('ru' in node or 'en' in node):
                    node['ru']=node.get('ru',node.get('en'))
                    node.pop('en',None)
                for child in node.values():ru_only(child)
            elif isinstance(node,list):
                for child in node:ru_only(child)
        ru_only(previous)
    capsule=semantic_revision_context(previous,base,source_ref='semantic.webui.json',revision='001')
    result=prepare_generation_request({'output_mode':'semantic_v2','base_request':{},'output_locales':['en','ru'],
        'dynamic_request':{'instruction':intent['statement'],'current_semantic':capsule,'output_locales':['en','ru']}},
        metadata={'builder_task_pipeline':'1','prototype_intent':intent},model='selected',reasoning=None,
        provider=Provider(proposal),journal_dir=tmp_path,previous_brief=base)
    expected=existing or ['ru']
    assert result['output_locales']==expected
    assert result['dynamic_request']['output_locales']==expected
    assert result['generation_plan']['output_locales']==expected


def test_observation_restart_recovers_the_same_durable_interpretation_job(tmp_path):
    _, _, proposal = prepared()
    provider = Provider(proposal, fail_observation=True)
    with pytest.raises(TimeoutError): invocation(tmp_path, provider)
    provider.fail_observation = False
    request = invocation(tmp_path, provider)
    assert request['task_spec']['interpretation_status'] == 'admitted'
    assert len(provider.submissions) == 1 and provider.observations == ['same-job', 'same-job']


def test_bad_interpretation_is_retained_and_never_becomes_a_candidate_request(tmp_path):
    _, _, proposal = prepared()
    proposal['facts'][0]['value'] = 0
    provider = Provider(proposal)
    with pytest.raises(TaskInterpretationRejected): invocation(tmp_path, provider)
    assert len(provider.submissions) == 1
    retained = json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))
    assert retained['result']['output_text'] == json.dumps(proposal)
    assert retained['admission']['status'] == 'rejected'
    assert retained['admission']['findings'][0]['code'].startswith('task.')


def test_uncertain_submission_needs_recovery_and_cannot_spend_twice(tmp_path):
    _, _, proposal = prepared()
    class Interrupted(Provider):
        def submit(self, messages, **options):
            self.submissions.append((messages, options))
            raise TimeoutError('Acknowledgement lost')
    provider = Interrupted(proposal)
    with pytest.raises(TimeoutError): invocation(tmp_path, provider)
    with pytest.raises(ValueError, match='outcome is unknown'): invocation(tmp_path, provider)
    assert len(provider.submissions) == 1


def test_unavailable_mcp_is_admitted_before_spending_on_interpretation(tmp_path, monkeypatch):
    monkeypatch.setenv('ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED', '1')
    from adaos.services.builder import prototype_mcp
    monkeypatch.setattr(prototype_mcp, 'enabled', lambda: False)
    _, _, proposal = prepared()
    provider = Provider(proposal)
    with pytest.raises(TaskInterpretationRejected) as error:
        invocation(tmp_path, provider)
    assert error.value.findings[0]['code'] == 'task.retrieval_unavailable'
    assert not provider.submissions


def test_exact_source_replay_is_one_generation_stage_and_never_reinterprets(tmp_path):
    from adaos.services.builder_intent import capture_intent
    intent, _, proposal = prepared()
    provider = Provider(proposal)
    original = invocation(tmp_path, provider)
    later = capture_intent(intent['statement'], locale=intent['locale'], project_ref='project:qualification',
        source={'kind': 'chat', 'ref': 'a-new-user-turn'})
    request = {'output_mode': 'semantic_v2', 'base_request': {},
        'dynamic_request': {'instruction': later['statement'], 'scenario_id': 'qualification'}}
    result = prepare_generation_request(request, metadata={'builder_task_pipeline': '1',
        'builder_ai_resource_ref': 'ai-resource:personal-chatgpt', 'prototype_intent': later},
        model='exact-selected-model', reasoning={'effort': 'low'}, provider=provider,
        previous_brief=original['prototype_brief'], journal_dir=tmp_path)
    assert result['generation_plan']['route'] == 'single_stage'
    assert result['generation_preflight_plan']['route'] == 'single_stage'
    assert result['interpretation_receipt']['provider_calls'] == 0
    assert len(provider.submissions) == 1
    assert result['prototype_brief']['task_contract']['derivation']['kind'] == 'exact_source_replay'
    assert result['prototype_brief']['task_contract']['derivation']['meaning_source_digest'] == intent['digest']
    assert result['task_spec']['source']['digest'] == later['digest'] != intent['digest']
    assert result['task_spec']['acceptance'] == original['task_spec']['acceptance']


def test_a_self_sealed_contract_cannot_claim_single_stage_execution_without_core_admission(tmp_path):
    from adaos.services.builder.task_interpretation import admit_interpretation
    intent, task, proposal = prepared()
    _, brief = admit_interpretation(task, proposal, intent)
    provider = Provider(proposal)
    request = {'output_mode': 'semantic_v2', 'base_request': {}, 'dynamic_request': {'instruction': intent['statement']}}
    with pytest.raises(TaskInterpretationRejected) as error:
        prepare_generation_request(request, metadata={'builder_task_pipeline': '1',
            'prototype_intent': intent, 'prototype_brief': brief}, model='exact-selected-model', reasoning=None,
            provider=provider, journal_dir=tmp_path)
    assert error.value.findings[0]['code'] == 'task.seed_admission_missing'
    assert not provider.submissions


def test_reused_task_source_is_checked_against_actual_instruction_before_any_model_call(tmp_path):
    from adaos.services.builder.task_pipeline import admit_task_source
    intent, _, proposal = prepared()
    provider = Provider(proposal)
    request = {'output_mode': 'semantic_v2', 'base_request': {}, 'dynamic_request': {'instruction': 'Unrelated source'},
        'task_source': admit_task_source(intent['statement'], metadata={'prototype_intent': intent})}
    with pytest.raises(ValueError, match='disagree'):
        prepare_generation_request(request, metadata={'builder_task_pipeline': '1'}, model='selected',
            reasoning=None, provider=provider, journal_dir=tmp_path)
    assert not provider.submissions


@pytest.mark.parametrize('history', ['legacy', 'foreign'])
def test_known_history_blockers_precede_paid_interpretation(tmp_path, history):
    from adaos.services.builder_intent import compile_prototype_brief, capture_intent
    from adaos.services.builder.task_interpretation import admit_interpretation
    intent, task, proposal = prepared()
    previous = compile_prototype_brief(intent)
    if history == 'foreign':
        from adaos.services.builder.task_spec import seal
        _, previous = admit_interpretation(task, proposal, intent)
        previous['task_contract']['source']['scope']['project_ref'] = 'project:other'
        previous = seal(previous, id_key='brief_id', prefix='brief:')
    provider = Provider(proposal)
    later = capture_intent('Keep all prior behavior.', project_ref='project:qualification')
    with pytest.raises(TaskInterpretationRejected) as error:
        prepare_generation_request({'output_mode':'semantic_v2', 'base_request':{},
            'dynamic_request':{'instruction':later['statement']}},
            metadata={'builder_task_pipeline':'1','prototype_intent':later}, model='selected', reasoning=None,
            provider=provider, journal_dir=tmp_path, previous_brief=previous)
    assert error.value.findings[0]['code'] == ('task.legacy_history_unadmitted' if history == 'legacy' else 'task.history_scope_conflict')
    assert not provider.submissions and not provider.observations


def test_source_mismatch_is_typed_and_cannot_spend(tmp_path):
    intent, _, proposal = prepared()
    provider = Provider(proposal)
    with pytest.raises(TaskInterpretationRejected) as error:
        prepare_generation_request({'output_mode':'semantic_v2','base_request':{},
            'dynamic_request':{'instruction':'Unrelated'}},
            metadata={'builder_task_pipeline':'1','prototype_intent':intent}, model='selected', reasoning=None,
            provider=provider,journal_dir=tmp_path)
    assert error.value.findings[0]['gate'] == 'G0'
    assert error.value.findings[0]['code'] == 'task.source_mismatch'
    assert not provider.submissions


def test_pre_generation_failure_reuses_admitted_meaning_only_on_the_same_history_basis(tmp_path, monkeypatch):
    from adaos.services.builder import task_preservation
    from adaos.services.builder_intent import capture_intent
    from test_builder_task_assertions import amendment
    _,_,initial=prepared()
    initial_provider=Provider(initial)
    original=invocation(tmp_path,initial_provider)
    intent,_,proposal,_=amendment(original['prototype_brief'])
    provider=Provider(proposal)
    request={'output_mode':'semantic_v2','base_request':{},'dynamic_request':{'instruction':intent['statement']}}
    options={'metadata':{'builder_task_pipeline':'1','prototype_intent':intent},'model':'selected',
        'reasoning':None,'provider':provider,'journal_dir':tmp_path,'previous_brief':original['prototype_brief']}
    def refuse(*args):raise TaskInterpretationRejected([{'code':'task.fixture_scope_unresolved','gate':'G4','owner':'capability_admission','detail':'No generation'}])
    with monkeypatch.context() as patch:
        patch.setattr(task_preservation,'fixture_write_scope',refuse)
        with pytest.raises(TaskInterpretationRejected):prepare_generation_request(request,**options)
    assert len(provider.submissions)==1
    failure=json.loads(next(tmp_path.glob('task-preflight-*.json')).read_text(encoding='utf-8'))
    assert failure['candidate_generation_submitted'] is False
    later=capture_intent(intent['statement'],project_ref='project:qualification',source={'kind':'chat','ref':'new-turn'})
    options['metadata']['prototype_intent']=later
    result=prepare_generation_request(request,**options)
    assert result['generation_plan']['route']=='single_stage'
    assert result['interpretation_receipt']['provider_calls']==0
    assert result['interpretation_receipt']['recovered_interpretation_job_id']=='same-job'
    assert len(provider.submissions)==1
    from adaos.services.builder.task_pipeline import _recover_admitted_interpretation
    changed=copy.deepcopy(original['prototype_brief'])
    changed['task_contract']['design_freedom'].append('different accepted history')
    assert _recover_admitted_interpretation(later,changed,tmp_path,{}) is None
    retained = next(path for path in tmp_path.glob('builder-interpretation-*.json')
        if json.loads(path.read_text(encoding='utf-8'))['task_spec']['source']['statement']==intent['statement'])
    record=json.loads(retained.read_text(encoding='utf-8'))
    bad=json.loads(record['result']['output_text'])
    bad['facts'][0]['value']=0
    record['result']['output_text']=json.dumps(bad)
    retained.write_text(json.dumps(record),encoding='utf-8')
    with pytest.raises(TaskInterpretationRejected):prepare_generation_request(request,**options)
    assert len(provider.submissions)==1
