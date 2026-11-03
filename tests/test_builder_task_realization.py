import json
import copy

import pytest
from jsonschema import Draft202012Validator,ValidationError

from adaos.sdk.builder import prototype
from adaos.services.builder_context import digest
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder.task_interpretation import admit_interpretation
from adaos.services.builder_intent import capture_intent,compile_prototype_brief


def test_stable_and_task_specific_transition_guidance_have_one_owner():
    from test_builder_task_acceptance import fixture
    from adaos.services.builder.task_realization import task_realization_context
    brief, _ = fixture()
    stable = prototype.semantic_generation_guidance()['construction']['source_transition']
    assert task_realization_context(brief)['transition_policy']['preferred_construction'] == stable
    assert 'preserve that view_ref and reuse that editor' in stable
    assert 'Do not keep that editor AND retarget' in stable
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
    policy = context['task_realization']['transition_policy']
    assert policy['source_ref'] == 'task_acceptance.transitions'
    assert 'not_equals' in policy['supported_realization']
    assert 'stored record state' in policy['supported_realization']
    assert 'unrelated commands' in policy['scope_boundary']
    assert any('command.guard' in item for item in policy['not_eligibility_evidence'])
    # Expected literals occur only in the source-owned acceptance contract,
    # never as another independently authored recipe for this test domain.
    assert 'accepted' not in json.dumps(policy)


def test_typed_ui_residual_slot_cannot_grant_automation_deferral():
    from adaos.services.builder.prototype_stage import automation_requirement_refs
    from adaos.services.builder.semantic_prototype import _requirement_contract_findings
    brief=caption_task()
    reference=brief['residual_requirements'][0]['id']
    assert reference.startswith('req:')
    assert automation_requirement_refs(brief)==set()
    schema=prototype.semantic_provider_contract(version='v2',brief=brief,locales=('en',))
    assert schema['properties']['automation_requirements']['maxItems']==0
    context=prototype.model_context(brief,compact=True)
    assert 'automation_requirements' in context['task_realization']['forbidden_output_arrays']
    findings=_requirement_contract_findings({'automation_requirements':[{'requirement_ref':reference}]},brief=brief)
    finding=next(row for row in findings if row['code']=='requirement.automation_reference_ineligible')
    assert finding['gate']=='G7' and finding['owner']=='candidate'
    assert finding['path']=='$.automation_requirements[0].requirement_ref'


def test_legacy_business_obligation_ownership_does_not_depend_on_id_prefix():
    from adaos.services.builder.prototype_stage import automation_requirement_refs
    legacy={'principal_jobs':[{'id':'req:business'}],'residual_requirements':[{'id':'job:integration'}]}
    assert automation_requirement_refs(legacy)=={'req:business','job:integration'}


def test_verification_examples_do_not_expand_the_generated_fixture_state_space():
    brief=caption_task()
    original=copy.deepcopy(brief)
    schema=prototype.semantic_provider_contract(version='v2',brief=brief)
    assert schema['properties']['representative_states']['maxItems']==0
    assert 'state' not in schema['$defs']['semanticRef']['properties']['kind']['enum']
    arrays=Draft202012Validator({'type':'object','properties':{
        'representative_states':schema['properties']['representative_states']},'$defs':schema['$defs']})
    arrays.validate({'representative_states':[]})
    with pytest.raises(ValidationError):arrays.validate({'representative_states':[{}]})
    context=prototype.model_context(brief,compact=True)['task_realization']
    rule=next(row for row in context['constraint_rules'] if row['kind']=='verification_state_separation')
    assert 'independent browser/runtime obligations' in rule['provider_rule']
    assert 'cannot stand for text search' in rule['provider_rule']
    assert brief==original


def test_an_explicit_admitted_data_state_keeps_the_state_proof_abi():
    text='Show an empty collection state.'
    intent=capture_intent(text,locale='en',project_ref='project:declared-state')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    proposal={'schema':'adaos.builder.task_interpretation.v1','source_digest':intent['digest'],
        'clause_roles':[{'clause_id':'clause:001','roles':['product']}],
        'requirements':[{'id':'empty','clause_ids':['clause:001'],'statement':text,
            'kind':'data_state','operation':None,'verification_family':'value','supersedes':[]}],
        'facts':[],'transitions':[],'constraints':[],'questions':[],'design_freedom':[]}
    _,brief=admit_interpretation(task,proposal,intent)
    schema=prototype.semantic_provider_contract(version='v2',brief=brief)
    assert schema['properties']['representative_states'].get('maxItems',1)>0
    assert 'state' in schema['$defs']['semanticRef']['properties']['kind']['enum']


def test_untyped_legacy_briefs_keep_the_state_proof_abi():
    schema=prototype.semantic_provider_contract(version='v2',brief=None)
    assert schema['properties']['representative_states'].get('maxItems',1)>0
    assert 'state' in schema['$defs']['semanticRef']['properties']['kind']['enum']
