"""Bounded source-owned disclosure, association and timestamp-label evidence.

No candidate or model answer supplies expectations. This is NOT a general prose
interpreter; unsupported/ambiguous recognized forms stay explicit and blocking.
"""
from __future__ import annotations

import re

from adaos.services.builder_context import digest
from .task_spec import source_clauses

GRAMMAR = 'source-grammar:record-obligations-v3'
SOURCE_TABLE_STATEMENTS = frozenset({'show all source fields in a table.', 'покажи все исходные поля в таблице.'})
_ID = r'[A-Za-z][A-Za-z0-9_.-]*'
_LINK = re.compile(rf'(?<![\w.-])(?P<subject>{_ID})\s+(?:относится\s+к|belongs\s+to|relates\s+to|для|for)\s+'
    rf'(?:(?P<target_kind>[\w-]+)\s+)?(?P<target>{_ID})(?=\s*[:;,!?]|\s*\.(?:\s|$)|\s*$)', re.I)
_NOTICE = re.compile(r'\b(?:show|display|покажи|отобрази)\s+(?:[\w-]+\s+){0,3}'
    r'(?:disclosure|notice|warning|explanation|пояснение|предупреждение|надпись)\s+'
    r'(?P<literal>"(?:\\.|[^"\\])*"|«[^»]+»|“[^”]+”|`[^`]+`)', re.I)
_DATE_MEANING = re.compile(r'\b(?:это|this\s+is|it\s+is)\s+(?P<positive>[^,;.!?]+),\s*'
    r'(?:а\s+не|not)\s+(?P<negative>[^;.!?]+)', re.I)


def source_table_field_scope(contract, constraint):
    """Freeze the exact instruction's source-field identities, not future facts.

    Assertion history retains the original source slots even after a value is
    explicitly superseded. Current values may change; their old projection
    obligation does not disappear. No resource/field is inferred from a caption.
    """
    source = constraint.get('source_digest') or contract['source']['digest']
    facts = [row['assertion'] for row in contract.get('requirements', [])
             if row.get('assertion_group') == 'facts' and row.get('source_digest') == source]
    facts += [row for row in contract.get('facts', [])
              if (row.get('source_digest') or contract['source']['digest']) == source]
    return {(row['subject'], row.get('source_field', row['field'])) for row in facts}


def _fold(value):
    return re.sub(r'\s+', ' ', value).strip().casefold()


def association_heads(text):
    """Explicit ASCII identities and one EN/RU ownership qualifier only."""
    from .source_relations import _unquoted
    outside = _unquoted(text)
    return [match for match in _LINK.finditer(text) if outside[match.start()]
        and (any(char.isdigit() for char in match['subject']) or '-' in match['subject']
             or len(match['subject'])>1 and match['subject'].isupper())]


