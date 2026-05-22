# src\adaos\api\tool_bridge.py
import logging
import os
import copy
import threading
import time
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

import anyio
import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.observe import attach_http_trace_headers
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.eventbus import emit
from adaos.services.runtime_lifecycle import is_accepting_new_work
from adaos.services.skill.manager import SkillManager
from adaos.adapters.db import SqliteSkillRegistry
from adaos.services.registry.subnet_directory import get_directory
from adaos.services.subnet.link_manager import get_hub_link_manager
from adaos.services.yjs.webspace import default_webspace_id


router = APIRouter()
_log = logging.getLogger("adaos.api.tool_bridge")
_HUB_LOCAL_TOOL_PREFIXES: tuple[str, ...] = (
    "browsers_skill:",
    "infrastate_skill:",
)
_SNAPSHOT_UNAVAILABLE_TTL_S = max(0.0, float(os.getenv("ADAOS_TOOL_BRIDGE_SNAPSHOT_UNAVAILABLE_TTL_S") or "20"))
_SNAPSHOT_UNAVAILABLE_CACHE_LOCK = threading.RLock()
_SNAPSHOT_UNAVAILABLE_CACHE: dict[str, tuple[float, Dict[str, Any]]] = {}


def _readonly_snapshot_rpc_timeout_s(requested_timeout: float | None) -> float | None:
    if requested_timeout is not None:
        return requested_timeout
    raw = str(os.getenv("ADAOS_TOOL_BRIDGE_READONLY_SNAPSHOT_RPC_TIMEOUT_S") or "8").strip()
    try:
        value = float(raw)
    except Exception:
        value = 8.0
    if value <= 0.0:
        return None
    return max(1.0, min(value, 30.0))


def _debug_autosync_enabled() -> bool:
    level = (os.getenv("ADAOS_LOG_LEVEL") or "").strip().upper()
    return not level or level == "DEBUG"


def _repo_workspace_skill_dir(ctx: AgentContext, skill_name: str) -> Path | None:
    try:
        repo_root_attr = getattr(ctx.paths, "repo_root", None)
        repo_root = repo_root_attr() if callable(repo_root_attr) else repo_root_attr
        if not repo_root:
            return None
        candidate = Path(repo_root).expanduser().resolve() / ".adaos" / "workspace" / "skills" / skill_name
        if candidate.exists():
            return candidate
    except Exception:
        return None
    return None


def _workspace_skill_source_exists(ctx: AgentContext, skill_name: str) -> bool:
    try:
        workspace_root = ctx.paths.skills_workspace_dir()
        root = workspace_root() if callable(workspace_root) else workspace_root
        candidate = Path(root).expanduser().resolve() / skill_name
        if candidate.exists():
            return True
    except Exception:
        pass
    return _repo_workspace_skill_dir(ctx, skill_name) is not None


def _runtime_ready(mgr: SkillManager, skill_name: str) -> bool:
    try:
        status = mgr.runtime_status(skill_name)
    except Exception:
        return False
    return bool(status.get("ready"))


def _resolve_tool_webspace_id(payload: Dict[str, Any]) -> str:
    token = str(payload.get("webspace_id") or "").strip()
    return token or default_webspace_id()


def _resolve_target_node_id(payload: Dict[str, Any]) -> str:
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    return str(
        payload.get("target_node_id")
        or payload.get("node_id")
        or meta.get("target_node_id")
        or meta.get("node_target_id")
        or ""
    ).strip()


def _is_loopback_base_url(base_url: str | None) -> bool:
    text = str(base_url or "").strip()
    if not text:
        return False
    try:
        parsed = urlparse(text)
        host = str(parsed.hostname or "").strip().lower()
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def _is_readonly_snapshot_tool(tool_name: str) -> bool:
    token = str(tool_name or "").strip()
    return token == "get_snapshot" or token.endswith(":get_snapshot") or token.endswith(".get_snapshot")


