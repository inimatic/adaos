from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from adaos.domain.personalization_access import SubjectRef
from adaos.services.ai.chatgpt import AccountError, ChatGPTAccount, PLAN_SCOPE
from adaos.services.ai.service import AIService, RESOURCE_REF
from adaos.services.policy.caller import verified_caller


class Vault:
    def __init__(self, data=None): self.data = data
    def read(self): return dict(self.data) if self.data else None
    def write(self, data): self.data = dict(data)
    def clear(self): self.data = None


class Provider:
    def __init__(self):
        self.binding = 'registration-1'
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()
    def status(self): return {'plan_enabled': True, 'binding_ref': self.binding, 'generation': 1}
    def binding_ref(self): return self.binding
    def stream(self, model, inputs, instructions, cancelled, **options):
        self.calls.append((model, inputs, options))
        while not self.gate.wait(.01):
            if cancelled.is_set(): return
        if cancelled.is_set(): return
        yield 'Hello '
        if options['expected_binding'] != self.binding: raise AccountError('ai_connection_changed')
        yield 'world'
        options['on_usage']({'input_tokens': 3, 'output_tokens': 2})
        options['on_citations']([{'url': 'https://example.org'}])
    def disconnect(self): self.binding = 'disconnected'; return {'connected': False}


@pytest.fixture
def service(tmp_path):
    provider = Provider()
    return AIService(tmp_path, 'user:owner', provider=provider)


def request(**extra):
    return {'resource_ref': RESOURCE_REF, 'model': 'test-model',
            'inputs': [{'role': 'user', 'content': 'Hi'}], 'instructions': 'Be concise',
            'expected_binding': 'registration-1', **extra}


def observe(service, job_id, *, consumer='skill:chat'):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = service.job('user:owner', consumer, job_id)
        if result['status'] in ('completed', 'failed', 'cancelled', 'interrupted'):
            return result
        time.sleep(.01)
    pytest.fail('AI attempt did not settle')


def test_shared_provider_has_consumer_scoped_attempts_and_no_duplicate_submission(service):
    first = service.submit('user:owner', 'skill:chat', 'intent-1', request())
    result = observe(service, first['job_id'])
    assert result['output_text'] == 'Hello world'
    assert result['usage']['input_tokens'] == 3
    assert result['cost_basis'] == 'subscription_usage_not_api_billing'
    assert service.submit('user:owner', 'skill:chat', 'intent-1', request())['job_id'] == first['job_id']
    assert len(service.provider.calls) == 1
    second = service.submit('user:owner', 'skill:builder', 'intent-1', request())
    assert observe(service, second['job_id'], consumer='skill:builder')['status'] == 'completed'
    assert len(service.provider.calls) == 2
    with pytest.raises(KeyError): service.job('user:owner', 'skill:builder', first['job_id'])
    with pytest.raises(ValueError, match='idempotency_conflict'):
        service.submit('user:owner', 'skill:chat', 'intent-1', request(instructions='Changed'))


@pytest.mark.parametrize('actor', ['user:family-member', '', 'service:chat', 'user:guest'])
def test_personal_connection_never_implicitly_sponsors_another_actor(service, actor):
    with pytest.raises(PermissionError): service.status(actor)
    with pytest.raises(PermissionError): service.submit(actor, 'skill:chat', 'intent', request())
    assert service.provider.calls == []


@pytest.mark.parametrize('extra', [
    {'expected_binding': 'other'}, {'resource_ref': 'ai-resource:managed'},
    {'max_output_tokens': 10}, {'background': True}, {'temperature': 1},
    {'model': ''}, {'inputs': 'Hi'}, {'inputs': [{'role': 'system', 'content': 'Hi'}]},
])
def test_unsupported_or_unpinned_requests_fail_before_provider_call(service, extra):
    with pytest.raises(ValueError): service.submit('user:owner', 'skill:chat', 'intent', request(**extra))
    assert service.provider.calls == []


def test_cancel_and_disconnect_stop_active_attempts(service):
    service.provider.gate.clear()
    first = service.submit('user:owner', 'skill:chat', 'intent-1', request())
    service.cancel('user:owner', 'skill:chat', first['job_id'])
    assert observe(service, first['job_id'])['status'] == 'cancelled'
    second = service.submit('user:owner', 'skill:chat', 'intent-2', request())
    service.disconnect('user:owner')
    assert observe(service, second['job_id'])['status'] == 'cancelled'


