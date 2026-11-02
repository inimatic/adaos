"""Constructive syntax must lower losslessly, not turn old failures into passes."""
import copy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from adaos.services.builder.prototype_context import prototype_model_requirement_inventory
from adaos.services.builder.semantic_construction import lower_constructions
from adaos.services.builder.semantic_prototype import (
    compile_semantic_prototype_candidate, normalize_semantic_prototype_candidate,
    semantic_prototype_candidate_contract, semantic_prototype_provider_contract,
    SemanticPrototypeValidationError,
)
from test_builder_task_acceptance import fixture
from test_builder_semantic_prototype import _multi_resource_candidate, _multi_resource_fixture


def constructed():
    brief, document = fixture()
    document.update(schema='adaos.webui.semantic.v2', document_id='operations', layout={'pattern': 'flow'},
        brief_ref=brief['brief_id'], brief_digest=brief['digest'],
        relationships=[], capability_surfaces=[], representative_states=[], capability_gaps=[])
    for resource in document['resources']:
        resource.update(identity_field_refs=['id'], item_semantics='One independent record', item_label={'ru': 'Запись'})
        for field in resource['fields']:
            field.update(required=True, editable=True)
            for item in [field, *field.get('options', [])]:
                item['label'] = {'ru': item['label'].get('ru') or item['label']['en']}
    document['views'].append({'id': 'incident-list', 'resource_ref': 'incidents', 'role': 'collection'})
    for index, view in enumerate(document['views']):
        view.update(region_role='primary' if index == 0 else 'supporting', title={'ru': 'Записи'},
            presentation='table' if view['role'] == 'collection' else None,
            field_refs=[f['id'] for r in document['resources'] if r['id'] == view['resource_ref'] for f in r['fields']])
    # Canonical fixture uses legacy section.label; provider grammar uses title.
    document['views'][0]['section'] = {'id': 'copies', 'kind': 'tab', 'title': {'ru': 'Копии'}}
    for row in prototype_model_requirement_inventory(brief):
        if row['id'] not in {b['requirement_ref'] for b in document['requirement_bindings']}:
            document['requirement_bindings'].append({'requirement_ref': row['id'], 'semantic_refs': ['view:servers']})
    candidate = _multi_resource_candidate(document)
    candidate['resources'][1].pop('read_only_when')
    transition = brief['task_contract']['transitions'][0]
    candidate['commands'] = [{'id': 'accept', 'kind': 'source_transition', 'view_ref': 'incident-editor',
        'field_ref': 'status', 'transition_ref': transition['requirement_id'],
        'exposure': {'placement': 'row_action', 'presentation': 'button'}}]
    candidate['requirement_bindings'] = [row for row in candidate['requirement_bindings']
        if row['requirement_ref'] != transition['requirement_id']]
    return brief, candidate


def lower(candidate, brief):
    return lower_constructions(candidate, brief, semantic_prototype_candidate_contract(version='v2'))


def test_constructed_transition_passes_full_compiler_without_repair_or_input_mutation():
    brief, candidate = constructed()
    original = copy.deepcopy((brief, candidate))
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    assert (brief, candidate) == original
    assert compiled['task_acceptance']['static_status'] == 'passed'
    assert compiled['task_acceptance']['status'] == 'runtime_acceptance_pending'
    doc = compiled['semantic_document']
    assert doc['commands'][0]['fixed_values'] == {'status': 'accepted'}
    assert doc['commands'][0]['input_field_refs'] == []
    assert doc['resources'][1]['read_only_when'] == {'field_ref': 'status', 'operator': 'not_equals', 'value': 'open'}
    assert any(row['kind'] == 'source_transition_construction' for row in compiled['normalizations'])
    assert normalize_semantic_prototype_candidate(candidate, brief=brief)['commands'] == doc['commands']
    # The lowered direct spelling has exactly the same runtime, graph and digest.
    direct, _ = lower(candidate, brief)
    baseline = compile_semantic_prototype_candidate(direct, brief=brief)
    for key in ('webui', 'semantic_document', 'semantic_digest', 'semantic_graph_receipt', 'prototype_resources'):
        assert compiled.get(key) == baseline.get(key)


