"""One bounded source-authority rule shared by interpretation input/admission.

This does not infer a complete product specification. In particular a field
enumeration or example is data, not a requested display order. Unknown phrasing
must remain unproved rather than silently becoming a hard assertion.
"""
from __future__ import annotations

import re

from .task_spec import source_clauses

_ORDER = re.compile(
    r'\b(?:in\s+(?:(?:this|that|the|following|specified|same|existing|relative)\s+)*order'
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
        'scope': 'This lexical admission rule qualifies explicit EN/RU field-order directives only; '
            'it is not general natural-language completeness or permission to discard other requirements.',
    }


def field_order_evidence(statement):
    from .source_relations import _unquoted
    from .source_operators import record_head_operator
    rows = []
    for clause in source_clauses(statement):
        text = clause['text']
        outside = _unquoted(text)
        for match in _ORDER.finditer(text):
            prefix = text[:match.start()]
            if not outside[match.start()] or record_head_operator(text, source_start=clause['start']):
                continue
            if re.search(r'\b(?:example|e\.g\.|for\s+instance|if|when|пример|например|если|когда)\b', prefix, re.I):
                continue
            if re.search(r'\b(?:any|arbitrary|unspecified|no\s+required|любой|произвольн\w*)\s*$', prefix, re.I):
                continue
            if re.search(r'\b(?:arbitrary|optional|unspecified|not\s+(?:required|specified)|произвольн\w*|не\s+задан\w*)\b',
                         text[match.end():], re.I):
                continue
            rows.append({'clause_id': clause['id'], 'operator': match.group(),
                         'start': clause['start'] + match.start(), 'end': clause['start'] + match.end()})
    return rows


def findings(statement, proposal):
    source = {row['id']: row for row in source_clauses(statement)}
    evidence = {row['clause_id'] for row in field_order_evidence(statement)}
    result = []
    for index, row in enumerate(proposal.get('constraints', [])):
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
