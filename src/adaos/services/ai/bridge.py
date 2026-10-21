"""Purpose-scoped loopback bridge for supervised service SDK clients."""
from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace

from adaos.ports.skill_context import CurrentSkill
from adaos.services.policy.skill_capabilities import require_skill_capability
from .service import ai_service

_LOCK = threading.Lock()
_TOKENS = {}
_SKILLS = {}
MANAGE = frozenset(('connect', 'disconnect', 'verify', 'cancel_sign_in', 'adopt_legacy'))
READ = frozenset(('status', 'models', 'submit', 'job', 'cancel'))


@dataclass(frozen=True)
class BridgeGrant:
    ctx: object
    skill: str
    root: Path


def _admission_ctx(grant):
    return SimpleNamespace(paths=grant.ctx.paths,
        authority_state_dir=getattr(grant.ctx, 'authority_state_dir', None),
        skill_ctx=SimpleNamespace(get=lambda: CurrentSkill(grant.skill, grant.root)))


def bridge_environment(ctx, skill: str, root: Path, *, legacy_root=None):
    revoke_bridge(skill)
    if skill != 'context_chat_service':
        # Other service UIs have no verified-user transport yet. A manifest
        # declaration alone must not turn a shared service into an owner proxy.
        return {}
    grant = BridgeGrant(ctx, skill, Path(root).resolve())
    try:
        require_skill_capability(_admission_ctx(grant), 'ai.inference')
    except PermissionError:
        return {}
    token = secrets.token_urlsafe(32)
    with _LOCK:
        _TOKENS[token] = grant
        _SKILLS[skill] = token
    if skill == 'context_chat_service' and legacy_root is not None:
        service = ai_service(ctx)
        service.legacy_root = Path(legacy_root).resolve()
    port = int(os.getenv('ADAOS_RUNTIME_PORT') or '8777')
    if not 1 <= port <= 65535:
        raise ValueError('ai_core_port_invalid')
    return {'ADAOS_AI_BRIDGE_URL': f'http://127.0.0.1:{port}/api/node/internal/ai',
            'ADAOS_AI_BRIDGE_TOKEN': token}


def revoke_bridge(skill):
    with _LOCK:
        old = _SKILLS.pop(skill, None)
        grant = None
        if old:
            grant = _TOKENS.pop(old, None)
    if grant is not None:
        service = ai_service(grant.ctx)
        with service.store.db() as db:
            active = db.execute("SELECT id FROM attempts WHERE consumer=? AND status IN ('queued','running')",
                                ('skill:' + skill,)).fetchall()
        for row in active:
            service.cancel(service.owner_ref, 'skill:' + skill, row['id'])


def invoke_service(token, remote_host, operation, arguments):
    try:
        loopback = ip_address(remote_host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise PermissionError('ai_loopback_required')
    with _LOCK:
        grant = _TOKENS.get(token)
    if grant is None:
        raise PermissionError('ai_service_credential_invalid')
    # Service credentials attest the executor, not an end-user. Only the
    # owner-only foreground consumer is supported until verified-user ingress.
    if operation not in READ:
        raise PermissionError('ai_service_operation_denied')
    require_skill_capability(_admission_ctx(grant), 'ai.inference')
    from adaos.services.applications.runtime_selection import application_execution
    with application_execution(grant.ctx, grant.skill):
        service = ai_service(grant.ctx)
        return dispatch(service, service.owner_ref, 'skill:' + grant.skill, operation, arguments)


def invoke_native(operation, arguments):
    from adaos.sdk.core._ctx import require_ctx
    from adaos.services.policy.caller import current_caller
    ctx = require_ctx('sdk.ai')
    caller = current_caller()
    if caller is None or caller.kind != 'user':
        raise PermissionError('ai_verified_user_required')
    principal = 'user:' + caller.id
    service = ai_service(ctx)
    service.authorize(principal)
    require_skill_capability(ctx, 'ai.connections.manage' if operation in MANAGE else 'ai.inference')
    current = ctx.skill_ctx.get()
    return dispatch(service, principal, 'skill:' + current.name, operation, arguments)


def dispatch(service, principal, consumer, operation, arguments):
    if operation not in MANAGE | READ or not isinstance(arguments, dict):
        raise ValueError('ai_operation_invalid')
    service.authorize(principal)
    shapes = {'status': set(), 'models': {'refresh'}, 'connect': set(), 'disconnect': set(),
              'verify': set(), 'cancel_sign_in': set(), 'adopt_legacy': {'confirmed'},
              'submit': {'request_id', 'request'}, 'job': {'job_id'}, 'cancel': {'job_id'}}
    if set(arguments) - shapes[operation]:
        raise ValueError('ai_operation_arguments_invalid')
    if operation == 'status':
        return service.status(principal)
    if operation == 'models':
        return {'models': service.provider.models(refresh=arguments.get('refresh', False))}
    if operation == 'connect':
        return service.provider.start()
    if operation == 'disconnect':
        return service.disconnect(principal)
    if operation == 'verify':
        return service.provider.verify()
    if operation == 'cancel_sign_in':
        return service.provider.cancel_sign_in()
    if operation == 'adopt_legacy':
        from .migration import adopt_legacy
        return adopt_legacy(service, confirmed=arguments.get('confirmed') is True)
    if operation == 'submit':
        return service.submit(principal, consumer, arguments.get('request_id'), arguments.get('request'))
    if operation == 'job':
        return service.job(principal, consumer, arguments.get('job_id'))
    return service.cancel(principal, consumer, arguments.get('job_id'))
