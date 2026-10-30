"""One native Builder provider selection and immutable stage-plan binding."""
from __future__ import annotations


class PlannedBuilderJobs:
    def __init__(self, delegate, plan):
        self.delegate, self.plan = delegate, plan

    def submit(self, messages, **options):
        return self.delegate.submit(messages, **({"generation_plan": self.plan, **options} if self.plan else options))

    def send(self, messages, **options):
        return self.delegate.send(messages, **({"generation_plan": self.plan, **options} if self.plan else options))

    def wait(self, *args, **options):
        return self.delegate.wait(*args, **options)


def provider_jobs(metadata):
    if (metadata or {}).get('builder_ai_resource_ref'):
        from .personal_prototype import PersonalBuilderJobs
        delegate = PersonalBuilderJobs(metadata)
    else:
        from adaos.sdk.llm import llm_client
        delegate = type('RootBuilderJobs', (), {
            'submit': staticmethod(llm_client.submit_response_job),
            'wait': staticmethod(llm_client.wait_response_job),
            'send': staticmethod(llm_client.send_response)})()
    return PlannedBuilderJobs(delegate, (metadata or {}).get('builder_generation_plan'))
