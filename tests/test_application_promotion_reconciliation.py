from dataclasses import replace
import json

import pytest

from test_application_lifecycle_operations import operations
from test_native_trial_runtime import activation_record, native_paths
from adaos.domain.artifact_release import WorkspaceLock, WorkspaceSlot, canonical_payload_digest
from adaos.services.applications.promotion_reconciliation import reconcile_completed_workspace_promotions
from adaos.services.artifact_pipeline.publication import PROMOTION_OPERATION_SCHEMA
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.domain.application import ApplicationOperation


def installed_promotion(ops, candidate):
    release = ops.store.get_release('example', candidate.release_digest).project_release
    lock = WorkspaceLock(lock_revision=1, updated_at='2026-11-06T00:00:00+00:00',
        slots=(WorkspaceSlot(slot_id='example', project_id='example', release='example@0.1.0', release_digest=candidate.release_digest),),
        components=release.components)
    atomic_write_json(ops.workspace / '.adaos/workspace.lock.json', lock.to_dict())
    installed = ops.service.reconcile_workspace_installation('example', candidate.release_digest, lock)
    selected = ops.store.get_runtime_selection('desktop', 'example')
    ops.store.save_runtime_selection(replace(selected, source='stable_installation', runtime_root_ref='workspace',
        revision=selected.revision+1), expected_revision=selected.revision)
    record = {'schema': PROMOTION_OPERATION_SCHEMA, 'candidate_id': candidate.candidate_id,
        'project_id': 'example', 'release_digest': candidate.release_digest, 'status': 'completed', 'phase': 'completed',
        'attestation_binding': {'status': 'completed', 'attestation_set': {
            'project_id': 'example', 'release_digest': candidate.release_digest}},
        'receipts': {'channel_moved': {'pointer': {'project_id': 'example', 'channel': 'stable',
            'release_digest': candidate.release_digest}}, 'workspace_activated': {'workspace_lock': lock.to_dict(),
                'lock_digest': lock.to_dict()['lock_digest']}}}
    path = ops.state / 'artifact_pipeline/promotions' / (candidate.candidate_id+'.json')
    atomic_write_json(path, record)
    return installed, record, path


def test_completed_publication_repairs_stale_channel_without_acceptance_or_data_effects(operations):
    ops, _, _, candidate = operations
    installed, _, _ = installed_promotion(ops, candidate)
    before = ops.store.get_release('example', candidate.release_digest).to_dict()
    result = reconcile_completed_workspace_promotions(ops.owner)
    assert result == [{'application_id': 'example', 'candidate_id': candidate.candidate_id,
                      'release_digest': candidate.release_digest, 'status': 'reconciled'}]
    assert ops.store.get_channels('example')['channels']['stable'] == candidate.release_digest
    assert ops.observer.snapshot('example')['runtime']['reason'] == 'StableInstallation'
    assert ops.store.get_release('example', candidate.release_digest).to_dict() == before
    assert ops.store.get_installation('example').to_dict() == installed.to_dict()
    assert reconcile_completed_workspace_promotions(ops.owner) == []


def test_completed_publication_supersedes_older_unknown_operation_for_another_release(operations):
    ops, subnet, _, candidate = operations
    _, record, path = installed_promotion(ops, candidate)
    record['updated_at'] = '2026-11-10T06:22:27+00:00'
    atomic_write_json(path, record)
    plan = {'release_digest': 'sha256:' + 'b' * 64}
    ops.store.put_operation(ApplicationOperation(
        operation_id='superseded', application_id='example', kind='reconcile',
        status='unknown', actor_ref='user:owner', subnet_ref=subnet,
        plan_digest=canonical_payload_digest(plan), idempotency_key='superseded',
        expected_revision=0, revision=1, plan=plan,
        created_at='2026-11-08T00:00:00+00:00',
        updated_at='2026-11-08T00:00:01+00:00'))

    assert reconcile_completed_workspace_promotions(ops.owner) == [{
        'application_id': 'example', 'candidate_id': candidate.candidate_id,
        'release_digest': candidate.release_digest, 'status': 'reconciled'}]


def test_intentionally_selected_older_workspace_never_rolls_back_a_newer_stable_channel(operations):
    ops, _, _, candidate = operations
    installed_promotion(ops, candidate)
    old = ops.store.get_release('example', candidate.release_digest)
    newer = replace(old.project_release, version='0.2.0', release_digest=None).seal()
    envelope = replace(old, project_release=newer)
    ops.store.put_release(envelope)
    ops.store.set_channel('example', 'stable', envelope.release_digest, expected_release_digest=None)
    assert reconcile_completed_workspace_promotions(ops.owner) == []
    assert ops.store.get_channels('example')['channels']['stable'] == envelope.release_digest


@pytest.mark.parametrize('fault', ['unknown', 'pointer', 'receipt_lock', 'missing_receipt', 'trial_selected',
                                  'removed', 'external_publisher', 'workspace_changed', 'pending_operation', 'attestation'])
def test_reconciliation_requires_exact_completed_installed_owned_publication(operations, fault):
    ops, subnet, _, candidate = operations
    installed, record, path = installed_promotion(ops, candidate)
    if fault == 'unknown': record['status'] = 'unknown'
    if fault == 'attestation': record['attestation_binding']['attestation_set']['release_digest'] = 'sha256:'+'b'*64
    if fault == 'pointer': record['receipts']['channel_moved']['pointer']['release_digest'] = 'sha256:'+'b'*64
    if fault == 'receipt_lock': record['receipts']['workspace_activated']['lock_digest'] = 'sha256:'+'b'*64
    if fault == 'missing_receipt': record['receipts'].pop('workspace_activated')
    if fault == 'trial_selected':
        current = ops.store.get_runtime_selection('desktop', 'example')
        ops.store.save_runtime_selection(replace(current, source='local_trial', runtime_root_ref='trial:candidate-example',
            revision=current.revision+1), expected_revision=current.revision)
    if fault == 'removed':
        ops.store.save_installation(replace(installed, status='removed', revision=installed.revision+1), expected_revision=installed.revision)
    if fault == 'external_publisher': ops.owner.config.subnet_id_value = 'sn_external'
    if fault == 'workspace_changed':
        atomic_write_json(ops.workspace / '.adaos/workspace.lock.json',
            WorkspaceLock(lock_revision=2, updated_at='2026-11-06T00:00:00+00:00').to_dict())
    if fault == 'pending_operation':
        from adaos.domain.application import ApplicationOperation
        from adaos.domain.artifact_release import canonical_payload_digest
        ops.store.put_operation(ApplicationOperation(operation_id='pending', application_id='example', kind='reconcile',
            status='unknown', actor_ref='user:owner', subnet_ref=subnet, plan_digest=canonical_payload_digest({}),
            idempotency_key='pending', expected_revision=0, revision=1, plan={}))
    atomic_write_json(path, record)
    assert reconcile_completed_workspace_promotions(ops.owner) == []
    assert 'stable' not in ops.store.get_channels('example').get('channels', {})
