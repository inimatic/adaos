"""Required evidence slots reduce bookkeeping, never prove content by themselves."""
import copy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from adaos.services.builder.prototype_context import prototype_model_requirement_inventory
from adaos.services.builder.semantic_prototype import (
    compile_semantic_prototype_candidate, semantic_prototype_provider_contract,
    SemanticPrototypeValidationError, _candidate_v2_field_namespaces,
)
from test_builder_semantic_construction import constructed


def fixture():
    brief, candidate = constructed()
    fields = _candidate_v2_field_namespaces(candidate['resources'])
    def target(ref):
        if ref['kind']!='field':return copy.deepcopy(ref)
        matches=[{'kind':'field','resource_ref':r,'id':f} for r,values in fields.items()
                 for f,identity in values.items() if identity==ref['id']]
        assert len(matches)==1
        return matches[0]
    old = {row['requirement_ref']:[target(ref) for ref in row['semantic_refs']]
           for row in candidate['requirement_bindings']}
    transition = brief['task_contract']['transitions'][0]['requirement_id']
    old[transition]=[{'kind':'command','id':'accept'}]
    coverage={}
    for row in prototype_model_requirement_inventory(brief):
        refs=old[row['id']]
        coverage[row['id']]={'status':'bound','first_target':refs[0],'additional_targets':refs[1:]}
    candidate.update(coverage_map=coverage,requirement_bindings=[])
    return brief,candidate


def test_total_evidence_lowers_to_the_same_existing_semantics_without_mutating_input():
    brief, legacy = constructed()
    expected = compile_semantic_prototype_candidate(legacy,brief=brief)['semantic_document']
    _, candidate = fixture();original=copy.deepcopy(candidate)
    result=compile_semantic_prototype_candidate(candidate,brief=brief)
    actual=result['semantic_document']
    assert candidate==original and 'coverage_map' not in actual
    for key in ('resources','views','commands','relationships','representative_states'):
        assert actual[key]==expected[key]
    bindings=lambda document:{row['requirement_ref']:set(row['semantic_refs']) for row in document['requirement_bindings']}
    assert bindings(actual)==bindings(expected)
    assert result['task_acceptance']['static_status']=='passed'
    assert any(row['kind']=='total_requirement_evidence' for row in result['normalizations'])


def test_bounded_binding_repair_extends_total_evidence_without_creating_parallel_legacy_arrays():
    from adaos.services.builder.semantic_repair import prepare_binding_repair, apply_binding_repair
    brief, candidate = fixture()
    original = copy.deepcopy(candidate)
    requirement = next(iter(candidate['coverage_map']))
    findings = [{'code': 'semantic.requirement_binding_incomplete', 'requirement_ref': requirement}]
    plan = prepare_binding_repair(candidate, findings)
    patch = {'schema': 'adaos.builder.binding_repair.v1', 'base_sha256': plan['base_sha256'],
             'bindings': [{'requirement_ref': requirement, 'add_semantic_refs': [{'kind': 'view', 'id': 'servers'}]}]}
    result = apply_binding_repair(candidate, patch, findings)
    assert candidate == original
    assert not result['requirement_bindings']
    assert result['resources'] == candidate['resources']
    assert result['views'] == candidate['views']
    for ref in candidate['coverage_map']:
        if ref != requirement:
            assert result['coverage_map'][ref] == candidate['coverage_map'][ref]
    assert compile_semantic_prototype_candidate(result, brief=brief)['task_acceptance']['static_status'] == 'passed'
    assert apply_binding_repair(result, {**patch, 'base_sha256': prepare_binding_repair(result, findings)['base_sha256']}, findings) == result


@pytest.mark.parametrize('mode',['explicit','owner_local'])
def test_legacy_provider_grammars_do_not_advertise_the_total_map(mode):
    brief,_=fixture()
    schema=semantic_prototype_provider_contract(version='v2',brief=brief,requirement_mode=mode)
    assert 'coverage_map' not in schema['properties'] and 'coverage_map' not in schema['required']


def test_fresh_total_provider_schema_requires_every_slot_and_a_first_target_not_an_empty_array():
    brief,candidate=fixture()
    schema=semantic_prototype_provider_contract(version='v2',brief=brief,requirement_mode='total')
    slots=schema['properties']['coverage_map']
    assert 'coverage_map' in schema['required']
    assert set(slots['required'])==set(candidate['coverage_map'])
    assert schema['$defs']['fulfills']['maxItems']==0
    validator=Draft202012Validator({**slots,'$defs':schema['$defs']})
    validator.validate(candidate['coverage_map'])
    wrong=copy.deepcopy(candidate['coverage_map']);wrong.pop(next(iter(wrong)))
    with pytest.raises(ValidationError):validator.validate(wrong)
    wrong=copy.deepcopy(candidate['coverage_map'])
    wrong[next(iter(wrong))]={'status':'bound','additional_targets':[]}
    with pytest.raises(ValidationError):validator.validate(wrong)
    wrong=copy.deepcopy(candidate['coverage_map'])
    wrong[next(iter(wrong))]={'status':'gap','code':'unsupported','detail':'Explicit proposal, not an accepted implementation'}
    validator.validate(wrong)


