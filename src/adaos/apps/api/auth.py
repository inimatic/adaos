import os
import asyncio

from fastapi import Header, HTTPException, Request, status

from adaos.services.agent_context import get_ctx


def _expected_token() -> str:
    env_token = str(os.getenv("ADAOS_TOKEN") or "").strip()
    if env_token:
        return env_token
    try:
        return str(get_ctx().config.token or "dev-local-token").strip() or "dev-local-token"
    except Exception:
        return "dev-local-token"


def resolve_presented_token(
    *,
    x_adaos_token: str | None = None,
    authorization: str | None = None,
    query_token: str | None = None,
) -> str | None:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_adaos_token:
        token = x_adaos_token
    elif query_token:
        token = query_token
    return token


def ensure_token(token: str | None) -> None:
    if token != _expected_token():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-AdaOS-Token",
        )


async def require_token(
    request: Request,
    x_adaos_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """
    Принимаем либо X-AdaOS-Token, либо Authorization: Bearer <token>.
    """
    desktop_credential = str(request.headers.get("X-AdaOS-Desktop-Credential") or request.query_params.get("desktop_token") or "")
    authorization_value = str(request.headers.get("Authorization") or "")
    if authorization_value.lower().startswith("bearer adaos-desktop-v1."):
        desktop_credential = authorization_value[7:].strip()
    if desktop_credential:
        from adaos.services.desktop_access import DesktopAccessService
        from adaos.services.policy.caller import CallerAccessDenied
        try:
            principal = DesktopAccessService().authenticate(desktop_credential)
        except (CallerAccessDenied, ValueError, TypeError) as exc:
            raise HTTPException(status_code=401, detail="desktop_credential_invalid") from exc
        # Desktop credentials are purpose-bound. A forwarded node token must
        # never silently elevate them on an unrelated administrative API.
        allowed_reads = {"/api/node/status", "/api/node/summary", "/api/ping"}
        if request.method not in {"GET", "HEAD"} or request.url.path not in allowed_reads:
            raise HTTPException(status_code=403, detail="desktop_credential_scope_denied")
        request.state.adaos_verified_caller = principal.actor
        request.state.adaos_desktop_principal = principal
        request.state.adaos_owner_node_credential = False
        return
    header_token = str(request.headers.get("X-AdaOS-Token") or request.headers.get("x-adaos-token") or "").strip() or None
    if request.headers.get("X-AdaOS-Route-Authenticated") == "1" and request.headers.get("X-AdaOS-Route-Principal") == "unverified":
        raise HTTPException(status_code=401, detail="verified_caller_required")
    auth_header = str(request.headers.get("Authorization") or request.headers.get("authorization") or "").strip() or None
    query_token = str(request.query_params.get("token") or "").strip() or None
    ensure_token(
        resolve_presented_token(
            x_adaos_token=header_token or x_adaos_token,
            authorization=auth_header or authorization,
            query_token=query_token,
        )
    )
    # The shared node credential authenticates the local owner, not a subject
    # named in a request body. Scoped session credentials need their own ingress.
    from adaos.domain.personalization_access import SubjectRef
    from adaos.services.personalization_runtime import current_user_id

    request.state.adaos_verified_caller = SubjectRef("user", current_user_id())
    # This exact ingress path validated the node-owner credential. Downstream
    # admission can trust an accompanying local device identifier without a
    # second personalization-store lookup. Scoped bearer credentials never set
    # this marker and retain full device/session validation.
    request.state.adaos_owner_node_credential = True
    request.state.adaos_root_routed = (
        str(request.headers.get("X-AdaOS-Route-Authenticated") or "").strip() == "1"
    )
    request.state.adaos_development_access = (
        str(request.headers.get("X-AdaOS-Development-Access") or "").strip() == "1"
    )


async def require_tool_caller(request: Request) -> None:
    """Accept an owner credential or a purpose-bound local session bearer."""
    authorization = request.headers.get("Authorization")
    token = resolve_presented_token(x_adaos_token=request.headers.get("X-AdaOS-Token"),
                                    authorization=authorization, query_token=request.query_params.get("token"))
    desktop_token = request.headers.get("X-AdaOS-Desktop-Credential") or (
        token if str(token or "").startswith("adaos-desktop-v1.") else None)
    if desktop_token:
        from adaos.apps.api.desktop_access import connection_principal
        from adaos.services.desktop_access import DesktopAccessService
        from adaos.domain.personalization_access import ScopeRef
        try:
            principal = await asyncio.to_thread(connection_principal, request)
            body = await request.json()
            context, arguments = body.get("context") or {}, body.get("arguments") or {}
            target = str(context.get("webspace_id") or "").strip()
            if not target or (arguments.get("webspace_id") and str(arguments["webspace_id"]) != target):
                raise PermissionError("desktop_tool_scope_required")
            await asyncio.to_thread(DesktopAccessService().require, principal, target)
        except (PermissionError, ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(status_code=403, detail="desktop_tool_access_denied") from exc
        request.state.adaos_verified_caller = principal.actor
        request.state.adaos_verified_caller_scope = ScopeRef("webspace", target)
        request.state.adaos_desktop_principal = principal
        request.state.adaos_owner_node_credential = False
        return
    if token == _expected_token():
        await require_token(request, x_adaos_token=None, authorization=None)
        return
    # Scoped secrets are not accepted in query strings or the node-token header.
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing tool credential")
    from starlette.concurrency import run_in_threadpool
    from adaos.services.personalization_access import PersonalizationAccessError
    from adaos.services.personalization_runtime import personalization_access_service
    from adaos.services.policy.caller import CallerAccessDenied
    from adaos.services.policy.session_credentials import authenticate_tool_credential

    def authenticate():
        return authenticate_tool_credential(personalization_access_service(), str(token or ""))
    try:
        actor, scope = await run_in_threadpool(authenticate)
    except (CallerAccessDenied, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired tool credential") from exc
    except PersonalizationAccessError as exc:
        raise HTTPException(status_code=503, detail="Caller access facts unavailable") from exc
    request.state.adaos_verified_caller = actor
    request.state.adaos_verified_caller_scope = scope


def require_owner_token(token: str) -> None:
    expected = os.getenv("ADAOS_ROOT_OWNER_TOKEN") or os.getenv("ROOT_TOKEN") or ""
    if not expected or token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid owner token")
