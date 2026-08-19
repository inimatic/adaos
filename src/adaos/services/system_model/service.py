from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

from adaos.services.core_update import read_status as read_core_update_status
from adaos.services.bootstrap import is_ready, load_config
from adaos.services.env_policy import env_bool
from adaos.services.reliability import reliability_snapshot, sidecar_runtime_snapshot
from adaos.services.registry.subnet_directory import get_directory
from adaos.services.runtime_paths import current_base_dir
from adaos.services.runtime_environment import runtime_environment_payload
from adaos.services.runtime_lifecycle import runtime_lifecycle_snapshot
from adaos.services.runtime_topology import runtime_port_http_base_from_env, supervisor_base_from_env
from adaos.services.subnet.link_client import get_member_link_client
from adaos.services.system_model.catalog import (
    browser_session_objects,
    current_profile_object,
    device_objects,
    installed_scenario_objects,
    installed_skill_objects,
    local_capacity_object,
    local_io_objects,
    workspace_objects,
)
from adaos.services.system_model.governance import apply_governance_defaults, apply_projection_governance
from adaos.services.system_model.model import CanonicalKind, canonical_ref, compact_mapping
from adaos.services.system_model.mappers import (
    canonical_object_from_capacity_snapshot,
    canonical_object_from_io_capacity_entry,
    canonical_object_from_node_status,
    canonical_object_from_supervisor_runtime,
    canonical_object_from_subnet_directory_node,
    coerce_mapping,
)
from adaos.services.system_model.projections import (
    canonical_object_inspector,
    canonical_object_projection,
    canonical_overview_projection,
    canonical_inventory_projection,
    canonical_neighborhood_projection,
    canonical_task_packet,
    canonical_topology_projection,
    canonical_projection_from_reliability_snapshot,
)


_CONTROL_PLANE_CACHE_TTL_S = 1.0
_CONTROL_PLANE_CACHE: dict[str, tuple[float, list[Any]]] = {}
_CONTROL_PLANE_CACHE_LOCK = threading.Lock()
_CONTROL_PLANE_CACHE_BUILD_LOCKS: dict[str, threading.Lock] = {}
_DEPLOYMENT_INVENTORY_CACHE_TTL_S = 5.0
_DEPLOYMENT_INVENTORY_CACHE: tuple[float, dict[str, Any]] = (0.0, {})
_DEPLOYMENT_INVENTORY_CACHE_LOCK = threading.Lock()


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def route_info(role: str) -> tuple[str | None, bool | None]:
    route_mode = None
    connected = None
    try:
        if role == "hub":
            route_mode = "hub"
        elif role == "member":
            connected = bool(get_member_link_client().is_connected())
            route_mode = "ws" if connected else "none"
    except Exception:
        route_mode = None
        connected = None
    return route_mode, connected


def _node_status_supervisor_runtime(base_dir: Path) -> dict[str, Any]:
    runtime_state = _read_json_file((base_dir / "state" / "supervisor" / "runtime.json").resolve())
    update_attempt = _read_json_file((base_dir / "state" / "supervisor" / "update_attempt.json").resolve())
    update_status = read_core_update_status() or {}
    supervisor_enabled = env_bool("ADAOS_SUPERVISOR_ENABLED")
    runtime_url = str(runtime_state.get("runtime_url") or "").strip()
    supervisor_url = str(os.getenv("ADAOS_SUPERVISOR_URL") or "").strip()
    if not supervisor_url and supervisor_enabled:
        supervisor_url = supervisor_base_from_env()
    return {
        "available": bool(supervisor_enabled or runtime_state),
        "enabled": bool(supervisor_enabled),
        "status": update_status if isinstance(update_status, dict) else {},
        "attempt": update_attempt if isinstance(update_attempt, dict) else {},
        "runtime": runtime_state if isinstance(runtime_state, dict) else {},
        "runtime_url": runtime_url.rstrip("/") or None,
        "supervisor_url": supervisor_url.rstrip("/") or None,
        "_served_by": "api.node.status",
    }


