import copy

import pytest

from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate
from adaos.services.builder.workflow import BuilderWorkflowError
from test_builder_semantic_prototype import _multi_resource_candidate, _multi_resource_fixture, _text


@pytest.mark.parametrize('presentation,widget_type', [
    ('board', 'collection.board'), ('accordion', 'ui.list'), ('tree', 'collection.tree'), ('chart', 'visual.metricChart'),
])
def test_extended_collection_preserves_resource_query_and_field_provenance(presentation, widget_type):
    brief, semantic = _multi_resource_fixture()
    resource = semantic['resources'][0]
    extra = copy.deepcopy(semantic['views'][0])
    extra.update(id='extra-view', presentation=presentation)
    options = dict(group_field_ref=None, parent_field_ref=None, value_field_ref=None, draggable=False)
    if presentation in {'board', 'accordion'}:
        options.update(group_field_ref='result', draggable=presentation == 'board')
    elif presentation == 'tree':
        resource['fields'].append(dict(id='parent', value_type='short_text', label=_text('parent', 'Parent', 'Parent'), required=False, editable=False))
        for record in resource['records']:
            record['parent'] = None
        options['parent_field_ref'] = 'parent'
    else:
        resource['fields'].append(dict(id='amount', value_type='number', label=_text('amount', 'Amount', 'Amount'), required=False, editable=False))
        for i, record in enumerate(resource['records']):
            record['amount'] = i + 1
        options.update(group_field_ref='title', value_field_ref='amount')
        extra['field_refs'] = ['title', 'amount']
    extra['presentation_options'] = options
    semantic['views'].append(extra)
    semantic['representative_states'][0]['view_ref'] = 'extra-view'
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    page = result['webui']['ui']['application']['desktop']['pageSchema']
    widget = next(widget for widget in page['widgets'] if widget['id'] == 'extra-view')
    assert widget['type'] == widget_type
    assert widget['dataSource']['kind'] == 'resourceQuery'
    state_id = semantic['representative_states'][0]['id']
    observable = result['source_map'][f'state:{state_id}']
    assert observable
    assert any('@extra-view.inputs.empty' in path for path in observable)
    for ref in extra['field_refs']:
        assert any('@extra-view.inputs.' in path for path in result['source_map'][f'field:{ref}'])
    if presentation == 'board':
        action = next(action for action in widget['actions'] if action['on'] == 'move')
        assert action['target'] == widget['dataSource']['resourceType']
        assert action['params'] == {'operation_id': 'update', 'record_id': '$event.id', 'payload': '$event.patch'}
        assert widget['inputs']['reorderWithinLane'] is False
    elif presentation == 'accordion':
        assert widget['inputs']['groupDisplay'] == 'accordion'
    elif presentation == 'tree':
        assert widget['inputs']['selectionMode'] == 'all'
        selected = widget['inputs']['selectedStateKey']
        assert any(action.get('params', {}).get(selected) == '$event.id' for action in widget['actions'])


def test_explicit_text_policy_survives_candidate_normalization():
    brief, semantic = _multi_resource_fixture()
    view = semantic['views'][0]
    view['presentation'] = 'cards'
    view['field_display'] = [
        {'field_ref': 'title', 'overflow': 'truncate', 'align': 'start'},
        {'field_ref': 'status', 'overflow': 'wrap', 'align': 'end'},
    ]
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    widget = next(widget for widget in result['webui']['ui']['application']['desktop']['pageSchema']['widgets'] if widget['id'] == view['id'])
    assert widget['inputs']['titleOverflow'] == 'truncate'
    assert next(meta for meta in widget['inputs']['meta'] if meta['key'] == 'status')['align'] == 'end'


def test_non_numeric_chart_is_rejected_not_coerced():
    brief, semantic = _multi_resource_fixture()
    view = semantic['views'][0]
    view.update(presentation='chart', field_refs=['title', 'status'], presentation_options={
        'group_field_ref': 'title', 'parent_field_ref': None, 'value_field_ref': 'status', 'draggable': False,
    })
    with pytest.raises(BuilderWorkflowError, match='numeric'):
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)


