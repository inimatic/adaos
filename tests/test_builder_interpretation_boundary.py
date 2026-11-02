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
    'Keep the work_id/description/done columns in their existing relative order.',
    'When selected, show details. Show columns in this order: work_id, description, done.',
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
    assert {row['subject'] for row in brief['task_contract']['constraints'] if row['kind'] == 'view_title'} == {'view:original'}
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


def test_quoted_literal_collection_name_is_not_a_generic_selector():
    from adaos.services.builder.interpretation_boundary import normalize_generic_selectors
    task = {'clauses': [{'id': 'clause:001', 'text': 'Rename the collection "existing table" to "Queue".'}],
            'reference_context': {'collections': [{'id': 'other'}], 'baseline_digest': 'pinned'}}
    value = {'constraints': [{'kind': 'view_title', 'subject': 'existing table', 'values': ['Queue'], 'clause_ids': ['clause:001']}]}
    assert normalize_generic_selectors(task, value) == (value, [])


@pytest.mark.parametrize('text', ['Add a Teams directory.', 'Show the name "Teams directory" in a record.'])
def test_a_topic_mention_does_not_request_an_exact_view_heading(text):
    value = {'constraints': [{'kind': 'view_title', 'subject': 'Teams directory',
        'values': ['Teams directory'], 'clause_ids': ['clause:001']}]}
    assert findings(text, value)[0]['code'] == 'task.constraint_authority_unproved'


@pytest.mark.parametrize('text', [
    'Add a table titled "Orders for selected team".',
    'Add a directory named Teams.',
    'Добавь таблицу с названием «Команды».',
])
def test_explicit_new_collection_title_has_source_authority(text):
    from adaos.services.builder.interpretation_boundary import display_directive_evidence
    row = display_directive_evidence(text)[0]
    assert findings(text, {'constraints': [row]}) == []


def test_record_headed_attribute_is_exactly_grounded_not_a_different_subject():
    from adaos.services.builder.task_interpretation import _attribute_literal
    assert _attribute_literal('w1 has team_id T-A', 'team_id', 'T-A', subject='w1')
    assert not _attribute_literal('w2 has team_id T-A', 'team_id', 'T-A', subject='w1')
    assert not _attribute_literal('w1 has team_id T-B', 'team_id', 'T-A', subject='w1')
    assert not _attribute_literal('perhaps w1 has team_id T-A', 'team_id', 'T-A', subject='w1')


def test_existing_exact_heading_preservation_retains_prior_authority_not_a_new_constraint():
    row = {'kind': 'view_title', 'subject': 'Queue', 'values': ['Queue'], 'clause_ids': ['clause:001']}
    text = 'Keep the existing table "Queue" on Teams.'
    assert findings(text, {'constraints': [row]})
    assert findings(text, {'constraints': [row]}, retained_constraints=[row]) == []
    for changed in ({**row, 'subject': 'Other'}, {**row, 'values': ['Other']}):
        assert findings(text, {'constraints': [row]}, retained_constraints=[changed])
    for invalid in ('If approved, keep the existing table "Queue".', 'Do not keep the existing table "Queue".',
                    'Add a Queue directory.', 'Record R1: note \'Keep the existing table "Queue".\''):
        assert findings(invalid, {'constraints': [row]}, retained_constraints=[row])


def test_g3_preserves_verified_prior_heading_and_does_not_drop_product_role_check():
    from adaos.services.builder.task_interpretation import role_coverage_contract
    old_text = 'Add a table titled "Queue".'
    old_intent, old_task, old_proposal = proposal(old_text)
    old_proposal['constraints'] = [{'kind': 'view_title', 'subject': 'Queue', 'values': ['Queue'], 'clause_ids': ['clause:001']}]
    _, old_brief = admit_interpretation(old_task, old_proposal, old_intent)
    text = 'Keep the existing table "Queue".'
    intent, task, value = proposal(text)
    value['constraints'] = copy.deepcopy(old_proposal['constraints'])
    _, brief = admit_interpretation(task, value, intent, previous_brief=old_brief)
    assert any(row['values'] == ['Queue'] for row in brief['task_contract']['constraints'])
    assert 'SAME clause' in role_coverage_contract()['preservation_boundary']
    invalid = copy.deepcopy(value)
    invalid['constraints'] = []
    invalid['requirements'][0].update(kind='authoring', verification_family='policy')
    with pytest.raises(TaskInterpretationRejected) as error:
        admit_interpretation(task, invalid, intent, previous_brief=old_brief)
    assert 'task.product_unrepresented' in {row['code'] for row in error.value.findings}


