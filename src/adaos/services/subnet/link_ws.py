from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect

from adaos.services.agent_context import get_ctx
from adaos.services.subnet.link_manager import get_hub_link_manager

router = APIRouter()
_log = logging.getLogger("adaos.subnet.ws")


async def _handle_member_rpc_request(
    *,
    node_id: str,
    link: Any,
    message: dict[str, Any],
) -> None:
    request_id = str(message.get("id") or "").strip()
    if not request_id:
        return
    method = str(message.get("method") or "").strip()
    params = message.get("params")
    if method != "tools.call" or not isinstance(params, dict):
        await link.send_json(
            {"t": "rpc.res", "id": request_id, "ok": False, "error": "unknown_method"}
        )
        return
    tool = str(params.get("tool") or "").strip()
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        from adaos.services.subnet.member_rpc import run_member_tool

        result = await asyncio.to_thread(
            run_member_tool,
            node_id=node_id,
            tool=tool,
            arguments=arguments,
            timeout=params.get("timeout"),
        )
        await link.send_json(
            {"t": "rpc.res", "id": request_id, "ok": True, "result": result}
        )
    except Exception as exc:
        _log.warning(
            "member RPC failed node_id=%s tool=%s error=%s",
            node_id,
            tool,
            exc,
        )
        await link.send_json(
            {
                "t": "rpc.res",
                "id": request_id,
                "ok": False,
                "error": f"{type(exc).__name__}:{str(exc)[:240]}",
            }
        )


def _extract_token(websocket: WebSocket) -> str | None:
    try:
        auth = websocket.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            return auth[7:].strip()
    except Exception:
        pass
    try:
        tok = websocket.headers.get("x-adaos-token")
        if tok:
            return tok.strip()
    except Exception:
        pass
    try:
        return (websocket.query_params.get("token") or "").strip() or None
    except Exception:
        return None


async def _accept_websocket(websocket: WebSocket) -> bool:
    try:
        await websocket.accept()
        return True
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        text = str(exc or "").strip().lower()
        if ("websocket.accept" in text and "websocket.close" in text) or "close message has been sent" in text:
            _log.info("subnet websocket accept skipped because handshake was already closed")
            return False
        raise


def _is_websocket_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, WebSocketDisconnect):
        return True
    # Starlette/FastAPI have moved this exception across import paths between
    # versions; normal close frames must not bubble as ASGI errors just because
    # the concrete class came from a sibling module.
    if exc.__class__.__name__ == "WebSocketDisconnect":
        return True
    if isinstance(exc, RuntimeError):
        text = str(exc or "").strip().lower()
        return (
            "websocket is not connected" in text
            or ("cannot call" in text and "receive" in text and "disconnect" in text)
            or "close message has been sent" in text
        )
    return False


