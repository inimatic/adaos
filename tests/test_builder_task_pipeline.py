import copy
import json

import pytest

from adaos.services.builder.task_pipeline import prepare_generation_request
from adaos.services.builder.task_interpretation import TaskInterpretationRejected
from test_builder_task_interpretation import prepared


class Provider:
    def __init__(self, proposal, fail_observation=False):
        self.proposal = proposal
        self.submissions = []
        self.observations = []
        self.fail_observation = fail_observation
    def submit(self, messages, **options):
        self.submissions.append((messages, options))
        return {'job_id': 'same-job', 'status': 'queued'}
    def wait(self, job_id, **options):
        self.observations.append(job_id)
        if self.fail_observation:
            raise TimeoutError('Observer disconnected')
        return {'job_id': job_id, 'status': 'succeeded', 'output_text': json.dumps(self.proposal),
                'telemetry': {'provider_calls': 1, 'usage': {'input_tokens': 12, 'output_tokens': 34}}}


def invocation(tmp_path, provider):
    intent, task, _ = prepared()
    request = {'output_mode': 'semantic_v2', 'prototype_brief': {}, 'base_request': {},
               'dynamic_request': {'instruction': intent['statement'], 'scenario_id': 'qualification'}}
    return prepare_generation_request(request, metadata={'builder_task_pipeline': '1',
        'builder_ai_resource_ref': 'ai-resource:personal-chatgpt', 'prototype_intent': intent},
        model='exact-selected-model', reasoning={'effort': 'low'}, provider=provider, journal_dir=tmp_path)


def test_two_stage_preparation_binds_exact_task_and_replays_without_second_interpretation(tmp_path):
    _, _, proposal = prepared()
    provider = Provider(proposal)
    request = invocation(tmp_path, provider)
    assert request['generation_plan']['route'] == 'two_stage'
    assert request['interpretation_receipt']['provider_calls'] == 1
    assert request['interpretation_receipt']['usage']['input_tokens'] == 12
    assert len(provider.submissions) == 1
    assert provider.submissions[0][1]['tools'] == []
    assert request['base_request']['selected_ui_capabilities']['qualification']['prototype_brief'] == request['prototype_brief']
    repeated = invocation(tmp_path, provider)
    assert repeated == request
    assert len(provider.submissions) == 1 and provider.observations == ['same-job']


def test_observation_restart_recovers_the_same_durable_interpretation_job(tmp_path):
    _, _, proposal = prepared()
    provider = Provider(proposal, fail_observation=True)
    with pytest.raises(TimeoutError): invocation(tmp_path, provider)
    provider.fail_observation = False
    request = invocation(tmp_path, provider)
    assert request['task_spec']['interpretation_status'] == 'admitted'
    assert len(provider.submissions) == 1 and provider.observations == ['same-job', 'same-job']


def test_bad_interpretation_is_retained_and_never_becomes_a_candidate_request(tmp_path):
    _, _, proposal = prepared()
    proposal['facts'][0]['value'] = 0
    provider = Provider(proposal)
    with pytest.raises(TaskInterpretationRejected): invocation(tmp_path, provider)
    assert len(provider.submissions) == 1
    retained = json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))
    assert retained['result']['output_text'] == json.dumps(proposal)
    assert retained['admission']['status'] == 'rejected'
    assert retained['admission']['findings'][0]['code'].startswith('task.')


def test_uncertain_submission_needs_recovery_and_cannot_spend_twice(tmp_path):
    _, _, proposal = prepared()
    class Interrupted(Provider):
        def submit(self, messages, **options):
            self.submissions.append((messages, options))
            raise TimeoutError('Acknowledgement lost')
    provider = Interrupted(proposal)
    with pytest.raises(TimeoutError): invocation(tmp_path, provider)
    with pytest.raises(ValueError, match='outcome is unknown'): invocation(tmp_path, provider)
    assert len(provider.submissions) == 1
