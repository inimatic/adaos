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


def setup(tmp_path):
    parent,messages,text=envelope('personal')
    body=json.loads(messages[-1]['content'])['builder_request']
    intent,task,proposal=prepared()
    _,brief=admit_interpretation(task,proposal,intent)
    baseline={'schema':'baseline','fixture':92}
    parent=seal({**parent,'output_schema_digest':digest(text['format']['schema']),
        'baseline_digest':digest(baseline),
        'model_context_digest':digest(body['prototype_brief']), 'output_locales':['en','ru'],
        'write_scope':{'baseline_digest':digest(baseline),'writes':[]}},id_key='generation_plan_id',prefix='generation-plan:')
    (tmp_path/(parent['generation_plan_id'].replace(':','-')+'.json')).write_text(json.dumps({
        'generation_plan':parent,'provider_output_schema':text['format']['schema'],'baseline':baseline}),encoding='utf-8')
    (tmp_path/'wire-primary.json').write_text(json.dumps({'job_id':'primary',
        'context_admission':{'generation_plan_ref':parent['generation_plan_id']}}),encoding='utf-8')
    candidate={'schema':'adaos.builder.semantic_prototype_candidate.v2','title':{'ru':'Candidate'}}
    context=prepare_repair_context(job_id='primary',candidate=candidate,
        findings=[{'code':'semantic.task_write_scope_exceeded'}],brief=brief,journal_dir=tmp_path)
    plan=context['generation_plan']
    repair={'instruction':intent['statement'],'prototype_brief':body['prototype_brief'],
        'generation_plan_ref':plan['generation_plan_id'],'write_scope':plan['write_scope'],
        'preservation_baseline':context['preservation_baseline'],'candidate':candidate,'repair_scope':None,
        'output_locales':plan['output_locales']}
    messages=[{'role':'system','content':'Repair only.'},
        {'role':'user','content':json.dumps({'stable_builder_context':{'semantic_invariants':prototype.semantic_generation_guidance()}})},
        {'role':'user','content':json.dumps({'semantic_repair':repair})}]
    return context,parent,messages,{'format':{'type':'json_schema','strict':True,'schema':context['output_schema']}}


def test_repair_has_exact_parent_child_lineage_and_durable_original_schema(tmp_path):
    context,parent,messages,text=setup(tmp_path)
    child=context['generation_plan']
    assert child['parent_generation_plan_ref']==parent['generation_plan_id']
    assert child['retrieval']=='inline' and child['stages'][0]['name']=='repair'
    assert 'not design a new application' in context['system_prompt']
    assert list(tmp_path.glob('repair-plan-*.json'))
    receipt=admit_generation_input(messages,model='selected',text=text,transport='personal',plan=child)
    assert receipt['parent_generation_plan_ref']==parent['generation_plan_id']
    assert receipt['required_retrieval_refs']==[]


@pytest.mark.parametrize('fault,code',[
    ('candidate','repair_base_conflict'),('scope','repair_base_conflict'),('baseline','repair_baseline_conflict'),
    ('source','repair_task_conflict'),('brief','model_context_conflict'),('schema','output_schema_conflict'),
    ('tools','repair_tools_conflict'),('guidance','repair_platform_dependency_missing'),
    ('write_scope','write_scope_conflict'),('locales','output_locale_conflict'),
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