@pytest.mark.parametrize('fault,code', [
    ('unknown_transition', 'transition_unknown'), ('wrong_owner', 'transition_owner_invalid'),
    ('unknown_field', 'transition_field_unknown'), ('conflicting_lock', 'transition_policy_conflict'),
    ('foreign_command', 'transition_scope_conflict'), ('extra_effect', 'shape_invalid'),
])
def test_construction_cannot_add_authority_or_silently_broaden_policy(fault, code):
    brief, candidate = constructed()
    command = candidate['commands'][0]
    if fault == 'unknown_transition': command['transition_ref'] = 'invented'
    elif fault == 'wrong_owner': command['view_ref'] = 'missing-view'
    elif fault == 'unknown_field': command['field_ref'] = 'disk'
    elif fault == 'conflicting_lock': candidate['resources'][1]['read_only_when'] = {'field_ref': 'status', 'operator': 'equals', 'value': 'accepted'}
    elif fault == 'foreign_command': candidate['commands'].append({'id': 'rename', 'kind': 'update', 'view_ref': 'incident-editor'})
    else: command['fixed_values'] = [{'field_ref': 'status', 'value': 'closed'}]
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    assert caught.value.findings[0]['code'] == 'semantic.construction_' + code


def test_wrong_source_field_is_still_rejected_by_independent_source_acceptance():
    brief, candidate = constructed()
    candidate['resources'][1]['fields'].append({'id': 'other_status', 'label': {'ru': 'Иное'},
        'value_type': 'short_text', 'required': True, 'editable': True, 'options': [], 'visible_when': None})
    candidate['resources'][1]['records'][0]['values'].append('open')
    candidate['commands'][0]['field_ref'] = 'other_status'
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    assert any(row['code'].startswith('task.transition_') for row in caught.value.findings)


def test_invalid_legacy_transition_does_not_receive_a_free_repair():
    brief, candidate = constructed()
    direct, _ = lower(candidate, brief)
    direct['resources'][1]['read_only_when'] = None
    direct['commands'][0]['guard'] = {'when': {'field_ref': 'status', 'operator': 'equals', 'value': 'open'}, 'require_nonempty': []}
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(direct, brief=brief)
    assert any(row['code'] == 'task.transition_precondition_unenforced' for row in caught.value.findings)


def test_precondition_failure_does_not_hide_independent_layout_failure():
    brief, candidate = constructed()
    direct, _ = lower(candidate, brief)
    direct['resources'][1]['read_only_when'] = None
    direct['views'][1]['region_role'] = 'detail'
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(direct, brief=brief)
    assert {'task.transition_precondition_unenforced', 'semantic.layout_region_unknown'} <= {
        row['code'] for row in caught.value.findings}


def test_owner_local_claims_compile_to_same_explicit_bindings():
    brief, semantic = _multi_resource_fixture()
    candidate = _multi_resource_candidate(semantic)
    # Move one single-owner claim from the separate reference list to its owner.
    binding = next(row for row in candidate['requirement_bindings']
        if len(row['semantic_refs']) == 1 and row['semantic_refs'][0]['kind'] in {'resource', 'view', 'command'})
    ref = binding['semantic_refs'][0]
    candidate['requirement_bindings'].remove(binding)
    candidate['requirement_bindings'].append(binding)
    original = compile_semantic_prototype_candidate(candidate, brief=brief)
    owner = next(row for row in candidate[{'resource': 'resources', 'view': 'views', 'command': 'commands'}[ref['kind']]] if row['id'] == ref['id'])
    owner['fulfills'] = [binding['requirement_ref']]
    candidate['requirement_bindings'].remove(binding)
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    before = copy.deepcopy(original['semantic_document'])
    after = copy.deepcopy(compiled['semantic_document'])
    for document in (before, after):
        document['requirement_bindings'].sort(key=lambda row: row['requirement_ref'])
    assert before == after
    assert compiled['webui'] == original['webui']


