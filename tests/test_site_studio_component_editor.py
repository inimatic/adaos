from copy import deepcopy

import pytest

from adaos.services.site_studio.component_editor import component_editor, component_field_operations, component_collection_operations
from adaos.services.site_studio.contracts import apply_change_set
from test_site_studio_authoring import CREATED, _source, _change_set


def test_anchor_is_visible_copyable_and_renames_existing_navigation_atomically():
    source = _source('anchor-editor', CREATED)
    source['resources'][0]['data']['items'] = [
        {'label': 'Hero', 'href': '#hero'}, {'label': 'Footer', 'href': '#footer'},
        {'label': 'External', 'href': 'https://example.org/#hero'},
    ]
    original = deepcopy(source)
    editor = component_editor(source, 'hero', revision=3)
    assert editor['section_anchor_id'] == 'hero'
    assert editor['section_href'] == '#hero'
    assert any(field['id'] == 'section_anchor_id' and '#hero' in field['helpText'] for field in editor['fields'])
    changes = _change_set(source, value='unused')
    changes['operations'] = component_field_operations(editor, {'section_anchor_id': 'projects-publications'})
    result = apply_change_set(source, changes)
    assert source == original
    assert result['routes'][0]['sections'][1]['anchor_id'] == 'projects-publications'
    assert result['resources'][0]['data']['items'] == [
        {'label': 'Hero', 'href': '#projects-publications'}, {'label': 'Footer', 'href': '#footer'},
        {'label': 'External', 'href': 'https://example.org/#hero'},
    ]
    assert component_editor(result, 'hero', revision=4)['section_href'] == '#projects-publications'


@pytest.mark.parametrize('anchor', ['', 'bad anchor', '#hero', 'раздел', 'footer'])
def test_anchor_edits_reject_invalid_or_duplicate_names(anchor):
    editor = component_editor(_source('anchor-invalid', CREATED), 'hero', revision=1)
    with pytest.raises(ValueError, match='Якорь'):
        component_field_operations(editor, {'section_anchor_id': anchor})


def test_anchor_rename_preserves_bare_links_in_resources_outside_the_route():
    source = _source('anchor-route-scope', CREATED)
    source['routes'][0].pop('canonical_url', None)
    source['resources'][0]['data']['items'] = [{'label': 'Hero', 'href': '#hero'}]
    other_menu = deepcopy(source['resources'][0])
    other_menu['id'] = 'other-route-menu'
    other_menu['data']['items'].append({'label': 'This route', 'href': '/#hero'})
    source['resources'].append(other_menu)
    editor = component_editor(source, 'hero', revision=1)
    changes = _change_set(source, value='unused')
    changes['operations'] = component_field_operations(editor, {'section_anchor_id': 'new-hero'})
    result = apply_change_set(source, changes)
    assert result['resources'][-1]['data']['items'] == [
        {'label': 'Hero', 'href': '#hero'}, {'label': 'This route', 'href': '#new-hero'},
    ]


def test_dataset_rows_do_not_fill_the_component_tree_or_expose_row_reordering():
    source = _source('dataset-editor', CREATED)
    source['resources'].append({'id': 'cities-data', 'type': 'site.dashboard', 'public': True, 'data': {
        'title': 'Cities', 'idField': 'id', 'labelField': 'name',
        'rows': [{'id': f'city-{index}', 'name': f'City {index}', 'geo': {'lat': 55, 'lon': 37}} for index in range(953)],
        'columns': [{'field': 'name', 'label': 'City'}],
        'map': {'publicKey': '', 'latitudeField': 'lat', 'longitudeField': 'lon'},
    }})
    source['routes'][0]['sections'].insert(2, {'id': 'cities', 'component': 'site.dashboard', 'resource_ref': 'cities-data'})
    editor = component_editor(source, 'cities', revision=1)
    children = editor['tree'][0]['children']
    assert 'rows' not in [child['id'] for child in children]
    assert {'columns', 'map'} <= {child['id'] for child in children}
    assert editor['section_href'] == '#cities'
    with pytest.raises(ValueError, match='chat'):
        component_editor(source, 'cities', revision=1, node_path='rows.0')


def _cards_source():
    source = _source('cards-controls', CREATED)
    source['resources'].append({'id': 'publications', 'type': 'site.card_collection', 'public': True, 'data': {
        'title': 'Publications', 'columns': 1, 'presentation': {'style': 'editorial'}, 'items': [
            {'title': 'Journal', 'body': 'Original text', 'image': 'owned-cover',
             'action': {'href': 'https://example.org/issue', 'label': 'Read'}, 'custom': {'retained': True}}]}})
    source['routes'][0]['sections'].insert(2, {'id': 'publications', 'component': 'site.cardGrid', 'resource_ref': 'publications'})
    return source


def test_card_layout_controls_are_typed_and_saving_defaults_does_not_restyle_source():
    source = _cards_source()
    editor = component_editor(source, 'publications', revision=1)
    by_path = {path: identity for identity, path in editor['field_paths'].items()}
    fields = {field['id']: field for field in editor['fields']}
    assert {option['value'] for option in fields[by_path['presentation.contentWidth']]['options']} == {'auto', 'site', 'narrow', 'full'}
    assert fields[by_path['columns']]['min'] == 1 and fields[by_path['columns']]['max'] == 4
    assert component_field_operations(editor, {identity: editor[identity] for identity in editor['field_paths']}) == []
    changes = _change_set(source, value='unused')
    changes['operations'] = component_field_operations(editor, {by_path['presentation.contentWidth']: 'site', by_path['presentation.contentAlign']: 'center'})
    result = apply_change_set(source, changes)
    assert result['resources'][:-1] == source['resources'][:-1]
    assert result['resources'][-1]['data']['presentation'] == {'style': 'editorial', 'contentWidth': 'site', 'contentAlign': 'center'}
    assert result['resources'][-1]['data']['items'] == source['resources'][-1]['data']['items']


def test_card_insertion_and_removal_preserve_whole_objects_and_other_sections():
    source = _cards_source()
    original = deepcopy(source)
    editor = component_editor(source, 'publications', revision=1)
    operations, selected = component_collection_operations(editor, 'append', source['resources'][-1]['data']['items'])
    assert selected == 'items.1'
    changes = _change_set(source, value='unused'); changes['operations'] = operations
    added = apply_change_set(source, changes)
    assert source == original and added['resources'][:-1] == source['resources'][:-1]
    assert added['resources'][-1]['data']['items'][0] == original['resources'][-1]['data']['items'][0]
    item = component_editor(added, 'publications', revision=2, node_path='items.1')
    assert item['can_remove_item'] and not item['can_add_item']
    operations, selected = component_collection_operations(item, 'remove', added['resources'][-1]['data']['items'])
    changes = _change_set(added, value='unused'); changes['operations'] = operations
    assert selected == '__block__' and apply_change_set(added, changes)['resources'] == source['resources']
    with pytest.raises(ValueError): component_collection_operations(editor, 'remove', [])


def test_collection_controls_reject_dashboard_rows_and_nested_card_links():
    source = _cards_source()
    nested = component_editor(source, 'publications', revision=1, node_path='items.0.action')
    assert not nested['can_remove_item'] and not nested['can_add_item']
    with pytest.raises(ValueError): component_collection_operations(nested, 'remove', source['resources'][-1]['data']['items'])
    with pytest.raises(ValueError): component_collection_operations({'component': 'site.dashboard'}, 'append', [])
