"""Lower admitted task constraints to the current semantic ABI choices."""
from __future__ import annotations

from adaos.services.builder_context import digest


def task_realization_context(brief):
    contract=(brief or {}).get('task_contract')
    if not contract:
        return None
    rules=[]
    forbidden=[]
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
    return result


def constrain_task_provider_contract(contract,brief):
    realization=task_realization_context(brief)
    if realization and 'capability_surfaces' in realization['forbidden_output_arrays']:
        contract['properties']['capability_surfaces']['maxItems']=0
        kinds=contract['$defs']['semanticRef']['properties']['kind']['enum']
        contract['$defs']['semanticRef']['properties']['kind']['enum']=[kind for kind in kinds if kind!='surface']
    return contract
