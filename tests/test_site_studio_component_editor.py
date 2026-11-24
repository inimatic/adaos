from copy import deepcopy

import pytest

from adaos.services.site_studio.component_editor import component_editor, component_field_operations
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
