# src\adaos\api\tool_bridge.py
import copy
import hashlib
import json
import logging
import os
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
from adaos.services.pending_actions import list_pending_actions, publish_pending_action
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
    "infra_access_skill:",
    "infrastate_skill:",
)
_HUB_LOCAL_TOOL_NAMES: tuple[str, ...] = (
    "prompt_engineer_skill:prompt_select_project",
)
_UI_NAVIGATION_TOOL_NAMES: tuple[str, ...] = (
    "prompt_engineer_skill:prompt_select_project",
)
_LOCAL_WRITE_TOOL_NAMES: tuple[str, ...] = (
    "prompt_engineer_skill:prompt_save_base_tz",
    "prompt_engineer_skill:prompt_append_tz_addendum",
    "prompt_engineer_skill:prompt_save_project_file",
    "prompt_engineer_skill:prompt_set_workflow_state",
    "prompt_engineer_skill:prompt_create_dev_project",
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
    side_effect_class = forced_side_effect_class or explicit
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
        elif effective_target and effective_target != str(local_node_id or "").strip():
            if any(tool_name.startswith(prefix) for prefix in _HUB_LOCAL_TOOL_PREFIXES):
                side_effect_class = "local_write"
                include_node_targets = False
            else:
                side_effect_class = "cross_node"
        elif effective_target and effective_target == str(local_node_id or "").strip():
            include_node_targets = False
    elif side_effect_class in {"safe", "read_only", "readonly", "local_write", "ui_navigation"}:
        include_node_targets = False
    normalized_side_effect = str(side_effect_class or "").strip().lower().replace("-", "_")
    ignore_freeform = normalized_side_effect in {"safe", "read_only", "readonly", "local_write", "ui_navigation"}
    if normalized_side_effect in {"safe", "read_only", "readonly"}:
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
            "webspace_id": _resolve_tool_webspace_id(payload),
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
    return _without_empty(
        {
            "tool": str(body.tool or ""),
            "skill": skill_name,
            "public_tool": public_tool,
            "webspace_id": _resolve_tool_webspace_id(payload),
            "target_node_id": str(target_node_id or _resolve_target_node_id(payload) or "").strip(),
            "risk_class": str(action_risk.get("risk_class") or "").strip(),
            "arguments_sha256": fingerprint,
        }
    )


def _runtime_action_pending_action_id(domain_ref: Dict[str, Any]) -> str:
    fingerprint = hashlib.sha256(_stable_json(domain_ref).encode("utf-8")).hexdigest()[:24]
    return f"pa.runtime_action.{fingerprint}"


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


def _find_runtime_action_pending_action(
    *,
    webspace_id: str,
    action_id: str,
    domain_ref: Dict[str, Any],
) -> Dict[str, Any] | None:
    try:
        snapshot = list_pending_actions(webspace_id=webspace_id, include_terminal=True)
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


def _ensure_runtime_action_pending_action(
    *,
    body: "ToolCall",
    skill_name: str,
    public_tool: str,
    payload: Dict[str, Any],
    action_risk: Dict[str, Any],
    target_node_id: str,
    local_node_id: str,
    ctx: AgentContext | None = None,
) -> Dict[str, Any]:
    webspace_id = _resolve_tool_webspace_id(payload)
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
    existing = _find_runtime_action_pending_action(webspace_id=webspace_id, action_id=action_id, domain_ref=domain_ref)
    if existing:
        return existing
    risk_class = str(action_risk.get("risk_class") or "runtime").strip() or "runtime"
    tool_name = str(body.tool or "").strip()
    try:
        return publish_pending_action(
            ctx=ctx or get_ctx(),
            webspace_id=webspace_id,
            action_id=action_id,
            kind=_RUNTIME_ACTION_APPROVAL_KIND,
            title="Action approval required",
            title_i18n={"key": "pending_actions.runtime.action_approval_title"},
            summary=f"Approve {tool_name} ({risk_class}) before it runs.",
            summary_i18n={
                "key": "pending_actions.runtime.action_approval_summary",
                "params": {"tool": tool_name, "risk_class": risk_class},
            },
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
            },
        )
    except ValueError as exc:
        if "already exists" not in str(exc):
            raise
        existing = _find_runtime_action_pending_action(webspace_id=webspace_id, action_id=action_id, domain_ref=domain_ref)
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
    ctx: AgentContext | None = None,
) -> Dict[str, Any]:
    action_risk = _runtime_action_risk(
        body=body,
        skill_name=skill_name,
        public_tool=public_tool,
        payload=payload,
        target_node_id=target_node_id,
        local_node_id=local_node_id,
        forced_side_effect_class=forced_side_effect_class,
    )
    if not _runtime_action_gate_enabled() or not bool(action_risk.get("approval_required")):
        return action_risk
    for approval in _approval_sources(payload, body.context):
        if _approval_allows_runtime_action(approval, action_risk):
            return {**action_risk, "approval": {k: v for k, v in approval.items() if k != "secret"}}
    pending_action: Dict[str, Any] = {}
    pending_action_error = ""
    try:
        pending_action = await anyio.to_thread.run_sync(
            lambda: _ensure_runtime_action_pending_action(
                body=body,
                skill_name=skill_name,
                public_tool=public_tool,
                payload=payload,
                action_risk=action_risk,
                target_node_id=target_node_id,
                local_node_id=local_node_id,
                ctx=ctx,
            )
        )
        approval = _pending_action_approval(pending_action, action_risk)
        if approval:
            return {**action_risk, "approval": approval}
    except Exception as exc:
        pending_action_error = f"{type(exc).__name__}: {exc}"
        _log.warning("failed to publish runtime action approval pending action tool=%s", body.tool, exc_info=True)
    raise HTTPException(
        status_code=403,
        detail={
            "error": "action_approval_required",
            "tool": str(body.tool or ""),
            "action_risk": action_risk,
            "pending_action_id": str(pending_action.get("id") or ""),
            "pending_action_status": str(pending_action.get("status") or ""),
            "approval_contract": _approval_contract(),
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


def _debug_autosync_enabled() -> bool:
    raw = str(os.getenv("ADAOS_TOOL_BRIDGE_WORKSPACE_AUTOSYNC") or "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    level = (os.getenv("ADAOS_LOG_LEVEL") or "").strip().upper()
    return level == "DEBUG"


def _should_autosync_workspace_runtime(*, tool_name: str) -> bool:
    if not _debug_autosync_enabled():
        return False
    if _is_readonly_snapshot_tool(tool_name):
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


def _workspace_runtime_guard_required(ctx: AgentContext, skill_name: str, *, tool_name: str) -> bool:
    if not _should_autosync_workspace_runtime(tool_name=tool_name):
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
    await _enforce_runtime_action_gate(
        body=body,
        skill_name=skill_name,
        public_tool=public_tool,
        payload=payload,
        target_node_id=target_node_id,
        local_node_id=local_node_id,
        ctx=ctx,
    )
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
        def _run_local_tool_unlocked() -> Any:
            if not body.dev and _should_autosync_workspace_runtime(tool_name=body.tool):
                _maybe_sync_workspace_runtime(ctx, mgr, skill_name)
            if body.dev:
                return mgr.run_dev_tool(skill_name, public_tool, payload, timeout=body.timeout)
            try:
                return mgr.run_tool(skill_name, public_tool, payload, timeout=body.timeout)
            except (FileNotFoundError, RuntimeError, KeyError):
                if not _repair_workspace_runtime(ctx, mgr, skill_name, webspace_id=webspace_id):
                    raise
                return mgr.run_tool(skill_name, public_tool, payload, timeout=body.timeout)

        def _run_local_tool() -> Any:
            if body.dev or not _workspace_runtime_guard_required(ctx, skill_name, tool_name=body.tool):
                return _run_local_tool_unlocked()
            with _workspace_runtime_lock(skill_name):
                return _run_local_tool_unlocked()

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
        await _enforce_runtime_action_gate(
            body=body,
            skill_name=skill_name,
            public_tool=public_tool,
            payload=payload,
            target_node_id=target_node_id,
            local_node_id=local_node_id,
            forced_side_effect_class="" if _is_readonly_snapshot_tool(body.tool) else "cross_node",
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
