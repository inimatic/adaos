"""Typed display targets; field/view labels are not sections or disclosures.

The lexical inventory is partial and source-bound. It only grounds explicit
naming constructions, never infers an owner from an arbitrary noun.
"""
from __future__ import annotations

import re
from .task_spec import source_clauses

KINDS = ('view_title', 'field_label', 'field_order')
_QUOTE = r'["«“](?P<value>[^"»”]+)["»”]'
_PREFIX = r'(?:(?:the|an?|its|existing|English|Russian)\s+)*'
_PATTERNS = (
    ('view_title', re.compile(r'\b(?:title|caption|heading|name)\s+of\s+'+_PREFIX+
        r'(?:table|collection|view|list)\s+(?:to|as)\s+'+_QUOTE,re.I)),
    ('field_label', re.compile(r'\b(?:caption|label|name)\s+(?:of|for)\s+'+_PREFIX+
        r'(?P<subject>[A-Za-z_][\w.-]*)\s+field\s+(?:to|as)\s+'+_QUOTE,re.I)),
    ('field_label', re.compile(r'\b(?:label|call|rename)\s+'+_PREFIX+r'field\s+'
        r'(?P<subject>[A-Za-z_][\w.-]*)\s+(?:to|as)\s+'+_QUOTE,re.I)),
    ('view_title', re.compile(r'(?:название|заголовок)\s+(?:таблицы|коллекции|списка|представления)\s+'
        r'(?:на|как)\s+'+_QUOTE,re.I)),
    ('field_label', re.compile(r'(?:подпись|название)\s+поля\s+["«“]?'
        r'(?P<subject>[A-Za-z_][\w.-]*)["»”]?\s+(?:на|как)\s+'+_QUOTE,re.I)),
)


def source_targets(statement):
    from .source_relations import _unquoted
    from .source_operators import record_head_operator
    rows=[]
    for clause in source_clauses(statement):
        outside=_unquoted(clause['text'])
        for kind,pattern in _PATTERNS:
            for match in pattern.finditer(clause['text']):
                prefix=clause['text'][clause['text'].rfind('.',0,match.start())+1:match.start()]
                if (not outside[match.start()] or record_head_operator(prefix,source_start=clause['start'])
                        or re.search(r'\b(?:example|e\.g\.|if|when|пример|например|если|когда)\b',prefix,re.I)):
                    continue
                rows.append({'kind':kind,'subject':match.groupdict().get('subject') or '',
                    'values':[match['value']],'clause_ids':[clause['id']]})
    return rows


def interpretation_findings(statement, proposal):
    targets=source_targets(statement)
    findings=[]
    source=(proposal.get('source') or {}).get('digest') or proposal.get('source_digest')
    current={source}
    if (proposal.get('derivation') or {}).get('kind')=='exact_source_replay':
        current.add(proposal['derivation'].get('meaning_source_digest'))
    for row in proposal.get('constraints',[]):
        if row.get('source_digest') is not None and row['source_digest'] not in current:
            continue  # Clause IDs are source-local, not history-wide identities.
        for value in row.get('values',[]):
            known=[target for target in targets if value in target['values']
                and set(target['clause_ids']) <= set(row.get('clause_ids',[]))]
            if known and row['kind'] not in {target['kind'] for target in known}:
                findings.append({'code':'task.ui_target_kind_conflict','gate':'G3','owner':'task_interpretation',
                    'detail':f'Explicit source display target for {value!r} is not {row["kind"]}',
                    'source_clause_ids':row.get('clause_ids',[])})
            if row['kind']=='field_label' and known and row['subject'] not in {target['subject'] for target in known}:
                findings.append({'code':'task.ui_target_subject_conflict','gate':'G3','owner':'task_interpretation',
                    'detail':'A field caption must keep its exact source field owner',
                    'source_clause_ids':row.get('clause_ids',[])})
    return findings


def derived_constraints(statement, source_digest):
    return [{**row,'source_digest':source_digest} for row in source_targets(statement)]


def candidate_issue(constraint, *, views, resources, previous, shown, matches, literal_matches, bound_field):
    """Resolve one actual owner, including retained identity on amendments."""
    kind,subject,expected=constraint['kind'],constraint['subject'],constraint['values']
    baseline=(previous or {}).get('document') if (previous or {}).get('schema')=='adaos.builder.semantic_compiler_view.v1' else previous
    def named(item):return [item['id']]+shown(item)
    if kind in {'view_title','field_order'}:
        candidates=[view for view in (baseline or {}).get('views',[]) if view.get('role')=='collection'
            and (not subject or matches(subject,named(view)))]
        if not candidates:candidates=[view for view in views.values() if view.get('role')=='collection'
            and (not subject or matches(subject,named(view)))]
        if len(candidates)!=1:return 'task.ui_target_unresolved','A display constraint needs one exact collection owner'
        view=views.get(candidates[0]['id'])
        if view is None:return 'task.ui_target_identity_changed','The retained display owner was removed or renamed by identity'
        if kind=='view_title':
            if len(expected)!=1 or not literal_matches(expected[0],shown(view),whole=True):
                return 'task.view_title_mismatch','The source-owned collection title differs from its required literal'
        else:
            resource=resources.get(view.get('resource_ref'),{})
            fields=[bound_field(resource,name) for name in expected]
            if any(field is None for field in fields):return 'task.field_order_unresolved','Every ordered field needs one exact source-owned field'
            ids=[field['id'] for field in fields]
            if len(set(ids))!=len(ids) or [ref for ref in view.get('field_refs',[]) if ref in ids]!=ids:
                return 'task.field_order_mismatch','The declared collection does not preserve the requested relative field order'
    elif kind=='field_label':
        candidates=[]
        for resource in (baseline or {}).get('resources',[]) or resources.values():
            field=bound_field(resource,subject)
            if field is not None:candidates.append((resource,field))
        if len(candidates)!=1:return 'task.ui_target_unresolved','A field caption needs one exact field owner, not a matching string elsewhere'
        resource,old_field=candidates[0]
        current=resources.get(resource['id'],{})
        field=next((row for row in current.get('fields',[]) if row['id']==old_field['id']),None)
        if field is None:return 'task.ui_target_identity_changed','The retained field owner was removed or renamed by identity'
        if len(expected)!=1 or not literal_matches(expected[0],shown(field),whole=True):
            return 'task.field_label_mismatch','The source-owned field caption differs from its required literal'
        if not any(view.get('resource_ref')==resource['id'] and field['id'] in view.get('field_refs',[]) for view in views.values()):
            return 'task.field_label_not_visible','The correctly named field has no actual view projection'
    return None
