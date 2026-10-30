import copy
import json

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.provider_schema import inline_local_references, SchemaProjectionError
from adaos.services.builder.task_interpretation import interpretation_contract, TaskInterpretationRejected
from test_builder_task_interpretation import prepared


def test_inline_delivery_preserves_grammar_assertions_and_input_bytes(monkeypatch):
    _, task, proposal = prepared()
    for row in proposal['requirements']:
        row['statement'] = next(c['text'].strip() for c in task['clauses'] if c['id'] == row['clause_ids'][0])
    monkeypatch.setenv('ADAOS_BUILDER_INTERPRETATION_SCHEMA_DELIVERY', 'references')
    original = interpretation_contract(provider=True, task=task)
    before = copy.deepcopy(original)
    monkeypatch.setenv('ADAOS_BUILDER_INTERPRETATION_SCHEMA_DELIVERY', 'inline_refs')
    expanded = interpretation_contract(provider=True, task=task)
    assert original == before and '$defs' not in expanded and '"$ref"' not in json.dumps(expanded)
    old, new = Draft202012Validator(original), Draft202012Validator(expanded)
    assert old.is_valid(proposal) and new.is_valid(proposal)
    mutations = [proposal]
    for mutate in (lambda p: p.update(source_digest='wrong'),
                   lambda p: p['requirements'][0].update(operation=None),
                   lambda p: p['requirements'][0].update(kind='invented'),
                   lambda p: p['facts'][0].update(value={'invented':True}),
                   lambda p: p['clause_roles'][0].update(clause_id='foreign')):
        variant = copy.deepcopy(proposal); mutate(variant); mutations.append(variant)
    for variant in mutations:
        assert old.is_valid(variant) == new.is_valid(variant)
    assert not any(new.is_valid(variant) for variant in mutations[1:])


@pytest.mark.parametrize('schema', [
    {'$ref':'https://example.invalid/schema'},
    {'$ref':'#/$defs/absent'},
    {'$defs':{'cycle':{'$ref':'#/$defs/cycle'}},'$ref':'#/$defs/cycle'},
    {'$defs':{'value':{'type':'string'}},'$ref':'#/$defs/value','maxLength':3},
])
def test_unsupported_reference_projection_refuses_without_io(schema):
    with pytest.raises(SchemaProjectionError): inline_local_references(schema)


def test_literal_dictionaries_are_not_treated_as_schema_instructions():
    literal={'$ref':'https://private.invalid','properties':{'secret':'not a schema'}}
    assert inline_local_references({'const':literal}) == {'const':literal}


def test_delivery_profile_is_explicit_and_does_not_change_canonical_abi(monkeypatch):
    canonical=interpretation_contract()
    monkeypatch.setenv('ADAOS_BUILDER_INTERPRETATION_SCHEMA_DELIVERY','typo')
    assert interpretation_contract()==canonical
    with pytest.raises(TaskInterpretationRejected) as caught: interpretation_contract(provider=True)
    assert caught.value.findings[0]['gate']=='G5'
