"""OAuth callbacks for Core-owned provider adapters."""

from __future__ import annotations

import asyncio
from html import escape
import logging
import threading

from fastapi import APIRouter, Body, Depends, Response
from fastapi.responses import HTMLResponse, JSONResponse

from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.providers.google_gmail import (
    GoogleGmailProvider,
    GoogleGmailProviderError,
)
from adaos.services.integrations.ingress import (
    GOOGLE_OAUTH_INGRESS_PROFILE_REF,
    LOCAL_DEVELOPMENT_ENVIRONMENT_REF,
    PUBLIC_CONNECTED_ENVIRONMENT_REF,
    IntegrationIngressError,
    broker_from_context,
)
from adaos.services.integrations.oauth_delivery import (
    OAuthIngressDeliveryError,
    OAuthIngressDeliveryStore,
)


router = APIRouter()
_LOG = logging.getLogger("adaos.integration.oauth_delivery")
_DELIVERY_WORKERS_LOCK = threading.RLock()
_DELIVERY_WORKERS: dict[str, threading.Thread] = {}


def _delivery_store(ctx: AgentContext) -> OAuthIngressDeliveryStore:
    return OAuthIngressDeliveryStore(getattr(ctx, "credential_vault", None))


def _complete_oauth_delivery(ctx: AgentContext, delivery_key: str) -> None:
    try:
        store = _delivery_store(ctx)
        record = store.claim(delivery_key)
        if record is None:
            return
        envelope = record.get("envelope")
        if not isinstance(envelope, dict):
            store.fail(delivery_key, "oauth_delivery_envelope_missing")
            return
        broker = broker_from_context(
            ctx,
            environment_profile_ref=PUBLIC_CONNECTED_ENVIRONMENT_REF,
        )
        payload = broker.decrypt_routed_envelope(envelope)
        if str(payload.get("profile_ref") or "") != GOOGLE_OAUTH_INGRESS_PROFILE_REF:
            raise IntegrationIngressError("oauth_profile_mismatch")
        provider = GoogleGmailProvider.from_context(ctx, ingress_broker=broker)
        result = provider.complete_authorization(
            state=str(payload.get("state") or ""),
            code=str(payload.get("code") or ""),
            error=str(payload.get("error") or ""),
            expected_attempt_ref=str(payload.get("attempt_ref") or ""),
        )
        store.complete(delivery_key, result)
        _LOG.info(
            "routed OAuth delivery completed attempt_ref=%s",
            str(record.get("attempt_ref") or ""),
        )
    except (IntegrationIngressError, GoogleGmailProviderError, OAuthIngressDeliveryError) as exc:
        code = str(getattr(exc, "code", "") or str(exc) or "oauth_delivery_failed")
        try:
            _delivery_store(ctx).fail(delivery_key, code)
        except Exception:
            _LOG.warning("failed to persist routed OAuth terminal failure", exc_info=True)
        _LOG.warning(
            "routed OAuth delivery failed code=%s",
            code[:160],
        )
    except Exception as exc:
        try:
            _delivery_store(ctx).fail(delivery_key, "oauth_delivery_failed")
        except Exception:
            _LOG.warning("failed to persist routed OAuth terminal failure", exc_info=True)
        _LOG.warning(
            "routed OAuth delivery failed exception_type=%s",
            type(exc).__name__,
        )
    finally:
        with _DELIVERY_WORKERS_LOCK:
            current = _DELIVERY_WORKERS.get(delivery_key)
            if current is threading.current_thread():
                _DELIVERY_WORKERS.pop(delivery_key, None)


def _schedule_oauth_delivery(ctx: AgentContext, delivery_key: str) -> None:
    with _DELIVERY_WORKERS_LOCK:
        current = _DELIVERY_WORKERS.get(delivery_key)
        if current is not None and current.is_alive():
            return
        worker = threading.Thread(
            target=_complete_oauth_delivery,
            args=(ctx, delivery_key),
            name="adaos-oauth-delivery-" + delivery_key.rsplit(":", 1)[-1][:12],
            daemon=True,
        )
        _DELIVERY_WORKERS[delivery_key] = worker
        worker.start()


def schedule_pending_oauth_ingress_recovery(ctx: AgentContext) -> None:
    """Recover accepted deliveries without extending API startup latency."""

    def recover() -> None:
        try:
            for delivery_key in _delivery_store(ctx).pending_keys():
                _schedule_oauth_delivery(ctx, delivery_key)
        except Exception as exc:
            _LOG.warning(
                "routed OAuth recovery scan failed exception_type=%s",
                type(exc).__name__,
            )

    threading.Thread(
        target=recover,
        name="adaos-oauth-delivery-recovery",
        daemon=True,
    ).start()


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
        broker = broker_from_context(
            ctx,
            environment_profile_ref=LOCAL_DEVELOPMENT_ENVIRONMENT_REF,
        )
        provider = GoogleGmailProvider.from_context(ctx, ingress_broker=broker)
        result = await asyncio.to_thread(
            provider.complete_authorization,
            state=state,
            code=code,
            error=error,
        )
    except (IntegrationIngressError, GoogleGmailProviderError) as exc:
        return _page(
            "Gmail connection was not completed",
            exc.code.replace("_", " "),
            ok=False,
        )
    account = str(result.get("email_address") or result.get("account_id") or "Gmail")
    return _page("Gmail connected", f"Connected account: {account}", ok=True)


@router.post("/integrations/ingress/oauth/deliver", response_model=None)
async def deliver_oauth_ingress(
    envelope: dict = Body(...),
    ctx: AgentContext = Depends(get_ctx),
) -> Response:
    """Accept one encrypted Root envelope at the selected Core authority."""

    try:
        broker = broker_from_context(
            ctx,
            environment_profile_ref=PUBLIC_CONNECTED_ENVIRONMENT_REF,
        )
        payload = await asyncio.to_thread(broker.decrypt_routed_envelope, envelope)
        if str(payload.get("profile_ref") or "") != GOOGLE_OAUTH_INGRESS_PROFILE_REF:
            raise IntegrationIngressError("oauth_profile_mismatch")
        record = await asyncio.to_thread(_delivery_store(ctx).accept, envelope)
    except (IntegrationIngressError, GoogleGmailProviderError, OAuthIngressDeliveryError) as exc:
        code = getattr(exc, "code", "oauth_ingress_rejected")
        return _page(
            "Connection was not completed",
            str(code).replace("_", " "),
            ok=False,
        )
    _schedule_oauth_delivery(ctx, str(record["delivery_key"]))
    return JSONResponse(
        dict(record["acknowledgement"]),
        status_code=200,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router", "schedule_pending_oauth_ingress_recovery"]
