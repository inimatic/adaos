"""Typed display targets; field/view labels are not sections or disclosures.

The lexical inventory is partial and source-bound. It only grounds explicit
naming constructions, never infers an owner from an arbitrary noun.
"""
from __future__ import annotations

import re
from .task_spec import source_clauses
from .interpretation_boundary import _SCOPED_SELECTOR

KINDS = ('view_title', 'field_label', 'field_order')
_QUOTE = r'["«“](?P<value>[^"»”]+)["»”]'
_PREFIX = r'(?:(?:the|an?|its|existing|English|Russian)\s+)*'
_PATTERNS = (
    ('view_title', re.compile(r'\b(?:title|caption|heading|name)\s+of\s+'
        r'(?P<subject>' + _SCOPED_SELECTOR.pattern + r')\s+(?:to|as)\s+' + _QUOTE, re.I)),
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
                scoped = match.groupdict()
                if (scoped.get('base') and not scoped.get('section') and not scoped.get('resource')
                        and scoped['base'].split()[-1].casefold() in {'table', 'collection', 'list'}):
                    continue  # Preserve the historical unscoped constructor.
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


def display_owner(constraint, *, views, resources, previous, bindings, shown, matches, literal_matches, bound_field):
    """Return a stable locator, never a replacement chosen by the new candidate."""
    kind,subject,expected=constraint['kind'],constraint['subject'],constraint['values']
    baseline=(previous or {}).get('document') if (previous or {}).get('schema')=='adaos.builder.semantic_compiler_view.v1' else previous
    source=baseline or {'views':list(views.values()),'resources':list(resources.values()),'requirement_bindings':bindings}
    prefix='field:' if kind=='field_label' else 'view:'
    identity=constraint.get('assertion_ref')
    def refs(rows):return {ref for row in rows if identity and row['requirement_ref']==identity
        for ref in row['semantic_refs'] if ref.startswith(prefix)}
    anchors=refs(source.get('requirement_bindings',[]))
    if anchors:
        if len(anchors)!=1:return None
        owner=next(iter(anchors))
    elif kind=='field_label':
        fields=[field for resource in source.get('resources',[]) if (field:=bound_field(resource,subject)) is not None]
        if len(fields)!=1:return None
        owner='field:'+fields[0]['id']
    else:
        typed=subject.startswith('view:')
        candidates=([view for view in source.get('views',[]) if view.get('role')=='collection' and 'view:'+view['id']==subject]
                    if typed else [view for view in source.get('views',[]) if view.get('role')=='collection'
                                   and (not subject or matches(subject,[view['id']]+shown(view)))])
        if subject and not candidates and not typed:
            # A source can address a record concept rather than its current
            # heading. Follow the closed resource -> collection owner edge
            # only when both identities are unambiguous; never pick a peer view.
            owners = [resource for resource in source.get('resources', [])
                      if matches(subject, [resource['id']] + shown({'label': resource.get('item_label', {})}))]
            if len(owners) == 1:
                candidates = [view for view in source.get('views', []) if view.get('role') == 'collection'
                              and view.get('resource_ref') == owners[0]['id']]
        if baseline and subject and not candidates and not typed:
            prior_ids={view['id'] for view in baseline.get('views',[])}
            candidates=[view for view in views.values() if view.get('role')=='collection' and view['id'] not in prior_ids
                and matches(subject,[view['id']]+shown(view))]
        # Migration of old, already accepted constraints is grounded in that
        # immutable baseline's labels/projections, never a candidate's rename.
        if baseline and len(candidates)>1:
            if kind=='view_title':
                candidates=[view for view in candidates if len(expected)==1 and literal_matches(expected[0],shown(view),whole=True)]
            else:
                old_resources={row['id']:row for row in source.get('resources',[])}
                candidates=[view for view in candidates if all((field:=bound_field(old_resources.get(view.get('resource_ref'),{}),name))
                    is not None and field['id'] in view.get('field_refs',[]) for name in expected)]
        if len(candidates)!=1:return None
        owner='view:'+candidates[0]['id']
    proposed=refs(bindings)
    if baseline and proposed and proposed!={owner}:return None
    return owner


def candidate_issue(constraint, *, views, resources, previous, shown, matches, literal_matches, bound_field, bindings=()):
    """Resolve one actual owner, including retained identity on amendments."""
    kind,subject,expected=constraint['kind'],constraint['subject'],constraint['values']
    owner=display_owner(constraint,views=views,resources=resources,previous=previous,bindings=bindings,
        shown=shown,matches=matches,literal_matches=literal_matches,bound_field=bound_field)
    if owner is None:return 'task.ui_target_unresolved','A display constraint needs one stable source-owned locator'
    if kind in {'view_title','field_order'}:
        view=views.get(owner.removeprefix('view:'))
        if view is None or view.get('role')!='collection':return 'task.ui_target_identity_changed','The retained display owner was removed or changed role'
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
        candidates=[(resource,field) for resource in resources.values() for field in resource.get('fields',[])
            if 'field:'+field['id']==owner]
        if len(candidates)!=1:return 'task.ui_target_unresolved','A field caption needs one exact field owner, not a matching string elsewhere'
        resource,field=candidates[0]
        if len(expected)!=1 or not literal_matches(expected[0],shown(field),whole=True):
            return 'task.field_label_mismatch','The source-owned field caption differs from its required literal'
        if not any(view.get('resource_ref')==resource['id'] and field['id'] in view.get('field_refs',[]) for view in views.values()):
            return 'task.field_label_not_visible','The correctly named field has no actual view projection'
    return None


def normalize_display_bindings(document, brief, *, previous=None):
    """After acceptance, persist checked owners for graph/recompile parity."""
    from .task_acceptance import localized_display_names, resolved_field_locator, _matches, _literal_text
    from .source_obligations import effective_constraints
    from .source_field_bindings import resolve_fields
    contract=(brief or {}).get('task_contract')
    if not contract:return []
    constraints=[row for row in effective_constraints(contract)
                 if row['kind'] in KINDS and row.get('assertion_ref')]
    if not constraints:return []
    resolved,findings=resolve_fields(document,contract,previous=previous)
    if findings:raise ValueError('Cannot persist a display locator with invalid source fields')
    shown=localized_display_names(contract);bound_field=resolved_field_locator(resolved)
    changes=[]
    for constraint in constraints:
        owner=display_owner(constraint,views={row['id']:row for row in document['views']},
            resources={row['id']:row for row in document['resources']},previous=previous,
            bindings=document.get('requirement_bindings',[]),shown=shown,matches=_matches,
            literal_matches=_literal_text,bound_field=bound_field)
        if owner is None:raise ValueError('Cannot persist an unvalidated display owner')
        rows=document.setdefault('requirement_bindings',[])
        row=next((row for row in rows if row['requirement_ref']==constraint['assertion_ref']),None)
        if row is not None and row['semantic_refs']==[owner]:continue
        before=row['semantic_refs'] if row else None
        if row is None:rows.append({'requirement_ref':constraint['assertion_ref'],'semantic_refs':[owner]})
        else:row['semantic_refs']=[owner]
        changes.append({'kind':'source_display_owner_binding','requirement_ref':constraint['assertion_ref'],
            'from':before,'to':[owner],'authority':'Core source-display acceptance, not model self-certification'})
    return changes
