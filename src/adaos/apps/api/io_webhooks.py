from __future__ import annotations
import os
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Request

from adaos.integrations.telegram.webhook import validate_secret
from adaos.integrations.telegram.normalize import to_input_event
from adaos.services.chat_io import pairing as pairing_svc  # generic pairing

router = APIRouter()


@router.post("/io/tg/{bot_id}/webhook")
async def telegram_webhook(
    request: Request,
    bot_id: str,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
    expected = os.getenv("TG_SECRET_TOKEN")
    if not validate_secret(x_telegram_bot_api_secret_token, expected):
        raise HTTPException(status_code=401, detail="invalid secret")

    update = await request.json()
    evt = to_input_event(bot_id, update, hub_id=None)
    # TODO: publish evt to EventBus when bus impl lands
    return {"ok": True}


@router.post("/io/tg/pair/create")
async def tg_pair_create(hub: Optional[str] = None, ttl: Optional[str] = None):
    # TODO: parse ttl to seconds (default 600)
    res = await pairing_svc.issue_pair_code(bot_id="main-bot", hub_id=hub, ttl_sec=600)
    return {"ok": True, **res}


@router.post("/io/tg/pair/confirm")
async def tg_pair_confirm(code: str, user_id: Optional[str] = None, bot_id: Optional[str] = None):
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    # platform_user: минимально user_id/bot_id
    res = await pairing_svc.confirm_pair_code(code=code, platform_user={"user_id": user_id, "bot_id": bot_id})
    return res


@router.get("/io/tg/pair/status")
async def tg_pair_status(code: str):
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    res = await pairing_svc.pair_status(code=code)
    return {"ok": True, **res}


@router.post("/io/tg/pair/revoke")
async def tg_pair_revoke(code: str):
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    res = await pairing_svc.revoke_pair_code(code=code)
    return res
