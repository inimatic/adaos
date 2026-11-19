from adaos.sdk.developer import ui


def test_bounded_record_selection_is_a_native_webui_contract():
    document = {'schema': 'adaos.webui.v1', 'ui': {'application': {'desktop': {'pageSchema': {
        'id': 'compare', 'initialState': {'cityIds': []},
        'layout': {'version': 2, 'pattern': 'collection', 'density': 'comfortable',
                   'interaction': {'selection': 'multiple'}, 'regions': [
                       {'id': 'main', 'role': 'collection', 'priority': 100,
                        'presentation': {'wide': 'pane', 'compact': 'stack'}},
                   ]},
        'widgets': [{'id': 'cities', 'type': 'ui.table', 'area': 'main',
                     'inputs': {'rowKey': 'id', 'selectedStateKey': 'cityIds',
                                'selectionIndicator': 'checkbox', 'maxSelected': 4,
                                'selectionLimitText': '{count} / {max}',
                                'columns': [{'key': 'name'}]},
                     'dataSource': {'kind': 'static', 'value': []},
                     'actions': [{'on': 'select', 'type': 'mutateState', 'params': {'operations': [
                         {'op': 'toggleArrayItem', 'path': 'cityIds', 'value': '$event.id'},
                     ]}}]}],
    }}}}}
    assert ui.validate_contract(document)['ok']
    result = ui.validate(document)
    assert result['ok'], result
