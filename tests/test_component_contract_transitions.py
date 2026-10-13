"""Durable CAS/replay guarantees independent of the active UI fixture."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from adaos.services.context_control import ContextConflict, ContextControlService
from adaos.services.ui_component_contracts import load_component_contract, project_component_contract


def successor(base, version, marker):
    value = copy.deepcopy(base)
    value['version'] = version
    value['components'][0]['effects'].append(marker)
    return value


def binding(service):
    return service.get_binding(subject_ref='component:ui.table', purpose='builder.contract_input', audience='builder')


def test_stale_base_and_conflicting_event_cannot_overwrite_accepted_changes(tmp_path):
    service = ContextControlService(tmp_path)
    base = load_component_contract()
    service.publish_component_projection(project_component_contract(base))
    first = successor(base, '1.0.1', 'accepted.effect')
    result = service.evolve_component_contract(base, first, event_ref='event:first')
    active = binding(service)
    stale = successor(base, '1.0.2', 'stale.effect')
    for event in ('event:stale', 'event:first'):
        with pytest.raises(ContextConflict):
            service.evolve_component_contract(base, stale, event_ref=event)
        assert binding(service) == active
    assert service.evolve_component_contract(base, first, event_ref='event:first') == result


def test_replay_after_later_evolution_and_restart_returns_original_result_without_promotion(tmp_path):
    service = ContextControlService(tmp_path)
    base = load_component_contract()
    service.publish_component_projection(project_component_contract(base))
    first = successor(base, '1.0.1', 'first.effect')
    original = service.evolve_component_contract(base, first, event_ref='event:first')
    second = successor(first, '1.0.2', 'second.effect')
    service.evolve_component_contract(first, second, event_ref='event:second')
    active = binding(service)
    restarted = ContextControlService(tmp_path)
    assert restarted.evolve_component_contract(base, first, event_ref='event:first') == original
    assert binding(restarted) == active
    unit = restarted.get_capsule(active['capsule_id'], include_content=True)['artifact']['content']
    assert unit['provenance']['source_digest'] == project_component_contract(second)['provenance']['source_digest']
    assert {'first.effect', 'second.effect'} <= set(unit['content']['effects'])


def test_two_concurrent_transitions_from_same_base_have_exactly_one_winner(tmp_path):
    service = ContextControlService(tmp_path)
    base = load_component_contract()
    service.publish_component_projection(project_component_contract(base))
    candidates = [successor(base, '1.0.1', 'effect.a'), successor(base, '1.0.2', 'effect.b')]
    barrier = Barrier(2)
    def transition(index):
        actor = ContextControlService(tmp_path)
        barrier.wait(timeout=10)
        try:
            actor.evolve_component_contract(base, candidates[index], event_ref='event:' + str(index))
            return index
        except ContextConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(transition, (0, 1)))
    winners = [index for index in outcomes if index is not None]
    assert len(winners) == 1
    unit = service.get_capsule(binding(service)['capsule_id'], include_content=True)['artifact']['content']
    assert unit['provenance']['source_digest'] == project_component_contract(candidates[winners[0]])['provenance']['source_digest']
    with service._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM component_contract_transitions').fetchone()[0] == 1


def test_direct_publication_cannot_bypass_transition_admission(tmp_path):
    service = ContextControlService(tmp_path)
    base = load_component_contract()
    service.publish_component_projection(project_component_contract(base))
    active = binding(service)
    with pytest.raises(ContextConflict):
        service.publish_component_projection(project_component_contract(successor(base, '1.0.1', 'unsafe.effect')))
    assert binding(service) == active
