"""OAuth callbacks for Core-owned provider adapters."""

from __future__ import annotations

import asyncio
from html import escape

from fastapi import APIRouter, Body, Depends
from fastapi.responses import HTMLResponse

from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.providers.google_gmail import (
    GoogleGmailProvider,
    GoogleGmailProviderError,
)
from adaos.services.integrations.ingress import (
    GOOGLE_OAUTH_INGRESS_PROFILE_REF,
    IntegrationIngressError,
    broker_from_context,
)


router = APIRouter()


def _page(title: str, message: str, *, ok: bool) -> HTMLResponse:
    color = "#166534" if ok else "#991b1b"
    body = (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{escape(title)}</title>"
        "<main style='font:16px system-ui;max-width:38rem;margin:4rem auto;padding:1.5rem'>"
        f"<h1 style='color:{color}'>{escape(title)}</h1>"
        f"<p>{escape(message)}</p>"
        "<p>You may close this window and return to AdaOS.</p>"
        "</main>"
    )
    return HTMLResponse(
        body,
        status_code=200 if ok else 400,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        },
    )


@router.get("/providers/google/gmail/oauth/callback")
async def google_gmail_oauth_callback(
    state: str = "",
    code: str = "",
    error: str = "",
    ctx: AgentContext = Depends(get_ctx),
) -> HTMLResponse:
    try:
        provider = GoogleGmailProvider.from_context(ctx)
        result = await asyncio.to_thread(
            provider.complete_authorization,
            state=state,
            code=code,
            error=error,
        )
    except GoogleGmailProviderError as exc:
        return _page(
            "Gmail connection was not completed",
            exc.code.replace("_", " "),
            ok=False,
        )
    account = str(result.get("email_address") or result.get("account_id") or "Gmail")
    return _page("Gmail connected", f"Connected account: {account}", ok=True)


@router.post("/integrations/ingress/oauth/deliver")
async def deliver_oauth_ingress(
    envelope: dict = Body(...),
    ctx: AgentContext = Depends(get_ctx),
) -> HTMLResponse:
    """Accept one encrypted Root envelope at the selected Core authority."""

    try:
        broker = broker_from_context(ctx)
        payload = await asyncio.to_thread(broker.decrypt_routed_envelope, envelope)
        if str(payload.get("profile_ref") or "") != GOOGLE_OAUTH_INGRESS_PROFILE_REF:
            raise IntegrationIngressError("oauth_profile_mismatch")
        provider = GoogleGmailProvider.from_context(ctx)
        result = await asyncio.to_thread(
            provider.complete_authorization,
            state=str(payload.get("state") or ""),
            code=str(payload.get("code") or ""),
            error=str(payload.get("error") or ""),
            expected_attempt_ref=str(payload.get("attempt_ref") or ""),
        )
    except (IntegrationIngressError, GoogleGmailProviderError) as exc:
        code = getattr(exc, "code", "oauth_ingress_rejected")
        return _page(
            "Connection was not completed",
            str(code).replace("_", " "),
            ok=False,
        )
    account = str(result.get("email_address") or result.get("account_id") or "provider")
    return _page("Account connected", f"Connected account: {account}", ok=True)


__all__ = ["router"]
