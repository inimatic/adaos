"""Structural owner edges must resolve before per-resource canonicalization."""
from __future__ import annotations

from collections.abc import Mapping


OWNER_EDGES = (
    ('views', 'resource_ref', 'resources', 'view'),
    ('commands', 'view_ref', 'views', 'command'),
    ('representative_states', 'view_ref', 'views', 'state'),
    ('relationships', 'from_resource_ref', 'resources', 'relationship'),
    ('relationships', 'to_resource_ref', 'resources', 'relationship'),
)


def missing_owner_edges(candidate: Mapping):
    targets = []
    for collection, field, destination, kind in OWNER_EDGES:
        known = sorted({row['id'] for row in candidate.get(destination, [])})
        for index, row in enumerate(candidate.get(collection, [])):
            if row.get(field) not in known:
                targets.append({'path': f'$.{collection}[{index}].{field}',
                    'collection': collection, 'identity': row['id'], 'field': field,
                    'value': row.get(field), 'allowed_refs': known,
                    'semantic_refs': [kind + ':' + row['id']]})
    return targets


def owner_reference_findings(candidate: Mapping):
    return [{'code': ('semantic.relationship_reference_missing' if target['collection']=='relationships'
        else 'semantic.owner_reference_missing'), 'gate': 'G7', 'owner': 'candidate',
        'path': target['path'], 'semantic_refs': target['semantic_refs'],
        'detail': 'Owner edge must name a declared identity: ' + target['path'],
        'unknown_reference': target['value'], 'allowed_refs': target['allowed_refs']}
        for target in missing_owner_edges(candidate)]


def collection_owner_findings(document: Mapping):
    findings = []
    for index, resource in enumerate(document.get('resources', [])):
        views = [view for view in document.get('views', []) if view['resource_ref']==resource['id']]
        if views and not any(view['role']=='collection' for view in views):
            findings.append({'code':'semantic.resource_collection_missing','gate':'G7','owner':'candidate',
                'path':f'$.resources[{index}]','semantic_refs':['resource:'+resource['id']],
                'detail':f"resource {resource['id']!r} requires a collection view; add role='collection' for this resource or remove the resource when it is not part of the requested UI"})
    return findings
