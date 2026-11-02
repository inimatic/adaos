"""One bounded source-authority rule shared by interpretation input/admission.

This does not infer a complete product specification. In particular a field
enumeration or example is data, not a requested display order. Unknown phrasing
must remain unproved rather than silently becoming a hard assertion.
"""
from __future__ import annotations

import re

from .task_spec import source_clauses

_GENERIC_COLLECTION = re.compile(
    r'(?:(?:the|one|single|existing|current|original)\s+)*(?:table|collection|list)'
    r'|(?:(?:существующая|текущая|исходная|единственная)\s+)*(?:таблица|коллекция|список)', re.I)


def reference_context(baseline):
    """Project identities, not records or new requirements, from the exact base."""
    from adaos.services.builder_context import digest
    if not baseline:
        return None
    document = baseline.get('document') if baseline.get('schema') == 'adaos.builder.semantic_compiler_view.v1' else baseline
    if baseline.get('schema') == 'adaos.builder.semantic_compiler_view.v1' and digest(document) != baseline.get('view_digest'):
        raise ValueError('Interpretation reference context requires the pinned compiler view')
    return {'schema': 'adaos.builder.interpretation_references.v1', 'baseline_digest': digest(baseline),
            'resources': [{'id': resource['id'], 'item_label': resource.get('item_label', {}),
                           'field_ids': [field['id'] for field in resource.get('fields', [])]}
                          for resource in document.get('resources', [])],
            'collections': [{'id': view['id'], 'resource_ref': view['resource_ref'],
                             'title': view.get('title', {}), 'field_refs': view.get('field_refs', [])}
                            for view in document.get('views', []) if view.get('role') == 'collection'],
            'policy': 'Read-only existing target identities, not extra user requirements or permission to redesign. '
                'For a generic existing-table selector use empty subject when this inventory has exactly one collection; '
                'otherwise resolve a source-named target or ask a material clarification. New named collections are not existing ones.'}


def normalize_generic_selectors(task, proposal):
    """Resolve a source-explicit generic selector only against a unique base.

    Raw proposals remain immutable. No arbitrary noun/name matching or first-
    collection fallback is permitted, and ambiguity stops at interpretation.
    """
    import copy
    context = task.get('reference_context')
    if not context:
        return proposal, []
    clauses = {row['id']: row['text'] for row in task['clauses']}
    result = copy.deepcopy(proposal)
    receipts = []
    for index, row in enumerate(result.get('constraints', [])):
        subject = row.get('subject', '').strip()
        if row.get('kind') not in {'view_title', 'field_order'} or not _GENERIC_COLLECTION.fullmatch(subject):
            continue
        from .source_relations import _unquoted
        cited = [clauses.get(ref, '') for ref in row.get('clause_ids', [])]
        pattern = r'(?<!\w)' + re.escape(subject) + r'(?!\w)'
        if not any(_unquoted(text)[match.start()] for text in cited for match in re.finditer(pattern, text, re.I)):
            continue
        owners = context['collections']
        if len(owners) != 1:
            from .task_interpretation import TaskInterpretationRejected
            raise TaskInterpretationRejected([{'code': 'task.display_selector_ambiguous', 'gate': 'G3',
                'owner': 'task_interpretation', 'source_clause_ids': row['clause_ids'],
                'detail': 'A generic existing-table selector needs one exact existing collection, not declaration order'}])
        row['subject'] = ''
        receipts.append({'kind': 'unique_existing_collection_selector', 'path': f'$.constraints[{index}].subject',
                         'from': subject, 'to': '', 'owner_ref': 'view:' + owners[0]['id'],
                         'baseline_digest': context['baseline_digest']})
    return result, receipts

_ORDER = re.compile(
    r'\b(?:in\s+(?:(?:this|that|the|their|its|following|specified|same|existing|relative)\s+)*order'
    r'|(?:column|field)s?\s+order|order\s+(?:the\s+)?(?:columns|fields)'
    r'|поряд(?:ок|ке|ка)\s+(?:столбцов|колонок|полей)'
    r'|в\s+(?:(?:этом|таком|следующем|указанном|прежнем)\s+)?порядке)\b', re.I)


def contract():
    return {
        'schema': 'adaos.builder.interpretation_boundary.v1',
        'mandatory': 'Only source-requested outcomes and exact admitted prior obligations are mandatory. '
            'Keep their owner, polarity, preconditions and evidence. A fixture value is a data fact, not a presentation decision.',
        'design_freedom': 'Unspecified reversible presentation choices remain design_freedom. '
            'Do not turn your preferred order, layout, control label or examples into a new user constraint. '
            'No material question is needed merely to choose an unspecified column order.',
        'field_order': 'Emit a field_order constraint ONLY when the cited source explicitly requires ordering. '
            'A list of source fields, record tuple order, Show all source fields in a table, or a collection introduction '
            'does not require that order. Omit that constraint and choose a valid design instead. '
            'For an explicit new ordering cite the clause with the ordering instruction AND all ordered field literals; '
            'do not cite an introduction or another collection. Retain prior accepted orders without re-inventing them.',
        'exact_display_labels': 'A mentioned concept such as Teams directory requires that concept, NOT the exact '
            'heading Teams directory. Emit view_title/field_label only for an explicit naming/caption directive, '
            'using source_display_directives as bounded lexical evidence. Otherwise keep the concept in its UI/collection '
            'requirement and choose its caption as a reversible design. Do not ask for a heading merely because it is unspecified.',
        'scope': 'This lexical admission rule qualifies explicit EN/RU field-order directives only; '
            'it is not general natural-language completeness or permission to discard other requirements.',
    }


