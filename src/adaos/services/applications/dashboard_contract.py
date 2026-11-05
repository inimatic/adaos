"""Public read contract for the Core-owned Application instrument panel."""


def dashboard_schema():
    nullable_text = {"type": ["string", "null"]}
    action = {"type": "object", "properties": {
        "eligible": {"type": "boolean"}, "reason": nullable_text,
        "required_capability": nullable_text, "requires_authorization": {"type": "boolean"},
        "requires_review": {"type": "boolean"}, "mode": {"enum": ["open", "switch_and_open", "navigate"]},
        "tool_id": nullable_text, "arguments": {"type": "object"}},
        "required": ["eligible"], "additionalProperties": True}
    def bound(tool, properties, required):
        return {'allOf': [action, {'if': {'properties': {'eligible': {'const': True}}, 'required': ['eligible']},
            'then': {'required': ['tool_id', 'arguments'], 'properties': {'tool_id': {'const': tool}, 'arguments': {'type': 'object',
                'properties': properties, 'required': required, 'additionalProperties': False}}}}]}

    actions = {name: action for name in ('preview', 'builder', 'dev_tickets', 'open', 'finalize', 'remove_beta',
        'renew_beta', 'restore_stable', 'update', 'publish_initial_stable', 'publish_beta',
        'select_release', 'promote_public_stable')}
    actions['finalize'] = bound('applications.lifecycle.finalize', {
        'application_id': {'type': 'string', 'minLength': 1},
        'candidate_id': {'type': 'string', 'minLength': 1},
        'release_digest': {'type': 'string', 'minLength': 1},
        'expected_revision': {'type': 'string', 'minLength': 1}},
        ['application_id', 'candidate_id', 'release_digest', 'expected_revision'])
    actions['finalize']['description'] = ('Open a read-only review of this exact identity; submit the named tool once '
        'after confirmation with a durable idempotency_key and current webspace_id. Core owns completion; do not '
        'create an intermediate lifecycle.plan or require a second acceptance.')
    for name, command in (('remove_beta', 'deactivate_beta'),
                          ('renew_beta', 'renew_beta'), ('restore_stable', 'restore_stable')):
        actions[name] = bound('applications.lifecycle.plan', {'action': {'const': command},
            'candidate_id': nullable_text}, ['action', 'candidate_id'])
    selection = {'action': {'const': 'select_runtime'}, 'target_position_id': {'enum': ['private_beta', 'private_stable']},
                 'release_digest': {'type': 'string'}, 'candidate_id': nullable_text}
    public_selection = {'kind': {'enum': ['install', 'update']}, 'release_digest': {'type': 'string'},
                        'update_track': {'enum': ['stable', 'prerelease']}}
    actions['open'] = {'allOf': [action, {'if': {'properties': {'eligible': {'const': True},
        'mode': {'const': 'switch_and_open'}}, 'required': ['eligible', 'mode']}, 'then': {'anyOf': [
            bound('applications.lifecycle.plan', selection, ['action', 'target_position_id', 'release_digest']),
            bound('applications.plan', public_selection, ['kind', 'release_digest', 'update_track'])]}}],
        'description': 'mode=open navigates to the Core effective target without mutation; mode=switch_and_open reviews the exact named operation. Add application_id, idempotency_key and the revision required by the named tool. lifecycle.plan uses dashboard.lifecycle_revision; publication tools use application.revision.'}
    actions['update'] = bound('applications.lifecycle.plan', selection, ['action', 'target_position_id', 'release_digest', 'candidate_id'])
    for name in ('publish_initial_stable', 'promote_public_stable'):
        actions[name] = bound('applications.development.publish_to_registry',
            {'release_digest': {'type': 'string'}}, ['release_digest'])
    actions['publish_beta'] = bound('applications.development.publish_prerelease',
        {'candidate_id': nullable_text, 'expected_prerelease_digest': nullable_text},
        ['candidate_id', 'expected_prerelease_digest'])
    actions['select_release'] = bound('applications.list_releases', {}, [])
    for name in ('preview', 'builder', 'dev_tickets'):
        actions[name] = {'allOf': [action], 'description': 'Navigate to the selected Application development context; this grants no runtime selection or mutation.'}
    position = {"type": "object", "properties": {
        "id": {"enum": ["private_alpha", "private_beta", "private_stable", "public_beta", "public_stable"]},
        "scope": {"enum": ["private", "public"]}, "stage": {"enum": ["alpha", "beta", "stable"]},
        "version": {"type": "string"}, "release_digest": nullable_text, "candidate_id": nullable_text,
        "source": {"type": "string"}, "active": {"type": "boolean"},
        "availability": {"enum": ["development", "ready", "expired", "unavailable"]},
        "hidden_on_desktop": {"type": "boolean"}, "available_at": nullable_text,
        "updated_at": nullable_text, "expires_at": nullable_text, "admission_kind": nullable_text,
        "update_version": nullable_text, "update_available_at": nullable_text,
        "actions": {"type": "object", "properties": actions, "additionalProperties": False}},
        "required": ["id", "scope", "stage", "version", "active", "availability", "hidden_on_desktop", "actions"],
        "additionalProperties": True}
    return {"type": "object", "properties": {
        "schema": {"const": "adaos.application.dashboard.v1"}, "application_id": {"type": "string"},
        "revision": {"type": "string"}, "lifecycle_revision": nullable_text, "observed_at": nullable_text,
        "freshness": {"enum": ["observed", "unavailable"]}, "publisher_local": {"type": "boolean"},
        "active_position_id": nullable_text, "positions": {"type": "array", "maxItems": 5, "items": position},
        "groups": {"type": "array", "maxItems": 2, "items": {"type": "object", "properties": {
            "id": {"enum": ["private", "public"]}, "position_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "position_ids"], "additionalProperties": False}},
        "operation": {"type": ["object", "null"]}, "read_only": {"const": True}},
        "required": ["schema", "application_id", "revision", "publisher_local", "positions", "groups", "read_only"],
        "additionalProperties": True}
