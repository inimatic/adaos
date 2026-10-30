"""Lossless, bounded delivery projections of Core-owned provider schemas."""
from __future__ import annotations

import copy


class SchemaProjectionError(ValueError):
    pass


def coalesce_primitive_unions(schema):
    """Use type arrays only for unconstrained anyOf primitive type branches.

    This is an equivalent delivery grammar, not removal of strictness, enum
    values or branch assertions. Annotated/constrained branches stay intact.
    Literal objects under const/enum/default are data, never schema programs.
    """
    primitive = {'string', 'number', 'integer', 'boolean', 'null'}
    maps = {'properties', '$defs', 'definitions', 'patternProperties'}
    arrays = {'anyOf', 'oneOf', 'allOf', 'prefixItems'}
    children = {'items', 'additionalProperties', 'contains', 'not', 'if', 'then', 'else', 'propertyNames'}
    nodes = 0

    def visit(node, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 50000 or depth > 128:
            raise SchemaProjectionError('Schema exceeds the bounded union projection capacity')
        if not isinstance(node, dict):
            return copy.deepcopy(node)
        result = {}
        for key, value in node.items():
            if key in maps and isinstance(value, dict):
                result[key] = {name:visit(child,depth+1) for name,child in value.items()}
            elif key in arrays and isinstance(value, list):
                result[key] = [visit(child,depth+1) for child in value]
            elif key in children and isinstance(value, (dict,bool)):
                result[key] = visit(value,depth+1)
            else:
                result[key] = copy.deepcopy(value)
        branches = result.get('anyOf')
        if (set(result) <= {'anyOf','description','title','$comment'} and branches
                and isinstance(branches,list) and all(isinstance(row,dict) and set(row)=={'type'}
                    and isinstance(row['type'],str) and row['type'] in primitive for row in branches)):
            result['type'] = list(dict.fromkeys(row['type'] for row in result.pop('anyOf')))
        return result

    return visit(schema)


def inline_local_references(schema):
    """Inline an acyclic schema without dropping assertions or fetching URLs.

    Intended for explicit provider compatibility qualification, not arbitrary
    schema simplification. Expanded capacity must be admitted by the caller.
    """
    maps = {'properties', '$defs', 'definitions', 'patternProperties'}
    arrays = {'anyOf', 'oneOf', 'allOf', 'prefixItems'}
    children = {'items', 'additionalProperties', 'contains', 'not', 'if', 'then', 'else', 'propertyNames'}
    nodes = 0

    def visit(node, stack=(), depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 50000 or depth > 128:
            raise SchemaProjectionError('Expanded schema exceeds the bounded projection capacity')
        if not isinstance(node, dict):
            return copy.deepcopy(node)
        if '$ref' in node:
            ref = node['$ref']
            if set(node) != {'$ref'} or not isinstance(ref, str) or not ref.startswith('#/'):
                raise SchemaProjectionError('Only assertion-free local reference nodes are supported')
            if ref in stack or len(stack) >= 32:
                raise SchemaProjectionError('Recursive schema cannot be inlined without losing assertions')
            target = schema
            for part in ref[2:].split('/'):
                part = part.replace('~1', '/').replace('~0', '~')
                if not isinstance(target, dict) or part not in target:
                    raise SchemaProjectionError('Schema reference does not resolve')
                target = target[part]
            return visit(target, (*stack, ref), depth+1)
        result = {}
        for key, value in node.items():
            if key in {'$defs', 'definitions'}:
                continue  # Referenced definitions are materialized where used.
            if key in maps and isinstance(value, dict):
                result[key] = {name: visit(child, stack, depth+1) for name, child in value.items()}
            elif key in arrays and isinstance(value, list):
                result[key] = [visit(child, stack, depth+1) for child in value]
            elif key in children and isinstance(value, (dict, bool)):
                result[key] = visit(value, stack, depth+1)
            else:
                # enum/const values are data, never recursive schema locations.
                result[key] = copy.deepcopy(value)
        return result

    return visit(schema)
