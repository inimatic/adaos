"""Source assertion locators over canonical semantic field identities.

Bindings locate an implementation; the source still owns every expected value.
Core narrows admitted fact witnesses to one canonical field so later label edits
cannot destroy/reassign that identity. A previous admitted locator wins over a
new candidate's proposed locator. No new provider/ABI output fields are needed.
"""
from __future__ import annotations

import copy

def validate_binding_context(rows):
    if not isinstance(rows,list) or any(not isinstance(row,dict) or set(row)!={'subject','source_field','semantic_ref'}
        or any(not isinstance(row[key],str) or not row[key] for key in ('subject','source_field','semantic_ref'))
        or not row['semantic_ref'].startswith('field:') for row in rows):
        raise ValueError('Source binding context must contain only declared Core locator fields')
    if len({(row['subject'].casefold(),row['source_field'].casefold()) for row in rows})!=len(rows):
        raise ValueError('One source slot cannot have competing context locators')
    return rows


def slot(fact):
    return (str(fact['subject']).casefold(), str(fact.get('source_field',fact['field'])).casefold())


def field_ref(resource, field):
    # Canonical semantic refs use the actual globally normalized field ID.
    # Graph-internal owner-qualified node IDs are a different namespace.
    return 'field:'+field['id']


def _index(document):
    entries={}
    for resource in document.get('resources',[]):
        for field in resource.get('fields',[]):
            entries.setdefault(field_ref(resource,field),[]).append((resource,field))
    return {ref:targets[0] for ref,targets in entries.items() if len(targets)==1}


def baseline_document(value,contract):
    if not value or value.get('schema')!='adaos.builder.semantic_compiler_view.v1':return value
    from adaos.services.builder_context import digest
    document=value.get('document')
    if not isinstance(document,dict) or digest(document)!=value.get('view_digest'):
        from .generation_plan import GenerationInputRejected
        raise GenerationInputRejected('context.source_field_baseline_conflict','The baseline compiler view no longer matches its document')
    rows=value.get('source_field_bindings',[])
    if rows:
        validate_binding_context(rows)
        if digest(rows)!=value.get('source_field_bindings_digest'):
            from .generation_plan import GenerationInputRejected
            raise GenerationInputRejected('context.source_field_baseline_conflict','The baseline source locator projection changed')
        document=copy.deepcopy(document)
        for fact in contract.get('facts',[]):
            matches=[row for row in rows if (row['subject'].casefold(),row['source_field'].casefold())==slot(fact)]
            if matches and fact.get('assertion_ref'):
                bindings=document.setdefault('requirement_bindings',[])
                bindings[:]=[row for row in bindings if row['requirement_ref']!=fact['assertion_ref']]
                bindings.append({'requirement_ref':fact['assertion_ref'],'semantic_refs':[matches[0]['semantic_ref']]})
    return document


def _fact_refs(contract,fact):
    # Include superseded source assertions: changing a literal does not rename
    # its field. The new TaskSpec already admits which expected value is active.
    return {row['id'] for row in contract.get('requirements',[]) if row.get('assertion_group')=='facts'
            and slot(row['assertion'])==slot(fact)} | ({fact['assertion_ref']} if fact.get('assertion_ref') else set())


def _bound(document,refs):
    return {ref for row in document.get('requirement_bindings',[]) if row['requirement_ref'] in refs
            for ref in row.get('semantic_refs',[]) if ref.startswith('field:')}


def _named(document,fact):
    from .task_acceptance import _field
    return [(resource,field) for resource in document.get('resources',[])
            if (field:=(_field(resource,fact['field']) or _field(resource,fact.get('source_field',fact['field']))))]


def _subject_matches(resource,fact):
    return any(any(isinstance(value,str) and value.casefold()==fact['subject'].casefold()
                   for value in record.values()) for record in resource.get('records',[]))


def _anchors(document,contract,fact):
    refs=_bound(document,_fact_refs(contract,fact))
    if refs:return refs
    # One-time migration from a baseline predating exact field witnesses. Read
    # its original labels, never the candidate's new label or proposed value.
    named=[field_ref(resource,field) for resource,field in _named(document,fact) if _subject_matches(resource,fact)]
    return set(named) if len(named)==1 else set()


