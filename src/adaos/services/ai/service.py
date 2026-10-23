"""Core AI ownership and bounded, idempotent foreground inference.

M1 is owner-only: an app capability never delegates the owner's subscription to
another user. CBS selects an implementation; it does not grant access to it.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

from adaos.ports.ai import AIProvider
from .chatgpt import AccountError, ChatGPTAccount
from .store import AIStore

RESOURCE_REF = 'ai-resource:personal-chatgpt'
TERMINAL = frozenset(('completed', 'failed', 'cancelled', 'interrupted'))
_SERVICES = {}
_LOCK = threading.Lock()


class AIService:
    def __init__(self, root: Path, owner_ref: str, *, provider: AIProvider | None = None):
        self.owner_ref = owner_ref
        self.store = AIStore(root)
        self.provider = provider or ChatGPTAccount(self.store)
        self.lock = threading.RLock()
        self.running = {}
        # Do not replay an ambiguous provider request after Core restarts.
        with self.store.db() as db:
            db.execute("UPDATE attempts SET status='interrupted',error='core_restarted_no_automatic_retry' WHERE status IN ('queued','running')")

    def authorize(self, principal: str):
        if principal != self.owner_ref:
            raise PermissionError('ai_personal_resource_owner_required')

    def status(self, principal: str):
        self.authorize(principal)
        return {**self.provider.status(), 'resource_ref': RESOURCE_REF,
                'credential_owner': self.owner_ref, 'funding_source': 'personal_subscription',
                'provider': 'openai.chatgpt', 'sharing': 'owner_only',
                'legacy_migration_available': bool(getattr(self, 'legacy_root', None)
                    and (self.legacy_root / 'files/chatgpt-session.enc').is_file()),
                'management_application': 'subscription_status'}

    def submit(self, principal: str, consumer: str, request_id: str, request: dict):
        self.authorize(principal)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
            raise ValueError('ai_request_id_required')
        if not isinstance(consumer, str) or not 1 <= len(consumer) <= 200:
            raise ValueError('ai_consumer_required')
        allowed = {'resource_ref', 'model', 'inputs', 'instructions', 'expected_binding',
                   'web_search', 'reasoning_effort', 'text'}
        if not isinstance(request, dict) or set(request) - allowed:
            raise ValueError('ai_unsupported_request_fields')
        if request.get('resource_ref') != RESOURCE_REF:
            raise ValueError('ai_resource_not_found')
        if not isinstance(request.get('model'), str) or not request['model'].strip():
            raise ValueError('ai_explicit_model_required')
        if not isinstance(request.get('expected_binding'), str) or not request['expected_binding']:
            raise ValueError('ai_connection_binding_required')
        inputs = request.get('inputs')
        if not isinstance(inputs, list) or any(not isinstance(row, dict) or set(row) != {'role', 'content'} or row.get('role') not in ('user', 'assistant', 'developer') or not isinstance(row.get('content'), str) for row in inputs):
            raise ValueError('ai_text_input_array_required')
        if not isinstance(request.get('instructions', ''), str) or type(request.get('web_search', False)) is not bool:
            raise ValueError('ai_invalid_request')
        if 'reasoning_effort' in request and (not isinstance(request['reasoning_effort'], str) or not 1 <= len(request['reasoning_effort']) <= 40):
            raise ValueError('ai_invalid_reasoning_effort')
        if 'text' in request and not isinstance(request['text'], dict):
            raise ValueError('ai_invalid_text_format')
        encoded = json.dumps(request, sort_keys=True, ensure_ascii=False).encode('utf-8')
        if len(encoded) > 1024 * 1024:
            raise ValueError('ai_input_capacity_exceeded')
        fingerprint = hashlib.sha256(encoded).hexdigest()
        # Freeze caller-owned lists/dicts before the asynchronous provider turn.
        request = json.loads(encoded)
        with self.lock, self.store.db() as db:
            previous = db.execute('SELECT * FROM attempts WHERE principal=? AND consumer=? AND request_id=?',
                                  (principal, consumer, request_id)).fetchone()
            if previous:
                if previous['digest'] != fingerprint:
                    raise ValueError('ai_idempotency_conflict')
                return self._snapshot(previous)
            if request['expected_binding'] != self.provider.binding_ref():
                raise AccountError('ai_connection_changed')
            if len(self.running) >= 4:
                raise ValueError('ai_concurrency_limit')
            if db.execute('SELECT count(*) FROM attempts').fetchone()[0] >= 5000:
                raise ValueError('ai_attempt_retention_capacity_exceeded')
            job_id = 'ai-' + uuid4().hex
            event = threading.Event()
            db.execute('INSERT INTO attempts(id,principal,consumer,request_id,digest,binding,status,created) VALUES (?,?,?,?,?,?,?,?)',
                       (job_id, principal, consumer, request_id, fingerprint, request['expected_binding'], 'queued', time.time()))
            db.commit()
            self.running[job_id] = event
            threading.Thread(target=self._work, args=(job_id, request, event), daemon=True,
                             name='core-ai-inference').start()
        return self.job(principal, consumer, job_id)

    @staticmethod
    def _snapshot(row):
        return {'job_id': row['id'], 'status': row['status'], 'output_text': row['output'],
                'citations': json.loads(row['citations']), 'usage': json.loads(row['usage']),
                'error': row['error'], 'input_digest': row['digest'], 'binding_ref': row['binding'],
                'resource_ref': RESOURCE_REF, 'funding_source': 'personal_subscription',
                'cost_basis': 'subscription_usage_not_api_billing'}

    def job(self, principal, consumer, job_id):
        self.authorize(principal)
        with self.store.db() as db:
            row = db.execute('SELECT * FROM attempts WHERE id=? AND principal=? AND consumer=?',
                             (job_id, principal, consumer)).fetchone()
        if row is None:
            raise KeyError('ai_attempt_not_found')
        return self._snapshot(row)

    def cancel(self, principal, consumer, job_id):
        self.job(principal, consumer, job_id)
        with self.lock:
            event = self.running.get(job_id)
            if event:
                event.set()
        return self.job(principal, consumer, job_id)

    def disconnect(self, principal):
        self.authorize(principal)
        with self.lock:
            for event in self.running.values():
                event.set()
            return self.provider.disconnect()

    def _update(self, job_id, **fields):
        with self.store.db() as db:
            db.execute('UPDATE attempts SET ' + ','.join(key + '=?' for key in fields) + ' WHERE id=?',
                       (*fields.values(), job_id))

    def _work(self, job_id, request, event):
        try:
            if event.is_set():
                self._update(job_id, status='cancelled')
                return
            self._update(job_id, status='running')
            options = {key: request[key] for key in ('expected_binding', 'reasoning_effort', 'web_search', 'text') if key in request}
            options['on_citations'] = lambda refs: self._update(job_id, citations=json.dumps(refs))
            options['on_usage'] = lambda usage: self._update(job_id, usage=json.dumps(_usage(usage)))
            output = ''
            for delta in self.provider.stream(request['model'], request['inputs'], request.get('instructions', ''), event, **options):
                if event.is_set():
                    break
                output += delta
                if len(output.encode('utf-8')) > 2 * 1024 * 1024:
                    raise AccountError('ai_response_capacity_exceeded')
                self._update(job_id, output=output)
            self._update(job_id, status='cancelled' if event.is_set() else 'completed')
        except Exception as exc:
            # Never persist provider payloads, credentials, or arbitrary exception text.
            error = str(exc) if isinstance(exc, AccountError) else 'ai_provider_request_failed'
            self._update(job_id, status='cancelled' if event.is_set() else 'failed', error=error)
        finally:
            with self.lock:
                self.running.pop(job_id, None)


def ai_service(ctx):
    from adaos.services.personalization_runtime import current_user_id
    authority = getattr(ctx, 'authority_context', None) or ctx
    state = Path(getattr(authority, 'authority_state_dir', None) or authority.paths.state_dir()).resolve()
    owner_ref = 'user:' + current_user_id(authority)
    key = (str(state), owner_ref)
    with _LOCK:
        if key not in _SERVICES:
            from .contracts import install_contracts
            install_contracts(state)
            _SERVICES[key] = AIService(state / 'ai' / hashlib.sha256(owner_ref.encode()).hexdigest(), owner_ref)
        return _SERVICES[key]


def _usage(value):
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in ('input_tokens', 'output_tokens', 'total_tokens')
              if type(value.get(key)) is int and value[key] >= 0}
    for key, field in (('input_tokens_details', 'cached_tokens'), ('output_tokens_details', 'reasoning_tokens')):
        details = value.get(key)
        if isinstance(details, dict) and type(details.get(field)) is int and details[field] >= 0:
            result[key] = {field: details[field]}
    return result
