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


def lower_constructions(value, brief, schema):
    from .semantic_prototype import SemanticPrototypeValidationError

    candidate = copy.deepcopy(value)
    receipts = []
    allowed = {row['id'] for row in prototype_model_requirement_inventory(brief)}

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
        for reference in claims:
            bind(reference, kind, identity or owner.get('id'), path + '.fulfills')

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

    transitions = {row['requirement_id']: row
                   for row in (brief.get('task_contract') or {}).get('transitions', [])}
    resources = {row.get('id'): row for row in candidate.get('resources', []) if isinstance(row, Mapping)}
    views = {row.get('id'): row for row in candidate.get('views', []) if isinstance(row, Mapping)}
    policies = {}
    constructed = set()
    for index, command in enumerate(candidate.get('commands') or []):
        if not isinstance(command, dict) or command.get('kind') != 'source_transition':
            continue
        path = f'$.commands[{index}]'
        validate(command, 'sourceTransitionCommand', path)
        source = transitions.get(command['transition_ref'])
        if source is None:
            reject('transition_unknown', path, 'transition_ref must name an admitted task transition requirement_id')
        view = views.get(command['view_ref'])
        resource = resources.get((view or {}).get('resource_ref'))
        if not view or view.get('role') != 'editor' or resource is None:
            reject('transition_owner_invalid', path, 'Source transition requires an existing editor and resource')
        if command['field_ref'] not in {field.get('id') for field in resource.get('fields', [])}:
            reject('transition_field_unknown', path, 'field_ref must name a field of the editor resource')
        policy = {'field_ref': command['field_ref'], 'operator': 'not_equals', 'value': source['from_value']}
        previous = policies.get(resource['id'])
        if previous is not None and previous != policy:
            reject('transition_scope_conflict', path, 'Current ABI cannot apply different stored-state preconditions to one resource')
        if resource.get('read_only_when') is not None and resource['read_only_when'] != policy:
            reject('transition_policy_conflict', path, 'Explicit resource policy contradicts the admitted source transition')
        policies[resource['id']] = policy
        locales = [locale for locale in candidate.get('title', {}) if locale in {'en', 'ru'}]
        expanded = {'id': command['id'], 'kind': 'transition', 'view_ref': command['view_ref'],
            'label': {locale: source['command_label'] for locale in locales}, 'confirmation': None,
            'input_field_refs': [], 'fixed_values': [{'field_ref': command['field_ref'], 'value': source['to_value']}],
            'guard': None, 'exposure': copy.deepcopy(command['exposure'])}
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
