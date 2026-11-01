import copy
import json

import pytest

from adaos.sdk.builder import prototype
from adaos.services.builder.generation_plan import admit_generation_input, GenerationInputRejected
from adaos.services.builder.repair_context import prepare_repair_context
from adaos.services.builder.task_spec import seal
from adaos.services.builder_context import digest
from test_builder_generation_wire import envelope
from test_builder_task_interpretation import prepared
from adaos.services.builder.task_interpretation import admit_interpretation


def setup(tmp_path, *, semantic_input_profile=None, capability_context_digest=None, scoped=False):
    parent,messages,text=envelope('personal')
    body=json.loads(messages[-1]['content'])['builder_request']
    intent,task,proposal=prepared()
    _,brief=admit_interpretation(task,proposal,intent)
    baseline={'schema':'baseline','fixture':92}
    from adaos.services.builder.semantic_input import input_contract_pin
    from adaos.services.builder.input_composition import composition_contract
    parent=seal({**parent,
        **({'semantic_input_contract':input_contract_pin(semantic_input_profile)} if semantic_input_profile else {}),
        **({'capability_context_digest':capability_context_digest} if semantic_input_profile else {}),
        **({'input_composition':composition_contract(messages)} if semantic_input_profile else {}),
        'output_schema_digest':digest(text['format']['schema']),
        'baseline_digest':digest(baseline),
        'model_context_digest':digest(body['prototype_brief']), 'output_locales':['en','ru'],
        'write_scope':{'baseline_digest':digest(baseline),'writes':[]}},id_key='generation_plan_id',prefix='generation-plan:')
    (tmp_path/(parent['generation_plan_id'].replace(':','-')+'.json')).write_text(json.dumps({
        'generation_plan':parent,'provider_output_schema':text['format']['schema'],'baseline':baseline}),encoding='utf-8')
    (tmp_path/'wire-primary.json').write_text(json.dumps({'job_id':'primary','request_id':'original-primary',
        'context_admission':{'generation_plan_ref':parent['generation_plan_id']}}),encoding='utf-8')
    candidate={'schema':'adaos.builder.semantic_prototype_candidate.v2','title':{'ru':'Candidate'}}
    findings=[{'code':'semantic.task_write_scope_exceeded'}]
    if scoped:
        candidate['resources']=[{'id':'items','fields':[{'id':'name'}], 'records':[{'id':'one','values':[]}]}]
        findings=[{'code':'semantic.record_arity','path':'$.resources[0].records[0].values'}]
    context=prepare_repair_context(job_id='primary',candidate=candidate,
        findings=findings,brief=brief,journal_dir=tmp_path)
    plan=context['generation_plan']
    repair={**context['repair_instructions'],'instruction':intent['statement'],'prototype_brief':body['prototype_brief'],
        'validation_findings':findings,
        'repair_diagnosis_ref':context['diagnosis']['diagnosis_id'],
        'generation_plan_ref':plan['generation_plan_id'],'write_scope':plan['write_scope'],
        'preservation_baseline':None if scoped else context['preservation_baseline'],'candidate':candidate,
        'repair_scope':{key:value for key,value in context['scope'].items() if key!='output_schema'} if scoped else None,
        'output_locales':plan['output_locales']}
    stable={'semantic_invariants':prototype.semantic_generation_guidance()}
    if scoped:
        stable['output_contract']={'schema':context['scope']['output_schema']['properties']['schema']['enum'][0],
            'mode':'provider_strict_json_schema',
            'canonical_output':'bounded replacements merged into the original candidate before full compilation'}
    messages=[{'role':'system','content':context['system_prompt']},
        {'role':'user','content':json.dumps({'stable_builder_context':stable})},
        {'role':'user','content':json.dumps({'semantic_repair':repair})}]
    return context,parent,messages,{'format':{'type':'json_schema','strict':True,'schema':context['output_schema']}}


def test_repair_has_exact_parent_child_lineage_and_durable_original_schema(tmp_path):
    context,parent,messages,text=setup(tmp_path)
    child=context['generation_plan']
    assert child['parent_generation_plan_ref']==parent['generation_plan_id']
    assert child['parent_request_id']=='original-primary'
    assert context['parent_request_id']=='original-primary'
    assert child['retrieval']=='inline' and child['stages'][0]['name']=='repair'
    assert 'not design a new application' in context['system_prompt']
    assert list(tmp_path.glob('repair-plan-*.json'))
    assert list(tmp_path.glob('repair-diagnosis-*.json'))
    receipt=admit_generation_input(messages,model='selected',text=text,transport='personal',plan=child)
    assert receipt['parent_generation_plan_ref']==parent['generation_plan_id']
    assert receipt['parent_request_id']=='original-primary'
    assert receipt['required_retrieval_refs']==[]


