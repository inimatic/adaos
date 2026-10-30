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
    task, brief = admit_interpretation(task, proposal, intent)
    plan = compile_generation_plan(task, model='exact-model', transport=transport, retrieval=retrieval)
    assert len(plan['stages']) == 1 and plan['retrieval'] == retrieval
    from adaos.sdk.builder import prototype
    messages = [{'role': 'user', 'content': json.dumps({'stable_builder_context': {
        'semantic_invariants': prototype.semantic_generation_guidance()}})},
        {'role': 'user', 'content': json.dumps({'builder_request': {'instruction': intent['statement'],
            'task_spec_ref': task['task_spec_id'], 'generation_plan_ref': plan['generation_plan_id'],
            'prototype_brief': prototype.model_context(brief, compact=True)}})}]
    text = {'format': {'type': 'json_schema', 'strict': True, 'schema': prototype.semantic_provider_contract(version='v2', brief=brief)}}
    receipt = admit_generation_input(messages, model='exact-model', text=text, transport=transport, plan=plan)
    assert receipt['generation_plan_ref'] == plan['generation_plan_id']
    with pytest.raises(GenerationInputRejected, match='profile_conflict'):
        admit_generation_input([], model='other-model', transport=transport, plan=plan)
    with pytest.raises(GenerationInputRejected, match='transport_conflict'):
        admit_generation_input([], model='exact-model', transport='personal' if transport == 'root' else 'root', plan=plan)


@pytest.mark.parametrize('fault,code', [('source', 'source_conflict'), ('task', 'task_identity_conflict'),
    ('acceptance', 'acceptance_conflict'), ('baseline', 'baseline_conflict'), ('schema', 'output_contract_conflict'),
    ('guidance', 'platform_dependency_missing'), ('modified_guidance', 'platform_fact_conflict')])
def test_plan_bound_generation_rejects_changed_or_missing_wire_facts(fault, code):
    from adaos.sdk.builder import prototype
    intent, task, proposal = prepared()
    task, brief = admit_interpretation(task, proposal, intent)
    plan = compile_generation_plan(task, model='exact', transport='personal')
    stable = {'semantic_invariants': prototype.semantic_generation_guidance()}
    request = {'instruction': intent['statement'], 'task_spec_ref': task['task_spec_id'],
        'generation_plan_ref': plan['generation_plan_id'], 'prototype_brief': prototype.model_context(brief, compact=True)}
    text = {'format': {'type': 'json_schema', 'strict': True, 'schema': prototype.semantic_provider_contract(version='v2', brief=brief)}}
    if fault == 'source': request['instruction'] = 'Other instruction'
    elif fault == 'task': request['task_spec_ref'] = 'task:other'
    elif fault == 'acceptance': request['prototype_brief']['task_acceptance']['facts'][0]['value'] = 0
    elif fault == 'baseline': request['current_semantic'] = {'different': 'base'}
    elif fault == 'schema': text['format']['strict'] = False
    elif fault == 'guidance': del stable['semantic_invariants']['command_ownership']
    elif fault == 'modified_guidance': stable['semantic_invariants']['command_ownership'] = 'invented'
    with pytest.raises(GenerationInputRejected, match=code):
        admit_generation_input([{'content': json.dumps({'stable_builder_context': stable})},
            {'content': json.dumps({'builder_request': request})}], model='exact', text=text, transport='personal', plan=plan)


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


@pytest.mark.parametrize('retrieval,strategy', [('inline', 'task-aware'), ('core_prefetch', 'full'), ('inline', 'core-prefetch')])
def test_generation_plan_cannot_claim_a_different_context_delivery_than_it_performs(retrieval, strategy):
    intent, task, proposal = prepared()
    task, _ = admit_interpretation(task, proposal, intent)
    with pytest.raises(ValueError, match='retrieval mode'):
        compile_generation_plan(task, model='selected', transport='personal', retrieval=retrieval, context_strategy=strategy)


def test_native_adaptive_and_prefetch_modes_keep_mandatory_facts_without_tools():
    from adaos.sdk.builder import prototype
    guidance = prototype.semantic_generation_guidance()
    messages = [{'role': 'system', 'content': 'policy'},
        {'role': 'user', 'content': json.dumps({'stable_builder_context': {'semantic_invariants': guidance}})},
        {'role': 'user', 'content': json.dumps({'builder_request': {'instruction': 'Search and edit server names.',
            'prototype_brief': {'required_references': []}}})}]
    for strategy in ('adaptive', 'core-prefetch'):
        projected, receipt = prototype.compile_generation_messages(messages, strategy=strategy)
        stable = json.loads(projected[1]['content'])['stable_builder_context']
        for key in ('authoring_constraints', 'command_ownership', 'field_validation', 'state_rules', 'query_filters'):
            assert stable['semantic_invariants'][key] == guidance[key]
        assert 'prototype_retrieval' not in stable
        assert admit_generation_input(projected, model='selected', transport='personal', tools=[])['status'] == 'admitted'
        if strategy == 'core-prefetch':
            assert receipt['prefetch_status'] == 'resolved' and receipt['prefetched']
