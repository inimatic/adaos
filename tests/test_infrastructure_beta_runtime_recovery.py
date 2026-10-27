from dataclasses import replace
import pytest
from adaos.domain.application import RuntimeSelection
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict
from adaos.services.applications.runtime_transition import ApplicationRuntimeTransition, TransitionStep

DIGEST = 'sha256:' + 'a' * 64


def prepared(tmp_path):
    channel = ApplicationRuntimeChannel(tmp_path, 'infra_test')
    target = RuntimeSelection(webspace_id='desktop', application_id='infra_test', source='local_trial',
        release_digest=DIGEST, runtime_root_ref='trial:infra-candidate', revision=1)
    runner = ApplicationRuntimeTransition(channel)
    runner.run('beta', contract_digest=DIGEST, expected=(), target=target,
        steps=[TransitionStep('initialize', lambda key: {'ok': True})])
    channel.select(replace(target, webspace_id='second-host'), expected_revision=0)
    return channel, runner, target


def test_rejection_retires_all_reviewed_exact_candidate_hosts(tmp_path):
    channel, runner, target = prepared(tmp_path)
    reviewed = channel.read()
    result = runner.reject_completed('beta', contract_digest=DIGEST, expected_current=reviewed,
        steps=[TransitionStep('verify_empty_baseline', lambda key: {'ok': True})])
    assert result['completed']
    assert {row['webspace_id'] for row in result['intent']['expected']} == {'desktop', 'second-host'}
    assert channel.read() == ()


def test_reviewed_rejection_cannot_replace_a_different_release(tmp_path):
    channel, runner, target = prepared(tmp_path)
    changed = tuple(replace(row, release_digest='sha256:' + 'b' * 64,
        runtime_root_ref='trial:other-candidate') for row in channel.read())
    with pytest.raises(RuntimeChannelConflict, match='exact current Candidate'):
        runner.reject_completed('beta', contract_digest=DIGEST, expected_current=changed,
            steps=[TransitionStep('verify', lambda key: {'ok': True})])


def test_reviewed_rejection_preserves_concurrent_change_guard(tmp_path):
    channel, runner, target = prepared(tmp_path)
    reviewed = channel.read()
    channel.select(replace(target, webspace_id='third-host'), expected_revision=0)
    with pytest.raises(RuntimeChannelConflict, match='channel changed before'):
        runner.reject_completed('beta', contract_digest=DIGEST, expected_current=reviewed,
            steps=[TransitionStep('verify', lambda key: {'ok': True})])
