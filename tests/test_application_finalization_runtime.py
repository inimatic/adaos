from datetime import datetime, timedelta, timezone
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from test_application_lifecycle_operations import operations, verified_operations
from test_native_trial_runtime import activation_record, native_paths
from adaos.services.applications.finalization_runtime import reconcile_finalizations
from adaos.services.applications.lifecycle_operations import ApplicationLifecycleOperations
from adaos.services.applications.runtime_channel import RuntimeChannelConflict
from adaos.services.applications.service import ApplicationServiceError
from adaos.services.root.client import RootHttpError


def test_shared_dependency_preflight_failure_preserves_structured_reason():
    from adaos.services.artifact_pipeline.activation import SharedSkillConflict
    from adaos.services.artifact_pipeline.publication import PublicationError
    from adaos.services.applications.finalization_runtime import finalization_failure, retryable_finalization_error
    from adaos.services.applications.transition_observation import operation_task
    try:
        try:
            raise SharedSkillConflict([{'skill': 'skill:provider', 'active_consumers': ['scenario:desktop'],
                'active_digest': 'sha256:active', 'candidate_digest': 'sha256:reviewed'}])
        except SharedSkillConflict as cause:
            raise PublicationError('workflow publication admission failed') from cause
    except PublicationError as error:
        failure = finalization_failure(error)
        assert failure['code'] == 'SharedSkillConflict'
        assert not retryable_finalization_error(error)
    task = operation_task({'operation_id': 'op', 'status': 'unknown', 'execution_status': 'blocked',
                           'execution_failure': failure})
    assert task['status'] == 'failed'
    assert 'skill:provider' in task['error'] and 'scenario:desktop' in task['error']
    assert 'No application data was moved' in task['error']


def test_shared_dependency_rejection_before_transition_is_terminal_not_an_unknown_fence(verified_operations, monkeypatch):
    from adaos.services.artifact_pipeline.activation import SharedSkillConflict
    ops, subnet, _, candidate = verified_operations
    def blocked(*_):
        raise SharedSkillConflict([{'skill': 'skill:provider', 'active_consumers': ['scenario:desktop']}])
    monkeypatch.setattr(ApplicationLifecycleOperations, '_effect', blocked)
    admitted = confirm(ops, subnet, candidate)
    assert reconcile_finalizations(ops.owner) == [{'operation_id': admitted['operation_id'], 'status': 'failed'}]
    op = ops.store.get_operation(admitted['operation_id'])
    assert op.status == 'failed' and op.result['confirmation'] == admitted['result']['confirmation']
    observed = ops.observer.snapshot('example')
    assert observed['transition'] is None and observed['runtime']['source'] == 'local_trial'
    assert observed['actions']['deactivate_beta']['eligible']


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


def test_queued_finalize_blocks_conflicting_plans_and_previously_reviewed_commands(verified_operations, monkeypatch):
    ops, subnet, _, candidate = verified_operations
    prior = ops.plan('example', 'renew_beta', actor_ref='user:owner', subnet_ref=subnet,
        capability='applications.plan', idempotency_key='renew-before-finalize',
        expected_revision=ops.observer.snapshot('example')['revision'])
    admitted = confirm(ops, subnet, candidate)
    snapshot = ops.observer.snapshot('example')
    assert snapshot.get('transition') is None  # The worker has not started yet.
    for name in ('deactivate_beta', 'restore_stable', 'uninstall_beta', 'renew_beta'):
        assert not snapshot['actions'][name]['eligible']
        assert snapshot['actions'][name]['reason'] == 'RuntimeOperationPending'
    with pytest.raises(RuntimeChannelConflict, match='RuntimeOperationPending'):
        ops.plan('example', 'deactivate_beta', actor_ref='user:owner', subnet_ref=subnet,
            capability='applications.plan', idempotency_key='interfering-command', expected_revision=snapshot['revision'])
    monkeypatch.setattr(ApplicationLifecycleOperations, '_effect', lambda *_: pytest.fail('Interfering effect'))
    with pytest.raises(RuntimeChannelConflict, match='RuntimeOperationPending'):
        ops.apply(prior['operation_id'], plan_digest=prior['plan_digest'], actor_ref='user:owner',
            subnet_ref=subnet, capability='applications.apply')
    assert ops.store.get_operation(admitted['operation_id']).status == 'applying'


def test_distinct_concurrent_confirmations_admit_only_one_application_operation(verified_operations):
    ops, subnet, _, candidate = verified_operations
    revision = ops.observer.snapshot('example')['revision']
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(confirm, ops, subnet, candidate, expected_revision=revision,
            idempotency_key=f'independent-confirmation-{idx}') for idx in range(2)]
        successes, rejected = [], []
        for future in futures:
            try:
                successes.append(future.result())
            except RuntimeChannelConflict as exc:
                rejected.append(str(exc))
    assert len(successes) == 1 and rejected == ['RuntimeOperationPending']
    assert len(ops.store.list_operations('example')) == 1


