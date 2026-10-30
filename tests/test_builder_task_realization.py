import json

import pytest
from jsonschema import Draft202012Validator,ValidationError

from adaos.sdk.builder import prototype
from adaos.services.builder_context import digest
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder.task_interpretation import admit_interpretation
from adaos.services.builder_intent import capture_intent,compile_prototype_brief
from test_builder_task_interpretation import prepared


def caption_task(*,external_prohibited=True):
    statement='Show the explanatory text "Sample records".'+ ('\nNo external calls.' if external_prohibited else '')
    intent=capture_intent(statement,
        locale='en',project_ref='project:caption')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    proposal={'schema':'adaos.builder.task_interpretation.v1','source_digest':intent['digest'],
        'clause_roles':[{'clause_id':'clause:001','roles':['product','fact']},
                        {'clause_id':'clause:002','roles':['policy']}],
        'requirements':[{'id':'caption','clause_ids':['clause:001'],'statement':task['clauses'][0]['text'].strip(),
            'kind':'ui','operation':None,'verification_family':'layout','supersedes':[]},
            {'id':'policy','clause_ids':['clause:002'],'statement':task['clauses'][1]['text'].strip() if external_prohibited else '',
            'kind':'policy','operation':None,'verification_family':'policy','supersedes':[]}],
        'facts':[],'transitions':[],
        'constraints':[{'clause_ids':['clause:001'],'kind':'visible_text','subject':'','values':['Sample records']},
            {'clause_ids':['clause:002'],'kind':'forbid_external','subject':'','values':[]}],
        'questions':[],'design_freedom':[]}
    if not external_prohibited:
        proposal['clause_roles']=proposal['clause_roles'][:1]
        proposal['requirements']=proposal['requirements'][:1]
        proposal['constraints']=proposal['constraints'][:1]
    return admit_interpretation(task,proposal,intent)[1]


def test_admitted_external_prohibition_constrains_provider_choices_before_generation():
    brief=caption_task()
    schema=prototype.semantic_provider_contract(version='v2',brief=brief,locales=('en',))
    assert schema['properties']['capability_surfaces']['maxItems']==0
    assert 'surface' not in schema['$defs']['semanticRef']['properties']['kind']['enum']
    validator=Draft202012Validator({'$defs':schema['$defs'],'$ref':'#/$defs/semanticRef'})
    validator.validate({'kind':'view','id':'known-view'})
    with pytest.raises(ValidationError):validator.validate({'kind':'surface','id':'invented-notice'})
    arrays=Draft202012Validator({'type':'object','properties':{
        'capability_surfaces':schema['properties']['capability_surfaces']},'$defs':schema['$defs']})
    arrays.validate({'capability_surfaces':[]})
    with pytest.raises(ValidationError):arrays.validate({'capability_surfaces':[{}]})


def test_visible_literal_is_not_repeated_and_realization_uses_actual_abi_slots():
    brief=caption_task()
    context=prototype.model_context(brief,compact=True)
    realization=context['task_realization']
    rule=next(row for row in realization['constraint_rules'] if row['kind']=='visible_text')
    assert rule['constraint_ref']=='task_acceptance.constraints[0]'
    assert rule['constraint_digest']==digest(context['task_acceptance']['constraints'][0])
    assert 'Sample records' not in json.dumps(realization)
    assert rule['visible_locations']==['views[].title','views[].section.title']
    assert 'document.title' in rule['not_visible_evidence']
    abi=prototype.semantic_candidate_contract(version='v2')
    assert 'title' in abi['$defs']['view']['properties']
    section=abi['$defs']['view']['properties']['section']
    ref=next(row['$ref'] for row in section['anyOf'] if '$ref' in row)
    assert 'title' in abi['$defs'][ref.split('/')[-1]]['properties']
    assert 'state_policy' not in realization


def test_unrestricted_and_legacy_generation_keep_the_external_abi():
    brief=caption_task(external_prohibited=False)
    for value in (None,brief):
        schema=prototype.semantic_provider_contract(version='v2',brief=value)
        assert schema['properties']['capability_surfaces'].get('maxItems')!=0
        assert 'surface' in schema['$defs']['semanticRef']['properties']['kind']['enum']


def test_transition_guidance_does_not_invent_initial_postcondition_fixtures():
    intent,task,proposal=prepared()
    _,brief=admit_interpretation(task,proposal,intent)
    context=prototype.model_context(brief,compact=True)
    assert 'not another initial fixture' in context['task_realization']['state_policy']
    assert context['task_acceptance']['facts']==brief['task_contract']['facts']
    assert context['task_acceptance']['transitions']==brief['task_contract']['transitions']
