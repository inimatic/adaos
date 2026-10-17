import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('review_candidate',Path(__file__).resolve().parents[1]/'e2e/stand/review-builder-snapshot.py')
module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


@pytest.mark.parametrize('fault',[None,'foreign_task','foreign_scenario','uncompleted'])
def test_candidate_observation_does_not_accept_an_unvalidated_or_foreign_factory_task(fault):
    task={'task_id':'task.one','status':'completed','target':{'type':'scenario','id':'owned_test'}}
    if fault=='foreign_task':task['task_id']='task.foreign'
    if fault=='foreign_scenario':task['target']['id']='user_app'
    if fault=='uncompleted':task['status']='in_progress'
    if fault:
        with pytest.raises(ValueError,match='exact completed'):
            module.require_completed_candidate(task,scenario='owned_test',revision='task.one')
    else:
        module.require_completed_candidate(task,scenario='owned_test',revision='task.one')
