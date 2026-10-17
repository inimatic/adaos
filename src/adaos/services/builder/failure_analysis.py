"""Evidence-backed critical failure localization, separate from model rationale.

Only the execution owner can link a terminal failure to blocked constraints.
This projection never admits a code repair or accepts a candidate. Missing
guards, recovery observations and causal links remain explicit unknowns.
"""
from __future__ import annotations

import copy
from .traceability import digest

AXES = ('source', 'verifier', 'state', 'environment')
OWNERS = {'application': 'repair_application', 'verifier': 'requalify_same_candidate',
          'environment': 'recover_environment', 'unknown': 'inspect_evidence'}


def analyze_constraints(*, episode, constraints, observations, hypotheses=()):
    required = ('task_id', 'execution_ref', *AXES)
    if any(not episode.get(key) for key in required):
        raise ValueError('Failure analysis requires the complete episode dependency identity')
    by_id = {row['constraint_id']: row for row in constraints}
    if len(by_id) != len(constraints):
        raise ValueError('Constraint identities must be unique')
    seen, facts, unknowns, violations = set(), [], [], []
    previous = -1
    for row in observations:
        identity, order = row.get('observation_id'), row.get('sequence')
        if not identity or identity in seen or type(order) is not int or order <= previous:
            raise ValueError('Observation identities and monotonically increasing sequence are required')
        seen.add(identity); previous = order
        constraint = by_id.get(row.get('constraint_id'))
        if constraint is None or not row.get('evidence_ref'):
            raise ValueError('Every observation requires a registered constraint and retained evidence')
        if any(row.get(key) != episode[key] for key in required):
            raise ValueError('Foreign task or changed dependency cannot enter this episode')
        if row.get('applies') is not None and type(row['applies']) is not bool:
            raise ValueError('Constraint guard must be observed true, false, or unknown')
        if row.get('satisfied') is not None and type(row['satisfied']) is not bool:
            raise ValueError('Constraint postcondition must be true, false, or unknown')
        fact = copy.deepcopy(row)
        facts.append(fact)
        if row.get('applies') is None or row.get('applies') is True and row.get('satisfied') is None:
            unknowns.append({'observation_id': identity, 'reason': 'guard_or_postcondition_not_observed'})
        if row.get('applies') is True and row.get('satisfied') is False:
            violations.append(fact)
    blocked = set(episode.get('blocked_by', []))
    if blocked - by_id.keys():
        raise ValueError('Terminal blocking links must reference registered constraints')
    if blocked and (episode.get('outcome') != 'failed' or not episode.get('terminal_evidence_ref')):
        raise ValueError('Terminal blocking links require an owned failed-outcome receipt')
    candidates = []
    for violation in violations:
        later = [row for row in facts if row['sequence'] > violation['sequence']
                 and row['constraint_id'] == violation['constraint_id'] and row.get('applies') is True]
        recovered = any(row.get('satisfied') is True for row in later)
        violation['recovered'] = recovered
        if not recovered and violation['constraint_id'] in blocked:
            candidates.append(violation)
    critical = candidates[0] if candidates else None
    owner = by_id[critical['constraint_id']].get('owner', 'unknown') if critical else 'unknown'
    if owner not in OWNERS:
        raise ValueError('Constraint owner must be application, verifier, environment, or unknown')
    if not critical and episode.get('outcome') == 'failed':
        unknowns.append({'reason': 'no_evidenced_unrecovered_terminal_blocking_violation'})
    return {'schema': 'adaos.builder.failure_analysis.v1', 'episode': copy.deepcopy(episode),
            'facts': facts, 'violations': violations, 'critical_violation': critical,
            'classification': owner if critical else 'insufficient_evidence',
            'next_action': OWNERS[owner], 'confidence': 'observed_constraint' if critical else 'unknown',
            'hypotheses': [{'claim': row['claim'], 'status': 'unverified',
                            'evidence_refs': list(row.get('evidence_refs', []))} for row in hypotheses],
            'unknowns': unknowns, 'source_rewrite_authorized': False,
            'hidden_model_reasoning': 'not_observed'}


def qualify_replay(before, after, *, changed_factor, hypothesis):
    if changed_factor not in AXES or not hypothesis:
        raise ValueError('Replay requires one declared dependency factor and a hypothesis')
    required = ('task_id', 'execution_ref', 'outcome', 'terminal_evidence_ref', *AXES)
    if any(not row.get(key) for row in (before, after) for key in required):
        raise ValueError('Replay needs complete retained executions')
    changed = [key for key in AXES if before[key] != after[key]]
    if changed != [changed_factor] or before['task_id'] != after['task_id']:
        raise ValueError('Controlled replay must freeze task and every other dependency')
    if before['execution_ref'] == after['execution_ref']:
        raise ValueError('Replay requires a distinct immutable execution receipt')
    supported = before['outcome'] == 'failed' and after['outcome'] == 'passed'
    body = {'schema': 'adaos.builder.controlled_replay.v1', 'hypothesis': hypothesis,
            'before': copy.deepcopy(before), 'after': copy.deepcopy(after),
            'changed_factor': changed_factor, 'supported': supported,
            'claim_scope': 'this controlled execution pair; not general model reliability'}
    return {**body, 'digest': digest(body)}
