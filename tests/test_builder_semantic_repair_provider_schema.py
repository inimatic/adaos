"""Bounded repairs must reach the model with an admissible strict schema."""
import pytest

from adaos.services.builder.semantic_prototype import semantic_prototype_provider_contract
from adaos.services.builder.semantic_repair import prepare_collection_repair, prepare_state_repair


@pytest.mark.parametrize('view_variants', [False, True])
def test_repair_and_generation_object_properties_are_all_required(view_variants):
    schema = semantic_prototype_provider_contract(version='v2', _view_variants=view_variants)
    assert_strict(schema)


def assert_strict(node, path='$'):
    if isinstance(node, dict):
        if node.get('type') == 'object' and 'properties' in node:
            assert node.get('additionalProperties') is False, path
            assert set(node.get('required', [])) == set(node['properties']), path
        for name, value in node.items():
            assert_strict(value, path + '.' + name)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            assert_strict(value, path + '.' + str(index))


def test_collection_repair_schema_is_strict_at_every_reachable_object():
    candidate = {
        'title': {'ru': 'Города'},
        'resources': [{'id': 'cities', 'fields': [{'id': 'name', 'label': {'ru': 'Город'}, 'value_type': 'short_text'}], 'records': [{'id': 'city-1', 'values': ['Плёс']}]}],
        'views': [{'id': 'passport', 'resource_ref': 'cities', 'role': 'details', 'region_role': 'primary', 'section': None, 'title': {'ru': 'Паспорт'}}],
    }
    plan = prepare_collection_repair(candidate, [{'code': 'semantic.resource_collection_missing', 'semantic_refs': ['resource:cities']}])
    assert plan is not None
    assert_strict(plan['output_schema'])


@pytest.mark.parametrize('version', [1, 2, 3])
def test_state_repair_schema_is_strict_at_every_reachable_object(version):
    candidate = {
        'title': {'ru': 'Города'},
        'resources': [{'id': 'cities', 'fields': [{'id': 'population', 'label': {'ru': 'Население'}, 'value_type': 'number', 'options': []}], 'records': []}],
        'views': [{'id': 'cities', 'resource_ref': 'cities', 'role': 'collection', 'region_role': 'primary', 'field_refs': ['population']}],
        'representative_states': [{'id': 'missing', 'view_ref': 'cities', 'proof': {'kind': 'field_predicate', 'visible_field_refs': ['population']}, 'filters': [], 'min_items': 1, 'max_items': None}],
    }
    plan = prepare_state_repair(candidate, [{'code': 'semantic.state_predicate_invalid', 'semantic_refs': ['state:missing']}], version=version)
    assert plan is not None
    assert_strict(plan['output_schema'])