def test_superseded_legacy_unknown_history_does_not_freeze_a_current_installation(verified_operations, monkeypatch):
    ops, subnet, _, candidate = verified_operations
    current = confirm(ops, subnet, candidate)
    original = ops.store.get_operation(current['operation_id'])
    legacy_plan = {**original.plan,'action':'retain_local_beta'}
    from adaos.domain.artifact_release import canonical_payload_digest
    historical = replace(original, operation_id='applifecycle.historical-retention', status='unknown',
        idempotency_key='historical-retention-key',
        created_at='2020-01-01T00:00:00+00:00', updated_at='2020-01-01T00:00:00+00:00',
        plan=legacy_plan, plan_digest=canonical_payload_digest(legacy_plan), result={})
    ops.store.put_operation(historical)
    monkeypatch.setattr(ApplicationLifecycleOperations, '_effect', lambda *_: {'ok':True})
    assert reconcile_finalizations(ops.owner)[0]['status'] == 'succeeded'
    planned=ops.plan('example','renew_beta',actor_ref='user:owner',subnet_ref=subnet,
        capability='applications.plan',idempotency_key='after-completed-operation',
        expected_revision=ops.observer.snapshot('example')['revision'])
    assert planned['status']=='planned'


def test_concurrent_confirmation_replays_the_first_persisted_plan(verified_operations, monkeypatch):
    ops, subnet, _, candidate = verified_operations
    revision = ops.observer.snapshot('example')['revision']
    checks = []
    original = ops._verify_reviewed_beta
    def verify(*args):
        checks.append('verify')
        time.sleep(.05)
        return original(*args)
    monkeypatch.setattr(ops, '_verify_reviewed_beta', verify)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(confirm, ops, subnet, candidate, expected_revision=revision) for _ in range(2)]
        admitted = [future.result() for future in futures]
    assert admitted[0] == admitted[1]
    assert checks == ['verify']
    assert len(ops.store.list_operations('example')) == 1


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


@pytest.mark.parametrize('interruption', [TimeoutError('lost acknowledgement'),
    RootHttpError('connection reset', status_code=0), RootHttpError('gateway timeout', status_code=504),
    RootHttpError('Failed to store draft archive', status_code=500, error_code='draft_store_failed')])
def test_transient_transport_retries_same_intent_but_business_rejection_stops(verified_operations, monkeypatch, interruption):
    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)
    calls = []
    def effect(self, op):
        calls.append(op.operation_id)
        if len(calls) == 1:
            raise interruption
        raise RootHttpError('Candidate evidence changed', status_code=409)
    monkeypatch.setattr(ApplicationLifecycleOperations, '_effect', effect)
    assert reconcile_finalizations(ops.owner)[0]['status'] == 'pending'
    assert reconcile_finalizations(ops.owner) == []
    assert reconcile_finalizations(ops.owner, now=datetime.now(timezone.utc) + timedelta(minutes=5))[0]['status'] == 'pending'
    assert ops.store.get_operation(admitted['operation_id']).result['execution']['status'] == 'blocked'
    execution = ops.store.get_operation(admitted['operation_id']).result['execution']
    assert execution['failure'] == {'code': 'ReleaseServiceRejected', 'status_code': 409}
    assert 'retry_at' not in execution
    projected = ops.observer.snapshot('example')['operation']
    assert projected['execution_failure'] == execution['failure']
    assert reconcile_finalizations(ops.owner, now=datetime.now(timezone.utc) + timedelta(hours=1)) == []
    assert calls == [admitted['operation_id']] * 2


@pytest.mark.parametrize('status', [0, 408, 429, 500, 502, 503, 504])
def test_root_transport_and_availability_failures_are_retryable(status):
    from adaos.services.applications.finalization_runtime import finalization_failure, retryable_finalization_error
    error = RootHttpError('private upstream message', status_code=status, payload={'token': 'private'})
    assert retryable_finalization_error(error)
    assert finalization_failure(error) == {'code': 'ReleaseServiceUnavailable', 'status_code': status}


@pytest.mark.parametrize('status', [400, 401, 403, 404, 409, 422, 501])
def test_root_rejections_are_not_retried_or_exposed_as_raw_upstream_messages(status):
    from adaos.services.applications.finalization_runtime import finalization_failure, retryable_finalization_error
    error = RootHttpError('private upstream message', status_code=status, payload={'token': 'private'})
    assert not retryable_finalization_error(error)
    assert finalization_failure(error) == {'code': 'ReleaseServiceFailed' if status >= 500 else 'ReleaseServiceRejected', 'status_code': status}


