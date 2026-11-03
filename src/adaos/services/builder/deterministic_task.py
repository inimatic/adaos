"""Fresh S1 admission for a deliberately closed facts-only source grammar.

Every nonblank line must be one Item/Record/Запись tuple with ASCII fields and
JSON-like scalar values, optionally the exact all-fields-table instruction.
No other prose, behavior, ownership inference or hidden model interpretation is
admitted. Other tasks use explicit S2.
"""
from __future__ import annotations

import json
import re

from .task_interpretation import admit_interpretation, TaskInterpretationRejected
from .task_spec import seal

GRAMMAR='source-grammar:fresh-facts-only-v1'
_LINE=re.compile(r'\s*(?:Item|Record|Запись)\s+(?P<subject>[A-Za-z][A-Za-z0-9_-]*)\s*:\s*(?P<attributes>.*?)\s*\.?\s*',re.I)
_ATTRIBUTE=re.compile(r'(?P<field>[A-Za-z][A-Za-z0-9_]*)\s+(?P<value>.+)')


def _unsupported(detail):
    raise TaskInterpretationRejected([{'code':'task.deterministic_source_scope_unsupported','gate':'G3',
        'owner':'task_interpretation','detail':detail+' Use the explicit two-stage profile; no provider call or automatic fallback was made.'}])


def _value(raw):
    if raw.startswith('"'):
        try:value=json.loads(raw)
        except ValueError:_unsupported('A quoted value must be one complete JSON string.')
        if not isinstance(value,str):_unsupported('Quoted values must be strings.')
        return value
    if raw in {'true','false','null'}:return json.loads(raw)
    if re.fullmatch(r'-?(?:0|[1-9]\d*)(?:\.\d+)?',raw):return json.loads(raw)
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]*|\d{4}-\d{2}-\d{2}',raw):return raw
    _unsupported('A scalar is outside the closed facts-only grammar; quote a string or use S2.')


def admit_facts(task,intent,*,previous_brief=None):
    if previous_brief:_unsupported('Fresh deterministic admission does not amend prior source meaning.')
    from .source_literals import _parts
    from .source_obligations import SOURCE_TABLE_STATEMENTS
    roles,facts,requirements=[],[],[]
    for clause in task['clauses']:
        text=clause['text'].strip()
        if not text:
            roles.append({'clause_id':clause['id'],'roles':['formatting']});continue
        if text.casefold() in SOURCE_TABLE_STATEMENTS:
            roles.append({'clause_id':clause['id'],'roles':['product']})
            requirements.append({'id':'source-table-'+clause['id'].partition(':')[2],
                'clause_ids':[clause['id']],'statement':text,'kind':'ui','operation':None,
                'verification_family':'layout','supersedes':[]})
            continue
        match=_LINE.fullmatch(text)
        if not match:_unsupported('Every nonblank line must be exactly Item/Record/Запись ID: field scalar, ... .')
        attributes=match['attributes']
        seen=set()
        for start,end in _parts(attributes,','):
            evidence=attributes[start:end].strip()
            attribute=_ATTRIBUTE.fullmatch(evidence)
            if not attribute or attribute['field'].casefold() in seen:
                _unsupported('Every tuple needs distinct ASCII field names and complete scalar evidence.')
            seen.add(attribute['field'].casefold())
            facts.append({'clause_ids':[clause['id']],'subject':match['subject'],
                'field':attribute['field'],'source_field':attribute['field'],'value':_value(attribute['value']),
                'record_evidence':text.rstrip('.').rstrip(),'field_evidence':evidence})
        roles.append({'clause_id':clause['id'],'roles':['fact']})
    if not facts:_unsupported('The facts-only source must contain at least one complete record.')
    proposal={'schema':'adaos.builder.task_interpretation.v1','source_digest':task['source']['digest'],
        'clause_roles':roles,'requirements':requirements,'facts':facts,'transitions':[],'constraints':[],
        'questions':[],'design_freedom':[]}
    admitted,brief=admit_interpretation(task,proposal,intent)
    brief=seal({**brief,'interpretation':{'mode':'deterministic_explicit','confidence':1.0,'unresolved_fields':[]}},
        id_key='brief_id',prefix='brief:')
    admitted=seal({**admitted,'brief_ref':brief['brief_id'],'brief_digest':brief['digest'],
        'deterministic_source_grammar':GRAMMAR})
    return admitted,brief,{'mode':'deterministic_facts','source_grammar':GRAMMAR,'provider_calls':0,
        'fresh_source_admitted':True,'general_natural_language_interpretation_proven':False}
