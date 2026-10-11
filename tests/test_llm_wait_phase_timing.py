from contextlib import contextmanager

import pytest

from adaos.sdk.llm import llm_client as llm


def clock(monkeypatch):
    value = [0.0]
    monkeypatch.setattr(llm.time, 'monotonic', lambda: value[0])
    monkeypatch.setattr(llm.time, 'sleep', lambda seconds: value.__setitem__(0, value[0] + seconds))
    return value


def test_wait_phases_are_disjoint_and_include_final_progress_callback(monkeypatch):
    value, calls = clock(monkeypatch), []

    def poll(*args, **kwargs):
        value[0] += .4
        calls.append(kwargs)
        return {'status': 'succeeded' if len(calls) == 3 else 'running',
                'progress': {'seq': 2 if len(calls) == 3 else 1}}

    def progress(event):
        value[0] += .3

    monkeypatch.setattr(llm, 'get_response_job', poll)
    result = llm._wait_response_job_loop('owned-test', timeout_s=10, poll_interval_s=.5, progress_callback=progress)
    observed = result['_client']['wait_observation']
    assert observed['elapsed_ms'] == 2800
    assert observed['phase_wall_ms'] == {'poll_total': 1200, 'poll_max': 400,
        'progress_callback_total': 600, 'progress_callback_max': 300,
        'sleep_total': 1000, 'sleep_max': 500, 'unattributed_loop': 0}
    assert observed['poll_count'] == 3


def test_failed_poll_time_is_counted_without_resubmission(monkeypatch):
    value, calls = clock(monkeypatch), []

    def poll(*args, **kwargs):
        value[0] += .4
        calls.append(kwargs)
        if len(calls) == 1:
            raise llm.RootHttpError('Transient read failure', status_code=502)
        return {'status': 'succeeded'}

    monkeypatch.setattr(llm, 'get_response_job', poll)
    observed = llm._wait_response_job_loop('owned-test', timeout_s=5, poll_interval_s=.5)['_client']['wait_observation']
    assert observed['elapsed_ms'] == 1300
    assert observed['phase_wall_ms']['poll_total'] == 800
    assert observed['poll_error_count'] == 1
    assert observed['remote_cancelled'] is False


def test_progress_error_preserves_terminal_and_measures_callback(monkeypatch):
    value = clock(monkeypatch)
    monkeypatch.setattr(llm, 'get_response_job', lambda *a, **k: {'status': 'succeeded', 'progress': {'seq': 1}})

    def progress(event):
        value[0] += .2
        raise RuntimeError('Local progress observer failed')

    result = llm._wait_response_job_loop('owned-test', progress_callback=progress)
    assert result['status'] == 'succeeded'
    assert result['_client']['wait_observation']['elapsed_ms'] == 200
    assert result['_client']['wait_observation']['phase_wall_ms']['progress_callback_total'] == 200


def test_wrapper_reports_session_setup_and_close_outside_loop(monkeypatch):
    value = clock(monkeypatch)

    @contextmanager
    def session(*args, **kwargs):
        value[0] += .3
        yield None, None
        value[0] += .2

    def poll(*args, **kwargs):
        value[0] += 1
        return {'status': 'succeeded'}

    monkeypatch.setattr(llm, '_response_job_poll_session', session)
    monkeypatch.setattr(llm, 'get_response_job', poll)
    observed = llm.wait_response_job('owned-test', base_url='https://root.invalid')['_client']['wait_observation']
    assert observed['session_setup_wall_ms'] == 300
    assert observed['elapsed_ms'] == 1000
    assert observed['wrapper_elapsed_ms'] == 1500
