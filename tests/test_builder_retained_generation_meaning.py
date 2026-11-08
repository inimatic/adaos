import copy
import pytest

from test_builder_accepted_meaning import accepted
from test_builder_generation_candidates import setup
from test_builder_workflow import workflow_project as _workflow_project
from adaos.services.builder.generation_authoring import retained_generation_meaning
from adaos.services.builder.interpretation_runs import InterpretationRunRejected

workflow_project = _workflow_project


@pytest.mark.parametrize('fault', [None, 'request', 'revision', 'project'])
def test_replay_uses_its_original_turn_instead_of_the_accepted_baseline_meaning(workflow_project, fault):
    service, _ = workflow_project
    directory, publication, baseline = accepted(service)
    statement = 'Покажи другой список.'
    result = setup(service, directory, baseline=baseline, statement=statement,
        job='failed-successor', request='successor-request')
    options = {'journal_dir': directory, 'project_ref': 'scenario:recipes',
        'job_id': result['wire']['job_id'], 'request_text': statement,
        'expected_revision': publication['seed']['revision']}
    if fault == 'request': options['request_text'] = 'Another task'
    elif fault == 'revision': options['expected_revision'] = '999'
    elif fault == 'project': options['project_ref'] = 'scenario:foreign'
    before = copy.deepcopy(service.lifecycle_state('scenario', 'recipes'))
    if fault:
        with pytest.raises((InterpretationRunRejected, ValueError)):
            retained_generation_meaning(service, **options)
    else:
        context = retained_generation_meaning(service, **options)
        assert context['brief']['task_contract']['source']['statement'] == statement
        assert context['brief']['digest'] != publication['brief']['digest']
        assert context['provider_calls'] == 0
        assert retained_generation_meaning(service, **options) == context
    assert service.lifecycle_state('scenario', 'recipes') == before
