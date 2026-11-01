"""Offline review projection on retained Builder evidence; never calls AI."""
from __future__ import annotations

import copy
import json

from adaos.services.builder_context import digest
from adaos.services.builder.generation_plan import admit_generation_input
from adaos.services.builder.semantic_input import PROFILE, compose_semantic_input, input_contract_pin
from adaos.services.builder.task_spec import seal


def message_objects(messages):
    for row in messages:
        try:
            body = json.loads(row.get('content', ''))
        except (ValueError, TypeError):
            continue
        if isinstance(body, dict):
            yield body


def review_saved_generation(record, admission, *, profile):
    if profile != PROFILE:
        raise ValueError('Unknown semantic input review profile')
    original_plan = admission['generation_plan']
    if original_plan.get('context_stage') == 'repair':
        raise ValueError('This review projection requires a primary generation admission')
    options = copy.deepcopy(record['generation']['options'])
    if options.get('tools'):
        raise ValueError('This offline review requires complete inline input without tool credentials')
    model = record['generation']['model']
    kwargs = {'model': model, 'reasoning': options.get('reasoning'), 'text': options.get('text'),
              'tools': options.get('tools'), 'tool_choice': options.get('tool_choice'),
              'transport': original_plan['transport']}
    original_receipt = admit_generation_input(record['messages'], plan=original_plan, **kwargs)
    messages = copy.deepcopy(record['messages'])
    from adaos.services.builder.input_composition import scoped_instruction
    if messages and messages[0].get('role')=='system':
        messages[0]['content']=scoped_instruction(messages[0]['content'])
    advice = [body['semantic_capability_context'] for body in message_objects(messages)
              if isinstance(body.get('semantic_capability_context'), dict)]
    if len(advice) > 1:
        raise ValueError('Retained capability advice is ambiguous')
    from adaos.services.builder.input_composition import composition_contract
    plan = seal({**original_plan, 'semantic_input_contract': input_contract_pin(profile),
                 'input_composition':composition_contract(messages),
                 'capability_context_digest': digest(advice[0]) if advice else None},
                id_key='generation_plan_id', prefix='generation-plan:')
    source_task = None
    for row in messages:
        try:
            body = json.loads(row['content'])
        except (ValueError, TypeError):
            continue
        if 'builder_request' in body:
            source_task = copy.deepcopy(body['builder_request'])
            body['builder_request']['generation_plan_ref'] = plan['generation_plan_id']
            row['content'] = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if source_task is None:
        raise ValueError('Retained generation task is missing')
    messages = compose_semantic_input(messages, plan=plan, text=options.get('text'))
    receipt = admit_generation_input(messages, plan=plan, **kwargs)
    actual_task = next(body['builder_request'] for body in message_objects(messages) if 'builder_request' in body)
    expected_task = {**source_task, 'generation_plan_ref': plan['generation_plan_id']}
    if actual_task != expected_task:
        raise ValueError('Offline review cannot rewrite task meaning')
    before = sum(len(row['content'].encode('utf-8')) for row in record['messages'])
    after = sum(len(row['content'].encode('utf-8')) for row in messages)
    before_guidance = next(body['stable_builder_context']['semantic_invariants']
        for body in message_objects(record['messages']) if 'stable_builder_context' in body)
    after_guidance = next(body['stable_builder_context']['semantic_invariants']
        for body in message_objects(messages) if 'stable_builder_context' in body)
    after_advice = [body['semantic_capability_context'] for body in message_objects(messages)
                   if 'semantic_capability_context' in body]
    for item in after_advice:
        item.pop('authoring_scope', None)
    preserved = {'task_except_plan_ref': actual_task == expected_task,
        'source_assertions': actual_task['prototype_brief'] == source_task['prototype_brief'],
        'output_schema': options['text'] == record['generation']['options']['text'],
        'model': model == record['generation']['model'],
        'reasoning': options.get('reasoning') == record['generation']['options'].get('reasoning'),
        'capability_items': after_advice == advice, 'guidance_fragments': before_guidance == after_guidance}
    if not all(preserved.values()):
        raise ValueError('Offline review changed a protected source input')
    report = {'schema': 'adaos.evidence.semantic_input_review.v1', 'model_calls': 0,
        'scope': 'offline_projection_of_saved_Builder_input_not_observed_provider_wire',
        'source_messages_digest': digest(record['messages']), 'source_plan_ref': original_plan['generation_plan_id'],
        'source_admission_status': original_receipt['status'], 'proposed_admission_status': receipt['status'],
        'profile': profile, 'authoring_admission': receipt['semantic_input_admission'],
        'preserved': preserved,
        'message_utf8_bytes': {'before': before, 'after': after, 'delta': after - before},
        'candidate_messages_digest': digest(messages),
        'review_status': 'requires_independent_content_review_and_held_out_generation',
        'input_sufficiency_proven': False, 'llm_understanding_proven': False,
        'latency_improvement_proven': False, 'inference_submission_authorized': False}
    source_contract=(admission.get('brief') or {}).get('task_contract')
    from adaos.services.builder.source_obligations import contract_obligation_findings
    report['current_source_validation'] = ({'status':'requires_readmission','findings':problems}
        if (problems:=contract_obligation_findings(source_contract,require_derived=True)) else {'status':'passed'}) if source_contract else {'status':'not_available'}
    # Explicitly not a fresh TaskSpec/Run/origin. Reusing its old execution origin
    # for inference would be rejected; live qualification must prepare a new Run.
    packet = {'schema': 'adaos.evidence.semantic_input_review_packet.v1',
        'purpose': 'isolated_content_review_only_not_an_execution_admission',
        'source_plan_ref': original_plan['generation_plan_id'], 'generation_plan': plan,
        'messages': messages, 'model': model, 'options': {key: options[key]
            for key in ('text', 'reasoning', 'tool_choice') if key in options},
        'inference_submission_authorized': False}
    return report, packet