def test_tree_parent_reference_survives_relationship_selector_lowering():
    brief, semantic = _multi_resource_fixture()
    resource = semantic['resources'][0]
    resource['fields'].append(dict(id='parent', value_type='short_text', label=_text('parent', 'Parent', 'Parent'), required=False, editable=True))
    for i, record in enumerate(resource['records']):
        record['parent'] = resource['records'][0]['id'] if i else None
    semantic['relationships'].append({
        'id': 'parent-link', 'from_resource_ref': resource['id'], 'from_field_ref': 'parent',
        'to_resource_ref': resource['id'], 'to_field_ref': 'id', 'cardinality': 'many_to_one',
        'label_field_refs': ['title'],
    })
    view = copy.deepcopy(semantic['views'][0])
    view.update(id='hierarchy', presentation='tree', field_refs=['title'], presentation_options={
        'group_field_ref': None, 'parent_field_ref': 'parent', 'value_field_ref': None, 'draggable': False,
    })
    semantic['views'].append(view)
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    widget = next(item for item in result['webui']['ui']['application']['desktop']['pageSchema']['widgets'] if item['id'] == 'hierarchy')
    assert widget['inputs']['parentIdKey'] == 'parent'
    assert next(field for field in result['semantic_document']['resources'][0]['fields'] if field['id'] == 'parent')['value_type'] == 'choice'


def test_sections_group_existing_content_without_changing_resource_identity():
    brief, semantic = _multi_resource_fixture()
    for view in semantic['views']:
        section_id = 'settings' if view['resource_ref'] == 'people' else 'work'
        view['section'] = {'id': section_id, 'title': _text(section_id, section_id, section_id),
                           'kind': 'settings' if section_id == 'settings' else 'tab'}
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    application = result['webui']['ui']['application']
    page = application['desktop']['pageSchema']
    assert page['initialState']['prototype_section'] == 'work'
    assert page['widgets'][0]['inputs']['variant'] == 'tabs'
    assert all(widget.get('visibleIf') for widget in page['widgets'] if widget['id'].startswith('work-'))
    assert application['modals']['settings-settings']['schema']['widgets'][0]['dataSource']['resourceType'] == 'prototype.people'
    assert all('modals.settings-settings' in ref for ref in result['source_map']['view:people-list'])


def test_conflicting_sections_are_rejected_instead_of_overwriting_content():
    brief, semantic = _multi_resource_fixture()
    for view in semantic['views']:
        view['section'] = {'id': 'one', 'kind': 'tab', 'title': _text('one', view['id'], view['id'])}
    with pytest.raises(BuilderWorkflowError, match='Conflicting'):
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)


@pytest.mark.parametrize('inverse', [False, True])
def test_selection_filter_resolves_source_action_and_preserves_independent_queries(inverse):
    brief, semantic = _multi_resource_fixture()
    view = semantic['views'][0]
    view['selection_filter'] = {'field_ref': 'work_owner_id', 'source_view_ref': 'people-list'}
    if inverse:
        relation = semantic['relationships'][0]
        relation.update(from_resource_ref='people', from_field_ref='id', to_resource_ref='work_items',
                        to_field_ref='work_owner_id', cardinality='one_to_many')
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    page = result['webui']['ui']['application']['desktop']['pageSchema']
    widgets = {widget['id']: widget for widget in page['widgets']}
    source = widgets['people-list']
    selected = next(key for action in source['actions'] if action['type'] == 'updateState'
                    for key, expression in action['params'].items() if expression == '$event.id')
    assert widgets[view['id']]['dataSource']['query']['filters']['work_owner_id'] == f'$state.{selected}'
    assert page['initialState'][selected] == ''
    assert result['semantic_document']['views'][0]['selection_filter'] == {**view['selection_filter'], 'source_field_ref': None}


def test_record_presentation_error_explains_the_role_not_a_lost_presentation():
    from adaos.services.builder.semantic_presentations import presentation_findings

    _, semantic = _multi_resource_fixture()
    view = next(view for view in semantic['views'] if view['role'] == 'details')
    view['presentation_options'] = {'group_field_ref': view['field_refs'][0]}
    findings = presentation_findings(semantic)
    assert any('use a collection for grouped records' in item['detail'] for item in findings)


