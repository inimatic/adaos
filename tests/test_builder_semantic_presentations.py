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
    result = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    page = result['webui']['ui']['application']['desktop']['pageSchema']
    widget = next(widget for widget in page['widgets'] if widget['id'] == 'extra-view')
    assert widget['type'] == widget_type
    assert widget['dataSource']['kind'] == 'resourceQuery'
    for ref in extra['field_refs']:
        assert any('@extra-view.inputs.' in path for path in result['source_map'][f'field:{ref}'])
    if presentation == 'board':
        action = next(action for action in widget['actions'] if action['on'] == 'move')
        assert action['target'] == widget['dataSource']['resourceType']
        assert action['params'] == {'operation_id': 'update', 'record_id': '$event.id', 'payload': '$event.patch'}
        assert widget['inputs']['reorderWithinLane'] is False
    elif presentation == 'accordion':
        assert widget['inputs']['groupDisplay'] == 'accordion'


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
