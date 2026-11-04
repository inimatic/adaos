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
            'target_inspection':'adaos.sdk.builder.semantic_ui.describe_patch_target; inspect the exact target/property with expected_digest before proposing input edits',
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


def _resolve_schema(root, value):
    for _ in range(16):
        ref = value.get('$ref') if isinstance(value, dict) else None
        if not ref:
            return value
        if not ref.startswith('#/'):
            raise ValueError('Only owned local ABI references may be inspected')
        value = root
        for part in ref[2:].split('/'):
            value = value[part.replace('~1', '/').replace('~0', '~')]
    raise ValueError('Cyclic ABI reference')


def _input_schema(root, widget_type):
    def walk(value):
        if isinstance(value, dict):
            condition = value.get('if', {}).get('properties', {}).get('type', {})
            if condition.get('const') == widget_type or widget_type in condition.get('enum', []):
                inputs = value.get('then', {}).get('properties', {}).get('inputs')
                if inputs is not None:
                    return _resolve_schema(root, inputs)
            for child in value.values():
                found = walk(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found is not None:
                    return found
        return None
    result = walk(root)
    if result is None:
        raise ValueError('No owned input schema for widget type ' + str(widget_type))
    return result


def describe_patch_target(document, target_ref, property_path, *, expected_digest):
    """Read the actual ABI property, never infer an alias or grant write scope."""
    source_digest = digest(document)
    result = {'schema':'adaos.builder.ui_patch_target.v1', 'ok':False,
              'source_digest':source_digest, 'target_ref':target_ref, 'effects':'none'}
    try:
        if not expected_digest or expected_digest != source_digest:
            raise ValueError('stale_source_digest: re-read the exact source')
        if not isinstance(property_path, list) or not 1 <= len(property_path) <= 8:
            raise ValueError('property_path requires 1..8 keys')
        _target(document, target_ref)
        parts = str(target_ref).split(':')
        widget = _widget_candidates(document, parts[1])[0]
        root = _schema('webui.v1.schema.json')
        node = _input_schema(root, widget['type'])
        path = list(property_path)
        if parts[0] == 'widget':
            if path.pop(0) != 'inputs':
                raise ValueError('Target inspection supports declared widget inputs')
        else:
            collection = 'buttons' if parts[0] == 'button' else 'fields'
            node = _resolve_schema(root, node['properties'][collection])['items']
        for key in path:
            node = _resolve_schema(root, node)
            properties = node.get('properties', {})
            if key not in properties:
                result['available_properties'] = sorted(properties)
                raise ValueError('Undeclared input property: ' + str(key))
            node = properties[key]
        node = _resolve_schema(root, node)
        # Preserve local refs with only their dependency closure. Consumers do
        # not need the complete ABI merely to edit a label or metadata entry.
        definitions = {}
        def include_refs(value):
            if isinstance(value, dict):
                ref = value.get('$ref', '')
                if ref.startswith('#/$defs/'):
                    name = ref.split('/')[2]
                    if name not in definitions:
                        definitions[name] = root['$defs'][name]
                        include_refs(definitions[name])
                for child in value.values(): include_refs(child)
            elif isinstance(value, list):
                for child in value: include_refs(child)
        include_refs(node)
        if len(definitions) > 48:
            raise ValueError('Property contract is too broad; inspect a narrower property')
        result.update(ok=True, widget_type=widget['type'], property_path=property_path,
                      property_schema={**copy.deepcopy(node), **({'$defs':definitions} if definitions else {})},
                      abi_digest=digest(root))
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        result['error'] = {'code':'ui_patch.target_contract_unavailable', 'message':str(exc)}
    return result


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
    contracts = [describe_patch_target(document, row.get('target_ref'), row.get('property_path'), expected_digest=original_digest)
                 for row in operations[:8] if isinstance(row, dict)] if isinstance(operations, list) else []
    return {'schema':'adaos.builder.ui_patch_preview.v1','ok':not findings,'source_digest':original_digest,
            'candidate_digest':digest(candidate) if not findings else None,
            'document':candidate if not findings else None,'findings':findings,
            'operation_contracts':contracts,
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
