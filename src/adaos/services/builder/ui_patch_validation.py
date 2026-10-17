"""Pure validation of a small UI patch before full candidate generation.

This is a preview, not a new mutation authority. Application source remains
owned by its normal admission/revision/validation pipeline.
"""
from __future__ import annotations
import copy
from jsonschema import Draft202012Validator
from .expression_contract import expression_contract, expression_findings
from .semantic_ui import _schema, _widget_candidates
from .traceability import digest


def patch_contract():
    schema = _schema('webui.v1.schema.json')
    return {'schema':'adaos.builder.ui_patch_contract.v1', 'operations':['set'], 'max_operations':8,
            'selectors':['widget:<id>','button:<widget_id>:<button_id>','field:<widget_id>:<field_id>'],
            'property_path':'1..8 existing dictionary parents and a final property name; id is immutable',
            'source_precondition':'expected_digest must equal the canonical JSON digest of the complete input',
            'webui_schema':{'ref':'abi:webui.v1.schema.json','digest':digest(schema)},
            'expressions':expression_contract(), 'effects':'none; preview never writes or admits a change',
            'validation_scope':'syntax and platform capability contracts; browser behavior remains unverified'}


def _target(document, selector):
    parts = str(selector).split(':')
    if len(parts) not in (2,3) or parts[0] not in {'widget','button','field'}:
        raise ValueError('Use widget:<id>, button:<widget_id>:<button_id>, or field:<widget_id>:<field_id>')
    widgets = _widget_candidates(document,parts[1])
    if len(widgets)!=1:
        raise ValueError(f'Widget {parts[1]!r} must resolve exactly once; found {len(widgets)}')
    widget = widgets[0]
    if parts[0]=='widget' and len(parts)==2:
        return widget
    if len(parts)!=3:
        raise ValueError('Nested targets require their containing widget and child identity')
    collection = 'buttons' if parts[0]=='button' else 'fields'
    inputs=widget.get('inputs',{})
    collections = [inputs.get(collection,[]) if isinstance(inputs,dict) else [], widget.get(collection,[])]
    rows = [row for values in collections if isinstance(values,list) for row in values if isinstance(row,dict)]
    matched = [row for row in rows if str(row.get('id'))==parts[2]]
    if len(matched)!=1:
        raise ValueError(f'{selector!r} must resolve exactly once; available {collection}: '
                         + ', '.join(str(row.get('id')) for row in rows))
    return matched[0]


def preview_patch(document, operations, *, expected_digest, write_refs):
    original_digest = digest(document)
    findings=[]; candidate=copy.deepcopy(document)
    try:
        if not expected_digest or expected_digest!=original_digest:
            raise ValueError('stale_source_digest: re-read the exact source before proposing a patch')
        if not isinstance(operations,list) or not 1<=len(operations)<=8:
            raise ValueError('A preview requires 1..8 operations')
        for index, row in enumerate(operations):
            if (not isinstance(row,dict) or set(row)!={'operation','target_ref','property_path','value'}
                    or row['operation']!='set'):
                raise ValueError(f'Operation {index} requires set, target_ref, property_path, value')
            if row['target_ref'] not in write_refs:
                raise ValueError(f'Target {row["target_ref"]!r} is outside the declared patch write_refs')
            path=row['property_path']
            if not isinstance(path,list) or not 1<=len(path)<=8 or any(not isinstance(p,str) or not p for p in path):
                raise ValueError('property_path requires 1..8 nonempty dictionary keys')
            if path[-1]=='id':
                raise ValueError('Stable target id is immutable in a property patch')
            target=_target(candidate,row['target_ref'])
            for part in path[:-1]:
                target=target.get(part)
                if not isinstance(target,dict):
                    raise ValueError(f'Property parent {part!r} does not exist as a dictionary')
            target[path[-1]]=copy.deepcopy(row['value'])
        for error in Draft202012Validator(_schema('webui.v1.schema.json')).iter_errors(candidate):
            findings.append({'code':'webui.schema.invalid','path':list(error.absolute_path),'message':error.message})
            if len(findings)>=12:break
        findings.extend(expression_findings(candidate,path='webui'))
        from adaos.services.ui_capabilities import validate_webui_capabilities
        report=validate_webui_capabilities(candidate)
        findings.extend(report.get('findings',[]))
    except (ValueError, TypeError, KeyError) as exc:
        findings.append({'code':'ui_patch.precondition_or_operation_invalid','message':str(exc)})
    return {'schema':'adaos.builder.ui_patch_preview.v1','ok':not findings,'source_digest':original_digest,
            'candidate_digest':digest(candidate) if not findings else None,
            'document':candidate if not findings else None,'findings':findings,
            'effects':'none','behavioral_acceptance':'not_assessed','contract':patch_contract()}


def validate_tool_arguments(arguments, input_schema):
    """Validate concrete tool arguments against an owner-supplied schema.

    UI binding expressions must first be resolved by their normal runtime;
    this helper does not guess their types or grant a callable tool binding.
    """
    Draft202012Validator.check_schema(input_schema)
    errors=[{'path':list(error.absolute_path),'message':error.message}
            for error in Draft202012Validator(input_schema).iter_errors(arguments)]
    return {'schema':'adaos.builder.tool_argument_validation.v1','ok':not errors,
            'input_schema_digest':digest(input_schema),'findings':errors,'callable_binding_granted':False}
