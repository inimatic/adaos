"""Compare compiled event effects with an independently specified cursor model."""
import copy

from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate
from test_builder_semantic_prototype import _multi_resource_fixture, _multi_resource_candidate
from test_builder_semantic_presentations import pin_work_invocations
from adaos.services.ui_component_contracts import project_component_contract


def fixture():
    brief, document = _multi_resource_fixture()
    pin_work_invocations(document)
    projection = copy.deepcopy(document['views'][0])
    projection.update(id='work-for-person', initial_selection_id=None,
                      selection_filter={'source_view_ref': 'people-list', 'source_field_ref': 'id',
                                        'field_ref': 'work_owner_id', 'effect': 'filter', 'empty_selection': 'show_all'})
    document['views'].append(projection)
    return brief, document


def select(widget, identity, state):
    """Small independent interpreter for the declared selection event only."""
    actions = [row for row in widget['actions'] if row.get('on') == 'select']
    assert actions and all(row['type'] == 'updateState' for row in actions)
    for action in actions:
        state.update({key: identity if value == '$event.id' else value for key, value in action['params'].items()})


def test_selecting_parent_resets_only_its_projection_not_an_unrelated_peer_cursor():
    brief, source = fixture()
    original = copy.deepcopy(source)
    compiled = compile_semantic_prototype_candidate(_multi_resource_candidate(source), brief=brief)
    assert source == original
    page = compiled['webui']['ui']['application']['desktop']['pageSchema']
    widgets = {row['id']: row for row in page['widgets']}
    main, child, people = [widgets[name] for name in ('work-list', 'work-for-person', 'people-list')]
    key = lambda widget: widget['inputs']['selectedStateKey']
    assert len({key(main), key(child), key(people)}) == 3
    state = copy.deepcopy(page['initialState'])
    select(main, 'work-2', state)
    select(child, 'work-1', state)
    select(people, 'person-1', state)
    assert state[key(main)] == 'work-2'
    assert state[key(child)] == ''
    assert state[key(people)] == 'person-1'
    select(child, 'work-1', state)
    assert state[key(main)] == 'work-2'
    assert state[key(people)] == 'person-1'
    assert child['dataSource']['query']['filters']['work_owner_id'] == '$state.' + key(people)
    editor = widgets['work-editor']
    assert editor['inputs']['selectedStateKey'] == key(main)
    # An alias mutant reproduces the old conformance failure, not a passing reference.
    child_key = key(child)
    changed = copy.deepcopy(people)
    for action in changed['actions']:
        if child_key in action.get('params', {}):
            action['params'][key(main)] = action['params'].pop(child_key)
    select(changed, 'person-2', state)
    assert state[key(main)] != 'work-2'


def test_linked_projection_cursor_is_deterministic_and_its_editor_can_bind_explicitly():
    brief, source = fixture()
    for command in source['commands']:
        command['exposure']['collection_view_ref'] = 'work-for-person'
    compiled = compile_semantic_prototype_candidate(_multi_resource_candidate(source), brief=brief)
    again = compile_semantic_prototype_candidate(_multi_resource_candidate(source), brief=brief)
    assert compiled['webui'] == again['webui']
    widgets = {row['id']: row for row in compiled['webui']['ui']['application']['desktop']['pageSchema']['widgets']}
    assert widgets['work-editor']['inputs']['selectedStateKey'] == widgets['work-for-person']['inputs']['selectedStateKey']
    assert widgets['work-editor']['inputs']['selectedStateKey'] != widgets['work-list']['inputs']['selectedStateKey']


def test_c2_delivers_selection_ownership_to_component_context():
    projection = project_component_contract()
    for unit in projection['retrieval_units']:
        rule = unit['content']['state_ownership']['selection']
        assert 'isolated view cursor' in rule and 'unrelated peer' in rule
