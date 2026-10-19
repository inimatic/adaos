"""Prepare a local node for removal before taking away its subnet access.

The journal is deliberately separate from the Desktop process. Completed hooks
and the Hub receipt survive a lost HTTP reply, runtime restart or file-lock
failure during the subsequent OS uninstall. Hooks must themselves be idempotent
for the operation ID (a crash can happen between a hook and its journal write).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from typing import Any, Callable

from adaos.domain import Event
from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.runtime_lifecycle import request_drain
from adaos.services.runtime_paths import current_state_dir

_LOCK = asyncio.Lock()
_HOOKS: dict[str, Callable[[dict[str, Any]], Any]] = {}


def register_departure_hook(name: str, handler: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
    """Register an awaited preparation step, e.g. a future subnet backup."""
    if not name or name in _HOOKS:
        raise ValueError("departure hook name must be unique")
    _HOOKS[name] = handler
    return lambda: _HOOKS.pop(name, None)


def _read_journal() -> dict[str, Any]:
    path = current_state_dir() / "node_departure.json"
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid node departure journal")
    return value


def _save(value: dict[str, Any]) -> None:
    atomic_write_json(current_state_dir() / "node_departure.json", value)


def _emit(topic: str, operation: dict[str, Any]) -> None:
    get_ctx().bus.publish(Event(type=topic, payload=dict(operation), source="node.departure", ts=time.time()))


async def _notify_hub(operation: dict[str, Any]) -> dict[str, Any]:
    from adaos.services.subnet.link_client import get_member_link_client

    # The existing member link binds the departure to its authenticated peer;
    # members cannot supply a different target node ID in an HTTP command.
    receipt = await get_member_link_client().request_departure(operation["operation_id"])
    if not isinstance(receipt, dict) or any(
        receipt.get(key) != operation[key] for key in ("node_id", "subnet_id")
    ) or receipt.get("departure_operation_id") != operation["operation_id"] or receipt.get("lifecycle_state") != "deleted":
        raise RuntimeError("Hub did not confirm this node's removal")
    return receipt


async def finalize_member_departure(node_id: str, operation_id: str) -> dict[str, Any]:
    from adaos.services.access_links import delete_member_link

    conf = get_ctx().config
    if conf.role != "hub" or str(conf.node_id) == node_id:
        raise ValueError("only a member can depart through this link")
    receipt = await asyncio.to_thread(delete_member_link, node_id, operation_id=operation_id, subnet_id=str(conf.subnet_id))
    await asyncio.to_thread(report_deleted_node_to_root, receipt, role="member")
    return receipt


def report_deleted_node_to_root(operation: dict[str, Any], *, role: str) -> dict[str, Any]:
    from adaos.services.root.control_lifecycle_sync import _root_client

    client = _root_client(get_ctx().config)
    if client is None:
        return {"status": "not_registered_with_root"}
    result = client.node_departure(payload={
        "node_id": operation["node_id"], "subnet_id": operation["subnet_id"],
        "operation_id": operation.get("operation_id") or operation.get("departure_operation_id"),
        "reason": "delete", "node_role": role,
    })
    if not result.get("ok") or not isinstance(result.get("receipt"), dict):
        raise RuntimeError("Root did not acknowledge node deletion")
    return {"status": "confirmed", "receipt": result["receipt"]}


def restore_departure_drain() -> None:
    operation = _read_journal()
    if operation and operation.get("node_id") == str(get_ctx().config.node_id):
        request_drain(reason="node.delete")


async def prepare_deletion(*, expected_node_id: str, allow_standalone: bool = False) -> dict[str, Any]:
    async with _LOCK:
        conf = get_ctx().config
        node_id, subnet_id = str(conf.node_id), str(conf.subnet_id)
        if node_id != expected_node_id:
            raise ValueError("local node identity changed")
        if conf.role not in {"hub", "member"}:
            raise ValueError("unsupported node role")
        if conf.role == "hub" and not allow_standalone:
            raise ValueError("standalone Hub removal requires explicit confirmation")
        operation = _read_journal()
        if operation and (operation.get("node_id") != node_id or operation.get("subnet_id") != subnet_id):
            raise ValueError("departure journal belongs to a different node or subnet")
        if not operation:
            operation = {
                "schema": "adaos.node_departure.v1", "operation_id": str(uuid.uuid4()),
                "node_id": node_id, "subnet_id": subnet_id, "reason": "delete",
                "role": conf.role, "phase": "preparing", "started_at": time.time(), "hooks_done": [],
            }
            _save(operation)
        request_drain(reason="node.delete")
        if operation.get("phase") == "ready_to_remove":
            return dict(operation)
        try:
            operation["phase"] = "preparing"
            operation.pop("error", None)
            _save(operation)
            _emit("node.departure.preparing", operation)
            # Events are observational. Required data preservation steps use
            # explicit awaited hooks so a handler failure cannot imply success.
            waiter = getattr(get_ctx().bus, "wait_for_idle", None)
            if waiter and not await waiter(timeout=30):
                raise TimeoutError("node work did not drain")
            for name, handler in list(_HOOKS.items()):
                if name in operation["hooks_done"]:
                    continue
                if inspect.iscoroutinefunction(handler):
                    result = await asyncio.wait_for(handler(dict(operation)), timeout=60)
                else:
                    result = await asyncio.wait_for(asyncio.to_thread(handler, dict(operation)), timeout=60)
                if result is False or (isinstance(result, dict) and result.get("ok") is False):
                    raise RuntimeError("departure preparation hook failed")
                operation["hooks_done"].append(name)
                _save(operation)
            operation["phase"] = "prepared"
            _save(operation)
            if conf.role == "member":
                operation["receipt"] = await _notify_hub(operation)
            else:
                # Removing the local HUB must never silently delete the Root
                # subnet account or pretend to be a member detach.
                operation["scope"] = "local_standalone_hub"
                operation["root"] = await asyncio.to_thread(report_deleted_node_to_root, operation, role="hub")
            operation.update(phase="ready_to_remove", prepared_at=time.time())
            _save(operation)
            _emit("node.departure.prepared", operation)
            return dict(operation)
        except Exception as exc:
            operation.update(phase="failed", error=type(exc).__name__)
            _save(operation)
            _emit("node.departure.failed", operation)
            raise
