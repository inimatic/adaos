import copy
import importlib.util
from pathlib import Path

import pytest
import yaml


spec = importlib.util.spec_from_file_location('dev_application_stand', Path(__file__).parents[1] / 'e2e/stand/check-dev-application.py')
stand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stand)


def scope():
    checkpoint = {'cleanup': {'test': True}, 'context': {'retain_test_projects': True,
        'owned_artifacts': [{'project_id': 'budget_test', 'primary_ref': 'scenario:budget_test'}]}}
    pin = {'scenario_id': 'budget_test', 'stage': 'automation', 'revision': 'task.current'}
    project = {'id': 'budget_test', 'catalog': {'title': 'Budget [TEST]'},
        'components': {'owned': [{'ref': 'skill:budget_skill'}]}}
    snapshot = {'object_type': 'scenario', 'object_id': 'budget_test', 'task_id': 'task.current'}
    return checkpoint, pin, project, snapshot, 'budget_skill'


def test_dev_scope_does_not_depend_on_an_id_prefix():
    stand.validate_scope(*scope())


@pytest.mark.parametrize('change', ['untagged', 'not_owned', 'other_skill', 'other_snapshot', 'prototype', 'other_project'])
def test_dev_scope_rejects_unowned_or_unpinned_application(change):
    cp, pin, project, snapshot, skill = scope()
    if change == 'untagged':
        project['catalog']['title'] = 'Real application'
    elif change == 'not_owned':
        cp['cleanup']['test'] = False
    elif change == 'other_skill':
        skill = 'unrelated_skill'
    elif change == 'other_snapshot':
        snapshot['task_id'] = 'task.previous'
    elif change == 'prototype':
        pin['stage'] = 'prototype'
    else:
        project['id'] = 'other'
    with pytest.raises(ValueError):
        stand.validate_scope(cp, pin, project, snapshot, skill)


def test_dev_plan_requires_local_tools_explicit_expectations_and_dev_callers():
    plan = {'schema': 'adaos.e2e.application_tools.v1', 'steps': [
        {'id': 'read', 'tool': 'list_records', 'expect': {'values': {'http_status': 200}}}]}
    stand.validate_plan(plan)
    for update in ({'tool': 'other_skill:list_records'}, {'caller': 'reader'}, {'expect': {}}):
        bad = copy.deepcopy(plan)
        bad['steps'][0].update(update)
        with pytest.raises(ValueError):
            stand.validate_plan(bad)
    plan['steps'].append(plan['steps'][0])
    with pytest.raises(ValueError):
        stand.validate_plan(plan)


def test_frozen_cohort_contains_only_remaining_existing_archetypes():
    root = Path(__file__).parents[1]
    plan = yaml.safe_load((root / 'e2e/builder/development/lifecycle/frozen-cohort.yaml').read_text(encoding='utf-8'))
    suite = yaml.safe_load((root / 'e2e/builder/development/archetypes/suite.yaml').read_text(encoding='utf-8'))
    assert set(plan['cases']) == {Path(ref).stem for ref in suite['case_refs']} - {'equipment-inspections-ru'}
    assert plan['revision'] == '002'
    assert all(case['locale'] in {'en', 'ru'} and case['brief'].strip() for case in plan['cases'].values())
