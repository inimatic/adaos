import json

import pytest

from adaos.services.builder.semantic_admission import SemanticAdmissionService, PROPOSAL_SCHEMA
from adaos.services.builder.semantic_proposer import SemanticProposer, provider_contract
from adaos.services.builder.workflow import BuilderWorkflowError
from test_builder_semantic_inspector import graph_project  # noqa: F401


def setup_proposer(graph_project, *, output=None, wait=None):
    admission = SemanticAdmissionService(graph_project[0])
    packet = admission.begin(project_ref='scenario:semantic_labels', instruction='State before name',
        source_message_id='model.user.message', write_refs=['view:work-list'])
    view = next(row for row in graph_project[3]['views'] if row['id'] == 'work-list')
    output = output or {'schema': PROPOSAL_SCHEMA, 'status': 'proposed', 'message': '',
        'ops': [{'kind': 'view.fields.reorder', 'ref': 'view:work-list', 'field_refs': list(reversed(view['field_refs']))}]}
    calls = []
    def submit(**kwargs):
        calls.append(kwargs)
        return {'job_id': 'job.test', '_client': {'base_url': 'https://root.example'}}
    def completed(*_args, **_kwargs):
        return {'status': 'succeeded', 'job_id': 'job.test', 'output_text': json.dumps(output),
            'telemetry': {'root_job_id': 'job.test', 'provider': {'model': 'gpt-5-2025-08-07'},
                'usage': {'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 0, 'reasoning_tokens': 10},
                'cost': {'basis': 'estimate', 'estimated_usd': 0.0002}}}
    return SemanticProposer(admission, submit=submit, wait=wait or completed), packet, calls, completed


def run(proposer, packet, **kwargs):
    return proposer.propose(project_ref=packet['project_ref'], admission_id=packet['admission_id'], model='gpt-5', **kwargs)


def test_proposal_uses_strict_typed_output_and_read_dependencies(graph_project):
    proposer, packet, calls, _completed = setup_proposer(graph_project)
    result = run(proposer, packet)
    assert result['status'] == 'planned' and result['model_calls'] == 1
    assert calls[0]['reasoning'] == {'effort': 'high'}
    assert calls[0]['text']['format']['strict']
    assert calls[0]['tools'] == [] and calls[0]['tool_choice'] == 'none'
    user = json.loads(calls[0]['messages'][1]['content'])
    assert user['context']['dependencies']
    assert 'records' not in user['context'] and 'semantic_document' not in user['context']
    assert result['execution_metrics']['output_tokens'] == 20
    assert result['execution_metrics']['reasoning_tokens'] == 10
    assert result['execution_metrics']['cost_usd'] == 0.0002
    assert graph_project[1].current_prototype_revision('scenario', 'semantic_labels') == '001'


def test_same_request_reuses_retained_proposal_and_never_resubmits(graph_project):
    proposer, packet, calls, _completed = setup_proposer(graph_project)
    first = run(proposer, packet)
    assert run(proposer, packet) == first and len(calls) == 1
    with pytest.raises(BuilderWorkflowError, match='model/profile/context'):
        run(proposer, packet, reasoning_effort='low')
    receipt = graph_project[0].apply(first['request'], execution_metrics=first['execution_metrics'])
    assert receipt['model_calls'] == 1
    full = graph_project[1].describe('scenario', 'semantic_labels')
    assert full['change']['runs'][-1]['execution_metrics']['cost_usd'] == 0.0002


def test_observer_timeout_resumes_the_same_root_job(graph_project):
    waits = []
    def wait(*args, **kwargs):
        waits.append(args[0])
        if len(waits) == 1:
            raise TimeoutError('observer stopped; Root job remains active')
        return completed(*args, **kwargs)
    proposer, packet, calls, completed = setup_proposer(graph_project, wait=wait)
    with pytest.raises(TimeoutError):
        run(proposer, packet)
    assert run(proposer, packet)['status'] == 'planned'
    assert len(calls) == 1 and waits == ['job.test', 'job.test']


def test_drift_is_rejected_before_any_paid_submission(graph_project):
    proposer, packet, calls, _completed = setup_proposer(graph_project)
    (graph_project[2] / 'assets/i18n/en.json').write_text('{}', encoding='utf-8')
    with pytest.raises(BuilderWorkflowError, match='locale dictionary'):
        run(proposer, packet)
    assert calls == []


def test_provider_schema_is_closed_and_uses_only_admitted_refs(graph_project):
    _proposer, packet, _calls, _completed = setup_proposer(graph_project)
    contract = provider_contract(packet)
    assert contract['additionalProperties'] is False
    for variant in contract['properties']['ops']['items']['anyOf']:
        assert variant['additionalProperties'] is False
        assert set(variant['required']) == set(variant['properties'])
        assert variant['properties']['ref']['enum'] == ['view:work-list']


def test_thin_root_wrapper_does_not_hide_nested_usage_or_cost():
    from adaos.services.builder.semantic_proposer import _response_metrics
    metrics = _response_metrics({'job_id': 'nested.job', 'telemetry': {'timing': {'queue_ms': 7}},
        'result': {'model': 'gpt-5', 'usage': {'input_tokens': 120, 'output_tokens': 80},
                   'cost': {'basis': 'estimate', 'estimated_usd': 0.002}}}, 'high')
    assert metrics['input_tokens'] == 120 and metrics['output_tokens'] == 80
    assert metrics['cost_usd'] == 0.002 and metrics['root_queue_ms'] == 7


def test_missing_metering_is_not_reported_as_zero_cost():
    from adaos.services.builder.semantic_proposer import _response_metrics
    metrics = _response_metrics({'job_id': 'unmetered', 'status': 'failed'}, 'high')
    assert metrics['llm_calls'] == 1 and 'cost_usd' not in metrics


def test_property_scope_is_visible_and_decoder_locale_is_not_broader_than_the_mask(graph_project):
    service = SemanticAdmissionService(graph_project[0])
    packet = service.begin(project_ref='scenario:semantic_labels', instruction='English heading only',
        source_message_id='explicit.locale.scope', write_refs=['view:work-list'],
        write_properties={'view:work-list': ['title.en']})
    contract = provider_contract(packet)
    label = next(v for v in contract['properties']['ops']['items']['anyOf'] if v['properties']['kind']['enum'] == ['label.set'])
    assert label['properties']['locale']['enum'] == ['en']


def test_observer_resume_keeps_the_frozen_request_after_a_prompt_update(graph_project, monkeypatch):
    from adaos.services.builder import semantic_proposer as module
    waits = []
    def wait(*args, **kwargs):
        waits.append(args[0])
        if len(waits) == 1:
            raise TimeoutError('observer interrupted')
        return completed(*args, **kwargs)
    proposer, packet, calls, completed = setup_proposer(graph_project, wait=wait)
    with pytest.raises(TimeoutError):
        run(proposer, packet)
    original_messages = calls[0]['messages']
    monkeypatch.setattr(module, 'RULES', module.RULES + '\nNew guidance for future requests only.')
    result = run(proposer, packet)
    assert result['status'] == 'planned' and len(calls) == 1
    assert calls[0]['messages'] == original_messages


def test_pending_model_job_is_visible_without_advancing_admitted_generation(graph_project):
    def wait(*args, **kwargs):
        raise TimeoutError('observer interrupted')
    proposer, packet, calls, _completed = setup_proposer(graph_project, wait=wait)
    workflow = graph_project[1]
    before = workflow.prototype_edit_context('scenario', 'semantic_labels')['generation']
    with pytest.raises(TimeoutError):
        run(proposer, packet)
    state = workflow.describe('scenario', 'semantic_labels')
    observed = next(row for row in state['change']['runs'] if row['run_id'] == packet['admission_id'])
    assert observed['status'] == 'running' and observed['execution_metrics']['llm_calls'] == 1
    assert 'cost_usd' not in observed['execution_metrics']
    assert state['generation'] == before and len(calls) == 1


@pytest.mark.parametrize('output', [
    {'schema': PROPOSAL_SCHEMA, 'status': 'unsupported', 'message': 'unsupported', 'ops': []},
    {'schema': PROPOSAL_SCHEMA, 'status': 'proposed', 'message': '', 'ops': []},
    {'unknown': 'invalid'},
])
def test_paid_nonrevision_outcome_remains_a_single_metered_canonical_run(graph_project, output):
    proposer, packet, calls, _completed = setup_proposer(graph_project, output=output)
    before = (graph_project[2] / 'semantic.webui.json').read_bytes()
    if 'unknown' in output:
        with pytest.raises(BuilderWorkflowError, match='no valid semantic proposal'):
            run(proposer, packet)
    else:
        run(proposer, packet)
    rows = graph_project[1].describe('scenario', 'semantic_labels')['change']['runs']
    observed = [row for row in rows if row['run_id'] == packet['admission_id']]
    assert len(observed) == 1 and observed[0]['execution_metrics']['cost_usd'] == 0.0002
    assert observed[0]['status'] == ('failed' if 'unknown' in output else 'succeeded')
    assert (graph_project[2] / 'semantic.webui.json').read_bytes() == before
    assert len(calls) == 1
