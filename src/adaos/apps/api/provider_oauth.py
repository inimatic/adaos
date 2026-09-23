"""OAuth callbacks for Core-owned provider adapters."""

from __future__ import annotations

import asyncio
from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.providers.google_gmail import (
    GoogleGmailProvider,
    GoogleGmailProviderError,
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


__all__ = ["router"]