@pytest.mark.parametrize('mutation',['missing','foreign','parallel_global','parallel_local','unknown_owner'])
def test_total_slots_do_not_fill_missing_proofs_or_borrow_another_owner(mutation):
    brief,candidate=fixture()
    ref=next(iter(candidate['coverage_map']))
    if mutation=='missing':candidate['coverage_map'].pop(ref)
    elif mutation=='foreign':candidate['coverage_map']['req:foreign']=candidate['coverage_map'].pop(ref)
    elif mutation=='parallel_global':candidate['requirement_bindings']=[{'requirement_ref':ref,'semantic_refs':[{'kind':'view','id':'servers'}]}]
    elif mutation=='parallel_local':candidate['views'][0]['fulfills']=[ref]
    else:candidate['coverage_map'][ref]['first_target']={'kind':'field','resource_ref':'unknown','id':'status'}
    with pytest.raises(SemanticPrototypeValidationError):compile_semantic_prototype_candidate(candidate,brief=brief)


def test_total_evidence_does_not_turn_a_wrong_source_value_into_a_pass():
    brief,candidate=fixture()
    candidate['resources'][0]['records'][0]['values'][1]=0
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(candidate,brief=brief)
    assert any(row['code']=='task.fixture_value_mismatch' for row in caught.value.findings)


def test_total_field_address_retains_exact_resource_ownership_when_names_collide():
    from adaos.services.builder.semantic_construction import lower_constructions
    from adaos.services.builder.semantic_prototype import semantic_prototype_candidate_contract
    brief,candidate=fixture()
    other=copy.deepcopy(candidate['resources'][1]['fields'][0])
    candidate['resources'][0]['fields'].append(other)
    candidate['resources'][0]['records'][0]['values'].append('open')
    ref=next(iter(candidate['coverage_map']))
    candidate['coverage_map'][ref]={'status':'bound',
        'first_target':{'kind':'field','resource_ref':'incidents','id':'status'},'additional_targets':[]}
    expanded,_=lower_constructions(candidate,brief,semantic_prototype_candidate_contract(version='v2'))
    proof=next(row for row in expanded['requirement_bindings'] if row['requirement_ref']==ref)
    assert {'kind':'field','id':'incidents.status'} in proof['semantic_refs']
    assert {'kind':'field','id':'servers.status'} not in proof['semantic_refs']


def test_section_selector_lowers_exact_membership_without_creating_a_view():
    brief,candidate=fixture()
    ref=next(iter(candidate['coverage_map']))
    candidate['coverage_map'][ref]={'status':'bound','first_target':{'kind':'section','id':'copies'},'additional_targets':[]}
    original=copy.deepcopy(candidate)
    result=compile_semantic_prototype_candidate(candidate,brief=brief)
    assert candidate==original
    document=result['semantic_document']
    assert not any(view['id']=='copies' for view in document['views'])
    proof=next(row for row in document['requirement_bindings'] if row['requirement_ref']==ref)
    assert 'view:servers' in proof['semantic_refs']
    assert any(row['kind']=='section_evidence_expansion' for row in result['normalizations'])


@pytest.mark.parametrize('kind,identity',[('section','missing'),('section','Копии'),('view','copies')])
def test_section_names_and_wrong_view_namespace_never_become_implicit_aliases(kind,identity):
    brief,candidate=fixture()
    ref=next(iter(candidate['coverage_map']))
    candidate['coverage_map'][ref]={'status':'bound','first_target':{'kind':kind,'id':identity},'additional_targets':[]}
    with pytest.raises(SemanticPrototypeValidationError):
        compile_semantic_prototype_candidate(candidate,brief=brief)


def test_section_selector_observes_final_membership_after_explicit_constructors():
    from adaos.services.builder.semantic_construction import lower_constructions
    from adaos.services.builder.semantic_prototype import semantic_prototype_candidate_contract
    brief,candidate=fixture()
    candidate['views'][1].update(role='collection',presentation='table',
        section={'id':'transitions','kind':'tab','title':{'ru':'Переходы'}})
    candidate['commands'][0]['kind']='source_transition_context'
    ref=next(iter(candidate['coverage_map']))
    candidate['coverage_map'][ref]={'status':'bound','first_target':{'kind':'section','id':'transitions'},'additional_targets':[]}
    expanded,_=lower_constructions(candidate,brief,semantic_prototype_candidate_contract(version='v2'))
    proof=next(row for row in expanded['requirement_bindings'] if row['requirement_ref']==ref)
    assert {row['id'] for row in proof['semantic_refs'] if row['kind']=='view'}=={'incident-editor','accept_source_editor'}


def test_legacy_missing_coverage_stays_invalid_without_explicit_total_proposals():
    brief,candidate=constructed()
    candidate['requirement_bindings']=[]
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(candidate,brief=brief)
    assert any(row['code']=='requirement.coverage_missing' for row in caught.value.findings)
