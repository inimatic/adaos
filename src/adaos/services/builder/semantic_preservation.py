"""Explicit fixed-point constructor for admitted preservation-only revisions.

Not repair and not inferred prose equivalence: no new authorable obligation may
exist, every prior product proof must be explicitly reaffirmed, and normal source,
closure, fixture and ABI admission still runs. The immutable baseline is Core's,
never an echoed model document or an application template.
"""
from __future__ import annotations

from adaos.services.builder_context import digest


def preservation_recipe(brief, previous):
    from .prototype_context import prototype_requirement_inventory, expand_prototype_requirement_aliases
    from .semantic_reaffirmation import document_of, candidate_findings
    contract=(brief or {}).get('task_contract') or {}
    relations=contract.get('reaffirmations', [])
    if not previous or not relations:
        return None
    document=document_of(previous)
    if document.get('schema')!='adaos.webui.semantic.v2' or document.get('capability_gaps'):
        return None
    current=contract['source']['digest']
    # A policy/exclusion cannot silently amend a fixture, display assertion or
    # transition. A new product obligation always uses ordinary authoring.
    if any((row.get('source_digest') or current)==current for group in ('facts','constraints','transitions')
           for row in contract.get(group, [])):
        return None
    reaffirmed={row['requirement_ref'] for row in relations}
    active=[row for row in contract.get('requirements', []) if row['status']=='active']
    if any(row.get('source_digest')==current and row['id'] not in reaffirmed
           and row['kind'] not in {'authoring','policy','exclusion'} for row in active):
        return None
    old_bindings={row['requirement_ref'] for row in document.get('requirement_bindings', [])}
    # The compiler's complete alias inventory matters here, not only the
    # deduplicated model-facing set: a new alias still needs a real binding.
    canonical=expand_prototype_requirement_aliases(document,brief)
    canonical_refs={row['requirement_ref'] for row in canonical.get('requirement_bindings', [])}
    if not {row['id'] for row in prototype_requirement_inventory(brief)}<=canonical_refs:
        return None
    old_product={row['id'] for row in active if row.get('source_digest')!=current
                 and row['kind']!='assertion' and row['id'] in old_bindings}
    covered={row['requirement_ref'] for relation in relations for row in relation['prior_requirements']}
    if not old_product or not old_product<=covered or candidate_findings(document,brief,previous):
        return None
    return {'baseline_digest':digest(previous)}


def preservation_provider_contract(brief, previous):
    recipe=preservation_recipe(brief,previous)
    if recipe is None:
        return None
    return {'type':'object','additionalProperties':False,'required':['schema','preserve_baseline'],
        'description':'This admitted task only reaffirms the complete existing product. Return this fixed-point constructor; do not rewrite resources, views, commands, states or evidence. Core reuses its exact pinned graph and independently readmits every retained obligation.',
        'properties':{'schema':{'type':'string','enum':['adaos.builder.semantic_prototype_candidate.v2']},
            'preserve_baseline':{'type':'object','additionalProperties':False,'required':['baseline_digest'],
                'properties':{'baseline_digest':{'type':'string','enum':[recipe['baseline_digest']]}}}}}


def compile_preservation_candidate(value, *, brief, previous, project_ref=None, runtime_scope_ref=None):
    from jsonschema import Draft202012Validator
    from .semantic_prototype import (semantic_prototype_candidate_contract, compile_semantic_prototype,
                                      SemanticPrototypeValidationError)
    from .semantic_reaffirmation import document_of, candidate_findings
    from .task_acceptance import task_candidate_findings, task_acceptance_receipt
    from .task_preservation import fixture_revision_findings
    from .prototype_context import expand_prototype_requirement_aliases
    from .source_field_bindings import normalize_fact_bindings
    from .semantic_ui_constraints import normalize_display_bindings
    recipe=preservation_recipe(brief,previous)
    errors=list(Draft202012Validator(semantic_prototype_candidate_contract(version='v2')).iter_errors(value))
    if errors or recipe is None or value.get('preserve_baseline')!=recipe:
        raise SemanticPrototypeValidationError([{'code':'semantic.baseline_preservation_unproved','gate':'G8',
            'path':'$.preserve_baseline','owner':'candidate',
            'detail':'Preservation needs the exact Core baseline, complete explicit prior proof coverage and no new authorable obligation; mixed/foreign/stale constructors are forbidden'}])
    # Compiler views deliberately omit exact-statement aliases and Core-only
    # fact assertions. Restore them by the SAME deterministic normalizers used
    # by ordinary authoring; do not mistake the model projection for a full
    # publication or invent any new product field/effect.
    document=expand_prototype_requirement_aliases(document_of(previous),brief)
    document.update(brief_ref=brief['brief_id'],brief_digest=brief['digest'])
    restorations=normalize_fact_bindings(document,brief,previous=previous)
    restorations+=normalize_display_bindings(document,brief,previous=previous)
    findings=task_candidate_findings(document,brief,previous=previous)+candidate_findings(document,brief,previous)
    fixture_findings,scope=fixture_revision_findings(previous,document,brief)
    if findings or fixture_findings:
        raise SemanticPrototypeValidationError(findings+fixture_findings)
    result=compile_semantic_prototype(document,brief=brief,project_ref=project_ref,runtime_scope_ref=runtime_scope_ref)
    result['normalizations']=[{'kind':'source_admitted_baseline_preservation','baseline_digest':recipe['baseline_digest'],
        'reaffirmation_refs':[row['requirement_ref'] for row in brief['task_contract']['reaffirmations']],
        'authority':'Core source/closure admission, not model repair or omitted-field inference'},*restorations]
    result['task_acceptance']=task_acceptance_receipt(result['semantic_document'],brief)
    if scope:
        result['preservation_receipt']={'schema':'adaos.builder.fixture_preservation_receipt.v1','status':'passed',
            'write_scope':scope,'candidate_digest':result['semantic_digest'],'model_certified':False}
    return result
