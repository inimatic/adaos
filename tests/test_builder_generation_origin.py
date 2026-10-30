import copy
import json

import pytest

from adaos.services.builder.generation_origin import (
    retain_generation_admission, load_generation_origin, verify_wire_origin,
)
from adaos.services.builder.generation_plan import GenerationInputRejected
from adaos.services.builder.interpretation_runs import InterpretationRuns
from adaos.services.builder.provider_jobs import PlannedBuilderJobs
from adaos.services.builder.task_spec import seal
from adaos.services.builder_context import digest
from test_builder_workflow import workflow_project as _workflow_project
from test_builder_interpretation_runs import planned, prepare
from test_builder_task_pipeline import Provider
from test_builder_task_interpretation import prepared

workflow_project = _workflow_project

def setup(service, folder):
    planned(service)
    result = prepare(service, folder, Provider(prepared()[2]))
    plan = result['generation_plan']
    path = folder / (plan['generation_plan_id'].replace(':', '-') + '.json')
    record = json.loads(path.read_text(encoding='utf-8'))
    return result, path, record


def test_origin_is_retained_before_candidate_submission_without_an_extra_run(workflow_project, tmp_path):
    service, _ = workflow_project
    request, path, record = setup(service, tmp_path / 'jobs')
    origin = record['execution_origin']
    assert origin['change_id'] == 'CH-origin'
    assert origin['source_message_ref'] == 'm.source'
    assert origin['generation_plan_ref'] == request['generation_plan']['generation_plan_id']
    assert origin['source_digest'] == request['task_spec']['source']['digest']
    assert origin['task_spec_ref'] == request['task_spec']['task_spec_id']
    assert origin['project_ref'] == 'scenario:recipes'
    assert len([run for run in service.describe('scenario', 'recipes')['change']['runs']
                if run['activity'].startswith('prototype_interpretation_')]) == 1
    before = path.read_bytes()
    assert prepare(service, path.parent, Provider(prepared()[2])) == request
    assert path.read_bytes() == before


def test_legacy_admission_is_not_retroactively_attributed(workflow_project, tmp_path):
    service, _ = workflow_project
    request, _, record = setup(service, tmp_path / 'bound')
    unqualified = {key: value for key, value in record.items() if key != 'execution_origin'}
    path = tmp_path / 'legacy.json'
    path.write_text(json.dumps(unqualified), encoding='utf-8')
    before = path.read_bytes()
    retained = retain_generation_admission(path, unqualified, observer=InterpretationRuns(service))
    assert 'execution_origin' not in retained and path.read_bytes() == before
    modified = copy.deepcopy(unqualified)
    modified['capability_selection_digest'] = digest('changed')
    with pytest.raises(GenerationInputRejected, match='generation_admission_conflict'):
        retain_generation_admission(path, modified, observer=InterpretationRuns(service))
    assert path.read_bytes() == before


@pytest.mark.parametrize('status', ['succeeded', 'failed', 'cancelled'])
def test_provider_resume_preserves_exact_origin_without_current_selection(workflow_project, tmp_path, status):
    service, _ = workflow_project
    request, path, record = setup(service, tmp_path / 'jobs')
    plan = request['generation_plan']
    calls = []

    class Transport:
        def submit(self, messages, **options):
            assert json.loads(path.read_text())['execution_origin'] == record['execution_origin']
            calls.append(options['request_id'])
            return {'job_id': 'candidate-job', 'status': 'queued', 'context_admission': {
                'generation_plan_ref': plan['generation_plan_id'], 'parent_generation_plan_ref': None,
                'required_retrieval_refs': []}}

        def wait(self, *args, **options):
            return {'job_id': 'candidate-job', 'status': status}

    first = PlannedBuilderJobs(Transport(), plan, path.parent)
    assert first.submit([], request_id='candidate-request')['execution_origin'] == record['execution_origin']
    planned(service, 'CH-later')
    # No plan/current Project is needed for observation of the frozen wire.
    resumed = PlannedBuilderJobs(Transport(), None, path.parent)
    assert resumed.wait('candidate-job')['execution_origin'] == record['execution_origin']
    assert calls == ['candidate-request']
    assert service.describe('scenario', 'recipes')['change']['change_id'] == 'CH-later'


