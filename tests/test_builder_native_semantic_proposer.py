import json

import pytest

from test_builder_semantic_label_requests import native_label_builder  # noqa: F401
from test_builder_semantic_inspector import graph_project  # noqa: F401
from adaos.services.builder.semantic_admission import SemanticAdmissionService, PROPOSAL_SCHEMA
from adaos.services.builder.semantic_proposer import SemanticProposer
from adaos.services.builder.semantic_graph import guard_values, guard_condition


@pytest.mark.parametrize('mode', ['bounded_ops', 'full_document'])
def test_native_guard_policy_authoring_preserves_every_other_obligation(native_proposer, monkeypatch, mode):
    module, workflow, root, document = native_proposer
    admission = SemanticAdmissionService.from_context()
    before = guard_condition(document, 'command:complete')['value']
    value = next(item for item in guard_values(document, 'command:complete') if item != before)
    candidate = json.loads(json.dumps(document))
    guard_condition(candidate, 'command:complete')['value'] = value
    output = ({'schema': PROPOSAL_SCHEMA, 'status': 'proposed', 'message': '', 'ops': [
        {'kind': 'command.guard.value.set', 'ref': 'command:complete', 'value': value}]} if mode == 'bounded_ops' else
        {'status': 'proposed', 'message': '', 'semantic_document': candidate})
    proposer = SemanticProposer(admission, submit=lambda **kw: {'job_id': 'native.guard.job'},
        wait=lambda *a, **kw: {'job_id': 'native.guard.job', 'status': 'succeeded', 'output_text': json.dumps(output)})
    monkeypatch.setattr(module.sdk_builder_semantic_ui, 'propose', proposer.propose)
    result = module.chat(text='Change only the complete guard value', webspace_id='builder-host', _meta={
        'source_message_id': 'native.guard.user', 'builder_semantic_edit_mode': mode,
        'builder_semantic_write_ref': 'command:complete', 'builder_semantic_write_property': 'guard.when.value',
        'builder_llm_model': 'gpt-5'})
    assert result['status'] == 'semantic_proposal_applied', result
    assert json.loads((root/'semantic.webui.json').read_text()) == candidate


@pytest.fixture
def native_proposer(native_label_builder, monkeypatch):
    module, workflow, root, document = native_label_builder
    monkeypatch.setattr(module, '_resolve_builder_context_for_turn', lambda *a: {'builder_webspace_id': 'builder-host'})
    monkeypatch.setattr(module, '_parse_builder_command', lambda *a, **kw: {'intent': ''})
    monkeypatch.setattr(module, '_requested_scenario_id_from_meta', lambda *a: '')
    monkeypatch.setattr(module, '_project_external_user_turn', lambda *a, **kw: None)
    monkeypatch.setattr(module, '_route_automation_chat', lambda **kw: None)
    monkeypatch.setattr(module, '_safe_emit_chat', lambda *a, **kw: None)
    monkeypatch.setattr(module, '_dialog_state', lambda *a, **kw: {})
    return module, workflow, root, document


