from types import SimpleNamespace

import pytest

from adaos.services.root import client as module
from adaos.services.root.client import RootHttpClient, RootHttpError
from adaos.services.root.service import CheckpointPreflightUnavailable


@pytest.mark.parametrize('status', [0, 502, 503, 504])
def test_draft_read_recovers_transient_ingress_without_changing_request(monkeypatch, status):
    calls, clock = [], [0.0]
    def request(method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        assert method == 'GET'  # A read retry never includes a Forge write.
        if len(calls) < 3:
            raise RootHttpError('temporary', status_code=status)
        return {'commit': 'exact', 'metadata': {'change_id': 'same'}}
    def sleep(delay): clock[0] += delay
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    client = RootHttpClient(base_url='https://example.invalid')
    monkeypatch.setattr(RootHttpClient, '_request', lambda self, *args, **kwargs: request(*args, **kwargs))
    result = client.get_draft_info(kind='scenarios', name='photo', node_id='node', timeout=3)
    assert result == {'commit': 'exact', 'metadata': {'change_id': 'same'}}
    assert len(calls) == 3 and clock[0] == .75
    assert all(c[1] == '/v1/scenarios/draft' and c[2]['params'] == {'name': 'photo', 'node_id': 'node'} for c in calls)
    assert all(0 < c[2]['timeout'] <= 3 for c in calls)


@pytest.mark.parametrize('status', [400, 401, 403, 404, 409, 429, 500])
def test_draft_read_preserves_definitive_or_unclassified_refusal(monkeypatch, status):
    calls = []
    error = RootHttpError('explicit refusal', status_code=status)
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        raise error
    client = RootHttpClient(base_url='https://example.invalid')
    monkeypatch.setattr(RootHttpClient, '_request', lambda self, *args, **kwargs: request(*args, **kwargs))
    with pytest.raises(RootHttpError) as caught:
        client.get_draft_info(kind='skills', name='photo', node_id='node')
    assert caught.value is error and len(calls) == 1


def test_draft_read_exhaustion_stays_bounded_and_does_not_fabricate_absence(monkeypatch):
    clock, calls = [0.0], []
    def sleep(delay): clock[0] += delay
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    def request(*args, **kwargs):
        calls.append(kwargs)
        raise RootHttpError('still unavailable', status_code=503)
    client = RootHttpClient(base_url='https://example.invalid')
    monkeypatch.setattr(RootHttpClient, '_request', lambda self, *args, **kwargs: request(*args, **kwargs))
    with pytest.raises(RootHttpError):
        client.get_draft_info(kind='scenarios', name='photo', node_id='node', timeout=.3)
    assert len(calls) == 3 and clock[0] == .3


@pytest.mark.parametrize('unresolved', [False, True])
def test_checkpoint_owner_retains_distinct_write_outcomes_without_secret_payload(unresolved):
    cause = RootHttpError('private upstream body', status_code=503, payload={'token': 'private'})
    error = CheckpointPreflightUnavailable(unresolved_write=unresolved, cause=cause)
    details = error.recovery_context
    assert details['phase'] == ('reconciliation' if unresolved else 'preflight')
    assert details['write_outcome'] == ('unresolved' if unresolved else 'not_started')
    assert details['status_code'] == 503 and details['cause_type'] == 'RootHttpError'
    assert details['recovery_command'] == 'reconcile_checkpoint'
    assert details['model_rerun_required'] is False and 'private' not in str(details)


def test_checkpoint_reconciliation_queues_existing_no_model_worker_in_background():
    from adaos.services.builder.automation import BuilderAutomationService
    current = {'session_id': 'session', 'status': 'failed', 'current_task_id': 'task.same',
               'change_id': 'change.same', 'task': {'status': 'completed'}, 'last_result': {'status': 'completed'},
               'last_failure': {'stage': 'forge_checkpoint'},
               'completion_readiness': {'vcs_checkpoints': [{'ok': False, 'kind': 'scenario', 'name': 'photo'}]}}
    launches = []
    class Service(BuilderAutomationService):
        def get_session(self, *args): return current
        def refresh_session(self, value): return value
        def _save_session(self, value, **kwargs): current.update(value)
        def _launch_worker(self, session_id, **kwargs): launches.append((session_id, kwargs))
        def project_session(self, value): return {'status': value['status']}
        def _finalize_completed_session(self, value): raise AssertionError('Do not block the chat request')
    service = object.__new__(Service); service.background = True
    result = service.reconcile_checkpoint(object_type='scenario', object_id='photo')
    assert result['status'] == 'automation_recovery_queued' and result['model_started'] is False
    assert launches == [('session', {'recovery_only': True})]
    assert current['current_task_id'] == 'task.same' and current['change_id'] == 'change.same'
    assert current['reconciliation_history'][-1]['mode'] == 'retry_exact'
    assert current['progress']['status'] == current['status'] == 'commit_ready'
    assert current['progress']['stage'] == 'checkpoint_reconciliation'
