import asyncio
from types import SimpleNamespace

import pytest

from adaos.services import operator_controls
from adaos.services.root import core_update_sync
from adaos.services.subnet import link_client


@pytest.mark.parametrize('node,operator,disabled', [(True, True, False), (False, True, True),
                                                  (True, False, True), (False, False, True)])
def test_hub_automatic_update_honors_both_controls(monkeypatch, node, operator, disabled):
    monkeypatch.setattr(operator_controls, 'read_controls', lambda: {'core_auto_update': operator})
    assert core_update_sync._node_core_update_disabled(SimpleNamespace(core_update_enabled=node)) is disabled


def test_hub_automatic_update_fails_closed_without_operator_context(monkeypatch):
    def missing():
        raise RuntimeError('no runtime Context')
    monkeypatch.setattr(operator_controls, 'read_controls', missing)
    assert core_update_sync._node_core_update_disabled(SimpleNamespace(core_update_enabled=True)) is True


@pytest.mark.parametrize('node,operator', [(False, True), (True, False), (False, False)])
def test_manual_update_checks_release_with_automatic_updates_disabled(monkeypatch, node, operator):
    calls = []
    release = {'ok': True, 'needs_update': False}

    def check_release(**kwargs):
        calls.append(kwargs)
        return release

    monkeypatch.setattr(operator_controls, 'read_controls', lambda: {'core_auto_update': operator})
    monkeypatch.setattr(core_update_sync, 'core_update_reactions_disabled_reason', lambda: None)
    monkeypatch.setattr(core_update_sync, '_root_client', lambda _conf: SimpleNamespace(
        hub_core_update_release=check_release))
    monkeypatch.setattr(core_update_sync, 'report_hub_core_update_state', lambda _conf: None)
    monkeypatch.setattr(core_update_sync, 'active_slot_manifest', lambda: {'git_commit': 'a' * 40})
    monkeypatch.setattr(core_update_sync, 'source_policy', lambda _conf: {'branch': 'main'})
    conf = SimpleNamespace(core_update_enabled=node)

    assert core_update_sync.reconcile_hub_core_update(conf, manual=True) == release
    assert calls == [{'branch': 'main', 'current_commit': 'a' * 40}]
    assert core_update_sync.reconcile_hub_core_update(conf)['reason'] == 'node_core_update_disabled'
    assert len(calls) == 1


def test_manual_update_preserves_runtime_update_admission(monkeypatch):
    monkeypatch.setattr(core_update_sync, 'core_update_reactions_disabled_reason',
                        lambda: 'dev_core_update_reactions_disabled')
    monkeypatch.setattr(core_update_sync, '_root_client',
                        lambda _conf: pytest.fail('disabled runtime must not query a release'))

    result = core_update_sync.reconcile_hub_core_update(
        SimpleNamespace(core_update_enabled=False), manual=True)

    assert result == {'ok': True, 'skipped': True, 'reason': 'dev_core_update_reactions_disabled'}


@pytest.mark.parametrize('node,operator,reason', [
    (False, True, 'core_update_disabled_by_node_config'),
    (True, False, 'core_auto_update_disabled_by_operator'),
    (False, False, 'core_update_disabled_by_node_config'),
])
def test_member_automatic_update_does_not_dispatch_when_either_gate_denies(monkeypatch, node, operator, reason):
    replies = []
    async def send(ws, message, *, lane):
        assert lane == 'control_response'
        replies.append(message)
    owner = SimpleNamespace(_send_ws_message=send)
    monkeypatch.setattr(link_client, 'get_ctx', lambda: SimpleNamespace(
        config=SimpleNamespace(core_update_enabled=node)))
    monkeypatch.setattr(operator_controls, 'read_controls', lambda: {'core_auto_update': operator})
    asyncio.run(link_client.MemberLinkClient._on_core_update_request(owner, None,
        {'action': 'update', 'request_id': 'request.synthetic', 'target_rev': 'main'}))
    assert replies == [{'t': 'core.update.result', 'request_id': 'request.synthetic', 'action': 'update',
                        'ok': False, 'accepted': False, 'error': reason}]
    assert not hasattr(owner, '_last_control_request')


def test_member_update_fails_closed_before_bootstrap_context(monkeypatch):
    replies = []
    async def send(ws, message, *, lane):
        assert lane == 'control_response'
        replies.append(message)
    def missing():
        raise RuntimeError('no runtime Context')
    monkeypatch.setattr(link_client, 'get_ctx', missing)
    asyncio.run(link_client.MemberLinkClient._on_core_update_request(
        SimpleNamespace(_send_ws_message=send), None, {'action': 'update'}))
    assert replies[0]['accepted'] is False
    assert replies[0]['error'] == 'core_update_configuration_unavailable'