@pytest.mark.parametrize('inverse', [False, True])
def test_selection_can_lookup_parent_using_selected_source_foreign_key(inverse):
    brief, semantic = _multi_resource_fixture()
    source = semantic['views'][0]
    target = next(view for view in semantic['views'] if view['id'] == 'people-list')
    target['selection_filter'] = {'field_ref': 'id', 'source_view_ref': source['id'],
                                  'source_field_ref': 'work_owner_id'}
    if inverse:
        semantic['relationships'][0].update(from_resource_ref='people', from_field_ref='id',
                                           to_resource_ref='work_items', to_field_ref='work_owner_id',
                                           cardinality='one_to_many')
    alternative = copy.deepcopy(source)
    alternative['id'] = 'alternative-source'
    semantic['views'].append(alternative)
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    page = result['webui']['ui']['application']['desktop']['pageSchema']
    widgets = {widget['id']: widget for widget in page['widgets']}
    reference = widgets[target['id']]['dataSource']['query']['filters']['id']
    key = reference.removeprefix('$state.')
    assert page['initialState'][key] == ''
    for source_id in (source['id'], alternative['id']):
        assert any(action.get('params', {}).get(key) == '$event.work_owner_id'
                   for action in widgets[source_id]['actions'])


def test_selection_cascade_clears_child_but_preserves_ancestor_lookup():
    brief, semantic = _multi_resource_fixture()
    child = semantic['views'][0]
    child['selection_filter'] = {'field_ref': 'work_owner_id', 'source_view_ref': 'people-list'}
    lookup = copy.deepcopy(next(view for view in semantic['views'] if view['id'] == 'people-list'))
    lookup.update(id='people-lookup', selection_filter={
        'field_ref': 'id', 'source_view_ref': child['id'], 'source_field_ref': 'work_owner_id'})
    semantic['views'].append(lookup)
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    widgets = {widget['id']: widget for widget in result['webui']['ui']['application']['desktop']['pageSchema']['widgets']}
    def selection_action(view_id):
        return next(action['params'] for action in widgets[view_id]['actions']
                    if action['type'] == 'updateState' and action['on'] == 'select')
    parent = selection_action('people-list')
    selected_parent = next(key for key, value in parent.items() if value == '$event.id')
    selected_child = next(key for key, value in selection_action(child['id']).items() if value == '$event.id')
    selected_fk = widgets[lookup['id']]['dataSource']['query']['filters']['id'].removeprefix('$state.')
    assert parent[selected_child] == ''
    assert parent[selected_fk] == ''
    assert selected_parent not in selection_action(child['id'])
    assert selection_action(child['id'])[selected_fk] == '$event.work_owner_id'


@pytest.mark.parametrize('broken', ['unknown_source_field', 'unrelated_source_field', 'cycle'])
def test_reverse_selection_rejects_invalid_endpoints_and_cycles(broken):
    brief, semantic = _multi_resource_fixture()
    child = semantic['views'][0]
    target = next(view for view in semantic['views'] if view['id'] == 'people-list')
    target['selection_filter'] = {'field_ref': 'id', 'source_view_ref': child['id'],
                                  'source_field_ref': 'work_owner_id'}
    if broken == 'cycle':
        child['selection_filter'] = {'field_ref': 'work_owner_id', 'source_view_ref': target['id']}
    else:
        target['selection_filter']['source_field_ref'] = 'absent' if broken == 'unknown_source_field' else 'title'
    with pytest.raises(BuilderWorkflowError, match='selection_filter'):
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)


@pytest.mark.parametrize('broken', ['missing_source', 'chart_source', 'wrong_key', 'filter_conflict', 'editor_target'])
def test_selection_filter_rejects_unexecutable_links(broken):
    brief, semantic = _multi_resource_fixture()
    view = semantic['views'][0]
    view['selection_filter'] = {'field_ref': 'work_owner_id', 'source_view_ref': 'people-list'}
    if broken == 'missing_source':
        view['selection_filter']['source_view_ref'] = 'absent'
    elif broken == 'chart_source':
        semantic['views'][-1]['presentation'] = 'chart'
    elif broken == 'wrong_key':
        view['selection_filter']['field_ref'] = 'title'
    elif broken == 'filter_conflict':
        view['scope_filters'] = [{'field_ref': 'work_owner_id', 'value': 'person-1'}]
    else:
        semantic['views'][1]['selection_filter'] = view.pop('selection_filter')
    with pytest.raises(BuilderWorkflowError, match='selection_filter|selection filters'):
        compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
