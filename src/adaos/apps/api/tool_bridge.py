# src\adaos\api\tool_bridge.py
import asyncio
import copy
import hashlib
import json
import logging
import mimetypes
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Literal, Mapping
from urllib.parse import quote, urlparse

import anyio
import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_tool_caller
from adaos.domain.node_identity import node_identities_match, node_identity_token
from adaos.services.observe import attach_http_trace_headers
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.eventbus import emit
from adaos.services.pending_actions import list_pending_actions_async, publish_pending_action_async
from adaos.services.policy.caller import CallerAccessDenied, current_caller, current_caller_scope
from adaos.services.runtime_lifecycle import is_accepting_new_work
from adaos.services.runtime_action_grants import (
    find_runtime_action_grant,
    remember_runtime_action_grant,
)
from adaos.services.skill.manager import SkillManager
from adaos.services.skill.tool_contract import (
    declared_tool_application_access as _declared_tool_application_access,
    declared_tool_contract as _declared_tool_contract,
    declared_tool_permissions as _declared_tool_permissions,
    declared_tool_side_effects as _declared_tool_side_effects,
    side_effects_are_read_only as _declared_side_effects_are_read_only,
)
from adaos.adapters.db import SqliteSkillRegistry
from adaos.services.registry.subnet_directory import get_directory
from adaos.services.subnet.link_manager import get_hub_link_manager
from adaos.services.yjs.webspace import default_webspace_id


router = APIRouter()
_log = logging.getLogger("adaos.api.tool_bridge")
_HUB_LOCAL_TOOL_PREFIXES: tuple[str, ...] = (
    "browsers_skill:",
    "infra_access_skill:",
    "slideshow_skill:",
)
_HUB_LOCAL_TOOL_NAMES: tuple[str, ...] = (
    "prompt_engineer_skill:prompt_select_project",
)
_UI_NAVIGATION_TOOL_NAMES: tuple[str, ...] = (
    "prompt_engineer_skill:prompt_select_project",
)
_WORKSPACE_AUTOSYNC_EXEMPT_TOOL_PREFIXES: tuple[str, ...] = (
    "slideshow_skill:",
)
_DECLARED_SIDE_EFFECT_RISK_CLASS: dict[str, str] = {
    "safe": "safe",
    "none": "safe",
    "read": "safe",
    "read_only": "safe",
    "readonly": "safe",
    "ui_navigation": "ui_navigation",
    "local_write": "local_write",
    "runtime_write": "local_write",
    "external_write": "network",
    "device_control": "device_control",
}


async def _skill_manager_for_context(ctx: AgentContext) -> SkillManager:
    registry = await asyncio.to_thread(SqliteSkillRegistry, ctx.sql)
    return SkillManager(
        repo=ctx.skills_repo,
        registry=registry,
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )


async def _ensure_on_demand_service_started(skill_name: str) -> bool:
    """Activate a selected service skill only when its stable tool is invoked."""

    from adaos.services.skill.service_supervisor import get_service_supervisor

    supervisor = get_service_supervisor()
    if skill_name not in supervisor._specs:
        await supervisor.refresh_discovered()
        if skill_name not in supervisor._specs:
            return False
    try:
        await supervisor.start(skill_name)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_skill_start_failed",
                "skill": skill_name,
                "reason": type(exc).__name__,
                "retryable": True,
            },
        ) from exc
    return True


_LOCAL_WRITE_TOOL_NAMES: tuple[str, ...] = (
    "cv_descriptor:cv_descriptor_configure_model",
    "cv_descriptor:cv_descriptor_save_descriptor",
    "cv_descriptor:cv_descriptor_update_descriptor",
    "cv_descriptor:cv_descriptor_delete_descriptor",
    "cv_descriptor:cv_descriptor_clear",
    "cv_descriptor:cv_descriptor_runtime_command",
    "cv_descriptor:cv_descriptor_record_runtime_event",
    "slideshow_skill:refresh_redevice_slideshow_state",
    "slideshow_skill:select_redevice_endpoint",
    "slideshow_skill:toggle_redevice_endpoint",
    "slideshow_skill:rename_redevice_endpoint",
    "redevice_settings:refresh_redevice_settings_state",
    "redevice_settings:select_redevice_settings_endpoint",
    "redevice_settings:rename_redevice_settings_endpoint",
    "redevice_settings:set_redevice_assignment",
    "browsers_skill:select_browser",
    "browsers_skill:rename_selected_browser",
    "browsers_skill:rename_device",
    "browsers_skill:rename_browser_device_name",
    "browsers_skill:set_browser_media_control",
    "notebook_skill:attach_note_file",
    "notebook_skill:attach_note_upload",
    "notebook_skill:create_note",
    "notebook_skill:delete_note",
    "notebook_skill:save_note",
    "notebook_skill:select_note",
    "prompt_engineer_skill:prompt_save_base_tz",
    "prompt_engineer_skill:prompt_append_tz_addendum",
    "prompt_engineer_skill:prompt_save_project_file",
    "prompt_engineer_skill:prompt_set_workflow_state",
    "prompt_engineer_skill:prompt_create_dev_project",
    "builder_skill:chat",
    "builder_skill:create_scenario_draft",
    "builder_skill:update_application_metadata",
    "builder_skill:update_current_scenario",
    "builder_skill:set_ui_revision_current",
    "builder_skill:set_active_draft",
    "builder_skill:delete_development_skill",
)
_RUNTIME_ACTION_APPROVAL_KIND = "runtime.action_approval"
_RUNTIME_ACTION_APPROVAL_RESPONSE_TOPIC = "runtime.action_approval.response"
_SNAPSHOT_UNAVAILABLE_TTL_S = max(0.0, float(os.getenv("ADAOS_TOOL_BRIDGE_SNAPSHOT_UNAVAILABLE_TTL_S") or "20"))
_SNAPSHOT_UNAVAILABLE_CACHE_LOCK = threading.RLock()
_SNAPSHOT_UNAVAILABLE_CACHE: dict[str, tuple[float, Dict[str, Any]]] = {}
_WORKSPACE_RUNTIME_SYNC_MIN_INTERVAL_S = max(
    0.0,
    float(os.getenv("ADAOS_TOOL_BRIDGE_WORKSPACE_AUTOSYNC_MIN_INTERVAL_S") or "10"),
)
_WORKSPACE_RUNTIME_LOCKS_LOCK = threading.RLock()
_WORKSPACE_RUNTIME_LOCKS: dict[str, threading.RLock] = {}
_WORKSPACE_RUNTIME_LAST_SYNC_AT: dict[str, float] = {}
_TOOL_CALL_IDEMPOTENCY_TTL_S = max(
    1.0,
    min(3600.0, float(os.getenv("ADAOS_TOOL_CALL_IDEMPOTENCY_TTL_S") or "180")),
)
_TOOL_CALL_IDEMPOTENCY_WAIT_S = max(
    1.0,
    min(300.0, float(os.getenv("ADAOS_TOOL_CALL_IDEMPOTENCY_WAIT_S") or "65")),
)
_TOOL_CALL_IDEMPOTENCY_LOCK = threading.RLock()
_TOOL_CALL_IDEMPOTENCY_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_APPROVED_ACTION_STATES = {"approve", "approved", "allowed", "operator_apply_allowed", "responded"}
_RISK_FREEFORM_ARGUMENT_KEYS = {"content", "text"}


