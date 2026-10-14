"""Provider-neutral mutation obligations carried by resource declarations.

Storage providers may differ; command identity, draft inputs and fixed effects
must not. Confirmation is a renderer obligation, not a claim of server auth.
"""
from __future__ import annotations
from typing import Any, Mapping
import json
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError


def validate_command_policy(policy: Mapping[str, Any], record_schema: Mapping[str, Any]) -> None:
    if not isinstance(policy, Mapping) or not isinstance(policy.get('commands', {}), Mapping):
        raise ValueError('resource command policy must be an owned mapping')
    definitions = json.loads((Path(__file__).resolve().parents[2]/'abi/webui.semantic.v1.schema.json').read_text(encoding='utf-8'))['$defs']
    validator = Draft202012Validator({'type': 'object', 'additionalProperties': False,
        'required': ['kind', 'input_field_refs', 'fixed_values', 'required_field_refs'], 'properties': {
            'kind': {'enum': ['create', 'update', 'delete', 'transition']},
            'input_field_refs': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'string'}},
            'required_field_refs': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'string'}},
            'guard_draft_field_refs': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'string'}},
            'fixed_values': {'type': 'object'}, 'guard': {'type': 'object', 'additionalProperties': False,
                'required': ['when', 'require_nonempty'], 'properties': {'when': {'$ref': '#/$defs/condition'},
                    'require_nonempty': {'type': 'array', 'minItems': 1, 'uniqueItems': True, 'items': {'type': 'string'}}}}},
        '$defs': definitions})
    fields = record_schema['properties']
    for command_id, command in policy.get('commands', {}).items():
        try:
            validator.validate(command)
        except ValidationError as exc:
            raise ValueError('invalid resource command declaration: ' + exc.message) from exc
        refs = set(command['input_field_refs']) | set(command['fixed_values']) | set(command.get('guard_draft_field_refs', []))
        if not set(command['required_field_refs']) <= set(command['input_field_refs']):
            raise ValueError('required inputs exceed the command write set')
        if command.get('guard'):
            refs |= set(command['guard']['require_nonempty']) | {command['guard']['when']['field_ref']}
        if not command_id or not refs <= set(fields):
            raise ValueError('command references an unknown property')
        for field, fixed in command['fixed_values'].items():
            try:
                Draft202012Validator(fields[field]).validate(fixed)
            except ValidationError as exc:
                raise ValueError('command fixed effect conflicts with the record schema') from exc
    if policy.get('read_only_when'):
        try:
            Draft202012Validator({'$defs': definitions, '$ref': '#/$defs/condition'}).validate(policy['read_only_when'])
        except ValidationError as exc:
            raise ValueError('invalid resource read-only declaration: ' + exc.message) from exc
        if policy['read_only_when']['field_ref'] not in fields:
            raise ValueError('read-only condition references an unknown property')
    for relation in policy.get('relationships', []):
        if not isinstance(relation, Mapping) or not {'field_ref', 'target_resource_type', 'target_field_ref'} <= set(relation):
            raise ValueError('invalid resource relationship declaration')
        if relation['field_ref'] not in fields or not str(relation['target_resource_type']).startswith('skill.'):
            raise ValueError('production relationship must bind a declared local resource property')


def condition_matches(record: Mapping[str, Any], condition: Mapping[str, Any]) -> bool:
    value: Any = record
    for part in condition['field_ref'].split('.'):
        value = value.get(part) if isinstance(value, Mapping) else None
    expected = condition.get('value')
    operator = condition['operator']
    if operator in {'empty', 'nonempty'}:
        empty = value is None or value == '' or value == []
        return empty if operator == 'empty' else not empty
    if operator not in {'equals', 'not_equals'}:
        raise ValueError('unsupported resource command predicate')
    equal = isinstance(value, bool) == isinstance(expected, bool) and value == expected
    return equal if operator == 'equals' else not equal


def normalize_mutation_payload(operation_kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    wrapper = {'create': 'record', 'update': 'patch'}.get(operation_kind)
    if wrapper and wrapper in payload:
        if not isinstance(payload[wrapper], Mapping):
            raise ValueError(f'{operation_kind} requires an object {wrapper}')
        return dict(payload[wrapper])
    return dict(payload)


def enforce_command_policy(policy: Mapping[str, Any], *, operation_kind: str,
                           command_id: str | None, values: Mapping[str, Any],
                           current: Mapping[str, Any] | None,
                           guard_draft: Mapping[str, Any] | None = None,
                           record_schema: Mapping[str, Any] | None = None) -> None:
    commands = policy.get('commands') or {}
    if commands and operation_kind in {'create', 'update', 'delete', 'reset'}:
        command = commands.get(command_id or '')
        if command is None:
            raise ValueError('mutation requires an admitted command identity')
        expected_kind = 'update' if command['kind'] == 'transition' else command['kind']
        if operation_kind != expected_kind:
            raise ValueError('command operation mismatch')
        allowed = set(command['input_field_refs']) | set(command['fixed_values'])
        if not (set(values) - {'id', 'revision'}) <= allowed or any(values.get(key) != value for key, value in command['fixed_values'].items()):
            raise ValueError('command payload exceeds its declared effect')
        draft = {**dict(current or {}), **{key: value for key, value in values.items()
            if key in command['input_field_refs'] and key not in command['fixed_values']}}
        if guard_draft is not None:
            if not isinstance(guard_draft, Mapping) or record_schema is None:
                raise ValueError('guard draft requires typed resource values')
            try:
                Draft202012Validator({'type': 'object', 'properties': record_schema['properties'],
                    'additionalProperties': False}).validate(dict(guard_draft))
            except ValidationError as exc:
                raise ValueError('guard draft conflicts with the resource schema') from exc
            draft.update({key: value for key, value in guard_draft.items()
                if key in command.get('guard_draft_field_refs', [])})
            draft.update({key: value for key, value in values.items()
                if key in command['input_field_refs'] and key not in command['fixed_values']})
        if any(draft.get(key) in (None, '', []) for key in command['required_field_refs']):
            raise ValueError('command required input rejected the mutation')
        guard = command.get('guard')
        if guard and condition_matches(draft, guard['when']) and any(draft.get(key) in (None, '', []) for key in guard['require_nonempty']):
            raise ValueError('command guard rejected the mutation')
    condition = policy.get('read_only_when')
    if operation_kind in {'update', 'delete'} and current is not None and condition and condition_matches(current, condition):
        raise ValueError('record is read-only in its current state')
