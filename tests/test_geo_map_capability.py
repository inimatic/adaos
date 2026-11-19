from adaos.sdk.developer import ui


def test_native_record_map_is_a_supported_webui_projection():
    document = {
        'schema': 'adaos.webui.v1',
        'ui': {'application': {'desktop': {'pageSchema': {
            'id': 'map', 'title': 'Города',
            'layout': {'version': 2, 'pattern': 'dashboard', 'density': 'comfortable', 'regions': [
                {'id': 'main', 'role': 'main', 'priority': 100, 'scroll': 'page', 'presentation': {'wide': 'pane', 'compact': 'stack'}},
            ]},
            'widgets': [{
                'id': 'cities', 'type': 'visual.geoMap', 'area': 'main',
                'inputs': {'latitudeField': 'lat', 'longitudeField': 'lon', 'stateKey': 'city', 'selectionValue': 'key'},
                'dataSource': {'kind': 'static', 'value': {'items': []}},
            }],
        }}}},
    }
    assert ui.validate_contract(document)['ok']
    result = ui.validate(document)
    assert result['ok'], result