def test_restart_never_resubmits_an_ambiguous_request(service):
    with service.store.db() as db:
        db.execute("INSERT INTO attempts(id,principal,consumer,request_id,digest,binding,status,created) VALUES ('lost','user:owner','skill:chat','intent','digest','registration-1','running',0)")
    restarted = AIService(service.store.root, 'user:owner', provider=service.provider)
    assert restarted.job('user:owner', 'skill:chat', 'lost')['status'] == 'interrupted'
    assert service.provider.calls == []


def test_provider_input_is_frozen_before_async_execution_and_usage_is_bounded(service):
    service.provider.gate.clear()
    original = request()
    first = service.submit('user:owner', 'skill:chat', 'intent', original)
    original['inputs'][0]['content'] = 'mutated after admission'
    service.provider.gate.set()
    assert observe(service, first['job_id'])['status'] == 'completed'
    assert service.provider.calls[0][1][0]['content'] == 'Hi'
    from adaos.services.ai.service import _usage
    assert _usage({'input_tokens': 2, 'private': 'not-a-receipt', 'output_tokens': True,
                   'input_tokens_details': {'cached_tokens': 1, 'secret': 'no'}}) == {
                       'input_tokens': 2, 'input_tokens_details': {'cached_tokens': 1}}


def test_cbs_contracts_are_portable_and_have_no_credentials():
    from adaos.services.ai.contracts import inference_contract, chatgpt_binding
    contract, binding = inference_contract(), chatgpt_binding()
    assert contract.capability_ref == binding.capability_ref == 'capability:ai.inference'
    assert binding.to_dict()['implementation_entrypoint'] == 'ai.openai-chatgpt'


def test_chatgpt_wire_contract_and_usage_are_preserved(service):
    captured = []
    def handle(req):
        payload = json.loads(req.content)
        assert req.headers['authorization'] == 'Bearer private-fixture'
        captured.append(payload)
        events = [
            {'type': 'response.output_text.delta', 'delta': 'OK'},
            {'type': 'response.completed', 'response': {'usage': {'input_tokens': 4, 'output_tokens': 1}, 'output': []}},
        ]
        return httpx.Response(200, text=''.join('data: ' + json.dumps(e) + '\n\n' for e in events))
    vault = Vault({'access_token': 'private-fixture', 'scope': PLAN_SCOPE, 'expires_at': time.time() + 3600,
                   'client_id': 'issued-client', 'subject': 'validated-subject'})
    service.provider = ChatGPTAccount(service.store, vault, httpx.Client(transport=httpx.MockTransport(handle)))
    text_format = {'format': {'type': 'json_schema', 'name': 'example', 'strict': True,
                              'schema': {'type': 'object'}}}
    first = service.submit('user:owner', 'skill:chat', 'intent', request(
        expected_binding=service.provider.binding_ref(), reasoning_effort='high', text=text_format))
    result = observe(service, first['job_id'])
    assert result['status'] == 'completed' and result['usage']['output_tokens'] == 1
    assert captured[0]['store'] is False and captured[0]['stream'] is True
    assert captured[0]['text'] == text_format
    assert 'max_output_tokens' not in captured[0]
    assert 'private-fixture' not in json.dumps(result)
    assert 'private-fixture' not in json.dumps(service.status('user:owner'))


def native_ctx(tmp_path, name='subscription_status_skill'):
    manifest = tmp_path / 'skill.yaml'
    manifest.write_text('capabilities: [ai.inference, ai.connections.manage]\n', encoding='utf-8')
    return SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path),
        skill_ctx=SimpleNamespace(get=lambda: SimpleNamespace(name=name, path=tmp_path)))


def test_native_sdk_requires_verified_owner_and_live_manifest_capability(service, tmp_path, monkeypatch):
    from adaos.sdk import ai
    from adaos.services.ai import bridge
    import adaos.sdk.core._ctx as sdk_ctx
    monkeypatch.delenv('ADAOS_AI_BRIDGE_URL', raising=False)
    ctx = native_ctx(tmp_path)
    monkeypatch.setattr(sdk_ctx, 'require_ctx', lambda *a: ctx)
    monkeypatch.setattr(bridge, 'ai_service', lambda ctx: service)
    with pytest.raises(PermissionError, match='verified_user'): ai.status()
    with verified_caller(SubjectRef('user', 'other')):
        with pytest.raises(PermissionError): ai.status()
    with verified_caller(SubjectRef('user', 'owner')):
        assert ai.status()['sharing'] == 'owner_only'
        (tmp_path / 'skill.yaml').write_text('capabilities: []\n', encoding='utf-8')
        with pytest.raises(PermissionError): ai.status()


