"""Personal-plan client tool execution stays bounded and credential-free."""
import copy
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from adaos.services.ai.chatgpt import AccountError, ChatGPTAccount, PLAN_SCOPE
from adaos.services.ai.service import AIService, RESOURCE_REF
from adaos.services.ai.tool_loop import run_tool_loop, prepare_toolset
from adaos.services.builder import prototype_mcp as mcp


class Toolset:
    namespace = 'adaos_prototype'
    max_provider_calls = 2
    max_tool_calls = 4
    definitions = [{'type': 'function', 'name': 'read', 'parameters': {'type': 'object',
        'properties': {'id': {'type': 'string'}}, 'required': ['id'], 'additionalProperties': False}}]

    def __init__(self): self.calls = []
    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return {'guide': arguments['id']}


def call(**extra):
    return {'type': 'function_call', 'call_id': 'call-1', 'namespace': 'adaos_prototype',
        'name': 'read', 'arguments': '{"id":"view_roles"}', **extra}


class Provider:
    def __init__(self, outputs=None):
        self.calls = []
        self.outputs = [[call()], []] if outputs is None else outputs
    def binding_ref(self): return 'binding'
    def stream(self, model, inputs, instructions, cancelled, **options):
        self.calls.append({'model': model, 'inputs': copy.deepcopy(inputs), 'instructions': instructions,
            'tools': options['tools'], 'tool_choice': options['tool_choice'], 'binding': options['expected_binding']})
        options.get('on_wire_request', lambda _: None)({'model': model, 'body_sha256': 'test-wire-' + str(len(self.calls))})
        output = self.outputs[len(self.calls) - 1]
        options['on_output_items'](copy.deepcopy(output))
        options['on_usage']({'input_tokens': 3, 'output_tokens': 2,
            'output_tokens_details': {'reasoning_tokens': 1}})
        if not any(item.get('type') == 'function_call' for item in output):
            yield '{"ok":true}'


def request():
    return {'resource_ref': RESOURCE_REF, 'model': 'exact-selected-model', 'expected_binding': 'binding',
        'inputs': [{'role': 'user', 'content': 'Use guidance'}], 'instructions': 'Return JSON',
        'toolset': {'id': mcp.PERSONAL_TOOLSET, 'library_digest': mcp.library_snapshot()['library_digest']}}


def run(provider, toolset=None, event=None):
    usage, telemetry = [], []
    result = run_tool_loop(provider, request(), event or threading.Event(), toolset or Toolset(),
        on_usage=usage.append, on_telemetry=telemetry.append)
    return result, usage, telemetry


def test_tool_loop_uses_namespaced_functions_full_history_and_total_receipts():
    provider, tools = Provider(), Toolset()
    result, usage, telemetry = run(provider, tools)
    assert result == '{"ok":true}' and len(provider.calls) == 2
    assert provider.calls[0]['tools'][0]['type'] == 'namespace'
    assert provider.calls[0]['tool_choice'] == 'auto' and provider.calls[1]['tool_choice'] == 'none'
    assert all(item['model'] == 'exact-selected-model' and item['binding'] == 'binding' for item in provider.calls)
    history = provider.calls[1]['inputs']
    assert history[0] == request()['inputs'][0]
    assert history[1] == call()
    assert history[2]['type'] == 'function_call_output' and history[2]['call_id'] == call()['call_id']
    assert usage[-1] == {'input_tokens': 6, 'output_tokens': 4, 'output_tokens_details': {'reasoning_tokens': 2}}
    assert len(tools.calls) == 1 and len(telemetry[-1]['tool_calls']) == 1
    assert 'guide' not in json.dumps(telemetry) and 'arguments' not in telemetry[-1]['tool_calls'][0]
    assert [row['provider_call'] for row in telemetry[-1]['wire_calls']] == [1, 2]
    assert telemetry[-1]['response_items'] == [
        {'provider_call': 1, 'item_count': 1, 'item_types': ['function_call']},
        {'provider_call': 2, 'item_count': 0, 'item_types': []}]