@pytest.mark.parametrize('fault,code',[
    ('candidate','repair_base_conflict'),('scope','repair_base_conflict'),('baseline','repair_baseline_conflict'),
    ('source','repair_task_conflict'),('brief','model_context_conflict'),('schema','output_schema_conflict'),
    ('tools','repair_tools_conflict'),('guidance','repair_platform_dependency_missing'),
    ('write_scope','write_scope_conflict'),('locales','output_locale_conflict'),
    ('diagnosis','repair_diagnosis_conflict'),('findings','repair_diagnosis_conflict'),
    ('generation_carrier','repair_carrier_conflict')])
def test_repair_cannot_change_authority_base_grammar_or_dependencies(tmp_path,fault,code):
    context,_,messages,text=setup(tmp_path)
    body=json.loads(messages[-1]['content'])
    repair=body['semantic_repair']
    options={}
    if fault=='candidate':repair['candidate']['title']['ru']='Changed'
    elif fault=='scope':repair['repair_scope']={'broader':True}
    elif fault=='baseline':repair['preservation_baseline']['fixture']=0
    elif fault=='source':repair['instruction']='Unrelated task'
    elif fault=='brief':repair['prototype_brief']['process_constraints']=[]
    elif fault=='schema':text['format']['schema']['description']='tampered'
    elif fault=='tools':options={'tools':[{'type':'mcp'}],'tool_choice':'auto'}
    elif fault=='guidance':
        stable=json.loads(messages[1]['content'])
        stable['stable_builder_context']['semantic_invariants'].pop('state_proofs')
        messages[1]['content']=json.dumps(stable)
    elif fault=='write_scope':repair['write_scope']=None
    elif fault=='locales':repair['output_locales']=['ru']
    elif fault=='diagnosis':repair['repair_diagnosis_ref']='different'
    elif fault=='findings':repair['validation_findings']=[]
    elif fault=='generation_carrier':body={'builder_request':{}}
    messages[-1]['content']=json.dumps(body)
    with pytest.raises(GenerationInputRejected,match=code):
        admit_generation_input(messages,model='selected',text=text,transport='personal',plan=context['generation_plan'],**options)


def test_legacy_without_a_task_plan_keeps_its_explicit_compatibility_path(tmp_path):
    assert prepare_repair_context(job_id='old',candidate={},findings=[],brief={},journal_dir=tmp_path) is None
    (tmp_path/'wire-old.json').write_text(json.dumps({'job_id':'old','context_admission':{'generation_plan_ref':None}}),encoding='utf-8')
    assert prepare_repair_context(job_id='old',candidate={},findings=[],brief={},journal_dir=tmp_path) is None
    with pytest.raises(GenerationInputRejected):
        prepare_repair_context(job_id='old',candidate={},findings=[],brief={'task_contract':{'schema':'claimed'}},journal_dir=tmp_path)


def test_repair_rejects_corrupted_original_baseline_before_submission(tmp_path):
    context,parent,messages,_=setup(tmp_path)
    path=tmp_path/(parent['generation_plan_id'].replace(':','-')+'.json')
    record=json.loads(path.read_text(encoding='utf-8'))
    record['baseline']['fixture']=0
    path.write_text(json.dumps(record),encoding='utf-8')
    intent,task,proposal=prepared()
    _,brief=admit_interpretation(task,proposal,intent)
    with pytest.raises(GenerationInputRejected,match='repair_baseline_conflict'):
        prepare_repair_context(job_id='primary',candidate={},findings=[],brief=brief,journal_dir=tmp_path)


