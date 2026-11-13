import pytest

from adaos.services import ui_capabilities as ui


def catalog(monkeypatch, components):
    monkeypatch.setattr(ui, 'ui_capability_catalog', lambda: {
        'components': list(components.values()), 'navigation_contracts': {'owned': True},
    })
    monkeypatch.setattr(ui, 'get_ui_capability', lambda key: components[key])


def test_source_contracts_take_priority_over_advisory_ranking_with_complete_dependencies(monkeypatch):
    items = {str(i): {'id': str(i)} for i in range(30)}
    items['29']['requires'] = {'components': ['28']}
    items['23']['requires'] = {'components': ['24', '25']}
    catalog(monkeypatch, items)
    result = ui._with_source_ui_contracts({
        'items': list(items.values())[:26], 'root_item_ids': list(items)[:24],
        'dependency_closure': ['24', '25'],
    }, {'widgets': [{'type': '29'}]})
    ids = {item['id'] for item in result['items']}
    assert {'28', '29'} <= ids
    assert len(ids) <= 24
    assert '23' in result['deferred_root_item_ids']
    assert result['deferred_contract_lookup'] == 'adaos.sdk.developer.ui.get'
    for item in result['items']:
        assert set(item.get('requires', {}).get('components', [])) <= ids
    assert result['navigation_contracts'] == {'owned': True}


def test_oversized_mandatory_source_closure_still_fails(monkeypatch):
    items = {str(i): {'id': str(i)} for i in range(25)}
    catalog(monkeypatch, items)
    with pytest.raises(ValueError, match='source contract closure exceeds'):
        ui._with_source_ui_contracts({'items': [], 'root_item_ids': []}, {
            'widgets': [{'type': str(i)} for i in range(25)],
        })


def test_source_dependency_cycle_is_bounded_and_preserves_focused_recipe(monkeypatch):
    items = {'viewer': {'id': 'viewer', 'requires': {'components': ['progress']}},
             'progress': {'id': 'progress', 'requires': {'components': ['viewer']}},
             'recipe': {'id': 'recipe', 'focused': True}}
    catalog(monkeypatch, items)
    result = ui._with_source_ui_contracts({'items': [items['recipe']], 'root_item_ids': ['recipe']},
                                        {'type': 'viewer'})
    assert result['source_item_ids'] == ['viewer']
    assert result['dependency_closure'] == ['progress']
    assert len(result['items']) == 3
    assert next(item for item in result['items'] if item['id'] == 'recipe')['focused']
