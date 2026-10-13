"""Bounded read-only semantic identity and write-set projection.

Only semantic descriptors and checked operation values are exposed. Records,
credentials, provider identifiers and private recovery images are not returned.
The compiler still validates the full source; dependency hints are not a claim
that a minimal model read context has been qualified.
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any, Mapping

from .workflow import BuilderWorkflowError


def field_ref(resource_id: str, identity: str) -> str:
    return 'field:' + (identity if identity.startswith(resource_id + '.') else f'{resource_id}.{identity}')


def semantic_refs(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .semantic_graph import supports_field_order, scope_property
    nodes = []
    keys = Counter()
    def text_keys(value):
        if isinstance(value, Mapping):
            if 'key' in value and ('en' in value or 'ru' in value):
                keys[value['key']] += 1
            for item in value.values():
                text_keys(item)
        elif isinstance(value, list):
            for item in value:
                text_keys(item)
    text_keys(document)

    def add(ref, kind, label=None, dependencies=(), writes=()):
        label = label or {}
        nodes.append({'ref': ref, 'kind': kind, 'label_en': label.get('en', ''), 'label_ru': label.get('ru', ''),
                      'dependencies': sorted(set(dependencies)), 'supported_writes': list(writes)})

    def label_writes(label, property_name):
        return [f'{property_name}.{locale}' for locale in ('en', 'ru')
                if locale in label and keys[label['key']] == 1]

    resources = {item['id']: item for item in document['resources']}
    views = {item['id']: item for item in document['views']}
    field_relationships = {}
    for relation in document.get('relationships', []):
        field_relationships.setdefault(field_ref(relation['from_resource_ref'], relation['from_field_ref']), []).append(f"relationship:{relation['id']}")
    for resource in resources.values():
        resource_ref = f"resource:{resource['id']}"
        add(resource_ref, 'resource', resource['item_label'])
        declared = set()
        for field in resource['fields']:
            ref = field_ref(resource['id'], field['id'])
            declared.add(ref)
            add(ref, 'field', field['label'], [resource_ref, *field_relationships.get(ref, [])], label_writes(field['label'], 'label'))
        for identity in resource['identity_field_refs']:
            ref = field_ref(resource['id'], identity)
            if ref not in declared:
                add(ref, 'identity_field', {'en': identity, 'ru': identity}, [resource_ref])
    for view in views.values():
        writes = label_writes(view['title'], 'title')
        if supports_field_order(document, view):
            writes.append('field_refs')
        if view['role'] == 'collection' and view.get('presentation') == 'table':
            writes.extend(scope_property(item['field_ref']) for item in view.get('scope_filters', []))
        add(f"view:{view['id']}", 'view', view['title'],
            [f"resource:{view['resource_ref']}", *[field_ref(view['resource_ref'], ref) for ref in view['field_refs']],
             *[field_ref(view['resource_ref'], item['field_ref']) for item in view.get('scope_filters', [])]], writes)
    for relation in document.get('relationships', []):
        dependencies = [
            f"resource:{relation['from_resource_ref']}", f"resource:{relation['to_resource_ref']}",
            field_ref(relation['from_resource_ref'], relation['from_field_ref']),
            field_ref(relation['to_resource_ref'], relation['to_field_ref']),
            *[field_ref(relation['to_resource_ref'], ref) for ref in relation.get('label_field_refs', [])]]
        if not relation.get('label_field_refs'):
            target = next((view for view in views.values() if view['resource_ref'] == relation['to_resource_ref'] and view['role'] == 'collection'), None)
            if target:
                dependencies.append(f"view:{target['id']}")
        add(f"relationship:{relation['id']}", 'relationship', dependencies=dependencies)
    for command in document['commands']:
        view = views[command['view_ref']]
        fields = set(command.get('input_field_refs', [])) | set(command.get('fixed_values', {}))
        guard = command.get('guard') or {}
        fields.update(guard.get('require_nonempty', []))
        def predicate_fields(value):
            if isinstance(value, Mapping):
                if value.get('field_ref'):
                    fields.add(value['field_ref'])
                for child in value.values():
                    predicate_fields(child)
            elif isinstance(value, list):
                for child in value:
                    predicate_fields(child)
        predicate_fields(guard.get('when'))
        add(f"command:{command['id']}", 'command', command['label'],
            [f"view:{view['id']}", *[field_ref(view['resource_ref'], ref) for ref in fields]])
    if len(nodes) > 1024 or len({item['ref'] for item in nodes}) != len(nodes):
        raise BuilderWorkflowError('semantic reference inventory exceeds capacity or has duplicate refs')
    for node in nodes:
        node.update(dependencies_text=', '.join(node['dependencies']),
                    writes_text=', '.join(node['supported_writes']) or '—')
    return nodes


def dependency_refs(nodes: list[dict[str, Any]], roots: list[str]) -> list[str]:
    index = {item['ref']: item for item in nodes}
    visited = set()
    pending = list(roots)
    while pending:
        ref = pending.pop()
        if ref in visited:
            continue
        if ref not in index:
            raise BuilderWorkflowError('operation references are absent from the semantic inventory')
        visited.add(ref)
        pending.extend(index[ref]['dependencies'])
    return sorted(visited)


def write_rows(ops) -> list[dict[str, Any]]:
    from .semantic_graph import operation_write_set
    import json
    rows = operation_write_set(ops)
    for item in rows:
        item.update(before_text=json.dumps(item['before'], ensure_ascii=False),
                    after_text=json.dumps(item['after'], ensure_ascii=False))
    return copy.deepcopy(rows)