def test_core_turn_cap_is_not_forwarded_as_a_provider_option():
    class BoundedProvider(Provider):
        def stream(self, *args, **options):
            assert 'provider_call_limit' not in options
            yield from super().stream(*args, **options)
    bounded = {**request(), 'provider_call_limit': 2}
    provider = BoundedProvider()
    assert run_tool_loop(provider, bounded, threading.Event(), Toolset(),
        on_usage=lambda _: None, on_telemetry=lambda _: None) == '{"ok":true}'
    assert len(provider.calls) == 2


@pytest.mark.parametrize('change,error', [
    ({'name': 'delete_all'}, 'not_allowed'), ({'namespace': 'shell'}, 'not_allowed'),
    ({'arguments': '{'}, 'arguments_invalid'), ({'arguments': '{"id":1}'}, 'arguments_invalid'),
    ({'arguments': '{"id":"a","url":"http://private"}'}, 'arguments_invalid'),
    ({'call_id': ''}, 'not_allowed'),
])
def test_bad_calls_fail_before_any_execution(change, error):
    tools, provider = Toolset(), Provider([[call(**change)]])
    with pytest.raises(AccountError, match=error): run(provider, tools)
    assert tools.calls == [] and len(provider.calls) == 1


@pytest.mark.parametrize('calls', [[call(), call()], [call(call_id=str(i)) for i in range(5)]])
def test_duplicate_and_over_budget_batches_do_not_partially_execute(calls):
    tools = Toolset()
    with pytest.raises(AccountError): run(Provider([calls]), tools)
    assert tools.calls == []


def test_second_round_cannot_execute_tools_or_open_a_third_provider_request():
    provider, tools = Provider([[call()], [call(call_id='second')]]), Toolset()
    with pytest.raises(AccountError, match='budget'): run(provider, tools)
    assert len(provider.calls) == 2 and len(tools.calls) == 1


@pytest.mark.parametrize('read', [False, True])
def test_omitted_guidance_must_be_materialized_before_a_final_answer(read):
    class Required(Toolset):
        required_fragments = {'view_roles'}
        def execute(self, name, arguments):
            return {'library_digest': mcp.library_snapshot()['library_digest'],
                'fragments': [next(row for row in mcp.library_snapshot()['fragments'] if row['id'] == 'view_roles')]}
    tools = Required()
    provider = Provider() if read else Provider([[]])
    if read:
        result, _, telemetry = run(provider, tools)
        assert result and telemetry[-1]['tool_calls'][0]['fragment_ids'] == ['view_roles']
        assert 'content' not in telemetry[-1]['tool_calls'][0]
    else:
        with pytest.raises(AccountError, match='ai_required_guidance_not_retrieved'):
            run(provider, tools)
        assert len(provider.calls) == 1


def test_fragment_requirements_extend_a_new_ai_contract_without_rewriting_old_versions():
    from adaos.services.ai.contracts import operation_schemas
    from jsonschema import Draft202012Validator
    value = {'request_id': 'request', **request()}
    value['toolset']['required_fragment_ids'] = ['view_roles']
    Draft202012Validator(operation_schemas('1.2.0')['submit'][0]).validate(value)
    assert list(Draft202012Validator(operation_schemas('1.1.0')['submit'][0]).iter_errors(value))
    assert 'required_fragment_ids' not in operation_schemas('1.1.0')['submit'][0]['properties']['toolset']['properties']
    assert 'toolset' not in operation_schemas('1.0.0')['submit'][0]['properties']


def test_cancel_after_public_read_never_starts_followup():
    event, tools, provider = threading.Event(), Toolset(), Provider()
    def execute(*args):
        event.set()
        return {}
    tools.execute = execute
    assert run(provider, tools, event)[0] == '' and len(provider.calls) == 1


def test_tool_failure_does_not_retry_or_include_arbitrary_exception_text():
    tools = Toolset()
    def execute(*args): raise RuntimeError('PRIVATE-token')
    tools.execute = execute
    provider = Provider()
    with pytest.raises(AccountError, match='ai_tool_execution_failed') as error: run(provider, tools)
    assert 'PRIVATE-token' not in str(error.value) and len(provider.calls) == 1


