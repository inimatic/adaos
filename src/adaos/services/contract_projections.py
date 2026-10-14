"""One projection/publish seam for owned C2 and public SDK context units.

Locators address real Root descriptor items. Consumers use explicit masks and
the same immutable bytes; cache identity includes source and policy digests.
No semantic capability identity is inferred from a tool name.
"""
from __future__ import annotations
import copy
from typing import Any, Mapping, Sequence
from adaos.domain.artifact_release import canonical_payload_digest as digest
from adaos.services.ui_component_contracts import project_component_contract

CONSUMERS = ('builder.inline', 'builder.prefetch', 'root.mcp', 'e2e')


def project_context_units(*, sdk_symbols: Sequence[str] = (), snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from adaos.services.root_mcp.descriptor_search import get_descriptor_item
    snapshot = copy.deepcopy(snapshot) if snapshot is not None else project_component_contract()
    units = [{**copy.deepcopy(unit), 'consumer_masks': list(CONSUMERS), 'source_kind': 'owned_component_declaration'}
             for unit in snapshot['retrieval_units']]
    for name in sorted(set(sdk_symbols)):
        item = get_descriptor_item('sdk_metadata', name, level='std')['item']
        fingerprint = digest(item)
        units.append({'id': name, 'content': item, 'content_digest': fingerprint,
            'provenance': {'owner': 'adaos.sdk', 'source_digest': fingerprint, 'projection_version': '1'},
            'source_kind': 'public_sdk_export', 'dependencies': [], 'consumer_masks': list(CONSUMERS),
            'locator': {'descriptor_id': 'sdk_metadata', 'item_id': name, 'expected_source_digest': fingerprint}})
    result = {'schema': 'adaos.platform.context_projection.v1', 'component_source': snapshot['provenance'],
        'discovery_policy': copy.deepcopy(snapshot['discovery_policy']), 'units': units}
    result['projection_digest'] = digest(result)
    return result


def consume_context_units(projection: Mapping[str, Any], *, consumer: str, required_ids: Sequence[str]) -> dict[str, Any]:
    if consumer not in CONSUMERS:
        raise ValueError('context.consumer_mask_unknown')
    if projection.get('projection_digest') != digest({key: value for key, value in projection.items() if key != 'projection_digest'}):
        raise ValueError('context.projection_integrity_conflict')
    by_id = {unit['id']: unit for unit in projection['units']}
    closure = set(required_ids)
    while True:
        if not closure <= set(by_id):
            raise ValueError('context.required_projection_missing')
        expanded = closure | {dep for name in closure for dep in by_id[name]['dependencies']}
        if expanded == closure:
            break
        closure = expanded
    if any(consumer not in by_id[name]['consumer_masks'] for name in closure):
        raise ValueError('context.consumer_not_admitted')
    return {'projection_digest': projection['projection_digest'], 'required_closure': sorted(closure),
            'units': [copy.deepcopy(unit) for unit in projection['units'] if unit['id'] in closure]}


def publish_context_units(projection: Mapping[str, Any], *, read=None) -> dict[str, Any]:
    """Verify the common Root read projector before publishing a reuse receipt."""
    from adaos.services.root_mcp.descriptor_search import get_descriptor_item
    read = read or get_descriptor_item
    units = consume_context_units(projection, consumer='root.mcp', required_ids=[unit['id'] for unit in projection['units']])
    for unit in units['units']:
        item = read(**unit['locator'])['item']
        actual = item.get('component_contract') if unit['source_kind'] == 'owned_component_declaration' else item
        expected = {key: value for key, value in unit.items() if key not in ('consumer_masks', 'source_kind')} if unit['source_kind'] == 'owned_component_declaration' else unit['content']
        if actual != expected:
            raise ValueError('context.published_projection_stale:' + unit['id'])
    return {'schema': 'adaos.platform.context_publication.v1', 'status': 'verified',
        'projection_digest': projection['projection_digest'], 'locators': [unit['locator'] for unit in units['units']],
        'consumer_masks': {unit['id']: unit['consumer_masks'] for unit in units['units']}}
