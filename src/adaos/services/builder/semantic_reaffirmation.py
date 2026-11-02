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


def preservation_targets(statement, context):
    """Bounded independent source constructors, not model-proposed dispositions.

    Other preservation wording can retain ordinary history, but cannot waive a
    new requirement's proof through an ungrounded reaffirms relation.
    """
    from .interpretation_boundary import _SCOPED_SELECTOR, resolve_reference, resolve_feature_reference
    from .source_relations import _unquoted
    outside = _unquoted(statement)
    if re.fullmatch(r'(?:keep|preserve|retain)\s+all\s+(?:the\s+)?(?:existing|current|original)\s+'
                    r'(?:behavior|behaviour|features|requirements)(?:\s+unchanged)?[.!]?\s*', statement, re.I):
        return {ref for row in context.get('reaffirmation_candidates', []) for ref in row['semantic_refs']}
    targets, view_owners = set(), set()
    consumed = []
    head = re.match(r'^(?:keep|preserve|retain|сохрани\w*|остав\w*)\b', statement, re.I)
    if head: consumed.append(head.span())
    for match in _SCOPED_SELECTOR.finditer(statement):
        if not outside[match.start()]: continue
        kind = match['base'].split()[-1].casefold()
        named = re.match(r'\s+(?:named\s+)?["«“]([^"»”]+)["»”]', statement[match.end():], re.I)
        consumed.append((match.start(), match.end() + (named.end() if named else 0)))
        owner = resolve_reference(context, role=kind if kind in {'editor', 'details'} else 'collection',
            presentation=kind if kind not in {'collection', 'editor', 'details'} else None,
            section=match['section'], resource=match['resource'], name=named[1] if named else None)
        targets.add(owner['ref']); view_owners.add(owner['ref'])
    for match in re.finditer(r'\b(search|filter|query|command|state)\b', statement, re.I):
        if not outside[match.start()]: continue
        named = re.match(r'\s+(?:named\s+)?["«“]([^"»”]+)["»”]', statement[match.end():], re.I)
        if match[1].casefold() in {'command', 'state'} and named is None:
            return set()  # Stored-state prose is not a named fixture proof.
        consumed.append((match.start(), match.end() + (named.end() if named else 0)))
        owner = resolve_feature_reference(context, kind=match[1].casefold(), name=named[1] if named else None,
            owner_view_ref=next(iter(view_owners)) if len(view_owners) == 1 else None)
        targets.add(owner['ref'])
    residual = ''.join(' ' if any(start <= index < end for start, end in consumed) else char
                       for index, char in enumerate(statement))
    residual = re.sub(r'\b(?:the|existing|current|original|and|on|unchanged)\b|\bas\s+before\b|\bwithout\s+changes\b', '', residual, flags=re.I)
    if re.search(r'\w', residual):
        return set()  # A recognized noun cannot hide another unparsed obligation.
    return targets


def document_of(baseline):
    if baseline.get('schema') == 'adaos.builder.semantic_compiler_view.v1':
        if digest(baseline.get('document')) != baseline.get('view_digest'):
            raise ValueError('Reaffirmation requires the exact pinned compiler view')
        return baseline['document']
    return baseline


def preservation_source_locations(task, statement, clause_ids):
    """The same standalone/unquoted authority rule for discovery and admission."""
    from .source_relations import _unquoted
    locations = []
    for clause in task['clauses']:
        if clause['id'] not in clause_ids:
            continue
        text = clause['text']; outside = _unquoted(text)
        for quote in re.finditer(re.escape(statement), text):
            boundaries = [index for index, char in enumerate(text[:quote.start()]) if char in '.!?' and outside[index]]
            prefix = text[(boundaries[-1] + 1) if boundaries else 0:quote.start()]
            if outside[quote.start()] and re.fullmatch(r'\s*(?:(?:[-*+]|\d+[.)])\s+)?', prefix):
                locations.append((clause['id'], quote.start()))
    return locations


