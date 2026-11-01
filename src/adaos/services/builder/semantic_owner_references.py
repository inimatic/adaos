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


def owner_reference_contract():
    """Enforced rules for inspection and the opt-in isolated-input authority."""
    return {'reference_form': 'Exact target_array[].id from this candidate. Labels, section ids and record ids are not substitute owners.',
        'rules': [{'source_array': source, 'reference_field': field, 'target_array': target,
                   'target_key': 'id'} for source, field, target, _ in OWNER_EDGES],
        'field_scope': "Resolve a command's view_ref in views, then its resource_ref in resources. "
            'input_field_refs and fixed_values[].field_ref name fields of THAT resource. '
            'All refs use exact DECLARED candidate field IDs. Source spelling and values remain in source assertions; '
            'a candidate field may use a different valid ID only with an unambiguous mapping to that source field. '
            'For example source_field=текст may map to field.id=text with label.ru=Текст; refs then use text, not текст. '
            'A display label is not a reference ID and ambiguous field mappings are rejected.'}


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