def capture_personal_projection(messages, *, model, text, reasoning=None, plan=None, work_dir):
    """Actual adapter/serializer, fake account and mock HTTP; never AIService.

    Returns exact body bytes and safe admission metadata. No authentication
    headers, live vault, execution authority or production state are captured.
    """
    import socket
    import threading
    import time
    from pathlib import Path
    from unittest.mock import patch
    import httpx
    from adaos.sdk import ai
    from adaos.services import context_control
    from adaos.services.ai.chatgpt import ChatGPTAccount, PLAN_SCOPE
    from adaos.services.ai.store import AIStore
    from adaos.services.builder.personal_prototype import PersonalBuilderJobs

    directory=Path(work_dir)
    captured=[]
    def capture(**kwargs):
        captured.append(copy.deepcopy(kwargs))
        return {'job_id':'offline-capture','status':'queued','input_digest':'offline-not-an-attempt'}
    class Vault:
        def read(self):
            return {'access_token':'offline-fixture','expires_at':time.time()+3600,'scope':PLAN_SCOPE}
    service=context_control.ContextControlService(directory/'context')
    with patch.object(context_control,'ContextControlService',return_value=service), \
         patch.object(socket.socket,'connect',side_effect=RuntimeError('offline_network_disabled')), \
         patch.object(socket.socket,'connect_ex',side_effect=RuntimeError('offline_network_disabled')), \
         patch.object(ai,'submit',side_effect=capture):
        provider=PersonalBuilderJobs({'builder_ai_resource_ref':ai.RESOURCE_REF,
            'builder_subscription_output_policy':'provider_default','builder_ai_expected_binding':'offline',
            'builder_llm_model':model})
        record=provider.submit(messages,model=model,request_id='offline-capture',text=text,reasoning=reasoning,generation_plan=plan)
        # Mirror AIService's key freeze without running its submission or store.
        frozen=json.loads(json.dumps(captured[0],ensure_ascii=False,sort_keys=True))
        bodies,receipts=[],[]
        def handler(request):
            bodies.append(bytes(request.content))
            events=[{'type':'response.output_text.delta','delta':'{}'},
                    {'type':'response.completed','response':{'output':[]}}]
            return httpx.Response(200,text='\n\n'.join('data: '+json.dumps(event) for event in events)+'\n\n')
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            account=ChatGPTAccount(AIStore(directory/'fake-account'),Vault(),client)
            list(account.stream(model,frozen['inputs'],frozen['instructions'],threading.Event(),
                text=frozen.get('text'),reasoning_effort=frozen.get('reasoning_effort'),on_wire_request=receipts.append))
    if len(bodies)!=1:
        raise ValueError('Offline serialization must produce exactly one local mock request')
    actual=json.loads(bodies[0])
    if (actual.get('instructions')!=frozen['instructions'] or actual.get('input')!=frozen['inputs']
            or actual.get('model')!=model or actual.get('text')!=frozen.get('text')
            or (actual.get('reasoning') or {}).get('effort')!=frozen.get('reasoning_effort')):
        raise ValueError('The actual serialized content differs from the admitted adapter projection')
    return bodies[0],{'scope':'current_adapter_and_mock_http_serialization_not_native_paid_wire',
        'provider_network_requests':0,'model_calls':0,'synthetic_http_requests':1,
        'serialized_content_preserved':True,
        'context_admission':record.get('context_admission'),'wire_receipt':receipts[0],
        'inference_submission_authorized':False}
