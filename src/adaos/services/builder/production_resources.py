"""Explicit lowering into the real durable local CRUD resource provider.

Semantic command identity survives physical resource/provider replacement.
Prototype records are never implicit production installation seeds.
"""
from __future__ import annotations
import copy
from typing import Any, Mapping


def lower_local_resource(definition: Mapping[str, Any], *, owner_skill: str,
                         resource_type: str, resource_map: Mapping[str, str]) -> dict[str, Any]:
    result = copy.deepcopy(dict(definition))
    result.update(resource_type=resource_type, version='1.0.0', record_schema_ref='inline:'+resource_type,
        authority={'provider': 'local_crud', 'binding': owner_skill, 'writes': 'optimistic', 'source_of_truth': 'local_skill_state'},
        scope={'owner': 'skill:'+owner_skill})
    result['operations'] = [{key: value for key, value in item.items() if key != 'prototype_activity_id'}
        for item in result['operations'] if item.get('kind') in {'list', 'show', 'create', 'update', 'delete'}]
    metadata = result.setdefault('metadata', {})
    policy = metadata.pop('prototype_policy', None)
    if policy is not None:
        for relationship in policy.get('relationships', []):
            relationship['target_resource_type'] = resource_map[relationship['target_resource_type']]
        metadata['command_policy'] = policy
    for key in ('prototype', 'project_ref', 'change_id', 'revision', 'webui_digest'):
        metadata.pop(key, None)
    if 'relationships' in result:
        for relationship in result['relationships']:
            if relationship.get('target_resource_type') in resource_map:
                relationship['target_resource_type'] = resource_map[relationship['target_resource_type']]
    result['privacy'] = {**result.get('privacy', {}), 'sensitivity': 'workspace', 'retention': 'skill_owned', 'external_export': 'denied'}
    return {'schema': 'adaos.resource.local_crud.v1', 'owner_ref': 'skill:'+owner_skill,
        'seed_policy': 'if_missing', 'seed': [], 'resource_definition': result}
