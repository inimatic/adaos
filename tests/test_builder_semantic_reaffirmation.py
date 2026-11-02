import copy

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.semantic_reaffirmation import candidate_findings, requirement_closure
from adaos.services.builder.interpretation_boundary import reference_context
from adaos.services.builder.task_interpretation import admit_interpretation, interpretation_contract, TaskInterpretationRejected
from adaos.services.builder.task_spec import seal
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from test_builder_semantic_prototype import _multi_resource_fixture
from test_builder_semantic_ui_constraints import proposal


def basis():
    intent, task, value = proposal('Show a table.')
    _, brief = admit_interpretation(task, value, intent)
    _, source = _multi_resource_fixture()
    document = compile_semantic_prototype(source)['semantic_document']
    ref = brief['residual_requirements'][0]['id']
    document.update(brief_ref=brief['brief_id'], brief_digest=brief['digest'],
        requirement_bindings=[{'requirement_ref': ref, 'semantic_refs': ['view:work-list']}])
    # All old definitions stay canonical; only their admitted proof authority is
    # replaced in this deliberately constructed, model-free test fixture.
    return brief, document, ref


def reaffirm(text='Preserve the existing table unchanged.'):
    previous_brief, document, ref = basis()
    intent, task, value = proposal(text)
    task = seal({**task, 'reference_context': reference_context(document, previous_brief)})
    value['requirements'][0].update(kind='behavior', operation=None, verification_family='preservation', reaffirms=[ref])
    return intent, task, value, previous_brief, document, ref


def test_reaffirmation_has_exact_owner_effect_closure_and_no_new_operation_or_proof():
    intent, task, value, previous_brief, previous, ref = reaffirm()
    _, brief = admit_interpretation(task, value, intent, previous_brief=previous_brief)
    assert len(brief['residual_requirements']) == len(previous_brief['residual_requirements']) == 1
    assert brief['operations'] == []
    relation = brief['task_contract']['reaffirmations'][0]
    assert relation['prior_requirements'][0]['requirement_ref'] == ref
    assert relation['prior_requirements'][0]['closure_digest'] == requirement_closure(previous, ref)['closure_digest']
    candidate = copy.deepcopy(previous)
    candidate.update(brief_ref=brief['brief_id'], brief_digest=brief['digest'])
    assert candidate_findings(candidate, brief, previous) == []
    assert candidate_findings(candidate, brief, None, require_baseline=False) == []
    assert candidate_findings(candidate, brief, None)[0]['code'] == 'task.reaffirmation_changed'


@pytest.mark.parametrize('mutation', ['owner', 'query_scope', 'precondition', 'effect', 'binding', 'base'])
def test_reuse_rejects_changed_semantic_owner_scope_guard_effect_and_base(mutation):
    intent, task, value, old_brief, previous, ref = reaffirm()
    # Bind the protected requirement to both the collection and its command;
    # dependency closure must include all editor/resource effects, not labels.
    previous['requirement_bindings'][0]['semantic_refs'].append('command:' + previous['commands'][0]['id'])
    task = seal({**task, 'reference_context': reference_context(previous, old_brief)})
    _, brief = admit_interpretation(task, value, intent, previous_brief=old_brief)
    candidate = copy.deepcopy(previous)
    collection = next(v for v in candidate['views'] if v['id'] == 'work-list')
    if mutation == 'owner': collection['id'] = 'new-owner'
    elif mutation == 'query_scope': collection['scope_filters'] = [{'field_ref': 'status', 'value': 'changed'}]
    elif mutation == 'precondition': candidate['resources'][0]['read_only_when'] = {'field_ref': 'status', 'operator': 'not_equals', 'value': 'closed'}
    elif mutation == 'effect': candidate['commands'][0]['fixed_values']['status'] = 'changed'
    elif mutation == 'binding': candidate['requirement_bindings'][0]['semantic_refs'] = ['view:people-list']
    else: previous = {**previous, 'title': {'en': 'Different base'}}
    assert candidate_findings(candidate, brief, previous)


@pytest.mark.parametrize('mutation', ['actor', 'foreign_ref', 'superseded', 'new_operation', 'amendment'])
def test_reaffirmation_cannot_grant_changed_authority_or_amendment(mutation):
    intent, task, value, old_brief, previous, ref = reaffirm()
    if mutation == 'actor': task = seal({**task, 'authority': {**task['authority'], 'external_effects': 'allowed'}})
    elif mutation == 'foreign_ref': value['requirements'][0]['reaffirms'] = ['req:foreign']
    elif mutation == 'superseded':
        old_brief = copy.deepcopy(old_brief)
        old_brief['task_contract']['requirements'][0]['status'] = 'superseded'
        old_brief = seal(old_brief, id_key='brief_id', prefix='brief:')
    elif mutation == 'new_operation': value['requirements'][0]['operation'] = 'search'
    else: value['requirements'][0]['supersedes'] = [ref]
    with pytest.raises(TaskInterpretationRejected):
        admit_interpretation(task, value, intent, previous_brief=old_brief)


@pytest.mark.parametrize('source', ['Do not preserve the table.', 'If approved, preserve the table.',
                                   'Preserve the table but change its search scope.'])
def test_nonpositive_or_mixed_source_is_not_an_unchanged_directive(source):
    intent, task, value, old_brief, _, _ = reaffirm(source)
    with pytest.raises(TaskInterpretationRejected):
        admit_interpretation(task, value, intent, previous_brief=old_brief)


def test_only_preservation_branch_advertises_exact_reaffirmable_refs():
    _, task, value, old_brief, _, ref = reaffirm()
    contract = interpretation_contract(provider=True, task=task, previous_brief=old_brief)
    requirement = copy.deepcopy(value['requirements'][0])
    validator = Draft202012Validator({'$ref': '#/$defs/requirement', '$defs': contract['$defs']})
    validator.validate(requirement)
    requirement.update(operation='search', verification_family='query')
    assert list(validator.iter_errors(requirement))
