"""Bounded client-side tools; the model never receives executor credentials.

Only a registered, owner-authorized toolset can execute anything. No user URLs,
shell, arbitrary MCP, retries, persisted model reasoning or durable replay.
"""
from __future__ import annotations

import json
import hashlib

from .chatgpt import AccountError


def prepare_toolset(specification, consumer):
    from adaos.services.builder.prototype_mcp import PERSONAL_TOOLSET, PersonalGuidanceTools
    if (not isinstance(specification, dict) or specification.get('id') != PERSONAL_TOOLSET
            or consumer != 'skill:builder_skill'):
        raise ValueError('ai_toolset_not_allowed')
    return PersonalGuidanceTools(specification)


def run_tool_loop(provider, request, cancelled, toolset, *, on_usage, on_telemetry):
    """One durable attempt, at most two provider calls and four public reads."""
    inputs = list(request['inputs'])
    used = set()
    trace = []
    receipts = []
    wire_calls = []
    for turn in range(toolset.max_provider_calls):
        if cancelled.is_set():
            return ''
        if len(json.dumps(inputs, ensure_ascii=False).encode('utf-8')) > 1024 * 1024:
            raise AccountError('ai_input_capacity_exceeded')
        output_items, chunks = [], []
        options = {key: request[key] for key in ('expected_binding', 'reasoning_effort', 'text') if key in request}
        options.update(tools=[{'type': 'namespace', 'name': toolset.namespace,
            'description': 'Read-only, digest-pinned public AdaOS Prototype authoring guidance.',
            'tools': toolset.definitions}], tool_choice='auto' if turn == 0 else 'none',
            on_output_items=lambda items: output_items.extend(items),
            on_usage=lambda usage: receipts.append(usage))
        def telemetry():
            on_telemetry({'provider_calls': turn + 1, 'tool_calls': list(trace),
                'toolset': request['toolset']['id'], 'wire_calls': list(wire_calls)})
        def wire_receipt(value):
            wire_calls.append({'provider_call': turn + 1, **value})
            telemetry()
        options['on_wire_request'] = wire_receipt
        telemetry()
        output_bytes = 0
        for delta in provider.stream(request['model'], inputs, request.get('instructions', ''), cancelled, **options):
            chunks.append(delta)
            output_bytes += len(delta.encode('utf-8'))
            if output_bytes > 2 * 1024 * 1024:
                raise AccountError('ai_response_capacity_exceeded')
        if cancelled.is_set():
            return ''
        on_usage(_combined_usage(receipts))
        if (len(output_items) > 64 or len(json.dumps(output_items).encode('utf-8')) > 512 * 1024
                or any(not isinstance(item, dict) or item.get('type') not in ('message', 'reasoning', 'function_call') for item in output_items)):
            raise AccountError('ai_tool_response_invalid')
        calls = [item for item in output_items if item.get('type') == 'function_call']
        if not calls:
            text = ''.join(chunks)
            if not text.strip():
                raise AccountError('ai_text_response_required')
            return text
        if turn or len(calls) > toolset.max_tool_calls:
            raise AccountError('ai_tool_budget_exceeded')
        admitted = []
        for call in calls:
            identity, name = call.get('call_id'), call.get('name')
            if isinstance(name, str) and name.startswith(toolset.namespace + '.'):
                name = name[len(toolset.namespace) + 1:]
            if (not isinstance(identity, str) or not identity or len(identity) > 200 or identity in used
                    or call.get('namespace') not in (None, toolset.namespace)
                    or name not in {row['name'] for row in toolset.definitions}
                    or not isinstance(call.get('arguments'), str) or len(call['arguments']) > 4096):
                raise AccountError('ai_tool_call_not_allowed')
            used.add(identity)
            try:
                arguments = json.loads(call['arguments'])
                from jsonschema import Draft202012Validator
                definition = next(row for row in toolset.definitions if row['name'] == name)
                Draft202012Validator(definition['parameters']).validate(arguments)
            except Exception as exc:
                raise AccountError('ai_tool_arguments_invalid') from exc
            admitted.append((identity, name, arguments))
        inputs.extend(output_items)
        for identity, name, arguments in admitted:
            if cancelled.is_set():
                return ''
            try:
                result = toolset.execute(name, arguments)
                encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
                if len(encoded.encode('utf-8')) > 64 * 1024:
                    raise ValueError('oversized result')
            except Exception as exc:
                raise AccountError('ai_tool_execution_failed') from exc
            inputs.append({'type': 'function_call_output', 'call_id': identity, 'output': encoded})
            trace.append({'name': name, 'status': 'completed',
                'arguments_digest': _digest(arguments), 'result_digest': _digest(result)})
            telemetry()
        inputs.append({'role': 'developer', 'content': 'The read-only tool budget is now closed. Use the retrieved guidance and return the requested final answer. Do not request more tools.'})
    raise AccountError('ai_tool_budget_exceeded')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def _combined_usage(receipts):
    from .service import _usage
    result = {}
    for value in receipts:
        for key, item in _usage(value).items():
            if isinstance(item, dict):
                target = result.setdefault(key, {})
                for field, count in item.items():
                    target[field] = target.get(field, 0) + count
            else:
                result[key] = result.get(key, 0) + item
    return result
