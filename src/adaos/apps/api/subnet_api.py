# src\adaos\apps\api\subnet_api.py
from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Any, Dict

from adaos.apps.api.auth import require_token
from adaos.services.agent_context import get_ctx
from adaos.services.subnet_kv_file_http import get_subnet_kv
from adaos.services.subnet_registry_mem import LEASE_SECONDS_DEFAULT, DOWN_GRACE_SECONDS
from adaos.services.registry.subnet_directory import get_directory
from adaos.services.subnet_registry_mem import get_subnet_registry
from adaos.services.subnet_heartbeat_runtime import get_heartbeat_persistence_runtime

from adaos.sdk.data import bus

router = APIRouter(tags=["subnet"])


def _register_directory_node(node_info: Dict[str, Any]) -> bool:
    """Persist a registration and report whether it is an offline -> online edge."""
    directory = get_directory()
    node_id = str(node_info.get("node_id") or "").strip()
    known = bool(node_id and directory.repo.get_node(node_id))
    was_online = bool(node_id and directory.is_online(node_id))
    directory.on_register(node_info)
    return not known or not was_online


def _heartbeat_directory_node(
    node_id: str,
    *,
    capacity: Dict[str, Any] | None,
    node_state: str | None,
    base_url: str | None,
) -> bool:
    directory = get_directory()
    if not directory.accept_heartbeat(node_id):
        return False
    get_heartbeat_persistence_runtime().submit(
        directory,
        node_id=node_id,
        capacity=capacity,
        node_state=node_state,
        base_url=base_url,
    )
    return True


# ---------- Models ----------
class RegisterRequest(BaseModel):
    node_id: str
    subnet_id: str
    hostname: str | None = None
    roles: list[str] | None = None
    base_url: str | None = None
    node_state: str | None = None
    capacity: Dict[str, Any] | None = None


class RegisterResponse(BaseModel):
    ok: bool
    lease_seconds: int = LEASE_SECONDS_DEFAULT


class HeartbeatRequest(BaseModel):
    node_id: str
    node_state: str | None = None
    capacity: Dict[str, Any] | None = None
    base_url: str | None = None


class HeartbeatResponse(BaseModel):
    ok: bool
    lease_seconds: int = LEASE_SECONDS_DEFAULT


class CtxValue(BaseModel):
    value: Any


class DeregisterRequest(BaseModel):
    node_id: str


# ---------- Endpoints (hub-only, mounted under /api) ----------


@router.post("/subnet/register", response_model=RegisterResponse, dependencies=[Depends(require_token)])
async def register(body: RegisterRequest):
    """
    Регистрация ноды на hub.
    """
    conf = get_ctx().config
    if conf.role != "hub":
        raise HTTPException(status_code=403, detail="only hub node accepts registrations")

    if body.subnet_id != conf.subnet_id:
        raise HTTPException(status_code=400, detail="subnet mismatch")

    # Добавляем/обновляем запись в persistent directory
    became_online = await asyncio.to_thread(
        _register_directory_node,
        {
            "node_id": body.node_id,
            "subnet_id": body.subnet_id,
            "hostname": body.hostname,
            "roles": body.roles or [],
            "base_url": body.base_url,
            "node_state": body.node_state,
            "capacity": body.capacity or {},
        },
    )
    # Сигнализируем о появлении ноды (node.up)
    if became_online:
        try:
            await bus.emit("net.subnet.node.up", {"node_id": body.node_id}, source="subnet_api", actor="system")
        except Exception:
            pass

    return RegisterResponse(ok=True, lease_seconds=LEASE_SECONDS_DEFAULT)


@router.post("/subnet/heartbeat", response_model=HeartbeatResponse, dependencies=[Depends(require_token)])
async def heartbeat(body: HeartbeatRequest):
    """
    Heartbeat от ноды к hub. Обновляет last_seen и (если надо) возвращает статус в 'up'.
    """
    conf = get_ctx().config
    if conf.role != "hub":
        raise HTTPException(status_code=403, detail="only hub node accepts heartbeats")

    known = _heartbeat_directory_node(
        body.node_id,
        capacity=body.capacity or None,
        node_state=body.node_state,
        base_url=body.base_url,
    )
    # Если нода неизвестна — 404 (сохраняем поведение)
    if not known:
        raise HTTPException(status_code=404, detail="node not registered")
    return HeartbeatResponse(ok=True, lease_seconds=LEASE_SECONDS_DEFAULT)


@router.post("/subnet/deregister", dependencies=[Depends(require_token)])
async def deregister(body: DeregisterRequest):
    """Корректная дерегистрация ноды на hub (когда нода уходит из подсети)."""
    conf = get_ctx().config
    if conf.role != "hub":
        raise HTTPException(status_code=403, detail="only hub node accepts deregistration")
    existed = get_subnet_registry().unregister_node(body.node_id)
    if existed:
        await bus.emit("net.subnet.node.down", {"node_id": body.node_id}, source="subnet_api", actor="system")
    return {"ok": True, "existed": bool(existed)}


@router.get("/subnet/context/{key}", dependencies=[Depends(require_token)])
async def ctx_get(key: str):
    """
    Получение значения глобального контекста подсети (hub-only).
    """
    conf = get_ctx().config
    if conf.role != "hub":
        raise HTTPException(status_code=403, detail="only hub node serves context")
    return {"ok": True, "value": CTX.hub_get(key)}


@router.put("/subnet/context/{key}", dependencies=[Depends(require_token)])
async def ctx_set(key: str, body: CtxValue):
    """
    Запись значения в глобальный контекст подсети (hub-only).
    """
    conf = get_ctx().config
    if conf.role != "hub":
        raise HTTPException(status_code=403, detail="only hub node serves context")
    CTX.hub_set(key, body.value)
    return {"ok": True}


@router.get("/subnet/nodes", dependencies=[Depends(require_token)])
async def nodes_list():
    """
    Список нод подсети с их статусами (hub-only).
    """
    conf = get_ctx().config
    if conf.role != "hub":
        raise HTTPException(status_code=403, detail="only hub node lists nodes")
    items = await asyncio.to_thread(lambda: get_directory().list_known_nodes())
    return {"ok": True, "nodes": items}


@router.get("/subnet/nodes/{node_id}", dependencies=[Depends(require_token)])
async def node_get(node_id: str):
    """
    Детали по конкретной ноде (hub-only).
    """
    conf = get_ctx().config
    if conf.role != "hub":
        raise HTTPException(status_code=403, detail="only hub node has node details")
    info = await asyncio.to_thread(lambda: get_directory().get_node(node_id))
    if not isinstance(info, dict):
        raise HTTPException(status_code=404, detail="node not found")
    return {"ok": True, "node": dict(info)}
