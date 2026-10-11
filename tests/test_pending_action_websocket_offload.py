import asyncio
import contextvars
import threading
import pytest
from adaos.services import conversation_interactions, conversation_store, pending_action_projection
from adaos.services.yjs import gateway_ws


@pytest.mark.parametrize('kind', ['conversation.interaction.list.request', 'conversation.interaction.get.request'])
def test_durable_projection_does_not_block_websocket_loop_and_preserves_context(monkeypatch, kind):
    origin_thread = threading.get_ident()
    request_context = contextvars.ContextVar('pending_action_test_request', default=None)
    request_context.set('owner-context')
    started, release = threading.Event(), threading.Event()
    calls, responses = [], []
    def slow_projection(interaction_id, *, principal, _inputs=None):
        calls.append((threading.get_ident(), request_context.get(), principal))
        started.set()
        assert release.wait(3), 'Event loop did not remain responsive'
        return {'projection_id': interaction_id}
    monkeypatch.setattr(pending_action_projection, 'project_pending_action_from_store', slow_projection)
    monkeypatch.setattr(conversation_interactions, 'query_interactions', lambda **_: {
        'items': [{'interaction_id': 'owned'}], 'next_cursor': 'next'})
    monkeypatch.setattr(conversation_store, 'read_interaction_projection_inputs', lambda _: {})
    async def scenario():
        async def send(value):
            responses.append(value)
        task = asyncio.create_task(gateway_ws.process_events_command(kind=kind, cmd_id='offload-regression',
            payload={'interaction_id': 'owned', '_meta': {'user_id': 'forged'}}, device_id='owned-device',
            webspace_id='owned-space', send_response=send))
        deadline = asyncio.get_running_loop().time() + 2
        try:
            while not started.is_set() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(.01)
            assert started.is_set()
            assert not task.done(), 'Projection must still be waiting while the event loop runs'
        finally:
            release.set()
        await task
    asyncio.run(scenario())
    assert calls[0][0] != origin_thread
    assert calls[0][1] == 'owner-context'
    assert calls[0][2]['actor_id'] == 'transport:browser:owned-device'
    assert responses[-1]['ok'] is True


def test_projection_page_retains_cursor_and_skips_unreadable_items_without_leaking_errors(monkeypatch):
    observed = []
    def query(**values):
        observed.append(values)
        return {'items': [{'interaction_id': 'allowed'}, {'interaction_id': 'denied'}], 'next_cursor': 'next'}
    def project(identity, **_):
        if identity == 'denied':
            raise PermissionError('private decision')
        return {'projection_id': identity}
    monkeypatch.setattr(conversation_interactions, 'query_interactions', query)
    monkeypatch.setattr(pending_action_projection, 'project_pending_action_from_store', project)
    monkeypatch.setattr(conversation_store, 'read_interaction_projection_inputs', lambda _: {})
    result = pending_action_projection.project_pending_action_page_from_store(principal={'id': 'caller'}, limit=1000, cursor='current')
    assert observed[0]['limit'] == 100 and observed[0]['cursor'] == 'current'
    assert result['next_cursor'] == 'next'
    assert result['items'] == [{'projection_id': 'allowed'}]
    assert result['skipped'] == [{'interaction_id': 'denied', 'reason_code': 'PermissionError'}]