def test_service_bridge_is_loopback_executor_scoped_and_cannot_manage_sessions(service, tmp_path, monkeypatch):
    from adaos.services.ai import bridge
    ctx = native_ctx(tmp_path, 'context_chat_service')
    monkeypatch.setattr(bridge, 'ai_service', lambda ctx: service)
    import adaos.services.applications.runtime_selection as selection
    from contextlib import nullcontext
    monkeypatch.setattr(selection, 'application_execution', lambda *a: nullcontext())
    env = bridge.bridge_environment(ctx, 'context_chat_service', tmp_path)
    token = env['ADAOS_AI_BRIDGE_TOKEN']
    assert bridge.invoke_service(token, '127.0.0.1', 'status', {})['plan_enabled']
    for host, op in [('192.0.2.1', 'status'), ('127.0.0.1', 'connect')]:
        with pytest.raises(PermissionError): bridge.invoke_service(token, host, op, {})
    with pytest.raises(ValueError): bridge.invoke_service(token, '127.0.0.1', 'status', {'principal': 'user:owner'})
    replacement = bridge.bridge_environment(ctx, 'context_chat_service', tmp_path)
    assert replacement['ADAOS_AI_BRIDGE_TOKEN'] != token
    with pytest.raises(PermissionError): bridge.invoke_service(token, '127.0.0.1', 'status', {})
    bridge.revoke_bridge('context_chat_service')
    assert bridge.bridge_environment(ctx, 'another_service', tmp_path) == {}


def test_sdk_stream_uses_core_attempt_and_stable_request_id(service, monkeypatch):
    from adaos.sdk import ai
    from adaos.services.ai.bridge import dispatch
    monkeypatch.setattr(ai, '_invoke', lambda op, **args: dispatch(service, 'user:owner', 'skill:chat', op, args))
    refs = []
    options = {'model': 'test-model', 'inputs': [], 'expected_binding': 'registration-1', 'request_id': 'same-intent'}
    assert ''.join(ai.stream(**options, on_citations=refs.extend)) == 'Hello world'
    assert ''.join(ai.stream(**options)) == 'Hello world'
    assert len(service.provider.calls) == 1 and refs


def test_reauthorization_url_never_returns_retained_identity_or_access_tokens(service):
    from urllib.parse import parse_qs, urlsplit
    service.store.set_setting('client_id', 'issued-client')
    account = ChatGPTAccount(service.store, Vault({'client_id': 'issued-client',
        'id_token': 'private-id-token', 'access_token': 'private-access-token', 'email': 'owner@example.invalid'}))
    try:
        result = account.start()
        values = parse_qs(urlsplit(result['url']).query)
        assert values['client_id'] == ['issued-client']
        assert 'id_token_hint' not in values and 'agent_name_hint' not in values
        assert 'private-id-token' not in result['url'] and 'private-access-token' not in result['url']
    finally:
        account.cancel_sign_in()


def test_http_bridge_checks_envelope_and_does_not_accept_browser_owner_tokens(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from adaos.apps.api.ai import router
    app = FastAPI(); app.include_router(router, prefix='/api/node/internal/ai')
    client = TestClient(app, client=('127.0.0.1', 8000))
    assert client.post('/api/node/internal/ai', json={'operation': 'status', 'arguments': {}, 'owner': 'user:owner'}).status_code == 400
    assert client.post('/api/node/internal/ai', json={'operation': 'status', 'arguments': {}}, headers={'X-AdaOS-Token': 'owner'}).status_code == 403
    assert client.post('/api/node/internal/ai', content=b'x' * (1100 * 1024 + 1)).status_code == 413


@pytest.fixture
def os_keyring(monkeypatch):
    import sys
    passwords = {}
    monkeypatch.setitem(sys.modules, 'keyring', SimpleNamespace(
        get_password=lambda service, identity: passwords.get((service, identity)),
        set_password=lambda service, identity, value: passwords.__setitem__((service, identity), value),
        delete_password=lambda service, identity: passwords.pop((service, identity), None)))
    return passwords


def legacy_connection(root):
    from adaos.services.ai.store import AIStore
    from adaos.services.ai.chatgpt import KeyringVault
    import sqlite3
    (root / 'db').mkdir(parents=True)
    with sqlite3.connect(root / 'db/context_chat.db') as db:
        db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)')
        for key, value in {'host_id': 'urn:uuid:legacy-host', 'client_id': 'issued-client', 'subject': 'verified-subject'}.items():
            db.execute('INSERT INTO settings VALUES (?,?)', (key, json.dumps(value)))
        db.execute('CREATE TABLE messages(content TEXT)')
        db.execute("INSERT INTO messages VALUES ('private chat history remains here')")
    vault = KeyringVault('context-chat:urn:uuid:legacy-host', root / 'files/chatgpt-session.enc',
                         service='AdaOS.ContextChat.key')
    vault.write({'client_id': 'issued-client', 'subject': 'verified-subject',
                 'access_token': 'legacy-private', 'refresh_token': 'legacy-refresh',
                 'scope': PLAN_SCOPE, 'expires_at': time.time() + 3600})
    return vault


