"""Independent expected effects against Prototype and real production CRUD."""
import copy
from pathlib import Path
import pytest
from adaos.e2e.component_contract_conformance import load_slice
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from adaos.services.builder.production_resources import lower_local_resource
from adaos.services.resources.prototype import PrototypeResourceService
from adaos.services.resources.local import LocalCrudResourceService
from adaos.services.resources.workbench import ResourceWorkbenchService
from adaos.sdk.developer.prototypes import derive_resource_specs

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('provider_kind', ['prototype', 'production'])
@pytest.mark.parametrize('application', ['task_queue', 'warehouse_stock'])
@pytest.mark.parametrize('guard_version', ['original', 'changed'])
@pytest.mark.parametrize('episode', ['success', 'guard', 'unknown_command', 'extra_effect', 'fixed_effect', 'identity', 'stale_revision', 'authority', 'state_locked', 'binding_unavailable', 'draft_guard', 'draft_effect_spoof', 'draft_unknown', 'draft_type', 'draft_no_write'])
def test_real_production_substitution_preserves_command_effects_and_rejections(tmp_path, provider_kind, episode, application, guard_version):
    import json
    if application == 'task_queue':
        document, _ = load_slice(ROOT)
        state_field, note_field, input_field, before_state, after_state, command_id, record_id = 'state', 'review_note', 'name', 'open', 'done', 'complete', 'record-1'
    else:
        document = json.loads((ROOT/'e2e/builder/development/contract-conformance/inventory-fixture.json').read_text(encoding='utf-8'))
        state_field, note_field, input_field, before_state, after_state, command_id, record_id = 'status', 'reserve_note', 'sku', 'available', 'reserved', 'stock-reserve', 'stock-1'
    if guard_version == 'changed':
        next(item for item in document['commands'] if item['id'] == command_id)['guard']['when']['value'] = after_state
    if episode == 'state_locked':
        document['resources'][0]['read_only_when'] = {'field_ref': state_field, 'operator': 'equals', 'value': before_state}
    compiled = compile_semantic_prototype(document, project_ref='project:production-interoperability')
    specs = derive_resource_specs(compiled['webui'], compiled['prototype_resources'])
    provider = PrototypeResourceService(tmp_path) if provider_kind == 'prototype' else LocalCrudResourceService(tmp_path)
    mapping = {spec['resource_definition']['resource_type']: 'skill.contract_provider.'+str(index)
        for index, spec in enumerate(specs)}
    for index, spec in enumerate(specs):
        if provider_kind == 'prototype':
            provider.materialize({'schema': 'adaos.builder.prototype_resource.v1', 'project_ref': 'project:production-interoperability',
                'change_id': 'change-qualified', 'revision': '001', 'webui_digest': compiled['semantic_digest'], **spec})
        else:
            bundle = lower_local_resource(spec['resource_definition'], owner_skill='contract_provider',
                resource_type=mapping[spec['resource_definition']['resource_type']], resource_map=mapping)
            assert bundle['seed'] == []  # production never inherits prototype data
            # These are explicitly owned synthetic qualification fixtures.
            bundle['seed'] = copy.deepcopy(compiled['prototype_resources'][index]['records'])
            for row in bundle['seed']:
                row.setdefault('revision', 1)
            provider.materialize(bundle)
    resource_type = specs[0]['resource_definition']['resource_type']
    if provider_kind == 'production': resource_type = mapping[resource_type]
    def records(): return provider.query(resource_type, filters={}, search='', sort=[], limit=None)
    before = records()
    payload = {state_field: after_state, note_field: 'Qualified note'}
    request = {'schema': 'adaos.resource.operation.v1', 'resource_type': resource_type, 'operation_id': 'update',
        'record_id': record_id, 'payload': {'patch': payload}, 'actor': {'id': 'owner', 'role': 'owner'},
        'context': {'command_id': command_id, 'prototype_command_id': command_id}}
    if episode == 'guard': payload[note_field] = ''
    if episode == 'unknown_command': request['context'] = {}
    if episode == 'extra_effect': payload[input_field] = 'Unadmitted change'
    if episode == 'fixed_effect': payload[state_field] = before_state
    if episode == 'identity': request['record_id'] = 'unknown-record'
    if episode == 'stale_revision': request['expected_revision'] = 'stale'
    if episode == 'authority': request['actor']['role'] = 'guest'
    if episode == 'binding_unavailable': request['resource_type'] += '.unavailable'
    if episode == 'state_locked': request['context']['guard_draft'] = {state_field: after_state}
    if episode == 'draft_guard':
        payload[note_field] = ''
        request['context']['guard_draft'] = {state_field: after_state}
    if episode == 'draft_effect_spoof':
        payload[note_field] = ''
        request['context']['guard_draft'] = {state_field: after_state if guard_version == 'changed' else before_state, note_field: 'Spoofed'}
    if episode == 'draft_unknown': request['context']['guard_draft'] = {'unknown': 'unadmitted'}
    if episode == 'draft_type': request['context']['guard_draft'] = {state_field: True}
    if episode == 'draft_no_write': request['context']['guard_draft'] = {input_field: 'Draft only', state_field: before_state}
    workbench = ResourceWorkbenchService(state_dir=tmp_path)
    if episode in {'success', 'draft_no_write'} or episode == 'guard' and guard_version == 'changed' or episode == 'draft_guard' and guard_version == 'original':
        result = workbench.operate(request)
        assert result['ok']
        expected = copy.deepcopy(before)
        expected[0].update(payload)
        if 'revision' in expected[0]: expected[0]['revision'] += 1
        assert records() == expected
    else:
        with pytest.raises((ValueError, PermissionError, KeyError)):
            workbench.operate(request)
        assert records() == before