def inventory(statement, source_digest):
    from .source_literals import _parts, source_literal_inventory
    from .source_relations import _unquoted, _label
    from .source_operators import record_head_operator
    literals = source_literal_inventory(statement, source_digest)
    atoms, unresolved = [], []
    for clause in source_clauses(statement):
        text = clause['text']
        if _fold(text) in SOURCE_TABLE_STATEMENTS:
            row = {'kind':'source_record_table','subject':'','values':['table'],
                   'clause_ids':[clause['id']],'start':clause['start'],'end':clause['end']}
            if literals['atoms']:
                atoms.append(row)
            else:
                unresolved.append({**row,'reason':'source_table_fact_scope_unresolved'})
        for begin, end in _parts(text, '.!?\n'):
            sentence = text[begin:end]
            base = clause['start'] + begin
            for match in association_heads(sentence):
                row = {'kind':'record_association', 'subject':match['subject'], 'values':[match['target']],
                    'target_kind':match['target_kind'] or '',
                    'clause_ids':[clause['id']], 'start':base+match.start(), 'end':base+match.end()}
                if record_head_operator(sentence[:match.end()], source_start=base):
                    unresolved.append({**row,'reason':'association_polarity_unresolved'})
                else:
                    atoms.append(row)
        outside = _unquoted(text)
        for match in _NOTICE.finditer(text):
            if not outside[match.start()]:
                continue
            row = {'kind':'visible_text', 'subject':'', 'values':[_label(match['literal'])],
                'clause_ids':[clause['id']], 'start':clause['start']+match.start(), 'end':clause['start']+match.end()}
            # Do not promote a negated display instruction to visible content.
            prefix = text[max(0, text.rfind('.',0,match.start())+1):match.end()]
            if record_head_operator(prefix, source_start=clause['start']):
                unresolved.append({**row,'reason':'disclosure_polarity_unresolved'})
            else:
                atoms.append(row)
        for match in _DATE_MEANING.finditer(text):
            if not outside[match.start()]:
                continue
            positive, negative = _fold(match['positive']), _fold(match['negative'])
            from .timestamp_labels import classify_caption, label_policy
            recognized = classify_caption(positive)
            if recognized['status'] != 'recognized':
                continue
            dates = [row for row in literals['atoms'] if row['clause_id']==clause['id']
                and row['start'] < clause['start']+match.start()
                and re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['evidence'].split()[-1].strip('"«»'))]
            row = {'kind':'field_display_meaning', 'values':[positive], 'excluded_values':[negative],
                'meaning':recognized['meaning'], 'label_policy':label_policy(),
                'clause_ids':[clause['id']], 'start':clause['start']+match.start(), 'end':clause['start']+match.end()}
            if len(dates)!=1:
                unresolved.append({**row,'reason':'timestamp_field_target_unresolved'})
            else:
                atoms.append({**row,'subject':dates[0]['subject'],'source_field':dates[0]['source_field']})
    for row in atoms + unresolved:
        row['source_grammar_ref'] = GRAMMAR
        row['source_expectation_ref'] = 'source-obligation:' + digest({'source':source_digest, **row})[7:31]
    from .source_literals import MAX_INVENTORY_ATOMS, MAX_INVENTORY_BYTES, _context_failure
    from adaos.services.builder_context import canonical
    if len(atoms)+len(unresolved)>MAX_INVENTORY_ATOMS or len(canonical([atoms,unresolved]).encode('utf-8'))>MAX_INVENTORY_BYTES:
        _context_failure('capacity','The complete source-obligation inventory exceeds its declared capacity; no truncation is allowed')
    return {'schema':'adaos.builder.source_obligations.v1', 'source_digest':source_digest,
        'grammar_ref':GRAMMAR, 'atoms':atoms, 'unresolved':unresolved,
        'supported_scope':'Explicit ASCII record IDs containing digits/hyphens or all-uppercase IDs; one EN/RU '
            'ownership qualifier; directly requested quoted notices; one ISO date field and an EN/RU creation/verification label contrast; '
            'the exact Show all source fields in a table / Покажи все исходные поля в таблице statement '
            'scoped to the literal fact slots of that source request, not future requests; a missing fact scope requires clarification. '
            'Core derives association/display-meaning constraints; other prose remains unproved, not certified complete.',
        'general_source_completeness_proven':False}


def interpretation_findings(statement, source_digest, proposal):
    source = inventory(statement, source_digest)
    findings = [{'code':'task.source_obligation_scope_unresolved','gate':'G3','owner':'task_interpretation',
        'detail':row['reason'], 'source_clause_ids':row['clause_ids'],
        'source_expectation_ref':row['source_expectation_ref']} for row in source['unresolved']]
    for atom in source['atoms']:
        if atom['kind'] != 'visible_text':
            continue  # Relations/label meanings are derived Core assertions, not invented proposal fields.
        if not any(row['kind']=='visible_text' and set(atom['clause_ids']) <= set(row['clause_ids'])
                and set(atom['values']) <= set(row['values']) for row in proposal.get('constraints', [])):
            findings.append({'code':'task.source_disclosure_unextracted','gate':'G3','owner':'task_interpretation',
                'source_clause_ids':atom['clause_ids'],'source_expectation_ref':atom['source_expectation_ref'],
                'detail':'An explicit visible disclosure is missing from typed source constraints, not missing user knowledge'})
    return findings


def derived_constraints(statement, source_digest):
    return [row for row in inventory(statement, source_digest)['atoms']
        if row['kind'] in {'record_association','field_display_meaning','source_record_table'}]


def effective_constraints(contract):
    """Recheck old source as well; never trust an old 'admitted' completeness bit."""
    source = contract['source']
    result = list(contract.get('constraints', []))
    for row in derived_constraints(source['statement'], source['digest']):
        if not any(all(item.get(key)==row.get(key) for key in ('kind','subject','values','source_field')) for item in result):
            result.append(row)
    return result


def contract_obligation_findings(contract, *, require_derived=False):
    source=contract['source']
    findings=interpretation_findings(source['statement'],source['digest'],contract)
    if require_derived:
        for expected in derived_constraints(source['statement'],source['digest']):
            keys = ('kind','subject','values','source_field') + (('meaning','label_policy')
                if expected['kind']=='field_display_meaning' else ())
            if not any(all(row.get(key)==expected.get(key) for key in keys)
                    for row in contract.get('constraints', [])):
                findings.append({'code':'task.source_expectations_readmission_required','gate':'G3','owner':'task_interpretation',
                    'source_clause_ids':expected['clause_ids'],'source_expectation_ref':expected['source_expectation_ref'],
                    'detail':'Retained meaning omits current source-owned expectations; re-admit its source/proposal before paid generation. Do not hide additional acceptance requirements from the model.'})
    return findings
