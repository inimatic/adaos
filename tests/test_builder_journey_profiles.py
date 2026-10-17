import copy
from pathlib import Path

import pytest

from adaos.services.builder.journey_profiles import (
    get_profile, profile_definition, require_owned_plan_target, qualify_owned_plan_report,
    evaluate_registered_application,
)

ROOT = Path(__file__).resolve().parents[1]


def evidence():
    pin = {'stage': 'automation', 'revision': 'task.one', 'scenario_id': 'library_test',
           'preview_webspace_id': 'library_test-dev',
           'expected_materialization': {'revision':'task.one','source_fingerprint':'source.one'}}
    checkpoint = {'cleanup': {'test': True}, 'context': {'retain_test_projects': True,
        'owned_artifacts': [{'primary_ref': 'scenario:library_test'}]}}
    return pin, checkpoint


def test_registration_is_explicit_and_does_not_accept_a_similar_scenario_name():
    with pytest.raises(ValueError, match='not registered'): get_profile('candidate-provided.v1')
    assert evaluate_registered_application(scenario_id='natural_retouch_forged') is None
    assert len(profile_definition(repo_root=ROOT, profile_id='natural-retouch.v4')['obligations']) == 14


@pytest.mark.parametrize('fault', [None, 'foreign_scenario', 'foreign_task', 'foreign_space', 'not_test', 'not_owned'])
def test_plan_runner_requires_existing_exact_test_ownership(fault):
    pin, checkpoint = evidence()
    if fault == 'foreign_scenario': pin['scenario_id'] = 'user_application'
    if fault == 'foreign_task': pin['revision'] = 'task.foreign'
    if fault == 'foreign_space': pin['preview_webspace_id'] = 'desktop'
    if fault == 'not_test': checkpoint['cleanup']['test'] = False
    if fault == 'not_owned': checkpoint['context']['owned_artifacts'] = []
    args = dict(profile_id='knowledge-library.v1', scenario_id='library_test', task_id='task.one',
                webspace_id='library_test-dev', checkpoint=checkpoint, pin=pin)
    if fault:
        with pytest.raises(ValueError, match='owned TEST'): require_owned_plan_target(**args)
    else:
        require_owned_plan_target(**args)


@pytest.mark.parametrize('fault', [None, 'missing_step', 'duplicate_step', 'foreign_revision', 'no_assets',
                                 'source_drift', 'verifier_drift', 'wrong_authority', 'green_flag_lies'])
def test_second_domain_grader_known_good_and_mutations(fault):
    definition = profile_definition(repo_root=ROOT, profile_id='knowledge-library.v1')
    pin, _ = evidence()
    sample = {'layout': 'wide', 'checks': [{'id': identity, 'status': 'passed'}
        for identity in definition['obligations']], 'errors': [], 'foreign_api_origins': [],
        'observed_materializations': [dict(pin['expected_materialization'])],
        'browser_environment': {'browser': 'known'}, 'client_assets': [{'digest': 'asset', 'size_bytes': 1}]}
    other = copy.deepcopy(sample); other['layout'] = 'compact'
    report = {'passed': True, 'samples': [sample, other]}
    if fault == 'missing_step': sample['checks'].pop()
    if fault == 'duplicate_step': sample['checks'][-1] = sample['checks'][0]
    if fault == 'foreign_revision': sample['observed_materializations'][0]['revision'] = 'task.other'
    if fault == 'no_assets': sample['client_assets'] = []
    if fault == 'wrong_authority': sample['foreign_api_origins'] = ['http://foreign']
    if fault == 'green_flag_lies': sample['failure'] = 'postcondition failed'
    result = qualify_owned_plan_report(repo_root=ROOT, profile_id='knowledge-library.v1', report=report,
        pin=pin, source_unchanged=fault != 'source_drift', verifier_unchanged=fault != 'verifier_drift')
    assert result['passed'] is (fault is None)
    assert result['application_ready_claim'] is False
