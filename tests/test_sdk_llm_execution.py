import copy

import pytest

from adaos.sdk.llm.execution import bind_inputs, require_current_approval, result_currentness, qualify_job_transition


def inputs(**changes):
    return bind_inputs(owner_ref='skill:example/run:one', inputs={
        'original': 'sha256:' + '0' * 64, 'mask_revision': 2,
        'parameters': {'strength': 30}, 'goal': 'Preserve texture', **changes})


@pytest.mark.parametrize('change', [{}, {'mask_revision': 3}, {'goal': 'A different goal'},
                                  {'parameters': {'strength': 31}}, {'original': 'sha256:' + '1' * 64}])
def test_approval_and_history_bind_every_declared_input(change):
    approved, current = inputs(), inputs(**change)
    plan = 'sha256:' + 'a' * 64
    if change:
        with pytest.raises(ValueError, match='stale'):
            require_current_approval(approved_inputs=approved, current_inputs=current,
                                     approved_plan_digest=plan, current_plan_digest=plan)
    else:
        require_current_approval(approved_inputs=approved, current_inputs=current,
                                 approved_plan_digest=plan, current_plan_digest=plan)
    assert result_currentness(result_inputs=approved, current_inputs=current)['current'] is (not change)
    assert approved == inputs()  # Freshness checks never rewrite historical inputs.


def test_plan_change_corruption_and_foreign_owners_cannot_approve():
    approved = inputs()
    with pytest.raises(ValueError, match='stale'):
        require_current_approval(approved_inputs=approved, current_inputs=approved,
            approved_plan_digest='sha256:' + 'a' * 64, current_plan_digest='sha256:' + 'b' * 64)
    corrupt = copy.deepcopy(approved); corrupt['inputs']['parameters']['strength'] = 99
    with pytest.raises(ValueError, match='corrupt'):
        result_currentness(result_inputs=corrupt, current_inputs=approved)
    with pytest.raises(ValueError, match='foreign'):
        result_currentness(result_inputs=approved,
            current_inputs=bind_inputs(owner_ref='skill:other', inputs=approved['inputs']))


def test_binding_detaches_mutable_input_and_rejects_non_json():
    original = {'parameters': {'strength': 30}}
    bound = bind_inputs(owner_ref='skill:example', inputs=original)
    original['parameters']['strength'] = 90
    assert bound['inputs']['parameters']['strength'] == 30
    with pytest.raises(ValueError):
        bind_inputs(owner_ref='skill:example', inputs={'strength': float('nan')})


def test_a_matching_but_invalid_plan_digest_does_not_pass():
    with pytest.raises(ValueError, match='stale'):
        require_current_approval(approved_inputs=inputs(), current_inputs=inputs(),
            approved_plan_digest='sha256:' + 'z' * 64, current_plan_digest='sha256:' + 'z' * 64)


def jobs():
    return {'owner_ref': 'skill:example', 'jobs': [
        {'request_id': 'r1', 'job_id': 'j1', 'input_digest': 'digest1', 'submits': 1, 'status': 'queued'}]}


@pytest.mark.parametrize('operation', ['read', 'replay'])
@pytest.mark.parametrize('fault', [None, 'new_job', 'job_id', 'input_digest', 'submits', 'removed_job'])
def test_transition_calibration_permits_polling_but_detects_duplicate_or_changed_jobs(operation, fault):
    before, after = jobs(), jobs()
    after['jobs'][0]['status'] = 'completed'
    if fault == 'new_job':
        after['jobs'].append({'request_id': 'r2', 'job_id': 'j2', 'input_digest': 'digest2', 'submits': 1})
    elif fault == 'removed_job':
        after['jobs'] = []
    elif fault:
        after['jobs'][0][fault] = 2 if fault == 'submits' else 'different'
    result = qualify_job_transition(before=before, after=after, operation=operation, request_id='r1')
    assert result['passed'] is (fault is None)


@pytest.mark.parametrize('fault', ['foreign', 'missing_count', 'duplicate_request', 'unknown_replay', 'mutation'])
def test_incomplete_or_foreign_evidence_is_not_graded_as_a_valid_read(fault):
    before, after = jobs(), jobs()
    if fault == 'foreign': after['owner_ref'] = 'skill:other'
    if fault == 'missing_count': after['jobs'][0].pop('submits')
    if fault == 'duplicate_request': after['jobs'].append(dict(after['jobs'][0]))
    with pytest.raises(ValueError):
        qualify_job_transition(before=before, after=after,
            operation='submit' if fault == 'mutation' else 'replay',
            request_id='unknown' if fault == 'unknown_replay' else 'r1')