def _target_snapshot_unavailable_response(
    *,
    tool_name: str,
    target_node_id: str,
    reason: str,
    retryable: bool = True,
    retry_after_s: float | None = None,
    cached: bool = False,
) -> Dict[str, Any]:
    payload = {
        "ok": False,
        "degraded": True,
        "unavailable": True,
        "source": "hub_tool_bridge",
        "error": "target_member_unavailable",
        "reason": reason,
        "tool": str(tool_name or ""),
        "target_node_id": str(target_node_id or ""),
        "retryable": bool(retryable),
        "updated_at": time.time(),
        "summary": {
            "value": "unavailable",
            "status": "degraded",
            "label": "Target member snapshot",
            "description": reason,
            "selected_node_id": str(target_node_id or ""),
        },
    }
    if retry_after_s is not None:
        payload["retry_after_s"] = max(0.0, float(retry_after_s))
    if cached:
        payload["cached"] = True
    return {"ok": True, "degraded": True, "result": payload}


def _snapshot_unavailable_cache_key(*, tool_name: str, target_node_id: str, webspace_id: str) -> str:
    return "\0".join([str(tool_name or ""), str(target_node_id or ""), str(webspace_id or "")])


def _snapshot_unavailable_cache_get(*, tool_name: str, target_node_id: str, webspace_id: str) -> Dict[str, Any] | None:
    if _SNAPSHOT_UNAVAILABLE_TTL_S <= 0.0:
        return None
    key = _snapshot_unavailable_cache_key(
        tool_name=tool_name,
        target_node_id=target_node_id,
        webspace_id=webspace_id,
    )
    now = time.time()
    with _SNAPSHOT_UNAVAILABLE_CACHE_LOCK:
        item = _SNAPSHOT_UNAVAILABLE_CACHE.get(key)
        if not item:
            return None
        expires_at, payload = item
        if expires_at <= now:
            _SNAPSHOT_UNAVAILABLE_CACHE.pop(key, None)
            return None
        cached_payload = copy.deepcopy(payload)
    result = cached_payload.get("result") if isinstance(cached_payload.get("result"), dict) else {}
    result["cached"] = True
    result["retry_after_s"] = round(max(0.0, float(expires_at) - now), 3)
    cached_payload["result"] = result
    cached_payload["degraded"] = True
    return cached_payload


def _snapshot_unavailable_cache_set(
    payload: Dict[str, Any],
    *,
    tool_name: str,
    target_node_id: str,
    webspace_id: str,
) -> Dict[str, Any]:
    if _SNAPSHOT_UNAVAILABLE_TTL_S <= 0.0:
        return payload
    key = _snapshot_unavailable_cache_key(
        tool_name=tool_name,
        target_node_id=target_node_id,
        webspace_id=webspace_id,
    )
    with _SNAPSHOT_UNAVAILABLE_CACHE_LOCK:
        _SNAPSHOT_UNAVAILABLE_CACHE[key] = (
            time.time() + _SNAPSHOT_UNAVAILABLE_TTL_S,
            copy.deepcopy(payload),
        )
    return payload


def _snapshot_unavailable_cache_clear(*, tool_name: str, target_node_id: str, webspace_id: str) -> None:
    key = _snapshot_unavailable_cache_key(
        tool_name=tool_name,
        target_node_id=target_node_id,
        webspace_id=webspace_id,
    )
    with _SNAPSHOT_UNAVAILABLE_CACHE_LOCK:
        _SNAPSHOT_UNAVAILABLE_CACHE.pop(key, None)


def _snapshot_unavailable_response_cached(
    *,
    tool_name: str,
    target_node_id: str,
    webspace_id: str,
    reason: str,
    retryable: bool = True,
) -> Dict[str, Any]:
    payload = _target_snapshot_unavailable_response(
        tool_name=tool_name,
        target_node_id=target_node_id,
        reason=reason,
        retryable=retryable,
        retry_after_s=_SNAPSHOT_UNAVAILABLE_TTL_S if _SNAPSHOT_UNAVAILABLE_TTL_S > 0.0 else None,
    )
    return _snapshot_unavailable_cache_set(
        payload,
        tool_name=tool_name,
        target_node_id=target_node_id,
        webspace_id=webspace_id,
    )


def _should_proxy_tool_call_to_target(
    *,
    conf: Any,
    tool_name: str,
    target_node_id: str,
    local_node_id: str,
) -> bool:
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return False
    if not target_node_id or target_node_id == local_node_id:
        return False
    tool_token = str(tool_name or "").strip()
    # Some tools expose hub-side projections of member state. Even when the UI
    # is focused on a member node, their authority lives on the hub.
    if any(tool_token.startswith(prefix) for prefix in _HUB_LOCAL_TOOL_PREFIXES):
        return False
    return True


