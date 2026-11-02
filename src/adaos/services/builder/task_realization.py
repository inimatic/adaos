"""Lower admitted task constraints to the current semantic ABI choices."""
from __future__ import annotations

from adaos.services.builder_context import digest


def task_realization_context(brief):
    contract=(brief or {}).get('task_contract')
    if not contract:
        return None
    rules=[]
    forbidden=[]
    # Verification examples exercise real controls; they are not a request for
    # additional fixture states. Keep this prospective provider profile separate
    # from canonical compilation of already retained, valid semantic documents.
    declared_states = [row['id'] for row in contract['requirements']
        if row['status']=='active' and row['kind']=='data_state']
    retained_states = (brief or {}).get('representative_states') or {}
    if not declared_states and not (retained_states.get('state')=='known' and retained_states.get('value')):
        forbidden.append('representative_states')
        rules.append({'kind':'verification_state_separation',
            'provider_rule':'representative_states has maxItems=0: no data_state requirement was admitted. '
                'Preserve the actual collection, search, reset and commands. Verification examples are '
                'independent browser/runtime obligations, not extra initial fixture states or new fields/filters. '
                'Current query_empty proofs require reachable equality filters; they cannot stand for text search. '
                'Do not add equality filters, fixture records or surrogate states merely to manufacture that proof.',
            'source_requirement_refs':[row['id'] for row in contract['requirements']
                if row['status']=='active' and row['kind']=='verification']})
    from .prototype_stage import automation_requirement_refs
    if not automation_requirement_refs(brief):
        forbidden.append('automation_requirements')
        rules.append({'kind':'prototype_stage_ownership',
            'provider_rule':'automation_requirements has maxItems=0. Supported local UI/data/operations and authoring policy cannot be deferred to Automation, even when retained in a legacy residual Brief slot.'})
    for index,constraint in enumerate(contract['constraints']):
        common={'constraint_ref':f'task_acceptance.constraints[{index}]',
            'constraint_digest':digest(constraint),'kind':constraint['kind']}
        if constraint['kind']=='forbid_external':
            forbidden.append('capability_surfaces')
            rules.append({**common,'provider_rule':'capability_surfaces has maxItems=0; surface evidence refs are unavailable'})
        elif constraint['kind']=='visible_text':
            rules.append({**common,'visible_locations':['views[].title','views[].section.title'],
                'not_visible_evidence':['document.title','representative_states[].label','requirement_bindings'],
                'realization':'Show the exact admitted literal in a real view or section title, in the admitted UI locale. '
                    'Preserve any separately required exact section titles; a view title can carry explanatory text '
                    'alongside its descriptive heading. Ordinary explanatory text needs no external capability surface.'})
    if not rules and not contract['transitions']:
        return None
    result={'schema':'adaos.builder.task_realization.v1','authority':'core_abi_projection_not_new_user_requirements',
        'constraint_rules':rules,'forbidden_output_arrays':sorted(set(forbidden))}
    if contract['transitions']:
        result['state_policy']=('Representative states describe fixtures that actually exist now. A command postcondition is '
            'not another initial fixture: bind it to the executable command and its admitted effect. Do not invent '
            'a changed initial record to prove a post-action result. Independent browser checks exercise the action.')
        result['transition_policy']={
            'source_ref':'task_acceptance.transitions',
            'authority':'admitted_from_value_is_executable_precondition_not_just_fixture',
            'preferred_construction':(
                'Use command.kind=source_transition_context with transition_ref equal to the admitted '
                'requirement_id, an actual collection/details/editor view_ref and its actual field_ref, plus exposure. '
                'A collection/details target gets a Core-constructed technical editor; no editor role must be reauthored. '
                'Core derives the exact command label, fixed effect, input preservation and inverse '
                'stored-state lock from that source. Do not reauthor label/from/to/guard in this form. '
                'This form still undergoes independent source-field, ownership and behavior acceptance.'),
            'supported_realization':(
                'For a resource whose update/transition/delete commands all share one admitted '
                'field/from_value precondition, set resource.read_only_when to '
                '{field_ref: resolved source field, operator: not_equals, value: admitted from_value}. '
                'The UI and local provider check stored record state independently of draft edits. '
                'Keep the exact to_value in command.fixed_values and preserve unrelated fields.'),
            'not_eligibility_evidence':[
                'command.guard (conditional nonempty-input validation, not availability)',
                'representative_states or initial fixtures',
                'a lock matching only to_value (null and other states remain writable)'],
            'scope_boundary':(
                'Do not apply resource-wide locking to unrelated commands or different preconditions. '
                'If current ABI granularity cannot implement the admitted behavior, report the '
                'capability gap rather than silently weakening or broadening the policy.')}
    return result


def constrain_task_provider_contract(contract,brief):
    realization=task_realization_context(brief)
    if realization and 'representative_states' in realization['forbidden_output_arrays']:
        contract['properties']['representative_states']['maxItems']=0
        kinds=contract['$defs']['semanticRef']['properties']['kind']['enum']
        contract['$defs']['semanticRef']['properties']['kind']['enum']=[kind for kind in kinds if kind!='state']
    if realization and 'capability_surfaces' in realization['forbidden_output_arrays']:
        contract['properties']['capability_surfaces']['maxItems']=0
        kinds=contract['$defs']['semanticRef']['properties']['kind']['enum']
        contract['$defs']['semanticRef']['properties']['kind']['enum']=[kind for kind in kinds if kind!='surface']
    return contract
