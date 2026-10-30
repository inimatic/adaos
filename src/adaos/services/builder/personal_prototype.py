"""Foreground Prototype-only adapter for the Core AI attempt contract.

No hosted MCP, Automation/Codex, API billing estimates or automatic fallback.
SIWC has no provider-side output-token cap; selection must explicitly accept
provider-default output policy. Core's byte capacity is not a spend guarantee.
"""
from __future__ import annotations

import time

from adaos.services.ai.service import RESOURCE_REF, TERMINAL


class PersonalPrototypeJobs:
    base_url = 'core-ai://personal-chatgpt'

    def submit(self, *, messages, model, reasoning, text, request_id, ai_resource_ref,
               ai_expected_binding, subscription_output_policy, tools, tool_choice,
               profile_scope, stream=False, provider_max_attempts=1, timeout=30):
        if ai_resource_ref != RESOURCE_REF or subscription_output_policy != 'provider_default':
            raise ValueError('Explicit personal resource and provider-default output consent are required')
        if tools or tool_choice != 'none' or profile_scope != 'development' or provider_max_attempts != 1:
            raise ValueError('Personal Prototype supports bounded text proposals without tools or retries')
        from adaos.sdk import ai
        instructions = '\n\n'.join(row['content'] for row in messages if row['role'] == 'system')
        inputs = [dict(row) for row in messages if row['role'] != 'system']
        options = {'text': text}
        if reasoning:
            options['reasoning_effort'] = reasoning['effort']
        result = ai.submit(request_id=request_id, model=model, inputs=inputs, instructions=instructions,
            expected_binding=ai_expected_binding, resource_ref=ai_resource_ref, **options)
        return {**result, 'model': model, '_client': {'base_url': self.base_url}}

    def wait(self, job_id, *, base_url=None, timeout_s=300, poll_interval_s=.5):
        if base_url not in (None, self.base_url):
            raise ValueError('A personal Prototype attempt cannot switch to Root')
        from adaos.sdk import ai
        deadline = time.monotonic() + max(0, timeout_s)
        while True:
            result = ai.job(job_id)
            if result['status'] in TERMINAL:
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError('Core AI observation timed out; the attempt was not resubmitted or cancelled')
            time.sleep(min(max(.05, poll_interval_s), max(0, deadline - time.monotonic())))