async def _proxy_tool_call_to_node(
    *,
    conf: Any,
    request: Request,
    body: "ToolCall",
    payload: Dict[str, Any],
    target_node_id: str,
) -> Dict[str, Any]:
    directory = get_directory()
    link_manager = get_hub_link_manager()
    webspace_id = _resolve_tool_webspace_id(payload)
    readonly_snapshot = _is_readonly_snapshot_tool(body.tool)
    link_connected = bool(target_node_id and link_manager.is_connected(target_node_id))
    if readonly_snapshot:
        cached = _snapshot_unavailable_cache_get(
            tool_name=body.tool,
            target_node_id=target_node_id,
            webspace_id=webspace_id,
        )
        if cached is not None:
            return cached
    rpc_timeout = _readonly_snapshot_rpc_timeout_s(body.timeout) if readonly_snapshot else body.timeout
    rpc_error: Exception | None = None
    if link_connected:
        try:
            res = await link_manager.rpc_tools_call(
                target_node_id,
                tool=body.tool,
                arguments=payload,
                timeout=rpc_timeout,
                dev=body.dev,
            )
            if readonly_snapshot:
                _snapshot_unavailable_cache_clear(
                    tool_name=body.tool,
                    target_node_id=target_node_id,
                    webspace_id=webspace_id,
                )
            return {"ok": True, "result": res}
        except Exception as exc:
            rpc_error = exc
            _log.debug("rpc tool proxy failed target_node_id=%s tool=%s", target_node_id, body.tool, exc_info=True)
    base_url = directory.get_node_base_url(target_node_id)
    if _is_loopback_base_url(base_url):
        if readonly_snapshot:
            reason = (
                f"member link rpc failed: {type(rpc_error).__name__}: {rpc_error}"
                if rpc_error is not None
                else "member base_url is loopback-only and the live member link is unavailable"
            )
            return _snapshot_unavailable_response_cached(
                tool_name=body.tool,
                target_node_id=target_node_id,
                webspace_id=webspace_id,
                reason=reason,
            )
        if rpc_error is not None:
            raise HTTPException(status_code=502, detail=f"member link rpc failed: {type(rpc_error).__name__}: {rpc_error}")
        raise HTTPException(status_code=503, detail="member base_url is loopback-only and the live member link is unavailable")
    if not base_url:
        if readonly_snapshot:
            reason = (
                f"member link rpc failed: {type(rpc_error).__name__}: {rpc_error}"
                if rpc_error is not None
                else "no base_url or p2p link for target node"
            )
            return _snapshot_unavailable_response_cached(
                tool_name=body.tool,
                target_node_id=target_node_id,
                webspace_id=webspace_id,
                reason=reason,
            )
        if rpc_error is not None:
            raise HTTPException(status_code=502, detail=f"member link rpc failed: {type(rpc_error).__name__}: {rpc_error}")
        raise HTTPException(status_code=503, detail="no base_url or p2p link for target node")
    forward = {"tool": body.tool, "arguments": payload}
    if body.timeout is not None:
        forward["timeout"] = body.timeout
    if body.dev:
        forward["dev"] = True
    token = conf.token or request.headers.get("X-AdaOS-Token") or "dev-local-token"
    try:
        r = await anyio.to_thread.run_sync(
            lambda: requests.post(
                f"{base_url.rstrip('/')}/api/tools/call",
                json=forward,
                headers={"X-AdaOS-Token": token, "Content-Type": "application/json"},
                timeout=(body.timeout or 10) + 2,
            )
        )
    except Exception as pe:
        if readonly_snapshot:
            return _snapshot_unavailable_response_cached(
                tool_name=body.tool,
                target_node_id=target_node_id,
                webspace_id=webspace_id,
                reason=f"proxy failed: {pe}",
            )
        raise HTTPException(status_code=502, detail=f"proxy failed: {pe}")
    if r.status_code != 200:
        if readonly_snapshot:
            return _snapshot_unavailable_response_cached(
                tool_name=body.tool,
                target_node_id=target_node_id,
                webspace_id=webspace_id,
                reason=f"proxy returned HTTP {r.status_code}: {r.text[:300]}",
            )
        raise HTTPException(status_code=r.status_code, detail=r.text)
    try:
        return r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="invalid JSON from proxied node")


