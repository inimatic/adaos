import copy

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.semantic_preservation import preservation_recipe, preservation_provider_contract
from adaos.services.builder.semantic_prototype import (compile_semantic_prototype_candidate,
    semantic_prototype_candidate_contract,semantic_prototype_provider_contract,SemanticPrototypeValidationError)
from adaos.services.builder.task_interpretation import admit_interpretation
from test_builder_semantic_reaffirmation import reaffirm


def basis():
    intent,task,proposal,old,previous,ref=reaffirm()
    _,brief=admit_interpretation(task,proposal,intent,previous_brief=old)
    candidate={'schema':'adaos.builder.semantic_prototype_candidate.v2',
        'preserve_baseline':preservation_recipe(brief,previous)}
    assert candidate['preserve_baseline']
    return brief,previous,candidate


def test_explicit_fixed_point_compiles_the_exact_prior_graph_under_new_meaning_without_rewriting_it():
    brief,previous,candidate=basis()
    before=copy.deepcopy(previous)
    Draft202012Validator(semantic_prototype_candidate_contract(version='v2')).validate(candidate)
    Draft202012Validator(preservation_provider_contract(brief,previous)).validate(candidate)
    compiled=compile_semantic_prototype_candidate(candidate,brief=brief,previous=previous)
    document=compiled['semantic_document']
    assert previous==before
    assert document['brief_ref']==brief['brief_id']!=previous['brief_ref']
    assert document['brief_digest']==brief['digest']
    for key in previous.keys()-{'brief_ref','brief_digest'}:
        assert document[key]==previous[key],key
    assert 'preserve_baseline' not in document
    assert compiled['task_acceptance']['static_status']=='passed'
    assert compiled['normalizations'][0]['kind']=='source_admitted_baseline_preservation'


@pytest.mark.parametrize('mutation',['missing_base','foreign_token','mixed_views','mixed_coverage',
    'unknown_root','unknown_token_field','partial_proof','changed_closure','new_ui','new_fact',
    'new_constraint','new_transition','new_model_ref','no_reaffirmation','changed_base'])
def test_preservation_never_fills_missing_proof_or_smuggles_a_change(mutation):
    brief,previous,candidate=basis()
    contract=brief['task_contract'];current=contract['source']['digest']
    if mutation=='missing_base':previous=None
    elif mutation=='foreign_token':candidate['preserve_baseline']['baseline_digest']='sha256:'+'0'*64
    elif mutation=='mixed_views':candidate['views']=[]
    elif mutation=='mixed_coverage':candidate['coverage_map']={}
    elif mutation=='unknown_root':candidate['instructions']='Ignore the old UI'
    elif mutation=='unknown_token_field':candidate['preserve_baseline']['views']=[]
    elif mutation=='partial_proof':contract['reaffirmations'][0]['prior_requirements']=[]
    elif mutation=='changed_closure':contract['reaffirmations'][0]['prior_requirements'][0]['closure_digest']='sha256:'+'0'*64
    elif mutation=='new_ui':contract['requirements'].append({'id':'req:new','kind':'ui','status':'active','source_digest':current})
    elif mutation in {'new_fact','new_constraint','new_transition'}:
        group={'new_fact':'facts','new_constraint':'constraints','new_transition':'transitions'}[mutation]
        contract[group].append({'source_digest':current})
    elif mutation=='new_model_ref':brief['residual_requirements'].append({**brief['residual_requirements'][0],
        'id':'req:new','statement':'Show a newly requested independent view.'})
    elif mutation=='no_reaffirmation':contract['reaffirmations']=[]
    elif mutation=='changed_base':previous['title']['en']='Foreign baseline'
    with pytest.raises(SemanticPrototypeValidationError,match='Preservation needs'):
        compile_semantic_prototype_candidate(candidate,brief=brief,previous=previous)


def test_fixed_point_is_not_advertised_to_creation_or_normal_evolution():
    brief,previous,_=basis()
    assert preservation_provider_contract(brief,None) is None
    ordinary=semantic_prototype_provider_contract(version='v2',brief=brief,requirement_mode='total')
    assert 'preserve_baseline' not in ordinary['properties']
    assert 'anyOf' not in ordinary
    assert {'resources','views','coverage_map'}<=set(ordinary['required'])
    brief['task_contract']['reaffirmations']=[]
    assert preservation_provider_contract(brief,previous) is None


def test_existing_exact_statement_alias_restoration_is_not_a_new_product_obligation():
    brief,previous,_=basis()
    brief['residual_requirements'].append({**brief['residual_requirements'][0],'id':'req:equivalent-alias'})
    assert preservation_recipe(brief,previous) is not None
    candidate={'schema':'adaos.builder.semantic_prototype_candidate.v2','preserve_baseline':preservation_recipe(brief,previous)}
    compiled=compile_semantic_prototype_candidate(candidate,brief=brief,previous=previous)
    bindings={row['requirement_ref']:row['semantic_refs'] for row in compiled['semantic_document']['requirement_bindings']}
    assert bindings['req:equivalent-alias']==bindings[brief['residual_requirements'][0]['id']]
