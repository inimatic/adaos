from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from adaos.sdk import ai
from adaos.services.ai.contracts import inference_contract, chatgpt_binding, install_contracts
from adaos.services.builder.personal_prototype import PersonalBuilderJobs


def metadata():
    return {'builder_ai_resource_ref': ai.RESOURCE_REF, 'builder_llm_model': 'test-model',
        'builder_subscription_output_policy': 'provider_default',
        'builder_ai_expected_binding': 'frozen-binding'}


def test_full_prototype_preserves_identity_and_never_forwards_root_only_options(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, 'submit', lambda **kw: calls.append(kw) or {
        'job_id': 'ai-full', 'status': 'queued', 'output_text': ''})
    monkeypatch.setattr(ai, 'job', lambda job_id: {
        'job_id': job_id, 'status': 'completed', 'output_text': '{}',
        'usage': {'input_tokens': 5, 'output_tokens': 2}, 'funding_source': 'personal_subscription'})
    jobs = PersonalBuilderJobs(metadata())
    submitted = jobs.submit([{'role': 'system', 'content': 'Rules'}, {'role': 'user', 'content': 'Build'}],
        model='test-model', request_id='stable-intent', temperature=1, max_tokens=64000,
        prompt_cache_retention='24h', prompt_cache_key='root-only',
        reasoning={'effort': 'high'}, text={'format': {'type': 'json_object'}})
    assert calls == [{'request_id': 'stable-intent', 'model': 'test-model',
        'inputs': [{'role': 'user', 'content': 'Build'}], 'instructions': 'Rules',
        'expected_binding': 'frozen-binding', 'resource_ref': ai.RESOURCE_REF,
        'reasoning_effort': 'high', 'text': {'format': {'type': 'json_object'}}}]
    result = jobs.wait(submitted['job_id'], base_url=jobs.base_url, timeout_s=0)
    assert result['status'] == 'succeeded' and result['telemetry']['usage']['input_tokens'] == 5
    assert 'cost' not in result['telemetry'] and len(calls) == 1


@pytest.mark.parametrize('options', [{'tools': [{'type': 'mcp'}]}, {'tool_choice': 'auto'},
    {'stream_protocol': 'jsonl'}, {'profile_scope': 'automation'}, {'model': 'different-model'}])
def test_personal_prototype_forbids_tool_and_model_fallback(monkeypatch, options):
    monkeypatch.setattr(ai, 'submit', lambda **_: pytest.fail('Rejected request reached provider'))
    with pytest.raises(ValueError):
        PersonalBuilderJobs(metadata()).submit([{'role': 'user', 'content': 'Build'}],
            request_id='one', **{'model': 'test-model', **options})


def test_personal_observer_timeout_does_not_cancel_or_resubmit(monkeypatch):
    monkeypatch.setattr(ai, 'job', lambda job_id: {'job_id': job_id, 'status': 'running'})
    monkeypatch.setattr(ai, 'submit', lambda **_: pytest.fail('Observer resubmitted'))
    monkeypatch.setattr(ai, 'cancel', lambda *_: pytest.fail('Observer cancelled'))
    from adaos.sdk.llm.llm_client import LlmJobWaitTimeout
    with pytest.raises(LlmJobWaitTimeout) as error:
        PersonalBuilderJobs(metadata()).wait('ai-slow', timeout_s=0)
    assert error.value.observation['provider_job_id'] == 'ai-slow'
    assert error.value.observation['resubmitted'] is False


def test_ai_cbs_contract_is_discoverable_but_never_grants_or_fabricates_activation(tmp_path):
    from adaos.services.capability_binding_state.catalog import PortableContractCatalog
    from adaos.services.builder.cbs_intent import validate_cbs_intent
    descriptor = ai.describe_capability()
    validate_cbs_intent(descriptor['cbs_intent'])
    assert descriptor['consumer_depends_on_management_app'] is False
    assert descriptor['implementation'] == 'core_builtin'
    assert descriptor['portable_provider_activation'] == 'requires_delivery_and_evidence'
    install_contracts(tmp_path)
    install_contracts(tmp_path)
    catalog = PortableContractCatalog(tmp_path / 'capability-binding-state/portable')
    assert catalog.matching_capabilities('capability:ai.inference', '^1.0.0')[0].digest == inference_contract().digest
    assert catalog.matching_bindings('capability:ai.inference', '1.0.0')[0].digest == chatgpt_binding().digest
    operation = next(op for op in descriptor['contract']['operations'] if op['operation_id'] == 'submit')
    valid = {'resource_ref': ai.RESOURCE_REF, 'model': 'test-model', 'request_id': 'intent',
        'expected_binding': 'binding', 'inputs': [{'role': 'user', 'content': 'Hi'}]}
    schema = Draft202012Validator(operation['input_schema'])
    schema.validate(valid)
    assert list(schema.iter_errors({**valid, 'access_token': 'forbidden'}))