def _maybe_sync_workspace_runtime(ctx: AgentContext, mgr: SkillManager, skill_name: str) -> None:
    if not _debug_autosync_enabled():
        return
    if not _workspace_skill_source_exists(ctx, skill_name):
        return
    if not _runtime_ready(mgr, skill_name):
        return
    try:
        mgr.runtime_update(skill_name, space="workspace")
    except Exception:
        _log.debug("workspace runtime_update failed for skill=%s", skill_name, exc_info=True)


def _repair_workspace_runtime(
    ctx: AgentContext,
    mgr: SkillManager,
    skill_name: str,
    *,
    webspace_id: str,
) -> bool:
    if not _workspace_skill_source_exists(ctx, skill_name):
        return False
    try:
        mgr.runtime_update(skill_name, space="workspace")
    except Exception:
        _log.debug("workspace runtime_update repair failed for skill=%s", skill_name, exc_info=True)
    if _runtime_ready(mgr, skill_name):
        return True
    try:
        mgr.activate_for_space(skill_name, space="default", webspace_id=webspace_id)
        return True
    except Exception:
        _log.debug("workspace runtime activation repair failed for skill=%s", skill_name, exc_info=True)
        return False


class ToolCall(BaseModel):
    """
    Вызов инструмента навыка:
      tool: "<skill_name>:<public_tool_name>"
      arguments: {...}  # опционально
      context:   {...}  # опционально (резерв на будущее)
    """

    tool: str
    arguments: Dict[str, Any] | None = None
    context: Dict[str, Any] | None = None
    timeout: float | None = Field(default=None)
    dev: bool = Field(default=False, description="Run tool from DEV workspace instead of installed runtime")
    model_config = {"extra": "ignore"}


