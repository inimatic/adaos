"""One native Builder provider selection and immutable stage-plan binding."""
from __future__ import annotations

import copy


class PlannedBuilderJobs:
    def __init__(self, delegate, plan, journal_dir=None, *, run_observer=None, run_observer_factory=None):
        self.delegate, self.plan, self.journal_dir = delegate, plan, journal_dir
        self._admissions = {}
        self._origins = {}
        self._run_bindings = {}
        self._wires = {}
        self.run_observer = run_observer
        self._run_observer_factory = run_observer_factory

    def _observer(self):
        if self.run_observer is None:
            if self._run_observer_factory is not None:
                self.run_observer = self._run_observer_factory()
            else:
                from .generation_runs import GenerationRuns
                self.run_observer = GenerationRuns()
        return self.run_observer

    def _binding(self, plan, origin):
        if not plan or not plan.get('execution_run_required'):
            return None
        from .generation_runs import retained_run_binding
        binding = retained_run_binding(self.journal_dir,plan,origin)
        if binding:
            observer = self._observer()
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
            if response.get('job_id'):
                self._wires[response['job_id']] = copy.deepcopy(record)
        if origin:
            response = {**response, 'execution_origin': copy.deepcopy(origin)}
        if binding:
            response = {**response, 'execution_run_binding': copy.deepcopy(binding)}
            if not admission or not admission.get('execution_run_required') or not response.get('job_id'):
                from .interpretation_runs import InterpretationRunRejected
                raise InterpretationRunRejected()
            observed = self._observe(response, self._wires.get(response['job_id']))
            if observed.get('generation_output'):
                response = {**response, 'generation_output': observed['generation_output']}
        return self._delivery(response, admission) if response.get('status') in {'succeeded', 'completed'} and admission else response

    def _observe(self, response, wire):
        if not (wire or {}).get('execution_run_binding'):
            from .interpretation_runs import InterpretationRunRejected
            raise InterpretationRunRejected()
        import json
        import re
        from pathlib import Path
        admission = wire['context_admission']
        ref = admission['generation_plan_ref']
        if not isinstance(ref,str) or not re.fullmatch(r'generation-plan:[a-f0-9]{24}',ref):
            from .interpretation_runs import InterpretationRunRejected
            raise InterpretationRunRejected()
        name = ('repair-plan-' + ref.partition(':')[2] if admission.get('parent_generation_plan_ref')
                else ref.replace(':','-'))
        plan = json.loads((Path(self.journal_dir) / (name+'.json')).read_text(encoding='utf-8'))['generation_plan']
        observer = self._observer()
        return observer.observe(wire['execution_run_binding'],wire['execution_origin'],plan,
            journal_dir=self.journal_dir,wire=wire,response=response)

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
        return self._submit('submit', messages, options, origin, binding)

    def _submit(self, operation, messages, options, origin, binding):
        from .semantic_input import compose_semantic_input
        messages = compose_semantic_input(messages, plan=options.get('generation_plan'), text=options.get('text'))
        if not binding:
            return self._record(getattr(self.delegate,operation)(messages,**options),options.get('request_id'),origin)
        from .generation_submissions import GenerationSubmissions
        scope = GenerationSubmissions(self.journal_dir,binding=binding,origin=origin,plan=options['generation_plan'])
        with scope.submitting(options.get('request_id')) as register:
            response = getattr(self.delegate,operation)(messages,**options)
            register(response)
            return self._record(response,options.get('request_id'),origin,binding)

    def send(self, messages, **options):
        from .generation_origin import load_generation_origin
        actual = {"generation_plan": self.plan, **options} if self.plan else options
        origin = load_generation_origin(self.journal_dir, actual.get('generation_plan'))
        binding = self._binding(actual.get('generation_plan'),origin)
        return self._submit('send', messages, actual, origin, binding)

    def wait(self, *args, **options):
        response = self.delegate.wait(*args, **options)
        job_id = response.get('job_id') or (args[0] if args else None)
        admission = self._admissions.get(job_id)
        origin = self._origins.get(job_id)
        binding = self._run_bindings.get(job_id)
        wire = self._wires.get(job_id)
        if admission is None and self.journal_dir:
            from pathlib import Path
            import json
            matches = [json.loads(path.read_text(encoding='utf-8')) for path in Path(self.journal_dir).glob('wire-*.json')]
            matches = [row for row in matches if row.get('job_id') == job_id]
            if len(matches) > 1:
                raise ValueError('The exact provider job has ambiguous retained wire admissions')
            if matches:
                wire = matches[0]
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
            observed = self._observe({**response,'job_id':job_id}, wire)
            if observed.get('generation_output'):
                response = {**response, 'generation_output': observed['generation_output']}
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


def provider_jobs(metadata, *, journal_dir=None, run_observer_factory=None):
    if (metadata or {}).get('builder_ai_resource_ref'):
        from .personal_prototype import PersonalBuilderJobs
        delegate = PersonalBuilderJobs(metadata)
    else:
        from adaos.sdk.llm import llm_client
        delegate = type('RootBuilderJobs', (), {
            'submit': staticmethod(llm_client.submit_response_job),
            'wait': staticmethod(llm_client.wait_response_job),
            'send': staticmethod(llm_client.send_response)})()
    return PlannedBuilderJobs(delegate, (metadata or {}).get('builder_generation_plan'), journal_dir,
        run_observer_factory=run_observer_factory)
