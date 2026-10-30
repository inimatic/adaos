"""Version literal assertions together with, not beside, the requirement ledger.

Internal assertion rows are not requests for widgets. Their source-bound IDs let
an amendment retire one exact expected value without deleting unrelated facts.
"""
from __future__ import annotations

import copy

from adaos.services.builder_context import canonical, digest

GROUPS = ('facts', 'transitions', 'constraints')
FAMILIES = {'facts': 'value', 'transitions': 'transition', 'constraints': 'layout'}


def assertion_row(group, assertion, source_digest):
    payload = copy.deepcopy(assertion)
    payload.pop('assertion_ref', None)
    payload.pop('source_digest', None)
    identity = 'assertion:' + digest({'group': group, 'source': source_digest, 'payload': payload})[7:31]
    family = {'search_fields': 'query', 'locale': 'locale', 'forbid_external': 'policy'}.get(payload.get('kind'), FAMILIES[group])
    return {'id': identity, 'kind': 'assertion', 'status': 'active', 'operation': None,
        'verification_family': family, 'clause_ids': copy.deepcopy(payload.get('clause_ids', [])),
        'source_digest': source_digest, 'statement': canonical({group: payload}), 'supersedes': [],
        'assertion_group': group, 'assertion': payload}


def assertion_ledger(contract):
    """Upgrade exact, previously admitted literals without reinterpreting prose."""
    ledger = copy.deepcopy(contract.get('requirements') or [])
    known = {row['id'] for row in ledger}
    for group in GROUPS:
        for assertion in contract.get(group, []):
            if assertion.get('assertion_ref') in known:
                continue
            row = assertion_row(group, assertion, assertion.get('source_digest') or contract['source']['digest'])
            if row['id'] not in known:
                ledger.append(row)
                known.add(row['id'])
    return ledger


def _slot(group, payload):
    if group == 'facts':
        return (str(payload['subject']).casefold(), str(payload.get('source_field', payload['field'])).casefold())
    if group == 'constraints':
        # Exact search scope is exclusive. Required titles, widths, locales and
        # disclosure text are additive obligations, not competing state values.
        return (payload['kind'], payload.get('subject', '').casefold(),
                None if payload['kind'] == 'search_fields' else canonical(sorted(payload['values'])))
    return (payload['command_label'].casefold(), payload['field'].casefold())


def _meaning(group, payload):
    keys = {'facts': ('value',), 'constraints': ('values',),
        'transitions': ('from_value', 'to_value', 'preserve_other_fields')}[group]
    return canonical({key: payload.get(key) for key in keys})


def consolidate_assertions(ledger, additions, source_digest):
    """Reject conflicting active assertions; only explicit supersession retires one."""
    ledger = copy.deepcopy(ledger)
    active = {row['id'] for row in ledger if row['status'] == 'active'}
    # A command transition belongs to its exact behavioral requirement as well
    # as to its literal assertion identity. Retiring that command retires its
    # expected effect, not another command's state assertion.
    for row in ledger:
        if (row['kind'] == 'assertion' and row['status'] == 'active'
                and row['assertion_group'] == 'transitions'
                and row['assertion']['requirement_id'] not in active):
            row['status'] = 'superseded'
            row['transition_source'] = source_digest
    retained = {group: {} for group in GROUPS}
    for row in ledger:
        if row['kind'] == 'assertion' and row['status'] == 'active':
            group, payload = row['assertion_group'], row['assertion']
            slot = _slot(group, payload)
            prior = retained[group].get(slot)
            if prior and _meaning(group, prior['assertion']) != _meaning(group, payload):
                raise ValueError('Conflicting retained literal assertions require an explicit amendment')
            retained[group][slot] = row
    conflicts = []
    for group in GROUPS:
        for payload in additions.get(group, []):
            slot = _slot(group, payload)
            prior = retained[group].get(slot)
            if prior:
                if _meaning(group, prior['assertion']) != _meaning(group, payload):
                    conflicts.append({'code': 'task.assertion_conflict', 'gate': 'G3', 'owner': 'task_interpretation',
                        'detail': 'Amend the exact prior assertion before changing its expected value',
                        'assertion_ref': prior['id'], 'assertion_group': group})
                continue
            row = assertion_row(group, payload, source_digest)
            ledger.append(row)
            retained[group][slot] = row
    projection = {group: [{**copy.deepcopy(row['assertion']), 'assertion_ref': row['id'],
        'source_digest': row['source_digest']} for row in values.values()] for group, values in retained.items()}
    return ledger, projection, conflicts