def _node_status_sidecar_runtime(role: str | None) -> dict[str, Any]:
    try:
        payload = sidecar_runtime_snapshot(
            role=role,
            readiness_tree={},
            hub_root_protocol={},
            transport_strategy={},
            media_runtime={},
        )
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def current_node_identity_status_payload() -> dict[str, Any]:
    """Build the node identity without entering diagnostic I/O paths."""

    conf = load_config()
    route_mode, connected = route_info(conf.role)
    lifecycle = runtime_lifecycle_snapshot()
    return {
        "node_id": conf.node_id,
        "subnet_id": conf.subnet_id,
        "role": conf.role,
        "node_names": list(getattr(conf, "node_names", []) or []),
        "primary_node_name": str(getattr(conf, "primary_node_name", "") or ""),
        "ready": is_ready() and not bool(lifecycle.get("draining")),
        "node_state": str(lifecycle.get("node_state") or "ready"),
        "draining": bool(lifecycle.get("draining")),
        "route_mode": route_mode,
        "connected_to_subnet": connected,
        "connected_to_hub": connected,
    }


def current_node_probe_status_payload() -> dict[str, Any]:
    """Return the live identity/reachability projection without diagnostic I/O."""

    identity = current_node_identity_status_payload()
    runtime_url = runtime_port_http_base_from_env()
    runtime = {
        "runtime_url": runtime_url,
        "runtime_state": str(identity.get("node_state") or "ready"),
        "transition_role": str(os.getenv("ADAOS_RUNTIME_TRANSITION_ROLE") or "active").strip() or "active",
        "runtime_instance_id": str(os.getenv("ADAOS_RUNTIME_INSTANCE_ID") or "").strip() or None,
        "slot": str(os.getenv("ADAOS_ACTIVE_CORE_SLOT") or os.getenv("ADAOS_RUNTIME_SLOT") or "").strip() or None,
    }
    return {
        **identity,
        "status_profile": "probe",
        "runtime": runtime,
        "environment": runtime_environment_payload(),
    }


def current_node_status_payload() -> dict[str, Any]:
    identity = current_node_identity_status_payload()
    runtime_environment = runtime_environment_payload()
    base_dir = current_base_dir()
    supervisor_runtime = _node_status_supervisor_runtime(base_dir)
    sidecar_runtime = _node_status_sidecar_runtime(str(identity.get("role") or "") or None)
    if sidecar_runtime:
        runtime_state = supervisor_runtime.get("runtime")
        runtime_state = dict(runtime_state) if isinstance(runtime_state, dict) else {}
        runtime_state["sidecar"] = sidecar_runtime
        runtime_state["sidecar_source"] = "reliability.sidecar_runtime_snapshot"
        supervisor_runtime["runtime"] = runtime_state
    core_update_status = supervisor_runtime.get("status")
    deployment = _deployment_inventory_status_payload()
    return {
        **identity,
        "runtime": {
            "environment": runtime_environment,
            "supervisor_available": bool(supervisor_runtime.get("available")),
            "supervisor_runtime": supervisor_runtime,
            "sidecar_runtime": sidecar_runtime,
            "core_update_status": core_update_status if isinstance(core_update_status, dict) else {},
        },
        "environment": runtime_environment,
        "deployment": deployment,
    }


def _deployment_inventory_status_payload() -> dict[str, Any]:
    global _DEPLOYMENT_INVENTORY_CACHE
    now = time.monotonic()
    cached_at, cached = _DEPLOYMENT_INVENTORY_CACHE
    if cached and now - cached_at < _DEPLOYMENT_INVENTORY_CACHE_TTL_S:
        return dict(cached)
    with _DEPLOYMENT_INVENTORY_CACHE_LOCK:
        cached_at, cached = _DEPLOYMENT_INVENTORY_CACHE
        if cached and now - cached_at < _DEPLOYMENT_INVENTORY_CACHE_TTL_S:
            return dict(cached)
        try:
            from adaos.services.project_deployment.default_runtime import (
                deployment_runtime_inventory_payload,
            )

            payload = deployment_runtime_inventory_payload()
        except Exception:
            payload = {}
        _DEPLOYMENT_INVENTORY_CACHE = (now, dict(payload))
        return dict(payload)


def _bounded_interval_seconds(raw: Any, *, default: float, minimum: float) -> float:
    try:
        interval_s = float(raw)
    except Exception:
        interval_s = float(default)
    if not math.isfinite(interval_s):
        interval_s = float(default)
    if interval_s < float(minimum):
        interval_s = float(minimum)
    return float(interval_s)


