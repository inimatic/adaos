import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.e2e.component_contract_conformance import CollectionEditorReference, load_slice
from adaos.services.builder.semantic_graph import reduce_graph
from adaos.services.builder.workflow import BuilderWorkflowError

ROOT = Path(__file__).resolve().parents[1]


def operation():
    return {'seq': 1, 'kind': 'view.scope.value.set', 'ref': 'view:records-list',
        'field_ref': 'team_id', 'expected': 'team-1', 'value': 'team-2'}


def test_permanent_scope_increment_and_inverse_preserve_every_other_source_field():
    document, spec = load_slice(ROOT)
    result = reduce_graph(document, [operation()], write_refs=['view:records-list'])
    expected = copy.deepcopy(document)
    next(view for view in expected['views'] if view['id'] == 'records-list')['scope_filters'][0]['value'] = 'team-2'
    assert result['semantic_document'] == expected
    oracle = CollectionEditorReference(copy.deepcopy(expected['resources'][0]['records']), {**spec['scope'], 'value': 'team-2'})
    assert oracle.visible_ids() == ['record-2']
    oracle.search = 'not found'
    assert oracle.visible_ids() == []
    oracle.reset_query()
    assert oracle.visible_ids() == ['record-2']
    restored = reduce_graph(expected, result['inverse_ops'], write_refs=['view:records-list'])
    assert restored['semantic_document'] == document
    assert result['write_set'][0]['property'] == 'scope_filters.team_id.value'


def test_numeric_scope_uses_json_number_semantics_but_never_boolean_coercion():
    from adaos.services.builder.semantic_graph import scope_value_equal
    assert scope_value_equal(2, 2.0)
    assert not scope_value_equal(1, True) and not scope_value_equal(0.0, False)
    document, _ = load_slice(ROOT)
    resource = document['resources'][0]
    resource['fields'].append({'id': 'batch', 'label': {'key': 'batch', 'en': 'Batch', 'ru': 'Партия'},
        'value_type': 'number', 'required': True, 'editable': True})
    for index, row in enumerate(resource['records'], 1):
        row['batch'] = index
    view = next(view for view in document['views'] if view['id'] == 'records-list')
    view['scope_filters'] = [{'field_ref': 'batch', 'value': 1}]
    op = {**operation(), 'field_ref': 'batch', 'expected': 1, 'value': 2.0}
    result = reduce_graph(document, [op], write_refs=['view:records-list'])
    assert next(v for v in result['semantic_document']['views'] if v['id'] == 'records-list')['scope_filters'][0]['value'] == 2
    assert reduce_graph(result['semantic_document'], result['inverse_ops'], write_refs=['view:records-list'])['semantic_document'] == document


@pytest.mark.parametrize('change', [{'expected': 'stale'}, {'field_ref': 'name'}, {'ref': 'view:record-editor'},
    {'value': ''}, {'value': 'unknown-team'}, {'value': True}, {'scope_filters': []}])
def test_scope_cannot_be_widened_retargeted_or_rebased(change):
    document, _ = load_slice(ROOT)
    with pytest.raises(BuilderWorkflowError):
        reduce_graph(document, [{**operation(), **change}], write_refs=['view:records-list', 'view:record-editor'])


def test_inspector_scope_mask_is_schema_valid_without_disclosing_records():
    from adaos.services.builder.semantic_inspector import semantic_refs
    document, _ = load_slice(ROOT)
    nodes = semantic_refs(document)
    node = next(item for item in nodes if item['ref'] == 'view:records-list')
    assert 'scope_filters.team_id.value' in node['supported_writes']
    schema = json.loads((ROOT / 'src/adaos/abi/builder.semantic_inspector.v1.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/node'}).validate(node)
    assert 'records' not in node and 'values' not in node


@pytest.mark.parametrize('with_options', [True, False])
def test_provider_decoder_exposes_only_exact_admitted_scope_pair(with_options):
    from adaos.services.builder.semantic_proposer import provider_contract
    packet = {'operation_kinds': ['view.scope.value.set'], 'max_ops': 1,
        'write_masks': [{'ref': 'view:records-list', 'properties': ['scope_filters.team_id.value']}],
        'context': {'targets': [{'ref': 'view:records-list', 'values': {'scope_filters.team_id.value': 'team-1'}}]}}
    if with_options:
        packet['context']['targets'][0]['allowed_values'] = {'scope_filters.team_id.value': ['team-1', 'team-2']}
    contract = provider_contract(packet)
    variants = contract['properties']['ops']['items']['anyOf']
    assert len(variants) == 1
    props = variants[0]['properties']
    assert props['ref']['enum'] == ['view:records-list']
    assert props['field_ref']['enum'] == ['team_id'] and props['value'] == {'type': 'string', **({'enum': ['team-1', 'team-2']} if with_options else {})}