@pytest.mark.parametrize('claims', [['invented'], ['x', 'x'], 'not-an-array', [1]])
def test_local_claims_cannot_invent_or_disguise_requirements(claims):
    brief, candidate = constructed()
    candidate['views'][0]['fulfills'] = claims
    with pytest.raises(SemanticPrototypeValidationError):
        compile_semantic_prototype_candidate(candidate, brief=brief)


def test_provider_exposes_constructor_only_for_admitted_transitions_and_scopes_refs():
    brief, candidate = constructed()
    schema = semantic_prototype_provider_contract(version='v2', brief=brief, locales=('ru',))
    validator = Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/sourceTransitionCommand'})
    command = {**candidate['commands'][0], 'fulfills': []}
    validator.validate(command)
    with pytest.raises(ValidationError): validator.validate({**command, 'transition_ref': 'invented'})
    with pytest.raises(ValidationError): validator.validate({**command, 'fixed_values': []})
    for context in (None, {**brief, 'task_contract': {**brief['task_contract'], 'transitions': []}}):
        schema = semantic_prototype_provider_contract(version='v2', brief=context)
        assert 'sourceTransitionCommand' not in schema['$defs']
        assert schema['properties']['commands']['items'] == {'$ref': '#/$defs/command'}


def test_owner_local_mode_closes_the_raw_reference_escape_without_losing_owner_kinds():
    from adaos.services.builder.semantic_construction import OWNER_SCHEMAS
    brief, candidate = constructed()
    schema = semantic_prototype_provider_contract(version='v2', brief=brief, requirement_mode='owner_local')
    validator = Draft202012Validator({'$defs': schema['$defs'], 'type': 'object', 'properties': {
        'requirement_bindings': schema['properties']['requirement_bindings']}})
    validator.validate({'requirement_bindings': []})
    with pytest.raises(ValidationError):
        validator.validate({'requirement_bindings': [{'requirement_ref': 'invented',
            'semantic_refs': [{'kind': 'view', 'id': 'content'}]}]})
    for name in OWNER_SCHEMAS:
        branches = schema['$defs'][name].get('anyOf', [schema['$defs'][name]])
        assert all('fulfills' in row['required'] for row in branches)
    assert semantic_prototype_provider_contract(version='v2', brief=brief)['properties']['requirement_bindings'].get('maxItems') != 0
    for options in ({'version': 'v1', 'brief': brief}, {'version': 'v2'}):
        with pytest.raises(ValueError):semantic_prototype_provider_contract(**options, requirement_mode='owner_local')
    assert schema['properties']['commands']['items'] == {'$ref': '#/$defs/sourceTransitionCommand'}
    assert schema['$defs']['sourceTransitionCommand']['properties']['kind']['enum'] == ['source_transition_context']
    extended = copy.deepcopy(brief)
    extended['task_contract']['requirements'].append({'id': 'additional-create', 'status': 'active',
        'kind': 'behavior', 'operation': 'create', 'statement': 'Create another record',
        'verification_family': 'runtime'})
    allowed = semantic_prototype_provider_contract(version='v2', brief=extended, requirement_mode='owner_local')
    assert 'anyOf' in allowed['properties']['commands']['items']


def test_one_requirement_can_claim_multiple_real_views_instead_of_surrogate_ids():
    brief, candidate = constructed()
    binding = candidate['requirement_bindings'].pop(0)
    reference = binding['requirement_ref']
    candidate['views'][0]['fulfills'] = [reference]
    candidate['views'][2]['fulfills'] = [reference]
    expanded, _ = lower(candidate, brief)
    evidence = next(row for row in expanded['requirement_bindings'] if row['requirement_ref'] == reference)
    assert evidence['semantic_refs'] == [{'kind': 'view', 'id': candidate['views'][0]['id']},
                                       {'kind': 'view', 'id': candidate['views'][2]['id']}]
    compile_semantic_prototype_candidate(candidate, brief=brief)