@router.post("/tools/call", dependencies=[Depends(require_token)])
async def call_tool(body: ToolCall, request: Request, response: Response, ctx: AgentContext = Depends(get_ctx)):
    if not is_accepting_new_work():
        raise HTTPException(status_code=503, detail="node is draining")
    # Разбираем "<skill_name>:<public_tool_name>"
    if ":" not in body.tool:
        raise HTTPException(status_code=400, detail="tool must be in '<skill_name>:<public_tool_name>' format")

    skill_name, public_tool = body.tool.split(":", 1)
    if not skill_name or not public_tool:
        raise HTTPException(status_code=400, detail="invalid tool spec")

    # Используем общий путь исполнения как в CLI (SkillManager.run_tool)
    mgr = SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )

    trace = attach_http_trace_headers(request.headers, response.headers)
    payload: Dict[str, Any] = body.arguments or {}
    webspace_id = _resolve_tool_webspace_id(payload)
    target_node_id = _resolve_target_node_id(payload)
    conf = getattr(ctx, "config", None)
    local_node_id = str(getattr(conf, "node_id", "") or "").strip()
    if conf and _should_proxy_tool_call_to_target(
        conf=conf,
        tool_name=body.tool,
        target_node_id=target_node_id,
        local_node_id=local_node_id,
    ):
        proxied = await _proxy_tool_call_to_node(
            conf=conf,
            request=request,
            body=body,
            payload=payload,
            target_node_id=target_node_id,
        )
        proxied.setdefault("trace_id", trace)
        return proxied
    # Пробуем локально; если навык отсутствует на узле-хабе — проксируем на member
    try:
        started_at = time.perf_counter()
        if not body.dev:
            _maybe_sync_workspace_runtime(ctx, mgr, skill_name)

        def _run_local_tool() -> Any:
            if body.dev:
                return mgr.run_dev_tool(skill_name, public_tool, payload, timeout=body.timeout)
            try:
                return mgr.run_tool(skill_name, public_tool, payload, timeout=body.timeout)
            except (FileNotFoundError, RuntimeError, KeyError):
                if not _repair_workspace_runtime(ctx, mgr, skill_name, webspace_id=webspace_id):
                    raise
                return mgr.run_tool(skill_name, public_tool, payload, timeout=body.timeout)

        result = await anyio.to_thread.run_sync(_run_local_tool)
        took_ms = (time.perf_counter() - started_at) * 1000.0
        if took_ms >= 2000:
            _log.warning(
                "tools.call slow tool=%s dev=%s took_ms=%.1f",
                body.tool,
                body.dev,
                took_ms,
            )
    except (FileNotFoundError, RuntimeError, KeyError) as e:
        # Если локально не найден навык/слот — попробуем проксировать на участника подсети (только если роль hub)
        if not conf or conf.role != "hub":
            # На member нет прокси — вернём исходную ошибку
            raise HTTPException(status_code=404, detail=str(e))

        # Найти online-ноду с этим skill (используем только runtime; workspace-fallback отключён)
        directory = get_directory()
        candidates = directory.find_nodes_with_skill(skill_name, require_online=True)
        # Сначала активные, затем по last_seen убыв.
        mgr = get_hub_link_manager()
        candidates.sort(key=lambda n: (not mgr.is_connected(n.get("node_id", "")), not bool(n.get("active"))), reverse=False)
        if not candidates:
            raise HTTPException(
                status_code=503,
                detail=f"skill '{skill_name}', tool '{public_tool}' is not available online in the subnet. In dev: {body.dev}. Candidates: {candidates}. Err: {str(e)}",
            )
        target = candidates[0]
        target_node_id = target.get("node_id", "")

        if target_node_id and mgr.is_connected(target_node_id):
            try:
                res = await mgr.rpc_tools_call(
                    target_node_id,
                    tool=body.tool,
                    arguments=payload,
                    timeout=body.timeout,
                    dev=body.dev,
                )
                return {"ok": True, "result": res, "trace_id": trace}
            except Exception as exc:
                rpc_error = exc
        else:
            rpc_error = None

        base_url = target.get("base_url") or directory.get_node_base_url(target_node_id)
        if _is_loopback_base_url(base_url):
            if target_node_id and target_node_id == local_node_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"local skill '{skill_name}', tool '{public_tool}' is unavailable: {e}",
                )
            if rpc_error is not None:
                raise HTTPException(status_code=502, detail=f"member link rpc failed: {type(rpc_error).__name__}: {rpc_error}")
            if _is_readonly_snapshot_tool(body.tool):
                return _target_snapshot_unavailable_response(
                    tool_name=body.tool,
                    target_node_id=target_node_id,
                    reason="member base_url is loopback-only and the live member link is unavailable",
                )
            raise HTTPException(status_code=503, detail="member base_url is loopback-only and the live member link is unavailable")
        if not base_url:
            if target_node_id and target_node_id == local_node_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"local skill '{skill_name}', tool '{public_tool}' is unavailable: {e}",
                )
            if rpc_error is not None:
                raise HTTPException(status_code=502, detail=f"member link rpc failed: {type(rpc_error).__name__}: {rpc_error}")
            if _is_readonly_snapshot_tool(body.tool):
                return _target_snapshot_unavailable_response(
                    tool_name=body.tool,
                    target_node_id=target_node_id,
                    reason="no base_url or p2p link for target node",
                )
            raise HTTPException(status_code=503, detail="no base_url or p2p link for target node")

        # Проксируем запрос прозрачно
        url = f"{base_url.rstrip('/')}/api/tools/call"
        forward = {"tool": body.tool, "arguments": payload}
        if body.timeout is not None:
            forward["timeout"] = body.timeout
        # сохраняем dev-флаг при прокси, если он был указан
        if body.dev:
            forward["dev"] = True
        token = conf.token or request.headers.get("X-AdaOS-Token") or "dev-local-token"
        try:
            r = await anyio.to_thread.run_sync(
                lambda: requests.post(
                    url,
                    json=forward,
                    headers={"X-AdaOS-Token": token, "Content-Type": "application/json"},
                    timeout=(body.timeout or 10) + 2,
                )
            )
        except Exception as pe:
            raise HTTPException(status_code=502, detail=f"proxy failed: {pe}")
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        try:
            result_payload = r.json()
        except Exception:
            raise HTTPException(status_code=502, detail="invalid JSON from proxied node")
        # Возвращаем payload как есть от член-узла
        return result_payload
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"run failed: {type(e).__name__}: {e}")

    # Optional routing via local bus: publish ui.notify when result looks like plain text
    try:
        text: str | None = None
        if isinstance(result, str):
            text = result
        elif isinstance(result, dict):
            t = result.get("text") if hasattr(result, "get") else None
            if isinstance(t, str) and t.strip():
                text = t
        if text:
            emit(ctx.bus, "ui.notify", {"text": text}, actor="api.tools")
    except Exception:
        # best-effort: failure to route should not break API response
        pass

    return {"ok": True, "result": result, "trace_id": trace}
