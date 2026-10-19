"""Register the native companion against the local node's physical device."""
from __future__ import annotations

import platform
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.agent_context import get_ctx
from adaos.services import access_links

router = APIRouter(dependencies=[Depends(require_token)])


class CompanionRegistration(BaseModel):
    endpoint_id: str = Field(pattern=r"^desktop-[a-zA-Z0-9-]{1,100}$")
    expected_node_id: str = Field(min_length=1, max_length=160)
    version: str = Field(default="", max_length=80)
    os_name: str = Field(default="", max_length=40)


@router.post("")
async def register(body: CompanionRegistration):
    conf = get_ctx().config
    if str(conf.node_id) != body.expected_node_id:
        raise HTTPException(status_code=409, detail="local node identity changed")
    parent = f"hub:{conf.subnet_id}" if conf.role == "hub" else f"member:{conf.node_id}"
    existing = access_links.get_link("browser", body.endpoint_id) or {}
    access_links.upsert_link("browser", body.endpoint_id, {
        "client_build_id": "adaos-desktop", "client_build_version": body.version,
        "endpoint_kind": "native_client", "endpoint_id": body.endpoint_id,
        "device_display_name": existing.get("device_display_name") or platform.node(), "hostname": platform.node(),
        "parent_device_ref": parent, "endpoint_display_name": existing.get("endpoint_display_name") or "AdaOS Desktop",
        "display_name": existing.get("display_name") or "AdaOS Desktop", "browser_family": "AdaOS Desktop",
        "os_name": body.os_name,
    })
    return {"ok": True, "endpoint_id": body.endpoint_id, "node_id": str(conf.node_id), "parent_device_ref": parent}
