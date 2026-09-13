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


def test_retained_http_acceptance_plans_have_explicit_contracts():
    root = Path(__file__).parents[1] / 'e2e/builder/development/lifecycle/acceptance'
    for path in root.glob('*.yaml'):
        stand.validate_plan(yaml.safe_load(path.read_text(encoding='utf-8')))


def test_race_plan_is_bounded_and_does_not_count_rejected_http_200_as_success():
    step = {'id': 'race', 'tool': 'save', 'concurrent_arguments': [{}, {}],
            'expect': {'values': {'success_count': 1}}}
    stand.validate_plan({'schema': 'adaos.e2e.application_tools.v1', 'steps': [step]})
    for variants in ([], [{}], [{}] * 5, [None, {}]):
        with pytest.raises(ValueError, match='two to four'):
            stand.validate_plan({'schema': 'adaos.e2e.application_tools.v1', 'steps': [{**step, 'concurrent_arguments': variants}]})
    results = [{'http_status': 200, 'body': {'ok': True, 'result': {'ok': True}}},
               {'http_status': 200, 'body': {'ok': True, 'result': {'ok': False, 'error': 'conflict'}}}]
    assert stand.race_summary(results) == {'results': results, 'http_statuses': [200, 200], 'success_count': 1}
    assert stand.race_summary([{'http_status': 409, 'body': {'detail': 'conflict'}}])['success_count'] == 0


def test_race_uses_independent_sessions_and_keeps_submission_order(monkeypatch):
    from threading import Barrier
    barrier = Barrier(2, timeout=2)
    sessions = []

    class Response:
        status_code = 200

        def __init__(self, value):
            self.value = value

        def json(self):
            return {'result': self.value}

    class Session:
        def __enter__(self):
            sessions.append(self)
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            barrier.wait()
            return Response(kwargs['json'])

    monkeypatch.setattr(stand.requests, 'Session', Session)
    result = stand.concurrent_calls('http://127.0.0.1', {}, [{'id': 1}, {'id': 2}])
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert [item['body']['result']['id'] for item in result['results']] == [1, 2]
def test_expectations_support_literal_dotted_fields_with_json_pointer():
    from adaos.e2e.builder import _expectation_findings, _path_get

    value = {"items": [{"sample.name": "Текст", "a/b~c": 3, "": False}]}
    assert _expectation_findings(value, {"values": {"/items/0/sample.name": "Текст", "/items/0/a~1b~0c": 3, "/items/0/": False}}) == []
    assert _path_get(value, "items.0") == (True, value["items"][0])
    for path in ("/items/-1", "/items/01", "/items/1", "/items/0/missing", "items.0.sample.name"):
        assert _path_get(value, path) == (False, None)
