from __future__ import annotations
import os
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Request
import hashlib
import json
import time

from adaos.services.agent_context import get_ctx
from adaos.integrations.telegram.webhook import validate_secret
from adaos.integrations.telegram.normalize import to_input_event
from adaos.integrations.telegram.files import get_file_path, download_file, convert_opus_to_wav16k
from adaos.adapters.db import sqlite as sqlite_db
from adaos.services.chat_io import pairing as pairing_svc  # generic pairing
from adaos.services.chat_io.router import resolve_hub_id
from adaos.services.chat_io import telemetry as tm
from dataclasses import asdict
from uuid import uuid4
from datetime import datetime
from pydantic import BaseModel

router = APIRouter()


class TelegramPairCreateRequest(BaseModel):
    """Canonical body accepted from ``adaos dev telegram`` and other clients."""

    code: Optional[str] = None
    hub_id: Optional[str] = None
    hub: Optional[str] = None
    ttl: Optional[int] = None
    bot_id: Optional[str] = None
    bot: Optional[str] = None


@router.post("/io/tg/pair/create")
async def tg_pair_create(
    payload: Optional[TelegramPairCreateRequest] = None,
    hub: Optional[str] = None,
    ttl: Optional[int] = None,
    bot: Optional[str] = None,
):
    body = payload or TelegramPairCreateRequest()
    hub_id = str(body.hub_id or body.hub or hub or "").strip() or None
    bot_id = str(body.bot_id or body.bot or bot or "main-bot").strip() or "main-bot"
    ttl_sec = int(body.ttl or ttl or 600)
    res = await pairing_svc.issue_pair_code(
        bot_id=bot_id,
        hub_id=hub_id,
        ttl_sec=ttl_sec,
    )
    return {"ok": True, "hub_id": hub_id, "bot_id": bot_id, **res}


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

# Inbound bus mirror endpoint: allow backend to POST envelopes to hub local bus
@router.post("/io/bus/tg.input.{hub_id}")
async def tg_input_bus(hub_id: str, req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    # publish to local event bus so hub pipeline can consume
    try:
        from adaos.services.eventbus import emit as _emit
        from adaos.services.agent_context import get_ctx as _get_ctx
        _emit(_get_ctx().bus, f"tg.input.{hub_id}", body, source="io.http")
    except Exception:
        pass
    return {"ok": True}
