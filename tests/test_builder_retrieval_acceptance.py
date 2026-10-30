import copy
import json

import pytest

from adaos.services.builder import prototype_mcp as mcp
from adaos.services.builder.retrieval_acceptance import verify_materialized_guidance, ContextDeliveryRejected


def fixture(transport):
    snapshot = mcp.library_snapshot()
    fragment = next(row for row in snapshot['fragments'] if row['id'] == 'view_roles')
    admission = {'transport': transport, 'generation_plan_ref': 'generation-plan:exact',
        'guidance_library_digest': snapshot['library_digest'], 'required_retrieval_refs': ['view_roles']}
    if transport == 'personal':
        response = {'status': 'completed', 'tool_telemetry': {'tool_calls': [{'name': mcp.OPENAI_TOOLS[1],
            'status': 'completed', 'library_digest': snapshot['library_digest'], 'fragment_ids': ['view_roles']}]}}
    else:
        response = {'status': 'succeeded', 'response': {'output': [{'type': 'mcp_call', 'server_label': mcp.SERVER_LABEL,
            'name': mcp.OPENAI_TOOLS[1], 'status': 'completed', 'output': json.dumps({'ok': True, 'result': {
                'library_digest': snapshot['library_digest'], 'side_effects': 'none', 'fragments': [fragment]}})}]}}
    return response, admission


@pytest.mark.parametrize('transport', ['personal', 'root'])
def test_only_actual_materialized_guidance_can_close_the_same_declared_dependency(transport):
    response, admission = fixture(transport)
    receipt = verify_materialized_guidance(response, admission)
    assert receipt['status'] == 'passed' and receipt['materialized_fragment_ids'] == ['view_roles']
    assert receipt['missing_fragment_ids'] == []


@pytest.mark.parametrize('transport', ['personal', 'root'])
def test_assistant_claims_or_other_tools_do_not_satisfy_a_guidance_dependency(transport):
    response, admission = fixture(transport)
    if transport == 'personal':
        response['tool_telemetry']['tool_calls'][0]['name'] = mcp.OPENAI_TOOLS[0]
    else:
        response['response']['output'][0]['type'] = 'message'
    with pytest.raises(ContextDeliveryRejected) as error:
        verify_materialized_guidance(response, admission)
    assert error.value.receipt['missing_fragment_ids'] == ['view_roles']
    assert error.value.findings[0]['owner'] == 'context_delivery'


@pytest.mark.parametrize('fault', ['content', 'digest', 'server', 'failed', 'no_effect_guarantee'])
def test_native_mcp_output_needs_the_exact_trusted_read_and_contents(fault):
    response, admission = fixture('root')
    item = response['response']['output'][0]
    body = json.loads(item['output'])
    if fault == 'content': body['result']['fragments'][0]['content'] = 'PRIVATE-MODIFIED'
    elif fault == 'digest': body['result']['library_digest'] = 'another'
    elif fault == 'server': item['server_label'] = 'another'
    elif fault == 'failed': item['status'] = 'failed'
    elif fault == 'no_effect_guarantee': body['result']['side_effects'] = 'write'
    item['output'] = json.dumps(body)
    with pytest.raises(ContextDeliveryRejected): verify_materialized_guidance(response, admission)


def test_inline_context_does_not_invent_a_need_for_tool_calls():
    assert verify_materialized_guidance({}, {'required_retrieval_refs': []}) is None


@pytest.mark.parametrize('valid', [False, True])
def test_async_observation_recovers_the_exact_admission_and_retains_delivery_verdict(tmp_path, valid):
    from adaos.services.builder.provider_jobs import PlannedBuilderJobs
    response, admission = fixture('root')
    response['job_id'] = 'retained-job'
    if not valid: response['response']['output'][0]['type'] = 'message'
    class Provider:
        def submit(self, messages, **options):
            return {'job_id': 'retained-job', 'status': 'queued', 'context_admission': admission}
        def wait(self, *args, **options): return response
    original = PlannedBuilderJobs(Provider(), None, tmp_path)
    original.submit([], request_id='exact-request')
    resumed = PlannedBuilderJobs(Provider(), None, tmp_path)
    if valid:
        result = resumed.wait('retained-job')
        assert result['retrieval_acceptance']['status'] == 'passed'
    else:
        with pytest.raises(ContextDeliveryRejected): resumed.wait('retained-job')
    records = list(tmp_path.glob('retrieval-*.json'))
    assert len(records) == 1
    assert json.loads(records[0].read_text())['status'] == ('passed' if valid else 'failed')
