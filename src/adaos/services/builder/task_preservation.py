"""Core-derived writes for the narrow, explicitly preserved fixture amendment.

This is not a generic natural-language write-set selector. Broader UI/operation
changes keep their existing route and cannot claim this preservation proof.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping

from adaos.services.builder_context import digest
from .task_acceptance import _same


def fixture_write_scope(previous, brief):
    contract = (brief or {}).get('task_contract') or {}
    if not contract or not previous:
        return None
    previous_basis=previous
    if previous.get('schema') == 'adaos.builder.semantic_compiler_view.v1':
        document = previous.get('document')
        if not isinstance(document, Mapping) or digest(document) != previous.get('view_digest'):
            from .task_interpretation import TaskInterpretationRejected
            raise TaskInterpretationRejected([{'code':'task.fixture_scope_baseline_invalid','gate':'G4',
                'owner':'capability_admission','detail':'The SDK compiler-view identity does not match its document'}])
        previous = document
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
    from .source_field_bindings import resolve_fields, slot
    source_targets,source_findings=resolve_fields(previous,contract,previous=previous_basis)
    if source_findings:
        from .task_interpretation import TaskInterpretationRejected
        raise TaskInterpretationRejected([{'code':'task.fixture_scope_unresolved','gate':'G4',
            'owner':'capability_admission','detail':'Retained source fields require explicit binding migration before fixture amendment'}])
    writes = []
    for identity, row in sorted(prior.items()):
        old = row['assertion']
        expected = [fact for fact in facts if (fact['subject'], fact['source_field']) == (old['subject'], old['source_field'])]
        if len(expected) != 1 or _same(old['value'], expected[0]['value']):
            return None
        fact = expected[0]
        targets=[(target['resource'],target['record'],target['field']) for target in source_targets
            if slot(target['fact'])==slot(fact) and str(target['record']['id'])==fact['subject']]
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
            'state_proof_id': 'fixture-proof-' + digest({'source':meaning,'resource':resource['id'],
                'record':record['id'],'field':field['id']})[7:23],
            'superseded_assertion_ref': identity, 'requirement_ref': row['superseded_by']})
    return {'schema': 'adaos.builder.fixture_write_scope.v1', 'baseline_digest': digest(previous),
        'meaning_source_digest': meaning, 'writes': writes,
        'evidence_extension': 'At most one new field_predicate proof per write, selecting exactly its existing record in an unchanged existing view and exposing the changed visible field. Existing state proofs are immutable.',
        'policy': 'Only these existing fixture values may change. Preserve all other semantic properties, IDs, list order, views, commands and guards. Only the bounded evidence extension and separately revalidated requirement provenance are exempt.'}


def fixture_revision_findings(previous, current, brief):
    scope = fixture_write_scope(previous, brief)
    if scope is None:
        return [], None
    if previous.get('schema')=='adaos.builder.semantic_compiler_view.v1':
        previous=previous['document']
    expected = copy.deepcopy(dict(previous))
    for write in scope['writes']:
        resource = next(row for row in expected['resources'] if row['id'] == write['resource_ref'])
        record = next(row for row in resource['records'] if row['id'] == write['record_id'])
        record[write['field_ref']] = copy.deepcopy(write['after'])
    comparable = copy.deepcopy(dict(current))
    old_states = {row['id'] for row in previous.get('representative_states', [])}
    additions = [row for row in comparable.get('representative_states', []) if row['id'] not in old_states]
    used = set()
    allowed = set()
    from .semantic_prototype import _matching_state_records
    for state in additions:
        proof = state.get('proof') or {}
        if (proof.get('kind') != 'field_predicate' or state.get('min_items') != 1 or state.get('max_items') != 1
                or not state.get('filters')):
            continue
        matches = []
        for index, write in enumerate(scope['writes']):
            view = next((row for row in previous.get('views', []) if row['id'] == state.get('view_ref')
                and row['resource_ref'] == write['resource_ref']), None)
            visible = proof.get('visible_field_refs') or []
            if (not view or write['field_ref'] not in visible or not set(visible) <= set(view.get('field_refs', []))):
                continue
            records = next(row['records'] for row in expected['resources'] if row['id'] == write['resource_ref'])
            selected = _matching_state_records(records, state['filters'])
            if [row['id'] for row in selected] == [write['record_id']]:
                matches.append(index)
        if len(matches) == 1 and matches[0] not in used:
            used.add(matches[0])
            allowed.add(state['id'])
    if allowed:
        comparable['representative_states'] = [row for row in comparable['representative_states'] if row['id'] not in allowed]
    differences = []

    def compare(left, right, path):
        numeric = lambda value: type(value) in (int, float)
        if type(left) is not type(right) and not (numeric(left) and numeric(right)):
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
            {key: value for key, value in comparable.items() if key not in provenance}, '$')
    return [{'code': 'semantic.task_write_scope_exceeded', 'gate': 'G8', 'owner': 'candidate',
        'path': path, 'requirement_refs': sorted({row['requirement_ref'] for row in scope['writes']}),
        'detail': 'Fixture-only amendment changed a protected semantic property: ' + path}
        for path in differences], scope


def fixture_provider_contract(schema, previous, scope):
    """Close scoped declaration/evidence IDs before asking the model to bind."""
    if not scope:
        return copy.deepcopy(schema)
    previous = previous.get('document',previous)
    value = copy.deepcopy(schema)
    identities = {kind:[row['id'] for row in previous.get(key,[])] for kind,key in (
        ('resource','resources'),('relationship','relationships'),('view','views'),('command','commands'),
        ('surface','capability_surfaces'),('state','representative_states'))}
    identities['state'] += [row['state_proof_id'] for row in scope['writes']]
    identities['field'] = [field['id'] for resource in previous['resources'] for field in resource['fields']]
    identities['query'] = [query['id'] for view in previous['views'] for query in (view.get('query_controls') or [])]
    definitions = value['$defs']
    definitions['semanticRef'] = {'anyOf':[{'type':'object','additionalProperties':False,
        'required':['kind','id'],'properties':{'kind':{'type':'string','enum':[kind]},
            'id':{'type':'string','enum':sorted(set(ids))}}} for kind,ids in identities.items() if ids]}
    def pin(node,ids):
        if 'properties' in node and 'id' in node['properties']:
            node['properties']['id']={'type':'string','enum':sorted(set(ids))}
        for branch in node.get('anyOf',[]):pin(branch,ids)
    for kind,key,definition in (('resource','resources','resource'),('relationship','relationships','relationship'),
            ('view','views','view'),('command','commands','command'),('surface','capability_surfaces','capabilitySurface'),
            ('state','representative_states','representativeState')):
        if identities[kind]:pin(definitions[definition],identities[kind])
        else:value['properties'][key]['maxItems']=0
    pin(definitions['field'],identities['field'])
    if identities['query']:pin(definitions['queryControl'],identities['query'])
    value['properties']['document_id']={'type':'string','enum':[previous['document_id']]}
    return value