def test_explicit_migration_preserves_registration_and_history_and_survives_restart(tmp_path, os_keyring):
    from adaos.services.ai.migration import adopt_legacy
    legacy_root = tmp_path / 'legacy'
    vault = legacy_connection(legacy_root)
    original_db = (legacy_root / 'db/context_chat.db').read_bytes()
    service = AIService(tmp_path / 'core', 'user:owner')
    service.legacy_root = legacy_root
    with pytest.raises(ValueError, match='confirmation'): adopt_legacy(service)
    result = adopt_legacy(service, confirmed=True)
    assert result['history_moved'] is False
    assert not vault.path.exists()
    assert (legacy_root / 'db/context_chat.db').read_bytes() == original_db
    binding = service.provider.binding_ref()
    restarted = AIService(tmp_path / 'core', 'user:owner')
    assert restarted.provider.binding_ref() == binding
    assert restarted.provider.vault.read()['refresh_token'] == 'legacy-refresh'
    assert restarted.store.setting('host_id') == 'urn:uuid:legacy-host'
    assert 'legacy-private' not in (tmp_path / 'core/connection.enc').read_text()


def test_migration_never_overwrites_a_different_core_account(tmp_path, os_keyring):
    from adaos.services.ai.migration import adopt_legacy
    legacy_root = tmp_path / 'legacy'
    legacy_connection(legacy_root)
    service = AIService(tmp_path / 'core', 'user:owner')
    service.legacy_root = legacy_root
    service.provider.vault.write({'client_id': 'other', 'subject': 'another-person', 'access_token': 'existing-private'})
    with pytest.raises(ValueError, match='will_not_be_overwritten'): adopt_legacy(service, confirmed=True)
    assert service.provider.vault.read()['access_token'] == 'existing-private'
    assert (legacy_root / 'files/chatgpt-session.enc').exists()


def test_migration_cleanup_retry_does_not_restore_a_stale_refresh_token(tmp_path, os_keyring, monkeypatch):
    from adaos.services.ai.migration import adopt_legacy
    from adaos.services.ai.chatgpt import KeyringVault
    legacy_root = tmp_path / 'legacy'
    legacy_connection(legacy_root)
    service = AIService(tmp_path / 'core', 'user:owner')
    service.legacy_root = legacy_root
    original_clear = KeyringVault.clear
    def fail_cleanup(vault): raise AccountError('simulated legacy cleanup failure')
    monkeypatch.setattr(KeyringVault, 'clear', fail_cleanup)
    with pytest.raises(AccountError): adopt_legacy(service, confirmed=True)
    newer = service.provider.vault.read()
    newer['refresh_token'] = 'newer-core-refresh'
    service.provider.vault.write(newer)
    monkeypatch.setattr(KeyringVault, 'clear', original_clear)
    assert adopt_legacy(service, confirmed=True)['status'] == 'adopted'
    assert service.provider.vault.read()['refresh_token'] == 'newer-core-refresh'


def test_personal_prototype_port_never_calls_an_older_builders_generic_chat():
    from adaos.adapters.builder.legacy_dev_skill import PersonalChatGPTPrototypeExecution
    calls = []
    def run_tool(skill, tool, payload, **kwargs):
        calls.append((skill, tool))
        raise ValueError('Unknown tool in old Builder')
    port = PersonalChatGPTPrototypeExecution()
    port._skill_manager = SimpleNamespace(run_tool=run_tool)
    with pytest.raises(ValueError, match='Unknown tool'):
        port.submit_turn({'text': 'Edit', '_meta': {}}, timeout_seconds=30)
    assert calls == [('builder_skill', 'prototype_chatgpt_edit')]
