import copy
import json

import pytest

from adaos.services.builder.interpretation_boundary import contract, findings, field_order_evidence
from adaos.services.builder.task_interpretation import interpretation_messages, admit_interpretation, TaskInterpretationRejected
from test_builder_semantic_ui_constraints import proposal


def ordered(*clauses):
    return {'constraints': [{'kind': 'field_order', 'subject': 'Checklists',
        'values': ['work_id', 'description', 'done'], 'clause_ids': list(clauses or ['clause:001'])}]}


@pytest.mark.parametrize('text', [
    'Show all source fields in a table.',
    'Record c1: work_id w1, description "Check seal", done false.',
    'Add a Checklists directory with fields work_id, description, done.',
    'Any column order is acceptable: work_id, description, done.',
    'Column order is arbitrary: work_id, description, done.',
    'For example, show columns in this order: work_id, description, done.',
    'If approved, show columns in this order: work_id, description, done.',
    'Record R1: note "Show columns in this order: work_id, description, done".',
    'Do not show columns in this order: work_id, description, done.',
])
def test_enumeration_freedom_examples_and_unresolved_scope_are_not_hard_order(text):
    assert field_order_evidence(text) == []
    assert findings(text, ordered())[0]['code'] == 'task.constraint_authority_unproved'
    assert findings(text, {'constraints': [], 'design_freedom': ['Choose a readable field order']}) == []


@pytest.mark.parametrize('text', [
    'Show columns in this order: work_id, description, done.',
    'Set the column order to work_id, description, done.',
    'Column order: work_id, description, done.',
    'Покажи поля в следующем порядке: work_id, description, done.',
    'Порядок полей: work_id, description, done.',
])
def test_explicit_ordering_with_cocited_literals_is_supported(text):
    assert field_order_evidence(text)
    assert findings(text, ordered()) == []


def test_introduction_or_unrelated_clause_cannot_borrow_order_authority():
    text = ('Add Checklists.\nRecord c1: work_id w1, description "Check seal", done false.\n'
            'Set another table column order to state, title.')
    for clauses in [('clause:001',), ('clause:002',), ('clause:002', 'clause:003')]:
        assert findings(text, ordered(*clauses))


def test_same_rule_is_delivered_and_enforced_by_real_g3_without_inventing_a_constraint():
    text = 'Show work_id, description, done in a table.'
    intent, task, value = proposal(text)
    context = json.loads(interpretation_messages(task)[1]['content'])['interpretation_request']
    assert context['interpretation_boundary'] == contract()
    assert context['source_field_order_evidence'] == []
    value['design_freedom'] = ['Choose a readable column order']
    accepted, brief = admit_interpretation(task, value, intent)
    assert accepted['interpretation_status'] == 'admitted'
    assert not any(row['kind'] == 'field_order' for row in brief['task_contract']['constraints'])
    invalid = copy.deepcopy(value)
    invalid['constraints'] = ordered()['constraints']
    with pytest.raises(TaskInterpretationRejected) as error:
        admit_interpretation(task, invalid, intent)
    assert 'task.constraint_authority_unproved' in {row['code'] for row in error.value.findings}


def test_source_graph_must_not_inherit_design_choice_as_an_obligation():
    text = 'Show work_id, description, done in this order.'
    intent, task, value = proposal(text)
    value['constraints'] = ordered()['constraints']
    _, brief = admit_interpretation(task, value, intent)
    assert any(row['kind'] == 'field_order' for row in brief['task_contract']['constraints'])


def test_generic_existing_table_selector_resolves_only_against_an_exact_unique_base():
    from adaos.services.builder.interpretation_boundary import reference_context
    from adaos.services.builder.task_spec import seal
    from adaos.services.builder_context import digest
    text = 'Set the title of the existing table to "Queue".'
    intent, task, value = proposal(text)
    value['constraints'] = [{'kind': 'view_title', 'subject': 'existing table',
        'values': ['Queue'], 'clause_ids': ['clause:001']}]
    document = {'views': [{'id': 'original', 'role': 'collection', 'resource_ref': 'records'}]}
    task = seal({**task, 'reference_context': reference_context(document)})
    raw = copy.deepcopy(value)
    admitted, brief = admit_interpretation(task, value, intent)
    assert value == raw
    assert admitted['proposal_digest'] == digest(raw)
    assert brief['task_contract']['interpretation_normalizations'][0]['owner_ref'] == 'view:original'
    assert {row['subject'] for row in brief['task_contract']['constraints'] if row['kind'] == 'view_title'} == {''}
    document['views'].append({**document['views'][0], 'id': 'other'})
    ambiguous = seal({**task, 'reference_context': reference_context(document)})
    with pytest.raises(TaskInterpretationRejected) as error:
        admit_interpretation(ambiguous, value, intent)
    assert error.value.findings[0]['code'] == 'task.display_selector_ambiguous'


def test_named_new_collection_is_not_normalized_to_the_existing_collection():
    from adaos.services.builder.interpretation_boundary import normalize_generic_selectors
    task = {'clauses': [{'id': 'clause:001', 'text': 'Add a Checklists table.'}],
            'reference_context': {'collections': [{'id': 'work-orders'}], 'baseline_digest': 'pinned'}}
    value = {'constraints': [{'kind': 'view_title', 'subject': 'Checklists', 'values': ['Checklists'], 'clause_ids': ['clause:001']}]}
    assert normalize_generic_selectors(task, value) == (value, [])


def test_reference_context_refuses_a_changed_compact_baseline():
    from adaos.services.builder.interpretation_boundary import reference_context
    with pytest.raises(ValueError, match='pinned compiler view'):
        reference_context({'schema': 'adaos.builder.semantic_compiler_view.v1', 'document': {'views': []}, 'view_digest': 'wrong'})
