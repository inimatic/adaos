"""Bounded private progress facts projected from existing Core operation owners.

Payloads, provider responses and raw errors stay in their private stores. The
event retains their identity and explicit references, not copies of their data.
Public release notes continue to require an exact successful publication intent.
"""
from collections.abc import Mapping
import re

from adaos.domain.artifact_release import canonical_payload_digest


def development_progress(operation: Mapping) -> dict:
    if operation.get('schema') != 'adaos.application.development_operation.v1':
        raise ValueError('Development operation required for project journal')
    facts = {key: operation[key] for key in (
        'schema', 'operation_id', 'application_id', 'action', 'status', 'actor_ref',
        'subnet_ref', 'capability', 'expected_revision', 'intent_digest', 'revision',
        'created_at', 'updated_at', 'recovery_attempt') if key in operation}
    facts['operation_digest'] = canonical_payload_digest(dict(operation))
    facts['intent'] = _references(operation.get('intent'))
    checkpoint = (operation.get('intent') or {}).get('source_checkpoint')
    if isinstance(checkpoint, Mapping):
        facts['source_checkpoint'] = _references(checkpoint)
    result = operation.get('result') or {}
    facts['result_digest'] = canonical_payload_digest(result)
    facts['result'] = _references(result)
    for key in ('candidate', 'release', 'source_checkpoint'):
        reference = _references(result.get(key))
        if reference:
            facts['result'][key] = reference
    checkpoint_result = result.get('source_checkpoint')
    if isinstance(checkpoint_result, Mapping):
        facts['result'].setdefault('source_checkpoint', {}).update(_references({
            'source_commit': checkpoint_result.get('commit'),
            'source_snapshot_digest': checkpoint_result.get('snapshot_digest'),
        }))
    reason = str(operation.get('recovery_reason') or '')
    # Exceptions may contain upstream response bodies or machine paths. Keep
    # only the typed classification in the shared event projection.
    code = reason.split(':', 2)[:2] if ':' in reason else [reason]
    if reason and all(re.fullmatch(r'[a-zA-Z0-9_.-]{1,128}', part) for part in code):
        facts['recovery_code'] = ':'.join(code)
    return facts


def _references(value) -> dict:
    if not isinstance(value, Mapping):
        return {}
    return {key: value[key] for key in (
        'project_id', 'change_id', 'candidate_id', 'candidate_digest', 'release_digest',
        'source_webspace_id', 'source_snapshot_digest', 'source_commit', 'operation_id')
        if isinstance(value.get(key), str) and len(value[key]) <= 256}
