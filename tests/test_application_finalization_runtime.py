from datetime import datetime, timedelta, timezone

import pytest

from test_application_lifecycle_operations import operations, verified_operations
from test_native_trial_runtime import activation_record, native_paths
from adaos.services.applications.finalization_runtime import reconcile_finalizations
from adaos.services.applications.lifecycle_operations import ApplicationLifecycleOperations
from adaos.services.applications.runtime_channel import RuntimeChannelConflict
from adaos.services.applications.service import ApplicationServiceError


def confirm(ops, subnet, candidate, **changes):
    args = dict(candidate_id=candidate.candidate_id, release_digest=candidate.release_digest,
        expected_revision=ops.observer.snapshot('example')['revision'], actor_ref='user:owner',
        subnet_ref=subnet, capability='applications.publish', idempotency_key='confirmed-finalize')
    args.update(changes)
    return ops.finalize('example', **args)


def test_confirmation_is_durable_before_ack_and_needs_no_open_browser(verified_operations, monkeypatch):
    ops, subnet, _, candidate = verified_operations
    effects = []
    monkeypatch.setattr(ApplicationLifecycleOperations, '_effect', lambda self, op: effects.append(op.operation_id) or {'ok': True})
    revision = ops.observer.snapshot('example')['revision']
    admitted = confirm(ops, subnet, candidate)
    assert admitted['status'] == 'applying' and effects == []
    assert admitted['result']['confirmation']['plan_digest'] == admitted['plan_digest']
    # A lost HTTP acknowledgement repeats the same confirmed intent, even after
    # the lifecycle revision changes; it neither creates another plan nor effect.
    assert confirm(ops, subnet, candidate, expected_revision=revision) == admitted
    assert reconcile_finalizations(ops.owner) == [{'operation_id': admitted['operation_id'], 'status': 'succeeded'}]
    assert effects == [admitted['operation_id']]
    assert reconcile_finalizations(ops.owner) == []
    assert confirm(ops, subnet, candidate, expected_revision=revision)['status'] == 'succeeded'


def test_unconfirmed_plan_and_closed_review_never_start_finalization(verified_operations, monkeypatch):
    ops, subnet, _, candidate = verified_operations
    monkeypatch.setattr(ApplicationLifecycleOperations, '_effect', lambda *_: pytest.fail('Unconfirmed effect'))
    planned = ops.plan('example', 'promote_stable', actor_ref='user:owner', subnet_ref=subnet,
        capability='applications.plan', idempotency_key='review-only',
        expected_revision=ops.observer.snapshot('example')['revision'])
    assert reconcile_finalizations(ops.owner) == []
    assert ops.store.get_operation(planned['operation_id']).status == 'planned'


def test_restart_resumes_same_confirmed_intent_without_new_confirmation(verified_operations, monkeypatch):
    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)
    calls = []
    def effect(self, op):
        calls.append(op.operation_id)
        if len(calls) == 1:
            raise SystemExit('process interrupted after durable admission')
        return {'ok': True, 'release_digest': candidate.release_digest}
    monkeypatch.setattr(ApplicationLifecycleOperations, '_effect', effect)
    with pytest.raises(SystemExit):
        reconcile_finalizations(ops.owner)
    # Recreate the coordinator from disk; the prepared-review TTL has passed.
    interrupted = ops.store.get_operation(admitted['operation_id'])
    assert interrupted.status == 'reconciling'
    fresh = ApplicationLifecycleOperations(ops.owner)
    assert reconcile_finalizations(fresh.owner, now=datetime.now(timezone.utc) + timedelta(days=1))[0]['status'] == 'succeeded'
    assert calls == [admitted['operation_id'], admitted['operation_id']]
    assert fresh.store.get_operation(admitted['operation_id']).result['ok']


def test_transient_transport_retries_same_intent_but_business_rejection_stops(verified_operations, monkeypatch):
    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)
    calls = []
    def effect(self, op):
        calls.append(op.operation_id)
        if len(calls) == 1:
            raise TimeoutError('lost transport acknowledgement')
        raise RuntimeChannelConflict('Candidate evidence changed')
    monkeypatch.setattr(ApplicationLifecycleOperations, '_effect', effect)
    assert reconcile_finalizations(ops.owner)[0]['status'] == 'pending'
    assert reconcile_finalizations(ops.owner) == []
    assert reconcile_finalizations(ops.owner, now=datetime.now(timezone.utc) + timedelta(minutes=5))[0]['status'] == 'pending'
    assert ops.store.get_operation(admitted['operation_id']).result['execution']['status'] == 'blocked'
    assert reconcile_finalizations(ops.owner, now=datetime.now(timezone.utc) + timedelta(hours=1)) == []
    assert calls == [admitted['operation_id']] * 2


@pytest.mark.parametrize('changes', [
    {'capability': 'applications.plan'}, {'actor_ref': 'agent:other'},
    {'release_digest': 'sha256:' + '0' * 64}, {'candidate_id': 'another-candidate'},
    {'expected_revision': 'sha256:' + '0' * 64},
])
def test_confirmation_cannot_change_reviewed_authority_or_identity(verified_operations, changes):
    ops, subnet, _, candidate = verified_operations
    with pytest.raises((PermissionError, RuntimeChannelConflict, FileNotFoundError, ApplicationServiceError)):
        confirm(ops, subnet, candidate, **changes)
    assert ops.store.list_operations('example') == ()


def test_operation_progress_invalidates_existing_lifecycle_stream(verified_operations):
    ops, subnet, _, candidate = verified_operations
    from adaos.services.applications.lifecycle_runtime import observe_lifecycle_leases
    from adaos.services.applications.lifecycle_events import LifecycleEvents
    observe_lifecycle_leases(ops.owner)
    cursor = LifecycleEvents(ops.state).poll()['cursor']
    admitted = confirm(ops, subnet, candidate)
    assert observe_lifecycle_leases(ops.owner)['changed'] == 1
    events = LifecycleEvents(ops.state).poll(cursor=cursor)
    assert events['events'][0]['observation']['operation']['operation_id'] == admitted['operation_id']
    assert 'applications.detail' in events['invalidates']