def field_order_evidence(statement):
    from .source_relations import _unquoted
    from .source_operators import record_head_operator
    from .source_literals import _parts
    rows = []
    for clause in source_clauses(statement):
        text = clause['text']
        outside = _unquoted(text)
        for match in _ORDER.finditer(text):
            begin, end = next((a, b) for a, b in _parts(text, '.!?\n') if a <= match.start() < b)
            prefix = text[begin:match.start()]
            if not outside[match.start()] or record_head_operator(text[begin:end], source_start=clause['start'] + begin):
                continue
            if re.search(r'\b(?:example|e\.g\.|for\s+instance|if|when|пример|например|если|когда)\b', prefix, re.I):
                continue
            if re.search(r'\b(?:any|arbitrary|unspecified|no\s+required|любой|произвольн\w*)\s*$', prefix, re.I):
                continue
            if re.search(r'\b(?:arbitrary|optional|unspecified|not\s+(?:required|specified)|произвольн\w*|не\s+задан\w*)\b',
                         text[match.end():end], re.I):
                continue
            rows.append({'clause_id': clause['id'], 'operator': match.group(),
                         'start': clause['start'] + match.start(), 'end': clause['start'] + match.end()})
    return rows


def display_directive_evidence(statement):
    from .semantic_ui_constraints import source_targets
    from .source_relations import _unquoted, _label
    from .source_operators import record_head_operator
    from .source_literals import _parts
    rows = source_targets(statement)
    pattern = re.compile(
        r'\b(?:table|collection|view|list|directory|таблиц\w*|коллекци\w*|представлени\w*|спис\w*|справочник\w*)\s+'
        r'(?:titled|named|called|с\s+(?:названием|заголовком)|под\s+названием)\s+'
        r'(?P<literal>"(?:\\.|[^"\\])*"|«[^»]+»|“[^”]+”|`[^`]+`|[\w.-]+)', re.I)
    for clause in source_clauses(statement):
        text = clause['text']; outside = _unquoted(text)
        for match in pattern.finditer(text):
            begin, end = next((a, b) for a, b in _parts(text, '.!?\n') if a <= match.start() < b)
            if not outside[match.start()] or record_head_operator(text[begin:end], source_start=clause['start'] + begin):
                continue
            if re.search(r'\b(?:example|e\.g\.|if|when|например|если|когда)\b', text[begin:match.start()], re.I):
                continue
            rows.append({'kind': 'view_title', 'subject': '', 'values': [_label(match['literal'])],
                         'clause_ids': [clause['id']]})
    return rows


def findings(statement, proposal):
    source = {row['id']: row for row in source_clauses(statement)}
    evidence = {row['clause_id'] for row in field_order_evidence(statement)}
    display = display_directive_evidence(statement)
    result = []
    for index, row in enumerate(proposal.get('constraints', [])):
        if row.get('kind') in {'view_title', 'field_label'}:
            if not any(item['kind'] == row['kind'] and item['values'] == row['values']
                       and set(item['clause_ids']) <= set(row.get('clause_ids', [])) for item in display):
                result.append({'code': 'task.constraint_authority_unproved', 'gate': 'G3',
                    'owner': 'task_interpretation', 'path': f'$.constraints[{index}]',
                    'source_clause_ids': row.get('clause_ids', []),
                    'detail': row['kind'] + ' needs an explicit source naming/caption directive; '
                        'mentioning a concept is not a mandatory exact display label. Keep unspecified captions as design freedom.'})
            continue
        if row.get('kind') != 'field_order':
            continue
        cited = [source[identity]['text'] for identity in row.get('clause_ids', [])
                 if identity in evidence and identity in source]
        # Field literals must occur together in a clause actually requesting
        # ordering; joining an introduction to unrelated fixture clauses is not proof.
        if not any(all(re.search(r'(?<![\w.-])' + re.escape(str(value)) + r'(?![\w-]|\.\w)', text, re.I)
                           for value in row.get('values', [])) for text in cited):
            result.append({'code': 'task.constraint_authority_unproved', 'gate': 'G3',
                'owner': 'task_interpretation', 'path': f'$.constraints[{index}]',
                'source_clause_ids': row.get('clause_ids', []),
                'detail': 'field_order needs an explicit source ordering directive citing its field literals; '
                    'a field enumeration or record example is not mandatory display order. '
                    'Keep unspecified presentation as design_freedom, without dropping requested fields.'})
    return result
