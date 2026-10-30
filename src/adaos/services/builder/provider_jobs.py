"""One native Builder provider selection and immutable stage-plan binding."""
from __future__ import annotations


class PlannedBuilderJobs:
    def __init__(self, delegate, plan, journal_dir=None):
        self.delegate, self.plan, self.journal_dir = delegate, plan, journal_dir

    def _record(self, response, request_id):
        admission = response.get('context_admission')
        if admission and self.journal_dir:
            from pathlib import Path
            from adaos.services.builder_context import digest
            from adaos.services.artifact_pipeline.storage import atomic_write_json
            target = Path(self.journal_dir) / ('wire-' + digest(str(request_id))[7:39] + '.json')
            record = {'schema': 'adaos.builder.wire_admission_record.v1', 'request_id': request_id,
                'job_id': response.get('job_id'), 'context_admission': admission}
            if target.exists():
                import json
                retained = json.loads(target.read_text(encoding='utf-8'))
                if retained != record:
                    raise ValueError('Retained model-input admission cannot be silently replaced')
            else:
                atomic_write_json(target, record)
        return response

    def submit(self, messages, **options):
        if self.plan:
            options = {'generation_plan': self.plan, 'provider_max_attempts': 1, **options}
        return self._record(self.delegate.submit(messages, **options), options.get('request_id'))

    def send(self, messages, **options):
        return self._record(self.delegate.send(messages, **({"generation_plan": self.plan, **options} if self.plan else options)), options.get('request_id'))

    def wait(self, *args, **options):
        return self.delegate.wait(*args, **options)


def provider_jobs(metadata, *, journal_dir=None):
    if (metadata or {}).get('builder_ai_resource_ref'):
        from .personal_prototype import PersonalBuilderJobs
        delegate = PersonalBuilderJobs(metadata)
    else:
        from adaos.sdk.llm import llm_client
        delegate = type('RootBuilderJobs', (), {
            'submit': staticmethod(llm_client.submit_response_job),
            'wait': staticmethod(llm_client.wait_response_job),
            'send': staticmethod(llm_client.send_response)})()
    return PlannedBuilderJobs(delegate, (metadata or {}).get('builder_generation_plan'), journal_dir)