class PersonalBuilderJobs:
    """Adapt foreground Builder jobs, not the global Root transport.

    The selection is frozen in the turn metadata and survives copied worker
    contexts. Unsupported tools/egress fail closed before any submission.
    Root-only sampling/token/cache knobs are intentionally never forwarded.
    """
    base_url = PersonalPrototypeJobs.base_url

    def __init__(self, metadata):
        self.metadata = dict(metadata)
        if (self.metadata.get('builder_ai_resource_ref') != RESOURCE_REF
                or self.metadata.get('builder_subscription_output_policy') != 'provider_default'
                or not self.metadata.get('builder_ai_expected_binding')):
            raise ValueError('Personal Prototype requires a pinned connection and provider-default output consent')

    @staticmethod
    def _result(record):
        tools = record.get('tool_telemetry') or {}
        return {**record, 'status': 'succeeded' if record['status'] == 'completed' else record['status'],
                '_client': {'base_url': PersonalBuilderJobs.base_url},
                'telemetry': {'usage': record.get('usage') or {},
                              'funding_source': 'personal_subscription',
                              'cost_basis': 'subscription_usage_not_api_billing',
                              'provider_calls': tools.get('provider_calls', 1),
                              'tools': {'requested_count': 2 if tools.get('toolset') else 0,
                                  'used_count': len(tools.get('tool_calls') or [])},
                              'mcp': {'used_mcp': bool(tools.get('tool_calls'))}}}

    def submit(self, messages, *, model, request_id, **options):
        from adaos.sdk import ai
        if options.get('stream_protocol'):
            raise ValueError('Personal Prototype does not support hosted tools or JSONL generation')
        if options.get('profile_scope', 'development') != 'development':
            raise ValueError('Personal resource is not an Automation profile')
        if not model or model != self.metadata.get('builder_llm_model'):
            raise ValueError('Personal Prototype cannot change the selected model')
        forwarded = {}
        from .prototype_mcp import personal_toolset_options
        forwarded.update(personal_toolset_options(options.get('tools'), options.get('tool_choice', 'none')))
        # Reuse the Root path's component-fact projection and final admission.
        # Only normalized messages are carried across; Root billing/cache/sampling
        # options remain outside the personal resource contract.
        from adaos.sdk.llm.llm_client import _llm_root_payload
        prepared_input, admitted_messages = _llm_root_payload(messages, model=model,
            reasoning=options.get('reasoning'), text=options.get('text'),
            tools=options.get('tools'), tool_choice=options.get('tool_choice', 'none'),
            request_id=request_id)
        from .generation_plan import admit_generation_input
        admission = admit_generation_input(admitted_messages, model=model, text=options.get('text'),
            reasoning=options.get('reasoning'), tools=options.get('tools'),
            tool_choice=options.get('tool_choice', 'none'), transport='personal',
            plan=options.get('generation_plan') or self.metadata.get('builder_generation_plan'))
        if admission and admission.get('required_retrieval_refs'):
            if not forwarded.get('toolset'):
                raise ValueError('Required guidance has no Core-owned executor')
            forwarded['toolset']['required_fragment_ids'] = admission['required_retrieval_refs']
        # Use the same role normalization and instruction authority as Root.
        # The personal bridge is text-only, so convert its one text-part items
        # back to the bridge ABI without moving developer policy into user data.
        inputs = []
        for row in prepared_input.get('input') or []:
            content = row.get('content')
            if (row.get('role') not in {'user', 'assistant'} or not isinstance(content, list)
                    or len(content) != 1 or content[0].get('type') not in {'input_text', 'output_text'}):
                raise ValueError('Personal Builder initial input supports normalized text messages only')
            inputs.append({'role': row['role'], 'content': content[0]['text']})
        instructions = prepared_input.get('instructions', '')
        if options.get('text'):
            forwarded['text'] = options['text']
        if options.get('reasoning'):
            forwarded['reasoning_effort'] = options['reasoning']['effort']
        result = self._result(ai.submit(request_id=request_id, model=model, inputs=inputs,
            instructions=instructions, expected_binding=self.metadata['builder_ai_expected_binding'],
            resource_ref=RESOURCE_REF, **forwarded))
        if admission:
            from adaos.services.builder_context import digest
            admission['adapter_input_digest'] = digest({'instructions': instructions, 'inputs': inputs,
                'model': model, **forwarded})
            admission['provider_attempt_input_digest'] = result.get('input_digest')
            result['context_admission'] = admission
        return result

    def wait(self, job_id, *, progress_callback=None, request_timeout=None, **options):
        # Observation timeout does not cancel or resubmit the durable Core job.
        started = time.monotonic()
        try:
            result = PersonalPrototypeJobs().wait(job_id, **options)
        except TimeoutError as exc:
            from adaos.sdk.llm.llm_client import LlmJobWaitTimeout
            raise LlmJobWaitTimeout({'job_id': job_id, 'root_status': 'unknown',
                'reason': 'personal_ai_observation_timeout', 'elapsed_ms': round((time.monotonic() - started) * 1000),
                'last_poll_error': None, 'provider_job_id': job_id,
                'funding_source': 'personal_subscription', 'resubmitted': False}) from exc
        return self._result(result)

    def send(self, messages, *, timeout=300, **options):
        record = self.submit(messages, **options)
        result = self.wait(record['job_id'], base_url=self.base_url, timeout_s=min(timeout, 540))
        return {**result, **({'context_admission': record['context_admission']} if record.get('context_admission') else {})}
