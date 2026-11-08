"""Browser-safe records returned by the existing reviewed lifecycle service."""


def lifecycle_operation_schema():
    text = {'type': 'string'}
    nullable = {'type': ['string', 'null']}
    plan = {'type': 'object', 'properties': {
        'schema': text, 'application_id': text, 'action': {'enum': [
            'renew_beta', 'promote_stable', 'retain_local_beta', 'deactivate_beta', 'restore_stable',
            'uninstall', 'uninstall_beta', 'delete_trial', 'recover_runtime', 'select_runtime']},
        'summary': text, 'source_version': nullable, 'target_version': nullable,
        'target_position_id': {'enum': ['private_beta', 'private_stable']},
        'candidate_id': nullable, 'release_digest': nullable,
        'expected_lifecycle_revision': text, 'webspace_id': text,
        'affected_webspace_ids': {'type': 'array', 'items': text},
        'required_apply_capability': {'enum': ['applications.apply', 'applications.publish', 'applications.recover'],
            'description': 'Confirm planned operations through lifecycle.apply for applications.apply, lifecycle.publish for applications.publish (Finalize), or lifecycle.recover for applications.recover. Always pass the same operation_id, plan_digest and idempotency_key. Observe uncertain acknowledgement using applications.get_operation before another mutation.'},
        'data_policy': text, 'data_disposition': text, 'migration_required': {'type': 'boolean'},
        'acknowledge_write_loss': {'type': 'boolean'}, 'expires_at': text,
        'previous_expires_at': nullable, 'renewed_expires_at': nullable,
    }, 'additionalProperties': True}
    # Installation and retained-runtime plans use the same durable operation
    # journal. Declare both projections so reconnect observers need no guesses.
    plan['properties'].update({
        'kind': {'enum': ['install', 'update', 'remove', 'select_track', 'relocate_component', 'install_component', 'remove_component']},
        'review_summary': text, 'expected_revision': {'type': 'integer'},
        'permissions': {'type': 'array', 'items': text},
        'permission_review': {'type': ['object', 'null'], 'properties': {
            'schema': text, 'profile_digest': text, 'approval_required': {'type': 'boolean'},
            **{key: {'type': 'array', 'items': text} for key in ('required', 'optional', 'approval_permissions')},
            'items': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'id': text, 'purpose': text, 'approval_policy': text, 'sensitive': {'type': 'boolean'},
                'requirement': {'enum': ['required', 'optional']},
            }, 'additionalProperties': True}},
            'diff': {'type': ['object', 'null'], 'additionalProperties': True},
        }, 'additionalProperties': True},
        'components': {'type': 'array', 'items': {'type': 'object', 'properties': {
            **{key: text for key in ('component_ref', 'kind', 'artifact_id', 'version', 'package_digest', 'lifecycle')},
        }, 'additionalProperties': True}},
        'conflicts': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': True}},
        'compatibility': {'type': ['object', 'null'], 'properties': {
            'platform': {'type': 'object'}, 'permissions': {'type': 'array', 'items': text},
            'migration': {'type': 'object', 'properties': {'required': {'type': 'boolean'},
                'count': {'type': 'integer'}, 'rollback_mode': text}},
            'contract_locks_present': {'type': 'boolean'}, 'validation_evidence_count': {'type': 'integer'},
        }, 'additionalProperties': True},
        'snapshot': {'type': 'object', 'properties': {'required': {'type': 'boolean'}, 'mode': text,
            **{key: nullable for key in ('consistency_boundary', 'source_release_digest', 'retention')}},
            'additionalProperties': True},
        'access': {'type': ['object', 'null'], 'properties': {
            'mode': {'enum': ['publisher_local', 'public_channel', 'trial_redemption']}, 'redemption_id': nullable}},
        'runtime_target': {'type': ['object', 'null'], 'properties': {
            **{key: text for key in ('webspace_id', 'source', 'release_digest', 'runtime_root_ref')},
            'expected_revision': {'type': 'integer'}, 'previous_source': nullable, 'previous_release_digest': nullable}},
        **{key: {'type': ['object', 'null'], 'additionalProperties': True}
           for key in ('removal', 'subscription_change', 'subscription_default', 'placement_change')},
        'installation_revision': {'type': ['integer', 'null']},
    })
    return {'type': 'object', 'properties': {
        'schema': {'const': 'adaos.application.operation.v1'}, 'operation_id': text, 'application_id': text,
        'kind': text, 'status': {'enum': ['planned', 'applying', 'succeeded', 'failed', 'unknown', 'reconciling', 'cancelled']},
        'actor_ref': text, 'subnet_ref': text, 'plan_digest': text, 'idempotency_key': text,
        'expected_revision': {'type': 'integer', 'minimum': 0}, 'revision': {'type': 'integer', 'minimum': 1},
        'plan': plan, 'result': {'type': 'object', 'additionalProperties': True},
        'recovery_reason': nullable, 'created_at': text, 'updated_at': text,
    }, 'required': ['schema', 'operation_id', 'application_id', 'kind', 'status', 'plan_digest',
                    'idempotency_key', 'plan', 'result', 'revision'], 'additionalProperties': True}


def lifecycle_operation_result_schema():
    return {'type': 'object', 'properties': {
        'operation': lifecycle_operation_schema(),
        **{name: {'type': 'boolean'} for name in ('would_plan', 'would_apply', 'would_recover', 'would_publish', 'would_cancel')},
        'action': {'type': 'string'}, 'application_id': {'type': 'string'}, 'operation_id': {'type': 'string'},
    }, 'anyOf': [{'required': ['operation']}, *[{'required': [name]} for name in
                ('would_plan', 'would_apply', 'would_recover', 'would_publish', 'would_cancel')]],
        'additionalProperties': True}
