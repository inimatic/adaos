"""Verified desktop ingress; request payloads never establish identity."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from fastapi import HTTPException, Request

from adaos.apps.api.auth import _expected_token, resolve_presented_token
from adaos.domain.personalization_access import SubjectRef, ScopeRef
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
from adaos.services.policy.caller import CallerAccessDenied, verified_caller

_PRINCIPAL: ContextVar[DesktopPrincipal | None] = ContextVar("http_desktop_principal", default=None)


def connection_principal(connection) -> DesktopPrincipal | None:
    """Authenticate a node owner or a purpose-bound desktop session credential."""
    headers = connection.headers
    params = connection.query_params
    credential = str(headers.get('X-AdaOS-Desktop-Credential') or params.get('desktop_token') or '')
    token = resolve_presented_token(x_adaos_token=headers.get('X-AdaOS-Token'),
                                    authorization=headers.get('Authorization'), query_token=params.get('token'))
    if not credential and str(token or '').startswith('adaos-desktop-v1.'):
        credential = str(token)
    if credential:
        return DesktopAccessService().authenticate(credential)
    if token and token == _expected_token():
        if headers.get('X-AdaOS-Route-Authenticated') == '1' and headers.get('X-AdaOS-Route-Principal') == 'unverified':
            return None
        from adaos.services.personalization_runtime import current_user_id
        return DesktopPrincipal(SubjectRef('user', current_user_id()))
    return None


async def require_desktop_caller(request: Request):
    try:
        principal = await asyncio.to_thread(connection_principal, request)
        if principal is None:
            raise HTTPException(status_code=401, detail='desktop_authentication_required')
        request.state.adaos_desktop_principal = principal
        request.state.adaos_verified_caller = principal.actor
        policy = DesktopAccessService()
        request.state.adaos_owner_node_credential = principal.actor == policy.access.owner
        desktop = request.path_params.get('webspace_id')
        home_catalog = request.url.path.endswith('/yjs/home-applications')
        if home_catalog:
            desktop = request.query_params.get('webspace_id')
        suffix = request.url.path.split('/yjs/webspaces', 1)[-1]
        payload = await request.json() if request.method in {'POST', 'PATCH'} and request.headers.get('content-type', '').startswith('application/json') else {}
        permissions = []
        if desktop and request.method in {'POST', 'PATCH'}:
            if suffix.endswith('/desktop/pinned-applications'):
                permissions.append('desktop.applications.pin')
            elif suffix.endswith('/desktop/pinned-widgets'):
                permissions.append('desktop.widgets.pin')
            elif suffix.endswith('/desktop/icon-media'):
                permissions.append('desktop.layout.customize')
            elif request.method == 'PATCH' and suffix.endswith('/desktop') and isinstance(payload, dict):
                fields = {'pinnedApplications': 'desktop.applications.pin', 'pinnedWidgets': 'desktop.widgets.pin',
                          'iconOrder': 'desktop.layout.reorder', 'widgetOrder': 'desktop.layout.reorder',
                          'hiddenSections': 'desktop.layout.customize'}
                if set(payload) - fields.keys():
                    await asyncio.to_thread(policy.require, principal, desktop, administer=True)
                permissions.extend(fields[name] for name in payload if name in fields)
            elif request.method == 'PATCH' and suffix == f'/{desktop}' and isinstance(payload, dict) and set(payload) <= {'title'}:
                permissions.append('desktop.rename')
            elif suffix.endswith('/toggle-install'):
                await asyncio.to_thread(policy.require, principal, desktop, administer=True)
            elif '/endpoints/' in suffix and suffix.endswith('/name'):
                permissions.append('endpoint.rename')
        for permission in set(permissions):
            await asyncio.to_thread(policy.require, principal, desktop, capability=permission)
        administrative = home_catalog or (request.method == 'GET' and suffix.endswith('/access')) or request.method == 'DELETE' or (request.method == 'PATCH' and suffix == f'/{desktop}') or (
            request.method == 'POST' and (not desktop or suffix.endswith((
                '/reload', '/reset', '/restore', '/backup', '/set-home', '/set-home-current',
                '/builder-materialize', '/refresh', '/access', '/access/revoke', '/access/permissions', '/access/invites', '/access/invites/revoke'))))
        if permissions:
            administrative = False
        if desktop or administrative:
            await asyncio.to_thread(policy.require, principal, desktop, administer=administrative)
            if desktop:
                await asyncio.to_thread(policy.require_attachment, principal, desktop)
        if request.method in {'POST', 'PATCH'} and request.headers.get('content-type', '').startswith('application/json'):
            if isinstance(payload, dict) and (payload.get('set_home') or payload.get('persist_home') or payload.get('recreate_room')):
                await asyncio.to_thread(policy.require, principal, desktop, administer=True)
    except CallerAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    with verified_caller(principal.actor, ScopeRef('webspace', desktop) if desktop else None):
        binding = _PRINCIPAL.set(principal)
        try:
            yield principal
        except CallerAccessDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        finally:
            _PRINCIPAL.reset(binding)


def current_desktop_principal() -> DesktopPrincipal:
    """HTTP dependencies establish context; direct internal calls are owner work."""
    from adaos.services.personalization_runtime import current_user_id
    from adaos.services.policy.caller import current_caller
    if _PRINCIPAL.get() is not None:
        return _PRINCIPAL.get()
    actor = current_caller() or SubjectRef('user', current_user_id())
    return DesktopPrincipal(actor, actor.id if actor.kind == 'session' else None)