def test_source_checkpoint_failure_is_distinct_from_a_business_rejection():
    from adaos.services.applications.finalization_runtime import finalization_failure
    from adaos.services.applications.transition_observation import operation_task
    failure = finalization_failure(RootHttpError('private body', status_code=500, error_code='draft_store_failed'))
    assert failure == {'code': 'SourceCheckpointUnavailable', 'status_code': 500}
    operation = {'operation_id': 'original', 'status': 'unknown', 'execution_status': 'retrying', 'execution_failure': failure}
    assert 'source checkpoint' in operation_task(operation)['message']
    operation['execution_status'] = 'blocked'
    assert 'source checkpoint' in operation_task(operation)['error']


def test_data_divergence_is_blocked_with_retained_branch_explanation():
    from adaos.services.applications.data_lifecycle import StableDataDiverged
    from adaos.services.applications.finalization_runtime import finalization_failure, retryable_finalization_error
    from adaos.services.applications.transition_observation import operation_task
    error = StableDataDiverged('skill:sample', 'state.sqlite')
    wrapped = RuntimeError('adapter failure')
    wrapped.__cause__ = error
    failure = finalization_failure(wrapped)
    assert failure == {'code': 'StableDataDiverged', 'component_ref': 'skill:sample', 'database': 'state.sqlite'}
    assert not retryable_finalization_error(error)
    task = operation_task({'operation_id': 'original', 'status': 'unknown',
        'execution_status': 'blocked', 'execution_failure': failure})
    assert task['status'] == 'failed' and not task['can_cancel']
    assert 'Both data branches are retained' in task['error']
    assert 'release service' not in task['error']


def test_root_unavailability_stops_after_bounded_retries_with_retained_receipts(verified_operations, monkeypatch):
    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)
    calls = []
    def effect(self, op):
        calls.append(op.operation_id)
        raise RootHttpError('lost acknowledgement', status_code=0)
    monkeypatch.setattr(ApplicationLifecycleOperations, '_effect', effect)
    for attempt in range(5):
        assert reconcile_finalizations(ops.owner, now=datetime.now(timezone.utc) + timedelta(hours=1))
    op = ops.store.get_operation(admitted['operation_id'])
    assert op.result['confirmation'] == admitted['result']['confirmation']
    assert op.result['execution'] == {'attempts': 5, 'status': 'blocked',
        'failure': {'code': 'ReleaseServiceUnavailable', 'status_code': 0}}
    assert reconcile_finalizations(ops.owner, now=datetime.now(timezone.utc) + timedelta(hours=2)) == []
    assert calls == [admitted['operation_id']] * 5


@pytest.mark.parametrize('interruption', [SystemExit('process interrupted'),
    RootHttpError('Root connection reset after adoption', status_code=0)])
def test_restart_continues_journal_without_repeating_data_adoption(verified_operations, monkeypatch, interruption):
    from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel
    from adaos.services.applications.runtime_transition import ApplicationRuntimeTransition, TransitionStep
    ops, subnet, _, candidate = verified_operations
    admitted = confirm(ops, subnet, candidate)
    channel = ApplicationRuntimeChannel(ops.state, 'example')
    expected = channel.read()
    target = replace(expected[0], source='stable_installation', runtime_root_ref='workspace', revision=expected[0].revision + 1)
    counts = {'adoption': 0, 'activation': 0}
    def adopt(key):
        counts['adoption'] += 1
        return {'ok': True, 'effect_key': key}
    def activate(key):
        counts['activation'] += 1
        if counts['activation'] == 1:
            raise interruption
        return {'ok': True, 'effect_key': key}
    def effect(self, operation):
        return ApplicationRuntimeTransition(channel).run('application-stable:example:candidate-example',
            contract_digest=candidate.release_digest, expected=expected, target=target,
            steps=[TransitionStep('adopt_data', adopt), TransitionStep('activate', activate)]) | {'ok': True}
    monkeypatch.setattr(ApplicationLifecycleOperations, '_effect', effect)
    if isinstance(interruption, SystemExit):
        with pytest.raises(SystemExit):
            reconcile_finalizations(ops.owner)
    else:
        assert reconcile_finalizations(ops.owner)[0]['status'] == 'pending'
    assert counts == {'adoption': 1, 'activation': 1}
    assert channel.pending_transitions()
    assert reconcile_finalizations(ops.owner, now=datetime.now(timezone.utc) + timedelta(minutes=5))[0]['status'] == 'succeeded'
    assert counts == {'adoption': 1, 'activation': 2}
    assert not channel.pending_transitions()
    assert channel.read()[0].source == 'stable_installation'
    assert channel.read()[0].runtime_root_ref == 'workspace'
    assert ops.store.get_operation(admitted['operation_id']).status == 'succeeded'


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
