"""Typed observation paths for the existing publication operation journal."""
from copy import deepcopy


def publication_operation_schema():
    text = {'type': 'string'}
    return {'type': 'object', 'properties': {
        'schema': {'const': 'adaos.application.development_operation.v1'},
        **{key: text for key in ('operation_id', 'application_id', 'action', 'actor_ref', 'subnet_ref',
            'capability', 'idempotency_key', 'intent_digest', 'created_at', 'updated_at')},
        'status': {'enum': ['planned', 'applying', 'succeeded', 'failed', 'unknown']},
        'intent': {'type': 'object', 'properties': {
            **{key: text for key in ('release_digest', 'candidate_id', 'release_notes', 'mode')},
            'expected_prerelease_digest': {'type': ['string', 'null']},
        }, 'additionalProperties': True},
        'result': {'type': 'object', 'properties': {
            'ok': {'type': 'boolean'}, 'status': text, 'message': text, 'error': {},
        }, 'additionalProperties': True},
        'recovery_reason': {'type': ['string', 'null']},
        'expected_revision': {'type': 'integer'}, 'revision': {'type': 'integer'},
    }, 'required': ['schema', 'operation_id', 'application_id', 'action', 'status',
        'idempotency_key', 'intent', 'result', 'revision'], 'additionalProperties': True}


def typed_publication_response(tool_id, envelope):
    suffix = tool_id.removeprefix('applications.development.')
    operation = publication_operation_schema()
    if suffix == 'list_operations':
        result = {'type': 'object', 'properties': {'operations': {'type': 'array', 'items': operation}}, 'required': ['operations']}
    elif suffix in {'get_operation', 'reconcile_operation'}:
        result = {'type': 'object', 'properties': {'operation': operation,
            'would_reconcile_operation': {'const': True}, 'operation_id': {'type': 'string'}},
            'anyOf': [{'required': ['operation']}, {'required': ['would_reconcile_operation']}]}
    elif suffix in {'publish_to_registry', 'publish_stable_source', 'publish_prerelease', 'promote_stable'}:
        result = {'anyOf': [operation, {'type': 'object', 'properties': {
            'would_' + suffix: {'const': True}, 'request': {'type': 'object'}},
            'required': ['would_' + suffix, 'request']}]}
    else:
        return None
    response = deepcopy(envelope)
    response['properties']['result'] = result
    return response
