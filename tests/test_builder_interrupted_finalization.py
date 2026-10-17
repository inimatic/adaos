import pytest

from adaos.services.builder.automation import BuilderAutomationService


@pytest.mark.parametrize('guard', [None, 'active_worker', 'missing_marker', 'foreign_marker',
    'unvalidated_task', 'foreign_result_task', 'new_instruction', 'wrong_expected_session'])
def test_ordinary_continue_recovers_a_dead_finalizer_without_reimplementing(guard):
    session = {'session_id': 'automation.scenario.example', 'object_type': 'scenario',
        'object_id': 'example', 'status': 'commit_ready', 'current_task_id': 'task.one',
        'finalizing_task_id': 'task.one', 'iteration': 4,
        'task': {'task_id': 'task.one', 'status': 'completed'},
        'last_result': {'summary': 'Validated candidate'},
        'completion_readiness': {'ok': False, 'task_id': 'task.one', 'stage': 'browser_feedback'}}
    if guard == 'missing_marker': session.pop('finalizing_task_id')
    if guard == 'foreign_marker': session['finalizing_task_id'] = 'task.foreign'
    if guard == 'unvalidated_task': session['task']['status'] = 'in_progress'
    if guard == 'foreign_result_task': session['task']['task_id'] = 'task.foreign'
    recovered = []
    class Service(BuilderAutomationService):
        def get_session(self, *args): return session
        def refresh_session(self, value): return value
        def project_session(self, value): return {'status': value['status']}
        def _detached_worker_is_active(self, value): return guard == 'active_worker'
        def recover_validated_result(self, **kwargs):
            recovered.append(kwargs)
            return {'ok': True, 'status': 'automation_recovery_queued', 'model_started': False}
    service = object.__new__(Service)
    kwargs = {'object_type': 'scenario', 'object_id': 'example',
              'text': 'Add a new feature' if guard == 'new_instruction' else 'Продолжи'}
    if guard == 'wrong_expected_session':
        kwargs.update(expected_session_id='automation.foreign', expected_iteration=4)
        with pytest.raises(ValueError, match='expected session'):
            service.submit_turn(**kwargs)
        assert recovered == []
        return
    result = service.submit_turn(**kwargs)
    assert bool(recovered) is (guard is None)
    assert result['status'] == ('automation_recovery_queued' if guard is None else 'automation_busy')
    if guard is None:
        assert result['model_started'] is False
        assert result['resumed_exact_checkpoint'] is True