@pytest.mark.parametrize('mode', ['bounded_ops', 'full_document'])
@pytest.mark.parametrize('scope_style', ['sdk', 'ui'])
def test_explicit_model_route_commits_through_core_and_records_billing(native_proposer, monkeypatch, mode, scope_style):
    module, workflow, root, document = native_proposer
    admission = SemanticAdmissionService.from_context()
    calls = []
    proposal = {'schema': PROPOSAL_SCHEMA, 'status': 'proposed', 'message': '',
        'ops': [{'kind': 'label.set', 'ref': 'view:work-list', 'locale': 'en', 'text': 'Work queue'}]}
    candidate = json.loads(json.dumps(document))
    next(view for view in candidate['views'] if view['id'] == 'work-list')['title']['en'] = 'Work queue'
    output = proposal if mode == 'bounded_ops' else {'status': 'proposed', 'message': '', 'semantic_document': candidate}
    def submit(**kwargs):
        calls.append(kwargs)
        return {'job_id': 'native.model.job'}
    proposer = SemanticProposer(admission, submit=submit, wait=lambda *a, **kw: {
        'job_id': 'native.model.job', 'status': 'succeeded', 'output_text': json.dumps(output),
        'telemetry': {'usage': {'input_tokens': 600, 'output_tokens': 100},
            'cost': {'basis': 'estimate', 'estimated_usd': 0.003}}})
    monkeypatch.setattr(module.sdk_builder_semantic_ui, 'propose', proposer.propose)
    scope = ({'builder_semantic_write_refs': ['view:work-list'],
              'builder_semantic_write_properties': {'view:work-list': ['title.en']}} if scope_style == 'sdk' else
             {'builder_semantic_write_ref': 'view:work-list', 'builder_semantic_write_property': 'title.en'})
    result = module.chat(text='Rename view "Work" to "Work queue" in English', webspace_id='builder-host',
        _meta={'source_message_id': 'native.model.user', 'builder_semantic_edit_mode': mode,
               **scope, 'builder_llm_model': 'gpt-5'})
    assert result['status'] == 'semantic_proposal_applied', result
    assert result['route_class'] == 'D1' and len(calls) == 1
    packet = json.loads(calls[0]['messages'][1]['content'])
    assert packet['admitted_scope']['write_masks'] == [{'ref': 'view:work-list', 'properties': ['title.en']}]
    assert result['execution_metrics']['cost_usd'] == 0.003
    full = workflow.describe('scenario', root.name)
    assert 'native.model.user' in full['change_set']['source_message_ids']
    assert full['change']['runs'][-1]['execution_metrics']['input_tokens'] == 600
    model_runs = [row for row in full['change']['runs'] if row['execution_metrics'] and row['execution_metrics'].get('llm_calls') == 1]
    assert len(model_runs) == 1 and model_runs[0]['activity'] == 'prototype_revision_recorded'
    assert json.loads((root / 'semantic.webui.json').read_text(encoding='utf-8'))['resources'] == document['resources']


def test_model_failure_never_falls_back_to_full_generation(native_proposer, monkeypatch):
    module, workflow, root, _document = native_proposer
    before = (root / 'semantic.webui.json').read_bytes()
    def fail(**kwargs):
        raise TimeoutError('Root observer timeout')
    monkeypatch.setattr(module.sdk_builder_semantic_ui, 'propose', fail)
    result = module.chat(text='Use a clearer heading', webspace_id='builder-host', _meta={
        'source_message_id': 'native.timeout.user', 'builder_semantic_edit_mode': 'bounded_ops',
        'builder_semantic_write_refs': ['view:work-list'], 'builder_llm_model': 'gpt-5'})
    assert result['status'] == 'semantic_proposal_rejected'
    assert (root / 'semantic.webui.json').read_bytes() == before
    assert workflow.current_prototype_revision('scenario', root.name) == '001'


def test_bounded_ui_requires_explicit_scope_before_intake_or_paid_call(native_proposer, monkeypatch):
    module, workflow, root, _document = native_proposer
    before = (root / 'semantic.webui.json').read_bytes()
    generation = workflow.describe('scenario', root.name)['generation']
    monkeypatch.setattr(module.sdk_builder_semantic_ui, 'propose', lambda **kw: pytest.fail('Missing scope must never bill'))
    result = module.chat(text='Use a clearer heading', webspace_id='builder-host', _meta={
        'source_message_id': 'native.missing.scope', 'builder_semantic_edit_mode': 'bounded_ops',
        'builder_semantic_write_ref': '', 'builder_semantic_write_property': 'title.en'})
    assert result['status'] == 'semantic_proposal_rejected' and result['model_calls'] == 0
    assert (root / 'semantic.webui.json').read_bytes() == before
    assert workflow.describe('scenario', root.name)['generation'] == generation