@pytest.mark.parametrize('finding,expected',[
    ({'code':'task.fact_not_source'},'interpretation'),
    ({'code':'context.retrieval_closure_incomplete','owner':'candidate'},'context_delivery'),
    ({'code':'capability.unsupported'},'capability_gap'),
    ({'code':'task.locale_not_supported','owner':'capability_admission'},'capability_gap'),
    ({'code':'semantic.compiler_contract_invalid'},'platform_runtime'),
    ({'code':'runtime.disconnected'},'platform_runtime'),
    ({'error':'semantic_prototype_invalid','detail':'untyped legacy error'},'unclassified'),
])
def test_non_candidate_diagnosis_is_persisted_without_authorizing_repair(tmp_path,finding,expected,monkeypatch):
    context,parent,messages,_=setup(tmp_path)
    plans=set(tmp_path.glob('repair-plan-*.json'))
    from adaos.services.builder import semantic_repair
    def unexpected(*args):pytest.fail('Scope selection must not run for a non-candidate failure')
    monkeypatch.setattr(semantic_repair,'prepare_record_repair',unexpected)
    _,task,proposal=prepared()
    intent=prepared()[0]
    _,brief=admit_interpretation(task,proposal,intent)
    result=prepare_repair_context(job_id='primary',candidate={'schema':'raw'},findings=[finding],
        brief=brief,journal_dir=tmp_path)
    assert result['status']=='blocked'
    diagnosis=result['diagnosis']
    assert diagnosis['failure_classes']==[expected]
    assert diagnosis['paid_candidate_repair_allowed'] is False
    assert diagnosis['findings']==[finding]
    assert diagnosis['parent_generation_plan_ref']==parent['generation_plan_id']
    assert (tmp_path/(diagnosis['diagnosis_id'].replace(':','-')+'.json')).is_file()
    assert set(tmp_path.glob('repair-plan-*.json'))==plans


def test_different_findings_over_same_candidate_have_distinct_diagnosis_and_child_plan(tmp_path):
    first,parent,messages,_=setup(tmp_path)
    repair=json.loads(messages[-1]['content'])['semantic_repair']
    intent,task,proposal=prepared()
    _,brief=admit_interpretation(task,proposal,intent)
    findings=[{'code':'semantic.task_write_scope_exceeded','path':'$/commands/0/guard'}]
    second=prepare_repair_context(job_id='primary',candidate=repair['candidate'],findings=findings,
        brief=brief,journal_dir=tmp_path)
    assert second['status']=='admitted'
    assert first['diagnosis']['diagnosis_id']!=second['diagnosis']['diagnosis_id']
    assert first['generation_plan']['generation_plan_id']!=second['generation_plan']['generation_plan_id']
    assert len(list(tmp_path.glob('repair-diagnosis-*.json')))==2
    assert prepare_repair_context(job_id='primary',candidate=repair['candidate'],findings=findings,
        brief=brief,journal_dir=tmp_path)==second


def test_task_acceptance_namespace_keeps_its_typed_candidate_owner():
    from adaos.services.builder.repair_diagnosis import compile_repair_diagnosis
    parent={'generation_plan_id':'plan','task_spec_ref':'task'}
    findings=[{'code':'semantic.binding_reference_missing'},
        {'code':'task.disclosure_text_missing','gate':'G8','owner':'candidate'}]
    diagnosis=compile_repair_diagnosis(candidate_digest='candidate',findings=findings,parent=parent,compiler_digest='compiler')
    assert diagnosis['failure_classes']==['candidate']
    assert diagnosis['paid_candidate_repair_allowed'] is True
    assert diagnosis['next_actions']==['repair_candidate']
    # A conflicting owner annotation cannot relabel an interpretation failure
    # at G3 or a platform/compiler defect as a candidate repair.
    for finding in ({'code':'task.fact_not_source','gate':'G3','owner':'candidate'},
                    {'code':'semantic.compiler_contract_invalid','gate':'G8','owner':'candidate'}):
        diagnosis=compile_repair_diagnosis(candidate_digest='candidate',findings=[finding],parent=parent,compiler_digest='compiler')
        assert diagnosis['paid_candidate_repair_allowed'] is False


def test_repair_requires_original_request_identity_in_its_wire_admission(tmp_path):
    setup(tmp_path)
    path=tmp_path/'wire-primary.json'
    wire=json.loads(path.read_text(encoding='utf-8'))
    wire.pop('request_id')
    path.write_text(json.dumps(wire),encoding='utf-8')
    intent,task,proposal=prepared()
    _,brief=admit_interpretation(task,proposal,intent)
    with pytest.raises(GenerationInputRejected,match='original wire admission'):
        prepare_repair_context(job_id='primary',candidate={},findings=[],brief=brief,journal_dir=tmp_path)
