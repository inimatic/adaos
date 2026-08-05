from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections.abc import Iterable, Mapping
import atexit
import asyncio
import base64
from functools import partial
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import traceback

import y_py as Y
import yaml

from adaos.domain.project_events import (
    BUILDER_CONTEXT_SELECTED,
    PROJECT_CONTENT_CHANGED,
    ProjectEventIdentity,
    legacy_project_event_topic,
)
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.capacity import get_local_capacity
from adaos.services.node_config import load_config
from adaos.services.node_display import node_display_from_config, node_display_from_directory_node
from adaos.services.yjs.doc import (
    get_ydoc,
    async_get_ydoc,
    async_read_ydoc,
    mutate_live_room,
    try_read_live_map_value,
)
from adaos.services.scenarios import loader as scenarios_loader
from adaos.services.runtime_environment import runtime_environment_payload
from adaos.services.browser_assets import (
    BrowserAssetPublishError,
    publish_scenario_resource_descriptor,
    publish_skill_resource_descriptor,
    publish_system_resource_descriptors,
)
from adaos.services.yjs.webspace import default_webspace_id
from adaos.services.workspaces import index as workspace_index
from adaos.services.yjs.store import get_ystore_for_webspace, ystore_write_metadata, ystore_write_metadata_sync
from adaos.services.yjs.bootstrap import ensure_webspace_seeded_from_scenario
from adaos.services.yjs.seed import SEED
from adaos.services.eventbus import emit
from adaos.services.webui_contract import (
    log_webui_contract_issues,
    validate_application_ui_contract,
)
from adaos.sdk.core.decorators import subscribe
from .node_data_scope import local_unscoped_data_path, node_scope_data_path
from .webspace_components import (
    MaterializationExecutorOwner,
    WebspaceCacheState,
    WebspaceProjectionService,
    WebspaceScenarioSwitchingService,
    WebspaceTaskState,
)
from .workflow_runtime import ScenarioWorkflowRuntime

_log = logging.getLogger("adaos.scenario.webspace_runtime")
_WS_ID_RE = re.compile(r"[^a-zA-Z0-9-_]+")
_TASK_STATE = WebspaceTaskState()
_CACHE_STATE = WebspaceCacheState()
_MATERIALIZATION_EXECUTOR = MaterializationExecutorOwner()
_PROJECTION_SERVICE = WebspaceProjectionService()
_SCENARIO_SWITCHING = WebspaceScenarioSwitchingService()
_SCENARIO_SWITCH_REBUILD_TASKS = _TASK_STATE.scenario_switch_rebuild_tasks
_WEBSPACE_REBUILD_STATUS = _TASK_STATE.webspace_rebuild_status
_WEBSPACE_RECOVERY_COMMAND_CACHE = _TASK_STATE.webspace_recovery_command_cache
_WEBSPACE_RECOVERY_COMMAND_CACHE_LIMIT = 256
_SKILL_RUNTIME_REBUILD_TASKS = _TASK_STATE.skill_runtime_rebuild_tasks
_SKILL_RUNTIME_REBUILD_PENDING = _TASK_STATE.skill_runtime_rebuild_pending
_SKILL_RUNTIME_REBUILD_STATS = _TASK_STATE.skill_runtime_rebuild_stats
# Compatibility scalar for callers that still inspect or reset the historical
# module-level task directly. Scheduling keeps it synchronized with TaskState.
_WEBSPACE_LISTING_SYNC_TASK: asyncio.Task[Any] | None = None
_WORKFLOW_SYNC_TASKS = _TASK_STATE.workflow_sync_tasks
_WORKFLOW_SYNC_PENDING = _TASK_STATE.workflow_sync_pending
_WORKFLOW_SYNC_STATS = _TASK_STATE.workflow_sync_stats
_LIVE_ROOM_REFRESH_TASKS = _TASK_STATE.live_room_refresh_tasks
_LIVE_ROOM_REFRESH_PENDING = _TASK_STATE.live_room_refresh_pending
_LIVE_ROOM_REFRESH_STATS = _TASK_STATE.live_room_refresh_stats
_BUILDER_YSTORE_BACKUP_TASKS = _TASK_STATE.builder_ystore_backup_tasks
_WEBUI_DECL_CACHE = _CACHE_STATE.webui_declarations
_SKILL_DECLS_CACHE_TTL_S = 300.0
_SKILL_DECLS_CACHE = _CACHE_STATE.skill_declarations
_SKILL_SOURCE_FINGERPRINT_CACHE_TTL_S = 600.0
_SKILL_SOURCE_FINGERPRINT_CACHE = _CACHE_STATE.skill_source_fingerprints
_MEMBER_SNAPSHOT_REBUILD_AT = _TASK_STATE.member_snapshot_rebuild_at
_MEMBER_SNAPSHOT_REBUILD_TASKS = _TASK_STATE.member_snapshot_rebuild_tasks
_MEMBER_SNAPSHOT_REBUILD_DELAYED_TASKS = _TASK_STATE.member_snapshot_rebuild_delayed_tasks
_MEMBER_SNAPSHOT_REBUILD_DIRTY = _TASK_STATE.member_snapshot_rebuild_dirty


def _is_control_flow_base_exception(exc: BaseException) -> bool:
    return isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit))
_MEMBER_SNAPSHOT_REBUILD_STATS = _TASK_STATE.member_snapshot_rebuild_stats
_MEMBER_SNAPSHOT_REBUILD_MATERIAL_FINGERPRINT = _TASK_STATE.member_snapshot_rebuild_material_fingerprint
_RESOLVED_WEBSPACE_CACHE = _CACHE_STATE.resolved_webspaces
_RESOLVED_WEBSPACE_CACHE_LIMIT = 16
_MATERIALIZED_WEBSPACE_CACHE = _CACHE_STATE.materialized_webspaces
_MATERIALIZED_WEBSPACE_CACHE_LIMIT = 8
_MATERIALIZED_WEBSPACE_DISK_CACHE_SCHEMA = "adaos.webspace.materialized_worker_cache.v1"
_DESKTOP_SCENARIOS_CACHE_TTL_S = 30.0
_DESKTOP_SCENARIOS_CACHE = _CACHE_STATE.desktop_scenarios
_LOCAL_NODE_DISPLAY_CACHE_TTL_S = 2.0
_EFFECTIVE_BRANCH_PATHS = (
    "ui.application",
    "data.catalog",
    "data.installed",
    "data.desktop",
    "data.webio",
    "data.routing",
    "registry.merged",
    "runtime.environment",
)
_DEFAULT_MATERIALIZATION_REQUIRED_BRANCHES = (
    "ui.application",
    "data.catalog",
    "data.installed",
    "data.desktop",
    "data.webio",
    "data.routing",
    "registry.merged",
)


def _normalize_materialization_token(value: Any, *, fallback: str = "") -> str:
    token = str(value or "").strip()
    fallback_token = str(fallback or "").strip()
    if not token:
        token = fallback_token
    token = re.sub(r"[^a-zA-Z0-9_.-]+", "_", token)
    normalized = token.strip("._-")
    if normalized:
        return normalized
    return fallback_token


def _normalize_materialization_roles(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items: Iterable[Any] = value.split(",")
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, str, Mapping)):
        raw_items = value
    else:
        raw_items = []
    roles: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        token = str(raw or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        roles.append(token)
    roles.sort()
    return roles


def canonical_materialization_identity(
    *,
    webspace_id: str,
    scenario_id: str,
    revision: str | None = None,
    source_fingerprint: str | None = None,
    user_id: str | None = None,
    roles: Any = None,
    policy_fingerprint: str | None = None,
) -> dict[str, Any]:
    """
    Build the access-scoped identity for a resolved effective view.

    This is deliberately safe to expose in diagnostics. Guests are normalized
    to ``user_id=guest`` so cached views cannot accidentally cross privilege
    boundaries when the caller does not provide authenticated identity.
    """
    webspace_token = _normalize_materialization_token(webspace_id, fallback="default")
    scenario_token = _normalize_materialization_token(scenario_id, fallback="web_desktop")
    revision_token = _normalize_materialization_token(revision, fallback="")
    source_token = _normalize_materialization_token(source_fingerprint, fallback="")
    user_token = _normalize_materialization_token(user_id, fallback="guest")
    if not user_token or user_token == "unknown":
        user_token = "guest"
    role_list = _normalize_materialization_roles(roles)
    roles_hash = hashlib.sha1(
        json.dumps(role_list, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    version_token = revision_token or source_token or "current"
    if revision_token and source_token:
        version_token = f"{revision_token}.{source_token[:12]}"
    policy_token = _normalize_materialization_token(policy_fingerprint, fallback="")
    key = f"{webspace_token}:{scenario_token}:{version_token}:{user_token}:roles-{roles_hash}"
    if policy_token:
        key = f"{key}:policy-{policy_token[:12]}"
    key_hash = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return {
        "schema": "adaos.materialization.identity.v1",
        "key": key,
        "key_hash": key_hash,
        "webspace_id": webspace_token,
        "scenario_id": scenario_token,
        "revision": revision_token or None,
        "source_fingerprint": source_token or None,
        "user_id": user_token,
        "guest": user_token == "guest",
        "roles": role_list,
        "roles_hash": roles_hash,
        "policy_fingerprint": policy_token or None,
    }


def _normalize_materialization_required_branches(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        raw_items = value.get("required_branches")
        if raw_items is None:
            raw_items = value.get("requiredBranches")
    else:
        raw_items = value
    if isinstance(raw_items, str):
        raw_items = [item.strip() for item in raw_items.split(",")]
    if not isinstance(raw_items, Iterable) or isinstance(raw_items, (bytes, bytearray, str)):
        return []
    allowed_roots = {"ui", "data", "registry", "runtime"}
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        token = str(raw or "").strip().replace("/", ".")
        parts = [part.strip() for part in token.split(".") if part.strip()]
        if len(parts) < 2 or parts[0] not in allowed_roots:
            continue
        path = ".".join(parts)
        if path in seen:
            continue
        seen.add(path)
        normalized.append(path)
    return normalized


def _scenario_materialization_contract(
    scenario_id: str | None,
    *,
    source_mode: str,
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    token = str(scenario_id or "").strip()
    loader_space = _scenario_loader_space(source_mode)
    manifest: Mapping[str, Any] = {}
    if token:
        try:
            manifest = scenarios_loader.read_manifest(token, space=loader_space)
        except Exception:
            manifest = {}
    materialization = manifest.get("materialization") if isinstance(manifest, Mapping) else None
    required = _normalize_materialization_required_branches(materialization)
    source = "scenario_manifest"
    if not required:
        runtime = manifest.get("runtime") if isinstance(manifest, Mapping) else None
        yjs_runtime = runtime.get("yjs") if isinstance(runtime, Mapping) else None
        required = _normalize_materialization_required_branches(yjs_runtime)
        source = "runtime.yjs" if required else "default"
    if not required:
        required = list(_DEFAULT_MATERIALIZATION_REQUIRED_BRANCHES)
    return {
        "required_branches": required,
        "source": source,
        "scenario_id": token or None,
        **({"identity": dict(identity), "key": identity.get("key"), "key_hash": identity.get("key_hash")} if isinstance(identity, Mapping) else {}),
    }


def _member_snapshot_rebuild_min_interval_s() -> float:
    raw = str(os.getenv("ADAOS_MEMBER_SNAPSHOT_REBUILD_MIN_INTERVAL_S", "") or "").strip()
    if raw:
        try:
            return max(0.0, min(float(raw), 60.0))
        except Exception:
            pass
    return 5.0


def _skill_runtime_rebuild_debounce_s() -> float:
    raw = str(os.getenv("ADAOS_SKILL_RUNTIME_REBUILD_DEBOUNCE_S", "") or "").strip()
    if raw:
        try:
            return max(0.0, min(float(raw), 30.0))
        except Exception:
            pass
    return 1.5


def _skill_runtime_rebuild_stats(webspace_id: str) -> Dict[str, Any]:
    key = str(webspace_id or "").strip() or default_webspace_id()
    stats = _SKILL_RUNTIME_REBUILD_STATS.get(key)
    if stats is None:
        stats = {
            "requested_total": 0,
            "scheduled_total": 0,
            "coalesced_total": 0,
            "completed_total": 0,
            "failed_total": 0,
        }
        _SKILL_RUNTIME_REBUILD_STATS[key] = stats
    return stats


def _merge_skill_runtime_rebuild_request(
    *,
    webspace_id: str,
    action: str,
    source_of_truth: str,
    reason: str,
) -> Dict[str, Any]:
    key = str(webspace_id or "").strip() or default_webspace_id()
    pending = _SKILL_RUNTIME_REBUILD_PENDING.get(key)
    if pending is None:
        pending = {
            "webspace_id": key,
            "actions": [],
            "reasons": [],
            "source_of_truth": str(source_of_truth or "").strip() or "skill_runtime",
            "requested_at": time.time(),
            "updated_at": time.time(),
            "request_count": 0,
        }
        _SKILL_RUNTIME_REBUILD_PENDING[key] = pending
    action_token = str(action or "").strip() or "skill_runtime_sync"
    reason_token = str(reason or "").strip() or action_token
    pending["request_count"] = int(pending.get("request_count") or 0) + 1
    pending["updated_at"] = time.time()
    if action_token not in list(pending.get("actions") or []):
        pending.setdefault("actions", []).append(action_token)
    if reason_token not in list(pending.get("reasons") or []):
        pending.setdefault("reasons", []).append(reason_token)
    return pending


def _coalesced_skill_runtime_action(actions: list[str]) -> str:
    normalized = [str(item or "").strip() for item in actions if str(item or "").strip()]
    unique = list(dict.fromkeys(normalized))
    if len(unique) == 1:
        return unique[0]
    if any(item in unique for item in {"skill_rollback_sync", "skill_uninstall_sync"}):
        return "skill_runtime_sync"
    if any(item == "skill_update_sync" for item in unique):
        return "skill_update_sync"
    if any(item == "skill_activation_sync" for item in unique):
        return "skill_activation_sync"
    return "skill_runtime_sync"


def schedule_skill_runtime_rebuild(
    *,
    webspace_id: str | None = None,
    action: str = "skill_runtime_sync",
    source_of_truth: str = "skill_runtime",
    reason: str = "",
) -> Dict[str, Any]:
    key = str(webspace_id or "").strip() or default_webspace_id()
    invalidate_webspace_materialization_cache(
        key,
        reason=reason or action,
        action=action,
        source_of_truth=source_of_truth,
    )
    stats = _skill_runtime_rebuild_stats(key)
    stats["requested_total"] = int(stats.get("requested_total") or 0) + 1
    pending = _merge_skill_runtime_rebuild_request(
        webspace_id=key,
        action=action,
        source_of_truth=source_of_truth,
        reason=reason or action,
    )
    existing = _SKILL_RUNTIME_REBUILD_TASKS.get(key)
    if existing is not None and not existing.done():
        stats["coalesced_total"] = int(stats.get("coalesced_total") or 0) + 1
        return {
            "scheduled": True,
            "mode": "coalesced",
            "webspace_id": key,
            "action": action,
            "source_of_truth": source_of_truth,
            "pending_count": int(pending.get("request_count") or 0),
            "task": existing.get_name(),
        }
    return _start_skill_runtime_rebuild_task(key, stats=stats, pending=pending)


def _start_skill_runtime_rebuild_task(
    webspace_id: str,
    *,
    stats: Dict[str, Any] | None = None,
    pending: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    key = str(webspace_id or "").strip() or default_webspace_id()
    stats = stats or _skill_runtime_rebuild_stats(key)
    pending = pending or _SKILL_RUNTIME_REBUILD_PENDING.get(key) or {}
    stats["scheduled_total"] = int(stats.get("scheduled_total") or 0) + 1
    task = asyncio.create_task(
        _run_skill_runtime_rebuild_coalesced(key),
        name=f"skill-runtime-rebuild:{key}",
    )
    _SKILL_RUNTIME_REBUILD_TASKS[key] = task
    return {
        "scheduled": True,
        "mode": "coalesced",
        "webspace_id": key,
        "action": _coalesced_skill_runtime_action(list(pending.get("actions") or [])),
        "source_of_truth": str(pending.get("source_of_truth") or "skill_runtime"),
        "pending_count": int(pending.get("request_count") or 0),
        "task": task.get_name(),
    }


async def _run_skill_runtime_rebuild_coalesced(webspace_id: str) -> None:
    key = str(webspace_id or "").strip() or default_webspace_id()
    stats = _skill_runtime_rebuild_stats(key)
    try:
        while True:
            delay_s = _skill_runtime_rebuild_debounce_s()
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            pending = _SKILL_RUNTIME_REBUILD_PENDING.pop(key, None)
            if not pending:
                return
            actions = list(pending.get("actions") or [])
            reasons = list(pending.get("reasons") or [])
            action = _coalesced_skill_runtime_action(actions)
            source_of_truth = str(pending.get("source_of_truth") or "skill_runtime").strip() or "skill_runtime"
            request_count = int(pending.get("request_count") or 0)
            _log.info(
                "running coalesced skill runtime rebuild webspace=%s action=%s requests=%s actions=%s reasons=%s",
                key,
                action,
                request_count,
                ",".join(str(item) for item in actions) or "-",
                ",".join(str(item) for item in reasons[:8]) or "-",
            )
            try:
                await rebuild_webspace_from_sources(
                    key,
                    action=action,
                    source_of_truth=source_of_truth,
                )
                stats["completed_total"] = int(stats.get("completed_total") or 0) + 1
            except Exception:
                stats["failed_total"] = int(stats.get("failed_total") or 0) + 1
                _log.warning(
                    "coalesced skill runtime rebuild failed webspace=%s action=%s",
                    key,
                    action,
                    exc_info=True,
                )
            if key not in _SKILL_RUNTIME_REBUILD_PENDING:
                return
    finally:
        current = asyncio.current_task()
        if _SKILL_RUNTIME_REBUILD_TASKS.get(key) is current:
            _SKILL_RUNTIME_REBUILD_TASKS.pop(key, None)
        if key in _SKILL_RUNTIME_REBUILD_PENDING and key not in _SKILL_RUNTIME_REBUILD_TASKS:
            _start_skill_runtime_rebuild_task(key)


def _member_snapshot_rebuild_request_id(*, webspace_id: str, node_id: str) -> str:
    webspace_token = str(webspace_id or "").strip() or default_webspace_id()
    node_token = str(node_id or "").strip() or "member"
    return f"member-snapshot-rebuild:{webspace_token}:{node_token}:{time.time_ns()}"


def _member_snapshot_desktop_material_fingerprint(node_id: str) -> str:
    try:
        from adaos.services.registry.subnet_directory import get_directory

        node = get_directory().get_node(str(node_id or "").strip()) or {}
    except Exception:
        return ""
    runtime_projection = node.get("runtime_projection") if isinstance(node.get("runtime_projection"), Mapping) else {}
    snapshot = runtime_projection.get("snapshot") if isinstance(runtime_projection.get("snapshot"), Mapping) else {}
    catalog = snapshot.get("desktop_catalog") if isinstance(snapshot.get("desktop_catalog"), Mapping) else {}
    if not catalog:
        return ""
    material = {
        "apps": catalog.get("apps") if isinstance(catalog.get("apps"), list) else [],
        "widgets": catalog.get("widgets") if isinstance(catalog.get("widgets"), list) else [],
        "registry": catalog.get("registry") if isinstance(catalog.get("registry"), Mapping) else {},
        "resources": catalog.get("resources") if isinstance(catalog.get("resources"), Mapping) else {},
        "webio": catalog.get("webio") if isinstance(catalog.get("webio"), Mapping) else {},
        "ydoc_defaults": catalog.get("ydoc_defaults") if isinstance(catalog.get("ydoc_defaults"), Mapping) else {},
    }
    try:
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        return ""
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _member_snapshot_rebuild_stats(task_key: str) -> Dict[str, Any]:
    stats = _MEMBER_SNAPSHOT_REBUILD_STATS.get(task_key)
    if stats is None:
        stats = {
            "requested_total": 0,
            "scheduled_total": 0,
            "coalesced_running_total": 0,
            "coalesced_interval_total": 0,
            "rerun_total": 0,
            "delayed_total": 0,
            "completed_total": 0,
            "last_reason": "",
            "last_requested_at": 0.0,
            "last_scheduled_at": 0.0,
            "last_completed_at": 0.0,
            "last_request_id": "",
            "current_request_id": "",
            "last_completed_request_id": "",
            "last_delayed_request_id": "",
        }
        _MEMBER_SNAPSHOT_REBUILD_STATS[task_key] = stats
    return stats


def member_snapshot_rebuild_runtime_snapshot(*, limit: int = 25) -> Dict[str, Any]:
    items: list[Dict[str, Any]] = []
    now_ts = time.time()
    for task_key, raw_stats in list(_MEMBER_SNAPSHOT_REBUILD_STATS.items()):
        if not isinstance(raw_stats, dict):
            continue
        node_id, _, webspace_id = str(task_key or "").partition("\0")
        dirty = dict(_MEMBER_SNAPSHOT_REBUILD_DIRTY.get(task_key) or {})
        task = _MEMBER_SNAPSHOT_REBUILD_TASKS.get(task_key)
        delayed = _MEMBER_SNAPSHOT_REBUILD_DELAYED_TASKS.get(task_key)
        last_requested_at = float(raw_stats.get("last_requested_at") or 0.0)
        last_completed_at = float(raw_stats.get("last_completed_at") or 0.0)
        items.append(
            {
                "task_key": task_key,
                "node_id": node_id or "member",
                "webspace_id": webspace_id or default_webspace_id(),
                "requested_total": int(raw_stats.get("requested_total") or 0),
                "scheduled_total": int(raw_stats.get("scheduled_total") or 0),
                "rerun_total": int(raw_stats.get("rerun_total") or 0),
                "completed_total": int(raw_stats.get("completed_total") or 0),
                "coalesced_running_total": int(raw_stats.get("coalesced_running_total") or 0),
                "coalesced_interval_total": int(raw_stats.get("coalesced_interval_total") or 0),
                "delayed_total": int(raw_stats.get("delayed_total") or 0),
                "last_reason": str(raw_stats.get("last_reason") or "").strip() or None,
                "last_request_id": str(raw_stats.get("last_request_id") or "").strip() or None,
                "current_request_id": str(raw_stats.get("current_request_id") or "").strip() or None,
                "last_completed_request_id": str(raw_stats.get("last_completed_request_id") or "").strip() or None,
                "last_delayed_request_id": str(raw_stats.get("last_delayed_request_id") or "").strip() or None,
                "last_requested_at": last_requested_at or None,
                "last_requested_age_s": round(max(0.0, now_ts - last_requested_at), 3) if last_requested_at > 0.0 else None,
                "last_completed_at": last_completed_at or None,
                "last_completed_age_s": round(max(0.0, now_ts - last_completed_at), 3) if last_completed_at > 0.0 else None,
                "active": bool(task is not None and not task.done()),
                "delayed_active": bool(delayed is not None and not delayed.done()),
                "dirty_pending": bool(dirty),
                "dirty_reason": str(dirty.get("last_reason") or "").strip() or None,
                "dirty_mode": str(dirty.get("last_mode") or "").strip() or None,
                "dirty_count": int(dirty.get("count") or 0),
                "dirty_requested_at": dirty.get("last_requested_at"),
                "dirty_request_id": str(dirty.get("last_request_id") or "").strip() or None,
                "correlation_id": (
                    str(raw_stats.get("current_request_id") or "").strip()
                    or str(dirty.get("last_request_id") or "").strip()
                    or str(raw_stats.get("last_request_id") or "").strip()
                    or None
                ),
            }
        )
    items.sort(
        key=lambda item: (
            0 if bool(item.get("active")) else 1,
            0 if bool(item.get("dirty_pending")) else 1,
            -int(item.get("requested_total") or 0),
            -float(item.get("last_requested_at") or 0.0),
            str(item.get("webspace_id") or ""),
        )
    )
    capped = items[: max(1, int(limit or 1))]
    return {
        "tracked_key_total": len(items),
        "active_total": sum(1 for item in items if bool(item.get("active"))),
        "delayed_total": sum(1 for item in items if bool(item.get("delayed_active"))),
        "dirty_total": sum(1 for item in items if bool(item.get("dirty_pending"))),
        "items": capped,
    }


def _member_snapshot_rebuild_reason(evt: Any, payload: Any) -> str:
    event_type = str(
        getattr(evt, "type", "")
        or (payload.get("type") if isinstance(payload, Mapping) else "")
        or "subnet.member.snapshot.changed"
    ).strip()
    source = str(
        getattr(evt, "source", "")
        or (payload.get("source") if isinstance(payload, Mapping) else "")
        or ""
    ).strip()
    if source:
        return f"{event_type}:{source}"
    return event_type


def _mark_member_snapshot_rebuild_dirty(*, task_key: str, reason: str, mode: str, request_id: str | None = None) -> None:
    dirty = _MEMBER_SNAPSHOT_REBUILD_DIRTY.get(task_key)
    if dirty is None:
        dirty = {
            "count": 0,
            "last_reason": "",
            "last_mode": "",
            "last_requested_at": 0.0,
            "last_request_id": "",
        }
    dirty["count"] = int(dirty.get("count") or 0) + 1
    dirty["last_reason"] = str(reason or "").strip() or "subnet.member.snapshot.changed"
    dirty["last_mode"] = str(mode or "").strip() or "coalesced"
    dirty["last_requested_at"] = time.time()
    dirty["last_request_id"] = str(request_id or "").strip() or str(dirty.get("last_request_id") or "").strip()
    _MEMBER_SNAPSHOT_REBUILD_DIRTY[task_key] = dirty
    stats = _member_snapshot_rebuild_stats(task_key)
    if mode == "task_running":
        stats["coalesced_running_total"] = int(stats.get("coalesced_running_total") or 0) + 1
    else:
        stats["coalesced_interval_total"] = int(stats.get("coalesced_interval_total") or 0) + 1
    stats["last_reason"] = dirty["last_reason"]
    stats["last_requested_at"] = dirty["last_requested_at"]
    stats["last_request_id"] = str(dirty.get("last_request_id") or "").strip()


def _schedule_member_snapshot_rebuild_delayed(
    *,
    webspace_id: str,
    node_id: str,
    delay_s: float,
    reason: str,
    request_id: str | None = None,
) -> None:
    task_key = f"{str(node_id or '').strip()}\0{str(webspace_id or '').strip()}"
    existing = _MEMBER_SNAPSHOT_REBUILD_DELAYED_TASKS.get(task_key)
    if existing is not None and not existing.done():
        return
    delayed_request_id = str(request_id or "").strip() or _member_snapshot_rebuild_request_id(
        webspace_id=webspace_id,
        node_id=node_id,
    )
    stats = _member_snapshot_rebuild_stats(task_key)
    stats["last_delayed_request_id"] = delayed_request_id

    async def _runner() -> None:
        try:
            await asyncio.sleep(max(0.0, float(delay_s)))
            if task_key not in _MEMBER_SNAPSHOT_REBUILD_DIRTY:
                return
            current = _MEMBER_SNAPSHOT_REBUILD_TASKS.get(task_key)
            if current is not None and not current.done():
                return
            _MEMBER_SNAPSHOT_REBUILD_AT[task_key] = time.monotonic()
            stats = _member_snapshot_rebuild_stats(task_key)
            stats["delayed_total"] = int(stats.get("delayed_total") or 0) + 1
            delayed_reason = str((_MEMBER_SNAPSHOT_REBUILD_DIRTY.get(task_key) or {}).get("last_reason") or reason or "").strip() or "subnet.member.snapshot.changed"
            _schedule_member_snapshot_rebuild(
                webspace_id=webspace_id,
                node_id=node_id,
                reason=f"{delayed_reason}:delayed",
                request_id=str((_MEMBER_SNAPSHOT_REBUILD_DIRTY.get(task_key) or {}).get("last_request_id") or delayed_request_id),
            )
        finally:
            current_delayed = _MEMBER_SNAPSHOT_REBUILD_DELAYED_TASKS.get(task_key)
            if current_delayed is task:
                _MEMBER_SNAPSHOT_REBUILD_DELAYED_TASKS.pop(task_key, None)

    task = asyncio.create_task(
        _runner(),
        name=f"member-snapshot-rebuild-delayed:{webspace_id}:{node_id}",
    )
    _MEMBER_SNAPSHOT_REBUILD_DELAYED_TASKS[task_key] = task


def _webspace_runtime_async_write_meta(*, root_names: list[str], source: str):
    return ystore_write_metadata(
        root_names=root_names,
        source=source,
        owner="core:webspace_runtime",
        channel="core.webspace_runtime.async",
    )


def _webspace_runtime_sync_write_meta(*, root_names: list[str], source: str):
    return ystore_write_metadata_sync(
        root_names=root_names,
        source=source,
        owner="core:webspace_runtime",
        channel="core.webspace_runtime.sync",
    )
_WHOLE_BRANCH_REPLACE_PATHS = frozenset()
_RUNTIME_META_EFFECTIVE_BRANCH_FINGERPRINTS_KEY = "effective_branch_fingerprints"
_MATERIALIZED_PAYLOAD_BRANCH_FINGERPRINTS_KEY = "branch_fingerprints"
_WEBUI_LOAD_PHASES = frozenset({"eager", "visible", "interaction", "deferred"})
_WEBUI_LOAD_FOCUS = frozenset({"primary", "supporting", "off_focus", "background"})
_WEBUI_READINESS_STATES = frozenset({"pending_structure", "first_paint", "interactive", "hydrating", "ready", "degraded"})
_DEFERRED_OFF_FOCUS_LOAD = {
    "structure": "interaction",
    "data": "deferred",
    "focus": "off_focus",
    "offFocusReadyState": "hydrating",
}


def _reload_dedupe_window_s() -> float:
    raw = str(os.getenv("ADAOS_WEBSPACE_RECOVERY_DEDUPE_WINDOW_S") or "").strip()
    if not raw:
        return 1.5
    try:
        value = float(raw)
    except Exception:
        return 1.5
    if value < 0.0:
        return 0.0
    if value > 30.0:
        return 30.0
    return value


def _reload_pending_stale_after_s() -> float:
    raw = str(os.getenv("ADAOS_WEBSPACE_RECOVERY_PENDING_STALE_AFTER_S") or "").strip()
    if not raw:
        return 10.0
    try:
        value = float(raw)
    except Exception:
        return 10.0
    if value < 0.0:
        return 0.0
    if value > 300.0:
        return 300.0
    return value


def _reload_command_dedupe_ttl_s() -> float:
    raw = str(os.getenv("ADAOS_WEBSPACE_RECOVERY_COMMAND_DEDUPE_TTL_S") or "").strip()
    if not raw:
        return 300.0
    try:
        value = float(raw)
    except Exception:
        return 300.0
    if value < 0.0:
        return 0.0
    if value > 3600.0:
        return 3600.0
    return value


def _recovery_command_cache_key(
    *,
    webspace_id: str,
    action: str,
    scenario_id: str | None,
    cmd_id: str,
) -> str:
    raw = {
        "webspace_id": str(webspace_id or "").strip() or "default",
        "action": str(action or "").strip() or "reload",
        "scenario_id": str(scenario_id or "").strip() or None,
        "cmd_id": str(cmd_id or "").strip(),
    }
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:16]


def _claim_recovery_command_once(
    *,
    webspace_id: str,
    action: str,
    scenario_id: str | None,
    cmd_id: str | None,
    fingerprint: str,
) -> tuple[bool, dict[str, Any] | None]:
    cmd_id = str(cmd_id or "").strip()
    ttl_s = _reload_command_dedupe_ttl_s()
    if not cmd_id or ttl_s <= 0.0:
        return True, None

    now = time.time()
    expired = [
        key
        for key, entry in _WEBSPACE_RECOVERY_COMMAND_CACHE.items()
        if now - float(entry.get("ts") or 0.0) > ttl_s
    ]
    for key in expired:
        _WEBSPACE_RECOVERY_COMMAND_CACHE.pop(key, None)
    while len(_WEBSPACE_RECOVERY_COMMAND_CACHE) >= _WEBSPACE_RECOVERY_COMMAND_CACHE_LIMIT:
        oldest_key = min(
            _WEBSPACE_RECOVERY_COMMAND_CACHE,
            key=lambda item: float(_WEBSPACE_RECOVERY_COMMAND_CACHE[item].get("ts") or 0.0),
        )
        _WEBSPACE_RECOVERY_COMMAND_CACHE.pop(oldest_key, None)

    key = _recovery_command_cache_key(
        webspace_id=webspace_id,
        action=action,
        scenario_id=scenario_id,
        cmd_id=cmd_id,
    )
    existing = _WEBSPACE_RECOVERY_COMMAND_CACHE.get(key)
    if existing:
        duplicate = dict(existing)
        duplicate["age_s"] = round(max(0.0, now - float(existing.get("ts") or now)), 3)
        duplicate["ttl_s"] = ttl_s
        duplicate["cmd_id"] = cmd_id
        duplicate["cache_key"] = key
        return False, duplicate

    _WEBSPACE_RECOVERY_COMMAND_CACHE[key] = {
        "ts": now,
        "webspace_id": str(webspace_id or "").strip() or "default",
        "action": str(action or "").strip() or "reload",
        "scenario_id": str(scenario_id or "").strip() or None,
        "fingerprint": str(fingerprint or "").strip(),
        "cmd_id": cmd_id,
        "cache_key": key,
    }
    return True, None


def _project_scenario_timeout_s() -> float:
    raw = str(os.getenv("ADAOS_WEBSPACE_PROJECT_SCENARIO_TIMEOUT_S") or "").strip()
    if not raw:
        return 6.0
    try:
        value = float(raw)
    except Exception:
        return 6.0
    if value < 0.0:
        return 0.0
    if value > 120.0:
        return 120.0
    return value


def _normalize_optional_token(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def _local_node_id() -> str:
    try:
        conf = load_config()
        node_id = str(getattr(conf, "node_id", "") or "").strip()
        if node_id:
            return node_id
        nested = str(getattr(getattr(conf, "node_settings", None), "id", "") or "").strip()
        if nested:
            return nested
    except Exception:
        pass
    return "hub"


def _local_node_label() -> str:
    try:
        conf = load_config()
        return str(node_display_from_config(conf).get("node_label") or "").strip() or _local_node_id()
    except Exception:
        return _local_node_id()


def _local_node_display() -> dict[str, Any]:
    cached_at, cached = _CACHE_STATE.local_node_display
    now = time.monotonic()
    if cached and (now - cached_at) <= _LOCAL_NODE_DISPLAY_CACHE_TTL_S:
        return dict(cached)
    try:
        display = node_display_from_config(load_config())
    except Exception:
        display = {
            "node_label": _local_node_label(),
            "node_compact_label": "N0",
            "node_index": 0,
            "node_color": "",
        }
    _CACHE_STATE.local_node_display = (now, dict(display))
    return dict(display)


_HOME_SCENARIO_REF_UNSET = object()


@dataclass(slots=True)
class WebUIRegistryEntry:
    """
    Effective UI model snapshot for a single webspace after merging:

      - scenario-projected catalog/registry,
      - skill contributions from webui.json,
      - auto-installed items and current desktop overlay state.
    """

    scenario_id: str
    apps: List[Dict[str, Any]] = field(default_factory=list)
    widgets: List[Dict[str, Any]] = field(default_factory=list)
    registry_modals: List[str] = field(default_factory=list)
    registry_widgets: List[str] = field(default_factory=list)
    installed: Dict[str, List[str]] = field(default_factory=lambda: {"apps": [], "widgets": []})


@dataclass(slots=True)
class WebspaceInfo:
    """
    Lightweight snapshot of a webspace entry used by higher-level services
    and SDK helpers. ``is_dev`` is derived from the display name and can be
    used to filter workspace vs dev spaces.
    """

    id: str
    title: str
    created_at: int
    kind: str = "workspace"
    home_scenario: str = "web_desktop"
    home_scenario_ref: dict[str, Any] | None = None
    source_mode: str = "workspace"
    node_id: str = "hub"
    node_label: str = "hub"
    node_compact_label: str | None = None
    node_index: int | None = None
    node_color: str | None = None
    is_dev: bool = False
    current_scenario: str | None = None
    stored_home_scenario_exists: bool | None = None
    home_scenario_exists: bool = True
    current_scenario_exists: bool | None = None
    degraded: bool = False
    validation_reason: str | None = None
    recommended_action: str | None = None


@dataclass(slots=True)
class WebspaceOperationalState:
    """
    Lightweight operational view of a webspace that combines persistent
    manifest metadata with the current live scenario selection from Yjs.

    ``stored_home_scenario`` preserves whether the manifest explicitly stores
    a home scenario. This matters for legacy spaces, where reload/reset should
    still be able to fall back to ``ui.current_scenario`` instead of forcing
    ``web_desktop`` semantics too early.
    """

    webspace_id: str
    title: str
    kind: str
    source_mode: str
    is_dev: bool
    stored_home_scenario: str | None
    effective_home_scenario: str
    home_scenario_ref: dict[str, Any] | None
    current_scenario: str | None
    stored_home_scenario_exists: bool | None = None
    home_scenario_exists: bool = True
    current_scenario_exists: bool | None = None
    degraded: bool = False
    validation_reason: str | None = None
    recommended_action: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "webspace_id": self.webspace_id,
            "title": self.title,
            "kind": self.kind,
            "source_mode": self.source_mode,
            "is_dev": self.is_dev,
            "stored_home_scenario": self.stored_home_scenario,
            "home_scenario": self.effective_home_scenario,
            "home_scenario_ref": self.home_scenario_ref,
            "current_scenario": self.current_scenario,
            "stored_home_scenario_exists": self.stored_home_scenario_exists,
            "home_scenario_exists": self.home_scenario_exists,
            "current_scenario_exists": self.current_scenario_exists,
            "degraded": self.degraded,
            "validation_reason": self.validation_reason,
            "recommended_action": self.recommended_action,
            "current_matches_home": bool(self.current_scenario) and self.current_scenario == self.effective_home_scenario,
        }


@dataclass(slots=True)
class WebspaceResolverInputs:
    """
    Explicit resolver inputs for the current light-weight Phase 3 contract.

    `overlay_snapshot` is sourced from persistent webspace metadata and
    represents canonical desktop customization state for the current MVP
    Phase 5 boundary.
    """

    webspace_id: str
    scenario_id: str
    source_mode: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    scenario_application: Dict[str, Any] = field(default_factory=dict)
    scenario_catalog: Dict[str, Any] = field(default_factory=dict)
    scenario_registry: Dict[str, Any] = field(default_factory=dict)
    overlay_snapshot: Dict[str, Any] = field(default_factory=dict)
    live_state: Dict[str, Any] = field(default_factory=dict)
    compatibility_cache_presence: Dict[str, bool] = field(default_factory=dict)
    skill_decls: List[Dict[str, Any]] = field(default_factory=list)
    desktop_scenarios: List[Tuple[str, str]] = field(default_factory=list)
    scenario_source: str = "legacy_yjs"
    legacy_scenario_fallback: bool = False
    skill_decls_fingerprint: str = ""


@dataclass(slots=True)
class WebspaceResolverOutputs:
    """
    Materialized effective UI state computed from resolver inputs.

    These values are still written to the existing Yjs compatibility paths,
    but the merge result itself is now an explicit architectural layer.
    """

    webspace_id: str
    scenario_id: str
    source_mode: str
    application: Dict[str, Any] = field(default_factory=dict)
    catalog: Dict[str, List[Dict[str, Any]]] = field(default_factory=lambda: {"apps": [], "widgets": []})
    registry: Dict[str, List[str]] = field(default_factory=lambda: {"modals": [], "widgets": []})
    installed: Dict[str, List[str]] = field(default_factory=lambda: {"apps": [], "widgets": []})
    desktop: Dict[str, Any] = field(default_factory=dict)
    webio: Dict[str, Any] = field(default_factory=dict)
    routing: Dict[str, Any] = field(default_factory=dict)
    skill_decls: List[Dict[str, Any]] = field(default_factory=list)

    def to_registry_entry(self) -> "WebUIRegistryEntry":
        return WebUIRegistryEntry(
            scenario_id=self.scenario_id,
            apps=[dict(it) for it in (self.catalog.get("apps") or []) if isinstance(it, Mapping)],
            widgets=[dict(it) for it in (self.catalog.get("widgets") or []) if isinstance(it, Mapping)],
            registry_modals=list(self.registry.get("modals") or []),
            registry_widgets=list(self.registry.get("widgets") or []),
            installed={
                "apps": list(self.installed.get("apps") or []),
                "widgets": list(self.installed.get("widgets") or []),
            },
        )


def _mark_entry(entry: Dict[str, Any], *, source: str, dev: bool) -> Dict[str, Any]:
    """
    Attach provenance / dev flag to a catalog entry without overwriting its
    semantic "source" (which may already contain a YDoc path like "y:data/...").
    """
    data = dict(entry)
    # Always keep provenance separate from semantic `source` paths used by
    # widget renderers (e.g. metric tiles reading y:data/...).
    data["origin"] = source
    data["dev"] = dev
    return data


def _mark_modal_def(entry: Any, *, source: str, skill: str, dev: bool) -> Dict[str, Any]:
    data = dict(entry) if isinstance(entry, Mapping) else {}
    skill_token = str(skill or "").strip()
    if source:
        data.setdefault("origin", source)
    if skill_token:
        data.setdefault("originSkill", skill_token)
    data.setdefault("dev", dev)
    meta = dict(data.get("_adaos") or {}) if isinstance(data.get("_adaos"), Mapping) else {}
    if source:
        meta.setdefault("origin", source)
    if skill_token:
        meta.setdefault("originSkill", skill_token)
    if meta:
        data["_adaos"] = meta
    return data


def _apply_node_display_to_entry(
    entry: Dict[str, Any],
    display: Mapping[str, Any] | None,
    *,
    node_id: str | None = None,
    override_existing: bool = False,
) -> Dict[str, Any]:
    data = dict(entry)
    resolved_node_id = str(node_id or data.get("node_id") or "").strip()
    if resolved_node_id and not str(data.get("node_id") or "").strip():
        data["node_id"] = resolved_node_id
    if not isinstance(display, Mapping):
        return data
    node_label = str(display.get("node_label") or "").strip()
    if node_label and (override_existing or not str(data.get("node_label") or "").strip()):
        data["node_label"] = node_label
    compact_label = str(display.get("node_compact_label") or "").strip()
    if compact_label and (override_existing or not str(data.get("node_compact_label") or "").strip()):
        data["node_compact_label"] = compact_label
    node_color = str(display.get("node_color") or "").strip()
    if node_color and (override_existing or not str(data.get("node_color") or "").strip()):
        data["node_color"] = node_color
    node_index = display.get("node_index")
    if node_index is not None and (override_existing or data.get("node_index") is None):
        data["node_index"] = node_index
    return data


def _is_node_owned_skill(skill_name: str) -> bool:
    token = str(skill_name or "").strip()
    return bool(token) and token != "web_desktop_skill"


def _decl_is_node_owned(decl: Mapping[str, Any] | None) -> bool:
    if not isinstance(decl, Mapping):
        return False
    owner = str(decl.get("ui_owner") or "").strip().lower()
    if owner == "shared":
        return False
    if owner == "node":
        return True
    return _is_node_owned_skill(str(decl.get("skill") or ""))


def _scope_node_data_source(data_source: Any, *, node_id: str) -> Any:
    if not isinstance(data_source, Mapping):
        return data_source
    scoped = _clone_json_like(data_source)
    if not isinstance(scoped, dict):
        return data_source
    kind = str(scoped.get("kind") or "").strip().lower()
    if kind == "stream":
        requested_scope = str(scoped.get("scope") or "").strip().lower()
        shared_scope = requested_scope in {"shared", "workspace", "local"}
        if node_id and not shared_scope and not str(scoped.get("nodeId") or scoped.get("node_id") or "").strip():
            scoped["nodeId"] = node_id
    if kind == "y" and scoped.get("path") is not None:
        scoped["path"] = node_scope_data_path(str(scoped.get("path") or ""), node_id)
    if kind == "api":
        for key in ("params", "body"):
            value = scoped.get(key)
            if isinstance(value, dict) and node_id and not str(value.get("node_id") or value.get("target_node_id") or "").strip():
                value["node_id"] = node_id
                value.setdefault("target_node_id", node_id)
    return scoped


def _scope_node_modal_id(modal_id: Any, *, node_id: str, modal_id_map: Mapping[str, str] | None = None) -> Any:
    if not isinstance(modal_id, str):
        return modal_id
    token = str(modal_id or "").strip()
    if not token or token.startswith("$"):
        return modal_id
    if modal_id_map and token in modal_id_map:
        return modal_id_map[token]
    return _node_scoped_catalog_id(node_id, token)


def _scope_node_observe_spec(observe_spec: Any, *, node_id: str) -> Any:
    if not isinstance(observe_spec, Mapping):
        return observe_spec
    scoped = _clone_json_like(observe_spec)
    if not isinstance(scoped, dict):
        return observe_spec
    kind = str(scoped.get("kind") or "").strip().lower()
    if (not kind or kind == "y") and scoped.get("path") is not None:
        scoped["path"] = node_scope_data_path(str(scoped.get("path") or ""), node_id)
        scoped.setdefault("kind", "y")
    return scoped


def _apply_node_context_to_ui(
    value: Any,
    display: Mapping[str, Any] | None,
    *,
    node_id: str,
    modal_id_map: Mapping[str, str] | None = None,
    override_node_display: bool = False,
) -> Any:
    if not node_id:
        return _clone_json_like(value)
    if isinstance(value, list):
        return [
            _apply_node_context_to_ui(
                item,
                display,
                node_id=node_id,
                modal_id_map=modal_id_map,
                override_node_display=override_node_display,
            )
            for item in value
        ]
    if not isinstance(value, Mapping):
        return _clone_json_like(value)

    data: Dict[str, Any] = {
        str(key): _apply_node_context_to_ui(
            item,
            display,
            node_id=node_id,
            modal_id_map=modal_id_map,
            override_node_display=override_node_display,
        )
        for key, item in value.items()
    }
    if data.get("id") or data.get("type") or data.get("dataSource") or data.get("actions") or data.get("source"):
        data = _apply_node_display_to_entry(
            data,
            display,
            node_id=node_id,
            override_existing=override_node_display,
        )
    if isinstance(data.get("dataSource"), Mapping):
        data["dataSource"] = _scope_node_data_source(data.get("dataSource"), node_id=node_id)
    if isinstance(data.get("source"), str):
        data["source"] = node_scope_data_path(str(data.get("source") or ""), node_id)
    if isinstance(data.get("launchModal"), str):
        data["launchModal"] = _scope_node_modal_id(
            data.get("launchModal"),
            node_id=node_id,
            modal_id_map=modal_id_map,
        )
    if isinstance(data.get("_observe"), Mapping):
        data["_observe"] = _scope_node_observe_spec(data.get("_observe"), node_id=node_id)
    params = data.get("params")
    if isinstance(params, dict):
        if isinstance(params.get("modalId"), str):
            params["modalId"] = _scope_node_modal_id(
                params.get("modalId"),
                node_id=node_id,
                modal_id_map=modal_id_map,
            )
        if isinstance(params.get("_observe"), Mapping):
            params["_observe"] = _scope_node_observe_spec(params.get("_observe"), node_id=node_id)
    return data


def _node_scoped_modal_ids(registry: Mapping[str, Any], *, node_id: str) -> Dict[str, str]:
    if not node_id:
        return {}
    modals = registry.get("modals") if isinstance(registry.get("modals"), Mapping) else {}
    if not isinstance(modals, Mapping):
        return {}
    out: Dict[str, str] = {}
    for key in modals.keys():
        token = str(key or "").strip()
        if token:
            scoped = _node_scoped_catalog_id(node_id, token)
            out[token] = scoped
            stripped = _strip_node_scoped_catalog_prefix(token)
            if stripped:
                out[stripped] = scoped
    return out


def _local_catalog_decl_entries(decls: List[Dict[str, Any]]) -> dict[str, Any]:
    try:
        conf = load_config()
        display = node_display_from_config(conf)
    except Exception:
        display = {
            "node_label": _local_node_label(),
            "node_compact_label": "N0",
            "node_color": "",
            "node_index": 0,
        }
    node_id = _local_node_id()
    apps: List[Dict[str, Any]] = []
    widgets: List[Dict[str, Any]] = []
    registry_modals: Dict[str, Any] = {}
    registry_widgets: Dict[str, Any] = {}
    resources: Dict[str, Any] = _materialized_system_resource_descriptors()
    interfaces: Dict[str, Any] = {}
    webio_receivers: Dict[str, Any] = {}
    ydoc_defaults: Dict[str, Any] = {}
    for decl in decls:
        skill_name = str(decl.get("skill") or "").strip()
        source = f"skill:{skill_name}" if skill_name else "skill:unknown"
        dev_flag = str(decl.get("space") or "default").strip().lower() == "dev"
        node_owned = _decl_is_node_owned(decl)
        reg = decl.get("registry") if isinstance(decl.get("registry"), Mapping) else {}
        modal_id_map = _node_scoped_modal_ids(reg, node_id=node_id) if node_owned else {}
        for app in decl.get("apps") or []:
            if not isinstance(app, dict):
                continue
            entry = _mark_entry(app, source=source, dev=dev_flag)
            if node_owned:
                entry = _apply_node_context_to_ui(entry, display, node_id=node_id, modal_id_map=modal_id_map)
            apps.append(_apply_node_display_to_entry(entry, display, node_id=node_id))
        for widget in decl.get("widgets") or []:
            if not isinstance(widget, dict):
                continue
            entry = _mark_entry(widget, source=source, dev=dev_flag)
            if node_owned:
                entry = _apply_node_context_to_ui(entry, display, node_id=node_id, modal_id_map=modal_id_map)
            widgets.append(_apply_node_display_to_entry(entry, display, node_id=node_id))
        mod_spec = reg.get("modals") if isinstance(reg, Mapping) else {}
        if isinstance(mod_spec, Mapping):
            for key, value in mod_spec.items():
                token = str(key or "").strip()
                if not token:
                    continue
                scoped_token = modal_id_map.get(token, token)
                if scoped_token in registry_modals:
                    continue
                modal_def = (
                    _apply_node_context_to_ui(value, display, node_id=node_id, modal_id_map=modal_id_map)
                    if node_owned
                    else _clone_json_like(value)
                )
                registry_modals[scoped_token] = _mark_modal_def(
                    modal_def,
                    source=source,
                    skill=skill_name,
                    dev=dev_flag,
                )
        wid_spec = reg.get("widgets") if isinstance(reg, Mapping) else {}
        if isinstance(wid_spec, Mapping):
            for key, value in wid_spec.items():
                token = str(key or "").strip()
                if not token or token in registry_widgets:
                    continue
                registry_widgets[token] = (
                    _apply_node_context_to_ui(value, display, node_id=node_id, modal_id_map=modal_id_map)
                    if node_owned
                    else _clone_json_like(value)
                )
        raw_resources = decl.get("resources") if isinstance(decl.get("resources"), Mapping) else {}
        skill_source_path = str(decl.get("source_path") or "").strip() or None
        for key, value in raw_resources.items():
            token = str(key or "").strip()
            if token and token not in resources:
                resources[token] = _materialize_skill_resource_descriptor(
                    token,
                    value,
                    skill_name=skill_name,
                    skill_dir=skill_source_path,
                )
        raw_interface = decl.get("interface") if isinstance(decl.get("interface"), Mapping) else {}
        if raw_interface and skill_name and skill_name not in interfaces:
            interfaces[skill_name] = _clone_skill_ui_interface(raw_interface, skill=skill_name, source=source)
        webio = decl.get("webio") if isinstance(decl.get("webio"), Mapping) else {}
        receivers = webio.get("receivers") if isinstance(webio.get("receivers"), Mapping) else {}
        for key, value in receivers.items():
            token = str(key or "").strip()
            if token and token not in webio_receivers:
                webio_receivers[token] = _normalize_webio_receiver(value)
        raw_defaults = decl.get("ydoc_defaults") if isinstance(decl.get("ydoc_defaults"), Mapping) else {}
        for path, value in raw_defaults.items():
            token = str(path or "").strip()
            if not token:
                continue
            scoped_path = node_scope_data_path(token, node_id) if node_owned else token
            ydoc_defaults.setdefault(scoped_path, _clone_json_like(value))
    return {
        "captured_at": time.time(),
        "apps": _merge_by_id(apps),
        "widgets": _merge_by_id(widgets),
        "registry": {
            "modals": registry_modals,
            "widgets": registry_widgets,
        },
        "resources": resources,
        "interfaces": interfaces,
        "webio": {"receivers": webio_receivers},
        "ydoc_defaults": ydoc_defaults,
    }


def build_local_desktop_catalog_snapshot(*, mode: str = "workspace", include_remote: bool = True) -> dict[str, Any]:
    runtime = WebspaceScenarioRuntime()
    snapshot = _local_catalog_decl_entries(runtime._collect_skill_decls(mode=mode, include_remote=include_remote))
    try:
        return _overlay_current_ydoc_defaults(snapshot, webspace_id=default_webspace_id())
    except Exception:
        _log.debug("failed to overlay local desktop catalog defaults from YDoc", exc_info=True)
        return snapshot


async def build_local_desktop_catalog_snapshot_async(*, mode: str = "workspace", include_remote: bool = True) -> dict[str, Any]:
    runtime = WebspaceScenarioRuntime()
    snapshot = _local_catalog_decl_entries(runtime._collect_skill_decls(mode=mode, include_remote=include_remote))
    timeout_s = _local_catalog_ydoc_overlay_timeout_s()
    try:
        return await asyncio.wait_for(
            _overlay_current_ydoc_defaults_async(snapshot, webspace_id=default_webspace_id()),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        _log.warning(
            "timed out overlaying local desktop catalog defaults from YDoc timeout_s=%.3f; using declaration catalog",
            timeout_s,
        )
        return snapshot
    except Exception:
        _log.debug("failed to overlay local desktop catalog defaults from YDoc", exc_info=True)
        return snapshot


_YDOC_PATH_MISSING = object()


def _read_current_ydoc_path_value(ydoc: Any, path: str) -> Any:
    segments = [str(seg or "").strip() for seg in str(path or "").split("/") if str(seg or "").strip()]
    if len(segments) < 2:
        return _YDOC_PATH_MISSING
    current: Any = ydoc.get_map(segments[0])
    for seg in segments[1:]:
        getter = getattr(current, "get", None)
        if not callable(getter):
            return _YDOC_PATH_MISSING
        current = getter(seg)
        if current is None:
            return _YDOC_PATH_MISSING
    return _clone_json_like(current)


def _read_current_ydoc_path_value_with_local_node_fallback(ydoc: Any, path: str) -> Any:
    current = _read_current_ydoc_path_value(ydoc, path)
    if current is not _YDOC_PATH_MISSING:
        return current
    local_path = local_unscoped_data_path(path, _local_node_id())
    if not local_path or local_path == path:
        return _YDOC_PATH_MISSING
    return _read_current_ydoc_path_value(ydoc, local_path)


def _resolve_live_room_ydoc(webspace_id: str) -> Any | None:
    try:
        from adaos.services.yjs.gateway import y_server  # pylint: disable=import-outside-toplevel
    except Exception:
        return None
    room = getattr(y_server, "rooms", {}).get(str(webspace_id or "").strip())
    ydoc = getattr(room, "ydoc", None)
    return ydoc if ydoc is not None else None


def _sync_ydoc_read_allowed() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


def _local_catalog_ydoc_overlay_timeout_s() -> float:
    raw = str(os.getenv("ADAOS_MEMBER_DESKTOP_CATALOG_YDOC_OVERLAY_TIMEOUT_S") or "").strip()
    try:
        value = float(raw or 2.5)
    except Exception:
        value = 2.5
    return max(0.1, min(30.0, value))


def _overlay_current_ydoc_defaults(snapshot: dict[str, Any], *, webspace_id: str) -> dict[str, Any]:
    defaults = snapshot.get("ydoc_defaults") if isinstance(snapshot.get("ydoc_defaults"), dict) else {}
    if not defaults:
        return snapshot
    live_ydoc = _resolve_live_room_ydoc(webspace_id)
    pending_paths = list(defaults.keys())
    if live_ydoc is not None:
        remaining: list[str] = []
        for path in pending_paths:
            live_value = _read_current_ydoc_path_value_with_local_node_fallback(live_ydoc, str(path or ""))
            if live_value is _YDOC_PATH_MISSING:
                remaining.append(str(path))
                continue
            defaults[str(path)] = live_value
        pending_paths = remaining
    if not pending_paths or not _sync_ydoc_read_allowed():
        snapshot["ydoc_defaults"] = defaults
        return snapshot
    try:
        with get_ydoc(webspace_id) as ydoc:
            for path in pending_paths:
                live_value = _read_current_ydoc_path_value_with_local_node_fallback(ydoc, str(path or ""))
                if live_value is _YDOC_PATH_MISSING:
                    continue
                defaults[str(path)] = live_value
    except Exception:
        return snapshot
    snapshot["ydoc_defaults"] = defaults
    return snapshot


async def _overlay_current_ydoc_defaults_async(snapshot: dict[str, Any], *, webspace_id: str) -> dict[str, Any]:
    defaults = snapshot.get("ydoc_defaults") if isinstance(snapshot.get("ydoc_defaults"), dict) else {}
    if not defaults:
        return snapshot
    live_ydoc = _resolve_live_room_ydoc(webspace_id)
    pending_paths = list(defaults.keys())
    if live_ydoc is not None:
        remaining: list[str] = []
        for path in pending_paths:
            live_value = _read_current_ydoc_path_value_with_local_node_fallback(live_ydoc, str(path or ""))
            if live_value is _YDOC_PATH_MISSING:
                remaining.append(str(path))
                continue
            defaults[str(path)] = live_value
        pending_paths = remaining
    if not pending_paths:
        snapshot["ydoc_defaults"] = defaults
        return snapshot
    try:
        async with async_read_ydoc(webspace_id) as ydoc:
            for path in pending_paths:
                live_value = _read_current_ydoc_path_value_with_local_node_fallback(ydoc, str(path or ""))
                if live_value is _YDOC_PATH_MISSING:
                    continue
                defaults[str(path)] = live_value
    except Exception:
        snapshot["ydoc_defaults"] = defaults
        return snapshot
    snapshot["ydoc_defaults"] = defaults
    return snapshot


def _merge_by_id(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    merged: List[Dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        merged.append(item)
    return merged


def _strip_node_scoped_catalog_prefix(item_id: str) -> str:
    item_token = str(item_id or "").strip()
    while item_token.startswith("node:"):
        _prefix, _node_id, remainder = item_token.split(":", 2) if item_token.count(":") >= 2 else ("", "", "")
        if not remainder:
            break
        item_token = remainder.strip()
    return item_token


def _node_scoped_catalog_id(node_id: str, item_id: str) -> str:
    node_token = str(node_id or "").strip()
    item_token = _strip_node_scoped_catalog_prefix(item_id)
    if not node_token or not item_token:
        return item_token
    return f"node:{node_token}:{item_token}"


def _node_scoped_entry_node_id(item_id: Any) -> str | None:
    token = str(item_id or "").strip()
    if not token.startswith("node:"):
        return None
    parts = token.split(":", 2)
    if len(parts) != 3:
        return None
    node_id = str(parts[1] or "").strip()
    return node_id or None


def _node_scoped_data_path_node_id(path: Any) -> str | None:
    raw = str(path or "").strip()
    if raw.startswith("y:"):
        raw = raw[2:]
    parts = [part for part in raw.split("/") if part]
    if len(parts) < 3 or parts[0] != "data" or parts[1] != "nodes":
        return None
    node_id = str(parts[2] or "").strip()
    return node_id or None


def _catalog_entry_is_foreign_relay(entry: Mapping[str, Any], *, node_id: str) -> bool:
    entry_node_id = _node_scoped_entry_node_id(entry.get("id"))
    if entry_node_id and entry_node_id != str(node_id or "").strip():
        return True
    source = str(entry.get("origin") or entry.get("source") or "").strip()
    if source.startswith("skill:subnet.member."):
        source_node_id = source.removeprefix("skill:subnet.member.").strip()
        if source_node_id and source_node_id != str(node_id or "").strip():
            return True
    return False


def _preserve_live_remote_catalog_entries(
    merged: List[Dict[str, Any]],
    *,
    current_items: Any,
    active_remote_node_ids: set[str],
    detached_remote_node_ids: set[str] | None = None,
) -> List[Dict[str, Any]]:
    if not isinstance(current_items, list):
        return merged
    seen_ids = {
        str(item.get("id") or "").strip()
        for item in merged
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    }
    preserved: List[Dict[str, Any]] = list(merged)
    for item in current_items:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id in seen_ids:
            continue
        node_id = _node_scoped_entry_node_id(item_id)
        if node_id and detached_remote_node_ids and node_id in detached_remote_node_ids:
            continue
        if not node_id or node_id in active_remote_node_ids:
            continue
        preserved.append(dict(item))
        seen_ids.add(item_id)
    return preserved


def _preserve_live_remote_modals(
    merged_modals_map: Dict[str, Any],
    *,
    current_modals: Any,
    active_remote_node_ids: set[str],
    detached_remote_node_ids: set[str] | None = None,
) -> Dict[str, Any]:
    if not isinstance(current_modals, Mapping):
        return merged_modals_map
    preserved = dict(merged_modals_map)
    for key, value in current_modals.items():
        modal_id = str(key or "").strip()
        if not modal_id or modal_id in preserved:
            continue
        node_id = _node_scoped_entry_node_id(modal_id)
        if node_id and detached_remote_node_ids and node_id in detached_remote_node_ids:
            continue
        if not node_id or node_id in active_remote_node_ids:
            continue
        preserved[modal_id] = _clone_json_like(value)
    return preserved


def _preserve_live_remote_registry_tokens(
    merged_tokens: List[str],
    *,
    current_tokens: Any,
    active_remote_node_ids: set[str],
    detached_remote_node_ids: set[str] | None = None,
) -> List[str]:
    tokens = [str(token or "").strip() for token in merged_tokens if str(token or "").strip()]
    seen = set(tokens)
    if not isinstance(current_tokens, list):
        return tokens
    for value in current_tokens:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        node_id = _node_scoped_entry_node_id(token)
        if node_id and detached_remote_node_ids and node_id in detached_remote_node_ids:
            continue
        if not node_id or node_id in active_remote_node_ids:
            continue
        tokens.append(token)
        seen.add(token)
    return tokens


def _detached_member_node_ids() -> set[str]:
    try:
        from adaos.services.device_inventory import list_devices
    except Exception:
        return set()
    out: set[str] = set()
    try:
        devices = list_devices(kind="member", include_detached=True)
    except Exception:
        return out
    for item in list(devices or []):
        if not isinstance(item, Mapping):
            continue
        policy = item.get("policy") if isinstance(item.get("policy"), Mapping) else {}
        managed_state = str(policy.get("managed_state") or "").strip().lower()
        admission_policy = str(policy.get("admission_policy") or "").strip().lower()
        if (
            not bool(policy.get("revoked"))
            and managed_state not in {"detached", "denied", "revoked"}
            and admission_policy not in {"detached", "deny", "denied"}
        ):
            continue
        identity = item.get("identity") if isinstance(item.get("identity"), Mapping) else {}
        node_id = str(identity.get("node_id") or "").strip()
        if node_id:
            out.add(node_id)
    return out


def _member_device_inventory_display_map() -> dict[str, dict[str, Any]]:
    try:
        from adaos.services.device_inventory import list_devices
    except Exception:
        return {}
    try:
        devices = list_devices(kind="member", include_detached=True)
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in list(devices or []):
        if not isinstance(item, Mapping):
            continue
        identity = item.get("identity") if isinstance(item.get("identity"), Mapping) else {}
        policy = item.get("policy") if isinstance(item.get("policy"), Mapping) else {}
        node_id = str(identity.get("node_id") or "").strip()
        if not node_id:
            continue
        effective_name = (
            str(policy.get("effective_name") or "").strip()
            or str(policy.get("display_name") or "").strip()
        )
        # Inventory identity defaults are not user-facing names.  Let the
        # fresher directory/runtime projection keep its explicit label when
        # inventory only repeats the stable node id.
        if effective_name == node_id:
            effective_name = ""
        display: dict[str, Any] = {}
        if effective_name:
            display["node_label"] = effective_name
        if display:
            out[node_id] = display
    return out


def _remote_member_node_display(
    node: Mapping[str, Any],
    *,
    inventory_display: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    display = node_display_from_directory_node(node)
    node_id = str(node.get("node_id") or "").strip()
    overlay = (
        inventory_display.get(node_id)
        if node_id and isinstance(inventory_display, Mapping)
        else None
    )
    if isinstance(overlay, Mapping):
        for key, value in overlay.items():
            if value is not None and str(value or "").strip():
                display[str(key)] = value
    return display


def _scope_remote_catalog_entry_id(entry: Dict[str, Any], *, node_id: str) -> Dict[str, Any]:
    data = dict(entry)
    remote_node_id = str(node_id or "").strip()
    local_item_id = _strip_node_scoped_catalog_prefix(str(data.get("id") or "").strip())
    if not remote_node_id or not local_item_id:
        return data
    canonical_local_id = _strip_node_scoped_catalog_prefix(
        str(data.get("node_local_id") or data.get("remote_id") or local_item_id).strip()
    )
    data["node_local_id"] = canonical_local_id or local_item_id
    data["remote_id"] = canonical_local_id or local_item_id
    data["id"] = _node_scoped_catalog_id(remote_node_id, local_item_id)
    return data


def _merge_registry_lists(base: List[str], extras: List[List[str]]) -> List[str]:
    seen: set[str] = set()
    merged: List[str] = []
    for value in base:
        token = str(value)
        if token and token not in seen:
            seen.add(token)
            merged.append(token)
    for contrib in extras:
        for token in contrib:
            token = str(token)
            if token and token not in seen:
                seen.add(token)
                merged.append(token)
    return merged


def _filter_installed(installed: Dict[str, List[str]], apps: List[Dict[str, Any]], widgets: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    app_ids = {str(item.get("id")) for item in apps if item.get("id")}
    widget_ids = {str(item.get("id")) for item in widgets if item.get("id")}
    current_apps = [a for a in (installed.get("apps") or []) if a in app_ids]
    current_widgets = [w for w in (installed.get("widgets") or []) if w in widget_ids]
    return {"apps": current_apps, "widgets": current_widgets}

def _dedupe_str_list(values: Any) -> List[str]:
    # YJS may return YArray-like values which are iterable but not `list`.
    if isinstance(values, (str, bytes, bytearray)) or isinstance(values, Mapping):
        return []
    if not isinstance(values, Iterable):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for v in values:
        if not isinstance(v, str):
            continue
        token = v.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _coerce_dict(value: Any) -> Dict[str, Any]:
    """
    Best-effort conversion of YJS map-like values to a plain dict.

    y_py map objects are not guaranteed to implement `collections.abc.Mapping`
    but they often expose `.items()`. Using `isinstance(..., Mapping)` only
    can silently drop persisted state (e.g. installed apps) during scenario
    switches.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (str, bytes, bytearray)):
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    items = getattr(value, "items", None)
    if callable(items):
        try:
            return dict(items())
        except Exception:
            return {}
    return {}


def _looks_like_skill_ui_interface(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if isinstance(value.get("views"), Mapping):
        return True
    if isinstance(value.get("transitions"), list):
        return True
    if str(value.get("defaultView") or "").strip():
        return True
    schema = str(value.get("schema") or "").strip()
    return schema.startswith("adaos.ui.skill_interface")


def _clone_skill_ui_interface(raw: Any, *, skill: str, source: str) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    interface_copy = _clone_json_like(raw)
    if not isinstance(interface_copy, dict):
        return {}
    meta = dict(interface_copy.get("_adaos") or {}) if isinstance(interface_copy.get("_adaos"), Mapping) else {}
    if skill:
        meta.setdefault("originSkill", skill)
    if source:
        meta.setdefault("origin", source)
    interface_copy["_adaos"] = meta
    return interface_copy


def _mapping_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            result = getter(key)
            return default if result is None else result
        except Exception:
            return default
    return default


def _coerce_live_branch_subset(value: Any, keys: tuple[str, ...]) -> Dict[str, Any]:
    """
    Read only the live YMap branches the resolver actually needs.

    Full ``dict(y_map.items())`` materialization is expensive on large Yjs
    maps and used to dominate scenario-switch allocation profiles. The
    resolver only needs a few preservation inputs from live state, so keep this
    intentionally shallow and explicit.
    """
    out: Dict[str, Any] = {}
    for key in keys:
        item = _mapping_get(value, key)
        if item is None:
            continue
        if key in {"modals", "installed", "pageSchema", "routes"}:
            out[key] = _coerce_dict(item)
        elif isinstance(item, (list, tuple)):
            out[key] = list(item)
        else:
            out[key] = item
    return out


def _normalize_webui_load_hint(value: Any) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    out: Dict[str, str] = {}
    structure = str(value.get("structure") or "").strip()
    if structure in _WEBUI_LOAD_PHASES:
        out["structure"] = structure
    data = str(value.get("data") or "").strip()
    if data in _WEBUI_LOAD_PHASES:
        out["data"] = data
    focus = str(value.get("focus") or "").strip()
    if focus in _WEBUI_LOAD_FOCUS:
        out["focus"] = focus
    off_focus_ready = str(value.get("offFocusReadyState") or "").strip()
    if off_focus_ready in _WEBUI_READINESS_STATES:
        out["offFocusReadyState"] = off_focus_ready
    return out


def _apply_webui_load_hint(node: Any) -> Dict[str, Any]:
    item = _coerce_dict(node)
    if not item:
        return {}
    load = _normalize_webui_load_hint(item.get("load"))
    if load:
        item["load"] = load
    else:
        item.pop("load", None)
    return item


def _normalize_webui_widget_config(node: Any) -> Dict[str, Any]:
    return _apply_webui_load_hint(node)


def _normalize_webui_page_schema(node: Any) -> Dict[str, Any]:
    page = _apply_webui_load_hint(node)
    if not page:
        return {}
    widgets = page.get("widgets")
    if isinstance(widgets, list):
        page["widgets"] = [_normalize_webui_widget_config(widget) for widget in widgets if isinstance(widget, Mapping)]
    return page


def _normalize_webui_modal_def(node: Any) -> Dict[str, Any]:
    modal = _apply_webui_load_hint(node)
    if not modal:
        return {}
    schema = modal.get("schema")
    if isinstance(schema, Mapping):
        modal["schema"] = _normalize_webui_page_schema(schema)
    return modal


def _clone_json_like(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return json.loads(json.dumps(value))
    except Exception:
        if isinstance(value, dict):
            return {str(k): _clone_json_like(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_clone_json_like(v) for v in value]
        if isinstance(value, tuple):
            return [_clone_json_like(v) for v in value]
        if isinstance(value, Mapping):
            return {str(k): _clone_json_like(v) for k, v in value.items()}
        items = getattr(value, "items", None)
        if callable(items):
            try:
                return {str(k): _clone_json_like(v) for k, v in items()}
            except Exception:
                return {}
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            try:
                return [_clone_json_like(v) for v in value]
            except Exception:
                return []
        return str(value)


def _materialize_skill_resource_descriptor(
    resource_id: str,
    value: Any,
    *,
    skill_name: str | None = None,
    skill_dir: str | Path | None = None,
) -> Any:
    descriptor = _clone_json_like(value)
    if not isinstance(descriptor, dict):
        return descriptor
    skill_token = str(skill_name or "").strip()
    if not skill_token:
        return descriptor
    descriptor.setdefault("scope", "skill")
    descriptor.setdefault("owner", f"skill:{skill_token}")
    delivery = str(descriptor.get("delivery") or "core").strip().lower()
    if delivery == "external" or descriptor.get("url") or descriptor.get("src") or descriptor.get("href"):
        try:
            return publish_skill_resource_descriptor(
                str(resource_id or ""),
                descriptor,
                skill_name=skill_token,
                skill_dir=skill_dir,
            )
        except BrowserAssetPublishError as exc:
            descriptor["published"] = False
            descriptor["publishError"] = str(exc)
        except Exception:
            descriptor["published"] = False
            descriptor["publishError"] = "publish_failed"
        return descriptor
    try:
        return publish_skill_resource_descriptor(
            str(resource_id or ""),
            descriptor,
            skill_name=skill_token,
            skill_dir=skill_dir,
        )
    except BrowserAssetPublishError as exc:
        descriptor["published"] = False
        descriptor["publishError"] = str(exc)
    except Exception:
        descriptor["published"] = False
        descriptor["publishError"] = "publish_failed"
    return descriptor


def _materialize_scenario_resource_descriptor(
    resource_id: str,
    value: Any,
    *,
    scenario_id: str | None = None,
    scenario_dir: str | Path | None = None,
) -> Any:
    descriptor = _clone_json_like(value)
    if not isinstance(descriptor, dict):
        return descriptor
    scenario_token = str(scenario_id or "").strip()
    if not scenario_token:
        return descriptor
    descriptor.setdefault("scope", "scenario")
    descriptor.setdefault("owner", f"scenario:{scenario_token}")
    delivery = str(descriptor.get("delivery") or "core").strip().lower()
    resolved_dir: Path | None = None
    if scenario_dir is not None:
        resolved_dir = Path(scenario_dir)
    else:
        try:
            resolved_dir = scenarios_loader.scenario_root_for_space(scenario_token, "workspace")
        except Exception:
            resolved_dir = None
    if delivery == "external" or descriptor.get("url") or descriptor.get("src") or descriptor.get("href"):
        try:
            return publish_scenario_resource_descriptor(
                str(resource_id or ""),
                descriptor,
                scenario_id=scenario_token,
                scenario_dir=resolved_dir,
            )
        except BrowserAssetPublishError as exc:
            descriptor["published"] = False
            descriptor["publishError"] = str(exc)
        except Exception:
            descriptor["published"] = False
            descriptor["publishError"] = "publish_failed"
        return descriptor
    if resolved_dir is None:
        return descriptor
    try:
        return publish_scenario_resource_descriptor(
            str(resource_id or ""),
            descriptor,
            scenario_id=scenario_token,
            scenario_dir=resolved_dir,
        )
    except BrowserAssetPublishError as exc:
        descriptor["published"] = False
        descriptor["publishError"] = str(exc)
    except Exception:
        descriptor["published"] = False
        descriptor["publishError"] = "publish_failed"
    return descriptor


def _materialized_system_resource_descriptors() -> Dict[str, Any]:
    try:
        result = publish_system_resource_descriptors()
    except Exception:
        return {}
    resources: Dict[str, Any] = {}
    for bucket in ("published", "skipped"):
        items = result.get(bucket) if isinstance(result, Mapping) else {}
        if not isinstance(items, Mapping):
            continue
        for key, value in items.items():
            token = str(key or "").strip()
            if token and isinstance(value, Mapping):
                resources[token] = _clone_json_like(value)
    return resources


_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


def _json_like_equal(current: Any, next_value: Any) -> bool:
    if current is next_value:
        return True

    if isinstance(current, _JSON_SCALAR_TYPES) and isinstance(next_value, _JSON_SCALAR_TYPES):
        try:
            return current == next_value
        except Exception:
            return False

    if isinstance(current, (list, tuple)) or isinstance(next_value, (list, tuple)):
        if not isinstance(current, (list, tuple)) or not isinstance(next_value, (list, tuple)):
            return False
        if len(current) != len(next_value):
            return False
        return all(_json_like_equal(left, right) for left, right in zip(current, next_value))

    current_items = _mapping_items(current)
    next_items = _mapping_items(next_value)
    if current_items is not None or next_items is not None:
        if current_items is None or next_items is None:
            return False
        if len(current_items) != len(next_items):
            return False
        next_lookup = {key: item for key, item in next_items}
        if len(next_lookup) != len(next_items):
            return False
        for key, current_item in current_items:
            if key not in next_lookup:
                return False
            if not _json_like_equal(current_item, next_lookup[key]):
                return False
        return True

    try:
        return current == next_value
    except Exception:
        return _clone_json_like(current) == _clone_json_like(next_value)


def _merge_nested_json_path(existing: Any, segments: List[str], payload: Any) -> tuple[bool, Any]:
    if not segments:
        if _json_like_equal(existing, payload):
            return False, existing
        return True, _clone_json_like(payload)

    key = str(segments[0] or "")
    if not key:
        return False, _clone_json_like(existing)

    child_existing = None
    items = _mapping_items(existing)
    if items is not None:
        for item_key, item_value in items:
            if item_key == key:
                child_existing = item_value
                break

    changed, merged_child = _merge_nested_json_path(child_existing, segments[1:], payload)
    if not changed:
        return False, existing

    base = _clone_json_like(existing)
    if not isinstance(base, dict):
        base = {}
    merged = dict(base)
    merged[key] = merged_child
    return True, merged


def _nested_json_path_exists(existing: Any, segments: List[str]) -> bool:
    current = existing
    for segment in segments:
        key = str(segment or "")
        if not key:
            return False
        items = _mapping_items(current)
        if items is None:
            return False
        found = False
        for item_key, item_value in items:
            if item_key == key:
                current = item_value
                found = True
                break
        if not found:
            return False
    return True


def _is_y_map_value(value: Any) -> bool:
    y_map_type = getattr(Y, "YMap", None)
    return bool(y_map_type) and isinstance(value, y_map_type)


def _mapping_items(value: Any) -> list[tuple[str, Any]] | None:
    if type(value) is dict:
        return [(str(key), item) for key, item in value.items() if str(key)]
    if isinstance(value, Mapping):
        return [(str(key), item) for key, item in value.items() if str(key)]
    items = getattr(value, "items", None)
    if callable(items):
        try:
            return [(str(key), item) for key, item in items() if str(key)]
        except Exception:
            return None
    return None


def _attach_empty_y_map(parent_map: Any, txn: Any, key: str) -> Any | None:
    y_map_type = getattr(Y, "YMap", None)
    if not y_map_type or not _is_y_map_value(parent_map):
        return None
    try:
        parent_map.set(txn, key, y_map_type({}))
        attached = parent_map.get(key)
    except Exception:
        return None
    return attached if _is_y_map_value(attached) else None


def _reconcile_attached_y_map(node: Any, txn: Any, next_value: Any) -> bool:
    next_items = _mapping_items(next_value)
    if next_items is None:
        return False
    changed = False
    next_keys = {key for key, _item in next_items}
    try:
        current_keys = tuple(str(key) for key in node.keys() if str(key))
    except Exception:
        current_keys = ()
    for current_key in current_keys:
        if current_key in next_keys:
            continue
        try:
            node.pop(txn, current_key)
            changed = True
        except Exception:
            continue
    for child_key, raw_child in next_items:
        child_items = _mapping_items(raw_child)
        try:
            current_child = node.get(child_key)
        except Exception:
            current_child = None
        if child_items is not None:
            if _is_y_map_value(current_child):
                if _reconcile_attached_y_map(current_child, txn, raw_child):
                    changed = True
                continue
            if _json_like_equal(current_child, raw_child):
                continue
            attached_child = _attach_empty_y_map(node, txn, child_key)
            if attached_child is None:
                node.set(txn, child_key, _clone_json_like(raw_child))
                changed = True
                continue
            changed = True
            _reconcile_attached_y_map(attached_child, txn, raw_child)
            continue
        if _json_like_equal(current_child, raw_child):
            continue
        node.set(txn, child_key, _clone_json_like(raw_child))
        changed = True
    return changed


def _fingerprint_json_like(value: Any) -> str:
    try:
        normalized = json.dumps(
            _clone_json_like(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    except Exception:
        normalized = repr(value)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _resolved_output_branch_fingerprints(resolved: "WebspaceResolverOutputs") -> Dict[str, str]:
    return {
        "ui.application": _fingerprint_json_like(resolved.application),
        "data.catalog": _fingerprint_json_like(resolved.catalog),
        "data.installed": _fingerprint_json_like(resolved.installed),
        "data.desktop": _fingerprint_json_like(resolved.desktop),
        "data.webio": _fingerprint_json_like(resolved.webio),
        "data.routing": _fingerprint_json_like(resolved.routing),
        "registry.merged": _fingerprint_json_like(resolved.registry),
        "runtime.environment": _fingerprint_json_like(runtime_environment_payload()),
    }


def _materialized_payload_branch_fingerprints(payload: Mapping[str, Any] | None) -> Dict[str, str]:
    if not isinstance(payload, Mapping):
        return {}
    raw = payload.get(_MATERIALIZED_PAYLOAD_BRANCH_FINGERPRINTS_KEY)
    fingerprints: Dict[str, str] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            path = str(key or "").strip()
            token = str(value or "").strip()
            if path and token:
                fingerprints[path] = token

    payload_paths = {
        "ui.application": "application",
        "data.catalog": "catalog",
        "data.installed": "installed",
        "data.desktop": "desktop",
        "data.webio": "webio",
        "data.routing": "routing",
        "registry.merged": "registry",
    }
    for path, key in payload_paths.items():
        if path in fingerprints:
            continue
        if key not in payload:
            continue
        fingerprints[path] = _fingerprint_json_like(payload.get(key))
    if isinstance(payload, dict) and fingerprints:
        payload[_MATERIALIZED_PAYLOAD_BRANCH_FINGERPRINTS_KEY] = dict(fingerprints)
    return fingerprints


def _normalize_webio_receiver(node: Any) -> Dict[str, Any]:
    item = _coerce_dict(node)
    if not item:
        return {}
    out: Dict[str, Any] = {}
    mode = str(item.get("mode") or "").strip().lower()
    if mode in {"replace", "append"}:
        out["mode"] = mode
    collection_key = str(item.get("collectionKey") or "").strip()
    if collection_key:
        out["collectionKey"] = collection_key
    dedupe_by = str(item.get("dedupeBy") or "").strip()
    if dedupe_by:
        out["dedupeBy"] = dedupe_by
    max_items = item.get("maxItems")
    try:
        if max_items is not None and int(max_items) > 0:
            out["maxItems"] = int(max_items)
    except Exception:
        pass
    node_id = str(item.get("nodeId") or item.get("node_id") or "").strip()
    if node_id:
        out["nodeId"] = node_id
    transport = str(item.get("transport") or "").strip().lower()
    if transport in {"auto", "member", "hub"}:
        out["transport"] = transport
    snapshot_policy = str(item.get("snapshotPolicy") or "").strip().lower()
    if snapshot_policy in {"none", "on_subscribe", "on_subscribe_if_stale", "manual"}:
        out["snapshotPolicy"] = snapshot_policy
    ttl_ms = item.get("ttlMs")
    try:
        if ttl_ms is not None and int(ttl_ms) > 0:
            out["ttlMs"] = int(ttl_ms)
    except Exception:
        pass
    sequence_field = str(item.get("sequenceField") or "").strip()
    if sequence_field:
        out["sequenceField"] = sequence_field
    updated_at_field = str(item.get("updatedAtField") or "").strip()
    if updated_at_field:
        out["updatedAtField"] = updated_at_field
    for key in ("budget", "guardVisibility", "route"):
        value = item.get(key)
        if isinstance(value, Mapping):
            out[key] = _clone_json_like(value)
        elif key == "guardVisibility" and isinstance(value, str) and value.strip():
            out[key] = value.strip()
    if "initialState" in item:
        out["initialState"] = _clone_json_like(item.get("initialState"))
    return out


def _merge_webio_receivers(skill_decls: List[Dict[str, Any]]) -> Dict[str, Any]:
    receivers: Dict[str, Any] = {}
    for decl in skill_decls:
        skill_name = str(decl.get("skill") or "").strip()
        webio = decl.get("webio") if isinstance(decl.get("webio"), Mapping) else {}
        raw_receivers = webio.get("receivers") if isinstance(webio.get("receivers"), Mapping) else {}
        for key, value in raw_receivers.items():
            receiver_id = str(key or "").strip()
            if not receiver_id or receiver_id in receivers:
                continue
            normalized = _normalize_webio_receiver(value)
            if not normalized:
                continue
            normalized["id"] = receiver_id
            if skill_name:
                normalized["origin"] = f"skill:{skill_name}"
            receivers[receiver_id] = normalized
    return {"receivers": receivers}


def _read_node_scoped_scenario_entry(
    scenarios_root: Any,
    scenario_id: str,
    *,
    node_id: str | None = None,
) -> Dict[str, Any]:
    target_node_id = str(node_id or "").strip() or _local_node_id()

    local_bucket = _mapping_get(scenarios_root or {}, target_node_id) or {}
    local_entry = _coerce_dict(_mapping_get(local_bucket, scenario_id) or {})
    if local_entry:
        return local_entry

    root_items = _mapping_items(scenarios_root or {}) or []
    for _bucket_key, maybe_bucket in root_items:
        entry = _coerce_dict(_mapping_get(maybe_bucket or {}, scenario_id) or {})
        if entry:
            return entry
    return {}


def _read_effective_branch_fingerprints(registry_map: Any) -> Dict[str, str]:
    runtime_meta = _coerce_dict(registry_map.get("runtime_meta") or {})
    stored = _coerce_dict(runtime_meta.get(_RUNTIME_META_EFFECTIVE_BRANCH_FINGERPRINTS_KEY) or {})
    fingerprints: Dict[str, str] = {}
    for path in _EFFECTIVE_BRANCH_PATHS:
        token = str(stored.get(path) or "").strip()
        if token:
            fingerprints[path] = token
    return fingerprints


def _write_effective_branch_fingerprints(
    registry_map: Any,
    txn: Any,
    *,
    current: Mapping[str, str],
    updates: Mapping[str, str],
) -> bool:
    runtime_meta = _coerce_dict(registry_map.get("runtime_meta") or {})
    next_runtime_meta = dict(runtime_meta)
    next_fingerprints = _coerce_dict(next_runtime_meta.get(_RUNTIME_META_EFFECTIVE_BRANCH_FINGERPRINTS_KEY) or {})
    changed = False
    for path in _EFFECTIVE_BRANCH_PATHS:
        current_value = str(current.get(path) or "").strip()
        next_value = str(updates.get(path) or current_value).strip()
        if not next_value:
            continue
        if str(next_fingerprints.get(path) or "").strip() == next_value:
            continue
        next_fingerprints[path] = next_value
        changed = True
    if not changed:
        return False
    next_runtime_meta[_RUNTIME_META_EFFECTIVE_BRANCH_FINGERPRINTS_KEY] = next_fingerprints
    _set_map_value_if_changed(registry_map, txn, "runtime_meta", next_runtime_meta)
    return True


def _has_effective_branch_value(y_map: Any, key: str) -> bool:
    try:
        return y_map.get(key) is not None
    except Exception:
        return False


def _resolver_cache_keys(inputs: WebspaceResolverInputs) -> Dict[str, str]:
    scenario_snapshot = {
        "scenario_id": inputs.scenario_id,
        "source_mode": inputs.source_mode,
        "scenario_source": inputs.scenario_source,
        "legacy_scenario_fallback": inputs.legacy_scenario_fallback,
        "scenario_application": inputs.scenario_application,
        "scenario_catalog": inputs.scenario_catalog,
        "scenario_registry": inputs.scenario_registry,
    }
    skill_decls_fingerprint = str(getattr(inputs, "skill_decls_fingerprint", "") or "").strip()
    return {
        "scenario": _fingerprint_json_like(scenario_snapshot),
        "skills": skill_decls_fingerprint or _fingerprint_json_like(inputs.skill_decls),
        "overlay": _fingerprint_json_like(inputs.overlay_snapshot),
        "live": _fingerprint_json_like(inputs.live_state),
        "desktop_scenarios": _fingerprint_json_like(inputs.desktop_scenarios),
    }


def _resolver_input_fingerprint(inputs: WebspaceResolverInputs, *, cache_keys: Mapping[str, Any]) -> str:
    snapshot = {
        "webspace_id": inputs.webspace_id,
        "scenario_id": inputs.scenario_id,
        "source_mode": inputs.source_mode,
        "scenario_source": inputs.scenario_source,
        "legacy_scenario_fallback": inputs.legacy_scenario_fallback,
        "metadata": inputs.metadata,
        "cache_keys": dict(cache_keys),
    }
    return _fingerprint_json_like(snapshot)


def _resolver_core_fingerprint(inputs: WebspaceResolverInputs) -> str:
    cache_keys = _resolver_cache_keys(inputs)
    materialization = _coerce_dict(_coerce_dict(inputs.metadata or {}).get("materialization") or {})
    identity = _coerce_dict(materialization.get("identity") or {})
    access_scope = {
        "user_id": str(identity.get("user_id") or "guest"),
        "roles_hash": str(identity.get("roles_hash") or ""),
        "policy_fingerprint": str(identity.get("policy_fingerprint") or ""),
        "revision": str(identity.get("revision") or ""),
    }
    return _fingerprint_json_like(
        {
            "scenario_id": inputs.scenario_id,
            "source_mode": inputs.source_mode,
            "scenario_source": inputs.scenario_source,
            "legacy_scenario_fallback": inputs.legacy_scenario_fallback,
            "scenario": cache_keys.get("scenario"),
            "skills": cache_keys.get("skills"),
            "desktop_scenarios": cache_keys.get("desktop_scenarios"),
            "access_scope": access_scope,
        }
    )


def _debug_page_signature_from_application(application: Mapping[str, Any] | None) -> dict[str, Any]:
    app = _coerce_dict(application or {})
    desktop = _coerce_dict(app.get("desktop") or {})
    page = _coerce_dict(desktop.get("pageSchema") or {})
    widgets = page.get("widgets") if isinstance(page.get("widgets"), list) else []
    cards = next(
        (
            item
            for item in widgets
            if isinstance(item, Mapping) and str(item.get("id") or "") in {"prototype-cards", "items_cards"}
        ),
        {},
    )
    inputs = _coerce_dict(cards.get("inputs") if isinstance(cards, Mapping) else {})
    preview_key = str(inputs.get("previewKey") or "").strip()
    rows = []
    if isinstance(cards, Mapping):
        data_source = _coerce_dict(cards.get("dataSource") or {})
        rows = data_source.get("value") if isinstance(data_source.get("value"), list) else []
    first_row = rows[0] if rows and isinstance(rows[0], Mapping) else {}
    return {
        "title": str(page.get("title") or "").strip() or None,
        "firstTitle": str(first_row.get("title") or "").strip() or None,
        "previewKey": preview_key or None,
        "firstPreview": first_row.get(preview_key) if preview_key and isinstance(first_row, Mapping) else None,
    }


def _materialized_ydoc_default_decls(decls: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only declaration fields consumed while applying a resolved payload."""

    compact: List[Dict[str, Any]] = []
    for decl in decls:
        defaults = decl.get("ydoc_defaults")
        if not isinstance(defaults, Mapping) or not defaults:
            continue
        item: Dict[str, Any] = {"ydoc_defaults": _clone_json_like(defaults)}
        for key in ("skill", "node_id", "ui_owner"):
            value = decl.get(key)
            if value is not None and str(value).strip():
                item[key] = value
        compact.append(item)
    return compact


def _resolved_outputs_to_cache_payload(resolved: WebspaceResolverOutputs) -> Dict[str, Any]:
    payload = {
        "webspace_id": str(resolved.webspace_id or ""),
        "scenario_id": str(resolved.scenario_id or ""),
        "source_mode": str(resolved.source_mode or ""),
        "application": _clone_json_like(resolved.application),
        "catalog": _clone_json_like(resolved.catalog),
        "registry": _clone_json_like(resolved.registry),
        "installed": _clone_json_like(resolved.installed),
        "desktop": _clone_json_like(resolved.desktop),
        "webio": _clone_json_like(resolved.webio),
        "routing": _clone_json_like(resolved.routing),
        "skill_decls": _materialized_ydoc_default_decls(resolved.skill_decls),
    }
    _materialized_payload_branch_fingerprints(payload)
    return payload


def _resolved_outputs_to_materialized_payload(
    resolved: WebspaceResolverOutputs,
    *,
    inputs: WebspaceResolverInputs | None = None,
) -> Dict[str, Any]:
    # Resolver outputs are private plain-Python values at this boundary. The
    # YDoc apply path treats them as immutable, so another full JSON roundtrip
    # only burns route budget and doubles peak allocations.
    payload = {
        "webspace_id": str(resolved.webspace_id or ""),
        "scenario_id": str(resolved.scenario_id or ""),
        "source_mode": str(resolved.source_mode or ""),
        "application": resolved.application,
        "catalog": resolved.catalog,
        "registry": resolved.registry,
        "installed": resolved.installed,
        "desktop": resolved.desktop,
        "webio": resolved.webio,
        "routing": resolved.routing,
        "skill_decls": _materialized_ydoc_default_decls(resolved.skill_decls),
    }
    _materialized_payload_branch_fingerprints(payload)
    payload["schema"] = "adaos.webspace.materialized_payload.v1"
    if inputs is not None:
        payload["metadata"] = _clone_json_like(inputs.metadata)
        skill_decls_fingerprint = str(getattr(inputs, "skill_decls_fingerprint", "") or "").strip()
        if skill_decls_fingerprint:
            payload["skill_decls_fingerprint"] = skill_decls_fingerprint
        payload["compatibility_cache_presence"] = {
            str(key): bool(value)
            for key, value in (inputs.compatibility_cache_presence or {}).items()
            if str(key).strip()
        }
        payload["scenario_source"] = str(inputs.scenario_source or "")
        payload["legacy_scenario_fallback"] = bool(inputs.legacy_scenario_fallback)
    return payload


def _resolved_outputs_from_cache_payload(payload: Mapping[str, Any]) -> WebspaceResolverOutputs:
    return WebspaceResolverOutputs(
        webspace_id=str(payload.get("webspace_id") or ""),
        scenario_id=str(payload.get("scenario_id") or ""),
        source_mode=str(payload.get("source_mode") or ""),
        application=_coerce_dict(payload.get("application") or {}),
        catalog=_coerce_dict(payload.get("catalog") or {}),
        registry=_coerce_dict(payload.get("registry") or {}),
        installed=_coerce_dict(payload.get("installed") or {}),
        desktop=_coerce_dict(payload.get("desktop") or {}),
        webio=_coerce_dict(payload.get("webio") or {}),
        routing=_coerce_dict(payload.get("routing") or {}),
        skill_decls=[
            dict(item)
            for item in (payload.get("skill_decls") or [])
            if isinstance(item, Mapping)
        ],
    )


def _materialized_payload_inputs(
    webspace_id: str,
    payload: Mapping[str, Any],
    resolved: WebspaceResolverOutputs,
    *,
    materialization_identity: Mapping[str, Any] | None = None,
) -> WebspaceResolverInputs:
    metadata = _coerce_dict(payload.get("metadata") or {})
    if materialization_identity is not None or not isinstance(metadata.get("materialization"), Mapping):
        metadata["materialization"] = _scenario_materialization_contract(
            resolved.scenario_id,
            source_mode=resolved.source_mode,
            identity=materialization_identity,
        )
    compatibility = _coerce_dict(payload.get("compatibility_cache_presence") or {})
    return WebspaceResolverInputs(
        webspace_id=str(webspace_id or resolved.webspace_id or ""),
        scenario_id=str(resolved.scenario_id or ""),
        source_mode=str(resolved.source_mode or ""),
        metadata=metadata,
        compatibility_cache_presence={str(key): bool(value) for key, value in compatibility.items()},
        skill_decls=[dict(item) for item in (resolved.skill_decls or []) if isinstance(item, Mapping)],
        skill_decls_fingerprint=str(payload.get("skill_decls_fingerprint") or ""),
        scenario_source=str(payload.get("scenario_source") or metadata.get("scenario_source") or "materialized_payload"),
        legacy_scenario_fallback=bool(payload.get("legacy_scenario_fallback")),
    )


def _get_cached_resolved_outputs(fingerprint: str) -> WebspaceResolverOutputs | None:
    token = str(fingerprint or "").strip()
    if not token:
        return None
    cached = _RESOLVED_WEBSPACE_CACHE.get(token)
    if not isinstance(cached, Mapping):
        return None
    _RESOLVED_WEBSPACE_CACHE.move_to_end(token)
    return _resolved_outputs_from_cache_payload(cached)


def _approximate_cache_size_bytes(value: Any, seen: set[int] | None = None) -> int:
    visited = seen if seen is not None else set()
    object_id = id(value)
    if object_id in visited:
        return 0
    visited.add(object_id)
    size = int(sys.getsizeof(value, 0))
    if isinstance(value, Mapping):
        return size + sum(
            _approximate_cache_size_bytes(key, visited)
            + _approximate_cache_size_bytes(item, visited)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return size + sum(_approximate_cache_size_bytes(item, visited) for item in value)
    return size


def _resolved_cache_size_bytes(payload: Mapping[str, Any]) -> int:
    """Estimate retained Python memory without recursively walking the UI tree.

    Resolved payloads are JSON-like. CPython container overhead for the current
    catalogs is about 4.4x their compact JSON representation, so a factor of
    five keeps the byte limit conservative while leaving the traversal to the
    C JSON encoder.
    """

    try:
        encoded_size = len(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return encoded_size * 5
    except (TypeError, ValueError, OverflowError):
        return _approximate_cache_size_bytes(payload)


def _cache_byte_limit(name: str, default_mb: int) -> int:
    try:
        value = int(str(os.getenv(name) or default_mb).strip())
    except Exception:
        value = default_mb
    return max(1, value) * 1024 * 1024


def _remember_resolved_outputs(fingerprint: str, resolved: WebspaceResolverOutputs) -> None:
    token = str(fingerprint or "").strip()
    if not token:
        return
    payload = _resolved_outputs_to_cache_payload(resolved)
    payload_size = _resolved_cache_size_bytes(payload)
    max_bytes = _cache_byte_limit("ADAOS_WEBSPACE_RESOLVED_CACHE_MAX_MB", 32)
    if payload_size > max_bytes:
        return
    payload["_cache_size_bytes"] = payload_size
    _RESOLVED_WEBSPACE_CACHE[token] = payload
    _RESOLVED_WEBSPACE_CACHE.move_to_end(token)
    while (
        len(_RESOLVED_WEBSPACE_CACHE) > _RESOLVED_WEBSPACE_CACHE_LIMIT
        or sum(int(item.get("_cache_size_bytes") or 0) for item in _RESOLVED_WEBSPACE_CACHE.values())
        > max_bytes
    ):
        _RESOLVED_WEBSPACE_CACHE.popitem(last=False)


def _materialized_webspace_cache_enabled() -> bool:
    return _env_flag_default_enabled("ADAOS_WEBSPACE_MATERIALIZATION_CACHE")


def _materialized_webspace_cache_limit() -> int:
    raw = os.getenv("ADAOS_WEBSPACE_MATERIALIZATION_CACHE_LIMIT")
    try:
        value = int(str(raw or _MATERIALIZED_WEBSPACE_CACHE_LIMIT).strip())
    except Exception:
        value = _MATERIALIZED_WEBSPACE_CACHE_LIMIT
    return max(0, min(value, 64))


def _materialized_webspace_disk_cache_enabled() -> bool:
    return _env_flag_default_enabled("ADAOS_WEBSPACE_MATERIALIZATION_DISK_CACHE")


def _materialized_webspace_disk_cache_limit() -> int:
    raw = os.getenv("ADAOS_WEBSPACE_MATERIALIZATION_DISK_CACHE_LIMIT")
    try:
        value = int(str(raw or "128").strip())
    except Exception:
        value = 128
    return max(0, min(value, 4096))


def _materialized_webspace_cache_dir() -> Path | None:
    override = str(os.getenv("ADAOS_WEBSPACE_MATERIALIZATION_CACHE_DIR") or "").strip()
    if override:
        return Path(override)
    try:
        from adaos.services.runtime_paths import current_state_dir

        return Path(current_state_dir()) / "scenario" / "materialization_cache"
    except Exception:
        return None


def _materialized_webspace_cache_key(
    identity: Mapping[str, Any] | None,
    *,
    cache_mode: str = "fresh_doc",
) -> str | None:
    if not isinstance(identity, Mapping):
        return None
    key_hash = str(identity.get("key_hash") or "").strip()
    key = str(identity.get("key") or "").strip()
    if key_hash:
        base_key = key_hash
    elif key:
        base_key = hashlib.sha1(key.encode("utf-8", errors="replace")).hexdigest()[:16]
    else:
        return None
    mode = str(cache_mode or "fresh_doc").strip() or "fresh_doc"
    if mode == "fresh_doc":
        return base_key
    return hashlib.sha1(f"{mode}:{base_key}".encode("utf-8", errors="replace")).hexdigest()[:16]


def _materialized_webspace_disk_cache_path(cache_key: str) -> Path | None:
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(cache_key or "").strip()).strip(".:-_")
    if not token:
        return None
    root = _materialized_webspace_cache_dir()
    if root is None:
        return None
    return root / f"{token}.json"


def _encode_cache_bytes(value: bytes) -> str:
    return base64.b64encode(bytes(value or b"")).decode("ascii")


def _decode_cache_bytes(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        return b""
    return base64.b64decode(value.encode("ascii"), validate=False)


def _clone_materialized_worker_result(
    value: Mapping[str, Any],
    *,
    cache_key: str,
    require_snapshot: bool = True,
) -> Dict[str, Any] | None:
    payload = value.get("materialized_payload") if isinstance(value.get("materialized_payload"), Mapping) else None
    if payload is None:
        return None
    payload = _clone_json_like(payload)
    if not isinstance(payload, dict):
        return None
    snapshot_update = bytes(value.get("snapshot_update") or b"")
    if require_snapshot and not snapshot_update:
        return None
    _materialized_payload_branch_fingerprints(payload)
    try:
        resolved = _resolved_outputs_from_cache_payload(payload)
        entry = resolved.to_registry_entry()
    except Exception:
        return None
    original_rebuild_timings = _copy_timing_map(value.get("rebuild_timings_ms")) or {}
    original_ydoc_timings = _copy_timing_map(value.get("ydoc_timings_ms")) or {}
    original_apply_summary = dict(value.get("apply_summary") or {}) if isinstance(value.get("apply_summary"), Mapping) else {}
    apply_summary = dict(original_apply_summary)
    apply_summary.update(
        {
            "materialization_cache_hit": True,
            "changed_branches": 0,
            "unchanged_branches": len(_EFFECTIVE_BRANCH_PATHS),
        }
    )
    return {
        "entry": entry,
        "snapshot_update": snapshot_update,
        "state_vector": bytes(value.get("state_vector") or b""),
        "materialized_payload": payload,
        "rebuild_timings_ms": {
            "materialization_cache_hit": 0.0,
            "cached_original_total": float(original_rebuild_timings.get("total") or 0.0),
            "total": 0.0,
        },
        "resolver_debug": {
            "source": "materialization_cache",
            "cache_hit": True,
            "materialization_cache_key": cache_key,
            "cached_original": dict(value.get("resolver_debug") or {}),
        },
        "apply_summary": apply_summary,
        "apply_phase_timings_ms": {},
        "ydoc_timings_ms": {
            "materialization_cache_hit": 0.0,
            "cached_original_total": float(original_ydoc_timings.get("total") or 0.0),
            "total": 0.0,
        },
        "materialization_cache": {
            "hit": True,
            "key": cache_key,
            "created_at": value.get("created_at"),
            "mode": str(value.get("cache_mode") or "fresh_doc"),
        },
    }


def _remember_materialized_worker_result_in_memory(
    cache_key: str,
    value: Mapping[str, Any],
) -> None:
    cached_value = {
        "created_at": value.get("created_at") or time.time(),
        "snapshot_update": bytes(value.get("snapshot_update") or b""),
        "state_vector": bytes(value.get("state_vector") or b""),
        "materialized_payload": _clone_json_like(value.get("materialized_payload") or {}),
        "rebuild_timings_ms": _copy_timing_map(value.get("rebuild_timings_ms")) or {},
        "resolver_debug": dict(value.get("resolver_debug") or {}),
        "apply_summary": dict(value.get("apply_summary") or {}) if isinstance(value.get("apply_summary"), Mapping) else {},
        "ydoc_timings_ms": _copy_timing_map(value.get("ydoc_timings_ms")) or {},
        "identity": dict(value.get("identity") or {}) if isinstance(value.get("identity"), Mapping) else {},
        "cache_mode": str(value.get("cache_mode") or "fresh_doc"),
    }
    cached_size = _approximate_cache_size_bytes(cached_value)
    max_bytes = _cache_byte_limit("ADAOS_WEBSPACE_MATERIALIZATION_CACHE_MAX_MB", 64)
    if cached_size > max_bytes:
        return
    cached_value["_cache_size_bytes"] = cached_size
    _MATERIALIZED_WEBSPACE_CACHE[cache_key] = cached_value
    _MATERIALIZED_WEBSPACE_CACHE.move_to_end(cache_key)
    while (
        len(_MATERIALIZED_WEBSPACE_CACHE) > _materialized_webspace_cache_limit()
        or sum(int(item.get("_cache_size_bytes") or 0) for item in _MATERIALIZED_WEBSPACE_CACHE.values())
        > max_bytes
    ):
        _MATERIALIZED_WEBSPACE_CACHE.popitem(last=False)


def _load_materialized_worker_result_from_disk(
    cache_key: str,
    *,
    require_snapshot: bool = True,
) -> Dict[str, Any] | None:
    if not _materialized_webspace_disk_cache_enabled():
        return None
    path = _materialized_webspace_disk_cache_path(cache_key)
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None
    if not isinstance(raw, Mapping) or raw.get("schema") != _MATERIALIZED_WEBSPACE_DISK_CACHE_SCHEMA:
        return None
    payload = raw.get("materialized_payload") if isinstance(raw.get("materialized_payload"), Mapping) else None
    snapshot_update = _decode_cache_bytes(raw.get("snapshot_update_b64"))
    if not payload or (require_snapshot and not snapshot_update):
        return None
    value: Dict[str, Any] = {
        "created_at": raw.get("created_at") or path.stat().st_mtime,
        "snapshot_update": snapshot_update,
        "state_vector": _decode_cache_bytes(raw.get("state_vector_b64")),
        "materialized_payload": payload,
        "rebuild_timings_ms": _copy_timing_map(raw.get("rebuild_timings_ms")) or {},
        "resolver_debug": dict(raw.get("resolver_debug") or {}),
        "apply_summary": dict(raw.get("apply_summary") or {}) if isinstance(raw.get("apply_summary"), Mapping) else {},
        "ydoc_timings_ms": _copy_timing_map(raw.get("ydoc_timings_ms")) or {},
        "identity": dict(raw.get("identity") or {}) if isinstance(raw.get("identity"), Mapping) else {},
        "cache_mode": str(raw.get("cache_mode") or "fresh_doc"),
    }
    cloned = _clone_materialized_worker_result(value, cache_key=cache_key, require_snapshot=require_snapshot)
    if cloned is None:
        return None
    _remember_materialized_worker_result_in_memory(cache_key, value)
    try:
        path.touch()
    except Exception:
        pass
    cloned.setdefault("materialization_cache", {})["source"] = "disk"
    return cloned


def _prune_materialized_disk_cache(root: Path) -> None:
    limit = _materialized_webspace_disk_cache_limit()
    if limit <= 0:
        return
    try:
        files = sorted(
            [path for path in root.glob("*.json") if path.is_file()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return
    for path in files[limit:]:
        try:
            path.unlink()
        except Exception:
            pass


def _remember_materialized_worker_result_on_disk(
    cache_key: str,
    value: Mapping[str, Any],
    *,
    cache_mode: str = "fresh_doc",
    require_snapshot: bool = True,
) -> None:
    if not _materialized_webspace_disk_cache_enabled() or _materialized_webspace_disk_cache_limit() <= 0:
        return
    path = _materialized_webspace_disk_cache_path(cache_key)
    if path is None:
        return
    payload = value.get("materialized_payload") if isinstance(value.get("materialized_payload"), Mapping) else None
    snapshot_update = bytes(value.get("snapshot_update") or b"")
    if not payload or (require_snapshot and not snapshot_update):
        return
    record = {
        "schema": _MATERIALIZED_WEBSPACE_DISK_CACHE_SCHEMA,
        "cache_key": cache_key,
        "cache_mode": str(cache_mode or "fresh_doc").strip() or "fresh_doc",
        "created_at": value.get("created_at") or time.time(),
        "snapshot_update_b64": _encode_cache_bytes(snapshot_update),
        "state_vector_b64": _encode_cache_bytes(bytes(value.get("state_vector") or b"")),
        "materialized_payload": payload,
        "rebuild_timings_ms": _copy_timing_map(value.get("rebuild_timings_ms")) or {},
        "resolver_debug": dict(value.get("resolver_debug") or {}),
        "apply_summary": dict(value.get("apply_summary") or {}) if isinstance(value.get("apply_summary"), Mapping) else {},
        "ydoc_timings_ms": _copy_timing_map(value.get("ydoc_timings_ms")) or {},
        "identity": dict(value.get("identity") or {}) if isinstance(value.get("identity"), Mapping) else {},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        _prune_materialized_disk_cache(path.parent)
    except Exception:
        try:
            if "tmp" in locals() and tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _get_cached_materialized_worker_result(
    identity: Mapping[str, Any] | None,
    *,
    cache_mode: str = "fresh_doc",
    require_snapshot: bool = True,
) -> Dict[str, Any] | None:
    if not _materialized_webspace_cache_enabled():
        return None
    key = _materialized_webspace_cache_key(identity, cache_mode=cache_mode)
    if not key:
        return None
    cached = _MATERIALIZED_WEBSPACE_CACHE.get(key)
    if not isinstance(cached, Mapping):
        return _load_materialized_worker_result_from_disk(key, require_snapshot=require_snapshot)
    cloned = _clone_materialized_worker_result(cached, cache_key=key, require_snapshot=require_snapshot)
    if cloned is None:
        _MATERIALIZED_WEBSPACE_CACHE.pop(key, None)
        return _load_materialized_worker_result_from_disk(key, require_snapshot=require_snapshot)
    _MATERIALIZED_WEBSPACE_CACHE.move_to_end(key)
    return cloned


def _remember_materialized_worker_result(
    identity: Mapping[str, Any] | None,
    worker_result: Mapping[str, Any],
    *,
    cache_mode: str = "fresh_doc",
    require_snapshot: bool = True,
) -> None:
    if not _materialized_webspace_cache_enabled():
        return
    limit = _materialized_webspace_cache_limit()
    if limit <= 0:
        _MATERIALIZED_WEBSPACE_CACHE.clear()
        return
    key = _materialized_webspace_cache_key(identity, cache_mode=cache_mode)
    payload = worker_result.get("materialized_payload") if isinstance(worker_result.get("materialized_payload"), Mapping) else None
    snapshot_update = bytes(worker_result.get("snapshot_update") or b"")
    if not key or not payload or (require_snapshot and not snapshot_update):
        return
    value = {
        "created_at": time.time(),
        "snapshot_update": snapshot_update,
        "state_vector": bytes(worker_result.get("state_vector") or b""),
        "materialized_payload": payload,
        "rebuild_timings_ms": _copy_timing_map(worker_result.get("rebuild_timings_ms")) or {},
        "resolver_debug": dict(worker_result.get("resolver_debug") or {}),
        "apply_summary": dict(worker_result.get("apply_summary") or {}) if isinstance(worker_result.get("apply_summary"), Mapping) else {},
        "ydoc_timings_ms": _copy_timing_map(worker_result.get("ydoc_timings_ms")) or {},
        "identity": dict(identity or {}),
        "cache_mode": str(cache_mode or "fresh_doc").strip() or "fresh_doc",
    }
    _remember_materialized_worker_result_in_memory(key, value)
    _remember_materialized_worker_result_on_disk(
        key,
        value,
        cache_mode=cache_mode,
        require_snapshot=require_snapshot,
    )


def _materialized_cache_value_matches(
    value: Mapping[str, Any],
    *,
    webspace_id: str,
    scenario_id: str | None = None,
) -> bool:
    identity = value.get("identity") if isinstance(value.get("identity"), Mapping) else {}
    if not identity:
        return False
    if str(identity.get("webspace_id") or "").strip() != str(webspace_id or "").strip():
        return False
    scenario_token = str(scenario_id or "").strip()
    if scenario_token and str(identity.get("scenario_id") or "").strip() != scenario_token:
        return False
    return True


def _drop_materialized_cache_for_webspace(webspace_id: str, *, scenario_id: str | None = None) -> dict[str, int]:
    target = str(webspace_id or "").strip()
    if not target:
        return {"memory": 0, "disk": 0}
    memory_removed = 0
    for key, value in list(_MATERIALIZED_WEBSPACE_CACHE.items()):
        if isinstance(value, Mapping) and _materialized_cache_value_matches(value, webspace_id=target, scenario_id=scenario_id):
            _MATERIALIZED_WEBSPACE_CACHE.pop(key, None)
            memory_removed += 1
    disk_removed = 0
    root = _materialized_webspace_cache_dir()
    if root is not None and root.exists():
        for path in root.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(raw, Mapping) and _materialized_cache_value_matches(raw, webspace_id=target, scenario_id=scenario_id):
                try:
                    path.unlink()
                    disk_removed += 1
                except Exception:
                    pass
    return {"memory": memory_removed, "disk": disk_removed}


def _invalidate_resolved_webspace_cache(*, scenario_id: str | None = None, reason: str | None = None) -> int:
    count = len(_RESOLVED_WEBSPACE_CACHE)
    if count:
        _RESOLVED_WEBSPACE_CACHE.clear()
    try:
        _log.debug(
            "invalidated resolved webspace cache scenario=%s reason=%s count=%d",
            str(scenario_id or "").strip() or "-",
            str(reason or "").strip() or "-",
            count,
        )
    except Exception:
        pass
    return count


def _set_map_value_if_changed(y_map: Any, txn: Any, key: str, value: Any) -> tuple[bool, str]:
    next_items = _mapping_items(value)
    try:
        current = y_map.get(key)
    except Exception:
        current = None
    if next_items is not None:
        if _is_y_map_value(current):
            return _reconcile_attached_y_map(current, txn, value), "diff"
        if _json_like_equal(current, value):
            attached = _attach_empty_y_map(y_map, txn, key)
            if attached is not None:
                _reconcile_attached_y_map(attached, txn, value)
                return True, "diff"
            return False, "diff"
        attached = _attach_empty_y_map(y_map, txn, key)
        if attached is not None:
            _reconcile_attached_y_map(attached, txn, value)
            return True, "diff"
        y_map.set(txn, key, _clone_json_like(value))
        return True, "replace"
    if _json_like_equal(current, value):
        return False, "replace"
    y_map.set(txn, key, _clone_json_like(value))
    return True, "replace"


def _replace_map_value(y_map: Any, txn: Any, key: str, value: Any) -> tuple[bool, str]:
    y_map.set(txn, key, _clone_json_like(value))
    return True, "replace"


def _changed_direct_mapping_keys(previous_value: Any, next_value: Any) -> set[str] | None:
    previous_items = _mapping_items(previous_value)
    next_items = _mapping_items(next_value)
    if previous_items is None or next_items is None:
        return None
    previous_lookup = {key: item for key, item in previous_items}
    next_lookup = {key: item for key, item in next_items}
    changed: set[str] = set()
    for child_key in set(previous_lookup) | set(next_lookup):
        if child_key not in previous_lookup or child_key not in next_lookup:
            changed.add(child_key)
            continue
        if not _json_like_equal(previous_lookup[child_key], next_lookup[child_key]):
            changed.add(child_key)
    return changed


def _patch_attached_y_map_from_previous(
    node: Any,
    txn: Any,
    next_value: Any,
    previous_value: Any,
    *,
    depth: int = 0,
) -> bool:
    if depth > 16:
        return False
    changed_keys = _changed_direct_mapping_keys(previous_value, next_value)
    if changed_keys is None:
        return False
    if not changed_keys:
        return False
    next_items = _mapping_items(next_value) or []
    previous_items = _mapping_items(previous_value) or []
    next_lookup = {key: item for key, item in next_items}
    previous_lookup = {key: item for key, item in previous_items}
    changed = False
    for child_key in sorted(changed_keys):
        if child_key not in next_lookup:
            try:
                node.pop(txn, child_key)
                changed = True
            except Exception:
                continue
            continue
        raw_child = next_lookup[child_key]
        previous_child = previous_lookup.get(child_key)
        try:
            current_child = node.get(child_key)
        except Exception:
            current_child = None
        if (
            _is_y_map_value(current_child)
            and _mapping_items(raw_child) is not None
            and _mapping_items(previous_child) is not None
        ):
            if _patch_attached_y_map_from_previous(
                current_child,
                txn,
                raw_child,
                previous_child,
                depth=depth + 1,
            ):
                changed = True
            continue
        node.set(txn, child_key, _clone_json_like(raw_child))
        changed = True
    return changed


def _patch_map_value_from_previous(y_map: Any, txn: Any, key: str, value: Any, previous_value: Any) -> tuple[bool, str]:
    try:
        current = y_map.get(key)
    except Exception:
        current = None
    if not _is_y_map_value(current):
        return _set_map_value_if_changed(y_map, txn, key, value)
    if _mapping_items(value) is None or _mapping_items(previous_value) is None:
        return _set_map_value_if_changed(y_map, txn, key, value)
    changed = _patch_attached_y_map_from_previous(current, txn, value, previous_value)
    return changed, "patch"


def _merge_installed_with_auto(installed: Dict[str, Any], *, auto_apps: set[str], auto_widgets: set[str]) -> Dict[str, List[str]]:
    """
    Merge existing installed apps/widgets with auto-installed ids while
    preserving user choices across scenario switches.

    Important: we do NOT drop ids that are not present in the current catalog,
    because switching scenarios would otherwise lose installed apps/widgets
    that become available again when returning to the previous scenario.
    """
    apps = _dedupe_str_list(installed.get("apps"))
    widgets = _dedupe_str_list(installed.get("widgets"))
    removed_apps = set(_dedupe_str_list(installed.get("removedApps")))
    removed_widgets = set(_dedupe_str_list(installed.get("removedWidgets")))

    for app_id in sorted(auto_apps):
        if app_id not in removed_apps and app_id not in apps:
            apps.append(app_id)
    for widget_id in sorted(auto_widgets):
        if widget_id not in removed_widgets and widget_id not in widgets:
            widgets.append(widget_id)

    return {"apps": apps, "widgets": widgets}


def _normalize_overlay_widget_entries(values: Any) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, Mapping):
            continue
        item = dict(value)
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        item["id"] = item_id
        if item.get("type") is not None:
            item["type"] = str(item.get("type"))
        out.append(item)
    return out


def _refresh_pinned_widgets_from_catalog_entries(
    pinned_widgets: Any,
    catalog_widgets: Any,
) -> List[Dict[str, Any]]:
    pinned = _normalize_overlay_widget_entries(pinned_widgets)
    if not pinned or not isinstance(catalog_widgets, list):
        return pinned
    catalog_by_id: Dict[str, Dict[str, Any]] = {}
    for raw in catalog_widgets:
        if not isinstance(raw, Mapping):
            continue
        item_id = str(raw.get("id") or "").strip()
        if item_id and item_id not in catalog_by_id:
            catalog_by_id[item_id] = dict(raw)
    if not catalog_by_id:
        return pinned
    refreshed: List[Dict[str, Any]] = []
    for item in pinned:
        base = catalog_by_id.get(str(item.get("id") or "").strip())
        if not isinstance(base, dict):
            refreshed.append(item)
            continue
        merged = dict(item)
        for key in (
            "type",
            "title",
            "source",
            "visibleIf",
            "icon",
            "origin",
            "node_id",
            "node_label",
            "node_compact_label",
            "node_index",
            "node_color",
            "node_local_id",
            "remote_id",
            "groupLabel",
            "dataSource",
            "inputs",
            "actions",
            "dev",
        ):
            if key in base:
                merged[key] = _clone_json_like(base[key])
        refreshed.append(merged)
    return refreshed


def _apply_webspace_overlay_to_resolved(
    core: WebspaceResolverOutputs,
    inputs: WebspaceResolverInputs,
) -> WebspaceResolverOutputs:
    """Clone scenario-invariant output and apply only webspace-owned state."""

    overlay = _coerce_dict(inputs.overlay_snapshot or {})
    installed = _merge_installed_with_auto(
        _coerce_dict(overlay.get("installed") or {}),
        auto_apps=set(_dedupe_str_list(core.installed.get("apps"))),
        auto_widgets=set(_dedupe_str_list(core.installed.get("widgets"))),
    )

    application = _coerce_dict(_clone_json_like(core.application))
    application_desktop = _coerce_dict(application.get("desktop") or {})
    core_pinned = _normalize_overlay_widget_entries(application_desktop.get("pinnedWidgets"))
    pinned_source = (
        _normalize_overlay_widget_entries(overlay.get("pinnedWidgets"))
        if "pinnedWidgets" in overlay
        else core_pinned
    )
    application_desktop["pinnedWidgets"] = _refresh_pinned_widgets_from_catalog_entries(
        pinned_source,
        core.catalog.get("widgets"),
    )
    for key, normalizer in (
        ("iconOrder", _dedupe_str_list),
        ("widgetOrder", _dedupe_str_list),
        ("hiddenSections", _dedupe_str_list),
    ):
        if key in overlay:
            raw = overlay.get(key)
            application_desktop[key] = normalizer(raw or [])
    application["desktop"] = application_desktop

    desktop = _coerce_dict(_clone_json_like(core.desktop))
    desktop["installed"] = _clone_json_like(installed)
    for key in (
        "topbar",
        "pageSchema",
        "pinnedWidgets",
        "iconOrder",
        "widgetOrder",
        "hiddenSections",
    ):
        desktop[key] = _clone_json_like(application_desktop.get(key))

    return WebspaceResolverOutputs(
        webspace_id=inputs.webspace_id,
        scenario_id=core.scenario_id,
        source_mode=core.source_mode,
        application=application,
        catalog=_coerce_dict(_clone_json_like(core.catalog)),
        registry=_coerce_dict(_clone_json_like(core.registry)),
        installed=installed,
        desktop=desktop,
        webio=_coerce_dict(_clone_json_like(core.webio)),
        routing=_coerce_dict(_clone_json_like(core.routing)),
        skill_decls=[dict(item) for item in core.skill_decls if isinstance(item, Mapping)],
    )


def _built_in_scenario_content(scenario_id: str) -> Dict[str, Any]:
    if str(scenario_id or "").strip() != "web_desktop":
        return {}
    try:
        app = json.loads(json.dumps(((SEED.get("ui") or {}).get("application") or {})))
        data = json.loads(json.dumps((SEED.get("data") or {})))
    except Exception:
        return {}
    catalog = data.get("catalog") if isinstance(data, dict) else {}
    if not isinstance(catalog, dict):
        catalog = {}
    return {
        "id": "web_desktop",
        "ui": {"application": app if isinstance(app, dict) else {}},
        "registry": {},
        "catalog": catalog,
        "data": data if isinstance(data, dict) else {},
    }


def _load_scenario_switch_content(scenario_id: str, *, space: str) -> Dict[str, Any]:
    content = scenarios_loader.read_content(scenario_id, space=space)
    if isinstance(content, dict) and content:
        return content
    fallback = _built_in_scenario_content(scenario_id)
    if fallback:
        _log.info("desktop.scenario.set: using built-in fallback content for scenario=%s", scenario_id)
        return fallback
    return {}


def _scenario_exists_for_switch(scenario_id: str, *, space: str) -> bool:
    if _built_in_scenario_content(scenario_id):
        return True
    try:
        return bool(scenarios_loader.scenario_exists(scenario_id, space=space))
    except Exception:
        return False


def _scenario_exists_for_source_mode(scenario_id: str | None, *, source_mode: str) -> bool | None:
    token = str(scenario_id or "").strip()
    if not token:
        return None
    return _scenario_exists_for_switch(token, space=_scenario_loader_space(source_mode))


def _build_webspace_validation(
    *,
    source_mode: str,
    stored_home_scenario: str | None,
    effective_home_scenario: str,
    current_scenario: str | None,
) -> dict[str, Any]:
    stored_home_exists = _scenario_exists_for_source_mode(stored_home_scenario, source_mode=source_mode)
    effective_home_exists = bool(_scenario_exists_for_source_mode(effective_home_scenario, source_mode=source_mode))
    current_exists = _scenario_exists_for_source_mode(current_scenario, source_mode=source_mode)

    degraded = False
    reason = None
    recommended_action = None
    if stored_home_scenario and stored_home_exists is False and current_scenario and current_exists is False:
        degraded = True
        reason = "current_and_home_scenario_missing"
        recommended_action = "reload_or_reset"
    elif current_scenario and current_exists is False:
        degraded = True
        reason = "current_scenario_missing"
        recommended_action = "reload_or_reset"
    elif stored_home_scenario and stored_home_exists is False:
        degraded = True
        reason = "home_scenario_missing"
        recommended_action = "go_home_or_set_home"
    elif effective_home_exists is False:
        degraded = True
        reason = "effective_home_scenario_missing"
        recommended_action = "set_home_or_reset"

    return {
        "stored_home_scenario_exists": stored_home_exists,
        "home_scenario_exists": effective_home_exists,
        "current_scenario_exists": current_exists,
        "degraded": degraded,
        "validation_reason": reason,
        "recommended_action": recommended_action,
    }


def _with_webspace_validation(
    *,
    source_mode: str,
    stored_home_scenario: str | None,
    effective_home_scenario: str,
    current_scenario: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload.update(
        _build_webspace_validation(
            source_mode=source_mode,
            stored_home_scenario=stored_home_scenario,
            effective_home_scenario=effective_home_scenario,
            current_scenario=current_scenario,
        )
    )
    return payload


def _preflight_validated_scenario(
    scenario_id: str | None,
    *,
    source_mode: str,
    resolution: str,
) -> tuple[str, str, dict[str, Any]]:
    requested = str(scenario_id or "").strip() or None
    requested_exists = _scenario_exists_for_source_mode(requested, source_mode=source_mode)
    if requested and requested_exists:
        return requested, resolution, {
            "requested_scenario_id": requested,
            "resolved_scenario_id": requested,
            "requested_scenario_exists": True,
            "fallback_applied": False,
            "reason": None,
        }

    fallback = "web_desktop"
    fallback_exists = bool(_scenario_exists_for_source_mode(fallback, source_mode=source_mode))
    if requested and fallback_exists:
        return fallback, f"{resolution}_fallback", {
            "requested_scenario_id": requested,
            "resolved_scenario_id": fallback,
            "requested_scenario_exists": bool(requested_exists),
            "fallback_applied": True,
            "reason": "scenario_missing",
        }

    return str(requested or ""), resolution, {
        "requested_scenario_id": requested,
        "resolved_scenario_id": requested,
        "requested_scenario_exists": bool(requested_exists),
        "fallback_applied": False,
        "reason": "scenario_missing" if requested else "scenario_unresolved",
    }


def _scenario_loader_space(source_mode: str) -> str:
    return "dev" if str(source_mode or "").strip().lower() == "dev" else "workspace"


def _materialization_path_stamp(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        stat = path.stat()
        return {
            "path": str(path.resolve()),
            "kind": "dir" if path.is_dir() else "file",
            "mtime_ns": int(stat.st_mtime_ns),
            "size": int(stat.st_size) if path.is_file() else 0,
        }
    except Exception:
        return None


def _skill_source_dir_for_materialization(paths: Any, skill_name: str, *, space: str) -> Path:
    base = paths.dev_skills_dir() if space == "dev" else paths.skills_dir()
    skill_dir = Path(base) / skill_name
    if (skill_dir / "webui.json").exists():
        return skill_dir
    try:
        repo_root_attr = getattr(paths, "repo_root", None)
        repo_root = repo_root_attr() if callable(repo_root_attr) else repo_root_attr
        if repo_root:
            fallback = Path(repo_root).expanduser().resolve() / ".adaos" / "workspace" / "skills" / skill_name
            if (fallback / "webui.json").exists():
                return fallback
    except Exception:
        pass
    return skill_dir


def _skill_sources_fingerprint_for_materialization(source_mode: str) -> str:
    mode = _scenario_loader_space(source_mode)
    cache_key = mode
    now = time.monotonic()
    cached = _SKILL_SOURCE_FINGERPRINT_CACHE.get(cache_key)
    if cached is not None and now - float(cached[0]) <= _skill_source_fingerprint_cache_ttl_s():
        return str(cached[1] or "")
    try:
        paths = get_ctx().paths
    except Exception:
        return ""
    try:
        capacity = get_local_capacity()
        skills = capacity.get("skills") if isinstance(capacity, Mapping) else []
    except Exception:
        skills = []
    if not isinstance(skills, list):
        skills = []

    selected: list[dict[str, Any]] = []
    for rec in skills:
        if not isinstance(rec, Mapping) or not rec.get("active", True):
            continue
        name = str(rec.get("name") or rec.get("id") or "").strip()
        if not name:
            continue
        selected.append(
            {
                "name": name,
                "version": str(rec.get("version") or "unknown"),
                "dev": bool(rec.get("dev", False)),
            }
        )
    if not any(item.get("name") == "web_desktop_skill" for item in selected):
        selected.append({"name": "web_desktop_skill", "version": "built-in", "dev": False})
    selected.sort(key=lambda item: str(item.get("name") or ""))

    stamps: list[dict[str, Any]] = []
    for item in selected:
        skill_name = str(item.get("name") or "").strip()
        if not skill_name:
            continue
        spaces = ["default"]
        if mode == "dev":
            spaces = ["dev", "default"]
        for space in spaces:
            try:
                skill_dir = _skill_source_dir_for_materialization(paths, skill_name, space=space)
            except Exception:
                continue
            for candidate in (skill_dir, skill_dir / "skill.yaml", skill_dir / "webui.json"):
                stamp = _materialization_path_stamp(Path(candidate))
                if stamp is not None:
                    stamp["skill"] = skill_name
                    stamp["space"] = space
                    stamps.append(stamp)
    fingerprint = _fingerprint_json_like(
        {
            "mode": mode,
            "skills": selected,
            "stamps": stamps,
        }
    )
    _SKILL_SOURCE_FINGERPRINT_CACHE[cache_key] = (now, fingerprint)
    return fingerprint


def _scenario_source_fingerprint_for_materialization(scenario_id: str, *, source_mode: str) -> str:
    token = str(scenario_id or "").strip()
    if not token:
        return ""
    loader_space = _scenario_loader_space(source_mode)
    try:
        source_fingerprint = scenarios_loader.scenario_source_fingerprint(token, space=loader_space)
    except Exception:
        source_fingerprint = ""
    if source_fingerprint:
        return f"{loader_space}:{source_fingerprint}"
    fallback = _built_in_scenario_content(token)
    if fallback:
        return f"{loader_space}:builtin:{_fingerprint_json_like(fallback)[:16]}"
    return f"{loader_space}:current"


def _scenario_switch_materialization_identity(
    *,
    webspace_id: str,
    scenario_id: str,
    source_mode: str,
) -> dict[str, Any] | None:
    target_webspace = str(webspace_id or "").strip()
    target_scenario = str(scenario_id or "").strip()
    if not target_webspace or not target_scenario:
        return None
    source_fingerprint = _scenario_source_fingerprint_for_materialization(
        target_scenario,
        source_mode=source_mode,
    )
    skill_fingerprint = _skill_sources_fingerprint_for_materialization(source_mode)
    return canonical_materialization_identity(
        webspace_id=target_webspace,
        scenario_id=target_scenario,
        source_fingerprint=source_fingerprint,
        policy_fingerprint=f"skills:{skill_fingerprint}" if skill_fingerprint else None,
    )


def _env_flag_enabled(name: str) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_flag_default_enabled(name: str) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _skill_decls_cache_ttl_s() -> float:
    raw = str(os.getenv("ADAOS_WEBSPACE_SKILL_DECLS_CACHE_TTL_S") or "").strip()
    if raw:
        try:
            return max(0.0, min(float(raw), 3600.0))
        except Exception:
            pass
    return _SKILL_DECLS_CACHE_TTL_S


def _skill_source_fingerprint_cache_ttl_s() -> float:
    raw = str(os.getenv("ADAOS_WEBSPACE_SKILL_SOURCE_FINGERPRINT_TTL_S") or "").strip()
    if raw:
        try:
            return max(0.0, min(float(raw), 3600.0))
        except Exception:
            pass
    return _SKILL_SOURCE_FINGERPRINT_CACHE_TTL_S


def _trust_previous_materialized_branch_fingerprints_enabled() -> bool:
    return _env_flag_default_enabled("ADAOS_WEBSPACE_TRUST_PREVIOUS_MATERIALIZED_BRANCH_FINGERPRINTS")


def _preserve_live_state_on_rebuild_enabled() -> bool:
    return _env_flag_enabled("ADAOS_WEBSPACE_REBUILD_PRESERVE_LIVE_STATE")


def _publish_live_room_during_rebuild_enabled() -> bool:
    return _env_flag_enabled("ADAOS_WEBSPACE_REBUILD_LIVE_ROOM_UPDATES")


def _publish_live_room_for_rebuild(action: str) -> bool:
    action_token = str(action or "").strip().lower()
    if action_token == "scenario_switch_rebuild":
        return False
    if action_token == "builder_revision_apply":
        return _env_flag_default_enabled("ADAOS_BUILDER_REVISION_LIVE_ROOM_UPDATES")
    return _publish_live_room_during_rebuild_enabled()


def _builder_revision_projection_refresh_enabled() -> bool:
    return _env_flag_enabled("ADAOS_BUILDER_REVISION_REFRESH_PROJECTION_RULES")


def _builder_revision_rebuild_prefers_live_room() -> bool:
    return _env_flag_enabled("ADAOS_BUILDER_REVISION_REBUILD_PREFER_LIVE_ROOM")


def _builder_revision_detached_direct_live_room_updates_enabled() -> bool:
    return _env_flag_enabled("ADAOS_BUILDER_REVISION_DETACHED_DIRECT_LIVE_ROOM_UPDATES")


def _semantic_rebuild_timeout_s(action: str) -> float | None:
    action_token = str(action or "").strip().lower()
    env_name = (
        "ADAOS_BUILDER_REVISION_REBUILD_TIMEOUT_S"
        if action_token == "builder_revision_apply"
        else "ADAOS_WEBSPACE_REBUILD_TIMEOUT_S"
    )
    raw = os.getenv(env_name)
    if raw is None and action_token != "builder_revision_apply":
        return None
    try:
        value = float(str(raw or "30").strip())
    except Exception:
        value = 30.0
    if value <= 0:
        return None
    return max(1.0, value)


def _refresh_live_room_after_rebuild_enabled() -> bool:
    return _env_flag_default_enabled("ADAOS_WEBSPACE_REBUILD_REFRESH_LIVE_ROOM")


def _rebuild_action_refreshes_live_room(action: str) -> bool:
    action_token = str(action or "").strip().lower()
    if action_token in {
        "scenario_switch_rebuild",
        "builder_revision_apply",
        "reload",
        "reset",
        "restore",
        "artifact_subscription_sync",
    }:
        return True
    return action_token.startswith("skill_") and action_token.endswith("_sync")


def _rebuild_action_applies_live_payload(action: str) -> bool:
    action_token = str(action or "").strip().lower()
    if action_token in {
        "scenario_switch_rebuild",
        "builder_revision_apply",
        "reload",
        "reset",
        "artifact_subscription_sync",
    }:
        return True
    return action_token.startswith("skill_") and action_token.endswith("_sync")


def _defer_live_room_refresh_for_rebuild(action: str) -> bool:
    action_token = str(action or "").strip()
    return action_token == "builder_revision_apply" and _env_flag_enabled(
        "ADAOS_BUILDER_REVISION_DEFER_LIVE_ROOM_REFRESH"
    )


def _live_room_refresh_debounce_s() -> float:
    raw = os.getenv("ADAOS_WEBSPACE_LIVE_ROOM_REFRESH_DEBOUNCE_S")
    try:
        value = float(str(raw or "").strip())
    except Exception:
        value = 0.2
    return max(0.0, min(value, 10.0))


def _scenario_switch_background_route_yield_s() -> float:
    raw = os.getenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_BACKGROUND_ROUTE_YIELD_S")
    if raw is None and _env_flag_enabled("ADAOS_TESTING"):
        return 0.0
    try:
        value = float(str(raw if raw is not None else "0.02").strip())
    except Exception:
        value = 0.02
    return max(0.0, min(value, 1.0))


def _builder_revision_fresh_doc_rebuild_enabled() -> bool:
    return _env_flag_default_enabled("ADAOS_BUILDER_REVISION_FRESH_DOC_REBUILD")


def _builder_revision_replace_ystore_snapshot_enabled() -> bool:
    return _env_flag_default_enabled("ADAOS_BUILDER_REVISION_REPLACE_YSTORE_SNAPSHOT")


def _scenario_switch_inline_listing_sync_enabled() -> bool:
    if _env_flag_enabled("ADAOS_TESTING") and os.getenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_INLINE_LISTING_SYNC") is None:
        return False
    return _env_flag_enabled("ADAOS_WEBSPACE_SCENARIO_SWITCH_INLINE_LISTING_SYNC")


def _defer_workflow_sync_for_rebuild(action: str) -> bool:
    if str(action or "").strip() != "scenario_switch_rebuild":
        return False
    if _env_flag_enabled("ADAOS_TESTING") and os.getenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_WORKFLOW_SYNC") is None:
        return False
    return _env_flag_default_enabled("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_WORKFLOW_SYNC")


def _workflow_sync_for_rebuild_enabled(action: str) -> bool:
    action_token = str(action or "").strip()
    if action_token != "scenario_switch_rebuild":
        return action_token in {"restore", "reload", "reset"}
    explicit = os.getenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_WORKFLOW_SYNC")
    if explicit is not None:
        return _env_flag_enabled("ADAOS_WEBSPACE_SCENARIO_SWITCH_WORKFLOW_SYNC")
    return _env_flag_enabled("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_WORKFLOW_SYNC")


def _workflow_sync_debounce_s() -> float:
    raw = os.getenv("ADAOS_WEBSPACE_WORKFLOW_SYNC_DEBOUNCE_S")
    try:
        value = float(str(raw or "").strip())
    except Exception:
        value = 0.2
    return max(0.0, min(value, 10.0))


def _materialization_worker_enabled() -> bool:
    explicit = os.getenv("ADAOS_MATERIALIZATION_WORKER")
    if explicit is not None:
        return _env_flag_enabled("ADAOS_MATERIALIZATION_WORKER")
    return not _env_flag_enabled("ADAOS_TESTING")


def _materialization_cpu_workers() -> int:
    raw = str(os.getenv("ADAOS_MATERIALIZATION_CPU_WORKERS") or "1").strip()
    try:
        value = int(raw)
    except Exception:
        value = 1
    return max(1, min(value, 4))


def _get_materialization_cpu_executor() -> Any:
    return _MATERIALIZATION_EXECUTOR.get(max_workers=_materialization_cpu_workers())


def _shutdown_materialization_cpu_executor() -> None:
    _MATERIALIZATION_EXECUTOR.shutdown()


async def _run_materialization_cpu(function: Any, /, *args: Any, **kwargs: Any) -> Any:
    # CLI one-shot execution has no persistent owner loop.  Running a resolver
    # that still holds any y_py-backed value in the shared CPU executor can
    # make its YDoc finalize on the worker thread during interpreter shutdown.
    # Keep this rare diagnostic/control path single-threaded; persistent API
    # runtimes continue to use the bounded executor.
    if str(os.getenv("ADAOS_DEV_TOOL_EXECUTION_MODE") or "").strip().lower() == "oneshot":
        return function(*args, **kwargs)
    loop = asyncio.get_running_loop()
    call = partial(function, *args, **kwargs)
    return await loop.run_in_executor(_get_materialization_cpu_executor(), call)


atexit.register(_shutdown_materialization_cpu_executor)


def _materialization_worker_timeout_s() -> float:
    raw = os.getenv("ADAOS_MATERIALIZATION_WORKER_TIMEOUT_S")
    try:
        value = float(str(raw or "180").strip())
    except Exception:
        value = 180.0
    return max(10.0, value)


def _materialization_worker_max_rss_bytes() -> int:
    raw = os.getenv("ADAOS_MATERIALIZATION_WORKER_MAX_RSS_MB")
    try:
        value = int(str(raw or "2048").strip())
    except Exception:
        value = 2048
    return max(256, value) * 1024 * 1024


def _materialization_worker_max_result_bytes() -> int:
    raw = os.getenv("ADAOS_MATERIALIZATION_WORKER_MAX_RESULT_MB")
    try:
        value = int(str(raw or "512").strip())
    except Exception:
        value = 512
    return max(16, value) * 1024 * 1024


async def _run_materialization_worker(
    webspace_id: str,
    *,
    mode: str,
    request_id: str | None = None,
    scenario_id: str | None = None,
    materialization_identity: Mapping[str, Any] | None = None,
    skill_decls_snapshot: Iterable[Mapping[str, Any]] | None = None,
    skill_decls_fingerprint: str | None = None,
) -> dict[str, Any]:
    request = {
        "schema": "adaos.webspace.materialization_worker_request.v1",
        "mode": str(mode or "").strip(),
        "webspace_id": str(webspace_id or "").strip(),
        "request_id": str(request_id or "").strip() or None,
        "scenario_id": str(scenario_id or "").strip() or None,
        "materialization_identity": (
            _clone_json_like(materialization_identity)
            if isinstance(materialization_identity, Mapping)
            else None
        ),
        "skill_decls_snapshot": (
            [dict(item) for item in skill_decls_snapshot if isinstance(item, Mapping)]
            if skill_decls_snapshot is not None
            else None
        ),
        "skill_decls_fingerprint": str(skill_decls_fingerprint or "").strip() or None,
    }

    started = time.perf_counter()
    peak_rss = 0
    with tempfile.TemporaryDirectory(prefix="adaos-materialize-") as temp_dir:
        root = Path(temp_dir)
        request_path = root / "request.json"
        result_path = root / "result.json"
        stdout_path = root / "stdout.log"
        stderr_path = root / "stderr.log"
        request_path.write_text(
            json.dumps(request, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        cmd = [
            sys.executable,
            "-m",
            "adaos.services.scenario.materialization_worker",
            str(request_path),
            str(result_path),
        ]
        env = os.environ.copy()
        env["ADAOS_MATERIALIZATION_WORKER"] = "0"
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0

        with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_file:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=creationflags,
            )
            try:
                import psutil

                process = psutil.Process(proc.pid)
            except Exception:
                process = None

            def _process_tree() -> list[Any]:
                if process is None:
                    return []
                try:
                    return [process, *process.children(recursive=True)]
                except Exception:
                    return [process]

            def _process_tree_rss() -> int:
                total = 0
                for item in _process_tree():
                    try:
                        total += int(item.memory_info().rss)
                    except Exception:
                        continue
                return total

            async def _stop_process_tree() -> None:
                descendants = _process_tree()[1:]
                for child in reversed(descendants):
                    try:
                        child.terminate()
                    except Exception:
                        continue
                if proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        await proc.wait()
                deadline = time.monotonic() + 5.0
                alive = list(descendants)
                while alive and time.monotonic() < deadline:
                    remaining = []
                    for child in alive:
                        try:
                            if child.is_running():
                                remaining.append(child)
                        except Exception:
                            continue
                    alive = remaining
                    if alive:
                        await asyncio.sleep(0.05)
                for child in alive:
                    try:
                        child.kill()
                    except Exception:
                        continue

            timeout_s = _materialization_worker_timeout_s()
            max_rss = _materialization_worker_max_rss_bytes()
            failure: str | None = None
            wait_task = asyncio.create_task(proc.wait())
            try:
                while not wait_task.done():
                    elapsed_s = time.perf_counter() - started
                    if elapsed_s > timeout_s:
                        failure = "materialization_worker_timeout"
                        break
                    if process is not None:
                        try:
                            current_rss = _process_tree_rss()
                            peak_rss = max(peak_rss, current_rss)
                            if current_rss > max_rss:
                                failure = "materialization_worker_rss_limit"
                                break
                        except Exception:
                            process = None
                    try:
                        await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.05)
                    except asyncio.TimeoutError:
                        continue
                if failure:
                    await _stop_process_tree()
                    await wait_task
                    raise RuntimeError(
                        f"{failure}: elapsed_ms={_elapsed_ms(started)} peak_rss_bytes={peak_rss}"
                    )
                returncode = int(await wait_task)
            except BaseException:
                await _stop_process_tree()
                if not wait_task.done():
                    await asyncio.shield(wait_task)
                raise

        stderr_tail = ""
        try:
            stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except Exception:
            pass
        if not result_path.exists():
            raise RuntimeError(
                f"materialization_worker_no_result: returncode={returncode} stderr={stderr_tail}"
            )
        result_size = int(result_path.stat().st_size)
        if result_size > _materialization_worker_max_result_bytes():
            raise RuntimeError(f"materialization_worker_result_limit: bytes={result_size}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or returncode != 0 or not bool(result.get("ok")):
            detail = str(result.get("detail") if isinstance(result, dict) else "")
            raise RuntimeError(
                f"materialization_worker_failed: returncode={returncode} detail={detail} stderr={stderr_tail}"
            )
        child_final_rss = int(result.get("worker_rss_bytes") or 0)
        worker_peak_rss = max(peak_rss, child_final_rss)
        if worker_peak_rss > max_rss:
            raise RuntimeError(
                f"materialization_worker_rss_limit: peak_rss_bytes={worker_peak_rss}"
            )
        result["worker_peak_rss_bytes"] = worker_peak_rss
        result["worker_result_bytes"] = result_size
        result["worker_parent_elapsed_ms"] = _elapsed_ms(started)
        snapshot_b64 = result.pop("snapshot_update_b64", None)
        state_vector_b64 = result.pop("state_vector_b64", None)
        if isinstance(snapshot_b64, str):
            result["snapshot_update"] = base64.b64decode(snapshot_b64.encode("ascii"))
        if isinstance(state_vector_b64, str):
            result["state_vector"] = base64.b64decode(state_vector_b64.encode("ascii"))
        payload = result.get("materialized_payload")
        if isinstance(payload, Mapping):
            result["entry"] = _resolved_outputs_from_cache_payload(payload).to_registry_entry()
        return result


def _scenario_switch_mode() -> str:
    return _SCENARIO_SWITCHING.mode()


def _extract_scenario_sections_from_content(content: Mapping[str, Any] | None) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    payload = _coerce_dict(content or {})
    ui_section = _coerce_dict(_coerce_dict(payload.get("ui") or {}).get("application") or {})
    registry_section = _coerce_dict(payload.get("registry") or {})
    catalog_section = _coerce_dict(payload.get("catalog") or {})
    return ui_section, catalog_section, registry_section


def _read_legacy_materialized_scenario_sections(
    ui_map: Any,
    data_map: Any,
    registry_map: Any,
    scenario_id: str,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    scenarios_ui = _mapping_get(ui_map, "scenarios") or {}
    scenario_ui_entry = _read_node_scoped_scenario_entry(scenarios_ui, scenario_id)
    scenario_app_ui = _coerce_dict(scenario_ui_entry.get("application") or {})

    scenarios_data = _mapping_get(data_map, "scenarios") or {}
    scenario_entry = _read_node_scoped_scenario_entry(scenarios_data, scenario_id)
    base_catalog = _coerce_dict(scenario_entry.get("catalog") or {})

    scenario_registry_map = _mapping_get(registry_map, "scenarios") or {}
    registry_entry = _read_node_scoped_scenario_entry(scenario_registry_map, scenario_id)
    return scenario_app_ui, base_catalog, registry_entry


def _resolve_scenario_sections_in_doc(
    ydoc: Any,
    *,
    webspace_id: str,
    scenario_id: str,
    source_mode: str,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], str, bool]:
    ui_map = ydoc.get_map("ui")
    data_map = ydoc.get_map("data")
    registry_map = ydoc.get_map("registry")

    loader_space = _scenario_loader_space(source_mode)
    content = _load_scenario_switch_content(scenario_id, space=loader_space)
    if isinstance(content, Mapping) and content:
        ui_section, catalog_section, registry_section = _extract_scenario_sections_from_content(content)
        return ui_section, catalog_section, registry_section, f"loader:{loader_space}", False

    scenario_app_ui, base_catalog, registry_entry = _read_legacy_materialized_scenario_sections(
        ui_map,
        data_map,
        registry_map,
        scenario_id,
    )
    if scenario_app_ui or base_catalog or registry_entry:
        _log.info(
            "resolver using legacy materialized scenario payload webspace=%s scenario=%s source_mode=%s",
            webspace_id,
            scenario_id,
            source_mode,
        )
        return scenario_app_ui, base_catalog, registry_entry, "legacy_yjs", True

    _log.warning(
        "resolver found no canonical or legacy scenario payload webspace=%s scenario=%s source_mode=%s",
        webspace_id,
        scenario_id,
        source_mode,
    )
    return {}, {}, {}, "missing", True


def _scenario_supports_catalog_controls(
    scenario_id: str,
    scenario_application: Mapping[str, Any] | None,
) -> bool:
    scenario_token = str(scenario_id or "").strip()
    if scenario_token == "web_desktop":
        return True
    app = _coerce_dict(scenario_application or {})
    desktop = _coerce_dict(app.get("desktop") or {})
    page_schema = _coerce_dict(desktop.get("pageSchema") or {})
    widgets = page_schema.get("widgets") or []
    if isinstance(widgets, list):
        for raw in widgets:
            if not isinstance(raw, Mapping):
                continue
            widget_type = str(raw.get("type") or "").strip()
            if widget_type == "desktop.widgets":
                return True
            data_source = _coerce_dict(raw.get("dataSource") or {})
            if (
                widget_type == "collection.grid"
                and str(data_source.get("transform") or "").strip() == "desktop.icons"
            ):
                return True
    topbar = desktop.get("topbar") or []
    if isinstance(topbar, list):
        for raw in topbar:
            if not isinstance(raw, Mapping):
                continue
            action = _coerce_dict(raw.get("action") or {})
            modal_id = str(action.get("openModal") or action.get("modalId") or "").strip()
            if modal_id in {"apps_catalog", "widgets_catalog"}:
                return True
    return False


def _collect_materialization_missing_branches(
    *,
    has_ui_application: bool,
    has_desktop_config: bool,
    has_desktop_page_schema: bool,
    has_apps_catalog_modal: bool,
    has_widgets_catalog_modal: bool,
    has_catalog_apps: bool,
    has_catalog_widgets: bool,
    has_data_desktop: bool,
    has_installed_apps: bool,
    has_installed_widgets: bool,
) -> list[str]:
    missing: list[str] = []
    if not has_ui_application:
        missing.append("ui.application")
    if not has_desktop_config:
        missing.append("ui.application.desktop")
    if not has_desktop_page_schema:
        missing.append("ui.application.desktop.pageSchema")
    if not has_apps_catalog_modal:
        missing.append("ui.application.modals.apps_catalog")
    if not has_widgets_catalog_modal:
        missing.append("ui.application.modals.widgets_catalog")
    if not has_catalog_apps:
        missing.append("data.catalog.apps")
    if not has_catalog_widgets:
        missing.append("data.catalog.widgets")
    if not has_data_desktop:
        missing.append("data.desktop")
    if not has_installed_apps:
        missing.append("data.installed.apps")
    if not has_installed_widgets:
        missing.append("data.installed.widgets")
    return missing


def _derive_materialization_readiness_state(
    *,
    ready: bool,
    current_scenario: str | None,
    has_ui_application: bool,
    has_desktop_config: bool,
    has_desktop_page_schema: bool,
    has_apps_catalog_modal: bool,
    has_widgets_catalog_modal: bool,
    has_catalog_apps: bool,
    has_catalog_widgets: bool,
    has_data_desktop: bool,
    has_installed_apps: bool,
    has_installed_widgets: bool,
) -> str:
    if ready:
        return "ready"
    has_effective_data = has_data_desktop and has_installed_apps and has_installed_widgets
    if has_desktop_page_schema and has_catalog_apps and has_catalog_widgets and has_effective_data:
        return "interactive"
    if has_desktop_page_schema and (
        has_catalog_apps
        or has_catalog_widgets
        or has_apps_catalog_modal
        or has_widgets_catalog_modal
        or has_effective_data
    ):
        return "hydrating"
    if has_desktop_page_schema:
        return "first_paint"
    if current_scenario or has_ui_application or has_desktop_config:
        return "pending_structure"
    return "degraded"


def _collect_compatibility_cache_required_branches(current_scenario: str | None) -> list[str]:
    scenario_id = str(current_scenario or "").strip()
    if not scenario_id:
        return []
    node_id = _local_node_id()
    return [
        f"ui.scenarios.{node_id}.{scenario_id}.application",
        f"registry.scenarios.{node_id}.{scenario_id}",
        f"data.scenarios.{node_id}.{scenario_id}.catalog",
    ]


def _describe_compatibility_caches(
    *,
    current_scenario: str | None,
    has_scenario_ui_application: bool,
    has_scenario_registry_entry: bool,
    has_scenario_catalog: bool,
    effective_ready: bool,
    rebuild_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    required_branches = _collect_compatibility_cache_required_branches(current_scenario)
    present_flags = (
        has_scenario_ui_application,
        has_scenario_registry_entry,
        has_scenario_catalog,
    )
    present_branches = [path for path, present in zip(required_branches, present_flags) if present]
    missing_branches = [path for path, present in zip(required_branches, present_flags) if not present]
    resolver = (
        rebuild_state.get("resolver")
        if isinstance(rebuild_state, Mapping) and isinstance(rebuild_state.get("resolver"), Mapping)
        else {}
    )
    legacy_fallback_active = bool(resolver.get("legacy_fallback"))
    switch_writes_enabled = False
    runtime_removal_blockers: list[str] = []
    if not str(current_scenario or "").strip():
        runtime_removal_blockers.append("current_scenario_missing")
    if not effective_ready:
        runtime_removal_blockers.append("effective_materialization_not_ready")
    if legacy_fallback_active:
        runtime_removal_blockers.append("resolver_legacy_fallback_active")
    return {
        "current_scenario": str(current_scenario or "").strip() or None,
        "required_branches": required_branches,
        "present_branches": present_branches,
        "missing_branches": missing_branches,
        "present_count": len(present_branches),
        "required_count": len(required_branches),
        "present": bool(present_branches),
        "complete": bool(required_branches) and not missing_branches,
        "client_fallback_readable": bool(str(current_scenario or "").strip() and has_scenario_ui_application),
        "switch_writes_enabled": switch_writes_enabled,
        "legacy_fallback_active": legacy_fallback_active,
        "runtime_removal_ready": not runtime_removal_blockers,
        "runtime_removal_blockers": runtime_removal_blockers,
    }


def _copy_materialization_snapshot(value: Any) -> Dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return json.loads(json.dumps(dict(value)))
    except Exception:
        return dict(value)


def _build_materialization_snapshot(
    *,
    webspace_id: str,
    current_scenario: str | None,
    has_ui_application: bool,
    has_desktop_config: bool,
    has_desktop_page_schema: bool,
    has_apps_catalog_modal: bool,
    has_widgets_catalog_modal: bool,
    has_catalog_apps: bool,
    has_catalog_widgets: bool,
    has_data_desktop: bool,
    has_installed_apps: bool,
    has_installed_widgets: bool,
    has_scenario_ui_application: bool,
    has_scenario_registry_entry: bool,
    has_scenario_catalog: bool,
    has_data_webio: bool | None = None,
    has_data_routing: bool | None = None,
    has_registry_merged: bool | None = None,
    catalog_apps_count: int,
    catalog_widgets_count: int,
    installed_apps_count: int,
    installed_widgets_count: int,
    topbar_count: int,
    page_widget_count: int,
    rebuild_state: Mapping[str, Any] | None = None,
    required_branches: list[str] | tuple[str, ...] | None = None,
    snapshot_source: str,
    stale: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    missing_branches = _collect_materialization_missing_branches(
        has_ui_application=has_ui_application,
        has_desktop_config=has_desktop_config,
        has_desktop_page_schema=has_desktop_page_schema,
        has_apps_catalog_modal=has_apps_catalog_modal,
        has_widgets_catalog_modal=has_widgets_catalog_modal,
        has_catalog_apps=has_catalog_apps,
        has_catalog_widgets=has_catalog_widgets,
        has_data_desktop=has_data_desktop,
        has_installed_apps=has_installed_apps,
        has_installed_widgets=has_installed_widgets,
    )
    declared_required_branches = list(required_branches or _DEFAULT_MATERIALIZATION_REQUIRED_BRANCHES)
    branch_presence = {
        "ui.application": bool(has_ui_application),
        "ui.application.desktop": bool(has_desktop_config),
        "ui.application.desktop.pageSchema": bool(has_desktop_page_schema),
        "ui.application.modals.apps_catalog": bool(has_apps_catalog_modal),
        "ui.application.modals.widgets_catalog": bool(has_widgets_catalog_modal),
        "data.catalog": bool(has_catalog_apps and has_catalog_widgets),
        "data.catalog.apps": bool(has_catalog_apps),
        "data.catalog.widgets": bool(has_catalog_widgets),
        "data.desktop": bool(has_data_desktop),
        "data.installed": bool(has_installed_apps and has_installed_widgets),
        "data.installed.apps": bool(has_installed_apps),
        "data.installed.widgets": bool(has_installed_widgets),
        "data.webio": bool(has_data_webio) if has_data_webio is not None else bool(has_catalog_apps or has_catalog_widgets),
        "data.routing": bool(has_data_routing) if has_data_routing is not None else bool(has_desktop_page_schema),
        "registry.merged": bool(has_registry_merged) if has_registry_merged is not None else bool(has_ui_application),
    }
    missing_required_branches = [
        branch for branch in declared_required_branches if branch_presence.get(str(branch), False) is False
    ]
    ready = not missing_required_branches
    readiness_state = _derive_materialization_readiness_state(
        ready=ready,
        current_scenario=current_scenario,
        has_ui_application=has_ui_application,
        has_desktop_config=has_desktop_config,
        has_desktop_page_schema=has_desktop_page_schema,
        has_apps_catalog_modal=has_apps_catalog_modal,
        has_widgets_catalog_modal=has_widgets_catalog_modal,
        has_catalog_apps=has_catalog_apps,
        has_catalog_widgets=has_catalog_widgets,
        has_data_desktop=has_data_desktop,
        has_installed_apps=has_installed_apps,
        has_installed_widgets=has_installed_widgets,
    )
    compatibility_caches = _describe_compatibility_caches(
        current_scenario=current_scenario,
        has_scenario_ui_application=has_scenario_ui_application,
        has_scenario_registry_entry=has_scenario_registry_entry,
        has_scenario_catalog=has_scenario_catalog,
        effective_ready=ready,
        rebuild_state=rebuild_state,
    )
    snapshot = {
        "ready": ready,
        "readiness_state": readiness_state,
        "missing_branches": missing_branches,
        "required_branches": declared_required_branches,
        "missing_required_branches": missing_required_branches,
        "compatibility_caches": compatibility_caches,
        "webspace_id": str(webspace_id or "").strip() or "default",
        "current_scenario": str(current_scenario or "").strip() or None,
        "has_ui_application": bool(has_ui_application),
        "has_desktop_config": bool(has_desktop_config),
        "has_desktop_page_schema": bool(has_desktop_page_schema),
        "has_apps_catalog_modal": bool(has_apps_catalog_modal),
        "has_widgets_catalog_modal": bool(has_widgets_catalog_modal),
        "has_catalog_apps": bool(has_catalog_apps),
        "has_catalog_widgets": bool(has_catalog_widgets),
        "has_data_desktop": bool(has_data_desktop),
        "has_installed_apps": bool(has_installed_apps),
        "has_installed_widgets": bool(has_installed_widgets),
        "catalog_counts": {
            "apps": int(catalog_apps_count or 0),
            "widgets": int(catalog_widgets_count or 0),
        },
        "installed_counts": {
            "apps": int(installed_apps_count or 0),
            "widgets": int(installed_widgets_count or 0),
        },
        "topbar_count": int(topbar_count or 0),
        "page_widget_count": int(page_widget_count or 0),
        "snapshot_source": str(snapshot_source or "").strip() or "unknown",
        "observed_at": time.time(),
        "stale": bool(stale),
    }
    error_text = str(error or "").strip()
    if error_text:
        snapshot["error"] = error_text
    return snapshot


def _build_materialization_snapshot_from_resolved(
    *,
    webspace_id: str,
    resolved: WebspaceResolverOutputs,
    compatibility_presence: Mapping[str, Any] | None = None,
    rebuild_state: Mapping[str, Any] | None = None,
    required_branches: list[str] | tuple[str, ...] | None = None,
    snapshot_source: str,
    phase_name: str = "complete",
    stale: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    application = _coerce_dict(resolved.application or {})
    desktop = _coerce_dict(application.get("desktop") or {})
    modals = _coerce_dict(application.get("modals") or {})
    page_schema = _coerce_dict(desktop.get("pageSchema") or {})
    topbar = desktop.get("topbar") if isinstance(desktop.get("topbar"), list) else []
    page_widgets = page_schema.get("widgets") if isinstance(page_schema.get("widgets"), list) else []
    installed = _coerce_dict(resolved.installed or {})
    include_catalog = str(phase_name or "").strip() != "structure"
    if not required_branches:
        materialization_contract = _scenario_materialization_contract(
            resolved.scenario_id,
            source_mode=resolved.source_mode,
        )
        required_branches = _normalize_materialization_required_branches(materialization_contract)
    presence = dict(compatibility_presence or {})
    return _build_materialization_snapshot(
        webspace_id=webspace_id,
        current_scenario=resolved.scenario_id,
        has_ui_application=bool(application),
        has_desktop_config=bool(desktop),
        has_desktop_page_schema=bool(page_schema),
        has_apps_catalog_modal="apps_catalog" in modals,
        has_widgets_catalog_modal="widgets_catalog" in modals,
        has_catalog_apps=include_catalog and isinstance(resolved.catalog.get("apps"), list),
        has_catalog_widgets=include_catalog and isinstance(resolved.catalog.get("widgets"), list),
        has_data_desktop=include_catalog and isinstance(resolved.desktop, Mapping),
        has_installed_apps=include_catalog and isinstance(installed.get("apps"), list),
        has_installed_widgets=include_catalog and isinstance(installed.get("widgets"), list),
        has_scenario_ui_application=bool(presence.get("scenario_ui_application")),
        has_scenario_registry_entry=bool(presence.get("scenario_registry_entry")),
        has_scenario_catalog=bool(presence.get("scenario_catalog")),
        has_data_webio=include_catalog and isinstance(resolved.webio, Mapping),
        has_data_routing=include_catalog and isinstance(resolved.routing, Mapping),
        has_registry_merged=bool(resolved.registry),
        catalog_apps_count=len(resolved.catalog.get("apps") or []) if include_catalog else 0,
        catalog_widgets_count=len(resolved.catalog.get("widgets") or []) if include_catalog else 0,
        installed_apps_count=len(installed.get("apps") or []) if include_catalog else 0,
        installed_widgets_count=len(installed.get("widgets") or []) if include_catalog else 0,
        topbar_count=len(topbar),
        page_widget_count=len(page_widgets),
        rebuild_state=rebuild_state,
        required_branches=list(required_branches or _DEFAULT_MATERIALIZATION_REQUIRED_BRANCHES),
        snapshot_source=snapshot_source,
        stale=stale,
        error=error,
    )


def _pending_materialization_snapshot(
    webspace_id: str,
    *,
    scenario_id: str | None,
    snapshot_source: str,
    rebuild_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _build_materialization_snapshot(
        webspace_id=webspace_id,
        current_scenario=scenario_id,
        has_ui_application=False,
        has_desktop_config=False,
        has_desktop_page_schema=False,
        has_apps_catalog_modal=False,
        has_widgets_catalog_modal=False,
        has_catalog_apps=False,
        has_catalog_widgets=False,
        has_data_desktop=False,
        has_installed_apps=False,
        has_installed_widgets=False,
        has_scenario_ui_application=False,
        has_scenario_registry_entry=False,
        has_scenario_catalog=False,
        catalog_apps_count=0,
        catalog_widgets_count=0,
        installed_apps_count=0,
        installed_widgets_count=0,
        topbar_count=0,
        page_widget_count=0,
        rebuild_state=rebuild_state,
        snapshot_source=snapshot_source,
        stale=True,
    )


def _set_webspace_rebuild_status(webspace_id: str, **fields: Any) -> dict[str, Any]:
    target = str(webspace_id or "").strip()
    current = dict(_WEBSPACE_REBUILD_STATUS.get(target) or {})
    current.update(fields)
    if str(current.get("status") or "").strip() == "ready" and "invalidation_reason" not in fields:
        current.pop("invalidation_reason", None)
    if str(current.get("status") or "").strip() != "ready" and "materialized_payload" not in fields:
        current.pop("materialized_payload", None)
    current["webspace_id"] = target
    current["updated_at"] = time.time()
    _WEBSPACE_REBUILD_STATUS[target] = current
    return dict(current)


def invalidate_webspace_materialization_cache(
    webspace_id: str | None = None,
    *,
    reason: str,
    action: str | None = None,
    source_of_truth: str | None = None,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    target = str(webspace_id or "").strip() or default_webspace_id()
    current = dict(_WEBSPACE_REBUILD_STATUS.get(target) or {})
    current_materialization = (
        current.get("materialization") if isinstance(current.get("materialization"), Mapping) else {}
    )
    effective_scenario = (
        str(scenario_id or "").strip()
        or str(current.get("scenario_id") or "").strip()
        or str(current_materialization.get("current_scenario") or "").strip()
        or None
    )
    reason_token = str(reason or "").strip() or "runtime_mutation"
    materialization = _pending_materialization_snapshot(
        target,
        scenario_id=effective_scenario,
        snapshot_source=f"invalidate:{reason_token}",
        rebuild_state=current,
    )
    materialization["stale_reason"] = reason_token
    previous_source = str(current_materialization.get("snapshot_source") or "").strip()
    if previous_source:
        materialization["previous_snapshot_source"] = previous_source
    if current_materialization.get("observed_at") is not None:
        materialization["previous_observed_at"] = current_materialization.get("observed_at")
    explicit_scenario = str(scenario_id or "").strip() or None
    dropped_cache = _drop_materialized_cache_for_webspace(target, scenario_id=explicit_scenario)
    _SKILL_DECLS_CACHE.clear()
    _SKILL_SOURCE_FINGERPRINT_CACHE.clear()
    materialization["cache_dropped"] = dropped_cache
    materialization["cache_drop_scope"] = "scenario" if explicit_scenario else "webspace"
    return _set_webspace_rebuild_status(
        target,
        status="invalidated",
        pending=True,
        background=False,
        action=str(action or current.get("action") or "").strip() or "materialization_cache_invalidated",
        source_of_truth=str(source_of_truth or current.get("source_of_truth") or "").strip() or "runtime_mutation",
        scenario_id=effective_scenario,
        requested_at=time.time(),
        started_at=time.time(),
        finished_at=None,
        invalidation_reason=reason_token,
        materialization=materialization,
        error=None,
    )


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000.0, 3)


def _record_timing(timings: Dict[str, float], key: str, started_at: float) -> float:
    value = _elapsed_ms(started_at)
    timings[str(key or "").strip() or "unknown"] = value
    return value


def _copy_timing_map(value: Any) -> Dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    out: Dict[str, float] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        try:
            out[key] = round(float(raw_value), 3)
        except Exception:
            continue
    return out or None


def _compact_apply_summary_for_log(value: Any) -> Dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    keys = (
        "changed_branches",
        "unchanged_branches",
        "failed_branches",
        "diff_applied_branches",
        "patch_applied_branches",
        "replaced_branches",
        "fingerprint_unchanged_branches",
        "trusted_fingerprint_unchanged_branches",
        "trusted_previous_fingerprint_patch_branches",
        "stale_fingerprint_branches",
        "patch_fallback_branches",
    )
    out: Dict[str, Any] = {key: source.get(key) for key in keys if source.get(key) is not None}
    for key in (
        "changed_paths",
        "fingerprint_unchanged_paths",
        "trusted_fingerprint_unchanged_paths",
        "trusted_previous_fingerprint_patch_paths",
        "stale_fingerprint_paths",
        "patch_fallback_paths",
    ):
        raw = source.get(key)
        if isinstance(raw, (list, tuple)):
            out[key] = [str(item) for item in list(raw)[:12]]
    return out


def _compact_live_room_refresh_result_for_log(value: Any) -> Dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    materialized = source.get("materialized_payload") if isinstance(source.get("materialized_payload"), Mapping) else {}
    broadcast = source.get("broadcast_diagnostics") if isinstance(source.get("broadcast_diagnostics"), Mapping) else {}
    return {
        "ok": bool(source.get("ok")),
        "ready": source.get("ready"),
        "materialized_payload_applied": bool(source.get("materialized_payload_applied")),
        "materialized_payload_update_bytes": source.get("materialized_payload_update_bytes"),
        "fallback_repair": bool(source.get("fallback_repair")),
        "semantic_repair": bool(source.get("semantic_repair")),
        "closed_connections": source.get("closed_connections"),
        "closed_webrtc_peers": source.get("closed_webrtc_peers"),
        "phase_timings_ms": _copy_timing_map(source.get("phase_timings_ms")),
        "broadcast": {
            "bytes": broadcast.get("bytes"),
            "client_count": broadcast.get("client_count"),
            "client_sync_done": broadcast.get("client_sync_done"),
            "client_sync_reason": broadcast.get("client_sync_reason"),
            "timed_out": broadcast.get("timed_out"),
            "phase_timings_ms": _copy_timing_map(broadcast.get("phase_timings_ms")),
        },
        "materialized_payload": {
            "ok": materialized.get("ok"),
            "ready": materialized.get("ready"),
            "broadcast_update_bytes": materialized.get("broadcast_update_bytes"),
            "full_state_update_bytes": materialized.get("full_state_update_bytes"),
            "force_full_state_update": materialized.get("force_full_state_update"),
            "full_state_snapshot_persisted": materialized.get("full_state_snapshot_persisted"),
            "phase_timings_ms": _copy_timing_map(materialized.get("phase_timings_ms")),
            "apply_summary": _compact_apply_summary_for_log(materialized.get("apply_summary")),
        },
    }


def _sum_timing_values(timings: Mapping[str, Any] | None, *keys: str) -> float | None:
    if not isinstance(timings, Mapping):
        return None
    total = 0.0
    seen = False
    for key in keys:
        try:
            value = timings.get(key)
        except Exception:
            value = None
        if value is None:
            continue
        try:
            total += float(value)
            seen = True
        except Exception:
            continue
    if not seen:
        return None
    return round(total, 3)


def _derive_phase_timings(
    *,
    switch_timings_ms: Mapping[str, Any] | None = None,
    rebuild_timings_ms: Mapping[str, Any] | None = None,
    semantic_rebuild_timings_ms: Mapping[str, Any] | None = None,
    switch_mode: str | None = None,
) -> Dict[str, float] | None:
    phase: Dict[str, float] = {}

    switch_total = None
    if isinstance(switch_timings_ms, Mapping):
        try:
            switch_total = float(switch_timings_ms.get("total")) if switch_timings_ms.get("total") is not None else None
        except Exception:
            switch_total = None
    rebuild_total = None
    if isinstance(rebuild_timings_ms, Mapping):
        try:
            rebuild_total = float(rebuild_timings_ms.get("total")) if rebuild_timings_ms.get("total") is not None else None
        except Exception:
            rebuild_total = None

    if switch_total is not None:
        phase["time_to_accept"] = round(switch_total, 3)

    eager_selector_commit = bool(
        isinstance(switch_timings_ms, Mapping)
        and "write_switch_pointer" in switch_timings_ms
    )
    atomic_selector_commit = bool(
        isinstance(switch_timings_ms, Mapping)
        and "defer_switch_pointer" in switch_timings_ms
    )
    if eager_selector_commit:
        pointer_update = _sum_timing_values(
            switch_timings_ms,
            "describe_state_before",
            "resolve_manifest_policy",
            "validate_scenario",
            "write_switch_pointer",
        )
        if pointer_update is not None:
            phase["time_to_pointer_update"] = pointer_update

    rebuild_before_semantic = _sum_timing_values(
        rebuild_timings_ms,
        "resolve_rebuild_target",
        "reseed_pointer",
        "invalidate_loader_cache",
        "reset_runtime_state",
        "seed_from_scenario",
        "sync_listing",
        "projection_refresh",
    )
    semantic_time_to_first_structure = _sum_timing_values(
        semantic_rebuild_timings_ms,
        "collect_inputs",
        "resolve",
        "apply_structure",
    )
    semantic_time_to_interactive = _sum_timing_values(
        semantic_rebuild_timings_ms,
        "collect_inputs",
        "resolve",
        "apply_structure",
        "apply_interactive",
    )
    semantic_total = None
    if isinstance(semantic_rebuild_timings_ms, Mapping):
        try:
            raw_semantic_total = semantic_rebuild_timings_ms.get("total")
            semantic_total = round(float(raw_semantic_total), 3) if raw_semantic_total is not None else None
        except Exception:
            semantic_total = None

    baseline = 0.0
    if switch_total is not None:
        baseline += switch_total
    if rebuild_before_semantic is not None:
        baseline += rebuild_before_semantic

    if semantic_time_to_first_structure is not None:
        phase["time_to_first_structure"] = round(baseline + semantic_time_to_first_structure, 3)
    if semantic_time_to_interactive is not None:
        phase["time_to_interactive_focus"] = round(baseline + semantic_time_to_interactive, 3)
    if "time_to_first_structure" not in phase and switch_total is not None and rebuild_total is not None:
        full_ready = round(switch_total + rebuild_total, 3)
        phase["time_to_first_structure"] = full_ready
        phase["time_to_interactive_focus"] = full_ready

    if semantic_total is not None:
        phase["time_to_full_hydration"] = round(baseline + semantic_total, 3)
    elif switch_total is not None and rebuild_total is not None:
        phase["time_to_full_hydration"] = round(switch_total + rebuild_total, 3)
    elif rebuild_total is not None:
        phase["time_to_full_hydration"] = round(rebuild_total, 3)

    if atomic_selector_commit and "time_to_full_hydration" in phase:
        atomic_commit_ready = phase["time_to_full_hydration"]
        phase["time_to_pointer_update"] = atomic_commit_ready
        phase["time_to_first_structure"] = atomic_commit_ready
        phase["time_to_interactive_focus"] = atomic_commit_ready

    return phase or None


def _finalize_timing_map(timings: Dict[str, float], *, started_at: float) -> Dict[str, float]:
    finalized = dict(timings)
    finalized["total"] = _elapsed_ms(started_at)
    return finalized


def _set_webspace_rebuild_status_if_current(webspace_id: str, request_id: str | None, **fields: Any) -> dict[str, Any]:
    target = str(webspace_id or "").strip()
    request_token = str(request_id or "").strip()
    if request_token:
        current = dict(_WEBSPACE_REBUILD_STATUS.get(target) or {})
        current_request = str(current.get("request_id") or "").strip()
        if current_request and current_request != request_token:
            return current
    if request_token and "request_id" not in fields:
        fields["request_id"] = request_token
    return _set_webspace_rebuild_status(target, **fields)


class _StaleRebuildRequestError(RuntimeError):
    def __init__(self, webspace_id: str, expected_request_id: str, current_request_id: str | None) -> None:
        self.webspace_id = str(webspace_id or "").strip()
        self.expected_request_id = str(expected_request_id or "").strip()
        self.current_request_id = str(current_request_id or "").strip() or None
        super().__init__(
            f"stale rebuild request superseded for webspace={self.webspace_id}: "
            f"expected={self.expected_request_id} current={self.current_request_id or '-'}"
        )


def _raise_if_rebuild_request_superseded(webspace_id: str, request_id: str | None) -> None:
    request_token = str(request_id or "").strip()
    if not request_token:
        return
    current_request = str(describe_webspace_rebuild_state(webspace_id).get("request_id") or "").strip()
    if current_request and current_request != request_token:
        raise _StaleRebuildRequestError(webspace_id, request_token, current_request)


def describe_webspace_rebuild_state(webspace_id: str) -> dict[str, Any]:
    target = str(webspace_id or "").strip()
    current = dict(_WEBSPACE_REBUILD_STATUS.get(target) or {})
    if not current:
        return {
            "webspace_id": target,
            "status": "idle",
            "pending": False,
            "background": False,
            "updated_at": None,
        }
    return {
        "webspace_id": target,
        "status": str(current.get("status") or "idle"),
        "pending": bool(current.get("pending")),
        "background": bool(current.get("background")),
        "action": str(current.get("action") or "") or None,
        "request_id": str(current.get("request_id") or "") or None,
        "request_source": str(current.get("request_source") or "") or None,
        "request_client": str(current.get("request_client") or "") or None,
        "source_of_truth": str(current.get("source_of_truth") or "") or None,
        "scenario_id": str(current.get("scenario_id") or "") or None,
        "scenario_resolution": str(current.get("scenario_resolution") or "") or None,
        "switch_mode": str(current.get("switch_mode") or "") or None,
        "invalidation_reason": str(current.get("invalidation_reason") or "") or None,
        "requested_at": current.get("requested_at"),
        "started_at": current.get("started_at"),
        "finished_at": current.get("finished_at"),
        "updated_at": current.get("updated_at"),
        "projection_refresh": dict(current.get("projection_refresh") or {})
        if isinstance(current.get("projection_refresh"), Mapping)
        else None,
        "registry_summary": dict(current.get("registry_summary") or {})
        if isinstance(current.get("registry_summary"), Mapping)
        else None,
        "resolver": dict(current.get("resolver") or {})
        if isinstance(current.get("resolver"), Mapping)
        else None,
        "apply_summary": dict(current.get("apply_summary") or {})
        if isinstance(current.get("apply_summary"), Mapping)
        else None,
        "timings_ms": _copy_timing_map(current.get("timings_ms")),
        "switch_timings_ms": _copy_timing_map(current.get("switch_timings_ms")),
        "semantic_rebuild_timings_ms": _copy_timing_map(current.get("semantic_rebuild_timings_ms")),
        "ydoc_timings_ms": _copy_timing_map(current.get("ydoc_timings_ms")),
        "phase_timings_ms": _copy_timing_map(current.get("phase_timings_ms")),
        "materialization": _copy_materialization_snapshot(current.get("materialization")),
        "live_room_update_requested": bool(current.get("live_room_update_requested"))
        if current.get("live_room_update_requested") is not None
        else None,
        "live_room_publish": bool(current.get("live_room_publish"))
        if current.get("live_room_publish") is not None
        else None,
        "live_room_refresh": dict(current.get("live_room_refresh") or {})
        if isinstance(current.get("live_room_refresh"), Mapping)
        else current.get("live_room_refresh"),
        "recovery_fingerprint": str(current.get("recovery_fingerprint") or "") or None,
        "recovery_duplicate_total": int(current.get("recovery_duplicate_total") or 0),
        "recovery_last_duplicate_at": current.get("recovery_last_duplicate_at"),
        "recovery_last_duplicate_reason": str(current.get("recovery_last_duplicate_reason") or "") or None,
        "recovery_last_duplicate_age_s": current.get("recovery_last_duplicate_age_s"),
        "recovery_last_command_client": str(current.get("recovery_last_command_client") or "") or None,
        "recovery_last_command_id": str(current.get("recovery_last_command_id") or "") or None,
        "recovery_last_command_seq": int(current.get("recovery_last_command_seq") or 0),
        "error": str(current.get("error") or "") or None,
    }


def get_webspace_rebuild_materialized_payload(webspace_id: str) -> dict[str, Any] | None:
    target = str(webspace_id or "").strip()
    current = _WEBSPACE_REBUILD_STATUS.get(target)
    if not isinstance(current, Mapping):
        return None
    if str(current.get("status") or "").strip() != "ready" or bool(current.get("pending")):
        return None
    payload = current.get("materialized_payload")
    if not isinstance(payload, Mapping) or not payload:
        return None
    try:
        return json.loads(json.dumps(dict(payload)))
    except Exception:
        return dict(payload)


class WebspaceScenarioRuntime:
    """
    Core runtime responsible for computing and applying the effective UI
    (application + catalog + registry + installed) for a given webspace.

    It reads:
      - ui.current_scenario,
      - scenario content from loader-backed canonical sources,
      - legacy Yjs scenario materialization as fallback only,
      - skill webui.json declarations (apps/widgets/registry/contributions),
      - persistent webspace desktop overlay,
    and writes:
      - ui.application,
      - data.catalog,
      - data.installed,
      - data.desktop,
      - data.webio,
      - registry.merged.
    """

    def __init__(self, ctx: Optional[AgentContext] = None) -> None:
        self.ctx: AgentContext = ctx or get_ctx()
        # Cached snapshot of desktop scenarios discovered on disk.
        self._desktop_scenarios: Optional[List[Tuple[str, str]]] = None
        self._last_rebuild_timings_ms: Dict[str, float] | None = None
        self._last_rebuild_ydoc_timings_ms: Dict[str, float] | None = None
        self._last_resolver_debug: Dict[str, Any] | None = None
        self._last_collect_inputs_timings_ms: Dict[str, float] | None = None
        self._last_apply_summary: Dict[str, Any] | None = None
        self._last_apply_phase_timings_ms: Dict[str, float] | None = None
        self._last_materialized_payload: Dict[str, Any] | None = None
        self._last_rebuild_snapshot_update: bytes | None = None
        self._last_rebuild_state_vector: bytes | None = None
        self._last_worker_diagnostics: Dict[str, Any] | None = None

    # --- scenario helpers -------------------------------------------------

    def _list_desktop_scenarios(self, space: str) -> List[Tuple[str, str]]:
        """
        Discover scenarios with ``type: desktop`` under the workspace
        scenarios directory. Returns a list of ``(scenario_id, title)``
        tuples. The ``web_desktop`` scenario itself is excluded so that it
        does not create a recursive launcher icon.

        ``space`` controls which manifest metadata is preferred:
          - ``workspace`` ¢?" use workspace manifests only,
          - ``dev``       ¢?" prefer dev manifests, fallback to workspace.
        """
        entries: List[Tuple[str, str]] = []
        try:
            root = self.ctx.paths.scenarios_dir()
            now = time.monotonic()
            cache_key = f"{space}:{root}"
            cached = _DESKTOP_SCENARIOS_CACHE.get(cache_key)
            if cached is not None and now - float(cached[0]) <= _DESKTOP_SCENARIOS_CACHE_TTL_S:
                return list(cached[2])
            children = [child for child in root.iterdir() if child.is_dir()]
            stamp = tuple(
                sorted(
                    (
                        str(child),
                        int((child / "scenario.yaml").stat().st_mtime_ns if (child / "scenario.yaml").exists() else 0),
                        int((child / "scenario.json").stat().st_mtime_ns if (child / "scenario.json").exists() else 0),
                    )
                    for child in children
                )
            )
            if cached is not None and cached[1] == stamp:
                _DESKTOP_SCENARIOS_CACHE[cache_key] = (now, stamp, list(cached[2]))
                return list(cached[2])
            for child in children:
                scenario_id = child.name
                if scenario_id == "web_desktop":
                    continue
                if space == "dev":
                    manifest = scenarios_loader.read_manifest(scenario_id, space="dev")
                    if not isinstance(manifest, dict) or not manifest:
                        manifest = scenarios_loader.read_manifest(scenario_id, space="workspace")
                else:
                    manifest = scenarios_loader.read_manifest(scenario_id, space="workspace")
                if not isinstance(manifest, dict) or not manifest:
                    continue
                if manifest.get("type") != "desktop":
                    continue
                title = str(manifest.get("title") or manifest.get("name") or scenario_id)
                entries.append((scenario_id, title))
            _DESKTOP_SCENARIOS_CACHE[cache_key] = (now, stamp, list(entries))
        except Exception:
            _log.debug("failed to list desktop scenarios", exc_info=True)
        return entries

    # --- helpers ---------------------------------------------------------

    def _load_webui(self, skill_name: str, space: str, *, log_missing: bool = False) -> Dict[str, Any]:
        paths = self.ctx.paths
        base = paths.dev_skills_dir() if space == "dev" else paths.skills_dir()
        skill_dir = Path(base) / skill_name
        path = skill_dir / "webui.json"
        manifest_path = skill_dir / "skill.yaml"
        if not path.exists():
            try:
                repo_root_attr = getattr(paths, "repo_root", None)
                repo_root = repo_root_attr() if callable(repo_root_attr) else repo_root_attr
                if repo_root:
                    fallback_dir = (
                        Path(repo_root).expanduser().resolve() / ".adaos" / "workspace" / "skills" / skill_name
                    )
                    fallback = fallback_dir / "webui.json"
                    if fallback.exists():
                        path = fallback
                        manifest_path = fallback_dir / "skill.yaml"
            except Exception:
                pass
        if not path.exists():
            _WEBUI_DECL_CACHE.pop(str(path), None)
            if log_missing and _log.isEnabledFor(logging.DEBUG):
                stack = " <- ".join(
                    f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                    for frame in traceback.extract_stack(limit=8)[:-1]
                )
                _log.debug("webui.json missing for %s (%s) caller=%s", skill_name, space, stack)
            return {}
        cache_key = str(path.resolve())
        try:
            stat = path.stat()
            stamp_parts: list[Any] = [cache_key, int(stat.st_mtime_ns), int(stat.st_size)]
            if manifest_path.exists():
                manifest_stat = manifest_path.stat()
                stamp_parts.extend(
                    [
                        str(manifest_path.resolve()),
                        int(manifest_stat.st_mtime_ns),
                        int(manifest_stat.st_size),
                    ]
                )
            stamp = tuple(stamp_parts)
        except Exception:
            stamp = None
        if stamp is not None:
            cached = _WEBUI_DECL_CACHE.get(cache_key)
            if cached is not None and cached[0] == stamp:
                return cached[1]
        try:
            # Accept UTF-8 with BOM produced by some Windows/PowerShell editors.
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            _log.warning("failed to read webui.json for %s: %s", skill_name, exc)
            if stamp is not None:
                _WEBUI_DECL_CACHE[cache_key] = (stamp, {})
            return {}
        if not isinstance(raw, dict):
            _log.warning("webui.json must be an object for %s", skill_name)
            if stamp is not None:
                _WEBUI_DECL_CACHE[cache_key] = (stamp, {})
            return {}

        catalog = raw.get("catalog") or {}
        apps = raw.get("apps") or catalog.get("apps") or []
        widgets = raw.get("widgets") or catalog.get("widgets") or []
        resources = raw.get("resources") or catalog.get("resources") or {}
        ui_interface = raw.get("interface") or raw.get("uiInterface") or {}
        registry = raw.get("registry") or {}
        reg_modals_raw = registry.get("modals") or {}
        reg_widgets_raw = registry.get("widgets") or {}
        ydoc_defaults = raw.get("ydoc_defaults") or {}
        raw_contrib = raw.get("contributions") or []
        contributions = [c for c in raw_contrib if isinstance(c, dict)]
        webio_raw = raw.get("webio") or {}
        webio_receivers_raw = webio_raw.get("receivers") if isinstance(webio_raw, dict) else {}
        ui_owner = "shared" if skill_name == "web_desktop_skill" else "node"
        try:
            if manifest_path.exists():
                manifest_raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
                if isinstance(manifest_raw, dict):
                    owner_token = str(manifest_raw.get("webui_owner") or manifest_raw.get("ui_owner") or "").strip().lower()
                    if owner_token in {"shared", "node"}:
                        ui_owner = owner_token
        except Exception:
            _log.debug("failed to read skill manifest ownership for %s", skill_name, exc_info=True)

        payload = {
            "skill": skill_name,
            "space": space,
            "source_path": str(path.parent.resolve()),
            "node_id": _local_node_id(),
            "ui_owner": ui_owner,
            "apps": [_apply_webui_load_hint(it) for it in apps if isinstance(it, dict)],
            "widgets": [_apply_webui_load_hint(it) for it in widgets if isinstance(it, dict)],
            "resources": _coerce_dict(resources),
            "interface": _coerce_dict(ui_interface),
            "registry": {
                "modals": (
                    {str(k): _normalize_webui_modal_def(v) for k, v in reg_modals_raw.items()}
                    if isinstance(reg_modals_raw, dict)
                    else [str(x) for x in reg_modals_raw if isinstance(x, (str, int))]
                ),
                "widgets": (
                    {str(k): _apply_webui_load_hint(v) for k, v in reg_widgets_raw.items()}
                    if isinstance(reg_widgets_raw, dict)
                    else [str(x) for x in reg_widgets_raw if isinstance(x, (str, int))]
                ),
            },
            "ydoc_defaults": ydoc_defaults if isinstance(ydoc_defaults, dict) else {},
            "contributions": contributions,
            "webio": {
                "receivers": (
                    {str(k): _normalize_webio_receiver(v) for k, v in webio_receivers_raw.items() if str(k).strip()}
                    if isinstance(webio_receivers_raw, dict)
                    else {}
                ),
            },
        }
        if stamp is not None:
            _WEBUI_DECL_CACHE[cache_key] = (stamp, payload)
        return payload

    def _collect_skill_decls(self, mode: str = "mixed", *, include_remote: bool = True) -> List[Dict[str, Any]]:
        cache_key = f"{str(mode or '').strip() or 'mixed'}:{1 if include_remote else 0}"
        now = time.monotonic()
        cached = _SKILL_DECLS_CACHE.get(cache_key)
        if cached is not None and now - float(cached[0]) <= _skill_decls_cache_ttl_s():
            try:
                self._last_skill_decls_fingerprint = str(cached[1] or "")
            except Exception:
                pass
            try:
                return list(cached[2])
            except Exception:
                return [dict(item) for item in cached[2] if isinstance(item, dict)]

        try:
            cap = get_local_capacity()
            skills = cap.get("skills") or []
        except Exception:
            skills = []
        if not isinstance(skills, list):
            skills = []

        decls: List[Dict[str, Any]] = []
        for rec in skills:
            if not isinstance(rec, dict) or not rec.get("active", True):
                continue
            name = rec.get("name") or rec.get("id")
            if not name:
                continue
            skill_name = str(name)

            if mode == "workspace":
                # Workspace mode: always use default webui.json regardless of
                # dev flag so that skills remain visible even when a dev
                # variant exists.
                decl = self._load_webui(skill_name, "default")
                if decl:
                    decls.append(decl)
                continue

            if mode == "dev":
                # Dev mode: include all active skills but prefer dev webui.json
                # when present, falling back to workspace webui.json.
                decl = self._load_webui(skill_name, "dev")
                if not decl:
                    decl = self._load_webui(skill_name, "default")
                if decl:
                    decls.append(decl)
                continue

            # Mixed mode: include both dev and default variants as-is.
            space = "dev" if rec.get("dev") else "default"
            decl = self._load_webui(skill_name, space)
            if decl:
                decls.append(decl)

        # Always ensure desktop skill's own webui.json is loaded so that
        # base desktop modals remain available even if not listed in capacity.
        try:
            desktop_decl = self._load_webui("web_desktop_skill", "default")
        except Exception:
            desktop_decl = {}
        if isinstance(desktop_decl, dict) and desktop_decl:
            decls.append(desktop_decl)

        if include_remote and mode != "dev":
            decls.extend(self._collect_remote_skill_decls())

        fingerprint = _fingerprint_json_like(decls)
        try:
            self._last_skill_decls_fingerprint = fingerprint
        except Exception:
            pass
        _SKILL_DECLS_CACHE[cache_key] = (now, fingerprint, _clone_json_like(decls))
        return decls

    def _collect_remote_skill_decls(self) -> List[Dict[str, Any]]:
        try:
            conf = load_config()
        except Exception:
            conf = None
        if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
            return []
        try:
            from adaos.services.registry.subnet_directory import get_directory

            nodes = get_directory().list_known_nodes()
        except Exception:
            nodes = []
        local_node_id = _local_node_id()
        detached_node_ids = _detached_member_node_ids()
        inventory_display = _member_device_inventory_display_map()
        decls: List[Dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            node_id = str(node.get("node_id") or "").strip()
            if not node_id or node_id == local_node_id:
                continue
            if node_id in detached_node_ids:
                continue
            runtime_projection = (
                node.get("runtime_projection")
                if isinstance(node.get("runtime_projection"), Mapping)
                else {}
            )
            snapshot = (
                runtime_projection.get("snapshot")
                if isinstance(runtime_projection.get("snapshot"), Mapping)
                else {}
            )
            snapshot_node_id = str(snapshot.get("node_id") or "").strip() if isinstance(snapshot, Mapping) else ""
            if snapshot_node_id and snapshot_node_id != node_id:
                continue
            catalog = (
                snapshot.get("desktop_catalog")
                if isinstance(snapshot.get("desktop_catalog"), Mapping)
                else {}
            )
            apps = catalog.get("apps") if isinstance(catalog.get("apps"), list) else []
            widgets = catalog.get("widgets") if isinstance(catalog.get("widgets"), list) else []
            registry = catalog.get("registry") if isinstance(catalog.get("registry"), Mapping) else {}
            resources = catalog.get("resources") if isinstance(catalog.get("resources"), Mapping) else {}
            raw_catalog_interface = catalog.get("interface") if isinstance(catalog.get("interface"), Mapping) else {}
            ui_interface = raw_catalog_interface if _looks_like_skill_ui_interface(raw_catalog_interface) else {}
            ui_interfaces = catalog.get("interfaces") if isinstance(catalog.get("interfaces"), Mapping) else {}
            if not ui_interfaces and raw_catalog_interface and not ui_interface:
                ui_interfaces = raw_catalog_interface
            webio = catalog.get("webio") if isinstance(catalog.get("webio"), Mapping) else {}
            ydoc_defaults = catalog.get("ydoc_defaults") if isinstance(catalog.get("ydoc_defaults"), Mapping) else {}
            if not apps and not widgets and not registry and not resources and not ui_interface and not ui_interfaces and not webio and not ydoc_defaults:
                capacity = node.get("capacity") if isinstance(node.get("capacity"), Mapping) else {}
                skills = capacity.get("skills") if isinstance(capacity.get("skills"), list) else []
                fallback_apps: list[dict[str, Any]] = []
                fallback_widgets: list[dict[str, Any]] = []
                fallback_registry: Dict[str, Any] = {"modals": {}, "widgets": {}}
                fallback_resources: Dict[str, Any] = {}
                fallback_interface: Dict[str, Any] = {}
                fallback_interfaces: Dict[str, Any] = {}
                fallback_webio: Dict[str, Any] = {"receivers": {}}
                fallback_ydoc_defaults: Dict[str, Any] = {}
                seen_skills: set[str] = set()
                for rec in skills:
                    if not isinstance(rec, Mapping):
                        continue
                    skill_name = str(rec.get("name") or rec.get("skill") or "").strip()
                    if not skill_name or skill_name in seen_skills:
                        continue
                    seen_skills.add(skill_name)
                    try:
                        local_decl = self._load_webui(skill_name, "default")
                    except Exception:
                        local_decl = None
                    if not isinstance(local_decl, Mapping) or not local_decl:
                        continue
                    local_apps = local_decl.get("apps") if isinstance(local_decl.get("apps"), list) else []
                    local_widgets = local_decl.get("widgets") if isinstance(local_decl.get("widgets"), list) else []
                    fallback_apps.extend([dict(item) for item in local_apps if isinstance(item, dict)])
                    fallback_widgets.extend([dict(item) for item in local_widgets if isinstance(item, dict)])
                    local_registry = local_decl.get("registry") if isinstance(local_decl.get("registry"), Mapping) else {}
                    for group in ("modals", "widgets"):
                        src = local_registry.get(group) if isinstance(local_registry.get(group), Mapping) else {}
                        dst = fallback_registry.setdefault(group, {})
                        if isinstance(dst, dict):
                            for key, value in src.items():
                                dst.setdefault(str(key), value)
                    local_resources = local_decl.get("resources") if isinstance(local_decl.get("resources"), Mapping) else {}
                    for key, value in local_resources.items():
                        fallback_resources.setdefault(str(key), value)
                    local_interface = local_decl.get("interface") if isinstance(local_decl.get("interface"), Mapping) else {}
                    if local_interface and not fallback_interface:
                        fallback_interface = _clone_json_like(local_interface)
                    if local_interface and skill_name:
                        fallback_interfaces.setdefault(skill_name, _clone_json_like(local_interface))
                    local_webio = local_decl.get("webio") if isinstance(local_decl.get("webio"), Mapping) else {}
                    local_receivers = local_webio.get("receivers") if isinstance(local_webio.get("receivers"), Mapping) else {}
                    receivers_dst = fallback_webio.setdefault("receivers", {})
                    if isinstance(receivers_dst, dict):
                        for key, value in local_receivers.items():
                            receivers_dst.setdefault(str(key), value)
                    local_defaults = local_decl.get("ydoc_defaults") if isinstance(local_decl.get("ydoc_defaults"), Mapping) else {}
                    for key, value in local_defaults.items():
                        fallback_ydoc_defaults.setdefault(str(key), value)
                apps = fallback_apps
                widgets = fallback_widgets
                registry = fallback_registry
                resources = fallback_resources
                ui_interface = fallback_interface
                ui_interfaces = fallback_interfaces
                webio = fallback_webio
                ydoc_defaults = fallback_ydoc_defaults
            if not apps and not widgets and not registry and not resources and not ui_interface and not ui_interfaces and not webio and not ydoc_defaults:
                continue
            display = _remote_member_node_display(node, inventory_display=inventory_display)
            modal_id_map = _node_scoped_modal_ids(registry, node_id=node_id)
            decl: Dict[str, Any] = {
                "skill": f"subnet.member.{node_id}",
                "space": "default",
                "node_id": node_id,
                "apps": [],
                "widgets": [],
                "resources": {},
                "interface": _coerce_dict(ui_interface),
                "interfaces": {},
                "registry": {"modals": {}, "widgets": {}},
                "webio": {"receivers": {}},
                "ydoc_defaults": {},
                "contributions": [],
            }
            mod_spec = registry.get("modals") if isinstance(registry.get("modals"), Mapping) else {}
            if isinstance(mod_spec, Mapping):
                for key, value in mod_spec.items():
                    token = str(key or "").strip()
                    if not token:
                        continue
                    scoped_token = modal_id_map.get(token, _node_scoped_catalog_id(node_id, token))
                    decl["registry"]["modals"][scoped_token] = _mark_modal_def(
                        _apply_node_context_to_ui(
                            value,
                            display,
                            node_id=node_id,
                            modal_id_map=modal_id_map,
                            override_node_display=True,
                        ),
                        source=f"skill:subnet.member.{node_id}",
                        skill=f"subnet.member.{node_id}",
                        dev=False,
                    )
            wid_spec = registry.get("widgets") if isinstance(registry.get("widgets"), Mapping) else {}
            if isinstance(wid_spec, Mapping):
                for key, value in wid_spec.items():
                    token = str(key or "").strip()
                    if not token:
                        continue
                    scoped_token = _node_scoped_catalog_id(node_id, token)
                    decl["registry"]["widgets"][scoped_token] = _apply_node_context_to_ui(
                        value,
                        display,
                        node_id=node_id,
                        modal_id_map=modal_id_map,
                        override_node_display=True,
                    )
            if isinstance(resources, Mapping):
                for key, value in resources.items():
                    token = str(key or "").strip()
                    if token:
                        decl["resources"][token] = _clone_json_like(value)
            if isinstance(ui_interfaces, Mapping):
                for key, value in ui_interfaces.items():
                    token = str(key or "").strip()
                    if token and isinstance(value, Mapping):
                        decl["interfaces"][token] = _clone_json_like(value)
            webio_receivers = webio.get("receivers") if isinstance(webio.get("receivers"), Mapping) else {}
            if isinstance(webio_receivers, Mapping):
                for key, value in webio_receivers.items():
                    token = str(key or "").strip()
                    if token:
                        decl["webio"]["receivers"][token] = _normalize_webio_receiver(value)
            for path, value in ydoc_defaults.items():
                token = str(path or "").strip()
                if token:
                    scoped_node_id = _node_scoped_data_path_node_id(token)
                    if scoped_node_id and scoped_node_id != node_id:
                        continue
                    decl["ydoc_defaults"][node_scope_data_path(token, node_id)] = _clone_json_like(value)
            for item in apps:
                if not isinstance(item, dict):
                    continue
                if _catalog_entry_is_foreign_relay(item, node_id=node_id):
                    continue
                scenario_id = str(item.get("scenario_id") or "").strip()
                if scenario_id and not _scenario_exists_for_switch(scenario_id, space="workspace"):
                    continue
                entry = _scope_remote_catalog_entry_id(
                    _apply_node_context_to_ui(
                        item,
                        display,
                        node_id=node_id,
                        modal_id_map=modal_id_map,
                        override_node_display=True,
                    ),
                    node_id=node_id,
                )
                decl["apps"].append(entry)
                app_id = str(entry.get("id") or "").strip()
                if app_id:
                    decl["contributions"].append(
                        {
                            "extensionPoint": "desktop.apps",
                            "type": "app",
                            "id": app_id,
                            "autoInstall": True,
                        }
                    )
            for item in widgets:
                if not isinstance(item, dict):
                    continue
                if _catalog_entry_is_foreign_relay(item, node_id=node_id):
                    continue
                entry = _scope_remote_catalog_entry_id(
                    _apply_node_context_to_ui(
                        item,
                        display,
                        node_id=node_id,
                        modal_id_map=modal_id_map,
                        override_node_display=True,
                    ),
                    node_id=node_id,
                )
                decl["widgets"].append(entry)
                widget_id = str(entry.get("id") or "").strip()
                if widget_id:
                    decl["contributions"].append(
                        {
                            "extensionPoint": "desktop.widgets",
                            "type": "widget",
                            "id": widget_id,
                            "autoInstall": True,
                        }
                    )
            if (
                decl["apps"]
                or decl["widgets"]
                or decl["resources"]
                or decl["interface"]
                or decl["interfaces"]
                or decl["registry"]["modals"]
                or decl["registry"]["widgets"]
                or decl["webio"]["receivers"]
                or decl["ydoc_defaults"]
            ):
                decls.append(decl)
        return decls

    def _apply_ydoc_defaults_in_txn(self, ydoc: Y.YDoc, txn: Any, decls: List[Dict[str, Any]]) -> None:  # type: ignore[override]
        spec: Dict[str, Any] = {}
        for decl in decls:
            raw = decl.get("ydoc_defaults") or {}
            if not isinstance(raw, dict):
                continue
            skill_name = str(decl.get("skill") or "").strip()
            node_id = str(decl.get("node_id") or "").strip()
            for path, default in raw.items():
                if not isinstance(path, str):
                    continue
                if _decl_is_node_owned(decl) and node_id:
                    path = node_scope_data_path(path, node_id)
                # Preserve first writer semantics for conflicting defaults.
                spec.setdefault(path, default)

        current_top_cache: Dict[tuple[str, str], Any] = {}
        missing = object()
        for path, default in spec.items():
            segments = [s for s in path.split("/") if s]
            if len(segments) < 2:
                continue
            root_name, key = segments[0], segments[1]
            root = ydoc.get_map(root_name)
            cache_key = (root_name, key)
            if len(segments) == 2:
                current_top = current_top_cache.get(cache_key, missing)
                if current_top is missing:
                    current_top = root.get(key)
                    current_top_cache[cache_key] = current_top
                if current_top is not None:
                    continue
                try:
                    value = json.loads(json.dumps(default))
                except Exception:
                    value = default
                _set_map_value_if_changed(root, txn, key, value)
                current_top_cache[cache_key] = root.get(key)
                continue
            current_top = current_top_cache.get(cache_key, missing)
            if current_top is missing:
                current_top = root.get(key)
                current_top_cache[cache_key] = current_top
            tail = segments[2:]
            if _nested_json_path_exists(current_top, tail):
                continue
            try:
                value = json.loads(json.dumps(default))
            except Exception:
                value = default
            changed, merged = _merge_nested_json_path(current_top, tail, value)
            if changed:
                _set_map_value_if_changed(root, txn, key, merged)
                current_top_cache[cache_key] = root.get(key)

    def _collect_resolver_inputs_in_doc(
        self,
        ydoc: Y.YDoc,
        webspace_id: str,
        *,
        materialization_identity: Mapping[str, Any] | None = None,
        scenario_id_override: str | None = None,
        skill_decls_override: Iterable[Mapping[str, Any]] | None = None,
        skill_decls_fingerprint_override: str | None = None,
        scenario_content_override: Mapping[str, Any] | None = None,
    ) -> WebspaceResolverInputs:
        collect_timings: Dict[str, float] = {}
        self._last_collect_inputs_timings_ms = None
        stage_started = time.perf_counter()
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        registry_map = ydoc.get_map("registry")

        scenario_id = (
            str(scenario_id_override or "").strip()
            or str(ui_map.get("current_scenario") or "web_desktop").strip()
            or "web_desktop"
        )
        scenarios_ui = _mapping_get(ui_map, "scenarios") or {}
        scenario_ui_entry = _read_node_scoped_scenario_entry(scenarios_ui, scenario_id)
        scenario_ui_application = _coerce_dict(scenario_ui_entry.get("application") or {})
        scenario_registry_map = _mapping_get(registry_map, "scenarios") or {}
        scenario_registry_entry = _read_node_scoped_scenario_entry(scenario_registry_map, scenario_id)
        scenario_data_map = _mapping_get(data_map, "scenarios") or {}
        scenario_data_entry = _read_node_scoped_scenario_entry(scenario_data_map, scenario_id)
        scenario_catalog = _coerce_dict(scenario_data_entry.get("catalog") or {})
        _record_timing(collect_timings, "collect_inputs_read_doc", stage_started)

        mode = "mixed"
        metadata: Dict[str, Any] = {}
        overlay_snapshot: Dict[str, Any] = {}
        stage_started = time.perf_counter()
        try:
            row = workspace_index.get_workspace(webspace_id)
            if row:
                mode = row.effective_source_mode
                metadata = {
                    "title": row.title,
                    "kind": row.effective_kind,
                    "source_mode": row.effective_source_mode,
                    "home_scenario": row.effective_home_scenario,
                    "is_dev": row.is_dev,
                }
                if getattr(row, "has_ui_overlay", False):
                    overlay_snapshot = {
                        "installed": _coerce_dict(getattr(row, "installed_overlay", {}) or {}),
                        "pinnedWidgets": _normalize_overlay_widget_entries(
                            getattr(row, "pinned_widgets_overlay", []) or []
                        ),
                        "topbar": list(getattr(row, "topbar_overlay", []) or []),
                        "pageSchema": _coerce_dict(getattr(row, "page_schema_overlay", {}) or {}),
                        "iconOrder": list(getattr(row, "icon_order_overlay", []) or []),
                        "widgetOrder": list(getattr(row, "widget_order_overlay", []) or []),
                        "hiddenSections": list(getattr(row, "hidden_sections_overlay", []) or []),
                        "source": "workspace_manifest_overlay",
                    }
        except Exception:
            mode = "mixed"
            metadata = {}
        _record_timing(collect_timings, "collect_inputs_manifest", stage_started)

        stage_started = time.perf_counter()
        if isinstance(scenario_content_override, Mapping) and scenario_content_override:
            scenario_app_ui, base_catalog, registry_entry = _extract_scenario_sections_from_content(
                scenario_content_override
            )
            scenario_source = "builder_preview_override"
            legacy_fallback = False
        else:
            scenario_app_ui, base_catalog, registry_entry, scenario_source, legacy_fallback = _resolve_scenario_sections_in_doc(
                ydoc,
                webspace_id=webspace_id,
                scenario_id=scenario_id,
                source_mode=mode,
            )
        _record_timing(collect_timings, "collect_inputs_scenario_sections", stage_started)
        if metadata:
            metadata = dict(metadata)
        metadata["scenario_source"] = scenario_source
        metadata["legacy_scenario_fallback"] = legacy_fallback
        metadata["materialization"] = _scenario_materialization_contract(
            scenario_id,
            source_mode=mode,
            identity=materialization_identity,
        )

        preserve_live_state = _preserve_live_state_on_rebuild_enabled()
        stage_started = time.perf_counter()
        if preserve_live_state:
            live_application = _coerce_live_branch_subset(
                _mapping_get(ui_map, "application") or {},
                ("modals", "interfaces"),
            )
            live_catalog = _coerce_live_branch_subset(
                _mapping_get(data_map, "catalog") or {},
                ("apps", "widgets"),
            )
            live_registry = _coerce_live_branch_subset(
                _mapping_get(registry_map, "merged") or {},
                ("modals", "widgets"),
            )
            live_desktop = _coerce_live_branch_subset(
                _mapping_get(data_map, "desktop") or {},
                ("installed", "topbar", "pageSchema", "pinnedWidgets", "iconOrder", "widgetOrder", "hiddenSections"),
            )
            live_routing = _coerce_live_branch_subset(
                _mapping_get(data_map, "routing") or {},
                ("routes",),
            )
        else:
            live_application = {}
            live_catalog = {}
            live_registry = {}
            live_desktop = {}
            live_routing = {}
        _record_timing(collect_timings, "collect_inputs_live_state", stage_started)

        stage_started = time.perf_counter()
        if skill_decls_override is None:
            try:
                self._last_skill_decls_fingerprint = ""
            except Exception:
                pass
            skill_decls = self._collect_skill_decls(mode=mode)
            skill_decls_fingerprint = str(getattr(self, "_last_skill_decls_fingerprint", "") or "").strip()
        else:
            skill_decls = [dict(item) for item in skill_decls_override if isinstance(item, Mapping)]
            skill_decls_fingerprint = str(skill_decls_fingerprint_override or "").strip()
            if not skill_decls_fingerprint:
                skill_decls_fingerprint = _fingerprint_json_like(skill_decls)
            self._last_skill_decls_fingerprint = skill_decls_fingerprint
        _record_timing(collect_timings, "collect_inputs_skill_decls", stage_started)

        stage_started = time.perf_counter()
        desktop_scenarios = self._list_desktop_scenarios(space=mode)
        _record_timing(collect_timings, "collect_inputs_desktop_scenarios", stage_started)
        self._last_collect_inputs_timings_ms = collect_timings

        # Resolver work continues in the materialization CPU executor.  Never
        # let thread-affine y_py values escape the owner loop through this
        # boundary: shallow ``dict(...)`` conversion can retain nested YMap or
        # YArray objects and make their parent YDoc finalize on the worker.
        detached_live_state = _coerce_dict(
            _clone_json_like(
                {
                    "application": live_application,
                    "catalog": live_catalog,
                    "registry": live_registry,
                    "desktop": live_desktop,
                    "routing": live_routing,
                }
            )
        )
        detached_skill_decls = _clone_json_like(skill_decls)

        return WebspaceResolverInputs(
            webspace_id=webspace_id,
            scenario_id=str(scenario_id),
            source_mode=mode,
            metadata=_coerce_dict(_clone_json_like(metadata)),
            scenario_application=_coerce_dict(_clone_json_like(scenario_app_ui)),
            scenario_catalog=_coerce_dict(_clone_json_like(base_catalog)),
            scenario_registry=_coerce_dict(_clone_json_like(registry_entry)),
            overlay_snapshot=_coerce_dict(_clone_json_like(overlay_snapshot)),
            live_state=detached_live_state,
            compatibility_cache_presence={
                "scenario_ui_application": bool(scenario_ui_application),
                "scenario_registry_entry": bool(scenario_registry_entry),
                "scenario_catalog": bool(scenario_catalog),
            },
            skill_decls=[dict(item) for item in detached_skill_decls if isinstance(item, Mapping)]
            if isinstance(detached_skill_decls, list)
            else [],
            skill_decls_fingerprint=skill_decls_fingerprint,
            desktop_scenarios=desktop_scenarios,
            scenario_source=scenario_source,
            legacy_scenario_fallback=legacy_fallback,
        )

    def resolve_webspace(self, inputs: WebspaceResolverInputs) -> WebspaceResolverOutputs:
        cache_keys = _resolver_cache_keys(inputs)
        resolver_fingerprint = _resolver_input_fingerprint(inputs, cache_keys=cache_keys)
        resolver_debug = {
            "source": str(inputs.scenario_source or ""),
            "legacy_fallback": bool(inputs.legacy_scenario_fallback),
            "cache_keys": dict(cache_keys),
            "input_fingerprint": resolver_fingerprint,
            "cache_hit": False,
            "source_page": _debug_page_signature_from_application(inputs.scenario_application),
        }
        cached = _get_cached_resolved_outputs(resolver_fingerprint)
        if cached is not None:
            resolver_debug["cache_hit"] = True
            resolver_debug["resolved_page"] = _debug_page_signature_from_application(cached.application)
            self._last_resolver_debug = resolver_debug
            return cached

        # Scenario and active-skill resolution is invariant across webspaces.
        # Keep webspace customization as a small overlay so two DEV previews of
        # the same generated scenario do not repeat the expensive merge.
        shared_core_eligible = not any(
            bool(value) for value in _coerce_dict(inputs.live_state or {}).values()
        )
        if shared_core_eligible:
            core_inputs = replace(
                inputs,
                webspace_id="__shared_materialization_core__",
                metadata={},
                overlay_snapshot={},
                live_state={},
            )
            core_fingerprint = _resolver_core_fingerprint(inputs)
            core = _get_cached_resolved_outputs(core_fingerprint)
            core_cache_hit = core is not None
            if core is None:
                core = self._resolve_webspace_uncached(core_inputs)
                _remember_resolved_outputs(core_fingerprint, core)
            resolved = _apply_webspace_overlay_to_resolved(core, inputs)
            resolver_debug["core_cache_hit"] = core_cache_hit
            resolver_debug["core_fingerprint"] = core_fingerprint
        else:
            resolved = self._resolve_webspace_uncached(inputs)
            resolver_debug["core_cache_hit"] = False
            resolver_debug["core_cache_bypass"] = "live_state"

        _remember_resolved_outputs(resolver_fingerprint, resolved)
        resolver_debug["resolved_page"] = _debug_page_signature_from_application(resolved.application)
        self._last_resolver_debug = resolver_debug
        return resolved

    def _resolve_webspace_uncached(self, inputs: WebspaceResolverInputs) -> WebspaceResolverOutputs:

        scenario_id = str(inputs.scenario_id or "").strip() or "web_desktop"
        source_mode = str(inputs.source_mode or "").strip() or "mixed"
        scenario_application = _coerce_dict(inputs.scenario_application or {})
        scenario_desktop = _coerce_dict(scenario_application.get("desktop") or {})
        scenario_catalog = _coerce_dict(inputs.scenario_catalog or {})
        scenario_registry = _coerce_dict(inputs.scenario_registry or {})
        scenario_apps = [it for it in (scenario_catalog.get("apps") or []) if isinstance(it, Mapping)]
        scenario_widgets = [it for it in (scenario_catalog.get("widgets") or []) if isinstance(it, Mapping)]
        raw_scenario_resources = _coerce_dict(
            scenario_application.get("resources") or scenario_catalog.get("resources") or {}
        )
        scenario_resources: Dict[str, Any] = {}
        scenario_space = _scenario_loader_space(source_mode)
        try:
            scenario_dir = scenarios_loader.scenario_root_for_space(scenario_id, scenario_space)
        except Exception:
            scenario_dir = None
        for key, value in raw_scenario_resources.items():
            token = str(key or "").strip()
            if token:
                scenario_resources[token] = _materialize_scenario_resource_descriptor(
                    token,
                    value,
                    scenario_id=scenario_id,
                    scenario_dir=scenario_dir,
                )
        base_registry_modals = [str(x) for x in (scenario_registry.get("modals") or [])]
        base_registry_widgets = [str(x) for x in (scenario_registry.get("widgets") or [])]

        skill_decls = list(inputs.skill_decls or [])
        skill_apps: List[Dict[str, Any]] = []
        skill_widgets: List[Dict[str, Any]] = []
        skill_resources: Dict[str, Any] = {}
        skill_interfaces: Dict[str, Any] = {}
        skill_registry_modals: List[List[str]] = []
        skill_registry_widgets: List[List[str]] = []
        auto_widget_ids: set[str] = set()
        auto_app_ids: set[str] = set()
        active_remote_node_ids: set[str] = set()
        detached_remote_node_ids = _detached_member_node_ids()
        local_display = node_display_from_config(load_config())

        for decl in skill_decls:
            skill_name = decl.get("skill") or ""
            space = decl.get("space") or "default"
            node_id = str(decl.get("node_id") or "").strip()
            node_owned = _decl_is_node_owned(decl)
            if node_id and str(skill_name or "").strip().startswith("subnet.member."):
                active_remote_node_ids.add(node_id)
            decl_display = {
                "node_label": str(decl.get("node_label") or "").strip(),
                "node_compact_label": str(decl.get("node_compact_label") or "").strip(),
                "node_color": str(decl.get("node_color") or "").strip(),
                "node_index": decl.get("node_index"),
            }
            if not any(decl_display.values()):
                decl_display = local_display
            source = f"skill:{skill_name}"
            dev_flag = space == "dev"
            reg = decl.get("registry") or {}
            modal_id_map = _node_scoped_modal_ids(reg, node_id=node_id) if node_owned else {}
            for app in decl.get("apps") or []:
                if isinstance(app, dict):
                    entry = _mark_entry(app, source=source, dev=dev_flag)
                    if node_owned and node_id:
                        entry = _apply_node_context_to_ui(entry, decl_display, node_id=node_id, modal_id_map=modal_id_map)
                    skill_apps.append(_apply_node_display_to_entry(entry, decl_display, node_id=node_id))
            for widget in decl.get("widgets") or []:
                if isinstance(widget, dict):
                    entry = _mark_entry(widget, source=source, dev=dev_flag)
                    if node_owned and node_id:
                        entry = _apply_node_context_to_ui(entry, decl_display, node_id=node_id, modal_id_map=modal_id_map)
                    skill_widgets.append(_apply_node_display_to_entry(entry, decl_display, node_id=node_id))
            raw_resources = decl.get("resources") if isinstance(decl.get("resources"), Mapping) else {}
            skill_source_path = str(decl.get("source_path") or "").strip() or None
            for key, value in raw_resources.items():
                token = str(key or "").strip()
                if token and token not in skill_resources:
                    skill_resources[token] = _materialize_skill_resource_descriptor(
                        token,
                        value,
                        skill_name=skill_name,
                        skill_dir=skill_source_path,
                    )
            raw_interface = decl.get("interface") if isinstance(decl.get("interface"), Mapping) else {}
            if raw_interface and skill_name:
                interface_copy = _clone_skill_ui_interface(raw_interface, skill=str(skill_name), source=source)
                if interface_copy:
                    skill_interfaces.setdefault(str(skill_name), interface_copy)
            raw_interfaces = decl.get("interfaces") if isinstance(decl.get("interfaces"), Mapping) else {}
            for interface_skill, raw_skill_interface in raw_interfaces.items():
                interface_skill_name = str(interface_skill or "").strip()
                if not interface_skill_name or not isinstance(raw_skill_interface, Mapping):
                    continue
                interface_copy = _clone_skill_ui_interface(
                    raw_skill_interface,
                    skill=interface_skill_name,
                    source=f"skill:{interface_skill_name}",
                )
                if interface_copy:
                    skill_interfaces.setdefault(interface_skill_name, interface_copy)
            mod_spec = reg.get("modals") or {}
            if isinstance(mod_spec, dict):
                skill_registry_modals.append([modal_id_map.get(str(k), str(k)) for k in mod_spec.keys()])
            else:
                skill_registry_modals.append([str(x) for x in mod_spec])
            wid_spec = reg.get("widgets") or {}
            if isinstance(wid_spec, dict):
                skill_registry_widgets.append([
                    _node_scoped_catalog_id(node_id, str(k)) if node_owned and node_id else str(k)
                    for k in wid_spec.keys()
                ])
            else:
                skill_registry_widgets.append([str(x) for x in wid_spec])
            for contrib in decl.get("contributions") or []:
                if not isinstance(contrib, dict):
                    continue
                ep = str(contrib.get("extensionPoint") or "")
                ctype = str(contrib.get("type") or "")
                cid = str(contrib.get("id") or "")
                auto = bool(contrib.get("autoInstall"))
                if not cid or not auto:
                    continue
                if ep == "desktop.widgets" and ctype == "widget":
                    auto_widget_ids.add(cid)
                if ep == "desktop.apps" and ctype == "app":
                    auto_app_ids.add(cid)

        merged_apps = [
            _apply_node_display_to_entry(
                _mark_entry(it, source=f"scenario:{scenario_id}", dev=False),
                local_display,
                node_id=_local_node_id(),
            )
            for it in scenario_apps
        ]
        merged_widgets = [
            _apply_node_display_to_entry(
                _mark_entry(it, source=f"scenario:{scenario_id}", dev=False),
                local_display,
                node_id=_local_node_id(),
            )
            for it in scenario_widgets
        ]

        extra_apps: List[Dict[str, Any]] = []
        for sid, title in inputs.desktop_scenarios:
            if sid == scenario_id:
                continue
            app_id = f"scenario:{sid}"
            extra_apps.append(
                _apply_node_display_to_entry(
                    _mark_entry(
                        {
                            "id": app_id,
                            "title": title,
                            "icon": "apps-outline",
                            "scenario_id": sid,
                        },
                        source=f"scenario:{sid}",
                        dev=False,
                    ),
                    local_display,
                    node_id=_local_node_id(),
                )
            )
            auto_app_ids.add(app_id)

        merged_apps = _merge_by_id(merged_apps + extra_apps + skill_apps)
        merged_widgets = _merge_by_id(merged_widgets + skill_widgets)
        merged_resources = {
            **_materialized_system_resource_descriptors(),
            **scenario_resources,
            **skill_resources,
        }
        live_catalog = _coerce_dict((inputs.live_state or {}).get("catalog") or {})
        merged_apps = _preserve_live_remote_catalog_entries(
            merged_apps,
            current_items=live_catalog.get("apps"),
            active_remote_node_ids=active_remote_node_ids,
            detached_remote_node_ids=detached_remote_node_ids,
        )
        merged_widgets = _preserve_live_remote_catalog_entries(
            merged_widgets,
            current_items=live_catalog.get("widgets"),
            active_remote_node_ids=active_remote_node_ids,
            detached_remote_node_ids=detached_remote_node_ids,
        )
        supports_catalog_controls = _scenario_supports_catalog_controls(
            scenario_id,
            scenario_application,
        )
        default_modal_ids = ["scenario_switcher"]
        if supports_catalog_controls:
            default_modal_ids = ["apps_catalog", "widgets_catalog", *default_modal_ids]
        merged_registry = {
            "modals": _merge_registry_lists(
                base_registry_modals,
                skill_registry_modals + [default_modal_ids],
            ),
            "widgets": _merge_registry_lists(base_registry_widgets, skill_registry_widgets),
        }

        installed_current = _coerce_dict((inputs.overlay_snapshot or {}).get("installed") or {})
        overlay_has_pinned_widgets = "pinnedWidgets" in (inputs.overlay_snapshot or {})
        overlay_pinned_widgets = _normalize_overlay_widget_entries((inputs.overlay_snapshot or {}).get("pinnedWidgets"))
        overlay_icon_order = _dedupe_str_list((inputs.overlay_snapshot or {}).get("iconOrder"))
        overlay_widget_order = _dedupe_str_list((inputs.overlay_snapshot or {}).get("widgetOrder"))
        overlay_hidden_sections = _dedupe_str_list((inputs.overlay_snapshot or {}).get("hiddenSections"))
        scenario_pinned_widgets = _normalize_overlay_widget_entries(scenario_desktop.get("pinnedWidgets"))
        scenario_topbar = list(scenario_desktop.get("topbar") or []) if isinstance(scenario_desktop.get("topbar"), list) else []
        scenario_page_schema = _coerce_dict(scenario_desktop.get("pageSchema") or {})
        installed_with_auto = _merge_installed_with_auto(
            installed_current,
            auto_apps=auto_app_ids,
            auto_widgets=auto_widget_ids,
        )

        merged_modals_map: Dict[str, Any] = {}
        base_modals_map = _coerce_dict(scenario_application.get("modals") or {})
        for key, value in base_modals_map.items():
            merged_modals_map[str(key)] = value
        for decl in skill_decls:
            reg = decl.get("registry") or {}
            mod_spec = reg.get("modals") or {}
            if not isinstance(mod_spec, dict):
                continue
            skill_name = str(decl.get("skill") or "").strip()
            node_id = str(decl.get("node_id") or "").strip()
            node_owned = _decl_is_node_owned(decl)
            decl_display = {
                "node_label": str(decl.get("node_label") or "").strip(),
                "node_compact_label": str(decl.get("node_compact_label") or "").strip(),
                "node_color": str(decl.get("node_color") or "").strip(),
                "node_index": decl.get("node_index"),
            }
            if not any(decl_display.values()):
                decl_display = local_display
            modal_id_map = _node_scoped_modal_ids(reg, node_id=node_id) if node_owned else {}
            for key, value in mod_spec.items():
                raw_token = str(key)
                token = modal_id_map.get(raw_token, raw_token)
                if token and token not in merged_modals_map:
                    modal_def = (
                        _apply_node_context_to_ui(value, decl_display, node_id=node_id, modal_id_map=modal_id_map)
                        if node_owned and node_id
                        else value
                    )
                    merged_modals_map[token] = _mark_modal_def(
                        modal_def,
                        source=f"skill:{skill_name}" if skill_name else "skill:unknown",
                        skill=skill_name,
                        dev=str(decl.get("space") or "default").strip().lower() == "dev",
                    )

        if supports_catalog_controls and "apps_catalog" not in merged_modals_map:
            merged_modals_map["apps_catalog"] = {
                "title": "Available Apps",
                "load": dict(_DEFERRED_OFF_FOCUS_LOAD),
                "schema": {
                    "id": "apps_catalog",
                    "load": dict(_DEFERRED_OFF_FOCUS_LOAD),
                    "layout": {
                        "type": "single",
                        "areas": [{"id": "main", "role": "main"}],
                    },
                    "widgets": [
                        {
                            "id": "apps-list",
                            "type": "collection.grid",
                            "area": "main",
                            "title": "Apps",
                            "load": dict(_DEFERRED_OFF_FOCUS_LOAD),
                            "dataSource": {
                                "kind": "y",
                                "path": "data/catalog/apps",
                            },
                            "actions": [
                                {
                                    "on": "select",
                                    "type": "callHost",
                                    "target": "desktop.toggleInstall",
                                    "params": {
                                        "type": "app",
                                        "id": "$event.id",
                                    },
                                }
                            ],
                        }
                    ],
                },
            }
        if supports_catalog_controls and "widgets_catalog" not in merged_modals_map:
            merged_modals_map["widgets_catalog"] = {
                "title": "Available Widgets",
                "load": dict(_DEFERRED_OFF_FOCUS_LOAD),
                "schema": {
                    "id": "widgets_catalog",
                    "load": dict(_DEFERRED_OFF_FOCUS_LOAD),
                    "layout": {
                        "type": "single",
                        "areas": [{"id": "main", "role": "main"}],
                    },
                    "widgets": [
                        {
                            "id": "widgets-list",
                            "type": "collection.grid",
                            "area": "main",
                            "title": "Widgets",
                            "load": dict(_DEFERRED_OFF_FOCUS_LOAD),
                            "dataSource": {
                                "kind": "y",
                                "path": "data/catalog/widgets",
                            },
                            "actions": [
                                {
                                    "on": "select",
                                    "type": "callHost",
                                    "target": "desktop.toggleInstall",
                                    "params": {
                                        "type": "widget",
                                        "id": "$event.id",
                                    },
                                }
                            ],
                        }
                    ],
                },
            }

        live_application = _coerce_dict((inputs.live_state or {}).get("application") or {})
        merged_modals_map = _preserve_live_remote_modals(
            merged_modals_map,
            current_modals=live_application.get("modals"),
            active_remote_node_ids=active_remote_node_ids,
            detached_remote_node_ids=detached_remote_node_ids,
        )

        live_registry = _coerce_dict((inputs.live_state or {}).get("registry") or {})
        merged_registry["modals"] = _preserve_live_remote_registry_tokens(
            list(merged_registry.get("modals") or []),
            current_tokens=live_registry.get("modals"),
            active_remote_node_ids=active_remote_node_ids,
            detached_remote_node_ids=detached_remote_node_ids,
        )
        merged_registry["widgets"] = _preserve_live_remote_registry_tokens(
            list(merged_registry.get("widgets") or []),
            current_tokens=live_registry.get("widgets"),
            active_remote_node_ids=active_remote_node_ids,
            detached_remote_node_ids=detached_remote_node_ids,
        )

        app_with_modals: Dict[str, Any] = dict(scenario_application)
        if merged_modals_map:
            app_with_modals["modals"] = merged_modals_map
        if merged_resources:
            app_with_modals["resources"] = merged_resources
        if skill_interfaces:
            merged_interfaces = _coerce_dict(app_with_modals.get("interfaces") or {})
            for key, value in skill_interfaces.items():
                merged_interfaces.setdefault(str(key), _clone_json_like(value))
            app_with_modals["interfaces"] = merged_interfaces
        desktop_config = _coerce_dict(app_with_modals.get("desktop") or {})
        desktop_config["topbar"] = scenario_topbar
        desktop_config["pageSchema"] = scenario_page_schema
        pinned_widgets_source = overlay_pinned_widgets if overlay_has_pinned_widgets else scenario_pinned_widgets
        desktop_config["pinnedWidgets"] = _refresh_pinned_widgets_from_catalog_entries(
            pinned_widgets_source,
            merged_widgets,
        )
        desktop_config["iconOrder"] = list(overlay_icon_order)
        desktop_config["widgetOrder"] = list(overlay_widget_order)
        desktop_config["hiddenSections"] = list(overlay_hidden_sections)
        app_with_modals["desktop"] = desktop_config
        webui_contract_issues = validate_application_ui_contract(
            app_with_modals,
            source=f"webspace:{inputs.webspace_id}:ui.application",
        )
        if webui_contract_issues:
            diagnostics = _coerce_dict(app_with_modals.get("diagnostics") or {})
            diagnostics["webui_contract"] = {
                "schema": "adaos.ui.webui_contract.diagnostics.v1",
                "status": "invalid"
                if any(issue.level == "error" for issue in webui_contract_issues)
                else "warning",
                "issue_count": len(webui_contract_issues),
                "error_count": sum(1 for issue in webui_contract_issues if issue.level == "error"),
                "warning_count": sum(1 for issue in webui_contract_issues if issue.level == "warning"),
                "issues": [issue.to_dict() for issue in webui_contract_issues[:40]],
            }
            app_with_modals["diagnostics"] = diagnostics
            log_webui_contract_issues(
                webui_contract_issues,
                webspace_id=inputs.webspace_id,
                source="webspace.materialization",
            )

        desktop_next = _coerce_dict((inputs.live_state or {}).get("desktop") or {})
        desktop_installed = _coerce_dict(desktop_next.get("installed") or {})
        desktop_installed["apps"] = list(installed_with_auto.get("apps") or [])
        desktop_installed["widgets"] = list(installed_with_auto.get("widgets") or [])
        desktop_next["installed"] = desktop_installed
        desktop_next["topbar"] = list(desktop_config.get("topbar") or [])
        desktop_next["pageSchema"] = _coerce_dict(desktop_config.get("pageSchema") or {})
        desktop_next["pinnedWidgets"] = list(desktop_config.get("pinnedWidgets") or [])
        desktop_next["iconOrder"] = list(desktop_config.get("iconOrder") or [])
        desktop_next["widgetOrder"] = list(desktop_config.get("widgetOrder") or [])
        desktop_next["hiddenSections"] = list(desktop_config.get("hiddenSections") or [])

        webio_dict = _merge_webio_receivers(skill_decls)

        routing_dict = _coerce_dict((inputs.live_state or {}).get("routing") or {})
        routes = routing_dict.get("routes")
        routing_dict = {**routing_dict, "routes": _coerce_dict(routes)}

        resolved = WebspaceResolverOutputs(
            webspace_id=inputs.webspace_id,
            scenario_id=scenario_id,
            source_mode=source_mode,
            application=app_with_modals,
            catalog={
                "apps": [dict(it) for it in merged_apps],
                "widgets": [dict(it) for it in merged_widgets],
                "resources": _clone_json_like(merged_resources),
            },
            registry={
                "modals": list(merged_registry.get("modals") or []),
                "widgets": list(merged_registry.get("widgets") or []),
            },
            installed={
                "apps": list(installed_with_auto.get("apps") or []),
                "widgets": list(installed_with_auto.get("widgets") or []),
            },
            desktop=desktop_next,
            webio=webio_dict,
            routing=routing_dict,
            skill_decls=skill_decls,
        )
        return resolved

    def _apply_resolved_state_in_doc(
        self,
        ydoc: Y.YDoc,
        webspace_id: str,
        resolved: WebspaceResolverOutputs,
        *,
        inputs: WebspaceResolverInputs | None = None,
        previous_resolved: WebspaceResolverOutputs | None = None,
        resolved_branch_fingerprints_override: Mapping[str, Any] | None = None,
        previous_branch_fingerprints_override: Mapping[str, Any] | None = None,
        expected_request_id: str | None = None,
        single_transaction: bool = False,
        materialization_status_per_phase: bool = True,
        force_selector_write: bool = False,
    ) -> None:
        _raise_if_rebuild_request_superseded(webspace_id, expected_request_id)
        effective_inputs = inputs or WebspaceResolverInputs(
            webspace_id=webspace_id,
            scenario_id=str(resolved.scenario_id or ""),
            source_mode=str(resolved.source_mode or ""),
        )
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        registry_map = ydoc.get_map("registry")
        runtime_map = ydoc.get_map("runtime")
        materialization_contract = _coerce_dict(effective_inputs.metadata.get("materialization") or {})
        if not materialization_contract:
            materialization_contract = _scenario_materialization_contract(
                resolved.scenario_id,
                source_mode=resolved.source_mode,
            )
        runtime_environment = dict(runtime_environment_payload())
        runtime_environment["materialization"] = materialization_contract
        target_paths = _EFFECTIVE_BRANCH_PATHS
        changed_paths: List[str] = []
        diff_applied_paths: List[str] = []
        patch_applied_paths: List[str] = []
        patch_actual_verified_paths: List[str] = []
        patch_fingerprint_mismatch_paths: List[str] = []
        patch_fallback_paths: List[str] = []
        patch_fallback_reasons: Dict[str, str] = {}
        replaced_paths: List[str] = []
        failed_paths: List[str] = []
        fingerprint_unchanged_paths: List[str] = []
        trusted_fingerprint_unchanged_paths: List[str] = []
        trusted_previous_fingerprint_patch_paths: List[str] = []
        stale_fingerprint_paths: List[str] = []
        defaults_failed = False
        selector_changed = False
        selector_reasserted = False
        selector_apply_mode = "not_attempted"
        phase_summaries: Dict[str, Dict[str, Any]] = {}
        phase_timings_ms: Dict[str, float] = {}
        branch_timings_ms: Dict[str, Dict[str, float]] = {}
        branch_apply_modes: Dict[str, str] = {}
        compatibility_presence = dict(effective_inputs.compatibility_cache_presence or {})
        resolved_branch_fingerprints = {
            str(key): str(value)
            for key, value in (resolved_branch_fingerprints_override or {}).items()
            if str(key).strip() and str(value or "").strip()
        }
        if not all(path in resolved_branch_fingerprints for path in _EFFECTIVE_BRANCH_PATHS if path != "runtime.environment"):
            fallback_fingerprints = _resolved_output_branch_fingerprints(resolved)
            for path, fingerprint in fallback_fingerprints.items():
                resolved_branch_fingerprints.setdefault(path, fingerprint)
        resolved_branch_fingerprints["runtime.environment"] = _fingerprint_json_like(runtime_environment)
        previous_branch_values: Dict[str, Any] = {}
        previous_branch_fingerprints: Dict[str, str] = {}
        if previous_resolved is not None:
            previous_branch_values = {
                "ui.application": previous_resolved.application,
                "data.catalog": previous_resolved.catalog,
                "data.installed": previous_resolved.installed,
                "data.desktop": previous_resolved.desktop,
                "data.webio": previous_resolved.webio,
                "data.routing": previous_resolved.routing,
                "registry.merged": previous_resolved.registry,
            }
            previous_branch_fingerprints = {
                str(key): str(value)
                for key, value in (previous_branch_fingerprints_override or {}).items()
                if str(key).strip() and str(value or "").strip()
            }
            if not all(path in previous_branch_fingerprints for path in previous_branch_values):
                fallback_previous_fingerprints = _resolved_output_branch_fingerprints(previous_resolved)
                for path, fingerprint in fallback_previous_fingerprints.items():
                    previous_branch_fingerprints.setdefault(path, fingerprint)
        persisted_branch_fingerprints = _read_effective_branch_fingerprints(registry_map)
        effective_branch_fingerprints = dict(persisted_branch_fingerprints)
        pending_fingerprint_updates: Dict[str, str] = {}
        transaction_total = 0

        def _update_materialization_snapshot(phase_name: str) -> None:
            application = _coerce_dict(resolved.application or {})
            desktop = _coerce_dict(application.get("desktop") or {})
            modals = _coerce_dict(application.get("modals") or {})
            page_schema = _coerce_dict(desktop.get("pageSchema") or {})
            topbar = desktop.get("topbar") if isinstance(desktop.get("topbar"), list) else []
            page_widgets = page_schema.get("widgets") if isinstance(page_schema.get("widgets"), list) else []
            installed = _coerce_dict(resolved.installed or {})
            include_catalog = phase_name != "structure"
            snapshot = _build_materialization_snapshot(
                webspace_id=webspace_id,
                current_scenario=resolved.scenario_id,
                has_ui_application=bool(application),
                has_desktop_config=bool(desktop),
                has_desktop_page_schema=bool(page_schema),
                has_apps_catalog_modal="apps_catalog" in modals,
                has_widgets_catalog_modal="widgets_catalog" in modals,
                has_catalog_apps=include_catalog and isinstance(resolved.catalog.get("apps"), list),
                has_catalog_widgets=include_catalog and isinstance(resolved.catalog.get("widgets"), list),
                has_data_desktop=include_catalog and isinstance(resolved.desktop, Mapping),
                has_installed_apps=include_catalog and isinstance(installed.get("apps"), list),
                has_installed_widgets=include_catalog and isinstance(installed.get("widgets"), list),
                has_scenario_ui_application=bool(compatibility_presence.get("scenario_ui_application")),
                has_scenario_registry_entry=bool(compatibility_presence.get("scenario_registry_entry")),
                has_scenario_catalog=bool(compatibility_presence.get("scenario_catalog")),
                has_data_webio=include_catalog and isinstance(resolved.webio, Mapping),
                has_data_routing=include_catalog and isinstance(resolved.routing, Mapping),
                has_registry_merged=bool(resolved.registry),
                catalog_apps_count=len(resolved.catalog.get("apps") or []) if include_catalog else 0,
                catalog_widgets_count=len(resolved.catalog.get("widgets") or []) if include_catalog else 0,
                installed_apps_count=len(installed.get("apps") or []) if include_catalog else 0,
                installed_widgets_count=len(installed.get("widgets") or []) if include_catalog else 0,
                topbar_count=len(topbar),
                page_widget_count=len(page_widgets),
                rebuild_state=describe_webspace_rebuild_state(webspace_id),
                required_branches=_normalize_materialization_required_branches(materialization_contract)
                or list(_DEFAULT_MATERIALIZATION_REQUIRED_BRANCHES),
                snapshot_source=f"semantic_rebuild:{phase_name}",
                stale=False,
            )
            current_request_id = str(describe_webspace_rebuild_state(webspace_id).get("request_id") or "").strip() or None
            _set_webspace_rebuild_status_if_current(
                webspace_id,
                current_request_id,
                materialization=snapshot,
            )

        def _apply_branch(
            txn: Any,
            path: str,
            y_map: Any,
            key: str,
            value: Any,
            *,
            fingerprint_updates: Dict[str, str],
            ignore_errors: bool = False,
        ) -> None:
            branch_started = time.perf_counter()
            branch_timing = branch_timings_ms.setdefault(path, {})
            fingerprint = ""
            changed = False
            apply_mode = "unknown"
            stale_branch = False
            try:
                stage_started = time.perf_counter()
                fingerprint = str(resolved_branch_fingerprints.get(path) or "").strip()
                branch_timing["fingerprint_lookup"] = _elapsed_ms(stage_started)
                actual_branch_fingerprint: str | None = None
                if (
                    fingerprint
                    and str(effective_branch_fingerprints.get(path) or "").strip() == fingerprint
                ):
                    stage_started = time.perf_counter()
                    trusted_previous_fingerprint = str(previous_branch_fingerprints.get(path) or "").strip()
                    if (
                        trusted_previous_fingerprint == fingerprint
                        and _trust_previous_materialized_branch_fingerprints_enabled()
                    ):
                        has_value = _has_effective_branch_value(y_map, key)
                        branch_timing["presence_check"] = _elapsed_ms(stage_started)
                        if has_value:
                            fingerprint_unchanged_paths.append(path)
                            trusted_fingerprint_unchanged_paths.append(path)
                            fingerprint_updates[path] = fingerprint
                            pending_fingerprint_updates[path] = fingerprint
                            branch_apply_modes[path] = "trusted_previous_fingerprint_unchanged"
                            return
                    else:
                        try:
                            actual_branch_fingerprint = _fingerprint_json_like(y_map.get(key))
                        except Exception:
                            actual_branch_fingerprint = ""
                        branch_timing["actual_fingerprint"] = _elapsed_ms(stage_started)
                        if actual_branch_fingerprint == fingerprint:
                            fingerprint_unchanged_paths.append(path)
                            fingerprint_updates[path] = fingerprint
                            pending_fingerprint_updates[path] = fingerprint
                            branch_apply_modes[path] = "fingerprint_unchanged"
                            return
                    if path not in stale_fingerprint_paths:
                        stale_fingerprint_paths.append(path)
                    stale_branch = True

                # Continue into previous-payload patching when the stored
                # fingerprint was trusted but the branch is missing, or when
                # the verified live branch did not match the stored token.
                stage_started = time.perf_counter()
                previous_fingerprint = str(previous_branch_fingerprints.get(path) or "").strip()
                previous_fingerprint_matches = False
                if previous_fingerprint and path in previous_branch_values and path not in _WHOLE_BRANCH_REPLACE_PATHS:
                    verify_started = time.perf_counter()
                    trusted_previous_state = (
                        _trust_previous_materialized_branch_fingerprints_enabled()
                        and str(effective_branch_fingerprints.get(path) or "").strip() == previous_fingerprint
                    )
                    if trusted_previous_state:
                        previous_fingerprint_matches = True
                        trusted_previous_fingerprint_patch_paths.append(path)
                        branch_timing["previous_fingerprint_trusted"] = _elapsed_ms(verify_started)
                    elif actual_branch_fingerprint is None:
                        try:
                            actual_branch_fingerprint = _fingerprint_json_like(y_map.get(key))
                        except Exception:
                            actual_branch_fingerprint = ""
                        branch_timing["previous_actual_fingerprint"] = _elapsed_ms(verify_started)
                    else:
                        branch_timing["previous_actual_fingerprint_reused"] = _elapsed_ms(verify_started)
                    if trusted_previous_state or actual_branch_fingerprint == previous_fingerprint:
                        previous_fingerprint_matches = True
                        patch_actual_verified_paths.append(path)
                    else:
                        patch_fingerprint_mismatch_paths.append(path)
                branch_timing["previous_check"] = _elapsed_ms(stage_started)

                stage_started = time.perf_counter()
                try:
                    if stale_branch:
                        changed, apply_mode = _replace_map_value(y_map, txn, key, value)
                    elif (
                        path in previous_branch_values
                        and previous_fingerprint
                        and previous_fingerprint_matches
                        and path not in _WHOLE_BRANCH_REPLACE_PATHS
                    ):
                        try:
                            current_for_patch = y_map.get(key)
                        except Exception:
                            current_for_patch = None
                        if not _is_y_map_value(current_for_patch):
                            patch_fallback_paths.append(path)
                            patch_fallback_reasons[path] = f"current_not_y_map:{type(current_for_patch).__name__}"
                            changed, apply_mode = _set_map_value_if_changed(y_map, txn, key, value)
                        elif _mapping_items(value) is None or _mapping_items(previous_branch_values[path]) is None:
                            patch_fallback_paths.append(path)
                            patch_fallback_reasons[path] = "non_mapping_payload"
                            changed, apply_mode = _set_map_value_if_changed(y_map, txn, key, value)
                        else:
                            changed, apply_mode = _patch_map_value_from_previous(
                                y_map,
                                txn,
                                key,
                                value,
                                previous_branch_values[path],
                            )
                    elif path in _WHOLE_BRANCH_REPLACE_PATHS:
                        changed, apply_mode = _replace_map_value(y_map, txn, key, value)
                    else:
                        changed, apply_mode = _set_map_value_if_changed(y_map, txn, key, value)
                finally:
                    branch_timing["apply"] = _elapsed_ms(stage_started)
            except Exception:
                branch_apply_modes[path] = "failed"
                if not ignore_errors:
                    raise
                failed_paths.append(path)
                return
            finally:
                branch_timing["total"] = _elapsed_ms(branch_started)
            if fingerprint:
                effective_branch_fingerprints[path] = fingerprint
                fingerprint_updates[path] = fingerprint
                pending_fingerprint_updates[path] = fingerprint
            branch_apply_modes[path] = f"{'changed' if changed else 'unchanged'}:{apply_mode}"
            if changed:
                changed_paths.append(path)
                if apply_mode == "diff":
                    diff_applied_paths.append(path)
                elif apply_mode == "patch":
                    patch_applied_paths.append(path)
                else:
                    replaced_paths.append(path)

        def _apply_phase(
            name: str,
            branch_specs: tuple[tuple[str, Any, str, Any, bool], ...],
            *,
            apply_defaults: bool = False,
            flush_fingerprints: bool = False,
            shared_txn: Any | None = None,
        ) -> None:
            nonlocal defaults_failed
            nonlocal transaction_total
            nonlocal selector_changed
            nonlocal selector_reasserted
            nonlocal selector_apply_mode
            _raise_if_rebuild_request_superseded(webspace_id, expected_request_id)
            phase_started = time.perf_counter()
            phase_changed_before = len(changed_paths)
            phase_diff_before = len(diff_applied_paths)
            phase_patch_before = len(patch_applied_paths)
            phase_replaced_before = len(replaced_paths)
            phase_failed_before = len(failed_paths)
            phase_fingerprint_unchanged_before = len(fingerprint_unchanged_paths)
            phase_trusted_fingerprint_unchanged_before = len(trusted_fingerprint_unchanged_paths)
            phase_stale_fingerprint_before = len(stale_fingerprint_paths)
            phase_defaults_failed = False

            def _apply_phase_body(txn: Any) -> None:
                nonlocal defaults_failed
                nonlocal selector_changed
                nonlocal selector_reasserted
                nonlocal selector_apply_mode
                phase_fingerprint_updates: Dict[str, str] = {}
                if apply_defaults:
                    try:
                        self._apply_ydoc_defaults_in_txn(ydoc, txn, resolved.skill_decls)
                    except Exception:
                        defaults_failed = True
                        phase_defaults_failed = True
                        _log.warning("failed to apply ydoc_defaults for webspace=%s", webspace_id, exc_info=True)

                if name == "structure":
                    selector_target = str(resolved.scenario_id or "").strip()
                    if selector_target:
                        if force_selector_write:
                            selector_changed = ui_map.get("current_scenario") != selector_target
                            ui_map.set(txn, "current_scenario", selector_target)
                            selector_reasserted = True
                            selector_apply_mode = "reasserted"
                        else:
                            selector_changed, selector_apply_mode = _set_map_value_if_changed(
                                ui_map,
                                txn,
                                "current_scenario",
                                selector_target,
                            )

                for path, y_map, key, value, ignore_errors in branch_specs:
                    _apply_branch(
                        txn,
                        path,
                        y_map,
                        key,
                        value,
                        fingerprint_updates=phase_fingerprint_updates,
                        ignore_errors=ignore_errors,
                    )
                if flush_fingerprints and pending_fingerprint_updates:
                    _write_effective_branch_fingerprints(
                        registry_map,
                        txn,
                        current=effective_branch_fingerprints,
                        updates=pending_fingerprint_updates,
                    )

            if shared_txn is None:
                with ydoc.begin_transaction() as txn:
                    transaction_total += 1
                    _apply_phase_body(txn)
            else:
                _apply_phase_body(shared_txn)

            phase_changed_paths = list(changed_paths[phase_changed_before:])
            phase_diff_paths = list(diff_applied_paths[phase_diff_before:])
            phase_patch_paths = list(patch_applied_paths[phase_patch_before:])
            phase_replaced_paths = list(replaced_paths[phase_replaced_before:])
            phase_failed_paths = list(failed_paths[phase_failed_before:])
            phase_fingerprint_unchanged_paths = list(fingerprint_unchanged_paths[phase_fingerprint_unchanged_before:])
            phase_trusted_fingerprint_unchanged_paths = list(
                trusted_fingerprint_unchanged_paths[phase_trusted_fingerprint_unchanged_before:]
            )
            phase_stale_fingerprint_paths = list(stale_fingerprint_paths[phase_stale_fingerprint_before:])
            phase_paths = [path for path, _y_map, _key, _value, _ignore_errors in branch_specs]
            phase_branch_timings = {
                path: dict(branch_timings_ms.get(path) or {})
                for path in phase_paths
                if branch_timings_ms.get(path)
            }
            phase_branch_modes = {
                path: str(branch_apply_modes.get(path) or "")
                for path in phase_paths
                if str(branch_apply_modes.get(path) or "")
            }
            branch_count = len(branch_specs)
            phase_summary: Dict[str, Any] = {
                "branch_count": branch_count,
                "changed_branches": len(phase_changed_paths),
                "unchanged_branches": branch_count - len(phase_changed_paths) - len(phase_failed_paths),
                "failed_branches": len(phase_failed_paths),
                "changed_paths": phase_changed_paths,
            }
            if phase_diff_paths:
                phase_summary["diff_applied_branches"] = len(phase_diff_paths)
                phase_summary["diff_applied_paths"] = phase_diff_paths
            if phase_patch_paths:
                phase_summary["patch_applied_branches"] = len(phase_patch_paths)
                phase_summary["patch_applied_paths"] = phase_patch_paths
            if phase_replaced_paths:
                phase_summary["replaced_branches"] = len(phase_replaced_paths)
                phase_summary["replaced_paths"] = phase_replaced_paths
            if phase_fingerprint_unchanged_paths:
                phase_summary["fingerprint_unchanged_branches"] = len(phase_fingerprint_unchanged_paths)
                phase_summary["fingerprint_unchanged_paths"] = phase_fingerprint_unchanged_paths
            if phase_trusted_fingerprint_unchanged_paths:
                phase_summary["trusted_fingerprint_unchanged_branches"] = len(
                    phase_trusted_fingerprint_unchanged_paths
                )
                phase_summary["trusted_fingerprint_unchanged_paths"] = phase_trusted_fingerprint_unchanged_paths
            if phase_stale_fingerprint_paths:
                phase_summary["stale_fingerprint_branches"] = len(phase_stale_fingerprint_paths)
                phase_summary["stale_fingerprint_paths"] = phase_stale_fingerprint_paths
            if phase_failed_paths:
                phase_summary["failed_paths"] = phase_failed_paths
            if phase_defaults_failed:
                phase_summary["defaults_failed"] = True
            if phase_branch_timings:
                phase_summary["branch_timings_ms"] = phase_branch_timings
            if phase_branch_modes:
                phase_summary["branch_apply_modes"] = phase_branch_modes
            phase_summaries[name] = phase_summary
            phase_timings_ms[f"apply_{name}"] = _elapsed_ms(phase_started)
            if materialization_status_per_phase:
                _update_materialization_snapshot(name)

        structure_specs = (
            ("ui.application", ui_map, "application", resolved.application, False),
            ("registry.merged", registry_map, "merged", resolved.registry, False),
            ("runtime.environment", runtime_map, "environment", runtime_environment, False),
        )
        interactive_specs = (
            ("data.catalog", data_map, "catalog", resolved.catalog, False),
            ("data.installed", data_map, "installed", resolved.installed, False),
            ("data.desktop", data_map, "desktop", resolved.desktop, True),
            ("data.webio", data_map, "webio", resolved.webio, True),
            ("data.routing", data_map, "routing", resolved.routing, True),
        )
        if single_transaction:
            combined_started = time.perf_counter()
            with ydoc.begin_transaction() as txn:
                transaction_total += 1
                _apply_phase(
                    "structure",
                    structure_specs,
                    apply_defaults=True,
                    flush_fingerprints=False,
                    shared_txn=txn,
                )
                _apply_phase(
                    "interactive",
                    interactive_specs,
                    flush_fingerprints=True,
                    shared_txn=txn,
                )
            phase_timings_ms["apply_combined_transaction"] = _elapsed_ms(combined_started)
        else:
            _apply_phase(
                "structure",
                structure_specs,
                apply_defaults=True,
                flush_fingerprints=False,
            )
            _apply_phase(
                "interactive",
                interactive_specs,
                flush_fingerprints=True,
            )
        if not materialization_status_per_phase:
            _update_materialization_snapshot("interactive")

        self._last_apply_summary = {
            "branch_count": len(target_paths),
            "changed_branches": len(changed_paths),
            "unchanged_branches": len(target_paths) - len(changed_paths) - len(failed_paths),
            "failed_branches": len(failed_paths),
            "changed_paths": list(changed_paths),
            "defaults_failed": defaults_failed,
            "transaction_total": transaction_total,
            "phases": phase_summaries,
            "branch_timings_ms": {path: dict(values) for path, values in branch_timings_ms.items()},
            "branch_apply_modes": dict(branch_apply_modes),
            "selector_changed": bool(selector_changed),
            "selector_reasserted": bool(selector_reasserted),
            "selector_apply_mode": selector_apply_mode,
        }
        if diff_applied_paths:
            self._last_apply_summary["diff_applied_branches"] = len(diff_applied_paths)
            self._last_apply_summary["diff_applied_paths"] = list(diff_applied_paths)
        if patch_applied_paths:
            self._last_apply_summary["patch_applied_branches"] = len(patch_applied_paths)
            self._last_apply_summary["patch_applied_paths"] = list(patch_applied_paths)
        if patch_actual_verified_paths:
            self._last_apply_summary["patch_actual_verified_branches"] = len(patch_actual_verified_paths)
            self._last_apply_summary["patch_actual_verified_paths"] = list(patch_actual_verified_paths)
        if patch_fingerprint_mismatch_paths:
            self._last_apply_summary["patch_fingerprint_mismatch_branches"] = len(patch_fingerprint_mismatch_paths)
            self._last_apply_summary["patch_fingerprint_mismatch_paths"] = list(patch_fingerprint_mismatch_paths)
        if patch_fallback_paths:
            self._last_apply_summary["patch_fallback_branches"] = len(patch_fallback_paths)
            self._last_apply_summary["patch_fallback_paths"] = list(patch_fallback_paths)
            self._last_apply_summary["patch_fallback_reasons"] = dict(patch_fallback_reasons)
        if replaced_paths:
            self._last_apply_summary["replaced_branches"] = len(replaced_paths)
            self._last_apply_summary["replaced_paths"] = list(replaced_paths)
        if fingerprint_unchanged_paths:
            self._last_apply_summary["fingerprint_unchanged_branches"] = len(fingerprint_unchanged_paths)
            self._last_apply_summary["fingerprint_unchanged_paths"] = list(fingerprint_unchanged_paths)
        if trusted_fingerprint_unchanged_paths:
            self._last_apply_summary["trusted_fingerprint_unchanged_branches"] = len(
                trusted_fingerprint_unchanged_paths
            )
            self._last_apply_summary["trusted_fingerprint_unchanged_paths"] = list(trusted_fingerprint_unchanged_paths)
        if trusted_previous_fingerprint_patch_paths:
            self._last_apply_summary["trusted_previous_fingerprint_patch_branches"] = len(
                trusted_previous_fingerprint_patch_paths
            )
            self._last_apply_summary["trusted_previous_fingerprint_patch_paths"] = list(
                trusted_previous_fingerprint_patch_paths
            )
        if stale_fingerprint_paths:
            self._last_apply_summary["stale_fingerprint_branches"] = len(stale_fingerprint_paths)
            self._last_apply_summary["stale_fingerprint_paths"] = list(stale_fingerprint_paths)
        if failed_paths:
            self._last_apply_summary["failed_paths"] = list(failed_paths)
        self._last_apply_phase_timings_ms = phase_timings_ms or None

    def apply_materialized_payload_to_doc(
        self,
        ydoc: Y.YDoc,
        webspace_id: str,
        payload: Mapping[str, Any],
        *,
        expected_request_id: str | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
        previous_payload: Mapping[str, Any] | None = None,
    ) -> WebUIRegistryEntry:
        apply_started = time.perf_counter()
        timings: Dict[str, float] = {}
        self._last_resolver_debug = None
        self._last_apply_summary = None
        self._last_apply_phase_timings_ms = None
        self._last_materialized_payload = None

        stage_started = time.perf_counter()
        payload_branch_fingerprints = _materialized_payload_branch_fingerprints(payload)
        resolved = _resolved_outputs_from_cache_payload(payload)
        previous_resolved: WebspaceResolverOutputs | None = None
        previous_payload_branch_fingerprints: Dict[str, str] = {}
        if isinstance(previous_payload, Mapping) and previous_payload:
            try:
                previous_payload_branch_fingerprints = _materialized_payload_branch_fingerprints(previous_payload)
                previous_resolved = _resolved_outputs_from_cache_payload(previous_payload)
            except Exception:
                previous_resolved = None
                previous_payload_branch_fingerprints = {}
        inputs = _materialized_payload_inputs(
            webspace_id,
            payload,
            resolved,
            materialization_identity=materialization_identity,
        )
        _record_timing(timings, "load_materialized_payload", stage_started)

        _raise_if_rebuild_request_superseded(webspace_id, expected_request_id)
        stage_started = time.perf_counter()
        self._apply_resolved_state_in_doc(
            ydoc,
            webspace_id,
            resolved,
            inputs=inputs,
            previous_resolved=previous_resolved,
            resolved_branch_fingerprints_override=payload_branch_fingerprints,
            previous_branch_fingerprints_override=previous_payload_branch_fingerprints,
            expected_request_id=expected_request_id,
            single_transaction=True,
            materialization_status_per_phase=False,
            force_selector_write=True,
        )
        _record_timing(timings, "apply", stage_started)
        apply_phase_timings = _copy_timing_map(self._last_apply_phase_timings_ms) or {}
        timings.update(apply_phase_timings)

        stage_started = time.perf_counter()
        entry = resolved.to_registry_entry()
        _record_timing(timings, "to_registry_entry", stage_started)
        self._last_rebuild_timings_ms = _finalize_timing_map(timings, started_at=apply_started)
        self._last_resolver_debug = {
            "source": "materialized_payload",
            "cache_hit": True,
            "scenario_id": resolved.scenario_id,
        }
        if isinstance(payload, Mapping):
            last_payload = dict(payload)
            last_payload.setdefault("schema", "adaos.webspace.materialized_payload.v1")
            last_payload["metadata"] = _clone_json_like(inputs.metadata)
            last_payload["compatibility_cache_presence"] = {
                str(key): bool(value)
                for key, value in (inputs.compatibility_cache_presence or {}).items()
                if str(key).strip()
            }
            last_payload["scenario_source"] = str(inputs.scenario_source or "")
            last_payload["legacy_scenario_fallback"] = bool(inputs.legacy_scenario_fallback)
            self._last_materialized_payload = last_payload
        else:
            self._last_materialized_payload = _resolved_outputs_to_materialized_payload(resolved, inputs=inputs)
        return entry

    def _resolve_in_doc(self, ydoc: Y.YDoc, webspace_id: str) -> WebspaceResolverOutputs:
        return self.resolve_webspace(self._collect_resolver_inputs_in_doc(ydoc, webspace_id))

    def _rebuild_in_doc(
        self,
        ydoc: Y.YDoc,
        webspace_id: str,
        *,
        expected_request_id: str | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
        skill_decls_override: Iterable[Mapping[str, Any]] | None = None,
        skill_decls_fingerprint_override: str | None = None,
    ) -> WebUIRegistryEntry:
        rebuild_started = time.perf_counter()
        timings: Dict[str, float] = {}
        self._last_resolver_debug = None
        self._last_collect_inputs_timings_ms = None
        self._last_apply_summary = None
        self._last_apply_phase_timings_ms = None
        self._last_materialized_payload = None

        stage_started = time.perf_counter()
        inputs = self._collect_resolver_inputs_in_doc(
            ydoc,
            webspace_id,
            materialization_identity=materialization_identity,
            skill_decls_override=skill_decls_override,
            skill_decls_fingerprint_override=skill_decls_fingerprint_override,
        )
        _record_timing(timings, "collect_inputs", stage_started)
        collect_phase_timings = _copy_timing_map(self._last_collect_inputs_timings_ms) or {}
        timings.update(collect_phase_timings)

        stage_started = time.perf_counter()
        resolved = self.resolve_webspace(inputs)
        _record_timing(timings, "resolve", stage_started)

        _raise_if_rebuild_request_superseded(webspace_id, expected_request_id)
        stage_started = time.perf_counter()
        self._apply_resolved_state_in_doc(
            ydoc,
            webspace_id,
            resolved,
            inputs=inputs,
            expected_request_id=expected_request_id,
        )
        _record_timing(timings, "apply", stage_started)
        apply_phase_timings = _copy_timing_map(self._last_apply_phase_timings_ms) or {}
        timings.update(apply_phase_timings)

        stage_started = time.perf_counter()
        entry = resolved.to_registry_entry()
        _record_timing(timings, "to_registry_entry", stage_started)
        self._last_rebuild_timings_ms = _finalize_timing_map(timings, started_at=rebuild_started)
        self._last_materialized_payload = _resolved_outputs_to_materialized_payload(resolved, inputs=inputs)

        try:
            _log.debug(
                "rebuilt webspace=%s scenario=%s source=%s legacy_fallback=%s cache_hit=%s apply=%d/%d apps=%d widgets=%d timings_ms=%s",
                webspace_id,
                resolved.scenario_id,
                str(inputs.scenario_source or ""),
                bool(inputs.legacy_scenario_fallback),
                bool((self._last_resolver_debug or {}).get("cache_hit")),
                int((self._last_apply_summary or {}).get("changed_branches") or 0),
                int((self._last_apply_summary or {}).get("branch_count") or 0),
                len(entry.apps),
                len(entry.widgets),
                self._last_rebuild_timings_ms,
            )
        except Exception:
            pass

        return entry

    def _resolve_materialized_payload_from_inputs_sync(
        self,
        inputs: WebspaceResolverInputs,
    ) -> tuple[WebspaceResolverOutputs, Dict[str, Any], Dict[str, float]]:
        """Resolve and serialize plain data off the YDoc owner loop."""

        timings: Dict[str, float] = {}
        stage_started = time.perf_counter()
        resolved = self.resolve_webspace(inputs)
        _record_timing(timings, "resolve", stage_started)
        stage_started = time.perf_counter()
        payload = _resolved_outputs_to_materialized_payload(resolved, inputs=inputs)
        _record_timing(timings, "build_materialized_payload", stage_started)
        return resolved, payload, timings

    def _prepare_materialization_skill_decls_sync(
        self,
        webspace_id: str,
        source_mode_override: str | None = None,
    ) -> tuple[List[Dict[str, Any]], str]:
        source_mode = str(source_mode_override or "").strip() or _resolve_projection_refresh_space(webspace_id)
        declarations = self._collect_skill_decls(source_mode)
        fingerprint = str(getattr(self, "_last_skill_decls_fingerprint", "") or "").strip()
        return declarations, fingerprint

    async def resolve_materialized_payload_from_doc_async(
        self,
        ydoc: Y.YDoc,
        webspace_id: str,
        *,
        request_id: str | None = None,
        scenario_id: str | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
        skill_decls_snapshot: Iterable[Mapping[str, Any]] | None = None,
        skill_decls_fingerprint: str | None = None,
        scenario_content_override: Mapping[str, Any] | None = None,
        skill_source_mode: str | None = None,
    ) -> WebUIRegistryEntry:
        """Resolve a payload from an owner-loop YDoc without mutating it."""

        started = time.perf_counter()
        timings: Dict[str, float] = {}
        self._last_resolver_debug = None
        self._last_collect_inputs_timings_ms = None
        self._last_apply_summary = None
        self._last_apply_phase_timings_ms = None
        self._last_materialized_payload = None
        self._last_worker_diagnostics = None

        prepared_skill_decls = skill_decls_snapshot
        prepared_skill_fingerprint = str(skill_decls_fingerprint or "").strip()
        if prepared_skill_decls is None:
            stage_started = time.perf_counter()
            prepared_skill_decls, prepared_skill_fingerprint = await _run_materialization_cpu(
                self._prepare_materialization_skill_decls_sync,
                webspace_id,
                skill_source_mode,
            )
            _record_timing(timings, "prepare_skill_decls", stage_started)

        _raise_if_rebuild_request_superseded(webspace_id, request_id)
        stage_started = time.perf_counter()
        inputs = self._collect_resolver_inputs_in_doc(
            ydoc,
            webspace_id,
            materialization_identity=materialization_identity,
            scenario_id_override=scenario_id,
            skill_decls_override=prepared_skill_decls,
            skill_decls_fingerprint_override=prepared_skill_fingerprint,
            scenario_content_override=scenario_content_override,
        )
        _record_timing(timings, "collect_inputs", stage_started)
        timings.update(_copy_timing_map(self._last_collect_inputs_timings_ms) or {})

        _raise_if_rebuild_request_superseded(webspace_id, request_id)
        resolved, payload, worker_timings = await _run_materialization_cpu(
            self._resolve_materialized_payload_from_inputs_sync,
            inputs,
        )
        timings.update(worker_timings)
        self._last_materialized_payload = payload
        self._last_rebuild_timings_ms = _finalize_timing_map(timings, started_at=started)
        self._last_rebuild_ydoc_timings_ms = {
            "payload_only": 0.0,
            "total": self._last_rebuild_timings_ms["total"],
        }
        self._last_apply_summary = {
            "branch_count": len(_EFFECTIVE_BRANCH_PATHS),
            "changed_branches": 0,
            "unchanged_branches": 0,
            "failed_branches": 0,
            "payload_only": True,
        }
        return resolved.to_registry_entry()

    async def resolve_materialized_payload_async(
        self,
        webspace_id: str,
        *,
        request_id: str | None = None,
        scenario_id: str | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
        isolate_process: bool | None = None,
        skill_decls_snapshot: Iterable[Mapping[str, Any]] | None = None,
        skill_decls_fingerprint: str | None = None,
        scenario_content_override: Mapping[str, Any] | None = None,
        skill_source_mode: str | None = None,
    ) -> WebUIRegistryEntry:
        """Resolve a materialized payload without mutating an intermediate YDoc."""
        materialize_started = time.perf_counter()
        timings: Dict[str, float] = {}
        ydoc_timings: Dict[str, float] = {"payload_only": 0.0}
        self._last_resolver_debug = None
        self._last_collect_inputs_timings_ms = None
        self._last_apply_summary = None
        self._last_apply_phase_timings_ms = None
        self._last_materialized_payload = None
        self._last_rebuild_ydoc_timings_ms = None
        self._last_rebuild_snapshot_update = None
        self._last_rebuild_state_vector = None
        self._last_worker_diagnostics = None

        use_process_worker = (
            _materialization_worker_enabled()
            and isolate_process is not False
            and not scenario_content_override
            and not skill_source_mode
        )
        if use_process_worker:
            stage_started = time.perf_counter()
            worker_result = _get_cached_materialized_worker_result(
                materialization_identity,
                cache_mode="payload_only",
                require_snapshot=False,
            )
            if worker_result is not None:
                _record_timing(ydoc_timings, "materialization_cache_lookup", stage_started)
                ydoc_timings["materialization_cache_hit"] = 0.0
            else:
                prepared_skill_decls = skill_decls_snapshot
                prepared_skill_fingerprint = str(skill_decls_fingerprint or "").strip()
                if prepared_skill_decls is None:
                    prepare_started = time.perf_counter()
                    prepared_skill_decls, prepared_skill_fingerprint = await _run_materialization_cpu(
                        self._prepare_materialization_skill_decls_sync,
                        webspace_id,
                        skill_source_mode,
                    )
                    _record_timing(ydoc_timings, "prepare_skill_decls", prepare_started)
                worker_result = await _run_materialization_worker(
                    webspace_id,
                    mode="payload_only",
                    request_id=request_id,
                    scenario_id=scenario_id,
                    materialization_identity=materialization_identity,
                    skill_decls_snapshot=prepared_skill_decls,
                    skill_decls_fingerprint=prepared_skill_fingerprint,
                )
                _record_timing(ydoc_timings, "payload_worker", stage_started)
                ydoc_timings["materialization_cache_miss"] = 0.0
                _remember_materialized_worker_result(
                    materialization_identity,
                    worker_result,
                    cache_mode="payload_only",
                    require_snapshot=False,
                )
            _raise_if_rebuild_request_superseded(webspace_id, request_id)
            payload = worker_result.get("materialized_payload")
            if not isinstance(payload, Mapping):
                raise RuntimeError("materialization_worker_missing_payload")
            self._last_materialized_payload = dict(payload)
            self._last_rebuild_timings_ms = _copy_timing_map(
                worker_result.get("rebuild_timings_ms")
            )
            self._last_resolver_debug = dict(worker_result.get("resolver_debug") or {})
            self._last_apply_summary = dict(worker_result.get("apply_summary") or {})
            self._last_apply_phase_timings_ms = _copy_timing_map(
                worker_result.get("apply_phase_timings_ms")
            )
            self._last_worker_diagnostics = {
                "mode": "payload_only",
                "elapsed_ms": worker_result.get("worker_parent_elapsed_ms"),
                "child_elapsed_ms": worker_result.get("worker_elapsed_ms"),
                "init_ms": worker_result.get("worker_init_ms"),
                "materialize_ms": worker_result.get("worker_materialize_ms"),
                "peak_rss_bytes": worker_result.get("worker_peak_rss_bytes"),
                "result_bytes": worker_result.get("worker_result_bytes"),
                "materialization_cache": dict(worker_result.get("materialization_cache") or {})
                if isinstance(worker_result.get("materialization_cache"), Mapping)
                else None,
            }
            worker_ydoc_timings = _copy_timing_map(worker_result.get("ydoc_timings_ms")) or {}
            worker_ydoc_timings["worker_process"] = round(
                float(worker_result.get("worker_parent_elapsed_ms") or 0.0),
                3,
            )
            self._last_rebuild_ydoc_timings_ms = _finalize_timing_map(
                worker_ydoc_timings,
                started_at=materialize_started,
            )
            resolved = _resolved_outputs_from_cache_payload(payload)
            inputs = _materialized_payload_inputs(
                webspace_id,
                payload,
                resolved,
                materialization_identity=materialization_identity,
            )
            materialization_contract = _coerce_dict(inputs.metadata.get("materialization") or {})
            materialization_snapshot = _build_materialization_snapshot_from_resolved(
                webspace_id=webspace_id,
                resolved=resolved,
                compatibility_presence=inputs.compatibility_cache_presence,
                rebuild_state=describe_webspace_rebuild_state(webspace_id),
                required_branches=_normalize_materialization_required_branches(materialization_contract)
                or list(_DEFAULT_MATERIALIZATION_REQUIRED_BRANCHES),
                snapshot_source="semantic_rebuild:payload_worker",
                phase_name="complete",
                stale=False,
            )
            if str(request_id or "").strip():
                _set_webspace_rebuild_status_if_current(
                    webspace_id,
                    request_id,
                    materialization=materialization_snapshot,
                )
            return resolved.to_registry_entry()

        prepared_skill_decls = skill_decls_snapshot
        prepared_skill_fingerprint = str(skill_decls_fingerprint or "").strip()
        if prepared_skill_decls is None:
            stage_started = time.perf_counter()
            prepared_skill_decls, prepared_skill_fingerprint = await _run_materialization_cpu(
                self._prepare_materialization_skill_decls_sync,
                webspace_id,
                skill_source_mode,
            )
            _record_timing(timings, "prepare_skill_decls", stage_started)

        operational_doc_started = time.perf_counter()
        operational_doc_close_started = operational_doc_started
        async with _open_readonly_operational_ydoc(webspace_id) as ydoc:
            _record_timing(timings, "open_operational_doc", operational_doc_started)
            _raise_if_rebuild_request_superseded(webspace_id, request_id)
            stage_started = time.perf_counter()
            inputs = self._collect_resolver_inputs_in_doc(
                ydoc,
                webspace_id,
                materialization_identity=materialization_identity,
                scenario_id_override=scenario_id,
                skill_decls_override=prepared_skill_decls,
                skill_decls_fingerprint_override=prepared_skill_fingerprint,
                scenario_content_override=scenario_content_override,
            )
            _record_timing(timings, "collect_inputs", stage_started)
            collect_phase_timings = _copy_timing_map(self._last_collect_inputs_timings_ms) or {}
            timings.update(collect_phase_timings)

            resolved, payload, worker_timings = await _run_materialization_cpu(
                self._resolve_materialized_payload_from_inputs_sync,
                inputs,
            )
            timings.update(worker_timings)
            operational_doc_close_started = time.perf_counter()
        _record_timing(timings, "close_operational_doc", operational_doc_close_started)

        _raise_if_rebuild_request_superseded(webspace_id, request_id)
        self._last_materialized_payload = payload
        materialization_contract = _coerce_dict(inputs.metadata.get("materialization") or {})
        if not materialization_contract:
            materialization_contract = _scenario_materialization_contract(
                resolved.scenario_id,
                source_mode=resolved.source_mode,
            )
        materialization_snapshot = _build_materialization_snapshot_from_resolved(
            webspace_id=webspace_id,
            resolved=resolved,
            compatibility_presence=inputs.compatibility_cache_presence,
            rebuild_state=describe_webspace_rebuild_state(webspace_id),
            required_branches=_normalize_materialization_required_branches(materialization_contract)
            or list(_DEFAULT_MATERIALIZATION_REQUIRED_BRANCHES),
            snapshot_source="semantic_rebuild:payload_only",
            phase_name="complete",
            stale=False,
        )
        if str(request_id or "").strip():
            _set_webspace_rebuild_status_if_current(
                webspace_id,
                request_id,
                materialization=materialization_snapshot,
            )

        stage_started = time.perf_counter()
        entry = resolved.to_registry_entry()
        _record_timing(timings, "to_registry_entry", stage_started)
        self._last_rebuild_timings_ms = _finalize_timing_map(timings, started_at=materialize_started)
        self._last_apply_summary = {
            "branch_count": len(_EFFECTIVE_BRANCH_PATHS),
            "changed_branches": 0,
            "unchanged_branches": 0,
            "failed_branches": 0,
            "payload_only": True,
        }
        self._last_apply_phase_timings_ms = None
        self._last_rebuild_ydoc_timings_ms = _finalize_timing_map(ydoc_timings, started_at=materialize_started)
        return entry

    # --- public API ------------------------------------------------------

    def compute_registry_for_webspace(
        self,
        webspace_id: str,
        *,
        request_id: str | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
    ) -> WebUIRegistryEntry:
        """
        Compute and apply the effective UI model for the given webspace.

        This is a synchronous helper that loads the YDoc via get_ydoc(),
        rebuilds ui.application/data.catalog/data.installed/registry.merged
        and returns the resulting registry snapshot.
        """
        with _webspace_runtime_sync_write_meta(
            root_names=["ui", "data", "registry", "runtime"],
            source="webspace_runtime.rebuild_sync",
        ):
            with get_ydoc(webspace_id) as ydoc:
                return self._rebuild_in_doc(
                    ydoc,
                    webspace_id,
                    expected_request_id=request_id,
                    materialization_identity=materialization_identity,
                )

    def _rebuild_fresh_doc_snapshot_sync(
        self,
        webspace_id: str,
        *,
        request_id: str | None = None,
        initial_scenario_id: str | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
        skill_decls_snapshot: Iterable[Mapping[str, Any]] | None = None,
        skill_decls_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """Build a fresh materialization snapshot in the calling thread.

        ``y_py`` documents must be created and dropped in the same thread. This
        helper intentionally returns only plain Python data and encoded bytes.
        """
        rebuild_started = time.perf_counter()
        ydoc_timings: Dict[str, float] = {}
        worker_runtime = WebspaceScenarioRuntime(self.ctx)
        ydoc: Any | None = None
        try:
            ydoc = Y.YDoc()
            ydoc_timings["ystore_apply_updates"] = 0.0
            seed_scenario = str(initial_scenario_id or "").strip()
            if seed_scenario:
                stage_started = time.perf_counter()
                ui_map = ydoc.get_map("ui")
                with ydoc.begin_transaction() as txn:
                    _set_map_value_if_changed(ui_map, txn, "current_scenario", seed_scenario)
                _record_timing(ydoc_timings, "seed_initial_scenario", stage_started)
            stage_started = time.perf_counter()
            entry = worker_runtime._rebuild_in_doc(
                ydoc,
                webspace_id,
                expected_request_id=request_id,
                materialization_identity=materialization_identity,
                skill_decls_override=skill_decls_snapshot,
                skill_decls_fingerprint_override=skill_decls_fingerprint,
            )
            _record_timing(ydoc_timings, "in_doc_rebuild", stage_started)
            stage_started = time.perf_counter()
            snapshot_update = Y.encode_state_as_update(ydoc)  # type: ignore[arg-type]
            state_vector = Y.encode_state_vector(ydoc)  # type: ignore[arg-type]
            _record_timing(ydoc_timings, "encode_snapshot", stage_started)
            ydoc_timings["encode_diff"] = 0.0
            ydoc_timings["ystore_write_update"] = 0.0
            ydoc_timings["room_update"] = 0.0
            return {
                "entry": entry,
                "snapshot_update": bytes(snapshot_update or b""),
                "state_vector": bytes(state_vector or b""),
                "materialized_payload": _clone_json_like(worker_runtime._last_materialized_payload or {}),
                "rebuild_timings_ms": _copy_timing_map(worker_runtime._last_rebuild_timings_ms),
                "resolver_debug": dict(worker_runtime._last_resolver_debug or {}),
                "apply_summary": dict(worker_runtime._last_apply_summary or {}),
                "apply_phase_timings_ms": _copy_timing_map(worker_runtime._last_apply_phase_timings_ms),
                "ydoc_timings_ms": _finalize_timing_map(ydoc_timings, started_at=rebuild_started),
            }
        except Exception as exc:
            ydoc = None
            raise RuntimeError(f"fresh_doc_materialization_failed: {type(exc).__name__}: {exc}") from None
        finally:
            ydoc = None

    async def rebuild_webspace_async(
        self,
        webspace_id: str,
        *,
        request_id: str | None = None,
        publish_live_room: bool = True,
        prefer_live_room: bool | None = None,
        initial_scenario_id: str | None = None,
        materialization_identity: Mapping[str, Any] | None = None,
        fresh_doc: bool = False,
        replace_ystore_snapshot: bool = False,
    ) -> WebUIRegistryEntry:
        """
        Async counterpart of :meth:`compute_registry_for_webspace` for use
        inside running event loops.
        """
        rebuild_started = time.perf_counter()
        ydoc_timings: Dict[str, float] = {}
        self._last_rebuild_ydoc_timings_ms = None
        self._last_rebuild_snapshot_update = None
        self._last_rebuild_state_vector = None
        self._last_materialized_payload = None
        self._last_worker_diagnostics = None
        use_live_room = bool(publish_live_room) if prefer_live_room is None else bool(prefer_live_room)
        try:
            if fresh_doc:
                ystore = get_ystore_for_webspace(webspace_id) if replace_ystore_snapshot else None
                if ystore is not None:
                    stage_started = time.perf_counter()
                    await ystore.start()
                    _record_timing(ydoc_timings, "ystore_start", stage_started)
                else:
                    ydoc_timings["ystore_start"] = 0.0
                try:
                    stage_started = time.perf_counter()
                    worker_result = _get_cached_materialized_worker_result(materialization_identity)
                    if worker_result is not None:
                        _record_timing(ydoc_timings, "materialization_cache_lookup", stage_started)
                        ydoc_timings["fresh_doc_worker"] = 0.0
                        ydoc_timings["materialization_cache_hit"] = 0.0
                    else:
                        if _materialization_worker_enabled():
                            prepare_started = time.perf_counter()
                            prepared_skill_decls, prepared_skill_fingerprint = await _run_materialization_cpu(
                                self._prepare_materialization_skill_decls_sync,
                                webspace_id,
                            )
                            _record_timing(ydoc_timings, "prepare_skill_decls", prepare_started)
                            worker_result = await _run_materialization_worker(
                                webspace_id,
                                mode="fresh_doc",
                                request_id=request_id,
                                scenario_id=initial_scenario_id,
                                materialization_identity=materialization_identity,
                                skill_decls_snapshot=prepared_skill_decls,
                                skill_decls_fingerprint=prepared_skill_fingerprint,
                            )
                        else:
                            worker_result = await _run_materialization_cpu(
                                self._rebuild_fresh_doc_snapshot_sync,
                                webspace_id,
                                request_id=request_id,
                                initial_scenario_id=initial_scenario_id,
                                materialization_identity=materialization_identity,
                            )
                        _record_timing(ydoc_timings, "fresh_doc_worker", stage_started)
                        ydoc_timings["materialization_cache_miss"] = 0.0
                        _remember_materialized_worker_result(materialization_identity, worker_result)
                    _raise_if_rebuild_request_superseded(webspace_id, request_id)
                    entry = worker_result["entry"]
                    snapshot_update = bytes(worker_result.get("snapshot_update") or b"")
                    state_vector = bytes(worker_result.get("state_vector") or b"")
                    self._last_rebuild_snapshot_update = snapshot_update
                    self._last_rebuild_state_vector = state_vector
                    materialized_payload = worker_result.get("materialized_payload")
                    self._last_materialized_payload = (
                        _clone_json_like(materialized_payload)
                        if isinstance(materialized_payload, Mapping)
                        else None
                    )
                    worker_ydoc_timings = _copy_timing_map(worker_result.get("ydoc_timings_ms")) or {}
                    for timing_key, timing_value in worker_ydoc_timings.items():
                        if timing_key == "total":
                            continue
                        ydoc_timings[timing_key] = timing_value
                    if worker_result.get("worker_parent_elapsed_ms") is not None:
                        ydoc_timings["worker_process"] = round(
                            float(worker_result.get("worker_parent_elapsed_ms") or 0.0),
                            3,
                        )
                        self._last_worker_diagnostics = {
                            "mode": "fresh_doc",
                            "elapsed_ms": worker_result.get("worker_parent_elapsed_ms"),
                            "child_elapsed_ms": worker_result.get("worker_elapsed_ms"),
                            "init_ms": worker_result.get("worker_init_ms"),
                            "materialize_ms": worker_result.get("worker_materialize_ms"),
                            "peak_rss_bytes": worker_result.get("worker_peak_rss_bytes"),
                            "result_bytes": worker_result.get("worker_result_bytes"),
                        }
                    self._last_rebuild_timings_ms = _copy_timing_map(worker_result.get("rebuild_timings_ms"))
                    self._last_resolver_debug = dict(worker_result.get("resolver_debug") or {})
                    self._last_apply_summary = dict(worker_result.get("apply_summary") or {})
                    self._last_apply_phase_timings_ms = _copy_timing_map(
                        worker_result.get("apply_phase_timings_ms")
                    )
                    if ystore is not None:
                        stage_started = time.perf_counter()
                        async with ystore_write_metadata(
                            root_names=["ui", "data", "registry", "runtime"],
                            source="webspace_runtime.rebuild_async.replace_snapshot",
                            owner="core:webspace_runtime",
                            channel="core.webspace_runtime.snapshot_replace",
                            governed=True,
                        ):
                            replace_result = await ystore.replace_snapshot_update(
                                snapshot_update,
                                state_vector=state_vector,
                                backup_kind="builder_revision_apply_snapshot_replace",
                                persist_snapshot=False,
                                notify=False,
                            )
                        _record_timing(ydoc_timings, "ystore_replace_snapshot", stage_started)
                        backup_schedule = _schedule_builder_ystore_snapshot_backup(
                            webspace_id,
                            reason="builder_revision_apply_snapshot_replace_deferred",
                        )
                        ydoc_timings["ystore_backup_deferred"] = 0.0 if backup_schedule.get("scheduled") else -1.0
                        if isinstance(replace_result, Mapping):
                            try:
                                ydoc_timings["ystore_replace_persist"] = round(
                                    float(replace_result.get("persist_ms") or 0.0),
                                    3,
                                )
                            except Exception:
                                pass
                            try:
                                ydoc_timings["ystore_replace_notify"] = round(
                                    float(replace_result.get("notify_ms") or 0.0),
                                    3,
                                )
                            except Exception:
                                pass
                    else:
                        ydoc_timings["ystore_replace_snapshot"] = 0.0
                    ydoc_timings["encode_diff"] = 0.0
                    ydoc_timings["ystore_write_update"] = 0.0
                    ydoc_timings["room_update"] = 0.0
                    return entry
                finally:
                    if ystore is not None:
                        stage_started = time.perf_counter()
                        try:
                            ystore.stop()
                        except Exception:
                            pass
                        _record_timing(ydoc_timings, "ystore_stop", stage_started)
            async with _open_rebuild_ydoc_session(
                webspace_id,
                timings=ydoc_timings,
                publish_live_room=publish_live_room,
                prefer_live_room=use_live_room,
            ) as ydoc:
                seed_scenario = str(initial_scenario_id or "").strip()
                if seed_scenario:
                    stage_started = time.perf_counter()
                    ui_map = ydoc.get_map("ui")
                    with ydoc.begin_transaction() as txn:
                        _set_map_value_if_changed(ui_map, txn, "current_scenario", seed_scenario)
                    _record_timing(ydoc_timings, "seed_initial_scenario", stage_started)
                stage_started = time.perf_counter()
                entry = self._rebuild_in_doc(
                    ydoc,
                    webspace_id,
                    expected_request_id=request_id,
                    materialization_identity=materialization_identity,
                )
                _record_timing(ydoc_timings, "in_doc_rebuild", stage_started)
                return entry
        finally:
            self._last_rebuild_ydoc_timings_ms = _finalize_timing_map(ydoc_timings, started_at=rebuild_started)


# --- webspace helpers ---------------------------------------------------


def _payload(evt: Any) -> Dict[str, Any]:
    if hasattr(evt, "payload"):
        data = getattr(evt, "payload") or {}
        if isinstance(data, dict):
            return data
    if isinstance(evt, dict):
        return evt
    return {}


def _event_type(evt: Any) -> str | None:
    if hasattr(evt, "type"):
        token = str(getattr(evt, "type") or "").strip()
        if token:
            return token
    if isinstance(evt, dict):
        direct = str(evt.get("_event_type") or "").strip()
        if direct:
            return direct
        if "payload" in evt and "type" in evt:
            token = str(evt.get("type") or "").strip()
            if token:
                return token
        meta = evt.get("_meta")
        if isinstance(meta, dict):
            token = str(meta.get("event_type") or "").strip()
            if token:
                return token
    return None


def _webspace_id(payload: Dict[str, Any]) -> str:
    """
    Resolve target webspace id for an event payload.

    Explicit fields on the payload (webspace_id/workspace_id) take
    precedence over metadata injected by the transport (_meta).
    """
    if isinstance(payload, dict):
        direct = payload.get("webspace_id") or payload.get("workspace_id")
        if direct:
            return str(direct)
        meta = payload.get("_meta")
        if isinstance(meta, dict):
            token = meta.get("webspace_id") or meta.get("workspace_id")
            if token:
                return str(token)
    return default_webspace_id()


def _payload_command_trace(payload: Dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    return {
        "cmd_id": str(meta.get("cmd_id") or "").strip() or None,
        "gateway_client": str(meta.get("gateway_client") or "").strip() or None,
        "gateway_command_seq": int(meta.get("gateway_command_seq") or 0),
        "gateway_command_fingerprint": str(meta.get("gateway_command_fingerprint") or "").strip() or None,
        "device_id": str(meta.get("device_id") or "").strip() or None,
        "trace_id": str(meta.get("trace_id") or "").strip() or None,
    }


def _recovery_request_fingerprint(
    *,
    webspace_id: str,
    action: str,
    scenario_id: str | None,
    command_trace: Mapping[str, Any] | None = None,
) -> str:
    trace = command_trace if isinstance(command_trace, Mapping) else {}
    trace_fp = str(trace.get("gateway_command_fingerprint") or "").strip()
    if trace_fp:
        return trace_fp
    raw = {
        "webspace_id": str(webspace_id or "").strip() or "default",
        "action": str(action or "").strip() or "reload",
        "scenario_id": str(scenario_id or "").strip() or None,
    }
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:12]


async def _resolve_rebuild_scenario_target(
    webspace_id: str,
    requested_scenario_id: str | None,
    *,
    prefer_manifest_home_before_current: bool = False,
) -> tuple[WebspaceOperationalState, str, str]:
    """
    Resolve the effective scenario target for backend-owned rebuild flows.

    ``prefer_manifest_home_before_current`` preserves legacy reload/reset
    behaviour where the stored manifest home scenario remains authoritative
    unless the caller explicitly overrides it.
    """
    state = await describe_webspace_operational_state(webspace_id)
    requested = str(requested_scenario_id or "").strip()
    if requested:
        return state, requested, "explicit"

    stored_home = str(state.stored_home_scenario or "").strip() or None
    current = str(state.current_scenario or "").strip() or None
    effective_home = str(state.effective_home_scenario or "").strip() or None

    if prefer_manifest_home_before_current:
        if stored_home:
            return state, stored_home, "manifest_home"
        if current:
            return state, current, "current_scenario"
    else:
        if current:
            return state, current, "current_scenario"
        if effective_home:
            return state, effective_home, "manifest_home"

    return state, "web_desktop", "default"


def _resolve_projection_refresh_space(webspace_id: str) -> str:
    try:
        row = workspace_index.get_workspace(webspace_id) or workspace_index.ensure_workspace(webspace_id)
        return _PROJECTION_SERVICE.space_for_source(getattr(row, "effective_source_mode", ""))
    except Exception:
        return "workspace"


async def _refresh_projection_rules_for_rebuild(
    ctx: AgentContext,
    webspace_id: str,
    *,
    scenario_id: str | None = None,
    scenario_resolution: str | None = None,
) -> dict[str, Any]:
    target_scenario = str(scenario_id or "").strip() or None
    target_resolution = str(scenario_resolution or "").strip() or None
    if not target_scenario or not target_resolution:
        try:
            _state, resolved_scenario, resolved_resolution = await _resolve_rebuild_scenario_target(
                webspace_id,
                target_scenario,
                prefer_manifest_home_before_current=False,
            )
            if not target_scenario:
                target_scenario = resolved_scenario
            if not target_resolution:
                target_resolution = resolved_resolution
        except Exception:
            _log.debug("failed to resolve projection refresh target for webspace=%s", webspace_id, exc_info=True)
            target_scenario = target_scenario or None
            target_resolution = target_resolution or None
    target_space = _resolve_projection_refresh_space(webspace_id)
    result = _PROJECTION_SERVICE.refresh_rules(
        registry=getattr(ctx, "projections", None),
        scenario_id=target_scenario,
        scenario_resolution=target_resolution,
        space=target_space,
    )
    if result.get("error"):
        _log.debug("failed to refresh data_projections for scenario=%s: %s", target_scenario, result["error"])
    return result


def _slugify_webspace_id(raw: str | None) -> str:
    if not raw:
        return ""
    # Preserve original casing while normalising invalid characters so that
    # webspace ids used in events and YDoc room names stay identical.
    token = _WS_ID_RE.sub("-", str(raw).strip())
    return token.strip("-")


def _allocate_webspace_id(raw: str | None) -> str:
    candidate = _slugify_webspace_id(raw)
    if not candidate:
        candidate = f"space-{secrets.token_hex(2)}"
    base = candidate
    suffix = 1
    while workspace_index.get_workspace(candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _is_dev_title(title: Optional[str]) -> bool:
    if not title:
        return False
    return str(title).lstrip().upper().startswith("DEV:")


def _display_name_for_kind(title: Optional[str], *, webspace_id: str, kind: str) -> str:
    raw_title = (title or webspace_id).strip() or webspace_id
    if kind == "dev":
        if _is_dev_title(raw_title):
            return raw_title
        return f"DEV: {raw_title}"
    if _is_dev_title(raw_title):
        return raw_title.lstrip()[4:].lstrip() or webspace_id
    return raw_title


def _workspace_manifest_current_scenario(row: workspace_index.WebspaceManifest) -> str | None:
    """
    Return the workspace-owned current scenario pointer.

    ``ui.current_scenario`` is a rendered/live selector and can survive room
    eviction or YStore reuse. For manifests that already have an explicit home
    scenario, absence of this overlay means "use home", not "trust whatever is
    left in the live YDoc". Legacy manifests without stored home_scenario keep
    the old live-pointer fallback.
    """
    try:
        if getattr(row, "has_current_scenario_overlay", False):
            return str(getattr(row, "current_scenario_overlay", "") or "").strip() or None
        if getattr(row, "home_scenario", None) is None:
            return _try_read_live_current_scenario(row.workspace_id)
    except Exception:
        return None
    return None


def _webspace_listing() -> List[Dict[str, Any]]:
    rows = workspace_index.list_workspaces()
    local_display = _local_node_display()
    return [
        _with_webspace_validation(
            source_mode=row.effective_source_mode,
            stored_home_scenario=str(row.home_scenario).strip() if row.home_scenario else None,
            effective_home_scenario=row.effective_home_scenario,
            current_scenario=_workspace_manifest_current_scenario(row),
            payload={
                "id": row.workspace_id,
                "title": row.title,
                "created_at": row.created_at,
                "kind": row.effective_kind,
                "home_scenario": row.effective_home_scenario,
                "home_scenario_ref": getattr(row, "home_scenario_ref_overlay", {}) or None,
                "source_mode": row.effective_source_mode,
                "node_id": _local_node_id(),
                "node_label": local_display.get("node_label"),
                "node_compact_label": local_display.get("node_compact_label"),
                "node_index": local_display.get("node_index"),
                "node_color": local_display.get("node_color"),
            },
        )
        for row in rows
    ]


def _webspace_info_from_row(
    row: workspace_index.WebspaceManifest,
    *,
    local_display: Mapping[str, Any] | None = None,
    current_scenario: Any = _HOME_SCENARIO_REF_UNSET,
) -> WebspaceInfo:
    resolved_display = dict(local_display) if isinstance(local_display, Mapping) else _local_node_display()
    if current_scenario is _HOME_SCENARIO_REF_UNSET:
        current_scenario = _workspace_manifest_current_scenario(row)
    validation = _build_webspace_validation(
        source_mode=row.effective_source_mode,
        stored_home_scenario=str(row.home_scenario).strip() if row.home_scenario else None,
        effective_home_scenario=row.effective_home_scenario,
        current_scenario=current_scenario,
    )
    return WebspaceInfo(
        id=row.workspace_id,
        title=row.title,
        created_at=row.created_at,
        kind=row.effective_kind,
        home_scenario=row.effective_home_scenario,
        home_scenario_ref=getattr(row, "home_scenario_ref_overlay", {}) or None,
        source_mode=row.effective_source_mode,
        node_id=_local_node_id(),
        node_label=str(resolved_display.get("node_label") or _local_node_label()),
        node_compact_label=str(resolved_display.get("node_compact_label") or "") or None,
        node_index=resolved_display.get("node_index"),
        node_color=str(resolved_display.get("node_color") or "") or None,
        is_dev=row.is_dev,
        current_scenario=current_scenario,
        stored_home_scenario_exists=validation.get("stored_home_scenario_exists"),
        home_scenario_exists=bool(validation.get("home_scenario_exists")),
        current_scenario_exists=validation.get("current_scenario_exists"),
        degraded=bool(validation.get("degraded")),
        validation_reason=str(validation.get("validation_reason") or "").strip() or None,
        recommended_action=str(validation.get("recommended_action") or "").strip() or None,
    )


async def describe_webspace_operational_state(webspace_id: str) -> WebspaceOperationalState:
    """
    Return the combined manifest + live scenario state for a webspace.

    The helper intentionally keeps both the raw stored ``home_scenario`` and
    the effective fallback value so Phase 2 callers can preserve legacy reload
    behaviour while still exposing stable operational semantics to control
    surfaces.
    """
    target_webspace_id = str(webspace_id or "").strip() or default_webspace_id()
    row = workspace_index.get_workspace(target_webspace_id) or workspace_index.ensure_workspace(target_webspace_id)

    current_scenario: str | None = _workspace_manifest_current_scenario(row)
    validation = _build_webspace_validation(
        source_mode=row.effective_source_mode,
        stored_home_scenario=str(row.home_scenario).strip() if row.home_scenario else None,
        effective_home_scenario=row.effective_home_scenario,
        current_scenario=current_scenario,
    )
    if current_scenario is not None:
        return WebspaceOperationalState(
            webspace_id=target_webspace_id,
            title=row.title,
            kind=row.effective_kind,
            source_mode=row.effective_source_mode,
            is_dev=row.is_dev,
            stored_home_scenario=str(row.home_scenario).strip() if row.home_scenario else None,
            effective_home_scenario=row.effective_home_scenario,
            home_scenario_ref=getattr(row, "home_scenario_ref_overlay", {}) or None,
            current_scenario=current_scenario,
            stored_home_scenario_exists=validation.get("stored_home_scenario_exists"),
            home_scenario_exists=bool(validation.get("home_scenario_exists")),
            current_scenario_exists=validation.get("current_scenario_exists"),
            degraded=bool(validation.get("degraded")),
            validation_reason=str(validation.get("validation_reason") or "").strip() or None,
            recommended_action=str(validation.get("recommended_action") or "").strip() or None,
        )

    if row.home_scenario is None and current_scenario is None:
        try:
            async with _open_readonly_operational_ydoc(target_webspace_id) as ydoc:
                ui_map = ydoc.get_map("ui")
                raw_current = ui_map.get("current_scenario")
                if raw_current is not None:
                    current_scenario = _normalize_optional_token(raw_current)
        except Exception:
            current_scenario = None

    validation = _build_webspace_validation(
        source_mode=row.effective_source_mode,
        stored_home_scenario=str(row.home_scenario).strip() if row.home_scenario else None,
        effective_home_scenario=row.effective_home_scenario,
        current_scenario=current_scenario,
    )
    return WebspaceOperationalState(
        webspace_id=target_webspace_id,
        title=row.title,
        kind=row.effective_kind,
        source_mode=row.effective_source_mode,
        is_dev=row.is_dev,
        stored_home_scenario=str(row.home_scenario).strip() if row.home_scenario else None,
        effective_home_scenario=row.effective_home_scenario,
        home_scenario_ref=getattr(row, "home_scenario_ref_overlay", {}) or None,
        current_scenario=current_scenario,
        stored_home_scenario_exists=validation.get("stored_home_scenario_exists"),
        home_scenario_exists=bool(validation.get("home_scenario_exists")),
        current_scenario_exists=validation.get("current_scenario_exists"),
        degraded=bool(validation.get("degraded")),
        validation_reason=str(validation.get("validation_reason") or "").strip() or None,
        recommended_action=str(validation.get("recommended_action") or "").strip() or None,
    )


async def describe_webspace_validation_state(webspace_id: str) -> dict[str, Any]:
    state = await describe_webspace_operational_state(webspace_id)
    return {
        "webspace_id": state.webspace_id,
        "source_mode": state.source_mode,
        "stored_home_scenario": state.stored_home_scenario,
        "home_scenario": state.effective_home_scenario,
        "current_scenario": state.current_scenario,
        "stored_home_scenario_exists": state.stored_home_scenario_exists,
        "home_scenario_exists": state.home_scenario_exists,
        "current_scenario_exists": state.current_scenario_exists,
        "degraded": state.degraded,
        "validation_reason": state.validation_reason,
        "recommended_action": state.recommended_action,
    }


def describe_webspace_overlay_state(webspace_id: str) -> dict[str, Any]:
    target_webspace_id = str(webspace_id or "").strip() or default_webspace_id()
    row = workspace_index.get_workspace(target_webspace_id) or workspace_index.ensure_workspace(target_webspace_id)
    return {
        "webspace_id": target_webspace_id,
        "source": "workspace_manifest_overlay",
        "has_overlay": bool(getattr(row, "has_ui_overlay", False)),
        "has_installed": bool(getattr(row, "has_installed_overlay", False)),
        "has_pinned_widgets": bool(getattr(row, "has_pinned_widgets_overlay", False)),
        "has_topbar": bool(getattr(row, "has_topbar_overlay", False)),
        "has_page_schema": bool(getattr(row, "has_page_schema_overlay", False)),
        "has_icon_order": bool(getattr(row, "has_icon_order_overlay", False)),
        "has_widget_order": bool(getattr(row, "has_widget_order_overlay", False)),
        "desktop": dict(getattr(row, "desktop_overlay", {}) or {}),
        "installed": _coerce_dict(getattr(row, "installed_overlay", {}) or {}),
        "pinned_widgets": _normalize_overlay_widget_entries(getattr(row, "pinned_widgets_overlay", []) or []),
        "topbar": list(getattr(row, "topbar_overlay", []) or []),
        "page_schema": _coerce_dict(getattr(row, "page_schema_overlay", {}) or {}),
        "icon_order": list(getattr(row, "icon_order_overlay", []) or []),
        "widget_order": list(getattr(row, "widget_order_overlay", []) or []),
    }


async def describe_webspace_projection_state(
    webspace_id: str,
    *,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    """
    Return a lightweight snapshot of the projection lifecycle for a webspace.

    This is a read-only control-surface helper: it does not refresh or mutate
    the active registry, it only explains which scenario the current layer is
    targeting and whether that matches the active scenario layer in memory.
    """
    operational = await describe_webspace_operational_state(webspace_id)
    registry = get_ctx().projections
    return _PROJECTION_SERVICE.describe(
        operational=operational,
        scenario_id=scenario_id,
        registry=registry,
    )


async def _resolve_reload_scenario_target(
    webspace_id: str,
    requested_scenario_id: str | None,
) -> tuple[WebspaceOperationalState, str, str]:
    """
    Resolve the scenario source for reload/reset.

    Ordering intentionally preserves Phase 1 / Phase 2 compatibility:

    1. explicit scenario override
    2. explicit stored manifest home_scenario
    3. current live scenario for legacy spaces without stored home
    4. default ``web_desktop``
    """
    return await _resolve_rebuild_scenario_target(
        webspace_id,
        requested_scenario_id,
        prefer_manifest_home_before_current=True,
    )


def _try_read_live_current_scenario(webspace_id: str) -> str | None:
    live_hit, raw_current = try_read_live_map_value(webspace_id, "ui", "current_scenario")
    if not live_hit:
        return None
    return _normalize_optional_token(raw_current)


def _materialization_scenario_from_environment(environment: Any) -> str | None:
    raw_environment = _coerce_dict(environment)
    raw_materialization = _coerce_dict(raw_environment.get("materialization"))
    return _normalize_optional_token(raw_materialization.get("scenario_id"))


def _materialization_scenario_from_rebuild_state(rebuild_state: Mapping[str, Any] | None) -> str | None:
    state = _coerce_dict(rebuild_state)
    materialization = _coerce_dict(state.get("materialization"))
    candidates = (
        materialization.get("current_scenario"),
        materialization.get("scenario_id"),
    )
    for candidate in candidates:
        token = _normalize_optional_token(candidate)
        if token:
            return token
    return None


def _open_readonly_operational_ydoc(webspace_id: str):
    """
    Open a read-only YDoc session for operational/status reads.

    Prefer the modern live-room-aware accessor, but degrade gracefully to the
    legacy helper or a bare async getter while tests and older wrappers still
    patch narrower call signatures during the migration.
    """
    try:
        return async_get_ydoc(
            webspace_id,
            read_only=True,
            prefer_live_room=True,
        )
    except TypeError:
        try:
            return async_get_ydoc(webspace_id)
        except TypeError:
            return async_read_ydoc(webspace_id)


async def _read_effective_materialization_scenario(webspace_id: str) -> str | None:
    try:
        live_hit, raw_environment = try_read_live_map_value(webspace_id, "runtime", "environment")
        if live_hit:
            return _materialization_scenario_from_environment(raw_environment)
    except Exception:
        pass

    try:
        async with _open_readonly_operational_ydoc(webspace_id) as ydoc:
            runtime_map = ydoc.get_map("runtime")
            return _materialization_scenario_from_environment(runtime_map.get("environment"))
    except Exception:
        return None


def _open_rebuild_ydoc_session(
    webspace_id: str,
    *,
    timings: dict[str, float] | None = None,
    publish_live_room: bool = True,
    prefer_live_room: bool | None = None,
):
    """
    Open a writable YDoc session for semantic rebuild.

    Production code prefers the live-room-aware async accessor with timing
    capture, but tests and older shims may still expose a narrower
    `async_get_ydoc(webspace_id)` contract.
    """
    use_live_room = bool(publish_live_room) if prefer_live_room is None else bool(prefer_live_room)
    try:
        return async_get_ydoc(
            webspace_id,
            prefer_live_room=use_live_room,
            publish_live_room=bool(publish_live_room),
            timings=timings,
            load_mark_roots=["ui", "data", "registry", "runtime"],
            governed=True,
            write_source="webspace_runtime.rebuild_async",
            write_owner="core:webspace_runtime",
            write_channel="core.webspace_runtime.async",
        )
    except TypeError:
        try:
            return async_get_ydoc(
                webspace_id,
                prefer_live_room=use_live_room,
                timings=timings,
            )
        except TypeError:
            return async_get_ydoc(webspace_id)


async def _sync_webspace_listing(
    webspace_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Publish the catalog only into rooms that already exist.

    SQLite plus its monotonic catalog version is authoritative. This
    compatibility projection must never open YDocs as a side effect.
    """
    listing = _webspace_listing()
    payload = {
        "schema": "adaos.workspace_catalog.v1",
        "version": workspace_index.workspace_catalog_version(),
        "items": listing,
    }
    if webspace_ids is None:
        try:
            from adaos.services.yjs.gateway_ws import live_webspace_ids  # pylint: disable=import-outside-toplevel

            targets = live_webspace_ids(require_transport=True)
        except Exception:
            targets = []
    else:
        targets = sorted(
            {
                str(item or "").strip()
                for item in webspace_ids
                if str(item or "").strip()
            }
        )
    updated: list[str] = []
    skipped: list[str] = []
    for webspace_id in targets:
        def _mutator(doc: Any, txn: Any) -> None:
            data_map = doc.get_map("data")
            _set_map_value_if_changed(data_map, txn, "webspaces", payload)

        if mutate_live_room(
            webspace_id,
            _mutator,
            root_names=["data"],
            source="webspace_runtime.sync_listing",
            owner="core:webspace_runtime",
            channel="core.webspace_runtime.catalog",
        ):
            updated.append(webspace_id)
        else:
            skipped.append(webspace_id)
    return {
        "catalog_version": payload["version"],
        "targeted": targets,
        "updated": updated,
        "skipped_not_live": skipped,
    }


async def _sync_webspace_listing_target(webspace_id: str) -> dict[str, Any] | None:
    """Call the targeted catalog projection with legacy test/plugin fallback."""
    try:
        return await _sync_webspace_listing([webspace_id])
    except TypeError as exc:
        if "positional argument" not in str(exc) and "given" not in str(exc):
            raise
        return await _sync_webspace_listing()


def _schedule_webspace_listing_sync(
    *,
    reason: str,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    global _WEBSPACE_LISTING_SYNC_TASK  # pylint: disable=global-statement

    current = _WEBSPACE_LISTING_SYNC_TASK
    _TASK_STATE.webspace_listing_sync_task = current
    if current is not None and not current.done():
        return {
            "scheduled": True,
            "coalesced": True,
            "reason": reason,
            "task": current.get_name(),
        }

    async def _runner() -> None:
        started = time.perf_counter()
        try:
            if webspace_id:
                await _sync_webspace_listing_target(webspace_id)
            else:
                await _sync_webspace_listing()
            _log.info(
                "post-ready webspace listing sync completed reason=%s duration_ms=%.3f",
                reason,
                _elapsed_ms(started),
            )
        except Exception:
            _log.warning("post-ready webspace listing sync failed reason=%s", reason, exc_info=True)
        finally:
            global _WEBSPACE_LISTING_SYNC_TASK  # pylint: disable=global-statement
            if _TASK_STATE.webspace_listing_sync_task is task:
                _TASK_STATE.webspace_listing_sync_task = None
            if _WEBSPACE_LISTING_SYNC_TASK is task:
                _WEBSPACE_LISTING_SYNC_TASK = None

    task = asyncio.create_task(
        _runner(),
        name=f"webspace-listing-sync:{str(reason or 'background')[:40]}",
    )
    _TASK_STATE.webspace_listing_sync_task = task
    _WEBSPACE_LISTING_SYNC_TASK = task
    return {
        "scheduled": True,
        "coalesced": False,
        "reason": reason,
        "task": task.get_name(),
    }


def _live_room_refresh_stats(webspace_id: str) -> Dict[str, Any]:
    key = str(webspace_id or "").strip()
    stats = _LIVE_ROOM_REFRESH_STATS.get(key)
    if stats is None:
        stats = {
            "requested_total": 0,
            "scheduled_total": 0,
            "coalesced_total": 0,
            "completed_total": 0,
            "failed_total": 0,
            "last_reason": "",
            "last_requested_at": 0.0,
            "last_completed_at": 0.0,
        }
        _LIVE_ROOM_REFRESH_STATS[key] = stats
    return stats


def _schedule_live_room_refresh(
    *,
    webspace_id: str,
    reason: str,
    persist_repair: bool | None = None,
    force_full_state_update: bool = False,
    materialized_payload: Mapping[str, Any] | None = None,
    materialization_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    key = str(webspace_id or "").strip()
    if not key:
        return {"scheduled": False, "reason": "missing_webspace"}

    stats = _live_room_refresh_stats(key)
    stats["requested_total"] = int(stats.get("requested_total") or 0) + 1
    stats["last_reason"] = str(reason or "").strip()
    stats["last_requested_at"] = time.time()

    request = {
        "webspace_id": key,
        "reason": str(reason or "").strip() or "live_room_refresh",
    }
    if persist_repair is not None:
        request["persist_repair"] = bool(persist_repair)
    if force_full_state_update:
        request["force_full_state_update"] = True
    if isinstance(materialized_payload, Mapping) and materialized_payload:
        request["materialized_payload"] = _clone_json_like(materialized_payload)
    if isinstance(materialization_identity, Mapping) and materialization_identity:
        request["materialization_identity"] = _clone_json_like(materialization_identity)
    current = _LIVE_ROOM_REFRESH_TASKS.get(key)
    if current is not None and not current.done():
        _LIVE_ROOM_REFRESH_PENDING[key] = request
        stats["coalesced_total"] = int(stats.get("coalesced_total") or 0) + 1
        return {
            "scheduled": True,
            "deferred": True,
            "coalesced": True,
            "task": current.get_name(),
        }

    async def _runner(initial: dict[str, Any]) -> None:
        current_request = dict(initial)
        try:
            while True:
                delay = _live_room_refresh_debounce_s()
                if delay > 0:
                    await asyncio.sleep(delay)
                pending_before_start = _LIVE_ROOM_REFRESH_PENDING.pop(key, None)
                if pending_before_start:
                    current_request = dict(pending_before_start)
                active_reason = str(current_request.get("reason") or "").strip() or "live_room_refresh"
                started = time.perf_counter()
                try:
                    refresh_kwargs: dict[str, Any] = {"reason": active_reason}
                    if "persist_repair" in current_request:
                        refresh_kwargs["persist_repair"] = bool(current_request.get("persist_repair"))
                    request_payload = current_request.get("materialized_payload")
                    request_identity = current_request.get("materialization_identity")
                    if isinstance(request_payload, Mapping) and request_payload:
                        from adaos.services.yjs.gateway import apply_materialized_payload_to_live_room  # pylint: disable=import-outside-toplevel

                        if bool(current_request.get("force_full_state_update")):
                            refresh_kwargs["force_full_state_update"] = True
                        await apply_materialized_payload_to_live_room(
                            key,
                            materialized_payload=request_payload,
                            **refresh_kwargs,
                            materialization_identity=(
                                request_identity
                                if isinstance(request_identity, Mapping) and request_identity
                                else None
                            ),
                        )
                    else:
                        from adaos.services.yjs.gateway import reconcile_live_webspace_effective_branches  # pylint: disable=import-outside-toplevel

                        await reconcile_live_webspace_effective_branches(
                            key,
                            **refresh_kwargs,
                        )
                    stats = _live_room_refresh_stats(key)
                    stats["completed_total"] = int(stats.get("completed_total") or 0) + 1
                    stats["last_completed_at"] = time.time()
                    _log.info(
                        "deferred live-room refresh completed webspace=%s reason=%s duration_ms=%.3f",
                        key,
                        active_reason,
                        _elapsed_ms(started),
                    )
                except Exception:
                    stats = _live_room_refresh_stats(key)
                    stats["failed_total"] = int(stats.get("failed_total") or 0) + 1
                    _log.warning(
                        "deferred live-room refresh failed webspace=%s reason=%s",
                        key,
                        active_reason,
                        exc_info=True,
                    )
                next_request = _LIVE_ROOM_REFRESH_PENDING.pop(key, None)
                if not next_request:
                    break
                current_request = dict(next_request)
        finally:
            if _LIVE_ROOM_REFRESH_TASKS.get(key) is task:
                _LIVE_ROOM_REFRESH_TASKS.pop(key, None)

    task = asyncio.create_task(
        _runner(request),
        name=f"live-room-refresh:{key}"[:120],
    )
    _LIVE_ROOM_REFRESH_TASKS[key] = task
    stats["scheduled_total"] = int(stats.get("scheduled_total") or 0) + 1
    return {
        "scheduled": True,
        "deferred": True,
        "coalesced": False,
        "task": task.get_name(),
    }


def _schedule_builder_ystore_snapshot_backup(
    webspace_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    key = str(webspace_id or "").strip() or default_webspace_id()
    reason_token = str(reason or "").strip() or "builder_revision_snapshot_backup"
    current = _BUILDER_YSTORE_BACKUP_TASKS.get(key)
    if current is not None and not current.done():
        return {
            "scheduled": True,
            "coalesced": True,
            "reason": reason_token,
            "task": current.get_name(),
        }
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {
            "scheduled": False,
            "coalesced": False,
            "reason": reason_token,
            "error": "no_running_loop",
        }

    async def _runner() -> None:
        try:
            await asyncio.sleep(0.05)
            store = get_ystore_for_webspace(key)
            await store.backup_to_disk(
                compact_runtime=True,
                backup_kind=reason_token,
            )
        except Exception:
            _log.warning(
                "builder YStore snapshot background backup failed webspace=%s reason=%s",
                key,
                reason_token,
                exc_info=True,
            )

    task = loop.create_task(_runner(), name=f"builder-ystore-backup:{key}"[:120])
    _BUILDER_YSTORE_BACKUP_TASKS[key] = task

    def _done(done: asyncio.Task[Any]) -> None:
        if _BUILDER_YSTORE_BACKUP_TASKS.get(key) is done:
            _BUILDER_YSTORE_BACKUP_TASKS.pop(key, None)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            _log.warning(
                "builder YStore snapshot background backup task failed webspace=%s reason=%s",
                key,
                reason_token,
                exc_info=True,
            )

    task.add_done_callback(_done)
    return {
        "scheduled": True,
        "coalesced": False,
        "reason": reason_token,
        "task": task.get_name(),
    }


def _workflow_sync_stats(webspace_id: str) -> Dict[str, Any]:
    key = str(webspace_id or "").strip()
    stats = _WORKFLOW_SYNC_STATS.get(key)
    if stats is None:
        stats = {
            "requested_total": 0,
            "scheduled_total": 0,
            "coalesced_total": 0,
            "completed_total": 0,
            "failed_total": 0,
            "last_reason": "",
            "last_scenario_id": "",
            "last_requested_at": 0.0,
            "last_completed_at": 0.0,
        }
        _WORKFLOW_SYNC_STATS[key] = stats
    return stats


def _schedule_workflow_sync(
    ctx: AgentContext,
    *,
    webspace_id: str,
    scenario_id: str,
    reason: str,
) -> dict[str, Any]:
    key = str(webspace_id or "").strip()
    scenario_token = str(scenario_id or "").strip()
    if not key or not scenario_token:
        return {"scheduled": False, "reason": "missing_target"}

    stats = _workflow_sync_stats(key)
    stats["requested_total"] = int(stats.get("requested_total") or 0) + 1
    stats["last_reason"] = str(reason or "").strip()
    stats["last_scenario_id"] = scenario_token
    stats["last_requested_at"] = time.time()

    request = {
        "webspace_id": key,
        "scenario_id": scenario_token,
        "reason": str(reason or "").strip() or "workflow_sync",
    }
    current = _WORKFLOW_SYNC_TASKS.get(key)
    if current is not None and not current.done():
        _WORKFLOW_SYNC_PENDING[key] = request
        stats["coalesced_total"] = int(stats.get("coalesced_total") or 0) + 1
        return {
            "scheduled": True,
            "deferred": True,
            "coalesced": True,
            "task": current.get_name(),
            "scenario_id": scenario_token,
        }

    async def _runner(initial: dict[str, Any]) -> None:
        current_request = dict(initial)
        try:
            while True:
                delay = _workflow_sync_debounce_s()
                if delay > 0:
                    await asyncio.sleep(delay)
                pending_before_start = _WORKFLOW_SYNC_PENDING.pop(key, None)
                if pending_before_start:
                    current_request = dict(pending_before_start)
                started = time.perf_counter()
                active_scenario = str(current_request.get("scenario_id") or "").strip()
                active_reason = str(current_request.get("reason") or "").strip() or "workflow_sync"
                try:
                    wf = ScenarioWorkflowRuntime(ctx)
                    await wf.sync_workflow_for_webspace(active_scenario, key)
                    stats = _workflow_sync_stats(key)
                    stats["completed_total"] = int(stats.get("completed_total") or 0) + 1
                    stats["last_completed_at"] = time.time()
                    _log.info(
                        "deferred workflow sync completed webspace=%s scenario=%s reason=%s duration_ms=%.3f",
                        key,
                        active_scenario,
                        active_reason,
                        _elapsed_ms(started),
                    )
                except BaseException as exc:
                    if _is_control_flow_base_exception(exc):
                        raise
                    stats = _workflow_sync_stats(key)
                    stats["failed_total"] = int(stats.get("failed_total") or 0) + 1
                    _log.warning(
                        "deferred workflow sync failed webspace=%s scenario=%s reason=%s",
                        key,
                        active_scenario,
                        active_reason,
                        exc_info=True,
                    )
                next_request = _WORKFLOW_SYNC_PENDING.pop(key, None)
                if not next_request:
                    break
                current_request = dict(next_request)
        finally:
            if _WORKFLOW_SYNC_TASKS.get(key) is task:
                _WORKFLOW_SYNC_TASKS.pop(key, None)

    task = asyncio.create_task(
        _runner(request),
        name=f"workflow-sync:{key}:{scenario_token}"[:120],
    )
    _WORKFLOW_SYNC_TASKS[key] = task
    stats["scheduled_total"] = int(stats.get("scheduled_total") or 0) + 1
    return {
        "scheduled": True,
        "deferred": True,
        "coalesced": False,
        "task": task.get_name(),
        "scenario_id": scenario_token,
    }


class WebspaceService:
    """
    Helper for managing webspaces (workspaces) from core services and SDK.

    This service centralises CRUD logic that was previously spread across
    event handlers so that higher-level callers do not need to touch YDoc
    or SQLite details directly.
    """

    def __init__(self, ctx: Optional[AgentContext] = None) -> None:
        self.ctx: AgentContext = ctx or get_ctx()

    def list_ids(self, *, mode: str = "mixed") -> List[str]:
        rows = workspace_index.list_workspaces()
        ids: List[str] = []
        for row in rows:
            kind = row.effective_kind
            if mode == "workspace" and kind != "workspace":
                continue
            if mode == "dev" and kind != "dev":
                continue
            token = str(row.workspace_id or "").strip()
            if token:
                ids.append(token)
        return ids

    def list(self, *, mode: str = "mixed") -> List[WebspaceInfo]:
        """
        List known webspaces.

        mode:
          - \"workspace\" — only non-dev webspaces,
          - \"dev\"       — only dev webspaces,
          - \"mixed\"     — all (default).
        """
        rows = workspace_index.list_workspaces()
        infos: List[WebspaceInfo] = []
        local_display = _local_node_display()
        for row in rows:
            title = row.title
            kind = row.effective_kind
            is_dev = row.is_dev
            if mode == "workspace" and kind != "workspace":
                continue
            if mode == "dev" and kind != "dev":
                continue
            infos.append(_webspace_info_from_row(row, local_display=local_display))
        return infos

    async def _sync_listing(self, webspace_id: str | None = None) -> None:
        if webspace_id:
            await _sync_webspace_listing_target(webspace_id)
        else:
            await _sync_webspace_listing()

    async def create(
        self,
        requested_id: Optional[str],
        title: Optional[str],
        *,
        scenario_id: str = "web_desktop",
        scenario_ref: Any = None,
        dev: bool = False,
    ) -> WebspaceInfo:
        webspace_id = _allocate_webspace_id(requested_id)
        _log.info("creating webspace %s (requested=%s dev=%s)", webspace_id, requested_id, dev)
        kind = "dev" if dev else "workspace"
        source_mode = "dev" if dev else "workspace"
        workspace_index.ensure_workspace(webspace_id)
        display_name = _display_name_for_kind(title, webspace_id=webspace_id, kind=kind)
        row = workspace_index.set_workspace_manifest(
            webspace_id,
            display_name=display_name,
            kind=kind,
            home_scenario=str(scenario_id or "").strip() or "web_desktop",
            source_mode=source_mode,
        )
        if isinstance(scenario_ref, Mapping):
            row = workspace_index.set_workspace_home_scenario_ref_overlay(webspace_id, dict(scenario_ref))
        await _seed_webspace_from_scenario(webspace_id, scenario_id, dev=dev)
        await rebuild_webspace_from_sources(
            webspace_id,
            action="create",
            scenario_id=str(scenario_id or "").strip() or "web_desktop",
            scenario_resolution="explicit",
            source_of_truth="webspace_create",
        )
        await self._sync_listing(webspace_id)
        return _webspace_info_from_row(row)

    async def rename(self, webspace_id: str, title: str) -> Optional[WebspaceInfo]:
        webspace_id = (webspace_id or "").strip()
        title = (title or "").strip()
        if not webspace_id or not title:
            return None
        row = workspace_index.get_workspace(webspace_id)
        if not row:
            _log.warning("cannot rename missing webspace %s", webspace_id)
            return None
        display_name = _display_name_for_kind(title, webspace_id=webspace_id, kind=row.effective_kind)
        row = workspace_index.set_workspace_manifest(
            webspace_id,
            display_name=display_name,
            kind=row.effective_kind,
            source_mode=row.effective_source_mode,
        )
        await self._sync_listing(webspace_id)
        return _webspace_info_from_row(row)

    async def update_metadata(
        self,
        webspace_id: str,
        *,
        title: str | None = None,
        home_scenario: str | None = None,
        home_scenario_ref: Any = _HOME_SCENARIO_REF_UNSET,
    ) -> Optional[WebspaceInfo]:
        webspace_id = str(webspace_id or "").strip()
        if not webspace_id:
            return None
        row = workspace_index.get_workspace(webspace_id)
        if not row:
            _log.warning("cannot update missing webspace %s", webspace_id)
            return None

        manifest_kwargs: Dict[str, Any] = {}
        next_title = str(title or "").strip()
        if next_title:
            manifest_kwargs["display_name"] = _display_name_for_kind(
                next_title,
                webspace_id=webspace_id,
                kind=row.effective_kind,
            )

        next_home_scenario = str(home_scenario or "").strip()
        if next_home_scenario:
            manifest_kwargs["home_scenario"] = next_home_scenario

        if not manifest_kwargs and home_scenario_ref is _HOME_SCENARIO_REF_UNSET:
            return _webspace_info_from_row(row)

        updated = row if not manifest_kwargs else workspace_index.set_workspace_manifest(webspace_id, **manifest_kwargs)
        if home_scenario_ref is not _HOME_SCENARIO_REF_UNSET:
            updated = workspace_index.set_workspace_home_scenario_ref_overlay(webspace_id, home_scenario_ref)
        await self._sync_listing(webspace_id)
        return _webspace_info_from_row(updated)

    async def set_home_scenario(
        self,
        webspace_id: str,
        scenario_id: str,
        *,
        home_scenario_ref: Any = _HOME_SCENARIO_REF_UNSET,
    ) -> Optional[WebspaceInfo]:
        webspace_id = (webspace_id or "").strip()
        scenario_id = (scenario_id or "").strip()
        if not webspace_id or not scenario_id:
            return None
        row = workspace_index.get_workspace(webspace_id)
        if not row:
            _log.warning("cannot set home_scenario for missing webspace %s", webspace_id)
            return None
        row = workspace_index.set_workspace_manifest(webspace_id, home_scenario=scenario_id)
        if home_scenario_ref is not _HOME_SCENARIO_REF_UNSET:
            row = workspace_index.set_workspace_home_scenario_ref_overlay(webspace_id, home_scenario_ref)
        await self._sync_listing(webspace_id)
        return _webspace_info_from_row(row)

    async def ensure_dev_for_scenario(
        self,
        scenario_id: str,
        *,
        requested_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> tuple[WebspaceInfo, bool]:
        scenario_id = str(scenario_id or "").strip()
        requested_id = str(requested_id or "").strip() or None
        title = str(title or "").strip() or None
        if not scenario_id:
            raise ValueError("scenario_id is required")

        existing: Optional[workspace_index.WebspaceManifest] = None
        if requested_id:
            row = workspace_index.get_workspace(requested_id)
            if row and not row.is_dev:
                raise ValueError("requested webspace is not a dev webspace")
            existing = row
        if existing is None:
            for row in workspace_index.list_workspaces():
                if row.is_dev and row.effective_home_scenario == scenario_id:
                    existing = row
                    break

        created = False
        if existing is None:
            preferred_id = requested_id or f"dev-{scenario_id}"
            info = await self.create(
                preferred_id,
                title or scenario_id,
                scenario_id=scenario_id,
                dev=True,
            )
            created = True
        else:
            info = _webspace_info_from_row(existing)

        return info, created

    async def delete(self, webspace_id: str) -> bool:
        webspace_id = (webspace_id or "").strip()
        if not webspace_id or webspace_id == default_webspace_id():
            return False
        _log.info("deleting webspace %s via WebspaceService", webspace_id)
        try:
            workspace_index.delete_workspace(webspace_id)
        except Exception as exc:
            _log.warning("failed to delete webspace %s: %s", webspace_id, exc)
            return False
        try:
            from adaos.services.yjs.gateway import reset_live_webspace_room  # pylint: disable=import-outside-toplevel
            from adaos.services.yjs.store import reset_ystore_for_webspace  # pylint: disable=import-outside-toplevel
 
            try:
                await reset_live_webspace_room(webspace_id, close_reason="webspace_delete")
            except Exception:
                pass
            try:
                reset_ystore_for_webspace(webspace_id)
            except Exception:
                pass
        except Exception:
            _log.warning("failed to reset ystore for webspace=%s", webspace_id, exc_info=True)
        await self._sync_listing(webspace_id)
        return True

    async def refresh(self) -> None:
        try:
            workspace_index.normalize_workspaces()
        except Exception:
            _log.debug("failed to normalize webspace manifests before refresh", exc_info=True)
        await self._sync_listing()


async def _seed_webspace_from_scenario(webspace_id: str, scenario_id: str, *, dev: Optional[bool] = None) -> None:
    """
    Seed a webspace YDoc from the given scenario package using the standard
    ScenarioManager.sync_to_yjs* projection path, falling back to static
    seeds inside ensure_webspace_seeded_from_scenario when needed.
    """
    await _seed_webspace_from_scenario_with_options(
        webspace_id,
        scenario_id,
        dev=dev,
    )


def _resolve_webspace_source_mode(webspace_id: str, *, dev: Optional[bool] = None) -> str:
    source_mode = "workspace"
    if dev is None:
        try:
            row = workspace_index.get_workspace(webspace_id)
            if row:
                dev = row.is_dev
                source_mode = row.effective_source_mode
            else:
                dev = False
        except Exception:
            dev = False
    elif dev:
        source_mode = "dev"
    return source_mode


def _build_scenario_manager():
    from adaos.adapters.db import SqliteScenarioRegistry  # pylint: disable=import-outside-toplevel
    from adaos.services.scenario.manager import ScenarioManager  # pylint: disable=import-outside-toplevel

    ctx = get_ctx()
    reg = SqliteScenarioRegistry(ctx.sql)
    return ScenarioManager(
        repo=ctx.scenarios_repo,
        registry=reg,
        git=ctx.git,
        paths=ctx.paths,
        bus=ctx.bus,
        caps=ctx.caps,
    )


async def _project_webspace_from_scenario(
    webspace_id: str,
    scenario_id: str,
    *,
    dev: Optional[bool] = None,
    emit_event: bool = True,
) -> None:
    source_mode = _resolve_webspace_source_mode(webspace_id, dev=dev)
    _log.debug(
        "projecting webspace=%s scenario=%s source_mode=%s emit_event=%s",
        webspace_id,
        scenario_id,
        source_mode,
        emit_event,
    )
    timeout_s = _project_scenario_timeout_s()
    result = await _PROJECTION_SERVICE.project(
        operation=lambda: _build_scenario_manager().sync_to_yjs_async(
            scenario_id or "web_desktop",
            webspace_id,
            space=source_mode,
            emit_event=emit_event,
        ),
        timeout_s=timeout_s,
    )
    if result["status"] == "timed_out":
        _log.warning(
            "timed out projecting webspace=%s from scenario=%s timeout_s=%s",
            webspace_id,
            scenario_id,
            _project_scenario_timeout_s(),
        )
    elif result["status"] == "failed":
        _log.warning(
            "failed to project webspace=%s from scenario=%s: %s",
            webspace_id,
            scenario_id,
            result.get("error"),
        )


async def _seed_webspace_from_scenario_with_options(
    webspace_id: str,
    scenario_id: str,
    *,
    dev: Optional[bool] = None,
) -> None:
    ystore = get_ystore_for_webspace(webspace_id)
    source_mode = _resolve_webspace_source_mode(webspace_id, dev=dev)
    _log.debug("seeding webspace=%s scenario=%s dev=%s", webspace_id, scenario_id, dev)
    try:
        await ensure_webspace_seeded_from_scenario(
            ystore,
            webspace_id=webspace_id,
            default_scenario_id=scenario_id or "web_desktop",
            space=source_mode,
            prefer_default_scenario=True,
        )
    except Exception:
        _log.warning("failed to seed webspace=%s from scenario=%s", webspace_id, scenario_id, exc_info=True)


# --- event subscriptions (core-level) -----------------------------------


@subscribe("scenarios.synced")
async def _on_scenarios_synced(evt: Dict[str, Any]) -> None:
    """
    Rebuild effective UI for a webspace when its scenario has been projected
    into YDoc by ScenarioManager.sync_to_yjs*.
    """
    webspace_id = str(evt.get("webspace_id") or default_webspace_id())
    scenario_id = str(evt.get("scenario_id") or "").strip() or None
    await rebuild_webspace_from_sources(
        webspace_id,
        action="scenario_projection_sync",
        scenario_id=scenario_id,
        scenario_resolution="projected_payload",
        source_of_truth="scenario_projection",
    )


@subscribe("skills.activated")
async def _on_skill_activated(evt: Dict[str, Any]) -> None:
    """
    Rebuild effective UI for the target webspace when a skill is activated.

    For MVP we only rebuild the webspace explicitly referenced in the event
    (or the default webspace), not all workspaces.
    """
    if bool(evt.get("defer_webspace_rebuild")):
        return
    webspace_id = str(evt.get("webspace_id") or default_webspace_id())
    schedule_skill_runtime_rebuild(
        webspace_id=webspace_id,
        action="skill_activation_sync",
        source_of_truth="skill_runtime",
        reason=str(evt.get("skill_name") or evt.get("name") or "skills.activated"),
    )


@subscribe("skills.updated")
async def _on_skill_updated(evt: Dict[str, Any]) -> None:
    if bool(evt.get("defer_webspace_rebuild")):
        return
    webspace_id = str(evt.get("webspace_id") or default_webspace_id())
    schedule_skill_runtime_rebuild(
        webspace_id=webspace_id,
        action="skill_update_sync",
        source_of_truth="skill_runtime",
        reason=str(evt.get("name") or evt.get("skill_name") or "skills.updated"),
    )


@subscribe("skills.rolledback")
async def _on_skill_rolled_back(evt: Dict[str, Any]) -> None:
    """
    Rebuild effective UI when a skill is rolled back so that its catalog
    entries and registry contributions are removed from the target webspace.
    """
    webspace_id = str(evt.get("webspace_id") or default_webspace_id())
    schedule_skill_runtime_rebuild(
        webspace_id=webspace_id,
        action="skill_rollback_sync",
        source_of_truth="skill_runtime",
        reason=str(evt.get("name") or evt.get("skill_name") or "skills.rolledback"),
    )


@subscribe("skill.uninstalled")
async def _on_skill_uninstalled(evt: Dict[str, Any]) -> None:
    webspace_id = str(evt.get("webspace_id") or default_webspace_id())
    schedule_skill_runtime_rebuild(
        webspace_id=webspace_id,
        action="skill_uninstall_sync",
        source_of_truth="skill_runtime",
        reason=str(evt.get("name") or evt.get("skill_name") or "skill.uninstalled"),
    )


@subscribe("scenario.removed")
async def _on_scenario_removed(evt: Dict[str, Any]) -> None:
    webspace_id = str(evt.get("webspace_id") or default_webspace_id())
    await rebuild_webspace_from_sources(
        webspace_id,
        action="scenario_uninstall_sync",
        source_of_truth="scenario_projection",
    )


def _member_snapshot_rebuild_targets() -> list[str]:
    try:
        rows = [row for row in workspace_index.list_workspaces() if not bool(getattr(row, "is_dev", False))]
    except Exception:
        rows = []
    return [
        str(getattr(row, "workspace_id", "") or "").strip()
        for row in rows
        if str(getattr(row, "workspace_id", "") or "").strip()
    ] or [default_webspace_id()]


def _member_desktop_catalog_expected_ids(node_id: str, snapshot: Mapping[str, Any]) -> dict[str, set[str]]:
    catalog = snapshot.get("desktop_catalog") if isinstance(snapshot.get("desktop_catalog"), Mapping) else {}
    expected: dict[str, set[str]] = {"apps": set(), "widgets": set()}
    for kind in ("apps", "widgets"):
        items = catalog.get(kind) if isinstance(catalog.get(kind), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            if _catalog_entry_is_foreign_relay(item, node_id=node_id):
                continue
            entry = _scope_remote_catalog_entry_id(_clone_json_like(item), node_id=node_id)
            entry_id = str(entry.get("id") or "").strip()
            if entry_id:
                expected[kind].add(entry_id)
    return expected


def _catalog_ids_from_snapshot(catalog: Any, kind: str) -> set[str]:
    if not isinstance(catalog, Mapping):
        return set()
    items = catalog.get(kind) if isinstance(catalog.get(kind), list) else []
    return {str(item.get("id") or "").strip() for item in items if isinstance(item, Mapping) and str(item.get("id") or "").strip()}


def _member_catalog_projection_missing_from_catalog(catalog: Any, expected: Mapping[str, set[str]]) -> bool:
    if catalog is _YDOC_PATH_MISSING:
        return True
    current_apps = _catalog_ids_from_snapshot(catalog, "apps")
    current_widgets = _catalog_ids_from_snapshot(catalog, "widgets")
    return bool((set(expected.get("apps") or set()) - current_apps) or (set(expected.get("widgets") or set()) - current_widgets))


async def _read_persisted_catalog_for_member_projection_check(webspace_id: str) -> Any:
    try:
        async with async_read_ydoc(webspace_id, prefer_live_room=False) as ydoc:
            return _read_current_ydoc_path_value(ydoc, "data/catalog")
    except TypeError:
        try:
            async with async_read_ydoc(webspace_id) as ydoc:
                return _read_current_ydoc_path_value(ydoc, "data/catalog")
        except Exception:
            return _YDOC_PATH_MISSING
    except Exception:
        return _YDOC_PATH_MISSING


async def _refresh_live_room_for_member_catalog_projection(webspace_id: str, *, node_id: str) -> bool:
    try:
        from adaos.services.yjs.gateway import reconcile_live_webspace_effective_branches  # pylint: disable=import-outside-toplevel

        result = await reconcile_live_webspace_effective_branches(
            webspace_id,
            reason=f"member_snapshot_refreshed_catalog_present:{node_id}",
        )
        return bool(result.get("ok") is not False)
    except Exception:
        _log.debug(
            "failed to refresh live Yjs room for member catalog projection webspace=%s node_id=%s",
            webspace_id,
            node_id,
            exc_info=True,
        )
        return False


async def _member_catalog_projection_missing(*, webspace_id: str, node_id: str) -> bool:
    try:
        from adaos.services.registry.subnet_directory import get_directory

        node = get_directory().get_node(node_id) or {}
    except Exception:
        return False
    runtime_projection = node.get("runtime_projection") if isinstance(node.get("runtime_projection"), Mapping) else {}
    snapshot = runtime_projection.get("snapshot") if isinstance(runtime_projection.get("snapshot"), Mapping) else {}
    expected = _member_desktop_catalog_expected_ids(node_id, snapshot)
    if not expected["apps"] and not expected["widgets"]:
        return False

    catalog: Any = _YDOC_PATH_MISSING
    live_ydoc = _resolve_live_room_ydoc(webspace_id)
    if live_ydoc is not None:
        catalog = _read_current_ydoc_path_value(live_ydoc, "data/catalog")
        if not _member_catalog_projection_missing_from_catalog(catalog, expected):
            return False
        persisted_catalog = await _read_persisted_catalog_for_member_projection_check(webspace_id)
        if not _member_catalog_projection_missing_from_catalog(persisted_catalog, expected):
            if await _refresh_live_room_for_member_catalog_projection(webspace_id, node_id=node_id):
                return False
            return True
        return True

    catalog = await _read_persisted_catalog_for_member_projection_check(webspace_id)
    return _member_catalog_projection_missing_from_catalog(catalog, expected)


def _member_entity_event_node_id(payload: Mapping[str, Any] | None) -> str:
    data = payload if isinstance(payload, Mapping) else {}
    scope = data.get("scope") if isinstance(data.get("scope"), Mapping) else {}
    entity_ref = str(data.get("entity_ref") or "").strip()
    entity_kind = str(data.get("entity_kind") or "").strip().lower()
    link_kind = str(scope.get("link_kind") or "").strip().lower()
    if link_kind and link_kind != "member":
        return ""
    if entity_kind and entity_kind != "device.member":
        return ""
    if entity_ref and not entity_ref.startswith("device:member:"):
        return ""
    node_id = (
        str(scope.get("device_id") or "").strip()
        or str(scope.get("node_id") or "").strip()
    )
    if not node_id and entity_ref.startswith("device:member:"):
        node_id = entity_ref.removeprefix("device:member:").strip()
    return node_id


async def _schedule_member_snapshot_rebuild_from_event(
    evt: Any,
    *,
    only_when_catalog_missing: bool = False,
    force_rebuild: bool = False,
) -> None:
    payload = _payload(evt)
    node_id = str(payload.get("node_id") or "").strip() or "member"
    reason = _member_snapshot_rebuild_reason(evt, payload)
    now = time.monotonic()
    interval_s = _member_snapshot_rebuild_min_interval_s()
    targets = _member_snapshot_rebuild_targets()
    for webspace_id in targets:
        if only_when_catalog_missing and not await _member_catalog_projection_missing(webspace_id=webspace_id, node_id=node_id):
            continue
        key = f"{node_id}\0{webspace_id}"
        stats = _member_snapshot_rebuild_stats(key)
        request_id = _member_snapshot_rebuild_request_id(webspace_id=webspace_id, node_id=node_id)
        stats["requested_total"] = int(stats.get("requested_total") or 0) + 1
        stats["last_reason"] = reason
        stats["last_requested_at"] = time.time()
        stats["last_request_id"] = request_id
        if force_rebuild:
            _MEMBER_SNAPSHOT_REBUILD_MATERIAL_FINGERPRINT.pop(key, None)
        material_fingerprint = _member_snapshot_desktop_material_fingerprint(node_id)
        if material_fingerprint:
            previous_fingerprint = _MEMBER_SNAPSHOT_REBUILD_MATERIAL_FINGERPRINT.get(key)
            if previous_fingerprint == material_fingerprint and not force_rebuild:
                stats["skipped_unchanged_total"] = int(stats.get("skipped_unchanged_total") or 0) + 1
                stats["last_skipped_unchanged_at"] = time.time()
                stats["last_material_fingerprint"] = material_fingerprint
                _log.debug(
                    "skipped unchanged member snapshot rebuild webspace=%s node_id=%s reason=%s fingerprint=%s",
                    webspace_id,
                    node_id,
                    reason,
                    material_fingerprint[:12],
                )
                continue
            _MEMBER_SNAPSHOT_REBUILD_MATERIAL_FINGERPRINT[key] = material_fingerprint
            stats["last_material_fingerprint"] = material_fingerprint
        existing = _MEMBER_SNAPSHOT_REBUILD_TASKS.get(key)
        if existing is not None and not existing.done():
            _mark_member_snapshot_rebuild_dirty(task_key=key, reason=reason, mode="task_running", request_id=request_id)
            continue
        last_at = float(_MEMBER_SNAPSHOT_REBUILD_AT.get(key) or 0.0)
        if not force_rebuild and interval_s > 0 and last_at > 0 and now - last_at < interval_s:
            _mark_member_snapshot_rebuild_dirty(task_key=key, reason=reason, mode="interval_window", request_id=request_id)
            _schedule_member_snapshot_rebuild_delayed(
                webspace_id=webspace_id,
                node_id=node_id,
                delay_s=max(0.0, interval_s - (now - last_at)),
                reason=reason,
                request_id=request_id,
            )
            continue
        _MEMBER_SNAPSHOT_REBUILD_AT[key] = now
        _schedule_member_snapshot_rebuild(webspace_id=webspace_id, node_id=node_id, reason=reason, request_id=request_id)


@subscribe("subnet.member.snapshot.changed")
async def _on_subnet_member_snapshot_changed(evt: Any) -> None:
    await _schedule_member_snapshot_rebuild_from_event(evt)


@subscribe("subnet.member.snapshot.refreshed")
async def _on_subnet_member_snapshot_refreshed(evt: Any) -> None:
    await _schedule_member_snapshot_rebuild_from_event(evt, only_when_catalog_missing=True)


@subscribe("subnet.member.access.reactivated")
async def _on_subnet_member_access_reactivated(evt: Any) -> None:
    await _schedule_member_snapshot_rebuild_from_event(evt, force_rebuild=True)


@subscribe("subnet.member.meta.changed")
async def _on_subnet_member_meta_changed(evt: Any) -> None:
    await _schedule_member_snapshot_rebuild_from_event(evt, force_rebuild=True)


@subscribe("entity.display_name.changed")
@subscribe("entity.observed")
async def _on_member_entity_name_changed(evt: Any) -> None:
    payload = _payload(evt)
    node_id = _member_entity_event_node_id(payload)
    if not node_id:
        return
    await _schedule_member_snapshot_rebuild_from_event(
        {
            "node_id": node_id,
            "type": _topic(evt) or str(payload.get("reason") or "entity.name.changed"),
            "source": str(payload.get("source") or "").strip() or "entity",
        },
        force_rebuild=True,
    )


def _schedule_member_snapshot_rebuild(
    *,
    webspace_id: str,
    node_id: str,
    reason: str = "subnet.member.snapshot.changed",
    request_id: str | None = None,
) -> None:
    task_key = f"{str(node_id or '').strip()}\0{str(webspace_id or '').strip()}"
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    delayed = _MEMBER_SNAPSHOT_REBUILD_DELAYED_TASKS.pop(task_key, None)
    if delayed is not None and delayed is not current_task and not delayed.done():
        delayed.cancel()
    existing = _MEMBER_SNAPSHOT_REBUILD_TASKS.get(task_key)
    if existing and not existing.done():
        _mark_member_snapshot_rebuild_dirty(task_key=task_key, reason=reason, mode="task_running", request_id=request_id)
        return
    stats = _member_snapshot_rebuild_stats(task_key)
    effective_request_id = str(request_id or "").strip() or _member_snapshot_rebuild_request_id(
        webspace_id=webspace_id,
        node_id=node_id,
    )
    stats["scheduled_total"] = int(stats.get("scheduled_total") or 0) + 1
    stats["last_reason"] = str(reason or "").strip() or str(stats.get("last_reason") or "") or "subnet.member.snapshot.changed"
    stats["last_scheduled_at"] = time.time()
    stats["last_request_id"] = effective_request_id
    stats["current_request_id"] = effective_request_id

    async def _runner() -> None:
        try:
            try:
                await _seed_member_snapshot_ydoc_defaults(webspace_id=webspace_id, node_id=node_id)
            except Exception:
                _log.debug(
                    "failed to seed member snapshot defaults webspace=%s node_id=%s",
                    webspace_id,
                    node_id,
                    exc_info=True,
                )
            _log.info(
                "starting member snapshot rebuild webspace=%s node_id=%s request_id=%s reason=%s requested_total=%s scheduled_total=%s",
                webspace_id,
                node_id,
                effective_request_id,
                str(stats.get("last_reason") or reason or "").strip() or "subnet.member.snapshot.changed",
                int(stats.get("requested_total") or 0),
                int(stats.get("scheduled_total") or 0),
            )
            result = await rebuild_webspace_from_sources(
                webspace_id,
                action="subnet_member_snapshot_sync",
                source_of_truth="member_runtime_snapshot",
                request_id=effective_request_id,
            )
            stats["completed_total"] = int(stats.get("completed_total") or 0) + 1
            stats["last_completed_at"] = time.time()
            stats["last_completed_request_id"] = effective_request_id
            dirty = _MEMBER_SNAPSHOT_REBUILD_DIRTY.get(task_key) or {}
            _log.info(
                "completed member snapshot rebuild webspace=%s node_id=%s request_id=%s accepted=%s error=%s requested_total=%s scheduled_total=%s rerun_total=%s coalesced_running_total=%s coalesced_interval_total=%s delayed_total=%s dirty_pending=%s",
                webspace_id,
                node_id,
                effective_request_id,
                bool(result.get("accepted")),
                str(result.get("error") or "").strip() or None,
                int(stats.get("requested_total") or 0),
                int(stats.get("scheduled_total") or 0),
                int(stats.get("rerun_total") or 0),
                int(stats.get("coalesced_running_total") or 0),
                int(stats.get("coalesced_interval_total") or 0),
                int(stats.get("delayed_total") or 0),
                int(dirty.get("count") or 0),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.warning(
                "member snapshot rebuild failed webspace=%s node_id=%s",
                webspace_id,
                node_id,
                exc_info=True,
            )
        finally:
            current = _MEMBER_SNAPSHOT_REBUILD_TASKS.get(task_key)
            if current is task:
                _MEMBER_SNAPSHOT_REBUILD_TASKS.pop(task_key, None)
            if str(stats.get("current_request_id") or "").strip() == effective_request_id:
                stats["current_request_id"] = ""
            dirty = _MEMBER_SNAPSHOT_REBUILD_DIRTY.pop(task_key, None)
            if dirty:
                stats["rerun_total"] = int(stats.get("rerun_total") or 0) + 1
                _MEMBER_SNAPSHOT_REBUILD_AT[task_key] = time.monotonic()
                rerun_reason = str(dirty.get("last_reason") or reason or "").strip() or "subnet.member.snapshot.changed"
                _schedule_member_snapshot_rebuild(
                    webspace_id=webspace_id,
                    node_id=node_id,
                    reason=f"{rerun_reason}:coalesced",
                    request_id=str(dirty.get("last_request_id") or "").strip() or None,
                )

    task = asyncio.create_task(
        _runner(),
        name=f"member-snapshot-rebuild:{webspace_id}:{node_id}",
    )
    _MEMBER_SNAPSHOT_REBUILD_TASKS[task_key] = task


async def _seed_member_snapshot_ydoc_defaults(*, webspace_id: str, node_id: str) -> None:
    try:
        from adaos.services.registry.subnet_directory import get_directory

        node = get_directory().get_node(node_id) or {}
    except Exception:
        return
    runtime_projection = node.get("runtime_projection") if isinstance(node.get("runtime_projection"), Mapping) else {}
    snapshot = runtime_projection.get("snapshot") if isinstance(runtime_projection.get("snapshot"), Mapping) else {}
    desktop_catalog = snapshot.get("desktop_catalog") if isinstance(snapshot.get("desktop_catalog"), Mapping) else {}
    defaults = desktop_catalog.get("ydoc_defaults") if isinstance(desktop_catalog.get("ydoc_defaults"), Mapping) else {}
    if not defaults:
        return

    async with _webspace_runtime_async_write_meta(root_names=["data"], source="member_snapshot.seed_defaults"):
        async with async_get_ydoc(webspace_id, prefer_live_room=True) as ydoc:
            with ydoc.begin_transaction() as txn:
                for raw_path, raw_value in defaults.items():
                    path = str(raw_path or "").strip()
                    if not path:
                        continue
                    segments = [segment for segment in path.split("/") if segment]
                    if len(segments) < 2:
                        continue
                    root_name = segments[0]
                    root = ydoc.get_map(root_name)
                    value = _clone_json_like(raw_value)
                    if len(segments) == 2:
                        key = segments[1]
                        current = root.get(key)
                        if _json_like_equal(current, value):
                            continue
                        root.set(txn, key, value)
                        continue
                    top_key = segments[1]
                    current_top = root.get(top_key)
                    changed, merged = _merge_nested_json_path(current_top, segments[2:], value)
                    if changed:
                        root.set(txn, top_key, merged)


@subscribe("desktop.webspace.create")
async def _on_webspace_create(evt: Dict[str, Any]) -> None:
    payload = _payload(evt)
    _log.debug("desktop.webspace.create payload=%s", payload)
    requested = payload.get("id") or payload.get("webspace_id")
    title = payload.get("title")
    scenario_id = str(payload.get("scenario_id") or "web_desktop")
    scenario_ref = payload.get("scenario_ref") if isinstance(payload.get("scenario_ref"), Mapping) else None
    dev = bool(payload.get("dev"))
    svc = WebspaceService(get_ctx())
    await svc.create(
        str(requested) if requested is not None else None,
        str(title) if title is not None else None,
        scenario_id=scenario_id,
        scenario_ref=scenario_ref,
        dev=dev,
    )


@subscribe("desktop.webspace.rename")
async def _on_webspace_rename(evt: Dict[str, Any]) -> None:
    payload = _payload(evt)
    webspace_id = str(payload.get("id") or "")
    title = str(payload.get("title") or "").strip()
    if not webspace_id or not title:
        return
    svc = WebspaceService(get_ctx())
    await svc.rename(webspace_id, title)


@subscribe("desktop.webspace.update")
async def _on_webspace_update(evt: Dict[str, Any]) -> None:
    payload = _payload(evt)
    webspace_id = str(payload.get("id") or payload.get("webspace_id") or "").strip()
    if not webspace_id:
        return
    title = str(payload.get("title") or "").strip() or None
    home_scenario = str(payload.get("home_scenario") or payload.get("scenario_id") or "").strip() or None
    home_scenario_ref = (
        payload.get("home_scenario_ref")
        if "home_scenario_ref" in payload
        else _HOME_SCENARIO_REF_UNSET
    )
    svc = WebspaceService(get_ctx())
    await svc.update_metadata(
        webspace_id,
        title=title,
        home_scenario=home_scenario,
        home_scenario_ref=home_scenario_ref,
    )


@subscribe("desktop.webspace.delete")
async def _on_webspace_delete(evt: Dict[str, Any]) -> None:
    payload = _payload(evt)
    webspace_id = str(payload.get("id") or "")
    svc = WebspaceService(get_ctx())
    await svc.delete(webspace_id)


@subscribe("desktop.webspace.refresh")
async def _on_webspace_refresh(evt: Dict[str, Any]) -> None:  # noqa: ARG001
    svc = WebspaceService(get_ctx())
    await svc.refresh()


async def rebuild_webspace_from_sources(
    webspace_id: str,
    *,
    action: str = "rebuild",
    scenario_id: str | None = None,
    scenario_resolution: str | None = None,
    source_of_truth: str = "current_runtime",
    reseed_from_scenario: bool = False,
    event_payload: dict[str, Any] | None = None,
    request_id: str | None = None,
    switch_mode: str | None = None,
    switch_timings_ms: Mapping[str, Any] | None = None,
    materialization_identity: Mapping[str, Any] | None = None,
    scenario_content_override: Mapping[str, Any] | None = None,
    skill_source_mode: str | None = None,
) -> dict[str, Any]:
    """
    Single semantic rebuild primitive for the current runtime.

    Phase 3 keeps the existing storage and frontend contracts intact, but
    routes reload/reset/restore-style operations through one backend-owned
    materialization step so reconcile behaviour is explicit.
    """
    webspace_id = str(webspace_id or "").strip()
    if not webspace_id:
        raise ValueError("webspace_id is required")

    rebuild_started = time.perf_counter()
    timings_ms: Dict[str, float] = {}
    requested_action = str(action or "").strip().lower() or "rebuild"
    target_scenario = str(scenario_id or "").strip() or None
    resolved_scenario_resolution = str(scenario_resolution or "").strip() or None
    status_started_at = time.time()
    if not target_scenario or not resolved_scenario_resolution:
        stage_started = time.perf_counter()
        _state, resolved_target_scenario, resolved_target_resolution = await _resolve_rebuild_scenario_target(
            webspace_id,
            target_scenario,
            prefer_manifest_home_before_current=requested_action in {"reload", "reset"},
        )
        if not target_scenario:
            target_scenario = resolved_target_scenario
        if not resolved_scenario_resolution:
            resolved_scenario_resolution = resolved_target_resolution
        _record_timing(timings_ms, "resolve_rebuild_target", stage_started)

    previous_status = describe_webspace_rebuild_state(webspace_id)
    effective_switch_timings = _copy_timing_map(switch_timings_ms) or _copy_timing_map(previous_status.get("switch_timings_ms"))
    effective_switch_mode = str(switch_mode or previous_status.get("switch_mode") or "").strip() or None
    if requested_action == "scenario_switch_rebuild":
        effective_switch_mode = "pointer_only"
    effective_materialization_identity = dict(materialization_identity) if isinstance(materialization_identity, Mapping) else None
    if effective_materialization_identity is None and requested_action == "scenario_switch_rebuild" and target_scenario:
        stage_started = time.perf_counter()
        try:
            source_mode_for_identity = _resolve_projection_refresh_space(webspace_id)
            effective_materialization_identity = _scenario_switch_materialization_identity(
                webspace_id=webspace_id,
                scenario_id=target_scenario,
                source_mode=source_mode_for_identity,
            )
        except Exception:
            effective_materialization_identity = None
            _log.debug(
                "failed to build scenario switch materialization identity webspace=%s scenario=%s",
                webspace_id,
                target_scenario,
                exc_info=True,
            )
        _record_timing(timings_ms, "resolve_materialization_identity", stage_started)
    running_materialization = _pending_materialization_snapshot(
        webspace_id,
        scenario_id=target_scenario,
        snapshot_source="rebuild:running",
        rebuild_state=previous_status,
    )
    _set_webspace_rebuild_status(
        webspace_id,
        status="running",
        pending=True,
        background=bool(previous_status.get("background")),
        request_id=request_id,
        action=requested_action,
        source_of_truth=source_of_truth,
        scenario_id=target_scenario,
        scenario_resolution=resolved_scenario_resolution,
        switch_mode=effective_switch_mode,
        requested_at=previous_status.get("requested_at") or status_started_at,
        started_at=status_started_at,
        finished_at=None,
        error=None,
        projection_refresh=None,
        registry_summary=None,
        resolver=None,
        apply_summary=None,
        timings_ms=None,
        switch_timings_ms=effective_switch_timings,
        semantic_rebuild_timings_ms=None,
        phase_timings_ms=None,
        materialization=running_materialization,
    )

    reset_room_result: dict[str, Any] | None = None
    ystore_reset = False
    fresh_doc_rebuild = False
    scenario_switch_payload_rebuild = requested_action == "scenario_switch_rebuild"

    async def _write_reseed_pointer() -> None:
        try:
            async with _webspace_runtime_async_write_meta(
                root_names=["ui"],
                source="webspace_runtime.reseed_pointer",
            ):
                async with async_get_ydoc(webspace_id) as ydoc:
                    ui_map = ydoc.get_map("ui")
                    with ydoc.begin_transaction() as txn:
                        ui_map.set(txn, "current_scenario", target_scenario)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            _log.warning(
                "failed to write reseed current_scenario pointer webspace=%s scenario=%s",
                webspace_id,
                target_scenario,
                exc_info=True,
            )

    def _note_authoritative_selector(reason: str) -> None:
        if not target_scenario:
            return
        try:
            from adaos.services.yjs.gateway import note_authoritative_current_scenario  # pylint: disable=import-outside-toplevel

            note_authoritative_current_scenario(
                webspace_id,
                target_scenario,
                reason=reason,
            )
        except Exception:
            _log.debug(
                "failed to publish authoritative current_scenario lease webspace=%s scenario=%s",
                webspace_id,
                target_scenario,
                exc_info=True,
            )

    if scenario_switch_payload_rebuild:
        if not target_scenario:
            raise ValueError("scenario_id is required for scenario switch rebuild")

        timings_ms["scenario_switch_transport_preserved"] = 0.0

        if _scenario_switch_inline_listing_sync_enabled():
            stage_started = time.perf_counter()
            await _sync_webspace_listing_target(webspace_id)
            _record_timing(timings_ms, "scenario_switch_sync_listing", stage_started)
        else:
            timings_ms["scenario_switch_sync_listing_deferred"] = 0.0

    should_invalidate_loader_cache = bool(
        target_scenario
        and (
            reseed_from_scenario
            or requested_action == "builder_revision_apply"
            or str(source_of_truth or "").strip().lower() == "builder_revision"
        )
    )
    if should_invalidate_loader_cache:
        stage_started = time.perf_counter()
        try:
            scenarios_loader.invalidate_cache(scenario_id=target_scenario, space="workspace")
            scenarios_loader.invalidate_cache(scenario_id=target_scenario, space="dev")
        except Exception:
            pass
        _record_timing(timings_ms, "invalidate_loader_cache", stage_started)
        stage_started = time.perf_counter()
        _invalidate_resolved_webspace_cache(
            scenario_id=target_scenario,
            reason=requested_action,
        )
        _record_timing(timings_ms, "invalidate_resolver_cache", stage_started)

    if reseed_from_scenario:
        if not target_scenario:
            raise ValueError("scenario_id is required when reseed_from_scenario is enabled")
        _note_authoritative_selector(f"{requested_action}:reseed")
        if requested_action != "reset":
            stage_started = time.perf_counter()
            await _write_reseed_pointer()
            _record_timing(timings_ms, "reseed_pointer", stage_started)

        if requested_action == "reset":
            stage_started = time.perf_counter()
            try:
                from adaos.services.yjs.gateway import reset_live_webspace_room  # pylint: disable=import-outside-toplevel
                from adaos.services.yjs.store import reset_ystore_for_webspace  # pylint: disable=import-outside-toplevel

                try:
                    reset_room_result = await reset_live_webspace_room(
                        webspace_id,
                        close_reason="webspace_reset",
                        persist_ystore_snapshot=False,
                    )
                except Exception:
                    pass
                try:
                    reset_ystore_for_webspace(webspace_id)
                    ystore_reset = True
                except Exception:
                    pass
            except Exception:
                _log.warning("failed to reset ystore for webspace=%s", webspace_id, exc_info=True)
            _record_timing(timings_ms, "reset_runtime_state", stage_started)

            stage_started = time.perf_counter()
            await _seed_webspace_from_scenario_with_options(
                webspace_id,
                target_scenario,
            )
            _record_timing(timings_ms, "seed_from_scenario", stage_started)

            stage_started = time.perf_counter()
            await _write_reseed_pointer()
            _record_timing(timings_ms, "reseed_pointer_after_reset", stage_started)
        else:
            stage_started = time.perf_counter()
            await _project_webspace_from_scenario(
                webspace_id,
                target_scenario,
                emit_event=False,
            )
            _record_timing(timings_ms, "project_scenario_payload", stage_started)

        stage_started = time.perf_counter()
        await _sync_webspace_listing_target(webspace_id)
        _record_timing(timings_ms, "sync_listing", stage_started)

    ctx = get_ctx()
    stage_started = time.perf_counter()
    if requested_action == "builder_revision_apply" and not _builder_revision_projection_refresh_enabled():
        target_space = _resolve_projection_refresh_space(webspace_id)
        projection_refresh = {
            "attempted": False,
            "scenario_id": target_scenario,
            "scenario_resolution": resolved_scenario_resolution,
            "space": target_space,
            "rules_loaded": 0,
            "source": "skipped",
            "reason": "builder_revision_apply_reuses_existing_projection_rules",
        }
        _record_timing(timings_ms, "projection_refresh_skipped", stage_started)
    else:
        _log.info(
            "starting projection refresh webspace=%s action=%s scenario=%s resolution=%s",
            webspace_id,
            requested_action,
            target_scenario,
            resolved_scenario_resolution,
        )
        projection_refresh = await _refresh_projection_rules_for_rebuild(
            ctx,
            webspace_id,
            scenario_id=target_scenario,
            scenario_resolution=resolved_scenario_resolution,
        )
        _record_timing(timings_ms, "projection_refresh", stage_started)
        _log.info(
            "finished projection refresh webspace=%s action=%s scenario=%s result=%s elapsed_ms=%.3f",
            webspace_id,
            requested_action,
            target_scenario,
            json.dumps(_clone_json_like(projection_refresh), ensure_ascii=True, sort_keys=True)[:1000],
            float(timings_ms.get("projection_refresh") or 0.0),
        )
    runtime = WebspaceScenarioRuntime(ctx)
    live_room_update_requested = _publish_live_room_for_rebuild(requested_action)
    prefer_live_room = (
        _builder_revision_rebuild_prefers_live_room()
        if requested_action == "builder_revision_apply"
        else bool(live_room_update_requested)
    )
    publish_live_room = bool(live_room_update_requested)
    if requested_action == "builder_revision_apply" and not prefer_live_room:
        publish_live_room = _builder_revision_detached_direct_live_room_updates_enabled()
    payload_only_rebuild = scenario_switch_payload_rebuild or bool(scenario_content_override)
    try:
        stage_started = time.perf_counter()
        rebuild_timeout_s = _semantic_rebuild_timeout_s(requested_action)
        initial_scenario_id = (
            target_scenario
            if scenario_switch_payload_rebuild or requested_action == "builder_revision_apply"
            else None
        )
        builder_fresh_doc_rebuild = (
            requested_action == "builder_revision_apply" and _builder_revision_fresh_doc_rebuild_enabled()
        )
        if builder_fresh_doc_rebuild:
            fresh_doc_rebuild = True
        rebuild_kwargs = {
            "publish_live_room": publish_live_room,
            "prefer_live_room": prefer_live_room,
            "initial_scenario_id": initial_scenario_id,
            "materialization_identity": effective_materialization_identity,
        }
        if builder_fresh_doc_rebuild:
            rebuild_kwargs["fresh_doc"] = True
            rebuild_kwargs["replace_ystore_snapshot"] = _builder_revision_replace_ystore_snapshot_enabled()
        if str(request_id or "").strip():
            rebuild_kwargs["request_id"] = request_id
        _log.info(
            "starting semantic rebuild core webspace=%s action=%s scenario=%s live_room_requested=%s publish_live_room=%s prefer_live_room=%s payload_only=%s timeout_s=%s materialization_key=%s",
            webspace_id,
            requested_action,
            target_scenario,
            bool(live_room_update_requested),
            bool(publish_live_room),
            bool(prefer_live_room),
            bool(payload_only_rebuild),
            rebuild_timeout_s,
            (
                effective_materialization_identity.get("key_hash")
                if isinstance(effective_materialization_identity, Mapping)
                else "-"
            ),
        )
        if payload_only_rebuild:
            payload_rebuild_kwargs: dict[str, Any] = {
                "scenario_id": target_scenario,
                "materialization_identity": effective_materialization_identity,
                # A scenario switch resolves plain effective branches. Keep it
                # off the event loop, but do not pay for a second runtime.
                "isolate_process": False,
            }
            if scenario_content_override:
                payload_rebuild_kwargs["scenario_content_override"] = scenario_content_override
            if str(skill_source_mode or "").strip():
                payload_rebuild_kwargs["skill_source_mode"] = str(skill_source_mode).strip()
            if str(request_id or "").strip():
                payload_rebuild_kwargs["request_id"] = request_id
            rebuild_coro = runtime.resolve_materialized_payload_async(webspace_id, **payload_rebuild_kwargs)
        else:
            rebuild_coro = runtime.rebuild_webspace_async(webspace_id, **rebuild_kwargs)
        if rebuild_timeout_s is not None:
            timeout_cm = getattr(asyncio, "timeout", None)
            if callable(timeout_cm):
                async with timeout_cm(rebuild_timeout_s):
                    entry = await rebuild_coro
            else:
                entry = await asyncio.wait_for(rebuild_coro, timeout=rebuild_timeout_s)
        else:
            entry = await rebuild_coro
        _record_timing(timings_ms, "semantic_rebuild", stage_started)
        _log.info(
            "finished semantic rebuild core webspace=%s action=%s scenario=%s live_room_requested=%s publish_live_room=%s prefer_live_room=%s payload_only=%s semantic_ms=%.3f ydoc_timings=%s semantic_timings=%s",
            webspace_id,
            requested_action,
            target_scenario,
            bool(live_room_update_requested),
            bool(publish_live_room),
            bool(prefer_live_room),
            bool(payload_only_rebuild),
            float(timings_ms.get("semantic_rebuild") or 0.0),
            _copy_timing_map(getattr(runtime, "_last_rebuild_ydoc_timings_ms", None)),
            _copy_timing_map(getattr(runtime, "_last_rebuild_timings_ms", None)),
        )
    except _StaleRebuildRequestError:
        finalized_timings = _finalize_timing_map(timings_ms, started_at=rebuild_started)
        semantic_timings = _copy_timing_map(getattr(runtime, "_last_rebuild_timings_ms", None))
        ydoc_timings = _copy_timing_map(getattr(runtime, "_last_rebuild_ydoc_timings_ms", None))
        resolver_debug = dict(getattr(runtime, "_last_resolver_debug", None) or {})
        apply_summary = dict(getattr(runtime, "_last_apply_summary", None) or {})
        phase_timings = _derive_phase_timings(
            switch_timings_ms=effective_switch_timings,
            rebuild_timings_ms=finalized_timings,
            semantic_rebuild_timings_ms=semantic_timings,
            switch_mode=effective_switch_mode,
        )
        _set_webspace_rebuild_status_if_current(
            webspace_id,
            request_id,
            status="cancelled",
            pending=False,
            finished_at=time.time(),
            error="stale_rebuild_superseded",
            switch_mode=effective_switch_mode,
            scenario_resolution=resolved_scenario_resolution,
            projection_refresh=projection_refresh,
            resolver=resolver_debug or None,
            apply_summary=apply_summary or None,
            timings_ms=finalized_timings,
            switch_timings_ms=effective_switch_timings,
            semantic_rebuild_timings_ms=semantic_timings,
            ydoc_timings_ms=ydoc_timings,
            phase_timings_ms=phase_timings,
        )
        _log.info(
            "stale semantic rebuild skipped apply webspace=%s action=%s scenario=%s request_id=%s",
            webspace_id,
            requested_action,
            target_scenario,
            request_id,
        )
        return {
            "ok": False,
            "accepted": False,
            "action": requested_action,
            "source_of_truth": source_of_truth,
            "webspace_id": webspace_id,
            "scenario_id": target_scenario,
            "scenario_resolution": resolved_scenario_resolution,
            "request_id": request_id,
            "switch_mode": effective_switch_mode,
            "projection_refresh": projection_refresh,
            "resolver": resolver_debug or None,
            "apply_summary": apply_summary or None,
            "timings_ms": finalized_timings,
            "switch_timings_ms": effective_switch_timings,
            "semantic_rebuild_timings_ms": semantic_timings,
            "ydoc_timings_ms": ydoc_timings,
            "phase_timings_ms": phase_timings,
            "error": "stale_rebuild_superseded",
        }
    except BaseException as exc:
        if _is_control_flow_base_exception(exc):
            raise
        error_token = "webspace_rebuild_timeout" if isinstance(exc, asyncio.TimeoutError) else "webspace_rebuild_failed"
        error_detail = f"{type(exc).__name__}: {exc}"[:1000]
        finalized_timings = _finalize_timing_map(timings_ms, started_at=rebuild_started)
        semantic_timings = _copy_timing_map(getattr(runtime, "_last_rebuild_timings_ms", None))
        ydoc_timings = _copy_timing_map(getattr(runtime, "_last_rebuild_ydoc_timings_ms", None))
        resolver_debug = dict(getattr(runtime, "_last_resolver_debug", None) or {})
        apply_summary = dict(getattr(runtime, "_last_apply_summary", None) or {})
        phase_timings = _derive_phase_timings(
            switch_timings_ms=effective_switch_timings,
            rebuild_timings_ms=finalized_timings,
            semantic_rebuild_timings_ms=semantic_timings,
            switch_mode=effective_switch_mode,
        )
        _set_webspace_rebuild_status_if_current(
            webspace_id,
            request_id,
            status="failed",
            pending=False,
            finished_at=time.time(),
            error=error_token,
            switch_mode=effective_switch_mode,
            scenario_resolution=resolved_scenario_resolution,
            projection_refresh=projection_refresh,
            resolver=resolver_debug or None,
            apply_summary=apply_summary or None,
            timings_ms=finalized_timings,
            switch_timings_ms=effective_switch_timings,
            semantic_rebuild_timings_ms=semantic_timings,
            ydoc_timings_ms=ydoc_timings,
            phase_timings_ms=phase_timings,
        )
        _log.warning(
            "failed to rebuild webspace from sources webspace=%s action=%s scenario=%s error=%s detail=%s timings_ms=%s semantic_timings_ms=%s",
            webspace_id,
            requested_action,
            target_scenario,
            error_token,
            error_detail,
            finalized_timings,
            semantic_timings,
            exc_info=True,
        )
        return {
            "ok": False,
            "accepted": False,
            "action": requested_action,
            "source_of_truth": source_of_truth,
            "webspace_id": webspace_id,
            "scenario_id": target_scenario,
            "scenario_resolution": resolved_scenario_resolution,
            "request_id": request_id,
            "switch_mode": effective_switch_mode,
            "projection_refresh": projection_refresh,
            "resolver": resolver_debug or None,
            "apply_summary": apply_summary or None,
            "timings_ms": finalized_timings,
            "switch_timings_ms": effective_switch_timings,
            "semantic_rebuild_timings_ms": semantic_timings,
            "ydoc_timings_ms": ydoc_timings,
            "phase_timings_ms": phase_timings,
            "error": error_token,
            "error_detail": error_detail,
        }

    semantic_timings = _copy_timing_map(getattr(runtime, "_last_rebuild_timings_ms", None))
    ydoc_timings = _copy_timing_map(getattr(runtime, "_last_rebuild_ydoc_timings_ms", None))
    resolver_debug = dict(getattr(runtime, "_last_resolver_debug", None) or {})
    apply_summary = dict(getattr(runtime, "_last_apply_summary", None) or {})
    worker_diagnostics = dict(getattr(runtime, "_last_worker_diagnostics", None) or {})
    raw_materialized_payload = getattr(runtime, "_last_materialized_payload", None)
    materialized_payload = (
        dict(raw_materialized_payload)
        if isinstance(raw_materialized_payload, Mapping)
        else None
    )
    live_room_refresh_result: dict[str, Any] | None = None

    should_refresh_live_room = (
        not publish_live_room
        and (
            scenario_switch_payload_rebuild
            or _refresh_live_room_after_rebuild_enabled()
        )
        and _rebuild_action_refreshes_live_room(requested_action)
    )
    force_full_state_update = bool(
        fresh_doc_rebuild
        and (
            ystore_reset
            or (requested_action == "builder_revision_apply" and payload_only_rebuild)
        )
    )
    if should_refresh_live_room:
        if _defer_live_room_refresh_for_rebuild(requested_action):
            persist_repair = not (
                requested_action == "builder_revision_apply"
                and builder_fresh_doc_rebuild
                and _builder_revision_replace_ystore_snapshot_enabled()
                and not payload_only_rebuild
            )
            deferred_refresh_kwargs: dict[str, Any] = {
                "persist_repair": persist_repair,
                "force_full_state_update": bool(force_full_state_update and persist_repair),
            }
            if materialized_payload:
                deferred_refresh_kwargs["materialized_payload"] = materialized_payload
                deferred_refresh_kwargs["materialization_identity"] = effective_materialization_identity
            live_room_refresh_result = _schedule_live_room_refresh(
                webspace_id=webspace_id,
                reason=f"semantic_rebuild:{requested_action}",
                **deferred_refresh_kwargs,
            )
            timings_ms["live_room_refresh_deferred"] = 0.0
        else:
            stage_started = time.perf_counter()
            try:
                _log.info(
                    "starting live-room refresh after semantic rebuild webspace=%s action=%s",
                    webspace_id,
                    requested_action,
                )
                if _rebuild_action_applies_live_payload(requested_action):
                    persist_repair = not (
                        requested_action == "builder_revision_apply"
                        and builder_fresh_doc_rebuild
                        and _builder_revision_replace_ystore_snapshot_enabled()
                        and not payload_only_rebuild
                    )
                    refresh_kwargs: dict[str, Any] = {
                        "reason": f"semantic_rebuild:{requested_action}",
                        "persist_repair": persist_repair,
                    }
                    if materialized_payload:
                        from adaos.services.yjs.gateway import apply_materialized_payload_to_live_room  # pylint: disable=import-outside-toplevel

                        refresh_kwargs["force_full_state_update"] = bool(
                            force_full_state_update and persist_repair
                        )
                        live_room_refresh_result = await apply_materialized_payload_to_live_room(
                            webspace_id,
                            materialized_payload=materialized_payload,
                            **refresh_kwargs,
                            materialization_identity=effective_materialization_identity,
                        )
                    else:
                        from adaos.services.yjs.gateway import reconcile_live_webspace_effective_branches  # pylint: disable=import-outside-toplevel

                        live_room_refresh_result = await reconcile_live_webspace_effective_branches(
                            webspace_id,
                            **refresh_kwargs,
                        )
                else:
                    from adaos.services.yjs.gateway import reset_live_webspace_room  # pylint: disable=import-outside-toplevel

                    live_room_refresh_result = await reset_live_webspace_room(
                        webspace_id,
                        close_reason=f"semantic_rebuild:{requested_action}",
                    )
                if not isinstance(live_room_refresh_result, Mapping):
                    live_room_refresh_result = {
                        "ok": live_room_refresh_result is not None,
                        "warning": "live_room_refresh_returned_non_mapping",
                        "result_type": type(live_room_refresh_result).__name__,
                    }
                _log.info(
                    "finished live-room refresh after semantic rebuild webspace=%s action=%s summary=%s",
                    webspace_id,
                    requested_action,
                    json.dumps(
                        _compact_live_room_refresh_result_for_log(live_room_refresh_result),
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                )
            except BaseException as exc:
                if _is_control_flow_base_exception(exc):
                    raise
                live_room_refresh_result = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                _log.warning(
                    "failed to refresh live YRoom after detached semantic rebuild webspace=%s action=%s",
                    webspace_id,
                    requested_action,
                    exc_info=True,
                )
            _record_timing(timings_ms, "live_room_refresh", stage_started)

    if isinstance(live_room_refresh_result, Mapping):
        refresh_payload = live_room_refresh_result.get("materialized_payload")
        refresh_apply_summary = (
            refresh_payload.get("apply_summary")
            if isinstance(refresh_payload, Mapping) and isinstance(refresh_payload.get("apply_summary"), Mapping)
            else None
        )
        if isinstance(refresh_apply_summary, Mapping):
            apply_summary = dict(refresh_apply_summary)

    if not target_scenario or not resolved_scenario_resolution:
        stage_started = time.perf_counter()
        try:
            state_after, resolved_target_scenario, resolved_target_resolution = await _resolve_rebuild_scenario_target(
                webspace_id,
                target_scenario,
                prefer_manifest_home_before_current=requested_action in {"reload", "reset"},
            )
            if not target_scenario:
                target_scenario = resolved_target_scenario
            if not resolved_scenario_resolution:
                resolved_scenario_resolution = resolved_target_resolution
        except Exception:
            target_scenario = target_scenario or None
            resolved_scenario_resolution = resolved_scenario_resolution or None
        _record_timing(timings_ms, "resolve_active_scenario", stage_started)

    workflow_sync_action = requested_action in {"scenario_switch_rebuild", "restore", "reload", "reset"}
    should_sync_workflow = _workflow_sync_for_rebuild_enabled(requested_action)
    workflow_sync_result: dict[str, Any] | None = None
    if target_scenario and should_sync_workflow:
        if _defer_workflow_sync_for_rebuild(requested_action):
            workflow_sync_result = _schedule_workflow_sync(
                ctx,
                webspace_id=webspace_id,
                scenario_id=target_scenario,
                reason=f"semantic_rebuild:{requested_action}",
            )
            timings_ms["workflow_sync_deferred"] = 0.0
        else:
            stage_started = time.perf_counter()
            try:
                wf = ScenarioWorkflowRuntime(ctx)
                await wf.sync_workflow_for_webspace(target_scenario, webspace_id)
                workflow_sync_result = {
                    "scheduled": False,
                    "deferred": False,
                    "scenario_id": target_scenario,
                }
            except BaseException as exc:
                if _is_control_flow_base_exception(exc):
                    raise
                workflow_sync_result = {
                    "scheduled": False,
                    "deferred": False,
                    "error": f"workflow_sync_failed:{type(exc).__name__}",
                    "scenario_id": target_scenario,
                }
                _log.warning(
                    "failed to sync workflow during semantic rebuild webspace=%s scenario=%s action=%s",
                    webspace_id,
                    target_scenario,
                    requested_action,
                    exc_info=True,
                )
            _record_timing(timings_ms, "workflow_sync", stage_started)
    elif workflow_sync_action and target_scenario:
        workflow_sync_result = {
            "scheduled": False,
            "deferred": False,
            "skipped": True,
            "reason": "workflow_sync_disabled_for_scenario_switch"
            if requested_action == "scenario_switch_rebuild"
            else "workflow_sync_disabled",
            "scenario_id": target_scenario,
        }
        timings_ms["workflow_sync_skipped"] = 0.0
    elif workflow_sync_action:
        workflow_sync_result = {
            "scheduled": False,
            "deferred": False,
            "skipped": True,
            "reason": "scenario_unresolved",
        }

    event_topic = None
    if requested_action in {"reload", "reset"}:
        event_topic = "desktop.webspace.reloaded"
    elif requested_action == "restore":
        event_topic = "desktop.webspace.restored"
    if event_topic:
        stage_started = time.perf_counter()
        try:
            payload: dict[str, Any] = {
                "webspace_id": webspace_id,
                "action": requested_action,
            }
            if target_scenario:
                payload["scenario_id"] = target_scenario
            if isinstance(event_payload, dict):
                payload.update(event_payload)
            payload["webspace_id"] = webspace_id
            payload["action"] = requested_action
            if target_scenario:
                payload["scenario_id"] = target_scenario
            payload["_event_type"] = event_topic
            payload.pop("recreate_room", None)
            emit(ctx.bus, event_topic, payload, "scenario.webspace_runtime")
        except Exception:
            _log.debug("failed to emit %s for webspace=%s", event_topic, webspace_id, exc_info=True)
        _record_timing(timings_ms, "event_emit", stage_started)

    finalized_timings = _finalize_timing_map(timings_ms, started_at=rebuild_started)
    phase_timings = _derive_phase_timings(
        switch_timings_ms=effective_switch_timings,
        rebuild_timings_ms=finalized_timings,
        semantic_rebuild_timings_ms=semantic_timings,
        switch_mode=effective_switch_mode,
    )
    final_rebuild_state = describe_webspace_rebuild_state(webspace_id)
    final_materialization = _copy_materialization_snapshot(
        final_rebuild_state.get("materialization") if isinstance(final_rebuild_state, Mapping) else None
    )
    result = {
        "ok": True,
        "accepted": True,
        "action": requested_action,
        "source_of_truth": source_of_truth,
        "webspace_id": webspace_id,
        "scenario_id": target_scenario,
        "scenario_resolution": resolved_scenario_resolution,
        "request_id": request_id,
        "switch_mode": effective_switch_mode,
        "projection_refresh": projection_refresh,
        "registry_summary": {
            "scenario_id": str(getattr(entry, "scenario_id", target_scenario) or ""),
            "apps": len(getattr(entry, "apps", []) or []),
            "widgets": len(getattr(entry, "widgets", []) or []),
        },
        "resolver": resolver_debug or None,
        "apply_summary": apply_summary or None,
        "timings_ms": finalized_timings,
        "switch_timings_ms": effective_switch_timings,
        "semantic_rebuild_timings_ms": semantic_timings,
        "ydoc_timings_ms": ydoc_timings,
        "materialization_worker": worker_diagnostics or None,
        "phase_timings_ms": phase_timings,
        "materialization": final_materialization,
        "materialization_identity": effective_materialization_identity,
        "live_room_update_requested": bool(live_room_update_requested),
        "live_room_publish": bool(publish_live_room),
        "live_room_refresh": live_room_refresh_result,
        "workflow_sync": workflow_sync_result,
        "fresh_doc_rebuild": bool(fresh_doc_rebuild),
        "atomic_payload_rebuild": bool(scenario_switch_payload_rebuild),
        "force_full_state_update": bool(force_full_state_update),
        "payload_only_rebuild": bool(payload_only_rebuild),
    }
    if requested_action == "reset" or reset_room_result is not None:
        result["reset_room"] = reset_room_result or {
            "webspace_id": webspace_id,
            "room_dropped": False,
        }
        result["ystore_reset"] = bool(ystore_reset)
    _set_webspace_rebuild_status_if_current(
        webspace_id,
        request_id,
        status="ready",
        pending=False,
        finished_at=time.time(),
        error=None,
        switch_mode=effective_switch_mode,
        scenario_id=target_scenario,
        scenario_resolution=resolved_scenario_resolution,
        projection_refresh=projection_refresh,
        registry_summary=result.get("registry_summary"),
        resolver=resolver_debug or None,
        apply_summary=apply_summary or None,
        timings_ms=finalized_timings,
        switch_timings_ms=effective_switch_timings,
        semantic_rebuild_timings_ms=semantic_timings,
        ydoc_timings_ms=ydoc_timings,
        phase_timings_ms=phase_timings,
        materialization=final_materialization,
        materialized_payload=materialized_payload,
        live_room_update_requested=bool(live_room_update_requested),
        live_room_publish=bool(publish_live_room),
        live_room_refresh=live_room_refresh_result,
    )
    _log.info(
        "semantic rebuild completed webspace=%s action=%s scenario=%s timings_ms=%s semantic_timings_ms=%s",
        webspace_id,
        requested_action,
        target_scenario,
        finalized_timings,
        semantic_timings,
    )
    return result


async def _complete_scenario_switch_rebuild(
    webspace_id: str,
    *,
    scenario_id: str,
    scenario_resolution: str | None,
    request_id: str | None = None,
    switch_mode: str | None = None,
    switch_timings_ms: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return await rebuild_webspace_from_sources(
        webspace_id,
        action="scenario_switch_rebuild",
        scenario_id=scenario_id,
        scenario_resolution=scenario_resolution,
        source_of_truth="scenario_switch",
        reseed_from_scenario=False,
        request_id=request_id,
        switch_mode=switch_mode,
        switch_timings_ms=switch_timings_ms,
    )


def _schedule_scenario_switch_rebuild(
    webspace_id: str,
    *,
    scenario_id: str,
    scenario_resolution: str | None,
    switch_mode: str | None = None,
    switch_timings_ms: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    request_source: str | None = None,
    request_client: str | None = None,
) -> None:
    switch_mode = "pointer_only"
    request_id = str(request_id or "").strip() or secrets.token_hex(8)
    initial_phase_timings = _derive_phase_timings(
        switch_timings_ms=switch_timings_ms,
        rebuild_timings_ms=None,
        switch_mode=switch_mode,
    )
    initial_materialization = _pending_materialization_snapshot(
        webspace_id,
        scenario_id=scenario_id,
        snapshot_source="rebuild:scheduled",
    )
    _set_webspace_rebuild_status(
        webspace_id,
        status="scheduled",
        pending=True,
        background=True,
        request_id=request_id,
        request_source=str(request_source or "").strip() or None,
        request_client=str(request_client or "").strip() or None,
        action="scenario_switch_rebuild",
        source_of_truth="scenario_switch",
        scenario_id=scenario_id,
        scenario_resolution=scenario_resolution,
        switch_mode=str(switch_mode or "") or None,
        requested_at=time.time(),
        started_at=None,
        finished_at=None,
        error=None,
        projection_refresh=None,
        registry_summary=None,
        resolver=None,
        apply_summary=None,
        timings_ms=None,
        switch_timings_ms=_copy_timing_map(switch_timings_ms),
        semantic_rebuild_timings_ms=None,
        phase_timings_ms=initial_phase_timings,
        materialization=initial_materialization,
    )
    existing = _SCENARIO_SWITCH_REBUILD_TASKS.get(webspace_id)
    if existing and not existing.done():
        existing.cancel()

    async def _runner() -> None:
        try:
            route_yield_s = _scenario_switch_background_route_yield_s()
            if route_yield_s > 0:
                await asyncio.sleep(route_yield_s)
            _set_webspace_rebuild_status_if_current(
                webspace_id,
                request_id,
                status="running",
                pending=True,
                background=True,
                switch_mode=str(switch_mode or "") or None,
                started_at=time.time(),
                finished_at=None,
                error=None,
                projection_refresh=None,
                registry_summary=None,
                resolver=None,
                apply_summary=None,
                timings_ms=None,
                semantic_rebuild_timings_ms=None,
                materialization=_pending_materialization_snapshot(
                    webspace_id,
                    scenario_id=scenario_id,
                    snapshot_source="rebuild:running",
                ),
            )
            result = await _complete_scenario_switch_rebuild(
                webspace_id,
                scenario_id=scenario_id,
                scenario_resolution=scenario_resolution,
                request_id=request_id,
                switch_mode=switch_mode,
                switch_timings_ms=None,
            )
            if not bool(result.get("accepted")):
                if str(result.get("error") or "").strip() == "stale_rebuild_superseded":
                    return
                _set_webspace_rebuild_status_if_current(
                    webspace_id,
                    request_id,
                    status="failed",
                    pending=False,
                    background=True,
                    finished_at=time.time(),
                    error=str(result.get("error") or "scenario_switch_rebuild_failed"),
                    switch_mode=str(switch_mode or "") or None,
                    projection_refresh=result.get("projection_refresh"),
                    resolver=result.get("resolver"),
                    apply_summary=result.get("apply_summary"),
                    timings_ms=_copy_timing_map(result.get("timings_ms")),
                    switch_timings_ms=_copy_timing_map(result.get("switch_timings_ms") or switch_timings_ms),
                    semantic_rebuild_timings_ms=_copy_timing_map(result.get("semantic_rebuild_timings_ms")),
                    phase_timings_ms=_copy_timing_map(result.get("phase_timings_ms")),
                )
                _log.warning(
                    "background scenario switch rebuild rejected webspace=%s scenario=%s error=%s",
                    webspace_id,
                    scenario_id,
                    result.get("error"),
                )
        except asyncio.CancelledError:
            _set_webspace_rebuild_status_if_current(
                webspace_id,
                request_id,
                status="cancelled",
                pending=False,
                background=True,
                finished_at=time.time(),
                error="cancelled",
            )
            raise
        except BaseException as exc:
            if _is_control_flow_base_exception(exc):
                raise
            _set_webspace_rebuild_status_if_current(
                webspace_id,
                request_id,
                status="failed",
                pending=False,
                background=True,
                finished_at=time.time(),
                error=f"background_scenario_switch_rebuild_failed:{type(exc).__name__}",
            )
            _log.warning(
                "background scenario switch rebuild failed webspace=%s scenario=%s",
                webspace_id,
                scenario_id,
                exc_info=True,
            )
        finally:
            current = _SCENARIO_SWITCH_REBUILD_TASKS.get(webspace_id)
            if current is task:
                _SCENARIO_SWITCH_REBUILD_TASKS.pop(webspace_id, None)

    task = asyncio.create_task(
        _runner(),
        name=f"webspace-scenario-switch:{webspace_id}:{scenario_id}",
    )
    _SCENARIO_SWITCH_REBUILD_TASKS[webspace_id] = task


async def reload_webspace_from_scenario(
    webspace_id: str,
    *,
    scenario_id: str | None = None,
    action: str = "reload",
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Re-seed a single webspace from its current or explicit scenario source and
    rebuild its effective UI/runtime projection.

    This is the explicit operator-facing sync recovery path used by event
    handlers as well as local control API endpoints.
    """
    webspace_id = str(webspace_id or "").strip()
    if not webspace_id:
        raise ValueError("webspace_id is required")

    requested_action = "reset" if str(action or "").strip().lower() == "reset" else "reload"
    state, scenario_id, scenario_resolution = await _resolve_reload_scenario_target(webspace_id, scenario_id)
    scenario_id, scenario_resolution, preflight = _preflight_validated_scenario(
        scenario_id,
        source_mode=state.source_mode,
        resolution=scenario_resolution,
    )
    if not scenario_id:
        return {
            "ok": False,
            "accepted": False,
            "action": requested_action,
            "webspace_id": webspace_id,
            "scenario_id": None,
            "scenario_resolution": scenario_resolution,
            "kind": state.kind,
            "source_mode": state.source_mode,
            "home_scenario": state.effective_home_scenario,
            "current_scenario_before": state.current_scenario,
            "validation": preflight,
            "error": "scenario_not_found",
        }

    command_trace = _payload_command_trace(event_payload or {})
    recovery_fingerprint = _recovery_request_fingerprint(
        webspace_id=webspace_id,
        action=requested_action,
        scenario_id=scenario_id,
        command_trace=command_trace,
    )
    claimed_command, duplicate_command = _claim_recovery_command_once(
        webspace_id=webspace_id,
        action=requested_action,
        scenario_id=scenario_id,
        cmd_id=command_trace.get("cmd_id"),
        fingerprint=recovery_fingerprint,
    )
    if not claimed_command:
        duplicate_total = int(describe_webspace_rebuild_state(webspace_id).get("recovery_duplicate_total") or 0) + 1
        _set_webspace_rebuild_status(
            webspace_id,
            recovery_fingerprint=recovery_fingerprint,
            recovery_duplicate_total=duplicate_total,
            recovery_last_duplicate_at=time.time(),
            recovery_last_duplicate_reason="duplicate_recovery_command",
            recovery_last_duplicate_age_s=duplicate_command.get("age_s") if isinstance(duplicate_command, dict) else None,
            recovery_last_command_client=command_trace.get("gateway_client"),
            recovery_last_command_id=command_trace.get("cmd_id"),
            recovery_last_command_seq=int(command_trace.get("gateway_command_seq") or 0),
        )
        _log.warning(
            "deduplicated webspace recovery command webspace=%s action=%s scenario=%s cmd=%s seq=%s client=%s fp=%s age_s=%s ttl_s=%s dup_total=%s",
            webspace_id,
            requested_action,
            scenario_id,
            command_trace.get("cmd_id") or "-",
            command_trace.get("gateway_command_seq") or 0,
            command_trace.get("gateway_client") or "-",
            recovery_fingerprint,
            duplicate_command.get("age_s") if isinstance(duplicate_command, dict) else "-",
            duplicate_command.get("ttl_s") if isinstance(duplicate_command, dict) else "-",
            duplicate_total,
        )
        return {
            "ok": True,
            "accepted": True,
            "deduplicated": True,
            "skip_reason": "duplicate_recovery_command",
            "action": requested_action,
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
            "kind": state.kind,
            "source_mode": state.source_mode,
            "home_scenario": state.effective_home_scenario,
            "current_scenario_before": state.current_scenario,
            "recovery_fingerprint": recovery_fingerprint,
            "recovery_duplicate_total": duplicate_total,
            "duplicate_age_s": duplicate_command.get("age_s") if isinstance(duplicate_command, dict) else None,
            "rebuild": describe_webspace_rebuild_state(webspace_id),
        }

    rebuild_state_before = describe_webspace_rebuild_state(webspace_id)
    duplicate_window_s = _reload_dedupe_window_s()
    previous_action = str(rebuild_state_before.get("action") or "").strip().lower()
    previous_scenario = str(rebuild_state_before.get("scenario_id") or "").strip() or None
    previous_fingerprint = str(rebuild_state_before.get("recovery_fingerprint") or "").strip()
    previous_pending = bool(rebuild_state_before.get("pending"))
    previous_status = str(rebuild_state_before.get("status") or "").strip().lower()
    previous_updated_at = rebuild_state_before.get("updated_at")
    if previous_updated_at is None:
        previous_updated_at = rebuild_state_before.get("finished_at")
    if previous_updated_at is None:
        previous_updated_at = rebuild_state_before.get("started_at")
    previous_age_s: float | None = None
    try:
        if previous_updated_at is not None:
            previous_age_s = round(max(0.0, time.time() - float(previous_updated_at)), 3)
    except Exception:
        previous_age_s = None
    pending_stale_after_s = _reload_pending_stale_after_s()
    previous_pending_stale = bool(
        previous_pending
        and pending_stale_after_s > 0.0
        and previous_age_s is not None
        and previous_age_s >= pending_stale_after_s
    )

    duplicate_reason: str | None = None
    if (
        previous_action == requested_action
        and previous_scenario == scenario_id
        and previous_fingerprint
        and previous_fingerprint == recovery_fingerprint
    ):
        if previous_pending and not previous_pending_stale:
            duplicate_reason = "already_pending_recovery"
        elif (
            duplicate_window_s > 0.0
            and previous_age_s is not None
            and previous_age_s <= duplicate_window_s
            and previous_status in {"running", "ready", "scheduled"}
        ):
            duplicate_reason = "duplicate_recovery_request"

    if previous_pending_stale:
        _log.warning(
            "stale pending webspace recovery will be superseded webspace=%s action=%s scenario=%s prev_status=%s age_s=%s stale_after_s=%s fp=%s",
            webspace_id,
            requested_action,
            scenario_id,
            previous_status or "-",
            previous_age_s if previous_age_s is not None else "-",
            pending_stale_after_s,
            previous_fingerprint or "-",
        )

    if duplicate_reason:
        duplicate_total = int(rebuild_state_before.get("recovery_duplicate_total") or 0) + 1
        duplicate_now = time.time()
        _set_webspace_rebuild_status(
            webspace_id,
            recovery_fingerprint=recovery_fingerprint,
            recovery_duplicate_total=duplicate_total,
            recovery_last_duplicate_at=duplicate_now,
            recovery_last_duplicate_reason=duplicate_reason,
            recovery_last_duplicate_age_s=previous_age_s,
            recovery_last_command_client=command_trace.get("gateway_client"),
            recovery_last_command_id=command_trace.get("cmd_id"),
            recovery_last_command_seq=int(command_trace.get("gateway_command_seq") or 0),
        )
        _log.warning(
            "deduplicated webspace recovery webspace=%s action=%s scenario=%s reason=%s prev_status=%s age_s=%s cmd=%s seq=%s client=%s fp=%s dup_total=%s",
            webspace_id,
            requested_action,
            scenario_id,
            duplicate_reason,
            previous_status or "-",
            previous_age_s if previous_age_s is not None else "-",
            command_trace.get("cmd_id") or "-",
            command_trace.get("gateway_command_seq") or 0,
            command_trace.get("gateway_client") or "-",
            recovery_fingerprint,
            duplicate_total,
        )
        return {
            "ok": True,
            "accepted": True,
            "deduplicated": True,
            "skip_reason": duplicate_reason,
            "action": requested_action,
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
            "kind": state.kind,
            "source_mode": state.source_mode,
            "home_scenario": state.effective_home_scenario,
            "current_scenario_before": state.current_scenario,
            "recovery_fingerprint": recovery_fingerprint,
            "recovery_duplicate_total": duplicate_total,
            "duplicate_age_s": previous_age_s,
            "rebuild": describe_webspace_rebuild_state(webspace_id),
        }

    _set_webspace_rebuild_status(
        webspace_id,
        recovery_fingerprint=recovery_fingerprint,
        recovery_last_command_client=command_trace.get("gateway_client"),
        recovery_last_command_id=command_trace.get("cmd_id"),
        recovery_last_command_seq=int(command_trace.get("gateway_command_seq") or 0),
    )

    verb = "resetting" if requested_action == "reset" else "reloading"
    _log.info(
        "%s webspace %s from scenario %s (resolution=%s kind=%s source_mode=%s current=%s home=%s cmd=%s seq=%s client=%s device=%s trace=%s fp=%s)",
        verb,
        webspace_id,
        scenario_id,
        scenario_resolution,
        state.kind,
        state.source_mode,
        state.current_scenario,
        state.effective_home_scenario,
        command_trace.get("cmd_id") or "-",
        command_trace.get("gateway_command_seq") or 0,
        command_trace.get("gateway_client") or "-",
        command_trace.get("device_id") or "-",
        command_trace.get("trace_id") or "-",
        recovery_fingerprint,
    )

    result = await rebuild_webspace_from_sources(
        webspace_id,
        action=requested_action,
        scenario_id=scenario_id,
        scenario_resolution=scenario_resolution,
        source_of_truth="scenario",
        reseed_from_scenario=True,
        event_payload=event_payload,
    )
    result.update(
        {
            "kind": state.kind,
            "source_mode": state.source_mode,
            "home_scenario": state.effective_home_scenario,
            "current_scenario_before": state.current_scenario,
            "validation": preflight,
        }
    )
    return result


def _builder_empty_canvas_widget() -> dict[str, Any]:
    return {
        "id": "builder-empty-canvas",
        "type": "ui.form",
        "area": "main",
        "inputs": {
            "fields": [
                {
                    "id": "builder-empty-canvas-message",
                    "type": "staticContent",
                    "title": "Empty prototype canvas",
                    "content": "Describe the interface in Builder to create the first prototype revision.",
                }
            ]
        },
    }


def _ensure_builder_empty_canvas_widget(page: dict[str, Any], scenario_id: str) -> None:
    meta = page.get("meta") if isinstance(page.get("meta"), Mapping) else {}
    builder_meta = meta.get("builder") if isinstance(meta.get("builder"), Mapping) else {}
    widgets = page.get("widgets") if isinstance(page.get("widgets"), list) else []
    if not bool(builder_meta.get("empty_canvas")) or widgets:
        return
    page["id"] = scenario_id
    page["widgets"] = [_builder_empty_canvas_widget()]
    builder_meta["placeholder_injected"] = True
    meta["builder"] = builder_meta
    page["meta"] = meta


def _builder_publication_package_content(
    scenario_id: str,
    *,
    revision: str | None,
) -> dict[str, Any] | None:
    """Read an installed immutable Publication even when its slot is not active.

    Workspace files are an active materialization, while subscriptions and
    release packages are the durable source for every installed project.
    """

    from io import BytesIO
    from zipfile import BadZipFile, ZipFile

    from adaos.domain.artifact_release import ProjectRelease
    from adaos.services.artifact_pipeline.channels import ChannelError, SubscriptionStore
    from adaos.services.artifact_pipeline.packages import (
        ContentAddressedPackageStore,
        PackageVerificationError,
    )
    from adaos.services.runtime_paths import current_state_dir

    scenario_root = scenarios_loader.scenario_root_for_space(scenario_id, "workspace")
    workspace_root = scenario_root.parent.parent
    try:
        subscription = SubscriptionStore(
            workspace_root / ".adaos" / "subscriptions.json"
        ).load().get(scenario_id)
    except ChannelError as exc:
        raise ValueError("Builder publication subscription metadata is invalid") from exc
    if subscription is None or not subscription.installed_digest:
        return None
    expected_release = f"{scenario_id}@{str(revision or '').strip()}"
    if revision and subscription.installed_release != expected_release:
        return None

    release_path = (
        workspace_root
        / ".adaos"
        / "releases"
        / f"{subscription.installed_digest.split(':', 1)[-1]}.json"
    )
    if not release_path.is_file():
        return None
    try:
        release = ProjectRelease.from_mapping(
            json.loads(release_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("Builder publication release metadata is invalid") from exc
    release_digest = release.release_digest or release.computed_digest()
    if (
        release.project_id != scenario_id
        or release_digest != subscription.installed_digest
        or (revision and release.version != str(revision).strip())
    ):
        raise ValueError("Builder publication release identity does not match its subscription")
    component = next(
        (
            item
            for item in release.components
            if item.kind == "scenario" and item.artifact_id == scenario_id
        ),
        None,
    )
    if component is None:
        raise ValueError("Builder publication release has no scenario component")

    store = ContentAddressedPackageStore(
        Path(current_state_dir()) / "artifact_pipeline" / "packages"
    )
    try:
        archive, verified = store.read_verified(component.digest)
        if verified.ref != component:
            raise ValueError("published package identity differs from its release")
        with ZipFile(BytesIO(archive), "r") as bundle:
            payload = json.loads(bundle.read("webui.json").decode("utf-8-sig"))
    except (OSError, KeyError, ValueError, BadZipFile, PackageVerificationError) as exc:
        raise ValueError("Builder publication package is unavailable or invalid") from exc
    return dict(payload) if isinstance(payload, Mapping) else None


def _builder_preview_content_override(
    scenario_id: str,
    *,
    stage: str,
    revision: str | None,
    label: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    stage_token = str(stage or "").strip().lower()
    if stage_token not in {"prototype", "automation", "publication"}:
        return None, None
    source_space = "workspace" if stage_token == "publication" else "dev"
    content: Mapping[str, Any] | None = None
    revision_token = str(revision or "").strip()
    if stage_token == "prototype" and revision_token:
        if not revision_token.isdigit():
            raise ValueError(f"Builder prototype revision is unavailable: {revision}")
        root = scenarios_loader.scenario_root_for_space(scenario_id, "dev")
        revision_path = root / "ui_revisions" / f"{revision_token}.json"
        try:
            revision_payload = json.loads(revision_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Builder prototype revision is unavailable: {revision}") from exc
        content = revision_payload.get("after_webui") if isinstance(revision_payload, Mapping) else None
    elif stage_token == "automation":
        from adaos.services.runtime_paths import current_state_dir

        snapshot_path = (
            current_state_dir()
            / "builder"
            / "workflow_snapshots"
            / "scenario"
            / scenario_id
            / "automation"
            / "webui.json"
        )
        try:
            snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            # Legacy completed Automation projects predate retained snapshots.
            # Their current DEV descriptor is the only recoverable source;
            # every completion under the v1 workflow creates a real snapshot.
            snapshot_payload = None
        content = snapshot_payload if isinstance(snapshot_payload, Mapping) else None
    if not isinstance(content, Mapping):
        content = scenarios_loader.read_content(scenario_id, space=source_space)
    if stage_token == "publication" and (not isinstance(content, Mapping) or not content):
        content = _builder_publication_package_content(
            scenario_id,
            revision=revision_token or None,
        )
    if not isinstance(content, Mapping) or not content:
        raise ValueError(f"Builder {stage_token} preview source is unavailable: {scenario_id}")

    override = _clone_json_like(content)
    ui = override.get("ui") if isinstance(override.get("ui"), Mapping) else {}
    application = ui.get("application") if isinstance(ui.get("application"), Mapping) else {}
    desktop = application.get("desktop") if isinstance(application.get("desktop"), Mapping) else {}
    page = desktop.get("pageSchema") if isinstance(desktop.get("pageSchema"), Mapping) else {}
    if stage_token == "prototype" and not page:
        try:
            manifest = scenarios_loader.read_manifest(scenario_id, space=source_space)
        except Exception:
            manifest = {}
        title = str(
            manifest.get("title")
            or manifest.get("name")
            or scenario_id
        ).strip() or scenario_id
        page = {
            "id": scenario_id,
            "title": title,
            "layout": {
                "type": "single",
                "pattern": "stack",
                "areas": [{"id": "main", "role": "main"}],
            },
            "widgets": [_builder_empty_canvas_widget()],
            "meta": {"builder": {"empty_canvas": True, "compatibility_fallback": True}},
        }
        desktop["pageSchema"] = page
        application["desktop"] = desktop
        ui["application"] = application
        override["ui"] = ui
    if stage_token == "prototype" and page:
        _ensure_builder_empty_canvas_widget(page, scenario_id)
    if page:
        existing_title = str(page.get("title") or scenario_id).strip() or scenario_id
        prefix = {
            "prototype": f"proto:{str(revision or 'current').strip() or 'current'}",
            "automation": "active:",
            "publication": f"public:{str(revision or 'current').strip() or 'current'}",
        }[stage_token]
        page["title"] = str(label or f"{prefix} {existing_title}").strip()
    return dict(override), source_space


async def apply_builder_revision_materialization(
    webspace_id: str,
    *,
    scenario_id: str,
    revision: str | None = None,
    preview_stage: str | None = None,
    preview_label: str | None = None,
    source_fingerprint: str | None = None,
    user_id: str | None = None,
    roles: Any = None,
    policy_fingerprint: str | None = None,
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Apply a Builder UI revision to a paired dev webspace without using the
    recovery-grade reload path.

    This keeps the current renderer compatibility mirror intact by running the
    semantic materialization pipeline, but it deliberately avoids scenario
    reseed, scenario projection, listing sync, workflow sync, and
    ``desktop.webspace.reloaded`` emission.
    """
    webspace_id = str(webspace_id or "").strip()
    if not webspace_id:
        raise ValueError("webspace_id is required")
    requested_scenario = str(scenario_id or "").strip()
    if not requested_scenario:
        raise ValueError("scenario_id is required")

    source_webspace_id = str((event_payload or {}).get("source_webspace_id") or "").strip()
    if source_webspace_id:
        try:
            from adaos.services.builder.workbench import BuilderWorkbenchService

            binding = BuilderWorkbenchService.from_context().get_workspace_binding(source_webspace_id)
        except Exception:
            binding = {}
            _log.debug(
                "builder materialization target guard unavailable source_webspace=%s dev_webspace=%s scenario=%s",
                source_webspace_id,
                webspace_id,
                requested_scenario,
                exc_info=True,
            )
        desired_dev_webspace = str(binding.get("dev_webspace_id") or "").strip()
        desired_scenario = str(binding.get("runtime_scenario_id") or "").strip()
        if desired_dev_webspace == webspace_id and desired_scenario and desired_scenario != requested_scenario:
            _log.info(
                "builder materialization superseded source_webspace=%s dev_webspace=%s requested_scenario=%s desired_scenario=%s revision=%s",
                source_webspace_id,
                webspace_id,
                requested_scenario,
                desired_scenario,
                str(revision or "").strip() or "-",
            )
            return {
                "ok": True,
                "accepted": False,
                "skipped": "superseded_builder_target",
                "action": "builder_revision_apply",
                "source_webspace_id": source_webspace_id,
                "webspace_id": webspace_id,
                "scenario_id": requested_scenario,
                "desired_scenario_id": desired_scenario,
                "revision": str(revision or "").strip() or None,
            }

    state, resolved_scenario_id, scenario_resolution = await _resolve_rebuild_scenario_target(
        webspace_id,
        requested_scenario,
        prefer_manifest_home_before_current=False,
    )
    resolved_scenario_id, scenario_resolution, preflight = _preflight_validated_scenario(
        resolved_scenario_id,
        source_mode=state.source_mode,
        resolution=scenario_resolution or "builder_revision",
    )
    if not resolved_scenario_id:
        return {
            "ok": False,
            "accepted": False,
            "action": "builder_revision_apply",
            "webspace_id": webspace_id,
            "scenario_id": None,
            "scenario_resolution": scenario_resolution,
            "kind": state.kind,
            "source_mode": state.source_mode,
            "validation": preflight,
            "error": "scenario_not_found",
        }

    identity_update = {
        "attempted": False,
        "changed": False,
        "webspace_id": webspace_id,
        "home_scenario_before": state.effective_home_scenario,
        "home_scenario": state.effective_home_scenario,
    }
    if state.is_dev and str(state.effective_home_scenario or "").strip() != resolved_scenario_id:
        stage_started = time.perf_counter()
        try:
            row = workspace_index.set_workspace_manifest(webspace_id, home_scenario=resolved_scenario_id)
            identity_update.update(
                {
                    "attempted": True,
                    "changed": True,
                    "home_scenario": row.effective_home_scenario,
                    "timing_ms": _elapsed_ms(stage_started),
                }
            )
        except Exception as exc:
            identity_update.update(
                {
                    "attempted": True,
                    "changed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "timing_ms": _elapsed_ms(stage_started),
                }
            )
            _log.warning(
                "failed to persist builder dev webspace identity webspace=%s scenario=%s",
                webspace_id,
                resolved_scenario_id,
                exc_info=True,
            )

    materialization_identity = canonical_materialization_identity(
        webspace_id=webspace_id,
        scenario_id=resolved_scenario_id,
        revision=revision,
        source_fingerprint=source_fingerprint,
        user_id=user_id,
        roles=roles,
        policy_fingerprint=policy_fingerprint,
    )
    scenario_content_override, skill_source_mode = _builder_preview_content_override(
        resolved_scenario_id,
        stage=str(preview_stage or ""),
        revision=revision,
        label=preview_label,
    )
    request_id = f"builder-revision-{materialization_identity['key_hash']}-{int(time.time() * 1000)}"
    trace = _payload_command_trace(event_payload or {})
    _log.info(
        "applying builder revision materialization webspace=%s scenario=%s revision=%s user=%s roles_hash=%s cmd=%s trace=%s key_hash=%s",
        webspace_id,
        resolved_scenario_id,
        materialization_identity.get("revision") or "-",
        materialization_identity.get("user_id") or "-",
        materialization_identity.get("roles_hash") or "-",
        trace.get("cmd_id") or "-",
        trace.get("trace_id") or "-",
        materialization_identity.get("key_hash") or "-",
    )

    result = await rebuild_webspace_from_sources(
        webspace_id,
        action="builder_revision_apply",
        scenario_id=resolved_scenario_id,
        scenario_resolution=scenario_resolution or "builder_revision",
        source_of_truth="builder_revision",
        reseed_from_scenario=False,
        event_payload=event_payload,
        request_id=request_id,
        switch_mode="materialization_pointer_compat",
        materialization_identity=materialization_identity,
        scenario_content_override=scenario_content_override,
        skill_source_mode=skill_source_mode,
    )
    result.update(
        {
            "kind": state.kind,
            "source_mode": state.source_mode,
            "home_scenario": identity_update.get("home_scenario") or state.effective_home_scenario,
            "current_scenario_before": state.current_scenario,
            "validation": preflight,
            "materialization_identity": materialization_identity,
            "webspace_identity_update": identity_update,
        }
    )
    return result


async def restore_webspace_from_snapshot(webspace_id: str) -> dict[str, Any]:
    """
    Restore a webspace from its latest persisted YStore snapshot and reconcile
    its materialized effective UI/runtime projection.
    """
    webspace_id = str(webspace_id or "").strip()
    if not webspace_id:
        raise ValueError("webspace_id is required")

    from adaos.services.yjs.gateway import reset_live_webspace_room  # pylint: disable=import-outside-toplevel
    from adaos.services.yjs.store import restore_ystore_for_webspace  # pylint: disable=import-outside-toplevel

    restore_result = await restore_ystore_for_webspace(webspace_id)
    if not bool(restore_result.get("accepted")):
        return restore_result

    reset_result: dict[str, Any] = {}
    try:
        reset_result = await reset_live_webspace_room(webspace_id, close_reason="webspace_restore")
    except Exception:
        _log.warning("failed to reset live room before restore for webspace=%s", webspace_id, exc_info=True)

    restored_current_scenario: str | None = None
    try:
        async with _open_readonly_operational_ydoc(webspace_id) as ydoc:
            restored_current_scenario = _normalize_optional_token(
                ydoc.get_map("ui").get("current_scenario")
            )
    except Exception:
        restored_current_scenario = None
    if restored_current_scenario:
        try:
            workspace_index.set_workspace_current_scenario_overlay(webspace_id, restored_current_scenario)
        except Exception:
            _log.debug(
                "failed to persist restored current scenario overlay webspace=%s scenario=%s",
                webspace_id,
                restored_current_scenario,
                exc_info=True,
            )

    rebuild_result = await rebuild_webspace_from_sources(
        webspace_id,
        action="restore",
        source_of_truth="snapshot",
        reseed_from_scenario=False,
        event_payload={"snapshot_path": str(restore_result.get("snapshot_path") or "")},
    )

    return {
        **restore_result,
        **rebuild_result,
        "action": "restore",
        "source_of_truth": "snapshot",
        "reset_room": reset_result,
    }


async def switch_webspace_scenario(
    webspace_id: str,
    scenario_id: str,
    *,
    set_home: bool | None = None,
    wait_for_rebuild: bool = True,
    request_id: str | None = None,
    request_source: str | None = None,
    request_client: str | None = None,
) -> dict[str, Any]:
    request = _SCENARIO_SWITCHING.normalize_request(
        webspace_id,
        scenario_id,
        set_home=set_home,
        wait_for_rebuild=wait_for_rebuild,
        request_id=request_id,
        request_source=request_source,
        request_client=request_client,
    )
    webspace_id = request.webspace_id
    scenario_id = request.scenario_id
    wait_for_rebuild = request.wait_for_rebuild
    request_id = request.request_id
    request_source = request.request_source
    request_client = request.request_client

    switch_started = time.perf_counter()
    timings_ms: Dict[str, float] = {}
    stage_started = time.perf_counter()
    state_before = await describe_webspace_operational_state(webspace_id)
    _record_timing(timings_ms, "describe_state_before", stage_started)

    stage_started = time.perf_counter()
    row = workspace_index.get_workspace(webspace_id) or workspace_index.ensure_workspace(webspace_id)
    resolved_set_home = request.set_home
    _record_timing(timings_ms, "resolve_manifest_policy", stage_started)
    stage_started = time.perf_counter()
    rebuild_state_before = describe_webspace_rebuild_state(webspace_id)
    _record_timing(timings_ms, "describe_rebuild_before", stage_started)
    materialized_scenario_before: str | None = None
    materialization_matches_target = True
    if str(state_before.current_scenario or "").strip() == scenario_id:
        stage_started = time.perf_counter()
        materialized_scenario_before = _materialization_scenario_from_rebuild_state(rebuild_state_before)
        if materialized_scenario_before is None:
            materialized_scenario_before = await _read_effective_materialization_scenario(webspace_id)
        _record_timing(timings_ms, "read_materialization_scenario_before", stage_started)
        materialization_matches_target = (
            materialized_scenario_before is None
            or str(materialized_scenario_before or "").strip() == scenario_id
        )
        if materialized_scenario_before and not materialization_matches_target:
            _log.warning(
                "desktop.scenario.set forcing rebuild for materialization mismatch webspace=%s current_scenario=%s materialized_scenario=%s target_scenario=%s",
                webspace_id,
                state_before.current_scenario,
                materialized_scenario_before,
                scenario_id,
            )

    _log.info(
        "desktop.scenario.set webspace=%s scenario=%s requested_set_home=%s resolved_set_home=%s request_source=%s request_id=%s request_client=%s",
        webspace_id,
        scenario_id,
        set_home,
        resolved_set_home,
        str(request_source or "").strip() or "-",
        str(request_id or "").strip() or "-",
        str(request_client or "").strip() or "-",
    )
    switch_mode = _scenario_switch_mode()
    atomic_selector_commit = True
    selector_commit_mode = "materialization_transaction"
    loader_space = _SCENARIO_SWITCHING.loader_space(row)
    switch_content: Dict[str, Any] | None = None

    def _build_switch_skip_result(*, skip_reason: str, rebuild_state: Mapping[str, Any], background_rebuild: bool) -> dict[str, Any]:
        phase_timings = _copy_timing_map(rebuild_state.get("phase_timings_ms"))
        if not phase_timings:
            phase_timings = _derive_phase_timings(
                switch_timings_ms=finalized_timings,
                rebuild_timings_ms=_copy_timing_map(rebuild_state.get("timings_ms")),
                semantic_rebuild_timings_ms=_copy_timing_map(rebuild_state.get("semantic_rebuild_timings_ms")),
                switch_mode="noop",
            )
        return {
            "ok": True,
            "accepted": True,
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
            "kind": row.effective_kind,
            "source_mode": row.effective_source_mode,
            "current_scenario_before": state_before.current_scenario,
            "home_scenario_before": state_before.effective_home_scenario,
            "home_scenario": row.effective_home_scenario,
            "set_home": resolved_set_home,
            "background_rebuild": background_rebuild,
            "scenario_switch_mode": switch_mode,
            "selector_commit_mode": "unchanged",
            "switch_skipped": True,
            "skip_reason": skip_reason,
            "timings_ms": finalized_timings,
            "rebuild_timings_ms": _copy_timing_map(rebuild_state.get("timings_ms")),
            "semantic_rebuild_timings_ms": _copy_timing_map(rebuild_state.get("semantic_rebuild_timings_ms")),
            "resolver": dict(rebuild_state.get("resolver") or {})
            if isinstance(rebuild_state.get("resolver"), Mapping)
            else None,
            "apply_summary": dict(rebuild_state.get("apply_summary") or {})
            if isinstance(rebuild_state.get("apply_summary"), Mapping)
            else None,
            "phase_timings_ms": phase_timings,
        }

    switch_decision = _SCENARIO_SWITCHING.decide(
        current_scenario=state_before.current_scenario,
        target_scenario=scenario_id,
        rebuild_state=rebuild_state_before,
        materialization_matches_target=materialization_matches_target,
    )
    if switch_decision.action == "skip":
        if resolved_set_home and row.effective_home_scenario != scenario_id:
            stage_started = time.perf_counter()
            row = workspace_index.set_workspace_manifest(webspace_id, home_scenario=scenario_id)
            _record_timing(timings_ms, "persist_home_scenario", stage_started)

            stage_started = time.perf_counter()
            await _sync_webspace_listing_target(webspace_id)
            _record_timing(timings_ms, "sync_listing", stage_started)

        finalized_timings = _finalize_timing_map(timings_ms, started_at=switch_started)
        _log.info(
            "desktop.scenario.set skipped webspace=%s scenario=%s mode=%s timings_ms=%s",
            webspace_id,
            scenario_id,
            switch_mode,
            finalized_timings,
        )
        return _build_switch_skip_result(
            skip_reason=str(switch_decision.reason or "already_current_ready"),
            rebuild_state=rebuild_state_before,
            background_rebuild=False,
        )

    if switch_decision.action == "join":
        if resolved_set_home and row.effective_home_scenario != scenario_id:
            stage_started = time.perf_counter()
            row = workspace_index.set_workspace_manifest(webspace_id, home_scenario=scenario_id)
            _record_timing(timings_ms, "persist_home_scenario", stage_started)

            stage_started = time.perf_counter()
            await _sync_webspace_listing_target(webspace_id)
            _record_timing(timings_ms, "sync_listing", stage_started)

        if wait_for_rebuild:
            existing_task = _SCENARIO_SWITCH_REBUILD_TASKS.get(webspace_id)
            if existing_task and not existing_task.done():
                stage_started = time.perf_counter()
                try:
                    await asyncio.shield(existing_task)
                except Exception:
                    pass
                _record_timing(timings_ms, "wait_existing_rebuild", stage_started)
                rebuild_state_before = describe_webspace_rebuild_state(webspace_id)

        finalized_timings = _finalize_timing_map(timings_ms, started_at=switch_started)
        _log.info(
            "desktop.scenario.set deduplicated webspace=%s scenario=%s mode=%s pending=%s timings_ms=%s",
            webspace_id,
            scenario_id,
            switch_mode,
            bool(rebuild_state_before.get("pending")),
            finalized_timings,
        )
        return _build_switch_skip_result(
            skip_reason=str(switch_decision.reason or "already_pending_rebuild"),
            rebuild_state=rebuild_state_before,
            background_rebuild=bool(rebuild_state_before.get("pending") or (not wait_for_rebuild and rebuild_state_before.get("background"))),
        )

    stage_started = time.perf_counter()
    scenario_exists = _scenario_exists_for_switch(scenario_id, space=loader_space)
    _record_timing(timings_ms, "validate_scenario", stage_started)
    if not scenario_exists:
        finalized_timings = _finalize_timing_map(timings_ms, started_at=switch_started)
        _set_webspace_rebuild_status(
            webspace_id,
            status="failed",
            pending=False,
            background=not wait_for_rebuild,
            action="scenario_switch_rebuild",
            source_of_truth="scenario_switch",
            scenario_id=scenario_id,
            scenario_resolution="explicit",
            switch_mode=switch_mode,
            requested_at=time.time(),
            finished_at=time.time(),
            error="scenario_not_found",
            projection_refresh=None,
            registry_summary=None,
            resolver=None,
            apply_summary=None,
            timings_ms=finalized_timings,
            phase_timings_ms=_derive_phase_timings(
                switch_timings_ms=finalized_timings,
                switch_mode=switch_mode,
            ),
        )
        return {
            "ok": False,
            "accepted": False,
            "error": "scenario_not_found",
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
            "scenario_switch_mode": switch_mode,
            "timings_ms": finalized_timings,
            "phase_timings_ms": _derive_phase_timings(
                switch_timings_ms=finalized_timings,
                switch_mode=switch_mode,
            ),
        }

    try:
        if atomic_selector_commit:
            timings_ms["defer_switch_pointer"] = 0.0
        else:
            stage_started = time.perf_counter()

            def _mutator(doc: Any, txn: Any) -> None:
                ui_map = doc.get_map("ui")
                _set_map_value_if_changed(ui_map, txn, "current_scenario", scenario_id)

            live_applied = mutate_live_room(
                webspace_id,
                _mutator,
                root_names=["ui"],
                source="webspace_runtime.switch_pointer",
                owner="core:webspace_runtime",
                channel="core.webspace_runtime.live_room",
            )
            if live_applied:
                _record_timing(timings_ms, "write_switch_pointer", stage_started)
            else:
                stage_started = time.perf_counter()
                async with _webspace_runtime_async_write_meta(
                    root_names=["ui"],
                    source="webspace_runtime.switch_pointer",
                ):
                    async with async_get_ydoc(webspace_id) as ydoc:
                        _record_timing(timings_ms, "open_doc", stage_started)
                        ui_map = ydoc.get_map("ui")
                        stage_started = time.perf_counter()
                        with ydoc.begin_transaction() as txn:
                            _set_map_value_if_changed(ui_map, txn, "current_scenario", scenario_id)
                        _record_timing(timings_ms, "write_switch_pointer", stage_started)

        stage_started = time.perf_counter()
        row = await asyncio.to_thread(
            workspace_index.set_workspace_current_scenario_overlay,
            webspace_id,
            scenario_id,
        )
        _record_timing(timings_ms, "persist_current_scenario", stage_started)
    except Exception:
        finalized_timings = _finalize_timing_map(timings_ms, started_at=switch_started)
        _set_webspace_rebuild_status(
            webspace_id,
            status="failed",
            pending=False,
            background=not wait_for_rebuild,
            action="scenario_switch_rebuild",
            source_of_truth="scenario_switch",
            scenario_id=scenario_id,
            scenario_resolution="explicit",
            switch_mode=switch_mode,
            requested_at=time.time(),
            finished_at=time.time(),
            error="scenario_switch_failed",
            projection_refresh=None,
            registry_summary=None,
            resolver=None,
            apply_summary=None,
            timings_ms=finalized_timings,
            phase_timings_ms=_derive_phase_timings(
                switch_timings_ms=finalized_timings,
                switch_mode=switch_mode,
            ),
        )
        _log.warning(
            "failed to switch scenario for webspace=%s scenario=%s timings_ms=%s",
            webspace_id,
            scenario_id,
            finalized_timings,
            exc_info=True,
        )
        return {
            "ok": False,
            "accepted": False,
            "error": "scenario_switch_failed",
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
            "scenario_switch_mode": switch_mode,
            "timings_ms": finalized_timings,
            "phase_timings_ms": _derive_phase_timings(
                switch_timings_ms=finalized_timings,
                switch_mode=switch_mode,
            ),
        }

    try:
        from adaos.services.yjs.gateway_ws import (  # pylint: disable=import-outside-toplevel
            note_authoritative_current_scenario,
        )

        if not atomic_selector_commit:
            note_authoritative_current_scenario(
                webspace_id,
                scenario_id,
                reason="scenario_switch",
            )
    except Exception:
        _log.debug("failed to publish authoritative current_scenario lease", exc_info=True)

    stage_started = time.perf_counter()
    row = workspace_index.get_workspace(webspace_id) or workspace_index.ensure_workspace(webspace_id)
    _record_timing(timings_ms, "refresh_manifest_row", stage_started)
    if resolved_set_home:
        stage_started = time.perf_counter()
        row = workspace_index.set_workspace_manifest(webspace_id, home_scenario=scenario_id)
        _record_timing(timings_ms, "persist_home_scenario", stage_started)

        stage_started = time.perf_counter()
        await _sync_webspace_listing_target(webspace_id)
        _record_timing(timings_ms, "sync_listing", stage_started)

    if not wait_for_rebuild:
        scheduled_switch_timings = _finalize_timing_map(dict(timings_ms), started_at=switch_started)
        stage_started = time.perf_counter()
        _schedule_scenario_switch_rebuild(
            webspace_id,
            scenario_id=scenario_id,
            scenario_resolution="explicit",
            switch_mode=switch_mode,
            switch_timings_ms=scheduled_switch_timings,
            request_id=request_id,
            request_source=request_source,
            request_client=request_client,
        )
        _record_timing(timings_ms, "schedule_background_rebuild", stage_started)
        finalized_timings = _finalize_timing_map(timings_ms, started_at=switch_started)
        current_status = describe_webspace_rebuild_state(webspace_id)
        _set_webspace_rebuild_status_if_current(
            webspace_id,
            str(current_status.get("request_id") or "").strip() or None,
            switch_timings_ms=finalized_timings,
            phase_timings_ms=_derive_phase_timings(
                switch_timings_ms=finalized_timings,
                switch_mode=switch_mode,
            ),
        )
        _log.info(
            "desktop.scenario.set accepted webspace=%s scenario=%s mode=%s background=%s timings_ms=%s",
            webspace_id,
            scenario_id,
            switch_mode,
            True,
            finalized_timings,
        )
        return {
            "ok": True,
            "accepted": True,
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
            "request_id": str(request_id or "").strip() or str(current_status.get("request_id") or "").strip() or None,
            "request_source": str(request_source or "").strip() or None,
            "request_client": str(request_client or "").strip() or None,
            "kind": row.effective_kind,
            "source_mode": row.effective_source_mode,
            "current_scenario_before": state_before.current_scenario,
            "home_scenario_before": state_before.effective_home_scenario,
            "home_scenario": row.effective_home_scenario,
            "set_home": resolved_set_home,
            "background_rebuild": True,
            "scenario_switch_mode": switch_mode,
            "selector_commit_mode": selector_commit_mode,
            "timings_ms": finalized_timings,
            "phase_timings_ms": _derive_phase_timings(
                switch_timings_ms=finalized_timings,
                switch_mode=switch_mode,
            ),
        }

    stage_started = time.perf_counter()
    rebuild_result = await _complete_scenario_switch_rebuild(
        webspace_id,
        scenario_id=scenario_id,
        scenario_resolution="explicit",
        switch_mode=switch_mode,
        switch_timings_ms=_finalize_timing_map(dict(timings_ms), started_at=switch_started),
    )
    _record_timing(timings_ms, "wait_rebuild", stage_started)
    if not bool(rebuild_result.get("accepted")):
        final_switch_timings = _finalize_timing_map(timings_ms, started_at=switch_started)
        rebuild_result["switch_timings_ms"] = final_switch_timings
        rebuild_result["phase_timings_ms"] = _derive_phase_timings(
            switch_timings_ms=final_switch_timings,
            rebuild_timings_ms=rebuild_result.get("timings_ms"),
            semantic_rebuild_timings_ms=rebuild_result.get("semantic_rebuild_timings_ms"),
            switch_mode=switch_mode,
        )
        return rebuild_result

    finalized_timings = _finalize_timing_map(timings_ms, started_at=switch_started)
    phase_timings = _derive_phase_timings(
        switch_timings_ms=finalized_timings,
        rebuild_timings_ms=rebuild_result.get("timings_ms"),
        semantic_rebuild_timings_ms=rebuild_result.get("semantic_rebuild_timings_ms"),
        switch_mode=switch_mode,
    )
    _log.info(
        "desktop.scenario.set completed webspace=%s scenario=%s mode=%s background=%s timings_ms=%s rebuild_timings_ms=%s",
        webspace_id,
        scenario_id,
        switch_mode,
        False,
        finalized_timings,
        rebuild_result.get("timings_ms"),
    )
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": webspace_id,
        "scenario_id": scenario_id,
        "kind": row.effective_kind,
        "source_mode": row.effective_source_mode,
        "current_scenario_before": state_before.current_scenario,
        "home_scenario_before": state_before.effective_home_scenario,
        "home_scenario": row.effective_home_scenario,
        "set_home": resolved_set_home,
        "background_rebuild": False,
        "scenario_switch_mode": switch_mode,
        "selector_commit_mode": selector_commit_mode,
        "timings_ms": finalized_timings,
        "rebuild_timings_ms": _copy_timing_map(rebuild_result.get("timings_ms")),
        "semantic_rebuild_timings_ms": _copy_timing_map(rebuild_result.get("semantic_rebuild_timings_ms")),
        "live_room_publish": rebuild_result.get("live_room_publish"),
        "live_room_refresh": rebuild_result.get("live_room_refresh"),
        "fresh_doc_rebuild": rebuild_result.get("fresh_doc_rebuild"),
        "resolver": dict(rebuild_result.get("resolver") or {})
        if isinstance(rebuild_result.get("resolver"), Mapping)
        else None,
        "apply_summary": dict(rebuild_result.get("apply_summary") or {})
        if isinstance(rebuild_result.get("apply_summary"), Mapping)
        else None,
        "phase_timings_ms": phase_timings,
    }


async def go_home_webspace(webspace_id: str, *, wait_for_rebuild: bool = False) -> dict[str, Any]:
    webspace_id = str(webspace_id or "").strip()
    if not webspace_id:
        raise ValueError("webspace_id is required")
    state = await describe_webspace_operational_state(webspace_id)
    scenario_id, scenario_resolution, preflight = _preflight_validated_scenario(
        state.effective_home_scenario,
        source_mode=state.source_mode,
        resolution="manifest_home",
    )
    if not scenario_id:
        return {
            "ok": False,
            "accepted": False,
            "action": "go_home",
            "source_of_truth": "manifest_home_scenario",
            "webspace_id": webspace_id,
            "scenario_id": None,
            "scenario_resolution": scenario_resolution,
            "validation": preflight,
            "error": "scenario_not_found",
        }
    result = await switch_webspace_scenario(
        webspace_id,
        scenario_id,
        set_home=False,
        wait_for_rebuild=wait_for_rebuild,
    )
    if bool(result.get("accepted")):
        try:
            workspace_index.set_workspace_current_scenario_overlay(webspace_id, None)
            result["current_scenario_overlay_cleared"] = True
        except Exception:
            result["current_scenario_overlay_cleared"] = False
            _log.debug("failed to clear current scenario overlay after go_home webspace=%s", webspace_id, exc_info=True)
    result["action"] = "go_home"
    result["source_of_truth"] = "manifest_home_scenario"
    result["scenario_resolution"] = scenario_resolution
    result["validation"] = preflight
    return result


async def set_current_webspace_home(webspace_id: str) -> dict[str, Any]:
    webspace_id = str(webspace_id or "").strip()
    if not webspace_id:
        raise ValueError("webspace_id is required")
    state = await describe_webspace_operational_state(webspace_id)
    scenario_id = str(state.current_scenario or "").strip()
    if not scenario_id:
        return {
            "ok": False,
            "accepted": False,
            "action": "set_home_current",
            "source_of_truth": "current_scenario",
            "webspace_id": webspace_id,
            "scenario_id": None,
            "current_scenario": None,
            "home_scenario_before": state.effective_home_scenario,
            "error": "current_scenario_unavailable",
        }
    svc = WebspaceService(get_ctx())
    info = await svc.set_home_scenario(webspace_id, scenario_id, home_scenario_ref=None)
    if info is None:
        return {
            "ok": False,
            "accepted": False,
            "action": "set_home_current",
            "source_of_truth": "current_scenario",
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
            "current_scenario": scenario_id,
            "home_scenario_before": state.effective_home_scenario,
            "error": "webspace_not_found",
        }
    return {
        "ok": True,
        "accepted": True,
        "action": "set_home_current",
        "source_of_truth": "current_scenario",
        "webspace_id": info.id,
        "scenario_id": scenario_id,
        "current_scenario": scenario_id,
        "home_scenario_before": state.effective_home_scenario,
        "home_scenario": info.home_scenario,
        "changed": str(state.effective_home_scenario or "").strip() != str(info.home_scenario or "").strip(),
        "kind": info.kind,
        "source_mode": info.source_mode,
    }


async def ensure_dev_webspace_for_scenario(
    scenario_id: str,
    *,
    requested_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    scenario_id = str(scenario_id or "").strip()
    if not scenario_id:
        raise ValueError("scenario_id is required")
    svc = WebspaceService(get_ctx())
    info, created = await svc.ensure_dev_for_scenario(
        scenario_id,
        requested_id=requested_id,
        title=title,
    )
    return {
        "ok": True,
        "accepted": True,
        "created": created,
        "webspace_id": info.id,
        "scenario_id": scenario_id,
        "home_scenario": info.home_scenario,
        "kind": info.kind,
        "source_mode": info.source_mode,
    }


async def reload_preview_webspaces_for_project(
    object_type: str,
    object_id: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    object_type = str(object_type or "").strip().lower()
    object_id = str(object_id or "").strip()
    if object_type not in {"scenario", "skill"} or not object_id:
        return {
            "ok": False,
            "accepted": False,
            "error": "project_identity_required",
        }

    # Only explicit Builder preview relations are consumers. A DEV workspace
    # is not a subscription merely because its home scenario happens to match.
    try:
        from adaos.services.builder.workbench import BuilderWorkbenchService

        workbench = BuilderWorkbenchService.from_context()
        workbench.list_workspace_bindings()  # migrate legacy binding files first
        relations = workbench.relationships.list()
    except Exception:
        relations = []

    targets: list[tuple[str, str]] = []
    seen_targets: set[str] = set()
    for relation in relations:
        webspace_id = str(relation.target_webspace_id or "").strip()
        if not webspace_id or webspace_id in seen_targets:
            continue
        row = workspace_index.get_workspace(webspace_id)
        if row is None:
            _log.info(
                "ignoring stale preview relation target=%s project=%s:%s",
                webspace_id,
                object_type,
                object_id,
            )
            continue
        home_scenario = str(
            getattr(row, "effective_home_scenario", "")
            or relation.metadata.get("scenario_id")
            or ""
        ).strip()
        if not home_scenario:
            continue
        if object_type == "scenario":
            if home_scenario == object_id:
                targets.append((webspace_id, home_scenario))
                seen_targets.add(webspace_id)
            continue
        try:
            source_mode = str(getattr(row, "effective_source_mode", "dev") or "dev")
            manifest = scenarios_loader.read_manifest(home_scenario, space=source_mode)
            depends_raw = manifest.get("depends") or []
            depends = {
                str(item).strip()
                for item in depends_raw
                if str(item).strip()
            }
            if object_id in depends:
                targets.append((webspace_id, home_scenario))
                seen_targets.add(webspace_id)
        except Exception:
            _log.debug(
                "failed to resolve scenario depends for preview webspace=%s home=%s",
                webspace_id,
                home_scenario,
                exc_info=True,
            )

    reloaded: list[str] = []
    failed: list[str] = []
    for webspace_id, scenario_id in targets:
        try:
            await reload_webspace_from_scenario(
                webspace_id,
                scenario_id=scenario_id,
                action="reload",
            )
            reloaded.append(webspace_id)
        except Exception:
            failed.append(webspace_id)
            _log.warning(
                "failed to reload preview webspace=%s for %s:%s reason=%s",
                webspace_id,
                object_type,
                object_id,
                reason,
                exc_info=True,
            )

    return {
        "ok": not failed,
        "accepted": bool(targets),
        "object_type": object_type,
        "object_id": object_id,
        "reason": str(reason or "").strip() or None,
        "reloaded_webspaces": reloaded,
        "failed_webspaces": failed,
    }


@subscribe("desktop.webspace.reload")
async def _on_webspace_reload(evt: Dict[str, Any]) -> None:
    """
    Re-seed the current webspace from its scenario, effectively
    rebuilding ui/data/registry for debugging or recovery.
    """
    payload = _payload(evt)
    event_type = _event_type(evt) or _event_type(payload)
    if event_type and event_type != "desktop.webspace.reload":
        _log.debug("ignoring non-command webspace reload event type=%s", event_type)
        return
    webspace_id = _webspace_id(payload)
    if not webspace_id:
        return
    recreate_room = bool(payload.get("recreate_room"))
    await reload_webspace_from_scenario(
        webspace_id,
        scenario_id=str(payload.get("scenario_id") or "").strip() or None,
        action="reset" if recreate_room else "reload",
        event_payload=payload,
    )


@subscribe("builder.ui_revision.materialize")
async def _on_builder_ui_revision_materialize(evt: Dict[str, Any]) -> None:
    """
    Apply a Builder UI revision from the runtime/event-loop owner context.

    Skill tool handlers may run in worker threads. They must not mutate Yjs
    documents directly from those threads because y-py/yrs state-vector
    encoding is not a safe cross-thread boundary. The Builder skill publishes
    this command instead; the async subscriber owns the actual materialization.
    """
    payload = _payload(evt)
    event_type = _event_type(evt) or _event_type(payload)
    if event_type and event_type != "builder.ui_revision.materialize":
        _log.debug("ignoring non-command builder materialize event type=%s", event_type)
        return
    webspace_id = _webspace_id(payload)
    scenario_id = str(payload.get("scenario_id") or "").strip()
    if not webspace_id or not scenario_id:
        return
    try:
        delay_s = max(0.0, min(float(payload.get("delay_s") or 0.0), 10.0))
    except Exception:
        delay_s = 0.0
    if delay_s > 0:
        await asyncio.sleep(delay_s)
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    try:
        started = time.perf_counter()
        revision_token = str(payload.get("revision") or payload.get("ui_revision") or "").strip() or None
        _log.info(
            "builder materialization event handling started webspace=%s scenario=%s revision=%s delay_s=%.3f",
            webspace_id,
            scenario_id,
            revision_token or "-",
            delay_s,
        )
        result = await apply_builder_revision_materialization(
            webspace_id,
            scenario_id=scenario_id,
            revision=revision_token,
            source_fingerprint=str(payload.get("source_fingerprint") or "").strip() or None,
            user_id=str(payload.get("user_id") or meta.get("user_id") or "guest").strip() or "guest",
            roles=payload.get("roles") if "roles" in payload else meta.get("roles"),
            policy_fingerprint=str(payload.get("policy_fingerprint") or "").strip() or None,
            event_payload=payload,
        )
        _log.info(
            "builder materialization event handled webspace=%s scenario=%s revision=%s ok=%s duration_ms=%.3f",
            webspace_id,
            scenario_id,
            revision_token or "-",
            bool(result.get("ok")) if isinstance(result, Mapping) else False,
            _elapsed_ms(started),
        )
    except Exception:
        _log.warning(
            "builder ui revision materialization failed webspace=%s scenario=%s revision=%s",
            webspace_id,
            scenario_id,
            str(payload.get("revision") or payload.get("ui_revision") or "").strip() or "-",
            exc_info=True,
        )


@subscribe("desktop.webspace.reset")
async def _on_webspace_reset(evt: Dict[str, Any]) -> None:
    """
    Hard reset of the current webspace from its scenario.

    Unlike desktop.webspace.reload, this recovery path intentionally resets
    the live room and persisted YStore before reseeding the scenario payload.
    """
    payload = _payload(evt)
    event_type = _event_type(evt) or _event_type(payload)
    if event_type and event_type != "desktop.webspace.reset":
        _log.debug("ignoring non-command webspace reset event type=%s", event_type)
        return
    webspace_id = _webspace_id(payload)
    if not webspace_id:
        return
    await reload_webspace_from_scenario(
        webspace_id,
        scenario_id=str(payload.get("scenario_id") or "").strip() or None,
        action="reset",
        event_payload=payload,
    )


@subscribe("desktop.webspace.go_home")
async def _on_webspace_go_home(evt: Dict[str, Any]) -> None:
    payload = _payload(evt)
    webspace_id = _webspace_id(payload)
    if not webspace_id:
        return
    wait_for_rebuild = bool(payload.get("wait_for_rebuild")) if "wait_for_rebuild" in payload else False
    await go_home_webspace(webspace_id, wait_for_rebuild=wait_for_rebuild)


async def prewarm_webspace_materialization_sources() -> dict[str, Any]:
    """Build the process-owned skill declaration catalog before first use."""

    started = time.perf_counter()

    def _warm() -> dict[str, Any]:
        runtime = WebspaceScenarioRuntime()
        modes: dict[str, Any] = {}
        for mode in ("workspace", "dev"):
            mode_started = time.perf_counter()
            decls = runtime._collect_skill_decls(mode=mode)
            modes[mode] = {
                "declarations": len(decls),
                "fingerprint": str(getattr(runtime, "_last_skill_decls_fingerprint", "") or ""),
                "elapsed_ms": _elapsed_ms(mode_started),
            }
            _skill_sources_fingerprint_for_materialization(mode)
        return modes

    modes = await asyncio.to_thread(_warm)
    result = {
        "ok": True,
        "modes": modes,
        "elapsed_ms": _elapsed_ms(started),
    }


async def reload_workspace_webspaces_for_publication(
    object_type: str,
    object_id: str,
) -> dict[str, Any]:
    """Reload workspace-backed consumers after a DEV artifact is published.

    Publishing copies a new source tree into ``workspace``. Existing Yjs rooms
    keep their materialized projection until they are explicitly rebuilt, so a
    successful publication must invalidate and reload matching workspace-mode
    webspaces. DEV webspaces are intentionally excluded: their source of truth
    remains the DEV tree and Builder revision materialization flow.
    """

    object_type = str(object_type or "").strip().lower()
    object_id = str(object_id or "").strip()
    if object_type not in {"scenario", "skill"} or not object_id:
        return {"ok": False, "accepted": False, "error": "project_identity_required"}

    if object_type == "scenario":
        scenarios_loader.invalidate_cache(scenario_id=object_id, space="workspace")

    try:
        rows = list(workspace_index.list_workspaces())
    except Exception:
        rows = []

    targets: list[tuple[str, str]] = []
    for row in rows:
        if str(getattr(row, "effective_source_mode", "workspace") or "workspace").strip().lower() != "workspace":
            continue
        webspace_id = str(getattr(row, "workspace_id", "") or "").strip()
        if not webspace_id:
            continue
        try:
            state = await describe_webspace_operational_state(webspace_id)
            scenario_id = str(state.current_scenario or state.effective_home_scenario or "").strip()
        except Exception:
            scenario_id = str(getattr(row, "effective_home_scenario", "") or "").strip()
        if not scenario_id:
            continue
        if object_type == "scenario":
            if scenario_id != object_id:
                continue
        else:
            try:
                manifest = scenarios_loader.read_manifest(scenario_id, space="workspace")
                dependencies = {
                    str(item).strip()
                    for item in (manifest.get("depends") or [])
                    if str(item).strip()
                }
            except Exception:
                dependencies = set()
            if object_id not in dependencies:
                continue
        targets.append((webspace_id, scenario_id))

    reloaded: list[str] = []
    failed: list[str] = []
    for webspace_id, scenario_id in targets:
        try:
            await reload_webspace_from_scenario(
                webspace_id,
                scenario_id=scenario_id,
                action=f"published_{object_type}_reload",
                event_payload={
                    "source": "registry.publication",
                    "object_type": object_type,
                    "object_id": object_id,
                },
            )
            reloaded.append(webspace_id)
        except Exception:
            failed.append(webspace_id)
            _log.warning(
                "failed to reload workspace webspace=%s after publishing %s:%s",
                webspace_id,
                object_type,
                object_id,
                exc_info=True,
            )

    return {
        "ok": not failed,
        "accepted": bool(targets),
        "object_type": object_type,
        "object_id": object_id,
        "reloaded_webspaces": reloaded,
        "failed_webspaces": failed,
    }


@subscribe("registry.scenarios.published")
async def _on_scenario_published(evt: Dict[str, Any]) -> None:
    payload = _payload(evt)
    scenario_id = str(payload.get("name") or payload.get("scenario_id") or "").strip()
    if scenario_id:
        await reload_workspace_webspaces_for_publication("scenario", scenario_id)


@subscribe("registry.skills.published")
async def _on_skill_published(evt: Dict[str, Any]) -> None:
    payload = _payload(evt)
    skill_id = str(payload.get("name") or payload.get("skill_id") or "").strip()
    if skill_id:
        await reload_workspace_webspaces_for_publication("skill", skill_id)
    _log.info("prewarmed webspace materialization sources result=%s", result)
    return result


@subscribe("desktop.webspace.set_home")
async def _on_webspace_set_home(evt: Dict[str, Any]) -> None:
    payload = _payload(evt)
    webspace_id = _webspace_id(payload)
    scenario_id = str(payload.get("scenario_id") or "").strip()
    if not webspace_id or not scenario_id:
        return
    svc = WebspaceService(get_ctx())
    home_scenario_ref = (
        payload.get("home_scenario_ref")
        if "home_scenario_ref" in payload
        else _HOME_SCENARIO_REF_UNSET
    )
    await svc.set_home_scenario(webspace_id, scenario_id, home_scenario_ref=home_scenario_ref)


@subscribe("desktop.webspace.set_home_current")
async def _on_webspace_set_home_current(evt: Dict[str, Any]) -> None:
    payload = _payload(evt)
    webspace_id = _webspace_id(payload)
    if not webspace_id:
        return
    await set_current_webspace_home(webspace_id)


@subscribe("desktop.scenario.set")
async def _on_desktop_scenario_set(evt: Dict[str, Any]) -> None:
    """
    Switch the current desktop scenario for a webspace and re-sync the
    target YDoc from the selected scenario package.

    Payload:
      - scenario_id: id of the desktop scenario (required)
      - webspace_id / workspace_id: optional, defaults to current/default.
    """
    payload = _payload(evt)
    scenario_id = str(payload.get("scenario_id") or "").strip()
    if not scenario_id:
        return
    webspace_id = _webspace_id(payload)
    set_home: bool | None = None
    if "set_home" in payload:
        set_home = bool(payload.get("set_home"))
    elif "persist_home" in payload:
        set_home = bool(payload.get("persist_home"))
    wait_for_rebuild = bool(payload.get("wait_for_rebuild")) if "wait_for_rebuild" in payload else False
    await switch_webspace_scenario(
        webspace_id,
        scenario_id,
        set_home=set_home,
        wait_for_rebuild=wait_for_rebuild,
    )


@subscribe(PROJECT_CONTENT_CHANGED)
async def _on_project_content_changed(evt: Dict[str, Any]) -> None:
    payload = _payload(evt)
    identity = ProjectEventIdentity.from_payload(payload)
    if identity is None:
        return
    reason = str(payload.get("reason") or "").strip()
    if reason in {
        "builder_project_updated",
        "builder_ui_revision_written",
    }:
        _log.debug(
            "prompt project change uses builder-managed preview refresh; skipping runtime reload object=%s:%s reason=%s",
            identity.kind,
            identity.project_id,
            reason,
        )
        return
    await reload_preview_webspaces_for_project(
        identity.kind,
        identity.project_id,
        reason=reason or None,
    )


@subscribe("prompt.project.changed")
async def _on_prompt_project_changed(evt: Dict[str, Any]) -> None:
    """Compatibility adapter for the former overloaded Prompt IDE event."""

    payload = _payload(evt)
    reason = str(payload.get("reason") or "").strip()
    if legacy_project_event_topic(reason) == BUILDER_CONTEXT_SELECTED:
        _log.debug("legacy project selection does not reload previews reason=%s", reason or "-")
        return
    await _on_project_content_changed(payload)


__all__ = [
    "WebUIRegistryEntry",
    "WebspaceResolverInputs",
    "WebspaceResolverOutputs",
    "WebspaceScenarioRuntime",
    "describe_webspace_operational_state",
    "describe_webspace_validation_state",
    "describe_webspace_overlay_state",
    "describe_webspace_projection_state",
    "describe_webspace_rebuild_state",
    "get_webspace_rebuild_materialized_payload",
    "invalidate_webspace_materialization_cache",
    "prewarm_webspace_materialization_sources",
    "reload_workspace_webspaces_for_publication",
    "set_current_webspace_home",
    "rebuild_webspace_from_sources",
    "canonical_materialization_identity",
    "apply_builder_revision_materialization",
]
