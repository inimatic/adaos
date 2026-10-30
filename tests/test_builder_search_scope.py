import copy

import pytest

from adaos.services.resources import PrototypeResourceService, ResourceWorkbenchService
from adaos.services.resources.search_scope import search_text
from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate
from test_prototype_resource_workbench import _bundle
from test_builder_semantic_prototype import _multi_resource_fixture, _multi_resource_candidate, _text


def test_literal_scoped_search_excludes_property_names_metadata_and_unrelated_values(tmp_path):
    bundle = _bundle()
    records = bundle['data_definition']['seed']
    records[0].update(title='RU', status='planned')
    records[1].update(title='EU', status='done')
    prototypes = PrototypeResourceService(state_dir=tmp_path)
    prototypes.materialize(bundle)
    workbench = ResourceWorkbenchService(state_dir=tmp_path)
    query = {'resource_type': bundle['resource_definition']['resource_type'], 'search_fields': ['title']}
    assert [row['title'] for row in workbench.query({**query, 'search': 'ru'})['items']] == ['RU']
    for text in ('title', 'planned', records[0]['id']):
        assert workbench.query({**query, 'search': text})['items'] == []
    assert len(workbench.query({**query, 'search': ''})['items']) == len(records)
    with pytest.raises(ValueError, match='declared record fields'):
        workbench.query({**query, 'search_fields': ['undeclared']})


def test_scoped_scalar_search_preserves_zero_and_false():
    record = {'count': 0, 'enabled': False, 'metadata': 'unrelated'}
    assert search_text(record, ['count', 'enabled']) == '0 false'
    assert 'metadata' not in search_text(record, ['count'])


def test_http_query_boundary_retains_the_declared_scope(tmp_path):
    from adaos.apps.api.resources import ResourceQueryRequest, query_resources
    bundle = _bundle()
    PrototypeResourceService(state_dir=tmp_path).materialize(bundle)
    service = ResourceWorkbenchService(state_dir=tmp_path)
    scoped = ResourceQueryRequest(resource_type=bundle['resource_definition']['resource_type'],
                                  search='planned', search_fields=['title'])
    assert query_resources(scoped, service=service)['items'] == []
    legacy = ResourceQueryRequest(resource_type=bundle['resource_definition']['resource_type'], search='planned')
    assert len(query_resources(legacy, service=service)['items']) == 1


def test_semantic_field_search_compiles_to_the_same_runtime_scope():
    brief, semantic = _multi_resource_fixture()
    view = next(row for row in semantic['views'] if row['role'] == 'collection')
    view['query_controls'] = [{'id': 'name-search', 'kind': 'search',
                              'label': _text('name-search', 'Search name', 'Поиск по имени'), 'field_ref': 'title'}]
    compiled = compile_semantic_prototype_candidate(_multi_resource_candidate(semantic), brief=brief)
    widget = next(row for row in compiled['webui']['ui']['application']['desktop']['pageSchema']['widgets']
                  if row['id'] == view['id'])
    assert widget['dataSource']['query']['search_fields'] == ['title']
    schema = compiled['webui']['ui']['application']['desktop']['pageSchema']['meta']['builder']['prototype_record_schemas'][widget['dataSource']['resourceType']]
    assert 'title' in schema['properties']
    assert widget['dataSource']['query']['search'].startswith('$state.query_')