@pytest.mark.parametrize('fault', ['origin', 'missing_plan', 'wire_plan', 'duplicate_job'])
def test_ambiguous_or_changed_origin_never_gets_replaced_during_resume(workflow_project, tmp_path, fault):
    service, _ = workflow_project
    request, path, record = setup(service, tmp_path / 'jobs')
    plan = request['generation_plan']
    wire = {'schema': 'adaos.builder.wire_admission_record.v1', 'request_id': 'request', 'job_id': 'job',
            'context_admission': {'generation_plan_ref': plan['generation_plan_id'], 'parent_generation_plan_ref': None},
            'execution_origin': record['execution_origin']}
    if fault == 'origin':
        wire['execution_origin'] = seal({**wire['execution_origin'], 'change_id': 'other'}, id_key='origin_id', prefix='generation-origin:')
    elif fault == 'missing_plan':
        path.unlink()
    elif fault == 'wire_plan':
        wire['context_admission']['generation_plan_ref'] = 'generation-plan:' + '0' * 24
    wire_path = path.parent / 'wire-one.json'
    wire_path.write_text(json.dumps(wire), encoding='utf-8')
    if fault == 'duplicate_job':
        (path.parent / 'wire-two.json').write_text(json.dumps(wire), encoding='utf-8')

    class Transport:
        def wait(self, *args, **options):
            return {'job_id': 'job', 'status': 'succeeded'}

    before = wire_path.read_bytes()
    with pytest.raises(ValueError):
        PlannedBuilderJobs(Transport(), None, path.parent).wait('job')
    assert wire_path.read_bytes() == before


@pytest.mark.parametrize('fault', ['source_digest', 'execution_group_ref', 'project_ref', 'task_spec_ref', 'source_message_ref'])
def test_wrong_origin_blocks_before_a_new_provider_submission(workflow_project, tmp_path, fault):
    service, _ = workflow_project
    request, path, record = setup(service, tmp_path / 'jobs')
    changed = {'source_digest':digest('different'), 'execution_group_ref':'task-execution:'+'0'*24,
               'project_ref':'scenario:other', 'task_spec_ref':'task:'+'0'*24, 'source_message_ref':'m.other'}
    record['execution_origin'] = seal({**record['execution_origin'], fault: changed[fault]},
                                     id_key='origin_id', prefix='generation-origin:')
    path.write_text(json.dumps(record), encoding='utf-8')

    class Transport:
        def submit(self, *args, **options):
            pytest.fail('Corrupt source binding reached the paid provider')

    with pytest.raises(GenerationInputRejected, match='execution_origin_conflict'):
        PlannedBuilderJobs(Transport(), request['generation_plan'], path.parent).submit([], request_id='new')


def test_repair_plan_uses_parent_origin_not_a_new_owner(workflow_project, tmp_path):
    from adaos.services.builder.repair_context import prepare_repair_context

    service, _ = workflow_project
    request, path, record = setup(service, tmp_path / 'jobs')
    plan = request['generation_plan']
    (path.parent / 'wire-primary.json').write_text(json.dumps({'job_id':'primary', 'request_id':'original',
        'context_admission': {'generation_plan_ref':plan['generation_plan_id']}}), encoding='utf-8')
    planned(service, 'CH-later')
    context = prepare_repair_context(job_id='primary', candidate={'schema':'adaos.builder.semantic_prototype_candidate.v2'},
        findings=[{'code':'semantic.task_write_scope_exceeded'}], brief=record['brief'], journal_dir=path.parent)
    origin = load_generation_origin(path.parent, context['generation_plan'])
    assert origin == record['execution_origin'] and origin['change_id'] == 'CH-origin'
    wire = {'execution_origin': origin, 'context_admission': {
        'generation_plan_ref':context['generation_plan']['generation_plan_id'],
        'parent_generation_plan_ref':plan['generation_plan_id']}}
    assert verify_wire_origin(path.parent, wire) == origin