def resolve_fields(document,contract,*,previous=None):
    current=_index(document)
    prior=baseline_document(previous,contract)
    resolved,findings=[],[]
    for fact in contract.get('facts',[]):
        anchored=_anchors(prior,contract,fact) if prior else set()
        proposed=_bound(document,{fact['assertion_ref']}) if fact.get('assertion_ref') else set()
        def fail(code,detail):
            findings.append({'code':code,'gate':'G8','owner':'candidate','path':'$.requirement_bindings',
                'detail':detail,'source_clause_ids':fact.get('clause_ids',[])})
        if len(anchored)>1 or prior and anchored-set(_index(prior)):
            findings.append({'code':'context.source_field_binding_ambiguous','gate':'G4','owner':'context_compilation',
                'path':'$.previous.requirement_bindings','detail':'The admitted baseline has competing or absent source fields; explicit migration is required'})
            continue
        if anchored:
            ref=next(iter(anchored))
            if ref not in current or (proposed and proposed!={ref}):
                fail('task.source_field_binding_changed',f'A previously admitted source field must remain {ref}; a label match or new candidate binding cannot replace it')
                continue
            eligible=[current[ref]]
            basis='admitted_baseline_field'
        else:
            named=_named(document,fact)
            if proposed:
                if len(proposed)!=1 or not proposed<=set(current):
                    fail('task.source_field_binding_ambiguous','A source fact needs one declared field locator, not several fields or an unknown ref')
                    continue
                selected=current[next(iter(proposed))]
                # A stable name must not be overridden by declaring a different
                # field with a coincidentally equal value in the same resource.
                if any(resource['id']==selected[0]['id'] and field_ref(resource,field)!=field_ref(*selected)
                       for resource,field in named):
                    fail('task.source_field_binding_conflict','The explicit source locator contradicts its unambiguous source field identity')
                    continue
                eligible=[selected];basis='declared_source_field'
            else:
                eligible=named;basis='unambiguous_source_name'
        matches=[(resource,record,field) for resource,field in eligible for record in resource.get('records',[])
            if any(isinstance(value,str) and value.casefold()==fact['subject'].casefold() for value in record.values())]
        exact=[row for row in matches if str(row[1].get('id',''))==fact['subject']]
        if exact:matches=exact
        if len(matches)!=1:
            fail('task.fixture_subject_unresolved',f"Source fixture {fact['subject']!r} needs one bound record/field, found {len(matches)}")
            continue
        resource,record,field=matches[0]
        resolved.append({'fact':fact,'resource':resource,'record':record,'field':field,
                         'semantic_ref':field_ref(resource,field),'basis':basis})
    return resolved,findings


def normalize_fact_bindings(document,brief,*,previous=None):
    """After source-value admission, retain Core's exact fact field witnesses."""
    contract=(brief or {}).get('task_contract') or {}
    resolved,findings=resolve_fields(document,contract,previous=previous)
    if findings:
        from .semantic_prototype import SemanticPrototypeValidationError
        raise SemanticPrototypeValidationError(findings)
    changes=[]
    for target in resolved:
        identity=target['fact'].get('assertion_ref')
        if not identity:continue
        rows=document.setdefault('requirement_bindings',[])
        row=next((row for row in rows if row['requirement_ref']==identity),None)
        expected=[target['semantic_ref']]
        if row is not None and row['semantic_refs']==expected:continue
        before=copy.deepcopy(row['semantic_refs']) if row else None
        if row is None:
            row={'requirement_ref':identity,'semantic_refs':expected};rows.append(row)
        else:row['semantic_refs']=expected
        changes.append({'kind':'source_fact_field_binding','requirement_ref':identity,
            'from':before,'to':expected,'authority':'Core source-value admission, not model self-certification'})
    return changes


def binding_context(document,brief):
    """Expose existing admitted field locators, without treating data as policy."""
    contract=(brief or {}).get('task_contract') or {}
    document=baseline_document(document,contract)
    index=_index(document)
    rows=[]
    for fact in contract.get('facts',[]):
        refs=_anchors(document,contract,fact)
        if len(refs)>1 or refs-set(index):
            from .generation_plan import GenerationInputRejected
            raise GenerationInputRejected('context.source_field_binding_ambiguous',
                'Retained source-field locators require explicit migration before generation')
        if refs:
            rows.append({'subject':fact['subject'],'source_field':fact.get('source_field',fact['field']),
                'semantic_ref':next(iter(refs))})
    return validate_binding_context(rows)