def test_unregistered_toolset_or_consumer_never_contacts_root(monkeypatch):
    monkeypatch.setattr(mcp, 'issue_protocol', lambda: pytest.fail('unexpected network'))
    with pytest.raises(ValueError): prepare_toolset(request()['toolset'], 'skill:other')
    with pytest.raises(ValueError): prepare_toolset({'id': 'shell'}, 'skill:builder_skill')
    monkeypatch.setenv('ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED', '0')
    with pytest.raises(ValueError): prepare_toolset(request()['toolset'], 'skill:builder_skill')


def test_hosted_descriptor_is_consumed_locally_and_arbitrary_credentials_rejected(monkeypatch):
    descriptor = {'type': 'mcp', 'authorization': 'PRIVATE-token', 'server_url': 'https://mcp.api.inimatic.com/v1/root/mcp'}
    monkeypatch.setenv('ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED', '1')
    monkeypatch.setattr(mcp, 'issue_protocol', lambda: {'tools': [descriptor]})
    options = mcp.personal_toolset_options([descriptor], 'auto')
    assert options == {'toolset': request()['toolset']} and 'PRIVATE-token' not in json.dumps(options)
    with pytest.raises(ValueError): mcp.personal_toolset_options([{**descriptor, 'authorization': 'unscoped'}], 'auto')


@pytest.mark.parametrize('fault', [None, 'content', 'digest', 'error', 'duplicate', 'unknown', 'url'])
def test_guidance_executor_uses_exact_public_reads_and_checks_contents(monkeypatch, fault):
    from adaos.services.root import client
    snapshot = mcp.library_snapshot()
    fragments = [{key: row[key] for key in ('id', 'content_digest', 'content')}
        for row in snapshot['fragments'] if row['id'] == 'view_roles']
    result = {'library_digest': snapshot['library_digest'], 'side_effects': 'none', 'fragments': fragments}
    if fault == 'content': fragments[0]['content'] = 'unknown modified guide'
    if fault == 'digest': result['library_digest'] = 'other'
    requests = []
    class Http:
        def __init__(self, **kwargs): self.kwargs = kwargs
        def request(self, method, path, **kwargs):
            requests.append((method, path, kwargs, self.kwargs))
            return {'result': {'isError': fault == 'error', 'structuredContent': {'ok': True, 'result': result}}}
    monkeypatch.setattr(client, 'RootHttpClient', Http)
    monkeypatch.setenv('ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED', '1')
    monkeypatch.setattr(mcp, 'issue_protocol', lambda: {'tools': [{'server_url': 'https://mcp.api.inimatic.com/v1/root/mcp',
        'authorization': 'PRIVATE-token'}], 'expires_epoch': time.time() + 3600})
    tools = mcp.PersonalGuidanceTools(request()['toolset'])
    arguments = {'fragment_ids': ['view_roles']}
    if fault == 'duplicate': arguments['fragment_ids'] *= 2
    if fault == 'unknown': arguments['fragment_ids'] = ['private_secrets']
    if fault == 'url': arguments['url'] = 'http://private'
    if fault:
        with pytest.raises(Exception): tools.execute(mcp.OPENAI_TOOLS[1], arguments)
        if fault in ('duplicate', 'unknown', 'url'): assert requests == []
    else:
        assert tools.execute(mcp.OPENAI_TOOLS[1], arguments)['fragments'] == fragments
        assert requests[0][2]['json']['params']['arguments']['expected_digest'] == snapshot['library_digest']
        assert requests[0][3]['cert'] is None
        assert 'PRIVATE-token' not in json.dumps(tools.definitions)


def test_service_keeps_tool_loop_idempotent_and_only_metadata_survives_restart(tmp_path, monkeypatch):
    from adaos.services.ai import tool_loop
    tools, provider = Toolset(), Provider()
    monkeypatch.setattr(tool_loop, 'prepare_toolset', lambda spec, consumer: tools)
    from adaos.services.policy import invocation
    monkeypatch.setattr(invocation, 'current_invocation', lambda: {'request_id': 'verified-user-turn'})
    monkeypatch.setenv('ADAOS_AI_BUILDER_PROVIDER_CALL_LIMIT', '2')
    service = AIService(tmp_path, 'user:owner', provider=provider)
    first = service.submit('user:owner', 'skill:builder_skill', 'intent', request())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        observed = service.job('user:owner', 'skill:builder_skill', first['job_id'])
        if observed['status'] in ('completed', 'failed'): break
        time.sleep(.01)
    assert observed['status'] == 'completed'
    assert observed['tool_telemetry']['provider_calls'] == 2
    assert observed['usage']['input_tokens'] == 6
    assert service.submit('user:owner', 'skill:builder_skill', 'intent', request())['job_id'] == first['job_id']
    assert len(provider.calls) == 2
    with pytest.raises(ValueError, match='turn_budget_exhausted'):
        service.submit('user:owner', 'skill:builder_skill', 'automatic-repair', request())
    assert len(provider.calls) == 2
    restarted = AIService(tmp_path, 'user:owner', provider=provider)
    assert restarted.job('user:owner', 'skill:builder_skill', first['job_id']) == observed
    with service.store.db() as db:
        row = dict(db.execute('SELECT * FROM attempts').fetchone())
    assert 'output_items' not in row and 'inputs' not in row


