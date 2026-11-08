import asyncio
import threading

from adaos.services.system_model import service
from adaos.apps.api import node_api


def test_transition_status_keeps_authority_without_inventory_or_skill_diagnostics(monkeypatch):
    monkeypatch.setattr(service, 'current_node_probe_status_payload', lambda: {
        'node_id': 'node.one', 'subnet_id': 'sn.one', 'role': 'hub', 'ready': False,
        'draining': True, 'runtime': {}, 'environment': {'envType': 'dev'}})
    monkeypatch.setattr(service, '_node_status_supervisor_runtime', lambda _: {
        'available': True, 'status': {'state': 'preparing', 'manifest': {'large': 'x' * 10000}},
        'attempt': {'state': 'preparing', 'target_version': '1.2.3'},
        'runtime': {'transition_mode': 'warm_switch', 'runtime_state': 'ready'}})
    def forbidden(*args, **kwargs):
        raise AssertionError('Transition observation must not collect unrelated diagnostics')
    for name in ('_node_status_sidecar_runtime', '_node_status_service_supervisor_runtime',
                 '_deployment_inventory_status_payload'):
        monkeypatch.setattr(service, name, forbidden)
    result = service.current_node_transition_status_payload()
    assert result['status_profile'] == 'transition'
    assert result['draining'] and not result['ready']
    assert result['runtime']['supervisor_runtime']['attempt']['target_version'] == '1.2.3'
    assert result['runtime']['supervisor_runtime']['runtime']['transition_mode'] == 'warm_switch'
    assert result['runtime']['supervisor_runtime']['status'] == {'state': 'preparing'}


def test_transition_route_collects_off_the_api_loop_without_full_status(monkeypatch):
    owner = threading.get_ident()
    def transition():
        assert threading.get_ident() != owner
        return {'node_id': 'node.one', 'subnet_id': 'sn.one', 'role': 'hub', 'ready': True,
                'status_profile': 'transition'}
    def forbidden():
        raise AssertionError('Full diagnostics were requested')
    monkeypatch.setattr(node_api, 'current_node_transition_status_payload', transition)
    monkeypatch.setattr(node_api, '_node_status_payload', forbidden)
    result = asyncio.run(node_api.node_status(diagnostics=False, profile='transition'))
    assert result.status_profile == 'transition' and result.ready
