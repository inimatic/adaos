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


def test_primitive_union_delivery_preserves_the_complete_interpretation_language(monkeypatch):
    _,task,proposal=prepared()
    for row in proposal['requirements']:
        row['statement']=next(c['text'].strip() for c in task['clauses'] if c['id']==row['clause_ids'][0])
    monkeypatch.setenv('ADAOS_BUILDER_INTERPRETATION_SCHEMA_DELIVERY','references')
    original=interpretation_contract(provider=True,task=task)
    before=copy.deepcopy(original)
    monkeypatch.setenv('ADAOS_BUILDER_INTERPRETATION_SCHEMA_DELIVERY','primitive_unions')
    projected=interpretation_contract(provider=True,task=task)
    assert original==before and projected['$defs']['scalar']=={'type':['string','number','boolean','null']}
    original_validator,projected_validator=Draft202012Validator(original),Draft202012Validator(projected)
    assert original_validator.is_valid(proposal) and projected_validator.is_valid(proposal)
    assert projected['$defs']['requirement']==original['$defs']['requirement']
    for value in ('text',True,False,None,0,1.5,[],{},['text']):
        candidate=copy.deepcopy(proposal)
        candidate['facts'][0]['value']=value
        assert original_validator.is_valid(candidate)==projected_validator.is_valid(candidate)
        scalar=Draft202012Validator(projected['$defs']['scalar'])
        assert scalar.is_valid(value)==(not isinstance(value,(list,dict)))


@pytest.mark.parametrize('schema',[
    {'type':'string','anyOf':[{'type':'string'},{'type':'number'}]},
    {'anyOf':[{'type':'string','minLength':3},{'type':'null'}]},
    {'anyOf':[{'type':'string'},{'type':'object','properties':{}}]},
    {'anyOf':[{'type':'boolean','const':True},{'type':'null'}]},
    {'const':{'anyOf':[{'type':'string'},{'type':'boolean'}]}},
])
def test_primitive_union_projection_cannot_drop_constraints_or_interpret_literal_data(schema):
    from adaos.services.builder.provider_schema import coalesce_primitive_unions
    assert coalesce_primitive_unions(schema)==schema


def test_union_projection_is_bounded_and_its_refusal_is_owned_before_submission(monkeypatch):
    from adaos.services.builder import provider_schema
    nested={'type':'string'}
    for _ in range(140):nested={'type':'array','items':nested}
    with pytest.raises(SchemaProjectionError):provider_schema.coalesce_primitive_unions(nested)
    def refuse(_):raise SchemaProjectionError('Bounded projection refuses')
    monkeypatch.setattr(provider_schema,'coalesce_primitive_unions',refuse)
    monkeypatch.setenv('ADAOS_BUILDER_INTERPRETATION_SCHEMA_DELIVERY','primitive_unions')
    with pytest.raises(TaskInterpretationRejected) as caught:interpretation_contract(provider=True)
    assert caught.value.findings[0]['gate']=='G5' and caught.value.findings[0]['owner']=='context_compilation'
