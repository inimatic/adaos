"""Controlled node image import for authenticated MCP/skill calls."""
from __future__ import annotations
import json
from pathlib import Path
from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.caller import current_caller
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.internet_images import InternetImages


def _binding(project_id):
    ctx = require_ctx('sdk.internet_images')
    require_skill_capability(ctx, 'network.images')
    from adaos.sdk import access
    access.require('network.images')
    caller = current_caller()
    if caller is None or caller.kind != 'user':
        raise PermissionError('image_verified_user_required')
    if not isinstance(project_id, str) or not 1 <= len(project_id) <= 200:
        raise ValueError('image_project_required')
    skill = ctx.skill_ctx.get()
    authority = getattr(ctx, 'authority_context', None) or ctx
    state = Path(getattr(authority, 'authority_state_dir', None) or authority.paths.state_dir())
    scope = json.dumps([caller.kind, caller.id, skill.name, project_id], separators=(',',':'))
    return InternetImages(state/'internet-images'), scope, authority, caller


def policy(*, project_id):
    service, *_ = _binding(project_id)
    return service.policy()


def set_policy(*, project_id, mode):
    service, _, authority, caller = _binding(project_id)
    from adaos.services.personalization_runtime import current_user_id
    if caller.id != current_user_id(authority):
        raise PermissionError('image_policy_node_owner_required')
    return service.set_policy(mode)


def authorize_task(*, project_id, confirmed=False):
    service, scope, *_ = _binding(project_id)
    return service.grant(scope, confirmed=confirmed)


def fetch(*, project_id, grant_id, request_id, url):
    service, scope, *_ = _binding(project_id)
    return service.fetch(scope, grant_id, request_id, url)


def task_status(*, project_id, grant_id):
    service, scope, *_ = _binding(project_id)
    return service.task_status(scope, grant_id)


def revoke(*, project_id, grant_id):
    service, scope, *_ = _binding(project_id)
    service.revoke(scope, grant_id)


def read(*, project_id, grant_id, fetch_id):
    service, scope, *_ = _binding(project_id)
    return service.read(scope, grant_id, fetch_id)
