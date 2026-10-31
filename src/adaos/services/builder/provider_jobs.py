"""One native Builder provider selection and immutable stage-plan binding."""
from __future__ import annotations

import copy


class PlannedBuilderJobs:
    def __init__(self, delegate, plan, journal_dir=None, *, run_observer=None):
        self.delegate, self.plan, self.journal_dir = delegate, plan, journal_dir
        self._admissions = {}
        self._origins = {}
        self._run_bindings = {}
        self.run_observer = run_observer

    def _binding(self, plan, origin):
        if not plan or not plan.get('execution_run_required'):
            return None
        from .generation_runs import GenerationRuns, retained_run_binding
        binding = retained_run_binding(self.journal_dir,plan,origin)
        if binding:
            observer = self.run_observer or GenerationRuns()
            observer.assert_submission(binding,origin,plan)
        return binding

    def _record(self, response, request_id, origin=None, binding=None):
        admission = response.get('context_admission')
        if admission and response.get('job_id'):
            self._admissions[response['job_id']] = copy.deepcopy(admission)
            if origin:
                self._origins[response['job_id']] = copy.deepcopy(origin)
            if binding:
                self._run_bindings[response['job_id']] = copy.deepcopy(binding)
        if admission and self.journal_dir:
            from pathlib import Path
            from adaos.services.builder_context import digest
            from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
            target = Path(self.journal_dir) / ('wire-' + digest(str(request_id))[7:39] + '.json')
            record = {'schema': 'adaos.builder.wire_admission_record.v1', 'request_id': request_id,
                'job_id': response.get('job_id'), 'context_admission': admission}
            if origin:
                record['execution_origin'] = origin
            if binding:
                record['execution_run_binding'] = binding
            with mutation_lock(target.with_suffix('.lock'), timeout_s=30):
                if target.exists():
                    import json
                    retained = json.loads(target.read_text(encoding='utf-8'))
                    if retained != record:
                        raise ValueError('Retained model-input admission cannot be silently replaced')
                else:
                    atomic_write_json(target, record)
        if origin:
            response = {**response, 'execution_origin': copy.deepcopy(origin)}
        if binding:
            response = {**response, 'execution_run_binding': copy.deepcopy(binding)}
            if not admission or not admission.get('execution_run_required') or not response.get('job_id'):
                from .interpretation_runs import InterpretationRunRejected
                raise InterpretationRunRejected()
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
        from .generation_origin import load_generation_origin
        origin = load_generation_origin(self.journal_dir, options.get('generation_plan'))
        binding = self._binding(options.get('generation_plan'),origin)
        return self._record(self.delegate.submit(messages, **options), options.get('request_id'), origin, binding)

    def send(self, messages, **options):
        from .generation_origin import load_generation_origin
        actual = {"generation_plan": self.plan, **options} if self.plan else options
        origin = load_generation_origin(self.journal_dir, actual.get('generation_plan'))
        binding = self._binding(actual.get('generation_plan'),origin)
        return self._record(self.delegate.send(messages, **actual), options.get('request_id'), origin, binding)

    def wait(self, *args, **options):
        response = self.delegate.wait(*args, **options)
        job_id = response.get('job_id') or (args[0] if args else None)
        admission = self._admissions.get(job_id)
        origin = self._origins.get(job_id)
        binding = self._run_bindings.get(job_id)
        if admission is None and self.journal_dir:
            from pathlib import Path
            import json
            matches = [json.loads(path.read_text(encoding='utf-8')) for path in Path(self.journal_dir).glob('wire-*.json')]
            matches = [row for row in matches if row.get('job_id') == job_id]
            if len(matches) > 1:
                raise ValueError('The exact provider job has ambiguous retained wire admissions')
            if matches:
                admission = matches[0]['context_admission']
                from .generation_origin import verify_wire_origin
                origin = verify_wire_origin(self.journal_dir, matches[0])
                from .generation_runs import wire_run_binding
                binding = wire_run_binding(self.journal_dir,matches[0],origin)
        if origin:
            from .generation_origin import verify_origin
            if self.plan:
                verify_origin(origin, self.plan)
            response = {**response, 'execution_origin': copy.deepcopy(origin)}
        if binding:
            if not (admission or {}).get('execution_run_required'):
                from .interpretation_runs import InterpretationRunRejected
                raise InterpretationRunRejected()
            response = {**response, 'execution_run_binding': copy.deepcopy(binding)}
        elif (admission or {}).get('execution_run_required') or (self.plan or {}).get('execution_run_required'):
            from .interpretation_runs import InterpretationRunRejected
            raise InterpretationRunRejected()
        if response.get('status') not in {'succeeded', 'completed'}:
            return response
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