def test_field_claims_derive_the_exact_owner_qualified_identity():
    brief, candidate = constructed()
    # Two resource-local fields share a name. Core, not the model, qualifies it.
    other = copy.deepcopy(candidate['resources'][1]['fields'][0])
    candidate['resources'][0]['fields'].append(other)
    candidate['resources'][0]['records'][0]['values'].append('open')
    reference = next(row['id'] for row in prototype_model_requirement_inventory(brief))
    candidate['resources'][0]['fields'][-1]['fulfills'] = [reference]
    candidate['resources'][1]['fields'][0]['fulfills'] = [reference]
    expanded, _ = lower(candidate, brief)
    evidence = next(row for row in expanded['requirement_bindings'] if row['requirement_ref'] == reference)
    fields = {row['id'] for row in evidence['semantic_refs'] if row['kind'] == 'field'}
    assert fields == {'servers.status', 'incidents.status'}
    compile_semantic_prototype_candidate(candidate, brief=brief)


@pytest.mark.parametrize('role', ['collection', 'details'])
@pytest.mark.parametrize('placement', ['row_action', 'editor_primary'])
def test_source_action_constructs_its_editor_without_rewriting_the_target_role(role, placement):
    brief, candidate = constructed()
    target = candidate['views'][1]
    target['role'] = role
    target['presentation'] = 'table' if role == 'collection' else None
    candidate['commands'][0]['kind'] = 'source_transition_context'
    candidate['commands'][0]['exposure']['placement'] = placement
    original = copy.deepcopy(candidate)
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    assert candidate == original
    document = compiled['semantic_document']
    assert next(view for view in document['views'] if view['id'] == target['id'])['role'] == role
    command = document['commands'][0]
    editor = next(view for view in document['views'] if view['id'] == command['view_ref'])
    assert editor['role'] == 'editor' and editor['resource_ref'] == target['resource_ref']
    assert editor['surface'] == ('modal' if placement == 'row_action' else 'inline')
    assert compiled['task_acceptance']['static_status'] == 'passed'
    assert any(row['kind'] == 'source_transition_editor_construction' for row in compiled['normalizations'])


def test_ordinary_legacy_command_cannot_borrow_source_editor_construction():
    brief, candidate = constructed()
    direct, _ = lower(candidate, brief)
    direct['views'][1]['role'] = 'details'
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(direct, brief=brief)
    assert any(row['code'] == 'semantic.command_editor_required' for row in caught.value.findings)


def test_old_source_constructor_does_not_silently_gain_context_editor_semantics():
    brief, candidate = constructed()
    candidate['views'][1]['role'] = 'details'
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(candidate, brief=brief)
    assert caught.value.findings[0]['code'] == 'semantic.construction_transition_owner_invalid'


def test_single_effect_constructor_never_picks_the_last_of_ambiguous_source_rows():
    brief, candidate = constructed()
    brief['task_contract']['transitions'].append(copy.deepcopy(brief['task_contract']['transitions'][0]))
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        lower(candidate, brief)
    assert caught.value.findings[0]['code'] == 'semantic.construction_transition_ambiguous'
    schema = semantic_prototype_provider_contract(version='v2', brief=brief, requirement_mode='owner_local')
    assert 'sourceTransitionCommand' not in schema['$defs']
    assert schema['properties']['commands']['items'] == {'$ref': '#/$defs/command'}


def test_repeating_the_same_owner_evidence_is_an_idempotent_set_not_an_extra_obligation():
    brief, candidate = constructed()
    binding = candidate['requirement_bindings'].pop(0)
    reference = binding['requirement_ref']
    candidate['views'][0]['fulfills'] = [reference]
    baseline = compile_semantic_prototype_candidate(candidate, brief=brief)
    candidate['views'][0]['fulfills'] = [reference, reference]
    repeated = compile_semantic_prototype_candidate(candidate, brief=brief)
    assert repeated['semantic_digest'] == baseline['semantic_digest']
    assert repeated['webui'] == baseline['webui']
    assert any(row['kind'] == 'idempotent_owner_claim_set' for row in repeated['normalizations'])