def test_pure_behavior_preservation_is_expressible_in_both_provider_and_canonical_contracts():
    from jsonschema import Draft202012Validator
    from adaos.services.builder.interpretation_delivery import request_packet, decode_proposal
    from test_builder_interpretation_clause_refs import wire_record
    intent, task, value = proposal('Preserve the existing search and filter without changing their scope.')
    value['requirements'][0].update(kind='behavior', operation=None, verification_family='preservation')
    packet, record = wire_record(task, value)
    Draft202012Validator(packet['wire_schema']).validate(json.loads(record['result']['output_text']))
    _, brief = admit_interpretation(task, decode_proposal(record), intent)
    assert brief['operations'] == []
    assert any(r['kind'] == 'behavior' and r['operation'] is None
               and r['verification_family'] == 'preservation' for r in brief['task_contract']['requirements'])
    invalid = copy.deepcopy(value)
    invalid['requirements'][0]['verification_family'] = 'value'
    with pytest.raises(TaskInterpretationRejected): admit_interpretation(task, invalid, intent)
    assert 'new/changed behavior has a non-null' in request_packet(task)['messages'][0]['content']


def test_normalized_existing_reference_survives_a_new_peer_with_a_colliding_display_name():
    from adaos.services.builder.interpretation_boundary import normalize_generic_selectors
    from adaos.services.builder.semantic_ui_constraints import display_owner
    task={'clauses':[{'id':'clause:001','text':'Set the title of the existing table to "New heading".'}],
          'reference_context':{'collections':[{'id':'original'}],'baseline_digest':'frozen'}}
    proposal={'constraints':[{'kind':'view_title','subject':'existing table','values':['New heading'],'clause_ids':['clause:001']}]}
    admitted,receipts=normalize_generic_selectors(task,proposal)
    constraint=admitted['constraints'][0]
    assert constraint['subject']=='view:original' and receipts[0]['baseline_digest']=='frozen'
    views={'original':{'id':'original','role':'collection','title':{'en':'New heading'}},
           'peer':{'id':'peer','role':'collection','title':{'en':'original'}}}
    owner=display_owner(constraint,views=views,resources={},previous=None,bindings=[],
        shown=lambda v:list(v['title'].values()),matches=lambda text,values:text in values,
        literal_matches=lambda *args,**kwargs:True,bound_field=lambda *args:None)
    assert owner=='view:original'
    views['peer']['title']['en']='view:missing'
    assert display_owner({**constraint,'subject':'view:missing'},views=views,resources={},previous=None,bindings=[],
        shown=lambda v:list(v['title'].values()),matches=lambda text,values:text in values,
        literal_matches=lambda *args,**kwargs:True,bound_field=lambda *args:None) is None


def test_prior_transition_is_history_not_new_current_source_literal_evidence():
    from adaos.services.builder.task_interpretation import role_coverage_contract
    intent, task, _ = proposal('Preserve the existing command.')
    delivered = json.loads(interpretation_messages(task)[1]['content'])['interpretation_request']
    rule = role_coverage_contract()['history_boundary']
    assert 'do not re-extract' in rule and 'NEW transitions row' in rule
    assert rule in json.dumps(delivered,ensure_ascii=False)


def test_existing_operation_cards_are_read_only_canonical_graph_projections_not_new_requirements():
    from adaos.services.builder.interpretation_boundary import reference_context
    from adaos.services.builder.semantic_graph_model import semantic_document_to_graph
    from test_builder_semantic_prototype import _multi_resource_fixture
    _, document = _multi_resource_fixture()
    view = document['views'][0]
    field = view['field_refs'][0]
    view['query_controls'] = [{'id':'existing-search','kind':'search','field_ref':field,
                              'label':{'key':'existing.search','en':'Search'}}]
    document['requirement_bindings'].append({'requirement_ref':'old-search','semantic_refs':['query:existing-search']})
    before = copy.deepcopy(document)
    context = reference_context(document)
    refs = {row['ref'] for row in semantic_document_to_graph(document)['nodes']}
    for key in ('query_controls','commands','state_proofs','supporting_views'):
        assert all(card['ref'] in refs for card in context[key])
    query = context['query_controls'][0]
    assert query['owner_view_ref'] == 'view:' + view['id']
    assert query['prior_requirement_refs'] == ['old-search']
    assert 'records' not in context['resources'][0]
    query['label']['en'] = 'changed outside the source'
    assert document == before