def _runtime_action_gate_enabled() -> bool:
    raw = str(os.getenv("ADAOS_RUNTIME_ACTION_RISK_GATE") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _without_empty(value: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in value.items() if v not in (None, "", {}, [])}


def _risk_relevant_payload(
    payload: Dict[str, Any],
    *,
    include_node_targets: bool,
    ignore_freeform: bool = False,
) -> Dict[str, Any]:
    ignored = {
        "_meta",
        "action_approval",
        "approval",
        "approval_ref",
        "pending_action_approval",
        "pending_action_id",
        "pending_action_status",
        "approved_by",
    }
    if not include_node_targets:
        ignored.update({"target_node_id", "node_id", "node_target_id", "source_node_id"})
    if ignore_freeform:
        ignored.update(_RISK_FREEFORM_ARGUMENT_KEYS)
    return {key: value for key, value in payload.items() if key not in ignored}


def _looks_readonly_tool(public_tool: str) -> bool:
    token = str(public_tool or "").strip().lower().replace("-", "_")
    if not token:
        return False
    readonly_prefixes = (
        "get_",
        "list_",
        "read_",
        "search_",
        "query_",
        "describe_",
        "preview_",
        "validate_",
        "check_",
        "inspect_",
        "prompt_list_",
        "prompt_read_",
    )
    return token in {"get_snapshot", "snapshot"} or token.startswith(readonly_prefixes)


def _looks_ui_navigation_tool(tool_name: str, public_tool: str) -> bool:
    full = str(tool_name or "").strip()
    return full in _UI_NAVIGATION_TOOL_NAMES


def _looks_local_write_tool(tool_name: str, public_tool: str) -> bool:
    full = str(tool_name or "").strip()
    return full in _LOCAL_WRITE_TOOL_NAMES


def _action_risk_may_mutate(action_risk: Mapping[str, Any] | None) -> bool:
    risk = action_risk if isinstance(action_risk, Mapping) else {}
    risk_class = str(risk.get("risk_class") or "").strip().lower().replace("-", "_")
    return risk_class not in {"safe", "none", "read", "read_only", "readonly", "ui_navigation"}


def _webspace_uses_dev_runtime(
    payload: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> bool:
    """Resolve DEV execution from authoritative webspace metadata."""
    request_context = _mapping(context)
    webspace_id = str(request_context.get("webspace_id") or "").strip()
    if not webspace_id:
        webspace_id = _resolve_tool_webspace_id(payload, context=context)
    if not webspace_id:
        return False
    try:
        from adaos.services.workspaces import index as workspace_index

        manifest = workspace_index.get_workspace(webspace_id)
        return bool(manifest and manifest.is_dev)
    except Exception:
        _log.debug("failed to resolve runtime space for webspace=%s", webspace_id, exc_info=True)
        return False


def _explicit_risk_class(payload: Dict[str, Any], context: Dict[str, Any] | None = None) -> str:
    meta = _mapping(payload.get("_meta"))
    ctx = _mapping(context)
    for source in (payload, meta, ctx):
        for key in ("risk_class", "side_effect_class", "effect_class", "action_risk_class"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        action_risk = source.get("action_risk")
        if isinstance(action_risk, dict):
            value = action_risk.get("risk_class") or action_risk.get("side_effect_class")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _runtime_action_risk(
    *,
    body: "ToolCall",
    skill_name: str,
    public_tool: str,
    payload: Dict[str, Any],
    target_node_id: str = "",
    local_node_id: str = "",
    forced_side_effect_class: str = "",
) -> Dict[str, Any]:
    from adaos.services.conversation_safety import classify_action_risk

    explicit = _explicit_risk_class(payload, body.context)
    tool_name = str(body.tool or "").strip()
    effective_target = str(target_node_id or _resolve_target_node_id(payload) or "").strip()
    trusted_side_effect_class = str(forced_side_effect_class or "").strip()
    side_effect_class = trusted_side_effect_class or explicit
    include_node_targets = True
    readonly_tool = _is_readonly_snapshot_tool(tool_name) or _looks_readonly_tool(public_tool)
    local_write_tool = _looks_local_write_tool(tool_name, public_tool)
    if not side_effect_class:
        if readonly_tool:
            side_effect_class = "safe"
            include_node_targets = False
        elif _looks_ui_navigation_tool(tool_name, public_tool):
            side_effect_class = "ui_navigation"
            include_node_targets = False
        elif local_write_tool:
            side_effect_class = "local_write"
            include_node_targets = False
        elif effective_target and not node_identities_match(effective_target, local_node_id):
            if any(tool_name.startswith(prefix) for prefix in _HUB_LOCAL_TOOL_PREFIXES):
                side_effect_class = "local_write"
                include_node_targets = False
            else:
                side_effect_class = "cross_node"
        elif effective_target and node_identities_match(effective_target, local_node_id):
            include_node_targets = False
    normalized_side_effect = str(side_effect_class or "").strip().lower().replace("-", "_")
    declared_risk_class = (
        _DECLARED_SIDE_EFFECT_RISK_CLASS.get(normalized_side_effect)
        if trusted_side_effect_class
        else None
    )
    if declared_risk_class is not None:
        include_node_targets = False
    elif normalized_side_effect in {"safe", "read_only", "readonly", "local_write", "ui_navigation"}:
        include_node_targets = False
    ignore_freeform = declared_risk_class is not None or normalized_side_effect in {
        "safe",
        "read_only",
        "readonly",
        "local_write",
        "ui_navigation",
    }
    if declared_risk_class is not None:
        action = {"side_effect_class": declared_risk_class}
    elif normalized_side_effect in {"safe", "read_only", "readonly"}:
        action = {"side_effect_class": "safe"}
    elif local_write_tool and normalized_side_effect == "local_write":
        action = {"side_effect_class": "local_write"}
    elif _looks_ui_navigation_tool(tool_name, public_tool) and normalized_side_effect == "ui_navigation":
        action = {"side_effect_class": "ui_navigation"}
    else:
        action = _without_empty(
            {
                "tool": tool_name,
                "skill": skill_name,
                "public_tool": public_tool,
                "side_effect_class": side_effect_class,
                "dev_runtime": bool(body.dev),
                "target_node_id": effective_target if include_node_targets else "",
                "arguments": _risk_relevant_payload(
                    payload,
                    include_node_targets=include_node_targets,
                    ignore_freeform=ignore_freeform,
                ),
            }
        )
    return classify_action_risk(action)


def _approval_sources(payload: Dict[str, Any], context: Dict[str, Any] | None = None) -> list[dict[str, Any]]:
    meta = _mapping(payload.get("_meta"))
    ctx = _mapping(context)
    sources: list[dict[str, Any]] = []
    for source in (payload, meta, ctx):
        for key in ("action_approval", "approval", "approval_ref", "pending_action_approval"):
            value = source.get(key)
            if isinstance(value, dict):
                sources.append(dict(value))
        direct_pending_action_id = source.get("pending_action_id")
        if isinstance(direct_pending_action_id, str) and direct_pending_action_id.strip():
            sources.append(
                {
                    "pending_action_id": direct_pending_action_id.strip(),
                    "status": source.get("pending_action_status") or source.get("response_action_id"),
                    "approved_by": source.get("approved_by") or source.get("responder") or source.get("user_id"),
                }
            )
    return sources


def _approval_allows_runtime_action(approval: Dict[str, Any], action_risk: Dict[str, Any]) -> bool:
    if approval.get("approved") is True or approval.get("allowed") is True:
        return bool(_approval_identity(approval))
    status = str(
        approval.get("status")
        or approval.get("decision")
        or approval.get("response_action_id")
        or approval.get("action")
        or ""
    ).strip().lower()
    if status not in _APPROVED_ACTION_STATES:
        return False
    approval_risk_class = str(approval.get("risk_class") or approval.get("approved_risk_class") or "").strip()
    requested_risk_class = str(action_risk.get("risk_class") or "").strip()
    if approval_risk_class and requested_risk_class and approval_risk_class != requested_risk_class:
        return False
    return bool(_approval_identity(approval))


def _approval_identity(approval: Dict[str, Any]) -> str:
    for key in ("approval_id", "pending_action_id", "id", "approved_by", "responder_id", "user_id"):
        value = approval.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    responder = approval.get("responder")
    if isinstance(responder, dict):
        for key in ("user_id", "actor_id", "id"):
            value = responder.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _approval_contract() -> Dict[str, Any]:
    return {
        "accepted_fields": ["action_approval", "_meta.action_approval", "context.action_approval"],
        "required": ["approved/status=approve", "approval_id|pending_action_id|approved_by"],
    }


def _first_text(*values: Any) -> str:
    for value in values:
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _runtime_operator_ui_approval(
    *,
    payload: Dict[str, Any],
    context: Dict[str, Any] | None,
    action_risk: Dict[str, Any],
) -> Dict[str, Any] | None:
    if str(action_risk.get("risk_class") or "").strip() != "device_control":
        return None
    meta = _mapping(payload.get("_meta"))
    ctx = _mapping(context)
    meta_context = _mapping(meta.get("action_context"))
    source = _first_text(meta.get("action_source"), ctx.get("action_source"))
    if source != "operator_ui":
        return None
    auto_action_id = _first_text(
        meta.get("auto_action_id"),
        meta.get("autoActionId"),
        meta_context.get("autoActionId"),
        meta_context.get("auto_action_id"),
        ctx.get("autoActionId"),
        ctx.get("auto_action_id"),
    )
    if auto_action_id:
        return None
    widget_id = _first_text(
        meta.get("widget_id"),
        meta_context.get("widgetId"),
        meta_context.get("widget_id"),
        ctx.get("widgetId"),
        ctx.get("widget_id"),
    )
    event_id = _first_text(
        meta.get("event_id"),
        meta_context.get("eventId"),
        meta_context.get("event_id"),
        ctx.get("eventId"),
        ctx.get("event_id"),
    )
    if not widget_id or not event_id:
        return None
    return {
        "status": "approve",
        "risk_class": "device_control",
        "approved_by": "operator_ui",
        "approval_id": f"operator_ui:{widget_id}:{event_id}",
        "source": "operator_ui",
        "widget_id": widget_id,
        "event_id": event_id,
    }


def _runtime_action_fingerprint_payload(
    *,
    body: "ToolCall",
    skill_name: str,
    public_tool: str,
    payload: Dict[str, Any],
    action_risk: Dict[str, Any],
    target_node_id: str,
    local_node_id: str,
) -> Dict[str, Any]:
    return _without_empty(
        {
            "tool": str(body.tool or ""),
            "skill": skill_name,
            "public_tool": public_tool,
            "webspace_id": _resolve_tool_webspace_id(
                payload, context=body.context
            ),
            "target_node_id": str(target_node_id or _resolve_target_node_id(payload) or "").strip(),
            "local_node_id": str(local_node_id or "").strip(),
            "dev": bool(body.dev),
            "risk_class": str(action_risk.get("risk_class") or "").strip(),
            "arguments": _risk_relevant_payload(payload, include_node_targets=True),
        }
    )


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return str(value)


def _tool_call_idempotency_key(body: "ToolCall", request: Request) -> str:
    raw = _first_text(
        getattr(body, "idempotency_key", None),
        getattr(body, "request_id", None),
        request.headers.get("Idempotency-Key") if hasattr(request, "headers") else "",
        request.headers.get("X-Idempotency-Key") if hasattr(request, "headers") else "",
    )
    if not raw:
        return ""
    token = "".join(ch if ch.isalnum() or ch in "._:-" else "_" for ch in raw.strip())
    if len(token) <= 180:
        return token
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{token[:140]}.{digest[:32]}"


def _tool_call_idempotency_fingerprint(body: "ToolCall") -> str:
    payload = {
        "tool": str(body.tool or "").strip(),
        "arguments": body.arguments or {},
        "context": body.context or {},
        "intent": body.intent,
        "timeout": body.timeout,
        "dev": bool(body.dev),
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _tool_call_idempotency_cleanup(now: float) -> None:
    expired = [
        key
        for key, entry in _TOOL_CALL_IDEMPOTENCY_CACHE.items()
        if float(entry.get("expires_at") or 0.0) <= now
    ]
    for key in expired:
        _TOOL_CALL_IDEMPOTENCY_CACHE.pop(key, None)


def _tool_call_idempotency_begin(
    body: "ToolCall",
    request: Request,
) -> tuple[str, str, dict[str, Any] | None]:
    key = _tool_call_idempotency_key(body, request)
    if not key:
        return "bypass", "", None
    from adaos.services.policy.caller import current_caller

    caller = current_caller()
    scope = current_caller_scope()
    cache_key = (caller.ref() if caller is not None else "", scope.ref() if scope else "", key)
    fingerprint = _tool_call_idempotency_fingerprint(body)
    now = time.time()
    with _TOOL_CALL_IDEMPOTENCY_LOCK:
        _tool_call_idempotency_cleanup(now)
        entry = _TOOL_CALL_IDEMPOTENCY_CACHE.get(cache_key)
        if entry is not None:
            if str(entry.get("fingerprint") or "") != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "tool_call_idempotency_conflict",
                        "idempotency_key": key,
                        "retryable": False,
                    },
                )
            if bool(entry.get("done")):
                return "cached", key, entry
            return "wait", key, entry
        event = threading.Event()
        entry = {
            "fingerprint": fingerprint,
            "created_at": now,
            "expires_at": now + _TOOL_CALL_IDEMPOTENCY_TTL_S,
            "event": event,
            "done": False,
        }
        _TOOL_CALL_IDEMPOTENCY_CACHE[cache_key] = entry
        return "owner", key, entry


def _tool_call_idempotency_store_result(entry: dict[str, Any], result: Any) -> None:
    with _TOOL_CALL_IDEMPOTENCY_LOCK:
        entry["kind"] = "result"
        entry["result"] = copy.deepcopy(result)
        entry["done"] = True
        entry["expires_at"] = time.time() + _TOOL_CALL_IDEMPOTENCY_TTL_S
        event = entry.get("event")
        if isinstance(event, threading.Event):
            event.set()


def _tool_call_idempotency_store_http_error(entry: dict[str, Any], exc: HTTPException) -> None:
    with _TOOL_CALL_IDEMPOTENCY_LOCK:
        entry["kind"] = "http_error"
        entry["status_code"] = int(exc.status_code)
        entry["detail"] = copy.deepcopy(exc.detail)
        entry["headers"] = copy.deepcopy(exc.headers)
        entry["done"] = True
        entry["expires_at"] = time.time() + _TOOL_CALL_IDEMPOTENCY_TTL_S
        event = entry.get("event")
        if isinstance(event, threading.Event):
            event.set()


def _tool_call_idempotency_store_runtime_error(entry: dict[str, Any], exc: BaseException) -> None:
    with _TOOL_CALL_IDEMPOTENCY_LOCK:
        entry["kind"] = "runtime_error"
        entry["detail"] = f"{type(exc).__name__}: {exc}"
        entry["done"] = True
        entry["expires_at"] = time.time() + _TOOL_CALL_IDEMPOTENCY_TTL_S
        event = entry.get("event")
        if isinstance(event, threading.Event):
            event.set()


def _tool_call_idempotency_replay(entry: dict[str, Any], response: Response) -> Any:
    kind = str(entry.get("kind") or "")
    try:
        response.headers["X-AdaOS-Idempotency-Replay"] = "1"
    except Exception:
        pass
    if kind == "result":
        return copy.deepcopy(entry.get("result"))
    if kind == "http_error":
        headers = copy.deepcopy(entry.get("headers") or {}) or {}
        headers["X-AdaOS-Idempotency-Replay"] = "1"
        raise HTTPException(
            status_code=int(entry.get("status_code") or 500),
            detail=copy.deepcopy(entry.get("detail")),
            headers=headers,
        )
    raise HTTPException(
        status_code=500,
        detail={
            "error": "tool_call_idempotent_retry_failed",
            "detail": str(entry.get("detail") or "cached call failed"),
            "retryable": False,
        },
    )


async def _tool_call_idempotency_wait(entry: dict[str, Any], response: Response) -> Any:
    event = entry.get("event")
    if not isinstance(event, threading.Event):
        raise HTTPException(status_code=409, detail={"error": "tool_call_idempotency_in_progress"})
    done = await anyio.to_thread.run_sync(event.wait, _TOOL_CALL_IDEMPOTENCY_WAIT_S)
    if not done:
        raise HTTPException(
            status_code=425,
            detail={
                "error": "tool_call_idempotency_in_progress",
                "retryable": True,
                "retry_after_s": min(5.0, _TOOL_CALL_IDEMPOTENCY_WAIT_S),
            },
        )
    return _tool_call_idempotency_replay(entry, response)


def _tool_call_forward_payload(body: "ToolCall", payload: Dict[str, Any]) -> Dict[str, Any]:
    forward: Dict[str, Any] = {"tool": body.tool, "arguments": payload}
    if body.intent:
        forward["intent"] = body.intent
    if body.timeout is not None:
        forward["timeout"] = body.timeout
    if body.dev:
        forward["dev"] = True
    if body.idempotency_key:
        forward["idempotency_key"] = body.idempotency_key
    if body.request_id:
        forward["request_id"] = body.request_id
    return forward


def _runtime_action_domain_ref(
    *,
    body: "ToolCall",
    skill_name: str,
    public_tool: str,
    payload: Dict[str, Any],
    action_risk: Dict[str, Any],
    target_node_id: str,
    local_node_id: str,
) -> Dict[str, Any]:
    fingerprint_payload = _runtime_action_fingerprint_payload(
        body=body,
        skill_name=skill_name,
        public_tool=public_tool,
        payload=payload,
        action_risk=action_risk,
        target_node_id=target_node_id,
        local_node_id=local_node_id,
    )
    fingerprint = hashlib.sha256(_stable_json(fingerprint_payload).encode("utf-8")).hexdigest()
    verified_application = _mapping(_mapping(body.context).get("_verified_application_access"))
    return _without_empty(
        {
            "tool": str(body.tool or ""),
            "skill": skill_name,
            "public_tool": public_tool,
            "webspace_id": _resolve_tool_webspace_id(
                payload, context=body.context
            ),
            "target_node_id": str(target_node_id or _resolve_target_node_id(payload) or "").strip(),
            "risk_class": str(action_risk.get("risk_class") or "").strip(),
            "arguments_sha256": fingerprint,
            "application_id": verified_application.get("application_id"),
            "release_digest": verified_application.get("release_digest"),
            "permission_profile_digest": verified_application.get("permission_profile_digest"),
            "subject_ref": verified_application.get("subject_ref"),
            "permission_id": verified_application.get("permission_id"),
            "app_capability": verified_application.get("app_capability"),
            "holder_ref": verified_application.get("holder_ref"),
        }
    )


def _runtime_action_pending_action_id(domain_ref: Dict[str, Any]) -> str:
    fingerprint = hashlib.sha256(_stable_json(domain_ref).encode("utf-8")).hexdigest()[:24]
    return f"pa.runtime_action.{fingerprint}"


def _runtime_action_grant_ref(
    declaration: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    spec = dict(declaration) if isinstance(declaration, Mapping) else {}
    scope = _first_text(spec.get("name"), spec.get("scope"))
    resource_argument = _first_text(spec.get("resource_argument"))
    if not scope or not resource_argument:
        return {}
    meta = _mapping(payload.get("_meta"))
    principal_key = _first_text(spec.get("principal_meta_key")) or "controller_device_id"
    controller = _first_text(meta.get(principal_key), payload.get("controller_id"))
    if controller:
        subject = f"controller:{controller}"
    else:
        profile = _first_text(payload.get("profile_id"))
        if not profile:
            return {}
        subject = f"profile:{profile}"
    resource = _first_text(payload.get(resource_argument))
    if not resource:
        return {}
    try:
        requested_ttl = int(spec.get("ttl_seconds") or 30 * 24 * 60 * 60)
    except (TypeError, ValueError):
        requested_ttl = 30 * 24 * 60 * 60
    return {
        "subject": subject,
        "scope": scope,
        "resource": resource,
        "webspace_id": _resolve_tool_webspace_id(dict(payload)),
        "ttl_seconds": max(
            300,
            min(365 * 24 * 60 * 60, requested_ttl),
        ),
    }


def _runtime_action_targets_local_resource(
    declaration: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
) -> bool:
    spec = dict(declaration) if isinstance(declaration, Mapping) else {}
    resource_argument = _first_text(spec.get("local_resource_argument"))
    if not resource_argument:
        return False
    meta = _mapping(payload.get("_meta"))
    principal_key = _first_text(spec.get("local_principal_meta_key")) or "controller_endpoint_id"
    principal = _first_text(meta.get(principal_key), payload.get(principal_key))
    resource = _first_text(payload.get(resource_argument))
    return bool(principal and resource and principal == resource)


def _runtime_action_approval_presentation(
    declaration: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
    *,
    tool_name: str,
    risk_class: str,
) -> dict[str, Any]:
    spec = dict(declaration) if isinstance(declaration, Mapping) else {}
    presentation = _mapping(spec.get("presentation"))
    verified_application = _mapping(spec.get("application_access"))
    target_label = _first_text(payload.get("target_label"), payload.get("target_id"))
    params = _without_empty({
        "tool": tool_name,
        "risk_class": risk_class,
        "target": target_label,
        "target_label": target_label,
        "application": verified_application.get("application_title"),
        "permission": verified_application.get("permission_id"),
    })
    application_title = _first_text(verified_application.get("application_title"))
    permission_id = _first_text(verified_application.get("permission_id"))
    title = _first_text(presentation.get("title")) or (
        f"{application_title} needs approval" if application_title else "Action approval required"
    )
    summary = _first_text(presentation.get("summary")) or (
        f"Approve {permission_id} for {application_title}."
        if application_title and permission_id
        else f"Approve {tool_name} ({risk_class}) before it runs."
    )
    title_key = _first_text(presentation.get("title_i18n_key")) or "pending_actions.runtime.action_approval_title"
    summary_key = _first_text(presentation.get("summary_i18n_key")) or "pending_actions.runtime.action_approval_summary"
    waiting_key = _first_text(presentation.get("waiting_i18n_key")) or summary_key
    return {
        "title": title,
        "title_i18n": {"key": title_key, "params": params},
        "summary": summary,
        "summary_i18n": {"key": summary_key, "params": params},
        "waiting_i18n": {"key": waiting_key, "params": params},
        "params": params,
    }


def _pending_action_domain_matches(action: Dict[str, Any], domain_ref: Dict[str, Any]) -> bool:
    candidate = _mapping(action.get("domain_ref"))
    if not candidate:
        return False
    return all(str(candidate.get(key) or "") == str(value or "") for key, value in domain_ref.items())


def _pending_action_approval(action: Dict[str, Any], action_risk: Dict[str, Any]) -> Dict[str, Any] | None:
    if str(action.get("status") or "").strip().lower() != "responded":
        return None
    response = _mapping(action.get("response"))
    approval = {
        "status": response.get("response_action_id"),
        "pending_action_id": action.get("id"),
        "risk_class": str(action_risk.get("risk_class") or "").strip(),
        "responder": response.get("responder"),
    }
    if _approval_allows_runtime_action(approval, action_risk):
        return approval
    return None


def _approval_actor_ref(approval: Mapping[str, Any]) -> str:
    responder = _mapping(approval.get("responder"))
    explicit = _first_text(
        responder.get("ref"),
        approval.get("approved_by"),
        responder.get("actor_ref"),
    )
    if explicit:
        return explicit if ":" in explicit else f"user:{explicit}"
    kind = _first_text(responder.get("kind"), responder.get("type"), "user")
    identifier = _first_text(
        responder.get("user_id"),
        responder.get("actor_id"),
        responder.get("id"),
    )
    return f"{kind}:{identifier}" if identifier else "user:approved-operator"


async def _materialize_approved_application_grant(
    *,
    application_gate: Mapping[str, Any],
    approval: Mapping[str, Any],
    ctx: AgentContext | None,
) -> Dict[str, Any]:
    decision = _mapping(application_gate.get("decision"))
    verified = _mapping(application_gate.get("context"))
    if str(decision.get("reason_code") or "").strip() != "application_grant_missing":
        return {}
    application_id = _first_text(verified.get("application_id"))
    release_digest = _first_text(verified.get("release_digest"))
    profile_digest = _first_text(verified.get("permission_profile_digest"))
    subject_ref = _first_text(verified.get("subject_ref"))
    permission_id = _first_text(verified.get("permission_id"))
    approval_id = _first_text(approval.get("pending_action_id"), approval.get("approval_id"))
    if not all(
        (
            application_id,
            release_digest,
            profile_digest,
            subject_ref,
            permission_id,
            approval_id,
        )
    ):
        return {}
    effective_ctx = ctx or get_ctx()
    paths = getattr(effective_ctx, "paths", None)
    state_dir_getter = getattr(paths, "state_dir", None)
    state_dir = getattr(effective_ctx, "authority_state_dir", None)
    if not state_dir and callable(state_dir_getter):
        state_dir = state_dir_getter()
    if not state_dir:
        return {}
    from adaos.services.applications.access_management import (
        ApplicationAccessManagementService,
    )
    from adaos.services.applications.runtime import get_application_service

    management = ApplicationAccessManagementService(
        get_application_service(Path(state_dir))
    )
    grant = await asyncio.to_thread(
        management.approve_missing_runtime_grant,
        application_id=application_id,
        release_digest=release_digest,
        permission_profile_digest=profile_digest,
        subject_ref=subject_ref,
        permission_id=permission_id,
        approval_id=approval_id,
        issuer_ref=_approval_actor_ref(approval),
    )
    return grant.to_dict()


async def _find_runtime_action_pending_action(
    *,
    webspace_id: str,
    action_id: str,
    domain_ref: Dict[str, Any],
) -> Dict[str, Any] | None:
    try:
        snapshot = await list_pending_actions_async(webspace_id=webspace_id, include_terminal=True)
    except Exception:
        _log.debug("failed to inspect runtime action pending actions", exc_info=True)
        return None
    by_id = _mapping(snapshot.get("by_id"))
    direct = _mapping(by_id.get(action_id))
    if direct and _pending_action_domain_matches(direct, domain_ref):
        return direct
    for item in by_id.values():
        candidate = _mapping(item)
        if str(candidate.get("kind") or "") != _RUNTIME_ACTION_APPROVAL_KIND:
            continue
        if _pending_action_domain_matches(candidate, domain_ref):
            return candidate
    return None


async def _ensure_runtime_action_pending_action(
    *,
    body: "ToolCall",
    skill_name: str,
    public_tool: str,
    payload: Dict[str, Any],
    action_risk: Dict[str, Any],
    target_node_id: str,
    local_node_id: str,
    approval_scope: Mapping[str, Any] | None = None,
    ctx: AgentContext | None = None,
) -> Dict[str, Any]:
    # The browser keeps the active webspace in the trusted call context.  Many
    # provider tools have no webspace argument of their own; dropping the
    # context here publishes an approval into the process default webspace and
    # makes an approval performed in the Application UI impossible to observe
    # on retry.
    webspace_id = _resolve_tool_webspace_id(payload, context=body.context)
    domain_ref = _runtime_action_domain_ref(
        body=body,
        skill_name=skill_name,
        public_tool=public_tool,
        payload=payload,
        action_risk=action_risk,
        target_node_id=target_node_id,
        local_node_id=local_node_id,
    )
    action_id = _runtime_action_pending_action_id(domain_ref)
    existing = await _find_runtime_action_pending_action(
        webspace_id=webspace_id,
        action_id=action_id,
        domain_ref=domain_ref,
    )
    if existing:
        return existing
    risk_class = str(action_risk.get("risk_class") or "runtime").strip() or "runtime"
    tool_name = str(body.tool or "").strip()
    presentation = _runtime_action_approval_presentation(
        approval_scope,
        payload,
        tool_name=tool_name,
        risk_class=risk_class,
    )
    verified_application = _mapping(
        _mapping(body.context).get("_verified_application_access")
    )
    application_id = _first_text(verified_application.get("application_id"))
    subject_ref = _first_text(verified_application.get("subject_ref"))
    try:
        return await publish_pending_action_async(
            ctx=ctx or get_ctx(),
            webspace_id=webspace_id,
            action_id=action_id,
            kind=_RUNTIME_ACTION_APPROVAL_KIND,
            title=presentation["title"],
            title_i18n=presentation["title_i18n"],
            summary=presentation["summary"],
            summary_i18n=presentation["summary_i18n"],
            producer={"type": "system", "system_id": "tool_bridge"},
            owner_scope=_without_empty({"webspace_id": webspace_id, "node_id": local_node_id}),
            domain_ref=domain_ref,
            allowed_actions=["approve", "refuse", "postpone"],
            default_text_binding=False,
            response_route={
                "type": "event",
                "topic": _RUNTIME_ACTION_APPROVAL_RESPONSE_TOPIC,
                "target": {"type": "system", "system_id": "tool_bridge"},
            },
            metadata={
                "source": "tool_bridge",
                "action_risk": action_risk,
                "approval_contract": _approval_contract(),
                "application_access": verified_application,
                "routes": _without_empty(
                    {
                        "application": (
                            f"applications://{application_id}/access"
                            if application_id
                            else "applications://permissions"
                        ),
                        "user": (
                            f"applications://users-access/{subject_ref}"
                            if subject_ref
                            else "applications://users-access"
                        ),
                        "trusted_device": "pending-actions://trusted-device",
                    }
                ),
                "channel_affordances": {
                    "chat": "keyboard",
                    "telegram": "inline_keyboard",
                    "voice": "trusted_device_handoff",
                    "voice_text": "This action needs confirmation on a trusted device.",
                    "voice_text_i18n_key": "runtime.application_approval.voice_handoff",
                },
            },
        )
    except ValueError as exc:
        if "already exists" not in str(exc):
            raise
        existing = await _find_runtime_action_pending_action(
            webspace_id=webspace_id,
            action_id=action_id,
            domain_ref=domain_ref,
        )
        if existing:
            return existing
        raise


async def _enforce_runtime_action_gate(
    *,
    body: "ToolCall",
    skill_name: str,
    public_tool: str,
    payload: Dict[str, Any],
    target_node_id: str = "",
    local_node_id: str = "",
    forced_side_effect_class: str = "",
    approval_scope: Mapping[str, Any] | None = None,
    application_access: Mapping[str, Any] | None = None,
    ctx: AgentContext | None = None,
) -> Dict[str, Any]:
    local_resource = _runtime_action_targets_local_resource(approval_scope, payload)
    action_risk = _runtime_action_risk(
        body=body,
        skill_name=skill_name,
        public_tool=public_tool,
        payload=payload,
        target_node_id=target_node_id,
        local_node_id=local_node_id,
        forced_side_effect_class="local_write" if local_resource else forced_side_effect_class,
    )
    application_gate = dict(application_access or {})
    application_decision = _mapping(application_gate.get("decision"))
    if application_decision:
        decision = str(application_decision.get("decision") or "").strip()
        if decision == "allow":
            return {
                **action_risk,
                "approval": {
                    "status": "approve",
                    "source": "application_grant",
                    "approval_id": application_decision.get("approval_id"),
                    "grant_id": application_decision.get("grant_id"),
                    "permission_id": application_decision.get("permission_id"),
                    "application_id": application_decision.get("application_id"),
                },
                "application_access": application_gate,
            }
        if decision == "deny":
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "application_access_denied",
                    "application_id": application_decision.get("application_id"),
                    "permission_id": application_decision.get("permission_id"),
                    "reason": application_decision.get("reason_code"),
                    "policy_explanation": application_decision.get("policy_explanation"),
                    "technical_detail": {"tool": str(body.tool or "")},
                },
            )
    if not _runtime_action_gate_enabled() or (
        not bool(action_risk.get("approval_required")) and not application_decision
    ):
        return action_risk
    verified_application = _mapping(_mapping(body.context).get("_verified_application_access"))
    if verified_application:
        scope = dict(approval_scope or {})
        scope["application_access"] = verified_application
        approval_scope = scope
    grant_ref = _runtime_action_grant_ref(approval_scope, payload)
    if verified_application.get("durable_approval_allowed") is False:
        grant_ref = None
    grant_binding = {
        key: verified_application[key]
        for key in (
            "application_id",
            "release_digest",
            "permission_profile_digest",
            "subject_ref",
            "holder_ref",
        )
        if verified_application.get(key)
    }
    if grant_ref:
        grant = await asyncio.to_thread(
            find_runtime_action_grant,
            ctx or get_ctx(),
            **{
                key: grant_ref[key]
                for key in ("subject", "scope", "resource", "webspace_id")
            },
            binding=grant_binding,
        )
        if grant:
            return {
                **action_risk,
                "approval": {
                    "status": "approve",
                    "source": "durable_grant",
                    "approval_id": grant["id"],
                    "approved_by": grant.get("approved_by"),
                    "scope": grant.get("scope"),
                    "resource": grant.get("resource"),
                },
            }
    for approval in _approval_sources(payload, body.context):
        if _approval_allows_runtime_action(approval, action_risk):
            accepted = {k: v for k, v in approval.items() if k != "secret"}
            if grant_ref:
                grant = await asyncio.to_thread(
                    remember_runtime_action_grant,
                    ctx or get_ctx(),
                    **grant_ref,
                    approval_id=_first_text(approval.get("approval_id")),
                    approved_by=_approval_identity(approval),
                    binding=grant_binding,
                )
                accepted["durable_grant_id"] = grant["id"]
            return {**action_risk, "approval": accepted}
    operator_approval = None if grant_ref else _runtime_operator_ui_approval(
        payload=payload,
        context=body.context,
        action_risk=action_risk,
    )
    if operator_approval:
        return {**action_risk, "approval": operator_approval}
    pending_action: Dict[str, Any] = {}
    pending_action_error = ""
    try:
        pending_action = await _ensure_runtime_action_pending_action(
            body=body,
            skill_name=skill_name,
            public_tool=public_tool,
            payload=payload,
            action_risk=action_risk,
            target_node_id=target_node_id,
            local_node_id=local_node_id,
            approval_scope=approval_scope,
            ctx=ctx,
        )
        approval = _pending_action_approval(pending_action, action_risk)
        if approval:
            pending_reason = str(application_decision.get("reason_code") or "")
            if application_decision:
                if pending_reason == "application_grant_missing":
                    application_grant = await _materialize_approved_application_grant(
                        application_gate=application_gate,
                        approval=approval,
                        ctx=ctx,
                    )
                    if application_grant:
                        approval = {
                            **approval,
                            "source": "application_pending_action",
                            "application_grant_id": application_grant.get("grant_id"),
                        }
                    else:
                        approval = None
                elif pending_reason not in {
                    "permission_not_granted",
                    "guardian_approval_required",
                    "device_trust_required",
                    "session_trust_required",
                }:
                    approval = None
        if approval:
            if grant_ref:
                grant = await asyncio.to_thread(
                    remember_runtime_action_grant,
                    ctx or get_ctx(),
                    **grant_ref,
                    approval_id=_first_text(approval.get("pending_action_id")),
                    approved_by=_approval_identity(approval),
                    binding=grant_binding,
                )
                approval = {**approval, "durable_grant_id": grant["id"]}
            return {**action_risk, "approval": approval}
    except Exception as exc:
        pending_action_error = f"{type(exc).__name__}: {exc}"
        _log.warning("failed to publish runtime action approval pending action tool=%s", body.tool, exc_info=True)
    raise HTTPException(
        status_code=403,
        detail={
            "error": "application_approval_required" if application_decision else "action_approval_required",
            "application_id": application_decision.get("application_id"),
            "permission_id": application_decision.get("permission_id"),
            "reason": application_decision.get("reason_code"),
            "policy_explanation": application_decision.get("policy_explanation"),
            "tool": str(body.tool or ""),
            "action_risk": action_risk,
            "pending_action_id": str(pending_action.get("id") or ""),
            "pending_action_status": str(pending_action.get("status") or ""),
            "approval_contract": _approval_contract(),
            "human_message": _runtime_action_approval_presentation(
                approval_scope,
                payload,
                tool_name=str(body.tool or ""),
                risk_class=str(action_risk.get("risk_class") or "runtime"),
            )["summary"],
            "human_message_i18n": _runtime_action_approval_presentation(
                approval_scope,
                payload,
                tool_name=str(body.tool or ""),
                risk_class=str(action_risk.get("risk_class") or "runtime"),
            )["waiting_i18n"],
            **({"pending_action_error": pending_action_error} if pending_action_error else {}),
        },
    )


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


def _tool_call_max_timeout_s() -> float:
    raw = str(os.getenv("ADAOS_TOOL_CALL_MAX_TIMEOUT_S") or "600").strip()
    try:
        value = float(raw)
    except Exception:
        value = 600.0
    return max(30.0, min(value, 3600.0))


def _bounded_tool_call_timeout_s(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed <= 0.0:
        return None
    return max(1.0, min(parsed, _tool_call_max_timeout_s()))


def _request_tool_call_timeout_s(body: "ToolCall", request: Request) -> float | None:
    timeout_s = _bounded_tool_call_timeout_s(body.timeout)
    if timeout_s is not None:
        return timeout_s
    try:
        raw_ms = str(request.headers.get("X-AdaOS-Timeout-Ms") or "").strip()
    except Exception:
        raw_ms = ""
    if raw_ms:
        try:
            parsed_ms = float(raw_ms)
        except Exception:
            parsed_ms = 0.0
        if parsed_ms > 0.0:
            timeout_s = _bounded_tool_call_timeout_s(parsed_ms / 1000.0)
            if timeout_s is not None:
                return timeout_s
    try:
        raw_s = str(request.headers.get("X-AdaOS-Timeout-S") or "").strip()
    except Exception:
        raw_s = ""
    return _bounded_tool_call_timeout_s(raw_s) if raw_s else None


def _debug_autosync_enabled() -> bool:
    raw = str(os.getenv("ADAOS_TOOL_BRIDGE_WORKSPACE_AUTOSYNC") or "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    level = (os.getenv("ADAOS_LOG_LEVEL") or "").strip().upper()
    return level == "DEBUG"


def _should_autosync_workspace_runtime(
    *, tool_name: str, declared_read_only: bool = False
) -> bool:
    if not _debug_autosync_enabled():
        return False
    # The resolved manifest is authoritative. Name heuristics only exist for
    # legacy calls that do not carry a declared contract; a provider may use a
    # logical entrypoint such as ``portable_list_messages`` which is read-only
    # even though its prefix is not in the legacy vocabulary. Re-syncing such
    # calls serializes independent provider reads behind the workspace lock.
    if declared_read_only:
        return False
    if _is_readonly_snapshot_tool(tool_name):
        return False
    full_name = str(tool_name or "").strip()
    if full_name.startswith(_WORKSPACE_AUTOSYNC_EXEMPT_TOOL_PREFIXES):
        return False
    public_tool = full_name.split(":", 1)[-1]
    if _looks_readonly_tool(public_tool) or _looks_ui_navigation_tool(full_name, public_tool):
        return False
    return True


def _workspace_runtime_lock(skill_name: str) -> threading.RLock:
    key = str(skill_name or "").strip()
    with _WORKSPACE_RUNTIME_LOCKS_LOCK:
        lock = _WORKSPACE_RUNTIME_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WORKSPACE_RUNTIME_LOCKS[key] = lock
        return lock


def _workspace_runtime_guard_required(
    ctx: AgentContext,
    skill_name: str,
    *,
    tool_name: str,
    declared_read_only: bool = False,
) -> bool:
    if not _should_autosync_workspace_runtime(
        tool_name=tool_name,
        declared_read_only=declared_read_only,
    ):
        return False
    return _workspace_skill_source_exists(ctx, skill_name)


def _workspace_runtime_sync_recent(skill_name: str) -> bool:
    if _WORKSPACE_RUNTIME_SYNC_MIN_INTERVAL_S <= 0.0:
        return False
    last_at = float(_WORKSPACE_RUNTIME_LAST_SYNC_AT.get(str(skill_name or "").strip()) or 0.0)
    return last_at > 0.0 and time.monotonic() - last_at < _WORKSPACE_RUNTIME_SYNC_MIN_INTERVAL_S


def _mark_workspace_runtime_sync_attempt(skill_name: str) -> None:
    if _WORKSPACE_RUNTIME_SYNC_MIN_INTERVAL_S <= 0.0:
        return
    _WORKSPACE_RUNTIME_LAST_SYNC_AT[str(skill_name or "").strip()] = time.monotonic()


def _dev_runtime_sync_key(skill_name: str) -> str:
    return f"dev:{str(skill_name or '').strip()}"


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


def _dev_skill_source_exists(ctx: AgentContext, skill_name: str) -> bool:
    try:
        dev_root_attr = getattr(ctx.paths, "dev_skills_dir", None)
        dev_root = dev_root_attr() if callable(dev_root_attr) else dev_root_attr
        if not dev_root:
            return False
        candidate = Path(dev_root).expanduser().resolve() / skill_name
        return candidate.exists() and any(candidate.iterdir())
    except Exception:
        return False


def _implicit_dev_runtime_available(ctx: AgentContext, mgr: SkillManager, skill_name: str) -> bool:
    if _dev_skill_source_exists(ctx, skill_name):
        return True
    try:
        mgr.dev_runtime_status(skill_name)
    except RuntimeError as exc:
        if "no versions installed" in str(exc).lower():
            return False
        return True
    except Exception:
        return True
    return True


def _runtime_ready(mgr: SkillManager, skill_name: str) -> bool:
    try:
        status = mgr.runtime_status(skill_name)
    except Exception:
        return False
    if not bool(status.get("ready")):
        return False
    manifest_path = Path(str(status.get("resolved_manifest") or ""))
    if not manifest_path.exists():
        return False
    runtime_skill_root = manifest_path.parent / "src" / "skills" / skill_name
    return runtime_skill_root.exists() and any(runtime_skill_root.iterdir())


def _runtime_repair_target(mgr: SkillManager, skill_name: str) -> tuple[str | None, str | None]:
    try:
        status = mgr.runtime_status(skill_name)
    except Exception:
        return None, None
    slot = str(status.get("pending_slot") or "").strip().upper() or None
    version = str(status.get("pending_version") or "").strip() or None
    return version, slot


def _resolve_tool_webspace_id(
    payload: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> str:
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    request_context = context if isinstance(context, Mapping) else {}
    token = str(
        payload.get("webspace_id")
        or meta.get("webspace_id")
        or request_context.get("webspace_id")
        or ""
    ).strip()
    return token or default_webspace_id()


def _dev_application_project_ref(body: "ToolCall", ctx: AgentContext) -> str | None:
    """Resolve the DEV Project from the server-owned webspace selection.

    Tool arguments and request context are caller-controlled, so neither may
    select one of several Projects that share a component.  The workspace
    index is the local authority for the current DEV scenario.  Builder uses
    the same stable id for an application's Project and owned scenario; the
    Project ownership check still verifies that it owns the executing skill.
    """

    webspace_id = _resolve_tool_webspace_id(body.arguments or {}, context=body.context)
    try:
        from adaos.services.workspaces import index as workspace_index

        manifest = workspace_index.get_workspace(webspace_id)
        if manifest is None or not bool(getattr(manifest, "is_dev", False)):
            return None
        scenario_id = str(
            getattr(manifest, "current_scenario_overlay", None)
            or getattr(manifest, "home_scenario", None)
            or ""
        ).strip()
        projects_dir = getattr(getattr(ctx, "paths", None), "dev_projects_dir", None)
        if not scenario_id or not callable(projects_dir):
            return None
        project_manifest = Path(projects_dir()) / scenario_id / "project.yaml"
        if not project_manifest.is_file():
            return None
        return f"project:{scenario_id}"
    except Exception:
        _log.debug(
            "failed to resolve DEV Application Project from webspace=%s",
            webspace_id,
            exc_info=True,
        )
        return None


def _resolve_target_node_id(
    payload: Dict[str, Any],
    *,
    local_node_id: str = "",
) -> str:
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    target_node_id = node_identity_token(
        payload.get("target_node_id")
        or payload.get("node_id")
        or meta.get("target_node_id")
        or meta.get("node_target_id")
        or ""
    )
    local_token = node_identity_token(local_node_id)
    if local_token and target_node_id.lower() in {"local", "self", "current", "current_node", "home"}:
        return local_token
    return target_node_id


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
    if not target_node_id or node_identities_match(target_node_id, local_node_id):
        return False
    tool_token = str(tool_name or "").strip()
    # Some tools expose hub-side projections of member state. Even when the UI
    # is focused on a member node, their authority lives on the hub.
    if tool_token in _HUB_LOCAL_TOOL_NAMES or any(tool_token.startswith(prefix) for prefix in _HUB_LOCAL_TOOL_PREFIXES):
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
    if current_caller_scope() is not None:
        raise HTTPException(status_code=403, detail="scoped_caller_forwarding_not_supported")
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
                intent=body.intent,
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
    forward = _tool_call_forward_payload(body, payload)
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
    try:
        from adaos.services.project_deployment.materialization import (
            active_project_component,
        )

        if active_project_component(ctx, f"skill:{skill_name}"):
            return
    except Exception:
        _log.debug(
            "project component ownership probe failed for skill=%s",
            skill_name,
            exc_info=True,
        )
    if _workspace_runtime_sync_recent(skill_name):
        return
    if not _runtime_ready(mgr, skill_name):
        return
    _mark_workspace_runtime_sync_attempt(skill_name)
    try:
        result = mgr.runtime_update(skill_name, space="workspace")
    except Exception:
        _log.debug("workspace runtime_update failed for skill=%s", skill_name, exc_info=True)
        return
    if isinstance(result, dict) and result.get("ok") is False:
        _log.warning(
            "workspace runtime_update returned not ok for skill=%s reason=%s detail=%s",
            skill_name,
            result.get("reason"),
            result.get("error") or result.get("path") or result.get("source_path"),
        )
    elif isinstance(result, dict) and result.get("changed"):
        _log.info(
            "DEV runtime synchronized before tool preflight skill=%s version=%s slot=%s files=%d tools=%d",
            skill_name,
            result.get("version"),
            result.get("slot"),
            len(result.get("files") or []),
            len(result.get("tools_added") or []),
        )


def _maybe_sync_dev_runtime(ctx: AgentContext, mgr: SkillManager, skill_name: str) -> None:
    """Keep DEV execution and its preflight contract on the same source revision."""

    if not _dev_skill_source_exists(ctx, skill_name):
        return
    sync_key = _dev_runtime_sync_key(skill_name)
    with _workspace_runtime_lock(sync_key):
        if _workspace_runtime_sync_recent(sync_key):
            return
        _mark_workspace_runtime_sync_attempt(sync_key)
        if not _dev_runtime_source_changed(ctx, skill_name):
            return
        try:
            result = mgr.runtime_update(skill_name, space="dev", notify_unchanged=False)
        except Exception:
            _log.warning("DEV runtime sync failed for skill=%s", skill_name, exc_info=True)
            return
    if isinstance(result, dict) and result.get("ok") is False:
        _log.warning(
            "DEV runtime sync returned not ok for skill=%s reason=%s detail=%s",
            skill_name,
            result.get("reason"),
            result.get("error") or result.get("path") or result.get("source_path"),
        )


def _dev_runtime_source_changed(ctx: AgentContext, skill_name: str) -> bool:
    """Return whether DEV source may be newer than its active runtime marker."""

    try:
        skills_root = Path(ctx.paths.dev_skills_dir()).expanduser().resolve()
        source_root = (skills_root / str(skill_name or "").strip()).resolve()
        marker = (
            skills_root / ".runtime" / str(skill_name or "").strip() / "current_runtime.json"
        ).resolve()
    except Exception:
        return True
    if not source_root.is_dir() or not marker.is_file():
        return True
    try:
        marker_mtime_ns = int(marker.stat().st_mtime_ns)
    except OSError:
        return True
    excluded = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
    try:
        for path in source_root.rglob("*"):
            try:
                relative = path.relative_to(source_root)
            except ValueError:
                return True
            if any(part in excluded for part in relative.parts) or not path.is_file():
                continue
            if int(path.stat().st_mtime_ns) > marker_mtime_ns:
                return True
    except OSError:
        return True
    return False


def _runtime_contract_diagnostics(mgr: SkillManager, skill_name: str, *, dev: bool) -> dict[str, Any]:
    runtime_space = "dev" if dev else "workspace"
    try:
        status = mgr.dev_runtime_status(skill_name) if dev else mgr.runtime_status(skill_name)
    except Exception as exc:
        return {
            "runtime_space": runtime_space,
            "runtime_status": "unavailable",
            "runtime_error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "runtime_space": runtime_space,
        "runtime_status": "ready" if bool(status.get("ready", True)) else "not_ready",
        "runtime_version": str(status.get("version") or "").strip() or None,
        "runtime_slot": str(status.get("active_slot") or "").strip() or None,
        "resolved_manifest": str(status.get("resolved_manifest") or "").strip() or None,
    }


def _repair_workspace_runtime(
    ctx: AgentContext,
    mgr: SkillManager,
    skill_name: str,
    *,
    webspace_id: str,
) -> bool:
    if not _workspace_skill_source_exists(ctx, skill_name):
        return False
    with _workspace_runtime_lock(skill_name):
        try:
            result = mgr.runtime_update(skill_name, space="workspace")
            _mark_workspace_runtime_sync_attempt(skill_name)
        except Exception:
            _log.debug("workspace runtime_update repair failed for skill=%s", skill_name, exc_info=True)
        else:
            if isinstance(result, dict) and result.get("ok") is False:
                _log.warning(
                    "workspace runtime_update repair returned not ok for skill=%s reason=%s detail=%s",
                    skill_name,
                    result.get("reason"),
                    result.get("error") or result.get("path") or result.get("source_path"),
                )
        if _runtime_ready(mgr, skill_name):
            return True
        version, slot = _runtime_repair_target(mgr, skill_name)
        try:
            mgr.activate_for_space(skill_name, space="default", webspace_id=webspace_id, version=version, slot=slot)
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
    intent: Literal["read", "mutation"] | None = Field(
        default=None,
        description="Routing hint; the server verifies it against trusted tool metadata.",
    )
    timeout: float | None = Field(default=None)
    dev: bool = Field(default=False, description="Run tool from DEV workspace instead of installed runtime")
    idempotency_key: str | None = None
    request_id: str | None = None
    model_config = {"extra": "ignore"}


_TOOL_CONTEXT_META_KEYS = frozenset(
    {
        "webspace_id",
        "source_webspace_id",
        "request_webspace_id",
        "reply_webspace_id",
        "builder_source_webspace_id",
        "conversation_id",
        "conversation_thread_id",
        "conversation_topic_id",
        "thread_id",
        "topic_id",
        "channel_id",
        "dialog_channel_id",
        "route_id",
        "transport",
        "chat_id",
        "message_id",
        "request_id",
        "turn_trace_id",
        "input_event_kind",
        "locale",
        "language",
        "builder_context",
        "builder_topic",
    }
)
_TOOL_ACTION_CONTEXT_KEYS = frozenset(
    {"widgetId", "widgetType", "nodeId", "eventId", "button"}
)


def _project_tool_context_meta(
    meta: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project bounded routing context into the skill-call metadata plane.

    ``ToolCall.context`` used to be retained by the HTTP envelope but discarded
    before skill execution.  That silently detached conversational calls from
    their thread/project.  Only routing and presentation identity is projected;
    authority, permissions, principals, and arbitrary caller data remain out of
    ``_meta``.  Explicit argument metadata wins for compatibility with trusted
    internal callers.
    """

    projected = dict(meta or {})
    source = dict(context or {})
    for key in _TOOL_CONTEXT_META_KEYS:
        if key in source and key not in projected:
            projected[key] = copy.deepcopy(source[key])
    action_context = {
        key: copy.deepcopy(source[key])
        for key in _TOOL_ACTION_CONTEXT_KEYS
        if key in source
    }
    if action_context and "action_context" not in projected:
        projected["action_context"] = action_context
    return projected


async def _authorize_application_tool_call(
    *,
    body: ToolCall,
    request: Request,
    ctx: AgentContext,
    skill_name: str,
    public_tool: str,
    manager: SkillManager,
    declared_side_effects: str,
    component_capabilities: tuple[str, ...],
    application_contract: Mapping[str, Any],
    phase_timings: dict[str, float] | None = None,
) -> tuple[ToolCall, dict[str, Any] | None]:
    """Resolve and enforce trusted Application context before action approval."""

    if body.dev:
        if not application_contract:
            return body, None
        from adaos.services.applications.access_management import (
            ApplicationAccessManagementService,
        )
        from adaos.services.builder.application_permissions import (
            application_permissions_context,
        )
        from adaos.services.policy.application import bind_application

        actor = current_caller()
        if actor is None:
            raise HTTPException(status_code=403, detail={"error": "application_actor_missing"})
        paths = getattr(ctx, "paths", None)
        projects_dir = getattr(paths, "dev_projects_dir", None)
        skills_dir = getattr(paths, "dev_skills_dir", None)
        if not callable(projects_dir) or not callable(skills_dir):
            raise HTTPException(
                status_code=503,
                detail={"error": "dev_application_authority_unavailable"},
            )
        request_context = _mapping(body.context)
        requested_project_ref = await asyncio.to_thread(
            _dev_application_project_ref,
            body,
            ctx,
        )
        authority = await asyncio.to_thread(
            application_permissions_context,
            component_ref=f"skill:{skill_name}",
            requested_project_ref=requested_project_ref or None,
            dev_projects_root=Path(projects_dir()),
            dev_skills_root=Path(skills_dir()),
        )
        if (
            authority.get("status") != "present"
            or authority.get("authority_status") != "valid"
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "dev_application_context_invalid",
                    "reason": "project_ownership_or_permission_profile_invalid",
                    "technical_detail": {
                        "tool": body.tool,
                        "diagnostics": list(authority.get("diagnostics") or ())[:8],
                    },
                },
            )
        permission_id, app_capability = ApplicationAccessManagementService.runtime_permission(
            side_effects=declared_side_effects,
            application_access=application_contract,
            component_capabilities=component_capabilities,
        )
        declared_permissions = {
            str(item).strip().lower()
            for item in authority.get("declared") or ()
            if str(item).strip()
        }
        required_permissions = {
            str(item).strip().lower()
            for item in component_capabilities
            if str(item).strip()
        }
        if not permission_id or not required_permissions.issubset(declared_permissions):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "dev_application_permission_not_declared",
                    "permission_id": permission_id or None,
                    "technical_detail": {
                        "tool": body.tool,
                        "undeclared_permissions": sorted(
                            required_permissions - declared_permissions
                        ),
                    },
                },
            )
        project_ref = str(authority.get("project_ref") or "")
        application_id = project_ref.partition(":")[2]
        if not application_id:
            raise HTTPException(
                status_code=403,
                detail={"error": "dev_application_context_invalid"},
            )
        verified = {
            "application_id": application_id,
            "application_title": application_id,
            "runtime_source": "dev",
            "release_digest": authority.get("manifest_digest"),
            "project_ref": project_ref,
            "project_manifest_digest": authority.get("manifest_digest"),
            "permission_profile_digest": authority.get("profile_digest"),
            "permission_profile": dict(_mapping(authority.get("profile"))),
            "external_provider_declarations": list(
                _mapping(authority.get("profile")).get("external_providers") or ()
            ),
            "subject_ref": actor.ref(),
            "holder_ref": actor.ref(),
            "permission_id": permission_id,
            "app_capability": app_capability,
            "component_ref": f"skill:{skill_name}",
            "tool_ref": f"tool:{skill_name}:{public_tool}",
            "webspace_id": _resolve_tool_webspace_id(
                body.arguments or {}, context=body.context
            ),
        }
        bind_application(verified)
        updated_context = {**request_context, "_verified_application_access": verified}
        # This context establishes identity and a declaration ceiling only. DEV
        # never synthesizes an Application grant, so the normal method-level
        # action gate remains authoritative for mutating operations.
        return body.model_copy(update={"context": updated_context}), {
            "context": verified,
            "component_capabilities": list(component_capabilities),
        }
    from adaos.services.applications.access import ApplicationAccessError
    from adaos.services.applications.access_management import ApplicationAccessManagementService
    from adaos.services.applications.runtime import get_application_service
    from adaos.services.personalization_runtime import personalization_access_service

    request_context = _mapping(body.context)
    requested_application_id = _first_text(request_context.get("application_id"))
    requested_release_digest = _first_text(
        request_context.get("application_release_digest"),
        _mapping(request_context.get("runtime_selection")).get("release_digest"),
    )
    requested_scenario_id = _first_text(
        request_context.get("scenario_id"),
        request_context.get("current_scenario_id"),
        _mapping(body.arguments).get("scenario_id"),
        _mapping(body.arguments).get("current_scenario"),
        _mapping(_mapping(body.arguments).get("_meta")).get("scenario_id"),
    )
    paths = getattr(ctx, "paths", None)
    state_dir_getter = getattr(paths, "state_dir", None)
    if not callable(state_dir_getter) and not getattr(ctx, "authority_state_dir", None):
        if requested_application_id:
            raise HTTPException(status_code=503, detail={"error": "application_authority_unavailable"})
        return body, None
    state_dir = Path(getattr(ctx, "authority_state_dir", None) or state_dir_getter())
    management = ApplicationAccessManagementService(get_application_service(state_dir))
    admission_timings = phase_timings if phase_timings is not None else {}
    stage_started = time.perf_counter()
    try:
        runtime = await asyncio.to_thread(
            management.resolve_runtime_context,
            skill_name=skill_name,
            requested_application_id=requested_application_id,
            requested_release_digest=requested_release_digest,
            requested_scenario_id=requested_scenario_id,
            webspace_id=_resolve_tool_webspace_id(body.arguments or {}, context=body.context),
        )
    except ApplicationAccessError as exc:
        ambiguous = "ambiguous" in str(exc).lower()
        raise HTTPException(
            status_code=409 if ambiguous else 403,
            detail={
                "error": (
                    "application_context_ambiguous"
                    if ambiguous
                    else "application_context_invalid"
                ),
                "retryable": False,
                "technical_detail": {
                    "tool": body.tool,
                    "requested_application_id": requested_application_id or None,
                    "requested_scenario_id": requested_scenario_id or None,
                },
            },
        ) from exc
    finally:
        admission_timings["application_runtime_resolution_ms"] = (
            time.perf_counter() - stage_started
        ) * 1000.0
    if runtime is None:
        if requested_application_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "application_context_invalid",
                    "application_id": requested_application_id,
                    "technical_detail": {"tool": body.tool},
                },
            )
        return body, None

    # The cheap read-only path may skip manifest loading for legacy skills.
    # Application authorization cannot inherit that shortcut: its permission
    # must be derived from the active runtime contract, never from a tool name.
    if not declared_side_effects:
        declared_side_effects = await asyncio.to_thread(
            _declared_tool_side_effects,
            manager,
            skill_name=skill_name,
            public_tool=public_tool,
            dev=False,
        )
    if not component_capabilities:
        component_capabilities = await asyncio.to_thread(
            _declared_tool_permissions,
            manager,
            skill_name=skill_name,
            public_tool=public_tool,
            dev=False,
        )
    if not application_contract:
        application_contract = await asyncio.to_thread(
            _declared_tool_application_access,
            manager,
            skill_name=skill_name,
            public_tool=public_tool,
            dev=False,
        )

    stage_started = time.perf_counter()
    actor = current_caller()
    if actor is None:
        raise HTTPException(status_code=403, detail={"error": "application_actor_missing"})
    subject_ref = actor.ref()
    session_ref = ""
    device_ref = ""
    session_trusted = False
    device_trusted = False
    access_service = personalization_access_service(ctx)
    access_store = access_service.store
    if actor.kind == "session":
        session = await asyncio.to_thread(access_store.get_session, actor.id)
        if session:
            subject = _mapping(session.get("subject"))
            if subject.get("kind") and subject.get("id"):
                subject_ref = f"{subject['kind']}:{subject['id']}"
            session_ref = f"session:{actor.id}"
            session_trusted = str(session.get("status") or "active") == "active"
            session_device_id = _first_text(session.get("device_id"))
            if session_device_id:
                device_ref = f"device:{session_device_id}"
    header_device_id = _first_text(
        request.headers.get("X-AdaOS-Device-Id"),
        request.headers.get("x-adaos-device-id"),
    )
    if header_device_id:
        device = await asyncio.to_thread(access_store.get_device_key, header_device_id)
        subject_id = subject_ref.partition(":")[2]
        if device and str(device.get("status") or "active") == "active" and str(device.get("user_id") or "") == subject_id:
            device_ref = f"device:{header_device_id}"
            device_trusted = True
    if device_ref and not device_trusted:
        device_id = device_ref.partition(":")[2]
        device = await asyncio.to_thread(access_store.get_device_key, device_id)
        device_trusted = bool(device and str(device.get("status") or "active") == "active")
    admission_timings["application_identity_resolution_ms"] = (
        time.perf_counter() - stage_started
    ) * 1000.0
    holder_ref = session_ref or device_ref or actor.ref()
    permission_id, app_capability = management.runtime_permission(
        side_effects=declared_side_effects,
        application_access=application_contract,
        component_capabilities=component_capabilities,
    )
    if not permission_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "application_permission_mapping_missing",
                "application_id": runtime["application_id"],
                "technical_detail": {"tool": body.tool},
            },
        )
    payload = dict(body.arguments or {})
    resource_argument = _first_text(application_contract.get("resource_argument"))
    provider_argument = _first_text(application_contract.get("provider_argument"))
    actor_chain = {
        "user_ref": subject_ref if subject_ref.startswith("user:") else "",
        "subject_ref": subject_ref,
        "application_id": runtime["application_id"],
        "component_ref": f"skill:{skill_name}",
        "tool_ref": f"tool:{skill_name}:{public_tool}",
        "device_ref": device_ref,
        "device_trusted": device_trusted,
        "session_ref": session_ref,
        "session_trusted": session_trusted,
        "webspace_id": _resolve_tool_webspace_id(payload, context=body.context),
        "resource_ref": _first_text(payload.get(resource_argument)) if resource_argument else "",
        "external_provider_ref": _first_text(payload.get(provider_argument)) if provider_argument else "",
    }
    stage_started = time.perf_counter()
    decision = await asyncio.to_thread(
        management.access.decide,
        runtime["application_id"],
        release_digest=runtime["release_digest"],
        subject_ref=subject_ref,
        permission_id=permission_id,
        app_capability=app_capability,
        actor_chain=actor_chain,
        component_capabilities=component_capabilities,
    )
    admission_timings["application_access_decision_ms"] = (
        time.perf_counter() - stage_started
    ) * 1000.0
    stage_started = time.perf_counter()
    application = management.store.get_application(runtime["application_id"])
    runtime_selection = _mapping(runtime.get("runtime_selection"))
    runtime_root_ref = _first_text(runtime_selection.get("runtime_root_ref"), "workspace")
    runtime_source = "trial" if runtime_root_ref.startswith("trial:") else "workspace"
    component = next(
        (
            item
            for item in runtime["release"].project_release.components
            if item.kind == "skill" and item.artifact_id == skill_name
        ),
        None,
    )
    verified = {
        "application_id": runtime["application_id"],
        "application_title": application.display["title"],
        "release_digest": runtime["release_digest"],
        "runtime_source": runtime_source,
        "runtime_root_ref": runtime_root_ref,
        "package_digest": component.digest if component is not None else "",
        "permission_profile_digest": runtime["permission_profile_digest"],
        "subject_ref": subject_ref,
        "holder_ref": holder_ref,
        "permission_id": permission_id,
        "app_capability": app_capability,
        "component_ref": f"skill:{skill_name}",
        "tool_ref": f"tool:{skill_name}:{public_tool}",
        "device_ref": device_ref,
        "session_ref": session_ref,
        "webspace_id": actor_chain["webspace_id"],
        # The Application gate below records this exact release/subject/tool
        # decision. SDK code may reuse it for the same permission instead of
        # reparsing and rewriting the large personalization facts store.
        "_ingress_authorized_permission_id": (
            permission_id if decision.decision == "allow" else ""
        ),
    }
    if decision.grant_id:
        grant = management.store.get_application_access_grant(decision.grant_id)
        verified["durable_approval_allowed"] = bool(
            grant.constraints.get("durable_approvals", True)
        )
    admission_timings["application_projection_ms"] = (
        time.perf_counter() - stage_started
    ) * 1000.0
    from adaos.services.policy.application import bind_application

    bind_application(verified)
    updated_context = {**request_context, "_verified_application_access": verified}
    stage_started = time.perf_counter()
    await asyncio.to_thread(
        management.record_runtime_observation,
        application_id=runtime["application_id"],
        subject_ref=subject_ref,
        permission_id=permission_id,
        actor_chain=actor_chain,
        outcome=decision.decision,
        grant_id=str(decision.grant_id or ""),
        network_destination=str(actor_chain.get("external_provider_ref") or ""),
    )
    admission_timings["application_observation_ms"] = (
        time.perf_counter() - stage_started
    ) * 1000.0
    return body.model_copy(update={"context": updated_context}), {
        "decision": decision.to_dict(),
        "context": verified,
        "component_capabilities": list(component_capabilities),
    }


def _apply_application_runtime_headers(
    response: Response, application_access: Mapping[str, Any] | None
) -> None:
    context = _mapping(_mapping(application_access).get("context"))
    runtime_source = _first_text(context.get("runtime_source"))
    release_digest = _first_text(context.get("release_digest"))
    package_digest = _first_text(context.get("package_digest"))
    if runtime_source:
        response.headers["X-AdaOS-Runtime-Source"] = runtime_source
    if release_digest:
        response.headers["X-AdaOS-Release-Digest"] = release_digest
    if package_digest:
        response.headers["X-AdaOS-Package-Digest"] = package_digest


@router.post("/tools/call", dependencies=[Depends(require_tool_caller)])
async def call_tool(body: ToolCall, request: Request, response: Response, ctx: AgentContext = Depends(get_ctx)):
    from adaos.services.policy.caller import verified_caller
    from adaos.services.policy.application import clear_application

    state = getattr(request, "state", None)
    actor = getattr(state, "adaos_verified_caller", None)
    scope = getattr(state, "adaos_verified_caller_scope", None)
    with verified_caller(actor, scope):
        try:
            return await _call_tool_with_identity(body, request, response, ctx)
        finally:
            clear_application()


def _tool_attachment_result(value: Any) -> dict[str, Any]:
    result = value.get("result") if isinstance(value, Mapping) and "result" in value else value
    if isinstance(result, Mapping) and isinstance(result.get("blob"), Mapping):
        result = result["blob"]
    return dict(result) if isinstance(result, Mapping) else {}


def _tool_attachment_ref(
    *,
    skill_name: str,
    read_tool: str,
    logical_name: str,
    digest: str,
    filename: str,
    webspace_id: str,
    dev: bool,
) -> str:
    hex_digest = digest.removeprefix("sha256:")
    query = []
    if webspace_id:
        query.append(f"webspace_id={quote(webspace_id, safe='')}")
    if dev:
        query.append("dev=true")
    suffix = f"?{'&'.join(query)}" if query else ""
    return (
        f"/api/tools/{quote(skill_name, safe='')}/{quote(read_tool, safe='')}/attachments/"
        f"{quote(logical_name, safe='')}/{hex_digest}/{quote(filename, safe='')}{suffix}"
    )


async def _read_tool_attachment_body(request: Request) -> bytes:
    from adaos.services.storage.upload_context import MAX_TOOL_ATTACHMENT_BYTES

    content_length = str(request.headers.get("content-length") or "").strip()
    if content_length:
        try:
            if int(content_length) > MAX_TOOL_ATTACHMENT_BYTES:
                raise HTTPException(status_code=413, detail="attachment exceeds 10 MiB")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content length") from exc
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_TOOL_ATTACHMENT_BYTES:
            raise HTTPException(status_code=413, detail="attachment exceeds 10 MiB")
        content.extend(chunk)
    return bytes(content)


@router.put(
    "/tools/{skill_name}/{upload_tool}/attachments",
    dependencies=[Depends(require_tool_caller)],
)
async def upload_tool_attachment(
    skill_name: str,
    upload_tool: str,
    request: Request,
    response: Response,
    field_id: str,
    filename: str,
    read_tool: str,
    webspace_id: str = "",
    dev: bool = False,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    """Run an authorized binary-ingress tool and return an app-owned blob ref."""

    from adaos.services.policy.application import clear_application
    from adaos.services.policy.caller import verified_caller
    from adaos.services.storage.upload_context import VerifiedToolUpload, verified_tool_upload

    skill = str(skill_name or "").strip()
    upload = str(upload_tool or "").strip()
    read = str(read_tool or "").strip()
    if not skill or not upload or not read or any(":" in item for item in (skill, upload, read)):
        raise HTTPException(status_code=400, detail="invalid attachment tool identity")
    try:
        admitted = VerifiedToolUpload(
            filename=filename,
            field_id=field_id,
            media_type=str(request.headers.get("content-type") or "application/octet-stream"),
            content=await _read_tool_attachment_body(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    arguments = {**admitted.metadata(), "webspace_id": webspace_id}
    body = ToolCall(
        tool=f"{skill}:{upload}",
        arguments=arguments,
        context={"webspace_id": webspace_id, "binary_ingress": "attachment"},
        intent="mutation",
        dev=dev,
    )
    state = getattr(request, "state", None)
    actor = getattr(state, "adaos_verified_caller", None)
    scope = getattr(state, "adaos_verified_caller_scope", None)
    with verified_caller(actor, scope), verified_tool_upload(admitted):
        try:
            envelope = await _call_tool_with_identity(body, request, response, ctx)
        finally:
            clear_application()
    receipt = _tool_attachment_result(envelope)
    if (
        receipt.get("digest") != admitted.digest
        or int(receipt.get("size_bytes") or -1) != admitted.size_bytes
        or receipt.get("owner_ref") != f"skill:{skill}"
        or not str(receipt.get("ref") or "").startswith("adaos-blob:")
    ):
        raise HTTPException(status_code=409, detail="attachment tool returned an invalid blob receipt")
    logical_name = str(receipt.get("logical_name") or "").strip()
    if not logical_name:
        raise HTTPException(status_code=409, detail="attachment tool omitted its logical blob store")
    return {
        "ok": True,
        "ref": _tool_attachment_ref(
            skill_name=skill,
            read_tool=read,
            logical_name=logical_name,
            digest=admitted.digest,
            filename=admitted.filename,
            webspace_id=webspace_id,
            dev=dev,
        ),
        "blob_ref": receipt["ref"],
        "sha256": admitted.digest.removeprefix("sha256:"),
        "size_bytes": admitted.size_bytes,
        "name": admitted.filename,
    }


async def _tool_attachment_store(
    body: ToolCall,
    *,
    logical_name: str,
    ctx: AgentContext,
):
    from adaos.domain.blob_storage import BlobStorageRequirements
    from adaos.services.applications.runtime_selection import selected_trial
    from adaos.services.skill.data_paths import resolve_skill_data_root
    from adaos.services.storage.blob import get_blob_storage_broker

    skill_name = body.tool.partition(":")[0]
    webspace = _resolve_tool_webspace_id(body.arguments or {}, context=body.context)
    trial_runtime = None if body.dev else await asyncio.to_thread(
        selected_trial,
        ctx,
        webspace,
        "skill",
        skill_name,
    )
    manager = (
        await asyncio.to_thread(trial_runtime.ready_manager, skill_name)
        if trial_runtime is not None
        else await _skill_manager_for_context(ctx)
    )
    implicit_dev = trial_runtime is None and not body.dev and await asyncio.to_thread(
        _webspace_uses_dev_runtime,
        body.arguments or {},
    )
    use_dev = body.dev or (
        implicit_dev
        and await asyncio.to_thread(_implicit_dev_runtime_available, ctx, manager, skill_name)
    )
    if use_dev:
        await asyncio.to_thread(_maybe_sync_dev_runtime, ctx, manager, skill_name)
        status = await asyncio.to_thread(manager.dev_runtime_status, skill_name)
    else:
        status = await asyncio.to_thread(manager.runtime_status, skill_name)
    manifest_path = Path(str(status.get("resolved_manifest") or "")).resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError("active skill runtime manifest is unavailable")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = Path(str(manifest.get("source") or "")).resolve()
    if not source.is_dir():
        raise FileNotFoundError("active skill runtime source is unavailable")
    runtime_ctx = getattr(manager, "ctx", ctx)
    data_root = resolve_skill_data_root(
        runtime_ctx,
        SimpleNamespace(name=skill_name, path=source),
    )
    broker = get_blob_storage_broker(runtime_ctx)
    binding = broker.bind(
        owner_ref=f"skill:{skill_name}",
        logical_name=logical_name,
        requirements=BlobStorageRequirements(),
        scope_root=data_root / "files",
    )
    return broker, binding


@router.get(
    "/tools/{skill_name}/{read_tool}/attachments/{logical_name}/{digest}/{filename}",
    dependencies=[Depends(require_tool_caller)],
)
async def download_tool_attachment(
    skill_name: str,
    read_tool: str,
    logical_name: str,
    digest: str,
    filename: str,
    request: Request,
    response: Response,
    webspace_id: str = "",
    dev: bool = False,
    ctx: AgentContext = Depends(get_ctx),
) -> FileResponse:
    """Authorize a read-tool before serving one verified app-owned blob."""

    from adaos.services.policy.application import clear_application
    from adaos.services.policy.caller import verified_caller
    from adaos.services.storage.upload_context import attachment_filename

    try:
        safe_filename = attachment_filename(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    hex_digest = str(digest or "").strip().lower()
    if len(hex_digest) != 64 or any(character not in "0123456789abcdef" for character in hex_digest):
        raise HTTPException(status_code=400, detail="invalid attachment digest")
    body = ToolCall(
        tool=f"{skill_name}:{read_tool}",
        arguments={
            "ref": f"sha256:{hex_digest}",
            "field_id": "attachment",
            "webspace_id": webspace_id,
        },
        context={"webspace_id": webspace_id, "binary_egress": "attachment"},
        intent="read",
        dev=dev,
    )
    state = getattr(request, "state", None)
    actor = getattr(state, "adaos_verified_caller", None)
    scope = getattr(state, "adaos_verified_caller_scope", None)
    with verified_caller(actor, scope):
        try:
            envelope = await _call_tool_with_identity(body, request, response, ctx)
        finally:
            clear_application()
    authorization = _tool_attachment_result(envelope)
    if authorization.get("ref") != f"sha256:{hex_digest}" or authorization.get("ok") is False:
        raise HTTPException(status_code=403, detail="attachment read tool did not authorize this object")
    try:
        broker, binding = await _tool_attachment_store(body, logical_name=logical_name, ctx=ctx)
        path = await asyncio.to_thread(
            broker.materialize_digest,
            binding,
            f"sha256:{hex_digest}",
            owner_ref=f"skill:{skill_name}",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="attachment not found") from exc
    except (ValueError, NotImplementedError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    guessed = mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
    allowed_inline = {"image/png", "image/jpeg", "image/gif", "image/webp"}
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600",
    }
    headers.update(
        {
            key: value
            for key, value in response.headers.items()
            if key.lower().startswith("x-adaos-")
        }
    )
    return FileResponse(
        path,
        media_type=guessed if guessed in allowed_inline else "application/octet-stream",
        filename=safe_filename,
        content_disposition_type="inline" if guessed in allowed_inline else "attachment",
        headers=headers,
    )


async def _authorize_scoped_tool_call(
    body: ToolCall,
    ctx: AgentContext,
    trial_runtime=None,
    *,
    resolved_manager=None,
) -> None:
    from adaos.domain.personalization_access import ScopeRef
    from adaos.services.personalization_runtime import personalization_access_service
    from adaos.services.policy.caller import current_caller

    scope = current_caller_scope()
    if scope is None:
        return
    skill, separator, tool = body.tool.partition(":")
    if not separator or not skill or not tool or scope != ScopeRef("skill", skill) or body.dev:
        raise HTTPException(status_code=403, detail="caller_credential_scope_mismatch")
    routing = dict(body.arguments or {})
    if not str(routing.get("webspace_id") or "").strip():
        routing["webspace_id"] = (body.context or {}).get("webspace_id")
    if await asyncio.to_thread(_webspace_uses_dev_runtime, routing):
        raise HTTPException(status_code=403, detail="scoped_caller_dev_runtime_not_supported")
    manager = resolved_manager or (
        await asyncio.to_thread(trial_runtime.ready_manager, skill)
        if trial_runtime is not None
        else await _skill_manager_for_context(ctx)
    )
    effects = await asyncio.to_thread(_declared_tool_side_effects, manager, skill_name=skill, public_tool=tool, dev=False)
    if not effects:
        raise HTTPException(status_code=403, detail="scoped_tool_effects_undeclared")
    action = "workspace.read" if _declared_side_effects_are_read_only(effects) else "workspace.write"
    actor = current_caller()
    def evaluate():
        return personalization_access_service(ctx).evaluate(actor=actor, action=action, scope=scope,
            resource=f"skill:{skill}", audit_success=action != "workspace.read")
    decision = await asyncio.to_thread(evaluate)
    if decision.decision != "allow":
        raise HTTPException(status_code=403, detail={"error": "caller_access_denied", "reason": decision.reason_code})


async def _call_tool_with_identity(body: ToolCall, request: Request, response: Response, ctx: AgentContext):
    # Authorization precedes cached results as well as new execution.
    from adaos.services.applications.runtime_selection import selected_trial
    from adaos.services.applications.trial_runtime import TrialRuntimeUnavailable
    from adaos.services.applications.runtime_channel import RuntimeChannelConflict

    request_started_at = time.perf_counter()
    outer_timings: dict[str, float] = {"request_started_at": request_started_at}
    request_context = _mapping(body.context)
    webspace = str(request_context.get("webspace_id") or "").strip() or _resolve_tool_webspace_id(
        body.arguments or {}, context=body.context
    )
    routing = dict(body.arguments or {})
    if webspace:
        routing["webspace_id"] = webspace
    implicit_dev_webspace = (not body.dev) and await asyncio.to_thread(
        _webspace_uses_dev_runtime,
        routing,
    )
    stage_started = time.perf_counter()
    try:
        # A DEV webspace is authoritative for its preview rail. Selecting a
        # production Trial first can otherwise bind one declarative read to an
        # older beta runtime while the rest of the page uses DEV.
        trial_runtime = None if body.dev or implicit_dev_webspace else await asyncio.to_thread(selected_trial, ctx, webspace, "skill", body.tool.partition(":")[0])
        resolved_manager = None
        if trial_runtime is not None:
            if body.dev:
                raise TrialRuntimeUnavailable("A production Trial selection cannot execute DEV tools")
            resolved_manager = await asyncio.to_thread(
                trial_runtime.ready_manager,
                body.tool.partition(":")[0],
            )
            context = dict(body.context or {})
            context["runtime_selection"] = trial_runtime.identity(body.tool.partition(":")[0])
            body = body.model_copy(update={"context": context})
            response.headers["X-AdaOS-Runtime-Source"] = "trial"
            response.headers["X-AdaOS-Release-Digest"] = trial_runtime.release_digest
            response.headers["X-AdaOS-Package-Digest"] = context["runtime_selection"]["package_digest"]
    except (TrialRuntimeUnavailable, FileNotFoundError, RuntimeChannelConflict) as exc:
        raise HTTPException(status_code=409, detail={"error": "trial_runtime_unavailable", "message": str(exc)}) from exc
    finally:
        outer_timings["runtime_selection_ms"] = (
            time.perf_counter() - stage_started
        ) * 1000.0
    stage_started = time.perf_counter()
    if resolved_manager is None:
        await _authorize_scoped_tool_call(body, ctx, trial_runtime)
    else:
        await _authorize_scoped_tool_call(
            body,
            ctx,
            trial_runtime,
            resolved_manager=resolved_manager,
        )
    outer_timings["scope_admission_ms"] = (
        time.perf_counter() - stage_started
    ) * 1000.0
    stage_started = time.perf_counter()
    await asyncio.to_thread(_reject_unavailable_trial_execution, body, ctx)
    outer_timings["availability_admission_ms"] = (
        time.perf_counter() - stage_started
    ) * 1000.0
    resolved_timeout = _request_tool_call_timeout_s(body, request)
    if resolved_timeout is not None and resolved_timeout != body.timeout:
        body = body.model_copy(update={"timeout": resolved_timeout})
    mode, key, entry = _tool_call_idempotency_begin(body, request)
    if mode == "cached" and entry is not None:
        return _tool_call_idempotency_replay(entry, response)
    if mode == "wait" and entry is not None:
        return await _tool_call_idempotency_wait(entry, response)
    if mode != "owner" or entry is None:
        impl_options = {
            "trial_runtime": trial_runtime,
            "outer_timings": outer_timings,
        }
        if resolved_manager is not None:
            impl_options["resolved_manager"] = resolved_manager
        return await _call_tool_impl(body, request, response, ctx, **impl_options)
    try:
        response.headers["X-AdaOS-Idempotency-Key"] = key
    except Exception:
        pass
    try:
        impl_options = {
            "trial_runtime": trial_runtime,
            "outer_timings": outer_timings,
        }
        if resolved_manager is not None:
            impl_options["resolved_manager"] = resolved_manager
        result = await _call_tool_impl(body, request, response, ctx, **impl_options)
    except HTTPException as exc:
        try:
            headers = dict(exc.headers or {})
            headers.setdefault("X-AdaOS-Idempotency-Key", key)
            exc.headers = headers
        except Exception:
            pass
        _tool_call_idempotency_store_http_error(entry, exc)
        raise
    except Exception as exc:
        _tool_call_idempotency_store_runtime_error(entry, exc)
        raise
    _tool_call_idempotency_store_result(entry, result)
    return result


def _existing_trial_preview_target(body: ToolCall, ctx: AgentContext) -> dict[str, Any] | None:
    paths = getattr(ctx, "paths", None)
    if paths is None:
        return None
    from adaos.services.builder.workbench import BuilderWorkbenchService

    webspace = _resolve_tool_webspace_id(body.arguments or {}, context=body.context)
    return BuilderWorkbenchService(state_dir=Path(paths.state_dir())).existing_preview_target(webspace)


def _reject_unavailable_trial_execution(body: ToolCall, ctx: AgentContext) -> None:
    """Never run a Trial's tool against mutable DEV or stable source as fallback."""
    target = _existing_trial_preview_target(body, ctx)
    if not target or target.get("stage") != "trial":
        return
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore

    state_dir = Path(ctx.paths.state_dir())
    activation = TrialActivationStore(state_dir / "artifact_pipeline/trial-activations").find_for_target(
        scenario_id=str(target.get("object_id") or ""),
        revision=target.get("candidate_id") or target.get("revision"))
    skill = body.tool.partition(":")[0]
    if activation and not any(item.get("kind") == "skill" and item.get("artifact_id") == skill
                              for item in activation.get("package_refs", [])):
        return
    raise HTTPException(status_code=409, detail={
        "error": "trial_runtime_unavailable",
        "message": "The selected Trial has no admitted isolated skill executor. DEV and stable fallback are disabled.",
        "candidate_id": (activation or {}).get("candidate_ref", {}).get("candidate_id"),
    })


async def _call_tool_impl(
    body: ToolCall,
    request: Request,
    response: Response,
    ctx: AgentContext = Depends(get_ctx),
    *,
    trial_runtime=None,
    resolved_manager=None,
    outer_timings: Mapping[str, float] | None = None,
):
    from adaos.services.applications.runtime_channel import RuntimeChannelConflict
    call_started_at = float(
        _mapping(outer_timings).get("request_started_at") or time.perf_counter()
    )
    phase_timings: dict[str, float] = {
        key: float(value)
        for key, value in _mapping(outer_timings).items()
        if key != "request_started_at"
    }
    # Разбираем "<skill_name>:<public_tool_name>"
    if ":" not in body.tool:
        raise HTTPException(status_code=400, detail="tool must be in '<skill_name>:<public_tool_name>' format")

    skill_name, public_tool = body.tool.split(":", 1)
    if not skill_name or not public_tool:
        raise HTTPException(status_code=400, detail="invalid tool spec")

    # Используем общий путь исполнения как в CLI (SkillManager.run_tool)
    payload: Dict[str, Any] = dict(body.arguments or {})
    routing_payload = dict(payload)
    routing_context = _mapping(body.context)
    context_webspace_id = str(routing_context.get("webspace_id") or "").strip()
    if context_webspace_id:
        routing_payload["webspace_id"] = context_webspace_id
    implicit_dev_webspace = (not body.dev) and await asyncio.to_thread(
        _webspace_uses_dev_runtime,
        routing_payload,
    )
    if current_caller_scope() is not None and (body.dev or implicit_dev_webspace):
        raise HTTPException(status_code=403, detail="scoped_caller_dev_runtime_not_supported")

    phase_started = time.perf_counter()
    mgr = resolved_manager or (
        await asyncio.to_thread(trial_runtime.ready_manager, skill_name)
        if trial_runtime is not None
        else await _skill_manager_for_context(ctx)
    )
    phase_timings["manager_resolution_ms"] = (
        time.perf_counter() - phase_started
    ) * 1000.0
    if trial_runtime is not None:
        implicit_dev_webspace = False
    if implicit_dev_webspace and await asyncio.to_thread(
        _implicit_dev_runtime_available,
        ctx,
        mgr,
        skill_name,
    ):
        body = body.model_copy(update={"dev": True})

    if body.dev:
        await asyncio.to_thread(_maybe_sync_dev_runtime, ctx, mgr, skill_name)

    accepting_new_work = is_accepting_new_work()
    # Preserve the cheap legacy path for obviously read-only calls while the
    # runtime is ready. A read intent or a lifecycle exception, however, must
    # always be authorized from the active resolved manifest.
    if (
        trial_runtime is None
        and not body.dev
        and accepting_new_work
        and body.intent != "read"
        and _looks_readonly_tool(public_tool)
    ):
        declared_side_effects = ""
        declared_approval_scope: dict[str, Any] = {}
        declared_component_permissions: tuple[str, ...] = ()
        declared_application_access: dict[str, Any] = {}
    else:
        phase_started = time.perf_counter()
        declared_contract = await asyncio.to_thread(
            _declared_tool_contract,
            mgr,
            skill_name=skill_name,
            public_tool=public_tool,
            dev=bool(body.dev),
        )
        declared_side_effects = str(declared_contract.get("side_effects") or "")
        declared_approval_scope = dict(
            declared_contract.get("approval_scope")
            if isinstance(declared_contract.get("approval_scope"), Mapping)
            else {}
        )
        declared_component_permissions = tuple(
            str(item)
            for item in declared_contract.get("permissions") or ()
            if str(item)
        )
        declared_application_access = dict(
            declared_contract.get("application_access")
            if isinstance(declared_contract.get("application_access"), Mapping)
            else {}
        )
        phase_timings["contract_resolution_ms"] = (
            time.perf_counter() - phase_started
        ) * 1000.0
    trusted_read_only = _declared_side_effects_are_read_only(declared_side_effects)
    if body.intent == "read" and not trusted_read_only:
        runtime_contract = await asyncio.to_thread(
            _runtime_contract_diagnostics,
            mgr,
            skill_name,
            dev=bool(body.dev),
        )
        _log.warning(
            "tool intent mismatch tool=%s requested=read declared=%s runtime_space=%s version=%s slot=%s manifest=%s",
            body.tool,
            declared_side_effects or "undeclared",
            runtime_contract.get("runtime_space"),
            runtime_contract.get("runtime_version"),
            runtime_contract.get("runtime_slot"),
            runtime_contract.get("resolved_manifest"),
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "tool_intent_mismatch",
                "tool": body.tool,
                "requested_intent": "read",
                "declared_side_effects": declared_side_effects or "undeclared",
                "runtime_space": runtime_contract.get("runtime_space"),
                "runtime_version": runtime_contract.get("runtime_version"),
                "runtime_slot": runtime_contract.get("runtime_slot"),
                "runtime_status": runtime_contract.get("runtime_status"),
                "retryable": False,
            },
        )
    if not accepting_new_work and not trusted_read_only:
        raise HTTPException(
            status_code=503,
            detail={"error": "node_draining", "tool": body.tool, "retryable": True},
        )

    trace = attach_http_trace_headers(request.headers, response.headers)
    context = _mapping(body.context)
    meta = _project_tool_context_meta(_mapping(payload.get("_meta")), context)
    action_source = _first_text(meta.get("action_source"), context.get("action_source"))
    if not action_source:
        meta["action_source"] = "api_tool_call"
        meta.setdefault("origin_label", "API")
    meta.setdefault("tool", body.tool)
    meta.setdefault("tool_name", body.tool)
    if body.idempotency_key:
        meta.setdefault("idempotency_key", body.idempotency_key)
    if body.request_id:
        meta.setdefault("request_id", body.request_id)
    payload["_meta"] = meta
    webspace_id = _resolve_tool_webspace_id(payload)
    conf = getattr(ctx, "config", None)
    local_node_id = node_identity_token(getattr(conf, "node_id", ""))
    target_node_id = _resolve_target_node_id(payload, local_node_id=local_node_id)
    if trial_runtime is not None and target_node_id and target_node_id != local_node_id:
        raise HTTPException(status_code=409, detail={"error": "trial_runtime_unavailable", "message": "Trial node forwarding is not admitted"})
    phase_started = time.perf_counter()
    body, application_access = await _authorize_application_tool_call(
        body=body,
        request=request,
        ctx=ctx,
        skill_name=skill_name,
        public_tool=public_tool,
        manager=mgr,
        declared_side_effects=declared_side_effects,
        component_capabilities=declared_component_permissions,
        application_contract=declared_application_access,
        phase_timings=phase_timings,
    )
    phase_timings["application_admission_ms"] = (
        time.perf_counter() - phase_started
    ) * 1000.0
    _apply_application_runtime_headers(response, application_access)
    gate_started_at = time.perf_counter()
    action_risk = await _enforce_runtime_action_gate(
        body=body,
        skill_name=skill_name,
        public_tool=public_tool,
        payload=payload,
        target_node_id=target_node_id,
        local_node_id=local_node_id,
        forced_side_effect_class=declared_side_effects,
        approval_scope=declared_approval_scope,
        application_access=application_access,
        ctx=ctx,
    )
    mutating_call = _action_risk_may_mutate(action_risk)
    gate_done_at = time.perf_counter()
    phase_timings["action_admission_ms"] = (
        gate_done_at - gate_started_at
    ) * 1000.0
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
    local_execution_started = False
    try:
        started_at = time.perf_counter()
        local_timings: Dict[str, float] = {}
        if trial_runtime is None and not body.dev:
            service_started_at = time.perf_counter()
            service_started = await _ensure_on_demand_service_started(skill_name)
            if service_started:
                local_timings["service_startup_ms"] = (
                    time.perf_counter() - service_started_at
                ) * 1000.0

        def _run_local_tool_unlocked() -> Any:
            nonlocal local_execution_started
            if trial_runtime is not None:
                from adaos.services.agent_context import use_ctx

                local_execution_started = True
                with use_ctx(mgr.ctx):
                    return mgr.run_tool(skill_name, public_tool, payload, timeout=body.timeout)
            if not body.dev and _should_autosync_workspace_runtime(
                tool_name=body.tool,
                declared_read_only=trusted_read_only,
            ):
                stage_started = time.perf_counter()
                _maybe_sync_workspace_runtime(ctx, mgr, skill_name)
                local_timings["autosync_ms"] = (time.perf_counter() - stage_started) * 1000.0
            if (
                not body.dev
                and mutating_call
                and _workspace_skill_source_exists(ctx, skill_name)
                and not _runtime_ready(mgr, skill_name)
            ):
                prepare_started = time.perf_counter()
                prepared = _repair_workspace_runtime(ctx, mgr, skill_name, webspace_id=webspace_id)
                local_timings["prepare_ms"] = (time.perf_counter() - prepare_started) * 1000.0
                if not prepared:
                    raise FileNotFoundError(f"workspace runtime for skill '{skill_name}' is not ready")
            stage_started = time.perf_counter()
            if body.dev:
                try:
                    local_execution_started = True
                    return mgr.run_dev_tool(skill_name, public_tool, payload, timeout=body.timeout)
                finally:
                    local_timings["run_tool_ms"] = (time.perf_counter() - stage_started) * 1000.0
            try:
                local_execution_started = True
                return mgr.run_tool(skill_name, public_tool, payload, timeout=body.timeout)
            finally:
                local_timings["run_tool_ms"] = (time.perf_counter() - stage_started) * 1000.0

        def _run_local_tool() -> Any:
            guard_required = mutating_call or _workspace_runtime_guard_required(
                ctx,
                skill_name,
                tool_name=body.tool,
                declared_read_only=trusted_read_only,
            )
            if body.dev or not guard_required:
                return _run_local_tool_unlocked()
            lock_started = time.perf_counter()
            with _workspace_runtime_lock(skill_name):
                local_timings["workspace_lock_ms"] = (time.perf_counter() - lock_started) * 1000.0
                return _run_local_tool_unlocked()

        result = await anyio.to_thread.run_sync(_run_local_tool)
        took_ms = (time.perf_counter() - started_at) * 1000.0
        total_ms = (time.perf_counter() - call_started_at) * 1000.0
        admission_ms = sum(
            float(phase_timings.get(key) or 0.0)
            for key in (
                "runtime_selection_ms",
                "scope_admission_ms",
                "availability_admission_ms",
                "application_admission_ms",
                "action_admission_ms",
            )
        )
        skill_startup_ms = float(local_timings.get("service_startup_ms") or 0.0) + float(
            local_timings.get("prepare_ms") or 0.0
        )
        server_timing = (
            f"admission;dur={admission_ms:.1f}, "
            f"skill-startup;dur={skill_startup_ms:.1f}, "
            f"skill-dispatch;dur={float(local_timings.get('run_tool_ms') or 0.0):.1f}"
        )
        response.headers["Server-Timing"] = server_timing
        _log.debug(
            "tools.call profile tool=%s total_ms=%.1f runtime_selection_ms=%.1f "
            "scope_admission_ms=%.1f availability_admission_ms=%.1f "
            "manager_resolution_ms=%.1f contract_resolution_ms=%.1f "
            "application_admission_ms=%.1f application_runtime_resolution_ms=%.1f "
            "application_identity_resolution_ms=%.1f application_access_decision_ms=%.1f "
            "application_projection_ms=%.1f application_observation_ms=%.1f "
            "action_admission_ms=%.1f "
            "workspace_lock_ms=%.1f autosync_ms=%.1f skill_startup_ms=%.1f "
            "skill_dispatch_ms=%.1f",
            body.tool,
            total_ms,
            float(phase_timings.get("runtime_selection_ms") or 0.0),
            float(phase_timings.get("scope_admission_ms") or 0.0),
            float(phase_timings.get("availability_admission_ms") or 0.0),
            float(phase_timings.get("manager_resolution_ms") or 0.0),
            float(phase_timings.get("contract_resolution_ms") or 0.0),
            float(phase_timings.get("application_admission_ms") or 0.0),
            float(phase_timings.get("application_runtime_resolution_ms") or 0.0),
            float(phase_timings.get("application_identity_resolution_ms") or 0.0),
            float(phase_timings.get("application_access_decision_ms") or 0.0),
            float(phase_timings.get("application_projection_ms") or 0.0),
            float(phase_timings.get("application_observation_ms") or 0.0),
            float(phase_timings.get("action_admission_ms") or 0.0),
            float(local_timings.get("workspace_lock_ms") or 0.0),
            float(local_timings.get("autosync_ms") or 0.0),
            skill_startup_ms,
            float(local_timings.get("run_tool_ms") or 0.0),
        )
        if took_ms >= 2000 or total_ms >= 2000:
            _log.warning(
                "tools.call slow tool=%s dev=%s total_ms=%.1f pre_local_ms=%.1f "
                "runtime_selection_ms=%.1f scope_admission_ms=%.1f "
                "availability_admission_ms=%.1f manager_resolution_ms=%.1f "
                "contract_resolution_ms=%.1f application_admission_ms=%.1f "
                "action_admission_ms=%.1f local_total_ms=%.1f workspace_lock_ms=%.1f "
                "autosync_ms=%.1f prepare_ms=%.1f run_tool_ms=%.1f",
                body.tool,
                body.dev,
                total_ms,
                (started_at - call_started_at) * 1000.0,
                float(phase_timings.get("runtime_selection_ms") or 0.0),
                float(phase_timings.get("scope_admission_ms") or 0.0),
                float(phase_timings.get("availability_admission_ms") or 0.0),
                float(phase_timings.get("manager_resolution_ms") or 0.0),
                float(phase_timings.get("contract_resolution_ms") or 0.0),
                float(phase_timings.get("application_admission_ms") or 0.0),
                float(phase_timings.get("action_admission_ms") or 0.0),
                took_ms,
                float(local_timings.get("workspace_lock_ms") or 0.0),
                float(local_timings.get("autosync_ms") or 0.0),
                float(local_timings.get("prepare_ms") or 0.0),
                float(local_timings.get("run_tool_ms") or 0.0),
            )
    except CallerAccessDenied as exc:
        raise HTTPException(status_code=403, detail={"error": "caller_access_denied", "reason": str(exc)}) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tool_permission_denied",
                "tool": body.tool,
                "reason": str(exc) or "permission_denied",
                "retryable": False,
            },
        ) from exc
    except RuntimeChannelConflict as exc:
        raise HTTPException(status_code=409, detail={"error": "application_runtime_inactive",
            "message": str(exc), "retryable": False}) from exc
    except (FileNotFoundError, RuntimeError, KeyError) as e:
        if trial_runtime is not None:
            raise HTTPException(status_code=409, detail={"error": "trial_execution_failed", "message": str(e), "retryable": False}) from e
        local_runtime_resolved = local_execution_started and await asyncio.to_thread(
            _runtime_ready,
            mgr,
            skill_name,
        )
        if local_execution_started and (mutating_call or local_runtime_resolved):
            missing_domain_value = isinstance(e, (FileNotFoundError, KeyError))
            raise HTTPException(
                status_code=409 if mutating_call else (404 if missing_domain_value else 500),
                detail={
                    "error": (
                        "tool_execution_failed_no_retry"
                        if mutating_call
                        else ("tool_domain_value_not_found" if missing_domain_value else "tool_execution_failed")
                    ),
                    "tool": body.tool,
                    "retryable": False,
                    "detail": str(e),
                },
            ) from e
        # Если локально не найден навык/слот — попробуем проксировать на участника подсети (только если роль hub)
        if current_caller_scope() is not None:
            raise HTTPException(status_code=403, detail="scoped_caller_forwarding_not_supported") from e
        if not conf or conf.role != "hub":
            # На member нет прокси — вернём исходную ошибку
            raise HTTPException(status_code=404, detail=str(e))

        # Найти online-ноду с этим skill (используем только runtime; workspace-fallback отключён)
        directory = get_directory()
        candidates = await asyncio.to_thread(
            directory.find_nodes_with_skill,
            skill_name,
            require_online=True,
        )
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
        await _enforce_runtime_action_gate(
            body=body,
            skill_name=skill_name,
            public_tool=public_tool,
            payload=payload,
            target_node_id=target_node_id,
            local_node_id=local_node_id,
            forced_side_effect_class=(
                "safe"
                if trusted_read_only or _looks_readonly_tool(public_tool)
                else ("" if _is_readonly_snapshot_tool(body.tool) else "cross_node")
            ),
            approval_scope=declared_approval_scope,
            ctx=ctx,
        )

        if target_node_id and mgr.is_connected(target_node_id):
            try:
                res = await mgr.rpc_tools_call(
                    target_node_id,
                    tool=body.tool,
                    arguments=payload,
                    timeout=body.timeout,
                    dev=body.dev,
                    intent=body.intent,
                )
                return {"ok": True, "result": res, "trace_id": trace}
            except Exception as exc:
                rpc_error = exc
        else:
            rpc_error = None

        base_url = target.get("base_url") or await asyncio.to_thread(
            directory.get_node_base_url,
            target_node_id,
        )
        if _is_loopback_base_url(base_url):
            if target_node_id and node_identities_match(target_node_id, local_node_id):
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
            if target_node_id and node_identities_match(target_node_id, local_node_id):
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
        forward = _tool_call_forward_payload(body, payload)
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