def test_two_stage_mcp_and_inline_repair_share_one_four_call_budget(tmp_path, monkeypatch):
    from adaos.services.ai import tool_loop
    from adaos.services.policy import invocation
    monkeypatch.setenv('ADAOS_AI_BUILDER_PROVIDER_CALL_LIMIT', '4')
    monkeypatch.setattr(invocation, 'current_invocation', lambda: {'request_id': 'one-s2-mcp-turn'})
    monkeypatch.setattr(tool_loop, 'prepare_toolset', lambda spec, consumer: Toolset())
    class MixedProvider(Provider):
        def stream(self, model, inputs, instructions, cancelled, **options):
            self.calls.append(options)
            options.get('on_wire_request', lambda _: None)({'model': model, 'body_sha256': 'wire-' + str(len(self.calls))})
            if options.get('tool_choice') == 'auto':
                options['on_output_items']([call()])
            else:
                options.get('on_output_items', lambda _: None)([])
                yield '{"ok":true}'
    provider = MixedProvider()
    service = AIService(tmp_path, 'user:owner', provider=provider)
    inline = request();inline.pop('toolset')
    def complete(identity, payload):
        job = service.submit('user:owner', 'skill:builder_skill', identity, payload)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            observed = service.job('user:owner', 'skill:builder_skill', job['job_id'])
            if observed['status'] in ('completed', 'failed'): break
            time.sleep(.01)
        assert observed['status'] == 'completed'
        return observed
    interpretation = complete('interpretation', inline)
    generation = complete('generation', request())
    repair = complete('inline-repair', inline)
    assert [row['tool_telemetry']['reserved_provider_calls'] for row in (interpretation, generation, repair)] == [1, 2, 1]
    assert len(generation['tool_telemetry']['wire_calls']) == 2
    assert len(provider.calls) == 4
    assert complete('generation', request())['job_id'] == generation['job_id']
    with pytest.raises(ValueError, match='turn_budget_exhausted'):
        service.submit('user:owner', 'skill:builder_skill', 'excess', inline)
    assert len(provider.calls) == 4


def test_wire_supports_tool_only_response_without_sending_hosted_mcp(tmp_path):
    from adaos.services.ai.store import AIStore
    class Vault:
        def read(self): return {'access_token': 'PRIVATE-token', 'scope': PLAN_SCOPE,
            'expires_at': time.time() + 3600, 'client_id': 'test-client', 'subject': 'test-subject'}
    captured = []
    def handle(req):
        payload = json.loads(req.content)
        captured.append(payload)
        event = {'type': 'response.completed', 'response': {'output': [call()], 'usage': {}}}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\n')
    account = ChatGPTAccount(AIStore(tmp_path), Vault(), httpx.Client(transport=httpx.MockTransport(handle)))
    items = []
    assert list(account.stream('selected-model', [], '', threading.Event(),
        tools=[{'type': 'namespace', 'name': 'adaos_prototype', 'tools': Toolset.definitions}],
        on_output_items=items.extend)) == []
    assert items == [call()]
    payload = captured[0]
    assert payload['store'] is False and payload['stream'] is True
    assert payload['include'] == ['reasoning.encrypted_content']
    assert 'PRIVATE-token' not in json.dumps(payload)
    assert all(tool['type'] != 'mcp' for tool in payload['tools'])
    assert not {'previous_response_id', 'max_output_tokens', 'max_tool_calls'} & payload.keys()