def node_status_push_heartbeat_s() -> float:
    try:
        raw = (
            os.getenv("ADAOS_NODE_STATUS_PUSH_HEARTBEAT_S", "5") or "5"
        )
    except Exception:
        raw = "5"
    return _bounded_interval_seconds(raw, default=5.0, minimum=2.0)


def _select_mapping_fields(value: object, fields: tuple[str, ...]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {field: source[field] for field in fields if field in source}


def _compact_core_update_status(value: object) -> dict[str, Any]:
    return _select_mapping_fields(
        value,
        (
            "state",
            "phase",
            "action",
            "message",
            "target_rev",
            "target_version",
            "requested_target_version",
            "target_slot",
            "reason",
            "planned_reason",
            "countdown_sec",
            "scheduled_for",
            "started_at",
            "prepare_elapsed_s",
            "prepare_heartbeat_at",
            "prepare_timeout_sec",
            "prepared_at",
            "validated_at",
            "finished_at",
            "updated_at",
            "min_update_period_sec",
            "subsequent_transition",
            "subsequent_transition_requested_at",
            "subsequent_transition_action",
            "subsequent_transition_target_rev",
            "subsequent_transition_target_version",
            "candidate_prewarm_state",
            "candidate_prewarm_message",
            "candidate_prewarm_ready_at",
            "candidate_prewarm_deferral_count",
            "candidate_prewarm_max_deferrals",
            "restart_mode",
            "restart_requested_at",
            "root_promotion_required",
            "error_type",
            "error",
            "last_error",
            "active_slot_target_mismatch",
            "active_slot_target_mismatch_reason",
        ),
    )


def compact_node_status_transport_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep node-status fanout bounded while retaining control-plane state."""

    top_level_fields = (
        "node_id",
        "subnet_id",
        "role",
        "node_names",
        "primary_node_name",
        "node_label",
        "node_compact_label",
        "node_index",
        "node_color",
        "ready",
        "node_state",
        "draining",
        "route_mode",
        "connected_to_subnet",
        "connected_to_hub",
        "updated_at",
        "heartbeat_interval_s",
        "trigger",
    )
    compact = _select_mapping_fields(payload, top_level_fields)
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    supervisor = runtime.get("supervisor_runtime") if isinstance(runtime.get("supervisor_runtime"), dict) else {}
    compact_supervisor = _select_mapping_fields(
        supervisor,
        ("available", "enabled", "runtime_url", "supervisor_url", "_served_by"),
    )
    compact_supervisor["status"] = _compact_core_update_status(supervisor.get("status"))
    compact_supervisor["attempt"] = _select_mapping_fields(
        supervisor.get("attempt"),
        (
            "contract_version",
            "authority",
            "state",
            "action",
            "requested_at",
            "transitioned_at",
            "scheduled_for",
            "updated_at",
            "completed_at",
            "countdown_sec",
            "target_rev",
            "target_version",
            "reason",
            "planned_reason",
            "completion_reason",
            "accepted",
            "awaiting_restart",
            "restart_required",
            "restart_mode",
            "restart_requested_at",
            "subsequent_transition",
            "subsequent_transition_requested_at",
            "candidate_prewarm_state",
            "candidate_prewarm_message",
            "candidate_prewarm_ready_at",
        ),
    )
    compact_supervisor["runtime"] = _select_mapping_fields(
        supervisor.get("runtime"),
        (
            "ok",
            "runtime_url",
            "runtime_instance_id",
            "transition_role",
            "active_slot",
            "previous_slot",
            "managed_pid",
            "managed_alive",
            "listener_running",
            "runtime_api_ready",
            "runtime_state",
            "candidate_slot",
            "candidate_runtime_port",
            "candidate_runtime_url",
            "candidate_runtime_instance_id",
            "candidate_transition_role",
            "candidate_managed_pid",
            "candidate_managed_alive",
            "candidate_runtime_api_ready",
            "candidate_runtime_state",
            "transition_mode",
            "warm_switch_enabled",
            "warm_switch_supported",
            "warm_switch_allowed",
            "warm_switch_reason",
            "root_promotion_required",
            "restart_count",
            "last_start_at",
            "last_stop_reason",
            "last_exit_at",
            "last_exit_code",
            "last_error",
            "updated_at",
        ),
    )
    compact_sidecar = _select_mapping_fields(
        runtime.get("sidecar_runtime"),
        (
            "enabled",
            "enablement",
            "phase",
            "transport_owner",
            "lifecycle_manager",
            "continuity_contract",
            "progress",
            "route_tunnel_contract",
            "status",
            "summary",
            "session_state",
            "status_reason",
            "local_url",
            "diag_age_s",
            "diag_fresh",
            "local_listener_state",
            "remote_session_state",
            "transport_ready",
            "control_ready",
            "route_ready",
            "sync_ready",
            "media_ready",
            "transport_provenance",
        ),
    )
    environment = payload.get("environment") if isinstance(payload.get("environment"), dict) else {}
    compact["runtime"] = {
        "environment": runtime.get("environment") if isinstance(runtime.get("environment"), dict) else environment,
        "supervisor_available": bool(runtime.get("supervisor_available")),
        "supervisor_runtime": compact_supervisor,
        "sidecar_runtime": compact_sidecar,
        "core_update_status": _compact_core_update_status(runtime.get("core_update_status")),
    }
    compact["environment"] = environment
    deployment = payload.get("deployment")
    compact["deployment"] = dict(deployment) if isinstance(deployment, dict) else {}
    meta = dict(payload.get("_meta") or {}) if isinstance(payload.get("_meta"), dict) else {}
    meta.update(
        {
            "projection": "adaos.node_status.transport.v1",
            "diagnostics_truncated": True,
        }
    )
    compact["_meta"] = meta
    return compact


def current_node_status_push_payload(*, updated_at: float | None = None) -> dict[str, Any]:
    payload = current_node_status_payload()
    payload["updated_at"] = float(updated_at or time.time())
    payload["heartbeat_interval_s"] = float(node_status_push_heartbeat_s())
    return compact_node_status_transport_payload(payload)


def _control_plane_scope_refs() -> tuple[str | None, str | None]:
    conf = load_config()
    subnet_value = str(getattr(conf, "subnet_id", "") or "").strip()
    owner_value = str(getattr(conf, "owner_id", "") or "").strip()
    tenant_id = f"subnet:{subnet_value}" if subnet_value else None
    owner_id = canonical_ref(CanonicalKind.PROFILE, owner_value) or (f"profile:{owner_value}" if owner_value else None)
    return tenant_id, owner_id


def _node_ref(subject_id: str) -> str:
    if ":" in subject_id:
        _, _, node_token = subject_id.partition(":")
        return node_token or subject_id
    return subject_id


def _append_unique(objects: list[Any], item: Any, seen: set[str]) -> None:
    obj_id = str(getattr(item, "id", "") or "").strip()
    if not obj_id or obj_id in seen:
        return
    seen.add(obj_id)
    objects.append(item)


def current_node_object():
    tenant_id, owner_id = _control_plane_scope_refs()
    return apply_governance_defaults(
        canonical_object_from_node_status(current_node_identity_status_payload()),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def current_supervisor_runtime_object():
    tenant_id, owner_id = _control_plane_scope_refs()
    node_payload = current_node_status_payload()
    node_id = str(node_payload.get("node_id") or "local").strip() or "local"
    base_dir = current_base_dir()
    runtime_state = _read_json_file((base_dir / "state" / "supervisor" / "runtime.json").resolve())
    update_attempt = _read_json_file((base_dir / "state" / "supervisor" / "update_attempt.json").resolve())
    update_status = read_core_update_status()
    if not runtime_state and not update_status and not update_attempt:
        return None
    return apply_governance_defaults(
        canonical_object_from_supervisor_runtime(
            {
                "node_id": node_id,
                "runtime_state": runtime_state,
                "update_status": update_status,
                "update_attempt": update_attempt,
            }
        ),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def current_reliability_payload(*, webspace_id: str | None = None) -> dict[str, Any]:
    conf = load_config()
    route_mode, connected = route_info(conf.role)
    lifecycle = runtime_lifecycle_snapshot()
    return reliability_snapshot(
        node_id=conf.node_id,
        subnet_id=conf.subnet_id,
        role=conf.role,
        zone_id=getattr(conf, "zone_id", None),
        local_ready=is_ready(),
        node_state=str(lifecycle.get("node_state") or "ready"),
        draining=bool(lifecycle.get("draining")),
        route_mode=route_mode,
        connected_to_hub=connected,
        node_names=list(getattr(conf, "node_names", []) or []),
        webspace_id=webspace_id,
    )


def current_reliability_projection(*, webspace_id: str | None = None):
    tenant_id, owner_id = _control_plane_scope_refs()
    return apply_projection_governance(
        canonical_projection_from_reliability_snapshot(current_reliability_payload(webspace_id=webspace_id)),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def _flatten_refs(relations: Any) -> list[str]:
    data = relations if isinstance(relations, dict) else {}
    out: list[str] = []
    for value in data.values():
        items = value if isinstance(value, list) else [value]
        for item in items:
            token = str(item or "").strip()
            if token and token not in out:
                out.append(token)
    return out


def _current_node_neighborhood_projection(*, webspace_id: str | None = None):
    tenant_id, owner_id = _control_plane_scope_refs()
    subject = current_node_object()
    node_ref = _node_ref(subject.id)
    reliability = current_reliability_projection(webspace_id=webspace_id)

    objects: list[Any] = []
    seen: set[str] = set()
    _append_unique(objects, local_capacity_object(node_id=node_ref), seen)
    for item in reliability.objects:
        if str(item.kind or "").strip() not in {CanonicalKind.ROOT.value, CanonicalKind.CONNECTION.value}:
            continue
        _append_unique(objects, item, seen)

    try:
        directory_nodes = list(get_directory().list_known_nodes() or [])
    except Exception:
        directory_nodes = []

    for entry in sorted(directory_nodes, key=lambda item: str(item.get("node_id") or "")):
        node_id = str(entry.get("node_id") or "").strip()
        if not node_id:
            continue
        node_obj = apply_governance_defaults(
            canonical_object_from_subnet_directory_node(entry),
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        if node_obj.id != subject.id:
            _append_unique(objects, node_obj, seen)

        capacity = entry.get("capacity") if isinstance(entry.get("capacity"), dict) else {}
        if not any(isinstance(capacity.get(name), list) and capacity.get(name) for name in ("io", "skills", "scenarios")):
            continue
        if node_id == node_ref:
            continue
        capacity_obj = apply_governance_defaults(
            canonical_object_from_capacity_snapshot(
                capacity,
                node_id=node_id,
                title=f"{node_obj.title} capacity",
                summary="Subnet directory capacity snapshot",
            ),
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        _append_unique(objects, capacity_obj, seen)
        for io_item in list(capacity.get("io") or []):
            if not isinstance(io_item, dict):
                continue
            io_obj = apply_governance_defaults(
                canonical_object_from_io_capacity_entry(io_item, node_id=node_id),
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
            _append_unique(objects, io_obj, seen)

    return apply_projection_governance(
        canonical_neighborhood_projection(subject, objects),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def current_control_plane_objects(*, webspace_id: str | None = None) -> list[Any]:
    cache_key = str(webspace_id or "").strip()
    now = time.monotonic()
    cached = _CONTROL_PLANE_CACHE.get(cache_key)
    if cached is not None:
        cached_at, cached_objects = cached
        if now - cached_at <= _CONTROL_PLANE_CACHE_TTL_S:
            return list(cached_objects)

    with _CONTROL_PLANE_CACHE_LOCK:
        build_lock = _CONTROL_PLANE_CACHE_BUILD_LOCKS.get(cache_key)
        if build_lock is None:
            build_lock = threading.Lock()
            _CONTROL_PLANE_CACHE_BUILD_LOCKS[cache_key] = build_lock

    with build_lock:
        now = time.monotonic()
        cached = _CONTROL_PLANE_CACHE.get(cache_key)
        if cached is not None:
            cached_at, cached_objects = cached
            if now - cached_at <= _CONTROL_PLANE_CACHE_TTL_S:
                return list(cached_objects)

        subject = current_node_object()
        inventory = current_inventory_projection()
        reliability = current_reliability_projection(webspace_id=webspace_id)
        neighborhood = _current_node_neighborhood_projection(webspace_id=webspace_id)
        supervisor_runtime = current_supervisor_runtime_object()
        objects: list[Any] = []
        seen: set[str] = set()
        for item in [
            subject,
            inventory.subject,
            reliability.subject,
            neighborhood.subject,
            supervisor_runtime,
            *inventory.objects,
            *reliability.objects,
            *neighborhood.objects,
        ]:
            _append_unique(objects, item, seen)
        # Stamp the cache after the expensive build.  If the build takes longer
        # than the TTL, stamping before it makes the entry stale immediately and
        # every compact overview/stream request rebuilds the same model.
        _CONTROL_PLANE_CACHE[cache_key] = (time.monotonic(), list(objects))
        return objects


def current_overview_projection(*, webspace_id: str | None = None):
    tenant_id, owner_id = _control_plane_scope_refs()
    subject = current_node_object()
    objects = [item for item in current_control_plane_objects(webspace_id=webspace_id) if str(getattr(item, "id", "") or "") != subject.id]
    return apply_projection_governance(
        canonical_overview_projection(subject, objects),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def _object_index(*, webspace_id: str | None = None) -> dict[str, Any]:
    return {str(item.id): item for item in current_control_plane_objects(webspace_id=webspace_id)}


def current_object_model(object_id: str, *, webspace_id: str | None = None):
    token = str(object_id or "").strip()
    if token in {"self", "current", "local"}:
        return current_node_object()
    obj = _object_index(webspace_id=webspace_id).get(token)
    if obj is None:
        raise KeyError(token)
    return obj


def _neighborhood_objects_for(subject: Any, universe: list[Any]) -> list[Any]:
    subject_id = str(getattr(subject, "id", "") or "")
    related_ids = set(_flatten_refs(getattr(subject, "relations", {})))
    for item in universe:
        item_id = str(getattr(item, "id", "") or "")
        if not item_id or item_id == subject_id:
            continue
        if subject_id in _flatten_refs(getattr(item, "relations", {})):
            related_ids.add(item_id)
    neighbors: list[Any] = []
    seen: set[str] = set()
    for item in universe:
        item_id = str(getattr(item, "id", "") or "")
        if not item_id or item_id == subject_id or item_id not in related_ids or item_id in seen:
            continue
        seen.add(item_id)
        neighbors.append(item)
    return neighbors


def current_object_projection(object_id: str, *, webspace_id: str | None = None):
    tenant_id, owner_id = _control_plane_scope_refs()
    subject = current_object_model(object_id, webspace_id=webspace_id)
    neighborhood = _neighborhood_objects_for(subject, current_control_plane_objects(webspace_id=webspace_id))
    return apply_projection_governance(
        canonical_object_projection(subject, neighborhood),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def current_object_inspector(object_id: str, *, task_goal: str | None = None, webspace_id: str | None = None):
    tenant_id, owner_id = _control_plane_scope_refs()
    subject = current_object_model(object_id, webspace_id=webspace_id)
    neighborhood = _neighborhood_objects_for(subject, current_control_plane_objects(webspace_id=webspace_id))
    return apply_projection_governance(
        canonical_object_inspector(subject, neighborhood, task_goal=task_goal),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def current_topology_projection(object_id: str, *, webspace_id: str | None = None):
    tenant_id, owner_id = _control_plane_scope_refs()
    subject = current_object_model(object_id, webspace_id=webspace_id)
    neighborhood = _neighborhood_objects_for(subject, current_control_plane_objects(webspace_id=webspace_id))
    return apply_projection_governance(
        canonical_topology_projection(subject, neighborhood),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def current_task_packet(object_id: str, *, task_goal: str | None = None, webspace_id: str | None = None):
    tenant_id, owner_id = _control_plane_scope_refs()
    subject = current_object_model(object_id, webspace_id=webspace_id)
    current_id = current_node_object().id
    if str(getattr(subject, "id", "") or "").strip() == current_id:
        # The current-node neighborhood has subnet-directory peers injected
        # explicitly. Generic relation traversal misses members because they
        # relate to the subnet rather than directly to the hub object.
        neighborhood_projection = _current_node_neighborhood_projection(webspace_id=webspace_id)
        neighborhood = list(getattr(neighborhood_projection, "objects", []) or [])
    else:
        neighborhood = _neighborhood_objects_for(subject, current_control_plane_objects(webspace_id=webspace_id))
    return apply_projection_governance(
        canonical_task_packet(subject, neighborhood, task_goal=task_goal),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def current_subnet_planning_context(
    object_id: str | None = None,
    *,
    task_goal: str | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    token = str(object_id or "").strip()
    current_id = current_node_object().id
    effective_object_id = current_id if not token or token in {"self", "current", "local", current_id} else token
    neighborhood = current_neighborhood_projection(object_id=effective_object_id, webspace_id=webspace_id)
    task_packet = current_task_packet(effective_object_id, task_goal=task_goal, webspace_id=webspace_id)
    neighborhood_context = coerce_mapping(getattr(neighborhood, "context", {}))
    task_context = coerce_mapping(getattr(task_packet, "context", {}))
    subnet_planning = coerce_mapping(task_context.get("subnet_planning"))
    planning_summary = coerce_mapping(subnet_planning.get("summary"))
    neighborhood_summary = coerce_mapping(neighborhood_context.get("subnet_runtime_summary"))
    planning_nodes = [
        item
        for item in list(subnet_planning.get("nodes") or [])
        if isinstance(item, dict)
    ]
    return compact_mapping(
        {
            "object_id": str(getattr(task_packet.subject, "id", "") or "").strip(),
            "object_title": str(getattr(task_packet.subject, "title", "") or "").strip(),
            "task_goal": str(task_context.get("task_goal") or "").strip() or None,
            "summary": planning_summary or neighborhood_summary,
            "nodes": planning_nodes,
            "constraints": coerce_mapping(task_context.get("constraints")),
            "allowed_actions": list(task_context.get("allowed_actions") or []),
            "relevant_incidents": list(task_context.get("relevant_incidents") or []),
            "desired_state": coerce_mapping(task_context.get("desired_state")),
            "actual_state": coerce_mapping(task_context.get("actual_state")),
            "gap": coerce_mapping(task_context.get("gap")),
            "source_projection_ids": {
                "neighborhood": str(getattr(neighborhood, "id", "") or "").strip() or None,
                "task_packet": str(getattr(task_packet, "id", "") or "").strip() or None,
            },
        }
    )


def current_neighborhood_projection(object_id: str | None = None, *, webspace_id: str | None = None):
    token = str(object_id or "").strip()
    current_id = current_node_object().id
    if not token or token in {"self", "current", "local", current_id}:
        return _current_node_neighborhood_projection(webspace_id=webspace_id)
    tenant_id, owner_id = _control_plane_scope_refs()
    subject = current_object_model(token, webspace_id=webspace_id)
    objects = _neighborhood_objects_for(subject, current_control_plane_objects(webspace_id=webspace_id))
    return apply_projection_governance(
        canonical_neighborhood_projection(subject, objects),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def current_inventory_projection():
    subject = current_node_object()
    node_ref = _node_ref(subject.id)
    reliability = current_reliability_projection()
    reliability_objects = [
        item
        for item in reliability.objects
        if str(item.kind or "").strip() in {CanonicalKind.ROOT.value, CanonicalKind.QUOTA.value}
    ]
    objects = [
        current_profile_object(),
        local_capacity_object(node_id=node_ref),
        *local_io_objects(node_id=node_ref),
        *device_objects(),
        *workspace_objects(),
        *browser_session_objects(),
        *installed_skill_objects(),
        *installed_scenario_objects(),
        *reliability_objects,
    ]
    tenant_id, owner_id = _control_plane_scope_refs()
    return apply_projection_governance(
        canonical_inventory_projection(subject, objects),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


__all__ = [
    "current_control_plane_objects",
    "current_inventory_projection",
    "current_neighborhood_projection",
    "current_node_object",
    "current_node_identity_status_payload",
    "current_node_status_push_payload",
    "current_node_probe_status_payload",
    "compact_node_status_transport_payload",
    "current_node_status_payload",
    "current_object_inspector",
    "current_object_model",
    "current_object_projection",
    "current_overview_projection",
    "current_reliability_payload",
    "current_reliability_projection",
    "current_subnet_planning_context",
    "current_task_packet",
    "current_topology_projection",
    "node_status_push_heartbeat_s",
    "route_info",
]
