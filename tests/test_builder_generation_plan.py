import copy
import json

import pytest

from adaos.services.builder.generation_plan import (
    GenerationInputRejected, admit_generation_input, compile_generation_plan, context_receipt,
)
from adaos.services.builder.task_spec import seal
from test_builder_task_interpretation import prepared
from adaos.services.builder.task_interpretation import admit_interpretation


def test_uninterpreted_source_uses_two_stages_and_cannot_claim_single_stage():
    _, task, _ = prepared()
    plan = compile_generation_plan(task, model='selected-model', transport='personal')
    assert plan['route'] == 'two_stage'
    assert [stage['name'] for stage in plan['stages']] == ['interpretation', 'generation']
    with pytest.raises(ValueError, match='admitted TaskSpec'):
        compile_generation_plan(task, model='selected-model', transport='personal', route='single_stage')
    with pytest.raises(ValueError, match='budget'):
        compile_generation_plan(task, model='selected-model', transport='personal', provider_call_limit=1)


@pytest.mark.parametrize('transport', ['root', 'personal'])
@pytest.mark.parametrize('retrieval', ['inline', 'core_prefetch', 'model_mcp'])
def test_stage_count_is_independent_of_transport_and_retrieval(transport, retrieval):
    intent, task, proposal = prepared()
    task, _ = admit_interpretation(task, proposal, intent)
    plan = compile_generation_plan(task, model='exact-model', transport=transport, retrieval=retrieval)
    assert len(plan['stages']) == 1 and plan['retrieval'] == retrieval
    receipt = admit_generation_input([{'role': 'user', 'content': 'Admitted typed task'}],
                                    model='exact-model', transport=transport, plan=plan)
    assert receipt['generation_plan_ref'] == plan['generation_plan_id']
    with pytest.raises(GenerationInputRejected, match='profile_conflict'):
        admit_generation_input([], model='other-model', transport=transport, plan=plan)
    with pytest.raises(GenerationInputRejected, match='transport_conflict'):
        admit_generation_input([], model='exact-model', transport='personal' if transport == 'root' else 'root', plan=plan)


def test_unavailable_retrieval_blocks_incomplete_input_before_submission():
    messages = [{'role': 'user', 'content': json.dumps({'stable_builder_context': {
        'semantic_invariants': {'contract': 'adaos.builder.semantic_prototype_candidate.v2'},
        'prototype_retrieval': {'dimensions': [{'id': 'command_ownership'}]}}})}]
    with pytest.raises(GenerationInputRejected, match='required_retrieval_unavailable'):
        admit_generation_input(messages, model='exact', transport='personal', tools=[], tool_choice='none')


def test_schema_and_task_inventory_must_agree_without_legacy_aliases():
    messages = [{'role': 'user', 'content': json.dumps({'stable_builder_context': {
        'semantic_invariants': {'contract': 'adaos.builder.semantic_prototype_candidate.v2'}}})},
        {'role': 'user', 'content': json.dumps({'builder_request': {'prototype_brief': {
            'required_references': [{'id': 'op:read'}]}}})}]
    text = {'format': {'type': 'json_schema', 'schema': {'$defs': {'requirementBinding': {
        'properties': {'requirement_ref': {'enum': ['op:read', 'job:read']}}}}}}}
    with pytest.raises(GenerationInputRejected, match='projection_conflict'):
        admit_generation_input(messages, model='exact', text=text, transport='root')


def test_wire_receipt_tracks_actual_units_without_copying_credentials():
    receipt = context_receipt([{'role': 'system', 'content': 'policy'},
        {'role': 'user', 'content': json.dumps({'builder_request': {'instruction': 'show'}})}],
        generation_options={'model': 'exact', 'tools': [{'authorization': 'PRIVATE'}]})
    assert receipt['tool_count'] == 1
    assert receipt['units'][1]['purpose'] == 'task'
    assert 'PRIVATE' not in json.dumps(receipt)
