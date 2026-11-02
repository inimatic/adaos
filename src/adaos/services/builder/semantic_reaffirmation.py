"""Explicit source-to-prior-obligation lineage, not inferred behavioral equivalence.

The existing canonical graph supplies exact dependency closure. Reuse is
conservative: a changed owner/effect needs a new admission, never an auto-binding.
No renderer source, runtime record or caption heuristic grants this authority.
"""
from __future__ import annotations

import copy
import re

from adaos.services.builder_context import canonical, digest
from .semantic_graph_model import semantic_document_to_graph, semantic_graph_dependency_closure


def document_of(baseline):
    if baseline.get('schema') == 'adaos.builder.semantic_compiler_view.v1':
        if digest(baseline.get('document')) != baseline.get('view_digest'):
            raise ValueError('Reaffirmation requires the exact pinned compiler view')
        return baseline['document']
    return baseline


def requirement_closure(document, requirement_ref):
    return _requirement_closure(semantic_document_to_graph(document), requirement_ref)


def _requirement_closure(graph, requirement_ref):
    roots = [node['ref'] for node in graph['nodes'] if node['kind'] == 'requirement_binding'
             and node['payload']['requirement_ref'] == requirement_ref]
    if len(roots) != 1:
        raise ValueError('Reaffirmation requires one exact prior semantic binding')
    refs = set(semantic_graph_dependency_closure(graph, roots))
    nodes = [{'ref': row['ref'], 'kind': row['kind'], 'payload': row['payload']}
             for row in graph['nodes'] if row['ref'] in refs]
    edges = [{key: value for key, value in row.items() if key != 'source_path'}
             for row in graph['edges'] if row['dependency'] and row['source'] in refs]
    return {'semantic_refs': sorted(refs), 'closure_digest': digest({
        'nodes': sorted(nodes, key=lambda row: row['ref']), 'edges': sorted(edges, key=canonical)})}


def candidates(baseline, previous_brief):
    if not baseline or not (previous_brief or {}).get('task_contract'):
        return []
    document = document_of(baseline)
    if document.get('schema') != 'adaos.webui.semantic.v2':
        # Legacy/locale-only context can guide interpretation, but cannot
        # manufacture a semantic proof or a dependency-closed reuse candidate.
        return []
    graph = semantic_document_to_graph(document)
    active = {row['id'] for row in previous_brief['task_contract']['requirements']
              if row['status'] == 'active' and row['kind'] != 'assertion'}
    return [{'requirement_ref': row['requirement_ref'],
             'baseline_digest': digest(baseline), **_requirement_closure(graph, row['requirement_ref'])}
            for row in document.get('requirement_bindings', []) if row['requirement_ref'] in active]


def admit_reaffirmations(task, requirements, previous_brief, current_ids):
    """An explicit positive preservation directive can reaffirm exact active refs."""
    from .task_interpretation import TaskInterpretationRejected
    available = {row['requirement_ref']: row for row in
                 (task.get('reference_context') or {}).get('reaffirmation_candidates', [])}
    previous = (previous_brief or {}).get('task_contract') or {}
    active = {row['id'] for row in previous.get('requirements', []) if row['status'] == 'active'}
    result = []
    for row in requirements:
        refs = row.get('reaffirms', [])
        if not refs:
            continue
        if (row['kind'] != 'behavior' or row['operation'] is not None
                or row['verification_family'] != 'preservation' or row['supersedes']
                or len(refs) != len(set(refs)) or not set(refs) <= active & available.keys()
                or not re.match(r'^(?:keep|preserve|retain|сохрани\w*|остав\w*)\b', row['statement'], re.I)
                or re.search(r'\b(?:but|except|instead|но|кроме)\b', row['statement'], re.I)
                or previous['source']['authority'] != task['authority']):
            raise TaskInterpretationRejected([{'code': 'task.reaffirmation_unproved', 'gate': 'G3',
                'owner': 'task_interpretation', 'detail': 'Reaffirmation requires a positive preservation quote, exact active prior owners and unchanged authority; amendments need new requirements'}])
        result.append({'requirement_ref': current_ids[row['id']], 'source_digest': task['source']['digest'],
            'clause_ids': copy.deepcopy(row['clause_ids']), 'statement': row['statement'],
            'previous_brief_ref': previous_brief['brief_id'], 'previous_brief_digest': previous_brief['digest'],
            'prior_requirements': [copy.deepcopy(available[ref]) for ref in refs]})
    return result


def candidate_findings(document, brief, previous, *, require_baseline=True):
    relations = ((brief or {}).get('task_contract') or {}).get('reaffirmations', [])
    if not relations:
        return []
    findings = []
    try:
        current_graph = semantic_document_to_graph(document)
        previous_graph = semantic_document_to_graph(document_of(previous)) if previous is not None else None
    except (ValueError, KeyError):
        current_graph = previous_graph = None
    for relation in relations:
        for prior in relation['prior_requirements']:
            try:
                if (current_graph is None or require_baseline and (
                        previous_graph is None or digest(previous) != prior['baseline_digest']
                        or _requirement_closure(previous_graph, prior['requirement_ref'])['closure_digest'] != prior['closure_digest'])
                        or _requirement_closure(current_graph, prior['requirement_ref'])['closure_digest'] != prior['closure_digest']):
                    raise ValueError('Changed or absent reaffirmation closure')
            except (ValueError, KeyError):
                findings.append({'code': 'task.reaffirmation_changed', 'gate': 'G8', 'owner': 'semantic_compiler',
                    'requirement_ref': relation['requirement_ref'], 'prior_requirement_ref': prior['requirement_ref'],
                    'detail': 'Reaffirmed owner/effect closure or its exact base changed; no proof is auto-attached'})
    return findings
