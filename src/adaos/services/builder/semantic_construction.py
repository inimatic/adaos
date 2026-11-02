"""Explicit semantic constructors lowered to the existing candidate/ABI.

These are opt-in syntax, not repairs: legacy invalid candidates remain invalid.
The original response is immutable; every expansion has a deterministic receipt.
The ordinary full compiler and independent source acceptance still run afterwards.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping

from jsonschema import Draft202012Validator

from .prototype_context import prototype_model_requirement_inventory

OWNER_SCHEMAS = ('resource', 'relationship', 'field', 'view', 'queryControl',
                 'command', 'sourceTransitionCommand', 'representativeState', 'sitePreviewSurface')


def source_transition_guidance():
    """One authoring/ownership rule for stable and task-specific delivery."""
    return (
        'For an admitted task_acceptance.transitions entry prefer command.kind=source_transition_context, '
        'transition_ref=its requirement_id, view_ref=an actual collection/details/editor context, field_ref=its '
        'actual field id, exposure and the evidence representation enabled by this call\'s schema. No separate editor declaration is necessary for this fixed '
        'source action: Core derives a technical editor for a collection/details context, preserving the '
        "original view's role/section. Editor-primary exposure gets an inline editor, row-action exposure a "
        'modal with its opener; an explicit editor is reused as authored. During evolution, if the retained '
        'command already targets a declared editor, preserve that view_ref and reuse that editor. Do not '
        'keep that editor AND retarget the constructor to a collection: its derived ID command.id + '
        '_source_editor would collide. Generated views count toward the existing ABI view limit. Do not emit '
        'label/fixed_values/guard/input_field_refs for this form. Core derives them and stored-state eligibility '
        'from the one admitted transition. The older source_transition form still requires an explicit editor. '
        'Ordinary legacy commands remain available but must implement all obligations explicitly.')


def total_coverage_provider_contract(contract, brief):
    """Require each proof slot structurally; its targets are still proposals."""
    refs = [row['id'] for row in prototype_model_requirement_inventory(brief)]
    contract['properties']['coverage_map'] = {'type':'object','additionalProperties':False,
        'properties':{ref:{'$ref':'#/$defs/coverageEntry'} for ref in refs}, 'required':refs}
    contract['required'].append('coverage_map')
    contract['properties']['requirement_bindings']['maxItems'] = 0
    contract['properties']['capability_gaps']['maxItems'] = 0
    contract['$defs']['fulfills'] = {'type':'array','items':{'type':'string'},'maxItems':0}
    # Preserve task-specific capability restrictions in this second address
    # representation. It does not expand the available semantic/ABI space.
    allowed = set(contract['$defs']['semanticRef']['properties']['kind']['enum'])
    simple = contract['$defs']['coverageTarget']['anyOf'][0]['properties']['kind']
    simple['enum'] = [kind for kind in simple['enum'] if kind in allowed]


def lower_constructions(value, brief, schema):
    from .semantic_prototype import SemanticPrototypeValidationError

    candidate = copy.deepcopy(value)
    receipts = []
    total_evidence = 'coverage_map' in candidate
    allowed = {row['id'] for row in prototype_model_requirement_inventory(brief)}
    # Retained valid local witnesses remain compatible when a quality check is
    # prospectively routed to its real independent acceptance owner instead.
    from .semantic_quality import responsive_quality_obligations
    allowed.update(row['id'] for row in responsive_quality_obligations(brief))

    def reject(code, path, detail):
        raise SemanticPrototypeValidationError([{
            'code': 'semantic.construction_' + code, 'path': path,
            'gate': 'G8', 'owner': 'candidate', 'detail': detail}])

    def validate(fragment, definition, path):
        validator = Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/' + definition})
        error = next(validator.iter_errors(fragment), None)
        if error:
            reject('shape_invalid', path, error.message)

    def bind(requirement, kind, identity, path):
        if requirement not in allowed:
            reject('requirement_unknown', path, 'Claim must name an admitted authorable requirement')
        if not isinstance(identity, str) or not identity:
            reject('owner_invalid', path, 'Claim requires a declared owner ID')
        bindings = candidate.setdefault('requirement_bindings', [])
        matching = [row for row in bindings if row.get('requirement_ref') == requirement]
        if len(matching) > 1:
            reject('binding_duplicate', path, 'Duplicate explicit bindings are not repaired by a local claim')
        if matching:
            binding = matching[0]
        else:
            binding = {'requirement_ref': requirement, 'semantic_refs': []}
            bindings.append(binding)
        reference = {'kind': kind, 'id': identity}
        if reference not in binding['semantic_refs']:
            binding['semantic_refs'].append(reference)
        receipts.append({'kind': 'owner_local_requirement', 'from': path,
                         'requirement_ref': requirement, 'to': reference})

    def claim(owner, kind, path, *, identity=None):
        if 'fulfills' not in owner:
            return
        claims = owner.pop('fulfills')
        validate(claims, 'fulfills', path + '.fulfills')
        if total_evidence and claims:
            reject('coverage_parallel_evidence', path, 'Total evidence cannot be mixed with nonempty owner-local claims')
        unique = list(dict.fromkeys(claims))
        if unique != claims:
            receipts.append({'kind': 'idempotent_owner_claim_set', 'from': claims,
                             'to': unique, 'target': path + '.fulfills'})
        for reference in unique:
            bind(reference, kind, identity or owner.get('id'), path + '.fulfills')

    if 'coverage_map' in candidate:
        coverage = candidate.pop('coverage_map')
        authorable = {row['id'] for row in prototype_model_requirement_inventory(brief)}
        if not isinstance(coverage, dict) or set(coverage) != authorable:
            reject('coverage_slots_invalid', '$.coverage_map', 'Every exact admitted authorable requirement needs one explicit evidence slot; no key is filled automatically')
        if candidate.get('requirement_bindings') or candidate.get('capability_gaps'):
            reject('coverage_parallel_evidence', '$.coverage_map', 'Total evidence cannot be mixed with legacy global evidence arrays')
        from .semantic_prototype import _candidate_v2_field_namespaces
        for index, resource in enumerate(candidate.get('resources') or []):
            validate(resource, 'resource', f'$.resources[{index}]')
        field_names = _candidate_v2_field_namespaces(candidate.get('resources') or [])
        fields = {r['id']:{f['id'] for f in r.get('fields', [])} for r in candidate.get('resources') or []}
        for ref, entry in coverage.items():
            path = '$.coverage_map.' + ref
            validate(entry, 'coverageEntry', path)
            if entry['status']=='gap':
                candidate.setdefault('capability_gaps', []).append({'requirement_ref':ref,
                    'code':entry['code'],'detail':entry['detail']})
            else:
                for target in [entry['first_target'], *entry['additional_targets']]:
                    identity = target['id']
                    if target['kind']=='field':
                        owner = target['resource_ref']
                        if identity not in fields.get(owner, set()):
                            reject('coverage_field_owner_unknown', path, 'A field target needs its exact declared resource and field; no label/first-owner fallback')
                        identity = field_names[owner][identity]
                    bind(ref, target['kind'], identity, path)
            receipts.append({'kind':'total_requirement_evidence','from':path,'requirement_ref':ref,
                             'proposal':copy.deepcopy(entry)})

    field_maps = None
    for group, kind in (('resources', 'resource'), ('views', 'view'), ('commands', 'command'),
                        ('relationships', 'relationship'), ('representative_states', 'state'),
                        ('capability_surfaces', 'surface')):
        for index, owner in enumerate(candidate.get(group) or []):
            if not isinstance(owner, dict):
                continue  # The ordinary candidate validator reports malformed owners.
            path = f'$.{group}[{index}]'
            claim(owner, kind, path)
            if group == 'resources':
                for findex, field in enumerate(owner.get('fields') or []):
                    if not isinstance(field, dict) or 'fulfills' not in field:
                        continue
                    if field_maps is None:
                        from .semantic_prototype import _candidate_v2_field_namespaces
                        field_maps = _candidate_v2_field_namespaces(candidate['resources'])
                    claim(field, 'field', path + f'.fields[{findex}]',
                          identity=field_maps[owner['id']][field['id']])
            if group == 'views':
                for qindex, query in enumerate(owner.get('query_controls') or []):
                    if isinstance(query, dict):
                        claim(query, 'query', path + f'.query_controls[{qindex}]')

    transitions = {}
    for row in (brief.get('task_contract') or {}).get('transitions', []):
        transitions.setdefault(row['requirement_id'], []).append(row)
    resources = {row.get('id'): row for row in candidate.get('resources', []) if isinstance(row, Mapping)}
    views = {row.get('id'): row for row in candidate.get('views', []) if isinstance(row, Mapping)}
    policies = {}
    constructed = set()
    for index, command in enumerate(candidate.get('commands') or []):
        if not isinstance(command, dict) or command.get('kind') not in {'source_transition', 'source_transition_context'}:
            continue
        path = f'$.commands[{index}]'
        validate(command, 'sourceTransitionCommand', path)
        sources = transitions.get(command['transition_ref'], [])
        if not sources:
            reject('transition_unknown', path, 'transition_ref must name an admitted task transition requirement_id')
        if len(sources) != 1:
            reject('transition_ambiguous', path, 'A single-effect constructor requires exactly one admitted transition for its requirement')
        source = sources[0]
        view = views.get(command['view_ref'])
        resource = resources.get((view or {}).get('resource_ref'))
        if not view or view.get('role') not in {'collection', 'details', 'editor'} or resource is None:
            reject('transition_owner_invalid', path, 'Source transition requires an actual view context and resource')
        if command['kind'] == 'source_transition' and view['role'] != 'editor':
            reject('transition_owner_invalid', path, 'Legacy source_transition requires an existing editor; use the explicit context constructor for Core-owned scaffolding')
        if command['field_ref'] not in {field.get('id') for field in resource.get('fields', [])}:
            reject('transition_field_unknown', path, 'field_ref must name a field of the editor resource')
        exposure = copy.deepcopy(command['exposure'])
        if view['role'] == 'collection':
            explicit = exposure.get('collection_view_ref')
            if explicit is not None and explicit != view['id']:
                reject('transition_context_conflict', path,
                       'A collection source context cannot be redirected to a different opener owner')
            exposure['collection_view_ref'] = view['id']
        policy = {'field_ref': command['field_ref'], 'operator': 'not_equals', 'value': source['from_value']}
        previous = policies.get(resource['id'])
        if previous is not None and previous != policy:
            reject('transition_scope_conflict', path, 'Current ABI cannot apply different stored-state preconditions to one resource')
        if resource.get('read_only_when') is not None and resource['read_only_when'] != policy:
            reject('transition_policy_conflict', path, 'Explicit resource policy contradicts the admitted source transition')
        policies[resource['id']] = policy
        locales = [locale for locale in candidate.get('title', {}) if locale in {'en', 'ru'}]
        editor_ref = view['id']
        if view['role'] != 'editor':
            # An explicit source command owns its technical editor scaffolding.
            # Preserve the authored collection/details role instead of "fixing"
            # that independent view. Never invent or resolve an absent owner.
            editor_ref = command['id'] + '_source_editor'
            if editor_ref in views:
                reject('transition_editor_collision', path, 'Derived editor ID collides with a declared view')
            editor = {'id': editor_ref, 'resource_ref': resource['id'], 'role': 'editor',
                'region_role': view['region_role'],
                'surface': 'inline' if command['exposure']['placement'].startswith('editor_') else 'modal',
                'presentation': None,
                'title': {locale: source['command_label'] for locale in locales},
                'field_refs': list(dict.fromkeys([*view.get('field_refs', []), command['field_ref']])),
                'filter': None, 'query_controls': [], 'empty_state': None,
                'section': copy.deepcopy(view.get('section'))}
            candidate['views'].append(editor)
            views[editor_ref] = editor
            receipts.append({'kind': 'source_transition_editor_construction', 'from': view['id'],
                             'to': copy.deepcopy(editor), 'command_ref': command['id']})
        expanded = {'id': command['id'], 'kind': 'transition', 'view_ref': editor_ref,
            'label': {locale: source['command_label'] for locale in locales}, 'confirmation': None,
            'input_field_refs': [], 'fixed_values': [{'field_ref': command['field_ref'], 'value': source['to_value']}],
            'guard': None, 'exposure': exposure}
        candidate['commands'][index] = expanded
        constructed.add(index)
        bind(source['requirement_id'], 'command', command['id'], path + '.transition_ref')
        receipts.append({'kind': 'source_transition_construction', 'from': path,
            'source_requirement_ref': source['requirement_id'], 'to': copy.deepcopy(expanded),
            'source_clause_ids': copy.deepcopy(source.get('clause_ids', []))})

    for resource_id, policy in policies.items():
        for index, command in enumerate(candidate['commands']):
            owner = views.get(command.get('view_ref')) or {}
            if (owner.get('resource_ref') == resource_id
                    and command.get('kind') in {'update', 'transition', 'delete'} and index not in constructed):
                reject('transition_scope_conflict', f'$.commands[{index}]',
                       'A source constructor cannot silently lock an unrelated or legacy mutable command')
        resources[resource_id]['read_only_when'] = policy
        receipts.append({'kind': 'source_transition_stored_state_policy', 'resource_ref': resource_id,
                         'to': copy.deepcopy(policy)})
    return candidate, receipts
