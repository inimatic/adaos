"""Normalize retained browser-owner evidence, without interpreting model intent."""
from __future__ import annotations

from .failure_analysis import analyze_constraints
from .traceability import digest


def analyze_browser_receipt(receipt, *, evidence_ref):
    """Project exact owner facts; incomplete legacy dependencies stay unqualified.

    A legacy verifier may be enriched from its retained trace, with that
    provenance preserved in a separate receipt. Never substitute today's file.
    This diagnostic does not change the browser verdict or permit a repair.
    """
    source = receipt.get('source') or {}
    verifier = receipt.get('verifier') or {}
    samples = (receipt.get('report') or {}).get('samples') or []
    if (not evidence_ref or type(receipt.get('ok')) is not bool or not receipt.get('task_id') or not source.get('digest')
            or not verifier.get('before') or not all(verifier['before'].values()) or not samples
            or any(not row.get('state_snapshots') or not row.get('environment')
                   or not row.get('runtime_identity')
                   or any(point.get('snapshot') is None for point in row.get('state_snapshots') or []) for row in samples)):
        raise ValueError('Retained source, verifier, observed state/environment and task evidence are required')
    episode = {'task_id': receipt['task_id'],
        'execution_ref': 'browser:' + receipt['task_id'] + ':' + str(receipt.get('attempt')),
        'source': digest(source), 'verifier': digest(verifier['before']),
        'state': digest([row['state_snapshots'] for row in samples]),
        'environment': digest({'runtime_contract': receipt.get('runtime_contract'),
            'samples': [{key: row.get(key) for key in ('layout', 'environment', 'runtime_identity')}
                        for row in samples]}),
        'outcome': 'passed' if receipt.get('ok') is True else 'failed',
        'terminal_evidence_ref': evidence_ref, 'blocked_by': []}
    constraints = [
        {'constraint_id': 'browser.verifier_stable', 'owner': 'verifier'},
        {'constraint_id': 'browser.source_stable', 'owner': 'environment'},
        {'constraint_id': 'browser.candidate_postcondition', 'owner': 'application'}]
    observations = []

    def observe(identity, satisfied, *, applies=True, suffix=''):
        observations.append({'observation_id': identity + suffix, 'sequence': len(observations),
            'constraint_id': identity, 'applies': applies, 'satisfied': satisfied,
            'evidence_ref': evidence_ref + suffix,
            **{key: episode[key] for key in ('task_id', 'execution_ref', 'source', 'verifier', 'state', 'environment')}})

    stable_verifier = verifier.get('unchanged_during_observation')
    stable_source = source.get('unchanged_during_observation')
    observe('browser.verifier_stable', stable_verifier)
    observe('browser.source_stable', stable_source, applies=stable_verifier is True)
    if episode['outcome'] == 'failed':
        if stable_verifier is False:
            episode['blocked_by'].append('browser.verifier_stable')
        elif stable_source is False:
            episode['blocked_by'].append('browser.source_stable')
    for index, sample in enumerate(samples):
        authority_id = 'browser.runtime_authority.sample_' + str(index)
        settled_id = 'browser.data_settled.sample_' + str(index)
        constraints.extend([{'constraint_id': authority_id, 'owner': 'environment'},
                            {'constraint_id': settled_id, 'owner': 'unknown'}])
        # This is an observed network authority, not an inferred selection race.
        foreign = sample.get('foreign_api_origins')
        authority_ok = not foreign if isinstance(foreign, list) else None
        applicable = stable_verifier is True and stable_source is True
        suffix = '#sample-' + str(index)
        observe(authority_id, authority_ok, applies=applicable, suffix=suffix)
        observe(settled_id, sample.get('authoritative_data_settled'),
                applies=applicable and authority_ok is True, suffix=suffix)
        if episode['outcome'] == 'failed' and applicable and authority_ok is False:
            episode['blocked_by'].append(authority_id)
    journey = receipt.get('application_journey') or {}
    # Only the trusted owner's explicit repairable postcondition verdict may
    # identify a candidate violation. Timeouts or green flags alone cannot.
    candidate_guard = (stable_verifier is True and stable_source is True
        and all(isinstance(row.get('foreign_api_origins'), list) and not row['foreign_api_origins']
                and row.get('authoritative_data_settled') is True for row in samples)
        and bool(journey))
    candidate_violation = (journey.get('ok') is False and journey.get('repairable') is True
                           and not journey.get('timed_out'))
    observe('browser.candidate_postcondition', False if candidate_violation else True if journey.get('ok') is True else None,
            applies=candidate_guard)
    if episode['outcome'] == 'failed' and candidate_guard and candidate_violation:
        episode['blocked_by'].append('browser.candidate_postcondition')
    episode['blocked_by'] = list(dict.fromkeys(episode['blocked_by']))
    return analyze_constraints(episode=episode, constraints=constraints, observations=observations)