def source_reaffirmation_options(task, previous_brief=None):
    """Advertise reuse only where this source has an admissible constructor.

    This is an optional proof-reuse capability, not a parser for all preservation
    prose. Unrecognized wording retains ordinary history with reaffirms=[].
    No new authority, semantic binding or expected result is inferred here.
    """
    from .source_relations import _unquoted
    from .task_interpretation import TaskInterpretationRejected
    context = (task or {}).get('reference_context') or {}
    candidates = context.get('reaffirmation_candidates', [])
    prior = (previous_brief or {}).get('task_contract') or {}
    if not prior or (prior.get('source') or {}).get('authority') != (task or {}).get('authority'):
        return []
    active = {row['id'] for row in prior.get('requirements', []) if row['status']=='active'}
    candidates = [row for row in candidates if row['requirement_ref'] in active]
    if not candidates:
        return []
    result = []
    for clause in task['clauses']:
        text = clause['text']; outside = _unquoted(text); start = 0
        ends = [index + 1 for index, char in enumerate(text) if char in '.!?' and outside[index]]
        for end in [*ends, len(text)]:
            statement = re.sub(r'^\s*(?:(?:[-*+]|\d+[.)])\s+)?', '', text[start:end]).strip()
            start = end
            if not re.match(r'^(?:keep|preserve|retain|сохрани\w*|остав\w*)\b', statement, re.I):
                continue
            if len(preservation_source_locations(task, statement, [clause['id']])) != 1:
                continue
            try:
                targets = preservation_targets(statement, context)
            except TaskInterpretationRejected:
                continue  # Ambiguous optional reuse cannot waive a proof.
            eligible = [row for row in candidates if targets.intersection(row['semantic_refs'])]
            if targets and targets <= {ref for row in eligible for ref in row['semantic_refs']}:
                result.append({'source_clause': clause['id'], 'statement': statement,
                    'source_targets': sorted(targets),
                    'candidate_requirement_refs': [row['requirement_ref'] for row in eligible]})
    return result


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
    source_options = source_reaffirmation_options(task, previous_brief)
    result = []
    for row in requirements:
        refs = row.get('reaffirms', [])
        if not refs:
            continue
        locations = preservation_source_locations(task, row['statement'], row['clause_ids'])
        if len(locations) != 1:
            raise TaskInterpretationRejected([{'code': 'task.reaffirmation_source_authority_unproved', 'gate': 'G3',
                'owner': 'task_interpretation', 'detail': 'Reaffirmation needs one unquoted standalone positive source sentence; a shortened quote cannot erase a condition, negation, example or data frame'}])
        if (row['kind'] != 'behavior' or row['operation'] is not None
                or row['verification_family'] != 'preservation' or row['supersedes']
                or len(refs) != len(set(refs)) or not set(refs) <= active & available.keys()
                or not re.match(r'^(?:keep|preserve|retain|сохрани\w*|остав\w*)\b', row['statement'], re.I)
                or re.search(r'\b(?:but|except|instead|но|кроме)\b', row['statement'], re.I)
                or previous['source']['authority'] != task['authority']):
            raise TaskInterpretationRejected([{'code': 'task.reaffirmation_unproved', 'gate': 'G3',
                'owner': 'task_interpretation', 'detail': 'Reaffirmation requires a positive preservation quote, exact active prior owners and unchanged authority; amendments need new requirements'}])
        protected = {ref for identity in refs for ref in available[identity]['semantic_refs']}
        targets = preservation_targets(row['statement'], task.get('reference_context') or {})
        complete_source = any(option['source_clause'] in row['clause_ids']
            and option['statement'].startswith(row['statement'].rstrip('.!?'))
            and set(option['source_targets']) == targets for option in source_options)
        if not targets or not targets <= protected or not complete_source:
            raise TaskInterpretationRejected([{'code': 'task.reaffirmation_reference_unproved', 'gate': 'G3',
                'owner': 'task_interpretation', 'detail': 'The current preservation quote must independently identify owners protected by the selected prior closures. Use reaffirms=[] and ordinary history when the source constructor is unresolved; never waive another target proof.'}])
        result.append({'requirement_ref': current_ids[row['id']], 'source_digest': task['source']['digest'],
            'clause_ids': copy.deepcopy(row['clause_ids']), 'statement': row['statement'],
            'source_targets': sorted(targets), 'reference_rule': 'preservation_reference_constructor.v1',
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
