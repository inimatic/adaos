"""A chat transport authenticates its caller, not identities in message metadata."""
import asyncio
from types import SimpleNamespace

import pytest

from adaos.domain.personalization_access import SubjectRef
from adaos.services.eventbus import LocalEventBus
from adaos.services.policy.caller import current_caller, verified_caller
from adaos.services.policy.invocation import current_invocation
from adaos.services.yjs import gateway_ws


@pytest.mark.parametrize('kind', ['voice.chat.user', 'dialog.user_message'])
@pytest.mark.parametrize('owner', [SubjectRef('user', 'owner'), None, {'kind': 'user', 'id': 'forged'}])
def test_chat_binds_only_verified_transport_user_across_async_worker(monkeypatch, kind, owner):
    bus = LocalEventBus()
    seen, acknowledgements = [], []
    monkeypatch.setattr(gateway_ws, 'get_agent_ctx', lambda: SimpleNamespace(bus=bus))

    async def consume(event):
        seen.append(await asyncio.to_thread(lambda: (current_caller(), current_invocation(), event.payload)))

    bus.subscribe(kind, consume)

    async def run():
        async def send(value):
            acknowledgements.append(value)
        # An unrelated ambient caller must not authorize a device-only socket.
        with verified_caller(SubjectRef('user', 'ambient')):
            await gateway_ws.process_events_command(kind=kind, cmd_id='chat-intent',
                payload={'text': 'Update prototype', 'webspace_id': 'builder-test',
                    '_meta': {'user_id': 'forged', 'caller': {'kind': 'user', 'id': 'forged'}}},
                device_id='device', webspace_id='builder-test', send_response=send, verified_owner=owner)
            assert current_caller().id == 'ambient'
        assert await bus.wait_for_idle()

    asyncio.run(run())
    expected = owner if isinstance(owner, SubjectRef) else None
    assert len(seen) == 1
    assert seen[0][0] == expected
    assert seen[0][1]['request_id'] == 'events:device:chat-intent'
    assert seen[0][2]['text'] == 'Update prototype'
    assert acknowledgements[-1]['ok'] is True
    assert current_caller() is None


@pytest.mark.parametrize('bounded', [False, True])
def test_queued_chat_events_never_share_the_first_users_authority(monkeypatch, bounded):
    monkeypatch.setenv('ADAOS_EVENTBUS_BOUNDED_TOPICS', 'voice.chat.user' if bounded else '')
    bus = LocalEventBus()
    monkeypatch.setattr(gateway_ws, 'get_agent_ctx', lambda: SimpleNamespace(bus=bus))
    seen = []
    async def consume(event):
        await asyncio.sleep(0)
        seen.append((current_caller(), current_invocation()['request_id']))
    bus.subscribe('voice.chat.user', consume)
    owners = [SubjectRef('user', 'first'), SubjectRef('user', 'second'), None]
    async def run():
        async def send(_): pass
        for number, owner in enumerate(owners):
            await gateway_ws.process_events_command(kind='voice.chat.user', cmd_id=str(number),
                payload={'text': 'Update', 'webspace_id': 'builder-test'}, device_id='device',
                webspace_id='builder-test', send_response=send, verified_owner=owner)
        assert await bus.wait_for_idle()
    asyncio.run(run())
    assert seen == [(owner, f'events:device:{number}') for number, owner in enumerate(owners)]
