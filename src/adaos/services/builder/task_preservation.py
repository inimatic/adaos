"""Core-derived writes for the narrow, explicitly preserved fixture amendment.

This is not a generic natural-language write-set selector. Broader UI/operation
changes keep their existing route and cannot claim this preservation proof.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping

from adaos.services.builder_context import digest
from .task_acceptance import _field, _same


def fixture_write_scope(previous, brief):
    contract = (brief or {}).get('task_contract') or {}
    if not contract or not previous:
        return None
    meaning = (contract.get('derivation') or {}).get('meaning_source_digest', contract['source']['digest'])
    rows = [row for row in contract['requirements'] if row['status'] == 'active'
            and row.get('source_digest') == meaning and row['kind'] != 'assertion']
    if (not rows or not any(row['verification_family'] == 'preservation' for row in rows)
            or any(row['kind'] not in {'data_state', 'authoring', 'policy', 'verification', 'exclusion'} for row in rows)
            or contract['design_freedom']
            or any(row.get('source_digest') == meaning for group in ('constraints', 'transitions') for row in contract[group])):
        return None
    superseded = {identity for row in rows for identity in row['supersedes']}
    if not superseded:
        return None
    prior = {row['id']: row for row in contract['requirements'] if row['id'] in superseded}
    if (len(prior) != len(superseded) or any(row['kind'] != 'assertion' or row.get('assertion_group') != 'facts'
            or row['status'] != 'superseded' for row in prior.values())):
        return None
    facts = [row for row in contract['facts'] if row.get('source_digest') == meaning]
    if len(facts) != len(prior):
        return None
    writes = []
    for identity, row in sorted(prior.items()):
        old = row['assertion']
        expected = [fact for fact in facts if (fact['subject'], fact['source_field']) == (old['subject'], old['source_field'])]
        if len(expected) != 1 or _same(old['value'], expected[0]['value']):
            return None
        fact = expected[0]
        targets = []
        for resource in previous.get('resources', []):
            field = _field(resource, fact['field']) or _field(resource, fact['source_field'])
            if field:
                targets.extend((resource, record, field) for record in resource.get('records', [])
                    if str(record['id']) == fact['subject'])
        if len(targets) != 1:
            from .task_interpretation import TaskInterpretationRejected
            raise TaskInterpretationRejected([{'code': 'task.fixture_scope_unresolved', 'gate': 'G4',
                'owner': 'capability_admission', 'detail': 'Fixture amendment requires one exact existing record and field'}])
        resource, record, field = targets[0]
        if not any(_same(record.get(field['id']), value) for value in (old['value'], fact['value'])):
            from .task_interpretation import TaskInterpretationRejected
            raise TaskInterpretationRejected([{'code': 'task.fixture_scope_stale', 'gate': 'G4',
                'owner': 'capability_admission', 'detail': 'The exact fixture baseline differs from the admitted old and new values'}])
        writes.append({'resource_ref': resource['id'], 'record_id': record['id'], 'field_ref': field['id'],
            'before': copy.deepcopy(record.get(field['id'])), 'after': copy.deepcopy(fact['value']),
            'superseded_assertion_ref': identity, 'requirement_ref': row['superseded_by']})
    return {'schema': 'adaos.builder.fixture_write_scope.v1', 'baseline_digest': digest(previous),
        'meaning_source_digest': meaning, 'writes': writes,
        'policy': 'Only these existing fixture values may change. Preserve all other semantic properties, IDs, list order, views, commands, guards and states. Requirement provenance is revalidated separately.'}


def fixture_revision_findings(previous, current, brief):
    scope = fixture_write_scope(previous, brief)
    if scope is None:
        return [], None
    expected = copy.deepcopy(dict(previous))
    for write in scope['writes']:
        resource = next(row for row in expected['resources'] if row['id'] == write['resource_ref'])
        record = next(row for row in resource['records'] if row['id'] == write['record_id'])
        record[write['field_ref']] = copy.deepcopy(write['after'])
    differences = []

    def compare(left, right, path):
        if type(left) is not type(right):
            differences.append(path)
        elif isinstance(left, Mapping):
            for key in sorted(left.keys() | right.keys()):
                if key not in left or key not in right:
                    differences.append(path + '/' + key)
                else:
                    compare(left[key], right[key], path + '/' + key)
        elif isinstance(left, list):
            if len(left) != len(right):
                differences.append(path)
            else:
                for index, (old, new) in enumerate(zip(left, right)):
                    compare(old, new, path + '/' + str(index))
        elif not _same(left, right):
            differences.append(path)

    # These fields describe source provenance, not executable UI. The full
    # compiler independently admits all requirement refs and their coverage.
    provenance = {'brief_ref', 'brief_digest', 'requirement_bindings'}
    compare({key: value for key, value in expected.items() if key not in provenance},
            {key: value for key, value in current.items() if key not in provenance}, '$')
    return [{'code': 'semantic.task_write_scope_exceeded', 'gate': 'G8', 'owner': 'candidate',
        'path': path, 'requirement_refs': sorted({row['requirement_ref'] for row in scope['writes']}),
        'detail': 'Fixture-only amendment changed a protected semantic property: ' + path}
        for path in differences], scope
