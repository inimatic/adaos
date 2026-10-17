from types import SimpleNamespace
import errno

import pytest
import requests

from adaos.services.builder.automation import BuilderAutomationService
from adaos.services.builder.environment_recovery import environment_interruption
from adaos.sdk.builder import preview


@pytest.mark.parametrize("text", ["Продолжи", "Возобновляй работу", "Continue."])
@pytest.mark.parametrize("legacy", [False, True])
def test_ordinary_chat_resumes_validated_task_without_new_model(text, legacy):
    session = {"session_id": "automation.scenario.photo", "object_type": "scenario", "object_id": "photo",
               "status": "failed", "iteration": 8, "current_task_id": "task.retained",
               "task": {"status": "completed"}, "last_result": {"summary": "validated"},
               "ui_change_authorization": {"digest": "existing.authorization"},
               "last_failure": {"stage": "candidate_materialization", "details": {
                   "preserved_task_id": "task.retained", "model_rerun_required": False,
                   "recovery_command": "recover_validated_result"}}}
    if not legacy:
        session["last_failure"]["failure_class"] = "environment_interruption"
    calls = []

    class Service(BuilderAutomationService):
        def get_session(self, *args): return session
        def refresh_session(self, value): return value
        def recover_validated_result(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "task_id": "task.retained"}

    service = object.__new__(Service)
    with pytest.raises(ValueError, match="expected session iteration"):
        service.submit_turn(text=text, object_type="scenario", object_id="photo",
                            expected_session_id=session["session_id"], expected_iteration=7)
    assert calls == []
    result = service.submit_turn(text=text, object_type="scenario", object_id="photo",
                                 expected_session_id=session["session_id"], expected_iteration=8)
    assert calls == [{"object_type": "scenario", "object_id": "photo"}]
    assert result["model_started"] is False and result["task_id"] == "task.retained"
    assert session["ui_change_authorization"]["digest"] == "existing.authorization"


def test_structured_exception_chain_preserves_native_cause_and_ignores_strings():
    from urllib3.exceptions import MaxRetryError, NewConnectionError
    native = ConnectionRefusedError(errno.ECONNREFUSED, "native refusal")
    urllib = NewConnectionError(None, "wrapped native refusal")
    urllib.__cause__ = native
    wrapped = requests.ConnectionError(MaxRetryError(None, "/local", reason=urllib))
    assert environment_interruption(wrapped)["reason"] == "runtime_unavailable"
    assert environment_interruption(RuntimeError("ConnectionRefusedError ECONNREFUSED")) is None
    cycle = RuntimeError("cycle")
    cycle.__cause__ = cycle
    assert environment_interruption(cycle) is None


@pytest.mark.parametrize("failure,expected_calls", [("refused", 3), ("exhausted", 6), ("cold_restart", 13), ("timeout", 1), ("ambiguous", 1), ("http", 1)])
def test_owner_materialization_retries_only_proven_unaccepted_connections(monkeypatch, failure, expected_calls):
    calls, delays = [], []
    clock = [0.0]
    class Session:
        def post(self, url, **kw):
            calls.append(kw)
            if failure == "cold_restart" and clock[0] < 37:
                raise requests.ConnectionError(ConnectionRefusedError(errno.ECONNREFUSED, "cold owner"))
            if failure in {"refused", "exhausted"} and (failure == "exhausted" or len(calls) < 3):
                raise requests.ConnectionError(ConnectionRefusedError(errno.ECONNREFUSED, "not accepted"))
            if failure == "timeout": raise requests.ReadTimeout("owner may have accepted")
            if failure == "ambiguous": raise requests.ConnectionError("outcome unknown")
            if failure == "http": raise requests.HTTPError("owner rejected")
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})
        def close(self): pass
    monkeypatch.setattr(requests, "Session", Session)
    def sleep(delay):
        delays.append(delay)
        clock[0] += delay
    monkeypatch.setattr(preview, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr("adaos.apps.cli.active_control.resolve_control_base_url", lambda **_: "http://127.0.0.1:8777")
    monkeypatch.setattr("adaos.apps.cli.active_control.resolve_control_token", lambda **_: "test-token")
    budget = 90 if failure == "cold_restart" else 10
    operation = lambda: preview.materialize_revision_via_owner("owned-test", scenario_id="photo", revision="task.retained", timeout_s=budget)
    if failure in {"refused", "cold_restart"}: assert operation() == {"ok": True}
    else:
        with pytest.raises(requests.RequestException): operation()
    assert len(calls) == expected_calls
    assert all(call["json"] == calls[0]["json"] for call in calls)
    assert sum(delays) <= budget
    assert all(1 <= call['timeout'] <= budget for call in calls)


@pytest.mark.parametrize('entry', ['Continue', 'retry'])
def test_forge_failure_uses_existing_exact_reconciliation_without_model(entry):
    state = {'session_id': 'session', 'iteration': 13, 'object_type': 'scenario', 'object_id': 'photo',
             'status': 'failed', 'current_task_id': 'task.same', 'task': {'status': 'completed'},
             'last_result': {'status': 'completed'}, 'last_failure': {'stage': 'forge_checkpoint'}}
    calls = []
    class Service(BuilderAutomationService):
        def get_session(self, *args): return state
        def refresh_session(self, value): return value
        def reconcile_checkpoint(self, **kwargs):
            calls.append(kwargs)
            return {'ok': True, 'status': 'automation_recovery_queued'}
        def recover_validated_result(self, **kwargs): raise AssertionError('Forge owns reconciliation')
    service = object.__new__(Service)
    if entry == 'Continue':
        result = service.submit_turn(text=entry, object_type='scenario', object_id='photo',
                                     expected_session_id='session', expected_iteration=13)
    else:
        result = service.retry_failed(object_type='scenario', object_id='photo')
    assert calls == [{'object_type': 'scenario', 'object_id': 'photo'}]
    assert result['model_started'] is False
