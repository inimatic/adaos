"""Personal AI capability. No credentials, implicit sponsor or paid fallback.

Native tools use verified caller + declared capability. Supervised services use
a purpose-scoped loopback lease issued by Core, never the node-owner token.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from uuid import uuid4
from urllib.parse import urlsplit

import httpx

from adaos.services.ai.chatgpt import AccountError
from adaos.services.ai.service import RESOURCE_REF, TERMINAL

__all__ = ['RESOURCE_REF', 'status', 'models', 'connect', 'disconnect', 'verify',
           'cancel_sign_in', 'adopt_context_chat_connection', 'submit', 'job', 'cancel',
           'stream', 'describe_capability', 'describe_model', 'ChatGPTConnection', 'AccountError']


def describe_capability() -> dict:
    """Discover the AI CBS contract, consumer declaration and SDK workflow.

    Pure metadata, not a grant or activation. Builder can use the returned
    cbs_intent with its normal compiler and compositions.set_capability_contracts
    to author requirements; portable providers still need delivery and evidence.
    """
    from adaos.services.ai.contracts import describe_capability as describe
    return describe()


def describe_model(model: dict) -> dict:
    """Normalize a provider model observation without paid inference or guessing."""
    from adaos.services.ai.models import descriptor
    return descriptor(model)


def _invoke(operation, **arguments):
    endpoint = os.getenv('ADAOS_AI_BRIDGE_URL')
    if not endpoint:
        from adaos.services.ai.bridge import invoke_native
        return invoke_native(operation, arguments)
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path != '/api/node/internal/ai' or parsed.username or parsed.query or parsed.fragment:
        raise AccountError('ai_bridge_endpoint_invalid')
    token = os.getenv('ADAOS_AI_BRIDGE_TOKEN')
    if not token:
        raise AccountError('ai_bridge_credential_missing')
    try:
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            result = client.post(endpoint, headers={'X-AdaOS-AI-Token': token},
                                 json={'operation': operation, 'arguments': arguments})
        if result.status_code != 200:
            raise AccountError(str(result.json().get('detail') or 'ai_core_request_failed'))
        return result.json()
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, AccountError):
            raise
        raise AccountError('ai_core_unavailable') from exc


def status() -> dict:
    """Read the verified owner's connection metadata, never upstream tokens."""
    return _invoke('status')


def models(*, refresh: bool = False) -> list[dict]:
    """List models and capability observations for the personal connection."""
    return _invoke('models', refresh=refresh)['models']


def connect() -> dict:
    """Start explicit owner sign-in. Requires ai.connections.manage."""
    return _invoke('connect')


def disconnect() -> dict:
    """Disconnect all consumers; keep application data. Requires management."""
    return _invoke('disconnect')


def verify() -> dict:
    """Start an owner-requested real probe that consumes ChatGPT plan resources."""
    return _invoke('verify')


def cancel_sign_in() -> dict:
    """Cancel pending Core sign-in without changing the active connection."""
    return _invoke('cancel_sign_in')


def adopt_context_chat_connection(*, confirmed: bool = False) -> dict:
    """Explicitly adopt the supervisor-pinned legacy session, not Chat history."""
    return _invoke('adopt_legacy', confirmed=confirmed)


def submit(*, request_id: str, model: str, inputs: list[dict], instructions: str = '',
           expected_binding: str, resource_ref: str = RESOURCE_REF, **options) -> dict:
    """Submit once per owner/executor/request id; never select a paid fallback.

Options are web_search, reasoning_effort and text. SIWC has no output-token
cap. Core byte/concurrency capacities are not subscription spend guarantees.
An interrupted attempt must be reconciled; it is not automatically replayed.
"""
    return _invoke('submit', request_id=request_id, request={'resource_ref': resource_ref,
        'model': model, 'inputs': inputs, 'instructions': instructions,
        'expected_binding': expected_binding, **options})


def job(job_id: str) -> dict:
    """Read only this owner's executing consumer's durable attempt."""
    return _invoke('job', job_id=job_id)


def cancel(job_id: str) -> dict:
    """Request cancellation, without promising reversal of consumed plan usage."""
    return _invoke('cancel', job_id=job_id)


def stream(*, model, inputs, instructions='', expected_binding, cancelled=None,
           request_id=None, on_citations=None, **options) -> Iterator[str]:
    """Observe text deltas from one Core attempt; reuse request_id on transport retry."""
    cancelled = cancelled or threading.Event()
    record = submit(request_id=request_id or uuid4().hex, model=model, inputs=inputs,
                    instructions=instructions, expected_binding=expected_binding, **options)
    offset = 0
    while True:
        if cancelled.is_set():
            cancel(record['job_id'])
            return
        output = record['output_text']
        if len(output) > offset:
            yield output[offset:]
            offset = len(output)
        if record['status'] in TERMINAL:
            if record['status'] != 'completed':
                raise AccountError(record.get('error') or 'ai_request_' + record['status'])
            if on_citations:
                on_citations(record.get('citations') or [])
            return
        cancelled.wait(.15)
        record = job(record['job_id'])


class ChatGPTConnection:
    """Chat-facing compatibility facade: inference only; manage in Subscriptions."""
    core_sdk = True
    def __init__(self, _store=None):
        pass

    status = staticmethod(status)
    def binding_ref(self):
        binding = status().get('binding_ref')
        if not binding:
            raise AccountError('ai_connection_not_enabled')
        return binding

    def models(self, refresh=False):
        return models(refresh=refresh)

    def cancel_sign_in(self):
        # Draining Chat must not cancel another application's Core sign-in.
        return {'ok': True}

    def stream(self, model, inputs, instructions, cancelled, **options):
        return stream(model=model, inputs=inputs, instructions=instructions,
                      cancelled=cancelled, **options)
