import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from adaos.apps.api import server
from adaos.services import source_policy
from test_core_update_source_fencing import update


def test_runtime_proxy_preserves_the_original_policy(update, monkeypatch):
    policy = source_policy.source_policy()
    captured = []
    def forward(path, body, **kwargs):
        captured.append((path, body))
        return {'ok': True}
    monkeypatch.setattr(server, '_try_forward_update_to_supervisor', forward)
    body = server.CoreUpdateStartRequest(target_rev='main', target_version='a' * 40, source_policy=policy)
    assert server._try_forward_update_start_to_supervisor(body) == {'ok': True}
    assert captured[0][1]['source_policy'] == policy


def test_runtime_api_rejects_stale_policy_before_forwarding_or_mutation(update, monkeypatch):
    policy = source_policy.source_policy()
    monkeypatch.setenv('SRC_BRANCH', 'dev2027')
    source_policy.source_policy()
    monkeypatch.setenv('SRC_BRANCH', 'main')
    source_policy.source_policy()
    monkeypatch.setattr(server, '_ensure_runtime_admin_mutation_allowed', lambda _: None)
    monkeypatch.setattr(server, 'core_update_reactions_disabled_reason', lambda: None)
    monkeypatch.setattr(server, '_try_forward_update_start_to_supervisor',
        lambda _: pytest.fail('stale policy reached Supervisor'))
    with pytest.raises(HTTPException) as failure:
        asyncio.run(server.admin_update_start(server.CoreUpdateStartRequest(
            target_rev='main', target_version='a' * 40, source_policy=policy)))
    assert failure.value.status_code == 409
    assert failure.value.detail == {'code': 'source_policy_changed'}


def test_policy_change_during_unmanaged_countdown_cannot_stop_the_serving_core(update, monkeypatch):
    policy = source_policy.source_policy()
    monkeypatch.setenv('SRC_BRANCH', 'dev2027')
    source_policy.source_policy()
    statuses = []
    async def status(payload):
        statuses.append(payload)
        return payload
    async def never_stop(*args, **kwargs):
        pytest.fail('stale countdown stopped the serving Core')
    monkeypatch.setattr(server, 'write_core_update_status_async', status)
    monkeypatch.setattr(server, '_run_core_update_shutdown', never_stop)
    monkeypatch.setattr(server, 'write_core_update_plan',
        lambda _: pytest.fail('stale countdown persisted a pending restart'))
    asyncio.run(server._core_update_countdown_worker(SimpleNamespace(state=SimpleNamespace()),
        action='update', target_rev='main', target_version='a' * 40, reason='test',
        countdown_sec=0, drain_timeout_sec=1, signal_delay_sec=0, source_policy_snapshot=policy))
    assert statuses[-1]['state'] == 'cancelled'
    assert statuses[-1]['error'] == 'source_policy_changed'


@pytest.mark.parametrize('policy_location', ['event', 'manifest'])
def test_member_follow_forwards_the_event_policy_without_recapturing_it(update, monkeypatch, policy_location):
    from adaos.services.subnet import link_client
    policy = source_policy.source_policy()
    monkeypatch.setattr(link_client, 'get_ctx', lambda: SimpleNamespace(config=source_policy._config()))
    monkeypatch.setattr(link_client, 'core_update_reactions_disabled_reason', lambda: None)
    monkeypatch.setattr(link_client, 'active_slot_manifest', lambda: {})
    monkeypatch.setattr('adaos.services.core_update.read_status', lambda: {})
    calls = []
    owner = SimpleNamespace(_last_follow_key='', _last_follow_at=0, _queue_node_snapshot=lambda: None,
        _remaining_countdown_s=lambda *args, **kwargs: 0,
        _post_local_admin=lambda path, body: calls.append((path, body)) or {'ok': True})
    event = {'action': 'update', 'state': 'validated', 'target_rev': 'main', 'target_version': 'a' * 40}
    if policy_location == 'event':
        event['source_policy'] = policy
    else:
        event['manifest'] = {'source_policy': policy}
    asyncio.run(link_client.MemberLinkClient._follow_hub_core_update(owner, event))
    assert len(calls) == 1 and calls[0][1]['source_policy'] == policy
    asyncio.run(link_client.MemberLinkClient._follow_hub_core_update(owner, event))
    assert len(calls) == 1
    monkeypatch.setenv('SRC_BRANCH', 'dev2027')
    source_policy.source_policy()
    monkeypatch.setenv('SRC_BRANCH', 'main')
    revised = source_policy.source_policy()
    event['source_policy'] = revised
    asyncio.run(link_client.MemberLinkClient._follow_hub_core_update(owner, event))
    assert len(calls) == 2 and calls[-1][1]['source_policy'] == revised
