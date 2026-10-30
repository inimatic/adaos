"""One native Builder provider selection and immutable stage-plan binding."""
from __future__ import annotations


class PlannedBuilderJobs:
    def __init__(self, delegate, plan, journal_dir=None):
        self.delegate, self.plan, self.journal_dir = delegate, plan, journal_dir
        self._admissions = {}

    def _record(self, response, request_id):
        admission = response.get('context_admission')
        if admission and response.get('job_id'):
            self._admissions[response['job_id']] = admission
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
        return self._delivery(response, admission) if response.get('status') in {'succeeded', 'completed'} and admission else response

    def _delivery(self, response, admission):
        from .retrieval_acceptance import verify_materialized_guidance, ContextDeliveryRejected
        failure = None
        try:
            receipt = verify_materialized_guidance(response, admission)
        except ContextDeliveryRejected as error:
            receipt, failure = error.receipt, error
        if receipt and self.journal_dir:
            from pathlib import Path
            import json
            from adaos.services.builder_context import digest
            from adaos.services.artifact_pipeline.storage import atomic_write_json
            identity = response.get('job_id') or admission['effective_input_digest']
            target = Path(self.journal_dir) / ('retrieval-' + digest(str(identity))[7:39] + '.json')
            if target.exists() and json.loads(target.read_text(encoding='utf-8')) != receipt:
                raise ValueError('Retained retrieval acceptance cannot be silently replaced')
            atomic_write_json(target, receipt)
        if failure:
            raise failure
        return {**response, **({'retrieval_acceptance': receipt} if receipt else {})}

    def submit(self, messages, **options):
        if self.plan:
            options = {'generation_plan': self.plan, 'provider_max_attempts': 1, **options}
        return self._record(self.delegate.submit(messages, **options), options.get('request_id'))

    def send(self, messages, **options):
        return self._record(self.delegate.send(messages, **({"generation_plan": self.plan, **options} if self.plan else options)), options.get('request_id'))

    def wait(self, *args, **options):
        response = self.delegate.wait(*args, **options)
        if response.get('status') not in {'succeeded', 'completed'}:
            return response
        job_id = response.get('job_id') or (args[0] if args else None)
        admission = self._admissions.get(job_id)
        if admission is None and self.journal_dir:
            from pathlib import Path
            import json
            for path in Path(self.journal_dir).glob('wire-*.json'):
                row = json.loads(path.read_text(encoding='utf-8'))
                if row.get('job_id') == job_id:
                    admission = row['context_admission']
                    break
        if admission:
            return self._delivery(response, admission)
        if self.plan and self.plan['retrieval'] == 'model_mcp' and self.plan['context_strategy'] != 'full':
            raise ValueError('Required retrieval admission receipt is missing; recover the exact model-input record before accepting output')
        return response


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