@router.websocket("/ws/subnet")
async def subnet_ws(websocket: WebSocket) -> None:
    """
    Member -> Hub persistent link (P2P in-subnet).

    Auth: X-AdaOS-Token header (or Authorization: Bearer).
    First message must be `{"t":"hello", ...}`.
    """
    conf = get_ctx().config
    if conf.role != "hub":
        await websocket.close(code=1008)
        return

    # Accept first; we can still close with 1008 on auth failure.
    if not await _accept_websocket(websocket):
        return

    token = _extract_token(websocket)
    expected = conf.token or "dev-local-token"
    if token != expected:
        await websocket.send_json({"t": "hello.ack", "ok": False, "error": "invalid_token"})
        await websocket.close(code=1008)
        return

    mgr = get_hub_link_manager()
    node_id: str | None = None
    hostname: Any = None
    roles: Any = []
    node_names: Any = []
    link = None
    registered = False
    member_rpc_tasks: set[asyncio.Task[Any]] = set()
    try:
        try:
            raw = await websocket.receive_json()
        except Exception as exc:
            if _is_websocket_disconnect(exc):
                return
            raise
        if not isinstance(raw, dict) or raw.get("t") != "hello":
            await websocket.send_json({"t": "hello.ack", "ok": False, "error": "hello_required"})
            await websocket.close(code=1002)
            return
        node_id = str(raw.get("node_id") or "").strip()
        subnet_id = str(raw.get("subnet_id") or "").strip()
        if not node_id or not subnet_id:
            await websocket.send_json({"t": "hello.ack", "ok": False, "error": "node_id_and_subnet_id_required"})
            await websocket.close(code=1002)
            return
        if subnet_id != conf.subnet_id:
            await websocket.send_json({"t": "hello.ack", "ok": False, "error": "subnet_mismatch"})
            await websocket.close(code=1008)
            return
        hostname = raw.get("hostname")
        roles = raw.get("roles") or []
        node_names = raw.get("node_names") or []
        try:
            from adaos.services.access_links import authorize_link

            allowed, reason = await asyncio.to_thread(authorize_link, "member", node_id)
            if not allowed:
                await websocket.send_json({"t": "hello.ack", "ok": False, "error": f"device_{reason or 'denied'}"})
                await websocket.close(code=1008)
                return
        except Exception:
            pass
        await websocket.send_json(
            {
                "t": "hello.ack",
                "ok": True,
                "hub_node_id": conf.node_id,
                "subnet_id": conf.subnet_id,
                "server_time": time.time(),
            }
        )
        try:
            link = await mgr.register(
                node_id,
                websocket,
                hostname=str(hostname) if hostname else None,
                roles=list(roles) if isinstance(roles, list) else [],
                node_names=list(node_names) if isinstance(node_names, list) else [],
            )
            registered = True
        except Exception:
            _log.warning("subnet member registration failed node_id=%s", node_id, exc_info=True)
            try:
                await websocket.close(code=1011)
            except Exception:
                pass
            return
        try:
            from adaos.services.access_links import touch_member_link

            await asyncio.to_thread(
                touch_member_link,
                node_id,
                hostname=str(hostname) if hostname else None,
                node_names=list(node_names) if isinstance(node_names, list) else [],
                online=True,
                connection_state="connected",
            )
        except Exception:
            pass
        try:
            await mgr.refresh_member_after_connect(node_id, reason="member_link_connected")
        except Exception:
            _log.debug("failed to request member state refresh after connect node_id=%s", node_id, exc_info=True)

        while True:
            try:
                msg: Any = await websocket.receive_json()
            except json.JSONDecodeError:
                _log.debug("ignored malformed subnet websocket JSON node_id=%s", node_id)
                continue
            except Exception as exc:
                if _is_websocket_disconnect(exc):
                    break
                # A receive failure is terminal unless it is an explicitly
                # recoverable malformed JSON frame. Retrying an already closed
                # Starlette WebSocket raises synchronously and otherwise turns
                # this loop into a process-wide CPU spin.
                _log.warning(
                    "subnet websocket receive failed; closing handler node_id=%s error=%s: %s",
                    node_id,
                    type(exc).__name__,
                    exc,
                )
                break
            if not isinstance(msg, dict):
                continue

            t = msg.get("t")
            try:
                await mgr.note_member_activity(node_id, message_type=str(t or ""))
            except Exception:
                pass
            if t == "ping":
                try:
                    await link.send_json({"t": "pong", "ts": time.time()})
                except Exception:
                    pass
                continue

            if t == "rpc.res":
                try:
                    await mgr.handle_rpc_response(node_id, msg)
                except Exception:
                    pass
                continue

            if t == "rpc.req":
                task = asyncio.create_task(
                    _handle_member_rpc_request(
                        node_id=node_id,
                        link=link,
                        message=msg,
                    )
                )
                member_rpc_tasks.add(task)
                task.add_done_callback(member_rpc_tasks.discard)
                continue

            if t == "bus.emit":
                ev = msg.get("event")
                if isinstance(ev, dict):
                    await mgr.ingest_member_bus_event(node_id=node_id, event=ev)
                continue

            if t == "node.meta":
                node_names = msg.get("node_names") or []
                if isinstance(node_names, list):
                    await mgr.update_member_metadata(node_id, node_names=list(node_names))
                continue

            if t == "node.status":
                status = msg.get("status")
                if isinstance(status, dict):
                    await mgr.update_member_status(node_id, status=status)
                continue

            if t == "node.catalog":
                snapshot = msg.get("snapshot")
                catalog = msg.get("catalog")
                if isinstance(snapshot, dict):
                    await mgr.update_member_status(node_id, status=snapshot)
                elif isinstance(catalog, dict):
                    await mgr.update_member_status(node_id, status={"desktop_catalog": catalog, "captured_at": time.time()})
                continue

            if t == "node.snapshot":
                snapshot = msg.get("snapshot")
                if isinstance(snapshot, dict):
                    await mgr.update_member_snapshot(node_id, snapshot=snapshot)
                continue

            if t == "node.snapshot.heartbeat":
                snapshot = msg.get("snapshot")
                if isinstance(snapshot, dict):
                    await mgr.update_member_snapshot_heartbeat(node_id, snapshot=snapshot)
                continue

            if t == "core.update.result":
                result = msg.get("result")
                if isinstance(result, dict):
                    await mgr.update_member_control_result(node_id, result=result)
                continue

            if t == "yjs.update":
                try:
                    webspace_id = str(msg.get("webspace_id") or "default")
                    b64 = msg.get("update_b64") or ""
                    if not isinstance(b64, str) or not b64:
                        continue
                    update = base64.b64decode(b64.encode("ascii"), validate=False)
                    await mgr.ingest_member_yjs_update(node_id=node_id, webspace_id=webspace_id, update=update)
                except Exception:
                    continue
                continue

            if t == "yjs.node_state":
                try:
                    webspace_id = str(msg.get("webspace_id") or "default")
                    state = msg.get("state")
                    if isinstance(state, dict):
                        try:
                            _log.info(
                                "received member yjs.node_state node_id=%s webspace=%s bytes=%d",
                                node_id,
                                webspace_id,
                                len(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                            )
                        except Exception:
                            pass
                        await mgr.ingest_member_node_state(
                            node_id=node_id,
                            webspace_id=webspace_id,
                            state=state,
                        )
                except Exception:
                    _log.warning("failed to handle member yjs.node_state node_id=%s", node_id, exc_info=True)
                    continue
                continue

            _ = link
    finally:
        for task in list(member_rpc_tasks):
            task.cancel()
        if member_rpc_tasks:
            await asyncio.gather(*member_rpc_tasks, return_exceptions=True)
        if node_id and registered:
            try:
                from adaos.services.access_links import touch_member_link

                await asyncio.to_thread(
                    touch_member_link,
                    node_id,
                    hostname=str(hostname) if hostname else None,
                    node_names=list(node_names) if isinstance(node_names, list) else [],
                    online=False,
                    connection_state="closed",
                )
            except Exception:
                pass
            try:
                await mgr.unregister(node_id, expected_link=link)
            except Exception:
                pass
