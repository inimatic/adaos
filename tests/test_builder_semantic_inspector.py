from __future__ import annotations

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from adaos.sdk.builder import semantic_ui as sdk
from adaos.services.builder import semantic_graph as graph
from adaos.services.builder.semantic_edit_requests import parse_request, plan
from adaos.services.builder.semantic_inspector import semantic_refs
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from adaos.services.builder.semantic_ui import _operation_root
from adaos.services.builder.workflow import BuilderWorkflowError
from test_builder_semantic_graph import make_graph_project
from test_builder_semantic_label_requests import native_label_builder as _native_label_builder  # noqa: F401


@pytest.fixture
def graph_project(tmp_path):
    service, workflow, root, original = make_graph_project(tmp_path)
    document = copy.deepcopy(original)
    next(item for item in document['views'] if item['id'] == 'work-list')['presentation'] = 'table'
    compiled = compile_semantic_prototype(document, project_ref='project:semantic_labels')
    for name, value in {'semantic.webui.json': compiled['semantic_document'], 'webui.json': compiled['webui'],
                        **{f'assets/i18n/{locale}.json': values for locale, values in compiled['locale_dictionaries'].items()}}.items():
        (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return service, workflow, root, compiled['semantic_document']


def table(document):
    return next(view for view in document['views'] if graph.supports_field_order(document, view))


def reorder(document):
    view = table(document)
    return {'seq': 1, 'kind': 'view.fields.reorder', 'ref': f"view:{view['id']}",
            'expected': list(view['field_refs']), 'field_refs': list(reversed(view['field_refs']))}


def test_order_replay_only_changes_one_view_field_order(graph_project):
    _service, _workflow, _root, document = graph_project
    op = reorder(document)
    result = sdk.replay(document, [op], write_refs=[op['ref']])
    expected = copy.deepcopy(document)
    table(expected)['field_refs'] = op['field_refs']
    assert result['semantic_document'] == expected
    assert sdk.replay(expected, result['inverse_ops'], write_refs=[op['ref']])['semantic_document'] == document
    assert result['write_set'] == [{'seq': 1, 'kind': op['kind'], 'ref': op['ref'], 'property': 'field_refs',
                                    'before': op['expected'], 'after': op['field_refs']}]
    before = compile_semantic_prototype(document, project_ref='project:semantic_labels')
    after = compile_semantic_prototype(expected, project_ref='project:semantic_labels')
    before_widgets = before['webui']['ui']['application']['desktop']['pageSchema']['widgets']
    after_widgets = after['webui']['ui']['application']['desktop']['pageSchema']['widgets']
    changed = [item for item, previous in zip(after_widgets, before_widgets) if item != previous]
    assert len(changed) == 1
    assert changed[0]['id'] == table(document)['id']
    assert before['locale_dictionaries'] == after['locale_dictionaries']


@pytest.mark.parametrize('change', [
    {'expected': ['unknown', 'also_unknown']}, {'field_refs': ['unknown', 'also_unknown']},
    {'field_refs': ['name', 'name']}, {'field_refs': ['name']}, {'ref': 'view:absent'},
    {'ref': 'command:complete'}, {'extra': True}, {'seq': 2}, {'kind': 'view.fields.remove'},
])
def test_order_rejects_stale_unsupported_or_shape_changes(graph_project, change):
    _service, _workflow, _root, document = graph_project
    op = {**reorder(document), **change}
    with pytest.raises(BuilderWorkflowError):
        sdk.replay(document, [op], write_refs=[op['ref']])


def test_order_rejects_wrong_write_ref_and_non_table(graph_project):
    _service, _workflow, _root, document = graph_project
    op = reorder(document)
    with pytest.raises(BuilderWorkflowError, match='write scope'):
        sdk.replay(document, [op], write_refs=['view:another'])
    modified = copy.deepcopy(document)
    table(modified)['role'] = 'details'
    with pytest.raises(BuilderWorkflowError, match='table collection'):
        sdk.replay(modified, [op], write_refs=[op['ref']])


def test_lookup_target_reorder_requires_explicit_stable_display_fields(graph_project):
    _service, _workflow, _root, document = graph_project
    view = next(item for item in document['views'] if item['id'] == 'people-list')
    op = {'seq': 1, 'kind': 'view.fields.reorder', 'ref': 'view:people-list',
          'expected': list(view['field_refs']), 'field_refs': list(reversed(view['field_refs']))}
    with pytest.raises(BuilderWorkflowError, match='relationship display'):
        sdk.replay(document, [op], write_refs=[op['ref']])
    assert 'field_refs' not in next(item for item in semantic_refs(document) if item['ref'] == op['ref'])['supported_writes']
    explicit = copy.deepcopy(document)
    explicit['relationships'][0]['label_field_refs'] = ['person_name']
    assert sdk.replay(explicit, [op], write_refs=[op['ref']])['semantic_document']['relationships'] == explicit['relationships']


def test_inspector_exposes_descriptors_not_records_or_recovery_images(graph_project):
    service, _workflow, root, document = graph_project
    before = {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    packet = service.inspect(project_ref='scenario:semantic_labels')
    assert packet['status'] == 'verified' and packet['source_revision'] == '001'
    assert packet['base_hash'] == graph.digest(document)
    assert packet['transaction'] is None and packet['write_set'] == []
    assert packet['read_scope'] == 'full_semantic_source_and_effective_compiled_ui'
    assert packet['read_refs'] == sorted(item['ref'] for item in packet['refs'])
    assert next(item for item in packet['refs'] if item['ref'] == f"view:{table(document)['id']}")['supported_writes'][-1] == 'field_refs'
    assert before == {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    assert 'before_files' not in json.dumps(packet) and 'after_files' not in json.dumps(packet)
    assert all('records' not in item for item in packet['refs'])


def test_inspector_projects_exact_committed_write_set_and_replay(graph_project):
    service, workflow, root, document = graph_project
    op = reorder(document)
    request = {'schema': graph.SCHEMA, 'project_ref': 'scenario:semantic_labels', 'change_id': 'labels-change',
               'transaction_id': 'column-order-1', 'source_revision': '001', 'base_hash': graph.digest(document),
               'expected_generation': workflow.describe('scenario', root.name)['generation'],
               'write_refs': [op['ref']], 'ops': [op]}
    result = service.apply(request)
    assert service.apply(request)['replayed'] is True
    packet = service.inspect(project_ref=request['project_ref'])
    assert packet['status'] == 'verified', packet
    assert packet['transaction']['source_revision'] == '001'
    assert packet['transaction']['revision'] == result['revision'] == packet['source_revision']
    assert packet['transaction']['base_hash'] == request['base_hash'] != packet['base_hash']
    assert packet['base_hash'] == result['semantic_digest']
    assert packet['write_set'][0]['property'] == 'field_refs'
    assert packet['admitted_write_refs'] == [op['ref']]
    assert 'resource:' + table(document)['resource_ref'] in packet['dependency_refs']


@pytest.mark.parametrize('drift', ['runtime', 'locale', 'locale_missing', 'receipt', 'retained_base'])
def test_inspector_fails_closed_on_source_or_receipt_drift(graph_project, drift):
    service, workflow, root, document = graph_project
    op = reorder(document)
    request = {'schema': graph.SCHEMA, 'project_ref': 'scenario:semantic_labels', 'change_id': 'labels-change',
               'transaction_id': 'column-order-1', 'source_revision': '001', 'base_hash': graph.digest(document),
               'expected_generation': workflow.describe('scenario', root.name)['generation'],
               'write_refs': [op['ref']], 'ops': [op]}
    service.apply(request)
    if drift == 'runtime':
        path = root / 'webui.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['ui']['application']['desktop']['pageSchema']['widgets'][0]['title'] = 'Drift'
    elif drift == 'locale':
        path = root / 'assets/i18n/en.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload[next(iter(payload))] = 'Drift'
    elif drift == 'locale_missing':
        path = root / 'assets/i18n/en.json'
        path.unlink()
    elif drift == 'receipt':
        path = root / 'ui_revisions/002.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['semantic_transaction']['write_refs'] = []
    else:
        path = next(_operation_root(workflow, request['project_ref']).glob('*.json'))
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['before_files']['semantic.webui.json'] = 'e30='
    if drift != 'locale_missing':
        path.write_text(json.dumps(payload), encoding='utf-8')
    packet = service.inspect(project_ref=request['project_ref'])
    assert packet['status'] == 'blocked' and packet['write_set'] == [] and packet['refs'] == []
    assert packet['ref_count'] == 0 and packet['write_count'] == 0
    if drift == 'locale_missing':
        assert packet['block_reason'] == 'locale_verification'
        assert not path.exists(), 'Inspection must not repair a missing projection'


def test_inspector_missing_source_is_read_only(graph_project):
    service, _workflow, root, _document = graph_project
    (root / 'semantic.webui.json').unlink()
    assert service.inspect(project_ref='scenario:semantic_labels')['status'] == 'source_missing'
    assert not (root / 'semantic.webui.json').exists()


@pytest.mark.parametrize('instruction', [
    'Move column "State" before "Name" in view "Records" in English',
    'Перемести колонку «Состояние» перед «Название» в таблице «Записи» на русском',
])
def test_parse_column_move_is_explicit_and_bounded(instruction):
    assert parse_request(instruction)['kind'] == 'reorder'
    assert sdk.recognizes_request(instruction)
    assert not sdk.recognizes_label_request(instruction)


def test_ambiguous_move_never_selects_a_view_or_invokes_a_model():
    document = json.loads(__import__('pathlib').Path('e2e/builder/development/semantic-increments/fixture.json').read_text(encoding='utf-8'))
    parsed = parse_request('Move column "State" before "Name" in view "Records" in English')
    planned = plan(document, parsed)
    assert planned['ops'][0]['field_refs'] == ['state', 'name', 'team_id']
    document['views'].append({**copy.deepcopy(document['views'][0]), 'id': 'duplicate'})
    assert plan(document, parsed)['reason'] == 'ambiguous_view'
    assert parse_request('Move column State')['kind'] == 'clarification'
    assert parse_request('Hide all columns') is None


def test_inspector_ref_inventory_has_unique_qualified_identity_refs(graph_project):
    *_rest, document = graph_project
    refs = semantic_refs(document)
    assert len({item['ref'] for item in refs}) == len(refs)
    assert any(item['kind'] == 'identity_field' for item in refs)


def test_native_column_move_retry_and_family_specific_undo_do_not_use_llm(_native_label_builder):  # noqa: F811
    module, workflow, root, document = _native_label_builder
    view = table(document)
    resource = next(item for item in document['resources'] if item['id'] == view['resource_ref'])
    labels = {item['id']: item['label']['en'] for item in resource['fields']}
    instruction = f'Move column "{labels[view["field_refs"][1]]}" before "{labels[view["field_refs"][0]]}" in view "{view["title"]["en"]}" in English'
    kwargs = {'instruction': instruction, 'webspace_id': 'builder-host', '_meta': {'message_id': 'move-1'}}
    result = module.update_current_scenario(**kwargs)
    assert result['status'] == 'semantic_edit_applied', result
    assert result['semantic_transaction']['write_set'][0]['property'] == 'field_refs'
    assert module.update_current_scenario(**kwargs)['semantic_transaction']['replayed'] is True
    current = (root / 'semantic.webui.json').read_bytes()
    wrong_undo = module.update_current_scenario(instruction='Undo the last label change', webspace_id='builder-host',
                                               _meta={'message_id': 'wrong-undo'})
    assert wrong_undo['status'] == 'semantic_label_rejected'
    assert (root / 'semantic.webui.json').read_bytes() == current
    undo = module.update_current_scenario(instruction='Undo the last column order change', webspace_id='builder-host',
                                         _meta={'message_id': 'undo-columns'})
    assert undo['status'] == 'semantic_edit_applied', undo
    assert json.loads((root / 'semantic.webui.json').read_text(encoding='utf-8')) == document


def test_native_column_move_obeys_the_selected_project_conversation_guard(_native_label_builder, monkeypatch):  # noqa: F811
    module, _workflow, root, _document = _native_label_builder
    def forbidden(*args, **kwargs):
        raise AssertionError('target mismatch must stop before binding lookup or source mutation')
    monkeypatch.setattr(module, '_resolve_builder_context_for_turn', forbidden)
    before = (root / 'semantic.webui.json').read_bytes()
    result = module.chat(text='Move column "State" before "Name" in view "Records" in English', _meta={
        'builder_selected_target': {'object_type': 'scenario', 'object_id': 'semantic_labels'},
        'thread_id': 'prompt-project:scenario:another-project'})
    assert result['status'] == 'builder_target_mismatch'
    assert (root / 'semantic.webui.json').read_bytes() == before


def test_inspector_packets_follow_the_versioned_abi(graph_project):
    from pathlib import Path
    service, _workflow, root, _document = graph_project
    schema = json.loads(Path('src/adaos/abi/builder.semantic_inspector.v1.schema.json').read_text(encoding='utf-8'))
    validator = Draft202012Validator(schema)
    validator.validate(service.inspect(project_ref='scenario:semantic_labels'))
    (root / 'semantic.webui.json').unlink()
    validator.validate(service.inspect(project_ref='scenario:semantic_labels'))
